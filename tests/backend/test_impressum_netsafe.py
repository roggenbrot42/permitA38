"""SSRF protection and politeness of the Impressum fetcher.

The network tests run the *real* ``SafeFetcher`` against a throw-away HTTP server on 127.0.0.1. Loopback is
normally forbidden, so these tests use an address policy that additionally allows 127.0.0.1 – everything else
(private ranges, metadata addresses, ...) is still judged by the production ``check_ip``.
"""

import gzip
import ipaddress
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from permita38.core.netsafe import (
    BlockedAddress,
    FetchError,
    RequestBudget,
    SafeFetcher,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    check_ip,
    parse_url,
    same_site,
)

UA = "permitA38/test (+https://example.invalid)"

BLOCKED_IPS = [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "10.255.255.255", "172.16.5.4", "172.31.0.1", "192.168.1.1",
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.100.100.200", "0.0.0.0", "0.1.2.3", "224.0.0.1",
    "239.255.255.250", "240.0.0.1", "255.255.255.255", "192.0.0.8", "198.18.0.1", "198.19.255.255",
    "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "ff02::1", "fec0::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "::ffff:169.254.169.254", "64:ff9b::7f00:1", "64:ff9b:1::1", "2002:7f00:1::1",
    "2001::1", "100::1",
]  # fmt: skip
PUBLIC_IPS = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "2a00:1450:4001:81b::200e"]


@pytest.mark.parametrize("raw", BLOCKED_IPS)
def test_blocked_ranges(raw):
    with pytest.raises(BlockedAddress):
        check_ip(ipaddress.ip_address(raw))


@pytest.mark.parametrize("raw", PUBLIC_IPS)
def test_public_addresses_pass(raw):
    check_ip(ipaddress.ip_address(raw))


# ---------------------------------------------------------------------------------------------------------
# URL validation (no network)
# ---------------------------------------------------------------------------------------------------------

BAD_URLS = [
    "", "   ", "example", "ftp://example.com/", "file:///etc/passwd", "gopher://example.com/", "javascript:alert(1)",
    "data:text/html,<script>", "http://", "https://", "//example.com/", "http://user:pw@example.com/",
    "http://example.com@evil.example/", "http://:pw@example.com/", "http://localhost/", "http://LOCALHOST/",
    "http://intranet/", "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://2130706433/",
    "http://0x7f.0.0.1/", "http://0177.0.0.1/", "http://127.1/", "http://10.1.2.3/", "http://169.254.169.254/latest/",
    "http://metadata.google.internal/", "http://printer.local/", "http://nas.lan/", "http://host.localhost/",
    "http://example.com:8080/", "http://example.com:22/", "http://example.com:0/", "http://example.com:99999/",
    "http://exa mple.com/", "http://a..b/", "http://.example.com/", "http://" + "a" * 70 + ".com/",
    "http://example.com/" + "a" * 2100, "http://example.com/\x00", "http://example.com/a\nb", "http://exa\tmple.com/",
]  # fmt: skip


@pytest.mark.parametrize("url", BAD_URLS)
def test_bad_urls_are_rejected(url):
    with pytest.raises(FetchError) as exc:
        parse_url(url)
    assert exc.value.code in ("invalid_url", "blocked_address")


def test_good_urls_are_normalised():
    p = parse_url("https://Beispiel-Software.Example/Impressum?x=1")
    assert (p.scheme, p.host, p.port, p.target) == ("https", "beispiel-software.example", 443, "/Impressum?x=1")
    assert parse_url("http://example.com:80/").port == 80
    assert parse_url("https://example.com:443").target == "/"
    assert parse_url("https://bücher.example/").host == "xn--bcher-kva.example"  # IDN -> punycode


def test_same_site():
    assert same_site("https://www.example.com/", "https://example.com/impressum")
    assert same_site("https://example.com/", "https://shop.example.com/")
    assert same_site("https://example.com/a", "http://example.com/b")
    assert not same_site("https://example.com/", "https://example.org/")
    assert not same_site("https://example.com/", "https://evil-example.com/")
    assert not same_site("https://a.co.uk/", "https://b.co.uk/")
    assert not same_site("https://example.com/", "mailto:x@example.com")


def test_https_connection_is_pinned_and_verifies_host_name():
    f = SafeFetcher(UA)
    conn = f._connection(parse_url("https://example.com/"), "93.184.216.34")
    assert isinstance(conn, _PinnedHTTPSConnection) and conn.host == "example.com"  # SNI/cert check use the host
    assert conn._pinned_ip == "93.184.216.34"
    assert isinstance(f._connection(parse_url("http://example.com/"), "93.184.216.34"), _PinnedHTTPConnection)


