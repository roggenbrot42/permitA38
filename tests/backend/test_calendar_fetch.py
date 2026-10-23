"""Calendar downloads through the SSRF-safe fetcher: https only, conditional requests, limits and redirects."""

import pytest

from permita38.core.netsafe import FetchError, SafeFetcher
from tests.backend.test_impressum_netsafe import _Site, make_fetcher


@pytest.fixture
def site():
    local = _Site()
    yield local
    local.server.shutdown()
    local.server.server_close()


ICS_TYPES = ("text/calendar", "application/ics", "text/plain", "application/octet-stream")
BODY = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def download(fetcher, url, **kw):
    params = {"accept": "text/calendar", "allowed_types": ICS_TYPES, "max_bytes": 64 * 1024}
    params.update(kw)
    return fetcher.fetch_resource(url, **params)


def test_plain_http_is_refused_when_https_is_required(site):
    with pytest.raises(FetchError) as err:
        download(make_fetcher(site), site.url("/a.ics"), https_only=True)
    assert err.value.code == "insecure_url"
    assert site.requests == []  # nothing was sent


def test_download_returns_body_and_validators(site):
    site.routes["/a.ics"] = (
        200,
        {
            "Content-Type": "text/calendar; charset=utf-8",
            "ETag": '"abc"',
            "Last-Modified": "Mon, 05 Oct 2026 10:00:00 GMT",
        },
        BODY,
    )
    res = download(make_fetcher(site), site.url("/a.ics"))
    assert res.status == 200 and res.body == BODY
    assert res.etag == '"abc"' and res.last_modified == "Mon, 05 Oct 2026 10:00:00 GMT"


def test_conditional_request_gets_304_without_a_body(site):
    def conditional(handler):
        if handler.headers.get("If-None-Match") == '"abc"':
            handler.send_response(304)
            handler.send_header("ETag", '"abc"')
            handler.end_headers()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/calendar")
        handler.send_header("ETag", '"abc"')
        handler.send_header("Content-Length", str(len(BODY)))
        handler.end_headers()
        handler.wfile.write(BODY)

    site.routes["/c.ics"] = conditional
    fetcher = make_fetcher(site)
    res = download(fetcher, site.url("/c.ics"), conditional={"etag": '"abc"', "last_modified": None})
    assert res.status == 304 and res.body == b""
    assert site.requests[-1]["headers"].get("If-None-Match") == '"abc"'
    res = download(fetcher, site.url("/c.ics"), conditional={"etag": None})
    assert res.status == 200 and res.body == BODY


def test_wrong_content_type_is_refused(site):
    site.routes["/page"] = (200, {"Content-Type": "text/html"}, b"<html></html>")
    with pytest.raises(FetchError) as err:
        download(make_fetcher(site), site.url("/page"))
    assert err.value.code == "bad_content_type"


def test_size_limit_applies_to_calendar_downloads(site):
    site.routes["/big.ics"] = (200, {"Content-Type": "text/calendar"}, b"x" * 5000)
    with pytest.raises(FetchError) as err:
        download(make_fetcher(site), site.url("/big.ics"), max_bytes=1000)
    assert err.value.code == "too_large"


def test_error_status_is_reported_without_the_address(site):
    site.routes["/gone.ics"] = (404, {"Content-Type": "text/plain"}, b"nope")
    with pytest.raises(FetchError) as err:
        download(make_fetcher(site), site.url("/gone.ics"))
    assert err.value.code == "http_error"
    assert "gone.ics" not in err.value.message


def test_redirect_into_a_private_address_is_refused(site):
    site.routes["/go.ics"] = (302, {"Location": "http://10.0.0.1/secret.ics"}, b"")
    with pytest.raises(FetchError) as err:
        download(make_fetcher(site), site.url("/go.ics"))
    assert err.value.code == "blocked_address"


def test_https_on_port_80_is_refused_when_https_is_required(site):
    with pytest.raises(FetchError) as err:
        download(SafeFetcher("test-agent"), "https://calendar.example.test:80/a.ics", https_only=True)
    assert err.value.code == "invalid_url"
