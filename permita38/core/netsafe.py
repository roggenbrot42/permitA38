"""SSRF-safe, polite HTTP fetching for features that read third-party web pages.

Rules enforced for every request *and every redirect hop*:

* only ``http``/``https`` on ports 80/443, no credentials in the URL, no IP-literal or single-label hosts;
* the host name is resolved **once per hop**, every returned address must be a public one, and the socket is then
  connected to that exact address (the connection is *pinned*), so a DNS-rebinding answer between "check" and
  "connect" cannot redirect us to an internal address; TLS still validates the certificate against the host name;
* at most 3 redirects, loops detected, a request budget shared by a whole lookup, connect/read timeouts plus an
  overall deadline, a 2 MB body cap (also for decompressed data) and a content-type allow-list.

The network is reached through the small :class:`Fetcher` interface so services can be tested without it.
"""

import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit

MAX_URL_LENGTH = 2048
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ALLOWED_PORTS = (80, 443)
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_REDIRECT_STATUS = (301, 302, 303, 307, 308)
_HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9"
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home", ".corp", ".home.arpa")

# Not covered (or only partly) by ``is_global``: tunnelling prefixes that embed an arbitrary IPv4 address.
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT, also Alibaba metadata 100.100.100.200
        "169.254.0.0/16",  # link local incl. cloud metadata 169.254.169.254
        "192.0.0.0/24",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/32",  # Teredo
        "2002::/16",  # 6to4
        "fc00::/7",  # unique local incl. fd00:ec2::254 metadata
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)


class FetchError(Exception):
    """A fetch was refused or failed. ``code`` is stable and machine readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BlockedAddress(FetchError):
    def __init__(self, message: str = "This address is not allowed (private, internal or reserved network)."):
        super().__init__("blocked_address", message)


@dataclass
class FetchedResource:
    """A non-HTML download (for example an iCalendar file). ``status`` is 200, or 304 when the server says unchanged."""

    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class FetchedPage:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    redirects: list[str] = field(default_factory=list)  # every URL visited before ``url``
    charset: str | None = None  # from the Content-Type header, if any


class RequestBudget:
    """Counts HTTP requests (redirect hops included) of one lookup."""

    def __init__(self, maximum: int = 5):
        self.maximum = maximum
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.maximum:
            raise FetchError("budget_exceeded", "Too many requests for one lookup.")
        self.used += 1


class Fetcher(Protocol):
    def fetch(self, url: str, budget: RequestBudget | None = None) -> FetchedPage: ...


AddressPolicy = Callable[["ipaddress.IPv4Address | ipaddress.IPv6Address"], None]
Resolver = Callable[[str, int], list[str]]


def check_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> None:
    """Raise :class:`BlockedAddress` unless ``ip`` is a public unicast address."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_global or any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version):
        raise BlockedAddress()
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise BlockedAddress()


def default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("dns_error", "The host name could not be resolved.") from None
    except UnicodeError:
        raise FetchError("invalid_url", "The host name is not valid.") from None
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


@dataclass(frozen=True)
class ParsedUrl:
    url: str
    scheme: str
    host: str  # IDNA (ASCII) host name, lower case
    port: int
    target: str  # path + query


