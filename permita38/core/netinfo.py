"""Who is calling? Client address resolution (spoof-resistant), privacy-friendly address hints and user-agent labels.

The socket peer address is the only thing a client cannot forge. ``X-Forwarded-For`` / ``X-Real-IP`` are honoured
ONLY when ``trust_proxy`` is on AND the peer is a trusted proxy (``forwarded_allow_ips``: comma separated addresses or
networks, or ``*`` = every peer is a proxy). The forwarded chain is then read from the RIGHT, because every proxy
appends the address it saw: entries on the left are whatever the client wrote. Hops that are trusted proxies are
skipped; the first other hop is the client. With ``*`` only the immediate peer counts as a proxy (one hop).
"""

import ipaddress
from dataclasses import dataclass

IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address
Net = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class ClientInfo:
    ip: str  # resolved client address (or the raw peer string if it is not an IP, e.g. in tests)
    ip_key: str  # throttle bucket: exact IPv4, /64 network for IPv6
    ip_hint: str  # what a session may store: /24 (IPv4) or /48 (IPv6), or the full address
    user_agent: str = ""


def _parse(value: str) -> IPAddr | None:
    value = value.strip().strip("[]")
    if value.startswith("::ffff:") and "." in value:  # IPv4-mapped IPv6
        value = value[7:]
    try:
        return ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return None


def parse_trusted(spec: str | None) -> tuple[bool, list[Net | IPAddr]]:
    """-> (trust_every_peer, explicit entries). Invalid entries are ignored (never widen trust by accident)."""
    entries: list[Net | IPAddr] = []
    star = False
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            star = True
            continue
        try:
            entries.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return star, entries


def _is_trusted(ip: IPAddr, star: bool, entries: list[Net | IPAddr], peer: IPAddr | None) -> bool:
    if any(isinstance(e, (ipaddress.IPv4Network, ipaddress.IPv6Network)) and ip in e for e in entries):
        return True
    return star and peer is not None and ip == peer


def resolve_client_ip(
    peer: str | None, headers: dict[str, str], trust_proxy: bool = False, allow_spec: str | None = "*"
) -> str:
    """The client address to use for throttling and sessions."""
    peer_s = (peer or "unknown").strip()
    peer_ip = _parse(peer_s)
    if not trust_proxy or peer_ip is None:
        return str(peer_ip) if peer_ip else peer_s
    star, entries = parse_trusted(allow_spec)
    if not _is_trusted(peer_ip, star, entries, peer_ip):
        return str(peer_ip)  # the peer is not a proxy we trust: ignore every forwarding header
    chain: list[str] = []
    xff = headers.get("x-forwarded-for", "")
    if xff:
        chain = [h.strip() for h in xff.split(",") if h.strip()]
    elif headers.get("x-real-ip"):
        chain = [headers["x-real-ip"].strip()]
    for hop in reversed(chain):
        ip = _parse(hop)
        if ip is None:
            return str(peer_ip)  # garbage in the chain: do not guess, fall back to the peer
        if not _is_trusted(ip, star, entries, peer_ip):
            return str(ip)
    return str(peer_ip)


def ip_bucket(ip: str) -> str:
    """Throttle bucket: IPv4 exact, IPv6 grouped by /64 (one subscriber usually owns a whole /64)."""
    addr = _parse(ip)
    if addr is None:
        return ip[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


def ip_hint(ip: str, full: bool = False) -> str:
    addr = _parse(ip)
    if addr is None:
        return ip[:64]
    if full:
        return str(addr)
    bits = 48 if isinstance(addr, ipaddress.IPv6Address) else 24
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


def client_info(
    peer: str | None, headers: dict[str, str], trust_proxy: bool, allow_spec: str | None, store_ip: bool
) -> ClientInfo:
    ip = resolve_client_ip(peer, headers, trust_proxy, allow_spec)
    return ClientInfo(
        ip=ip,
        ip_key="ip:" + ip_bucket(ip),
        ip_hint=ip_hint(ip, store_ip),
        user_agent=(headers.get("user-agent") or "")[:200],
    )


def describe_user_agent(ua: str) -> str:
    """'Firefox on Linux' style label for the sessions page (no fingerprinting, just a human hint)."""
    u = ua or ""
    if not u:
        return "Unknown device"
    browser = next(
        (name for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                  ("Safari/", "Safari"), ("curl/", "curl"), ("python-", "Python"), ("httpx", "Python"))
         if token in u),
        "Browser",
    )
    system = next(
        (name for token, name in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iOS"),
                                  ("Mac OS X", "macOS"), ("Linux", "Linux"), ("CrOS", "ChromeOS"))
         if token in u),
        "",
    )
    return f"{browser} on {system}" if system else browser
