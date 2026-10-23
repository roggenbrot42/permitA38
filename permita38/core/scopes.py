"""Scopes of personal API tokens.

A scope is ``read:<area>`` or ``write:<area>``. ``read:*`` grants every read scope. There is deliberately no
``write:*``: every write scope must be listed explicitly. A write scope does not imply the read scope of the same area.

Areas map to API paths (first segment after ``/api``). Paths that are not mapped are DENIED for tokens, and the
management endpoints (``/auth/*``, ``/users/{id}/totp|sessions|tokens``) are never reachable with a token, so a token
can neither mint new tokens nor change passwords or two-factor settings.

A token never exceeds the user it belongs to: the user's own role/permissions are still checked on every request, and
at creation only scopes in :func:`allowed_scopes` can be granted. When the roles-and-rights work lands,
:func:`allowed_scopes` is the single place to intersect the catalog with the user's rights.
"""

import re

READ_ALL = "read:*"

# area -> (api path segments that belong to it)
AREAS: dict[str, tuple[str, ...]] = {
    "time": ("entries", "timer", "dashboard", "absences", "holidays"),
    "planning": ("contracts", "forecast"),
    "customers": ("customers",),
    "projects": ("projects", "tasks"),
    "invoices": ("offers", "invoices"),
    "expenses": ("expenses",),
    "finance": ("finance", "project-report"),
    "settings": ("settings", "templates", "bootstrap"),
    "users": ("users",),
    "files": ("attachments",),
}
SEGMENT_TO_AREA = {seg: area for area, segs in AREAS.items() for seg in segs}

# areas that cannot be written with a token at all (user administration happens in the web app, with a session)
READ_ONLY_AREAS = {"users", "finance"}
WRITE_AREAS = [a for a in AREAS if a not in READ_ONLY_AREAS]

ALL_SCOPES = [READ_ALL] + [f"read:{a}" for a in AREAS] + [f"write:{a}" for a in WRITE_AREAS]

# members may only use what the web app lets them use
MEMBER_AREAS_READ = {"time", "projects", "users"}
MEMBER_AREAS_WRITE = {"time"}

_NEVER_FOR_TOKENS = re.compile(r"^/(auth(/|$)|users/[^/]+/(totp|sessions|tokens)(/|$))")
_SAFE = {"GET", "HEAD", "OPTIONS"}


def is_valid_scope(scope: str) -> bool:
    return scope in ALL_SCOPES


def allowed_scopes(user: dict) -> list[str]:
    """Scopes this user may put on a token (hook for the rights catalog)."""
    if user.get("role") == "boss":
        return list(ALL_SCOPES)
    return [READ_ALL] + [f"read:{a}" for a in AREAS if a in MEMBER_AREAS_READ] + [
        f"write:{a}" for a in WRITE_AREAS if a in MEMBER_AREAS_WRITE
    ]


def normalize_scopes(scopes: list[str]) -> list[str]:
    out: list[str] = []
    for s in scopes:
        if s not in out:
            out.append(s)
    return out


def required_scope(method: str, path: str) -> str | None:
    """Scope a request needs, or None when tokens may not call it at all. ``path`` is relative to ``/api``."""
    if _NEVER_FOR_TOKENS.match(path):
        return None
    segment = path.strip("/").split("/", 1)[0]
    area = SEGMENT_TO_AREA.get(segment)
    if area is None:
        return None
    if method.upper() in _SAFE:
        return f"read:{area}"
    return f"write:{area}"


def token_allows(scopes: list[str], needed: str | None) -> bool:
    if needed is None:
        return False
    if needed in scopes:
        return True
    return needed.startswith("read:") and READ_ALL in scopes