def parse_url(url: str, allowed_ports: Iterable[int] = ALLOWED_PORTS) -> ParsedUrl:
    """Validate and normalise a URL. Raises :class:`FetchError` (``invalid_url``/``blocked_address``)."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise FetchError("invalid_url", "Please enter a valid web address (http:// or https://).")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise FetchError("invalid_url", "Please enter a valid web address (http:// or https://).") from None
    if parts.scheme not in ("http", "https"):
        raise FetchError("invalid_url", "Only http:// and https:// addresses are supported.")
    if "@" in parts.netloc:
        raise FetchError("invalid_url", "Addresses with a user name or password are not supported.")
    if not host:
        raise FetchError("invalid_url", "Please enter a valid web address (http:// or https://).")
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if port not in tuple(allowed_ports):
        raise BlockedAddress("Only the standard web ports (80 and 443) are allowed.")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        raise FetchError("invalid_url", "The host name is not valid.") from None
    if not _is_public_hostname(ascii_host):
        raise BlockedAddress()
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return ParsedUrl(url=url, scheme=parts.scheme, host=ascii_host, port=port, target=target)


def _is_public_hostname(host: str) -> bool:
    if "." not in host or host.startswith(".") or ".." in host or len(host) > 253:
        return False  # single-label names (localhost, intranet) and malformed names
    if host.endswith(_BLOCKED_SUFFIXES):
        return False
    if ":" in host:  # IPv6 literal
        return False
    labels = host.split(".")
    if not any(c.isalpha() for c in labels[-1]):  # IP literals in any notation (1.2.3.4, 0x7f.1, 2130706433)
        return False
    return all(0 < len(label) <= 63 for label in labels)


def same_site(a: str, b: str) -> bool:
    """Same host, ``www.`` variants, or one host is a sub-domain of the other."""

    def norm(h: str) -> str:
        h = (urlsplit(h).hostname or "").lower().rstrip(".")
        return h[4:] if h.startswith("www.") else h

    ha, hb = norm(a), norm(b)
    return bool(ha) and bool(hb) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        # SNI and certificate verification use the real host name, the TCP connection goes to the pinned address
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


class SafeFetcher:
    """The production :class:`Fetcher` (standard library only, no JavaScript, no cookies, no auth)."""

    def __init__(
        self,
        user_agent: str,
        *,
        resolver: Resolver = default_resolver,
        address_policy: AddressPolicy = check_ip,
        allowed_ports: Iterable[int] = ALLOWED_PORTS,
        timeout: float = 10.0,
        deadline: float = 25.0,
        max_bytes: int = MAX_BODY_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        ssl_context: ssl.SSLContext | None = None,
    ):
        self.user_agent = user_agent
        self.resolver = resolver
        self.address_policy = address_policy
        self.allowed_ports = tuple(allowed_ports)
        self.timeout = timeout
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._ssl_context = ssl_context

    # -- public ---------------------------------------------------------------------------------
    def fetch(self, url: str, budget: RequestBudget | None = None) -> FetchedPage:
        current, status, headers, body, visited = self._walk(url, budget, _HTML_ACCEPT, ALLOWED_CONTENT_TYPES, None)
        if status != 200:
            raise FetchError("http_error", f"The website answered with HTTP status {status}.")
        raw_type = headers.get("content-type", "")
        ctype = raw_type.split(";")[0].strip().lower()
        if ctype not in ALLOWED_CONTENT_TYPES:
            raise FetchError("bad_content_type", "The address does not point to a web page (HTML).")
        charset = None
        for param in raw_type.split(";")[1:]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "charset":
                charset = value.strip().strip("\"'") or None
        return FetchedPage(
            url=current, status=status, content_type=ctype, body=body, redirects=visited, charset=charset
        )

    def fetch_resource(
        self,
        url: str,
        *,
        accept: str,
        allowed_types: tuple[str, ...],
        max_bytes: int,
        https_only: bool = False,
        conditional: dict[str, str] | None = None,
        budget: RequestBudget | None = None,
    ) -> FetchedResource:
        """Download a file with the same SSRF rules. ``conditional`` may hold ETag/Last-Modified validators.

        Returns status 304 (empty body) when the validators say the file is unchanged. Raises :class:`FetchError`.
        """
        headers_in = {"If-None-Match": conditional["etag"]} if conditional and conditional.get("etag") else {}
        if conditional and conditional.get("last_modified"):
            headers_in["If-Modified-Since"] = conditional["last_modified"]
        current, status, headers, body, _ = self._walk(
            url, budget, accept, allowed_types, (max_bytes, https_only, headers_in)
        )
        if status == 304:
            return FetchedResource(url=current, status=304, content_type="", body=b"")
        if status != 200:
            raise FetchError("http_error", f"The server answered with HTTP status {status}.")
        ctype = headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in allowed_types:
            raise FetchError("bad_content_type", "The address does not point to a calendar file.")
        return FetchedResource(
            url=current,
            status=200,
            content_type=ctype,
            body=body,
            etag=headers.get("etag"),
            last_modified=headers.get("last-modified"),
        )

    # -- internals ------------------------------------------------------------------------------
    def _walk(
        self,
        url: str,
        budget: RequestBudget | None,
        accept: str,
        allowed_types: tuple[str, ...],
        extra: tuple[int, bool, dict[str, str]] | None,
    ) -> tuple[str, int, dict[str, str], bytes, list[str]]:
        """Follow redirects; returns (final url, status, headers, body, URLs visited before it)."""
        budget = budget or RequestBudget()
        started = time.monotonic()
        max_bytes, https_only, extra_headers = extra if extra else (self.max_bytes, False, {})
        current = url
        visited: list[str] = []
        for _ in range(self.max_redirects + 1):
            parsed = parse_url(current, self.allowed_ports)
            if https_only and parsed.scheme != "https":
                raise FetchError("insecure_url", "Only https:// addresses are accepted here.")
            if https_only and parsed.port != 443:
                raise FetchError("invalid_url", "Only the standard https port is accepted here.")
            key = f"{parsed.scheme}://{parsed.host}:{parsed.port}{parsed.target}"
            if key in visited:
                raise FetchError("redirect_loop", "The website redirects in a loop.")
            visited.append(key)
            ip = self._resolve_and_check(parsed)
            budget.spend()
            status, headers, body = self._request(
                parsed,
                ip,
                started,
                accept=accept,
                allowed_types=allowed_types,
                max_bytes=max_bytes,
                extra=extra_headers,
            )
            if status in _REDIRECT_STATUS:
                location = headers.get("location")
                if not location:
                    raise FetchError("http_error", "The website sent an invalid redirect.")
                current = urljoin(current, location.strip())
                continue
            return current, status, headers, body, visited[:-1]
        raise FetchError("too_many_redirects", "The website redirects too often.")

    def _resolve_and_check(self, parsed: ParsedUrl) -> str:
        addresses = self.resolver(parsed.host, parsed.port)
        if not addresses:
            raise FetchError("dns_error", "The host name could not be resolved.")
        checked: list[str] = []
        for raw in addresses:  # every answer must be acceptable, one bad record rejects the host
            try:
                ip = ipaddress.ip_address(raw.split("%")[0])
            except ValueError:
                raise BlockedAddress() from None
            self.address_policy(ip)
            checked.append(str(ip))
        return checked[0]  # the connection is pinned to this exact address

    def _connection(self, parsed: ParsedUrl, ip: str) -> http.client.HTTPConnection:
        if parsed.scheme == "https":
            ctx = self._ssl_context or ssl.create_default_context()
            return _PinnedHTTPSConnection(parsed.host, ip, parsed.port, self.timeout, ctx)
        return _PinnedHTTPConnection(parsed.host, ip, parsed.port, self.timeout)

    def _request(
        self,
        parsed: ParsedUrl,
        ip: str,
        started: float,
        *,
        accept: str = _HTML_ACCEPT,
        allowed_types: tuple[str, ...] = ALLOWED_CONTENT_TYPES,
        max_bytes: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = self._connection(parsed, ip)
        try:
            headers = {
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Encoding": "gzip",
                "Accept-Language": "de,en;q=0.8",
                "Connection": "close",
            }
            headers.update(extra or {})
            conn.request("GET", parsed.target, headers=headers)
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            if resp.status in _REDIRECT_STATUS or resp.status != 200:
                return resp.status, headers, b""
            ctype = headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in allowed_types:
                return resp.status, headers, b""
            limit = self.max_bytes if max_bytes is None else max_bytes
            return resp.status, headers, self._read_body(conn, resp, headers, started, limit)
        except FetchError:
            raise
        except (TimeoutError, socket.timeout):
            raise FetchError("timeout", "The website did not answer in time.") from None
        except ssl.SSLError:
            raise FetchError(
                "tls_error", "The secure connection to the website failed (invalid certificate?)."
            ) from None
        except (OSError, http.client.HTTPException):
            raise FetchError("network_error", "The website could not be reached.") from None
        finally:
            conn.close()

    def _read_body(
        self,
        conn: http.client.HTTPConnection,
        resp: http.client.HTTPResponse,
        headers: dict[str, str],
        started: float,
        limit: int,
    ) -> bytes:
        encoding = headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("", "identity", "gzip", "x-gzip"):
            raise FetchError("bad_content_type", "The website uses an unsupported content encoding.")
        decomp = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding in ("gzip", "x-gzip") else None
        out = bytearray()
        while True:
            remaining = self.deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise FetchError("timeout", "The website did not answer in time.")
            if conn.sock is not None:  # a stalled peer must not outlive the overall deadline
                conn.sock.settimeout(max(0.05, min(self.timeout, remaining)))
            chunk = resp.read1(32768)  # returns what has arrived instead of blocking for a full block
            if not chunk:
                break
            if decomp is not None:
                try:
                    chunk = decomp.decompress(chunk, limit + 1 - len(out))
                except zlib.error:
                    raise FetchError("network_error", "The website sent damaged data.") from None
            out += chunk
            if len(out) > limit:
                raise FetchError("too_large", "The web page is too large.")
        return bytes(out)