# ---------------------------------------------------------------------------------------------------------
# real fetcher against a local server
# ---------------------------------------------------------------------------------------------------------


class _Site:
    def __init__(self):
        self.routes: dict = {}
        self.requests: list[dict] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                site.requests.append({"path": self.path, "headers": dict(self.headers)})
                route = site.routes.get(self.path.split("?")[0], (404, {}, b"not found"))
                if callable(route):
                    return route(self)
                status, headers, body = route
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def url(self, path="/", host="site.example"):
        return f"http://{host}:{self.port}{path}"


HTML = {"Content-Type": "text/html; charset=utf-8"}


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.server.shutdown()
    s.server.server_close()


def allow_loopback(ip):
    if str(ip) != "127.0.0.1":
        check_ip(ip)


def make_fetcher(site, resolver=None, **kw):
    return SafeFetcher(
        UA,
        resolver=resolver or (lambda host, port: ["127.0.0.1"]),
        address_policy=allow_loopback,
        allowed_ports=(site.port,),
        **kw,
    )


def test_fetch_ok_sends_polite_headers_and_pins_the_ip(site):
    site.routes["/"] = (200, HTML, b"<html>hi</html>")
    page = make_fetcher(site).fetch(site.url("/"))
    assert (page.status, page.content_type, page.charset, page.body) == (200, "text/html", "utf-8", b"<html>hi</html>")
    req = site.requests[0]["headers"]
    assert req["Host"] == f"site.example:{site.port}"  # the server was reached by IP, the Host header is the name
    assert req["User-Agent"] == UA
    assert "Cookie" not in req and "Authorization" not in req
    assert req["Accept-Encoding"] == "gzip"


def test_xhtml_and_gzip_are_accepted(site):
    body = gzip.compress("<html>Müller</html>".encode())
    site.routes["/"] = (200, {"Content-Type": "application/xhtml+xml", "Content-Encoding": "gzip"}, body)
    page = make_fetcher(site).fetch(site.url("/"))
    assert page.content_type == "application/xhtml+xml" and page.body == "<html>Müller</html>".encode()


def test_redirect_chain_is_followed_and_reported(site):
    site.routes["/a"] = (301, {"Location": "/b"}, b"")
    site.routes["/b"] = (302, {"Location": site.url("/c")}, b"")
    site.routes["/c"] = (200, HTML, b"done")
    page = make_fetcher(site).fetch(site.url("/a"))
    assert page.body == b"done" and page.url == site.url("/c")
    assert page.redirects == [f"http://site.example:{site.port}/a", f"http://site.example:{site.port}/b"]


def test_redirect_loop_and_too_many_redirects(site):
    site.routes["/loop1"] = (302, {"Location": "/loop2"}, b"")
    site.routes["/loop2"] = (302, {"Location": "/loop1"}, b"")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/loop1"))
    assert exc.value.code == "redirect_loop"
    for i in range(6):
        site.routes[f"/r{i}"] = (302, {"Location": f"/r{i + 1}"}, b"")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/r0"))
    assert exc.value.code == "too_many_redirects"
    assert len(site.requests) <= 2 + 4  # never more than 3 redirects (4 requests) per fetch


@pytest.mark.parametrize(
    "location",
    [
        "http://metadata.example/latest/meta-data/",  # resolves to 169.254.169.254
        "http://private.example/",  # resolves to 10.0.0.5
        "http://[::1]/",
        "http://127.0.0.1/",  # IP literal
        "http://localhost/",
        "file:///etc/passwd",
        "gopher://site.example/",
        "http://user:pw@site.example/",
        "http://site.example:8080/",  # port not allowed
    ],
)
def test_redirect_to_forbidden_target_is_refused_before_connecting(site, location):
    answers = {"metadata.example": ["169.254.169.254"], "private.example": ["10.0.0.5"]}
    resolver = lambda host, port: answers.get(host, ["127.0.0.1"])  # noqa: E731
    site.routes["/go"] = (302, {"Location": location}, b"")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, resolver).fetch(site.url("/go"))
    assert exc.value.code in ("blocked_address", "invalid_url")
    assert len(site.requests) == 1  # only the first (allowed) hop ever hit the server


def test_one_bad_dns_record_rejects_the_host(site):
    site.routes["/"] = (200, HTML, b"x")
    with pytest.raises(BlockedAddress):
        make_fetcher(site, lambda host, port: ["127.0.0.1", "10.0.0.9"]).fetch(site.url("/"))
    assert site.requests == []


def test_dns_rebinding_cannot_swap_the_address_after_the_check(site):
    """The name is resolved once per hop and the socket is connected to exactly the checked address."""
    calls = []

    def rebinding_resolver(host, port):
        calls.append(host)
        return ["127.0.0.1"] if len(calls) == 1 else ["10.0.0.9"]  # 'second answer' is internal

    site.routes["/one"] = (200, HTML, b"ok")
    page = make_fetcher(site, rebinding_resolver).fetch(site.url("/one"))
    assert page.body == b"ok" and calls == ["site.example"]  # one lookup, no second resolution before connect()

    calls.clear()
    site.routes["/hop"] = (302, {"Location": "/two"}, b"")
    site.routes["/two"] = (200, HTML, b"unreachable")
    with pytest.raises(BlockedAddress):  # the next hop re-resolves and the internal answer is caught
        make_fetcher(site, rebinding_resolver).fetch(site.url("/hop"))
    assert [r["path"] for r in site.requests if r["path"] in ("/hop", "/two")] == ["/hop"]


def test_body_size_limit_plain_and_gzip_bomb(site):
    site.routes["/big"] = (200, HTML, b"a" * 5000)
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, max_bytes=1000).fetch(site.url("/big"))
    assert exc.value.code == "too_large"
    bomb = gzip.compress(b"0" * 20_000_000)  # tiny on the wire, huge when expanded
    assert len(bomb) < 100_000
    site.routes["/bomb"] = (200, {**HTML, "Content-Encoding": "gzip"}, bomb)
    started = time.monotonic()
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, max_bytes=100_000).fetch(site.url("/bomb"))
    assert exc.value.code == "too_large" and time.monotonic() - started < 3


@pytest.mark.parametrize("ctype", ["image/png", "text/plain", "application/json", "application/pdf", "", "text/xml"])
def test_content_type_allow_list(site, ctype):
    site.routes["/x"] = (200, {"Content-Type": ctype} if ctype else {}, b"data")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/x"))
    assert exc.value.code == "bad_content_type"


def test_unsupported_content_encoding_and_http_errors(site):
    site.routes["/br"] = (200, {**HTML, "Content-Encoding": "br"}, b"xx")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/br"))
    assert exc.value.code == "bad_content_type"
    for status in (401, 403, 404, 410, 500, 503):
        site.routes["/s"] = (status, HTML, b"nope")
        with pytest.raises(FetchError) as exc:
            make_fetcher(site).fetch(site.url("/s"))
        assert exc.value.code == "http_error"
    site.routes["/nolocation"] = (302, {}, b"")
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/nolocation"))
    assert exc.value.code == "http_error"


def test_timeouts(site):
    def slow(handler):
        time.sleep(1.5)
        handler.send_response(200)
        handler.end_headers()

    site.routes["/slow"] = slow
    started = time.monotonic()
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, timeout=0.3).fetch(site.url("/slow"))
    assert exc.value.code == "timeout" and time.monotonic() - started < 1.2

    def drip(handler):  # sends data forever, slowly: only the overall deadline stops it
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.send_header("Content-Length", "100000")
        handler.end_headers()
        try:
            for _ in range(100):
                handler.wfile.write(b"x" * 10)
                handler.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass

    site.routes["/drip"] = drip
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, timeout=1.0, deadline=0.5).fetch(site.url("/drip"))
    assert exc.value.code == "timeout"


def test_connection_refused_is_a_network_error():
    s = _Site()
    port = s.port
    s.server.shutdown()
    s.server.server_close()
    fetcher = SafeFetcher(UA, resolver=lambda h, p: ["127.0.0.1"], address_policy=allow_loopback, allowed_ports=(port,))
    with pytest.raises(FetchError) as exc:
        fetcher.fetch(f"http://site.example:{port}/")
    assert exc.value.code == "network_error"


def test_request_budget_is_shared_across_hops_and_fetches(site):
    for i in range(4):
        site.routes[f"/h{i}"] = (302, {"Location": f"/h{i + 1}"}, b"")
    site.routes["/h4"] = (200, HTML, b"end")
    budget = RequestBudget(3)
    with pytest.raises(FetchError) as exc:
        make_fetcher(site).fetch(site.url("/h0"), budget)
    assert exc.value.code == "budget_exceeded" and budget.used == 3 and len(site.requests) == 3


def test_dns_failure_and_empty_answer(site):
    def nx(host, port):
        raise FetchError("dns_error", "The host name could not be resolved.")

    with pytest.raises(FetchError) as exc:
        make_fetcher(site, nx).fetch(site.url("/"))
    assert exc.value.code == "dns_error"
    with pytest.raises(FetchError) as exc:
        make_fetcher(site, lambda h, p: []).fetch(site.url("/"))
    assert exc.value.code == "dns_error"
