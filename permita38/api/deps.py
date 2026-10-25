"""Dependency injection: services container and authentication."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from permita38 import __version__
from permita38.core import rights
from permita38.core.config import Config
from permita38.core.netsafe import SafeFetcher
from permita38.core.repository import Repository, auto_backup, create_repository
from permita38.core.service.allocations import AllocationService
from permita38.core.service.audit import AuditService
from permita38.core.service.auth import AuthService
from permita38.core.service.busy import BusyTimeProvider
from permita38.core.service.calendar.busy_provider import CalendarBusyProvider
from permita38.core.service.calendar.service import CalendarService, ResourceFetcher
from permita38.core.service.importer import ImportService
from permita38.core.service.impressum import ImpressumService
from permita38.core.service.plan_service import PlanningService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService
from permita38.core.service.sanity import SanityService
from permita38.core.service.settings import SettingsService
from permita38.core.service.suppliers import SupplierService
from permita38.core.service.team_timeline import TeamTimelineService
from permita38.core.service.time import TimeService
from permita38.core.service.tokens import PREFIX as TOKEN_PREFIX
from permita38.core.service.tokens import TokenService
from permita38.core.service.totp import TotpService
from permita38.core.service.transfer.service import TransferService, clean_stale_tmp
from permita38.core.service.trips import TripService

COOKIE_NAME = "authorization"


@dataclass
class Services:
    config: Config
    store: Repository
    auth: AuthService
    totp: TotpService
    records: RecordService
    sales: SalesService
    time: TimeService
    settings: SettingsService
    impressum: ImpressumService
    imports: ImportService
    calendar: CalendarService
    busy: BusyTimeProvider
    planning: PlanningService
    allocations: AllocationService
    suppliers: SupplierService
    sanity: SanityService
    trips: TripService
    team_timeline: TeamTimelineService
    transfer: TransferService
    tokens: TokenService
    audit: AuditService

    @classmethod
    def create(cls, config: Config, *, calendar_fetcher: ResourceFetcher | None = None) -> "Services":
        """``calendar_fetcher`` exists for tests (a local fake server); production always uses the SSRF-safe fetcher."""
        store = create_repository(config)
        records = RecordService(store)
        auth = AuthService(store, config.secret_key, config.token_ttl_hours)
        user_agent = f"permitA38/{__version__} (+https://github.com/roggenbrot42/permitA38)"
        calendar = CalendarService(store, calendar_fetcher or SafeFetcher(user_agent))
        busy = CalendarBusyProvider(store, on_request=calendar.schedule_stale_refresh)
        return cls(
            busy=busy,
            planning=PlanningService(store, busy),
            allocations=AllocationService(store),
            suppliers=SupplierService(store),
            sanity=SanityService(store),
            trips=TripService(store),
            config=config,
            store=store,
            auth=auth,
            totp=TotpService(auth),
            records=records,
            sales=SalesService(store, records),
            imports=ImportService(store, numbering=None),
            time=TimeService(store),
            settings=SettingsService(store),
            team_timeline=TeamTimelineService(store),
            transfer=TransferService(store, config.data_dir / "tmp"),
            tokens=TokenService(store),
            audit=AuditService(store),
            impressum=ImpressumService(
                SafeFetcher(user_agent),
                enabled=config.features.impressum_lookup,
            ),
            calendar=calendar,
        )


def startup_services(services: Services) -> None:
    """Open the database (migrating if needed; refuses a newer one) and take the daily backup."""
    services.store.startup()
    clean_stale_tmp(services.config.data_dir / "tmp", store=services.store)
    db = services.config.database
    if db.auto_backup:
        auto_backup(services.store, db.backup_keep)
    services.audit.prune()  # audit rows older than a year


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]
_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/auth/login")


SESSION_ONLY_MESSAGE = "This route cannot be used with an API token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUDIT_SCOPE_KEY = "permita38.audit"  # request.scope: (token id, owner id) of a request authenticated by a token


def client_ip(request: Request, config: Config) -> str:
    """Client address for the invalid-token throttle. Behind a trusted proxy the last ``X-Forwarded-For`` entry (the
    one the proxy itself appended) is used, never an entry the client could have chosen. Correct only behind exactly
    one proxy that overwrites ``X-Forwarded-For`` (docs/api-tokens.md)."""
    if config.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def _unauthenticated() -> HTTPException:
    return HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def _route_is_session_only(request: Request) -> bool:
    route = request.scope.get("route")
    return hasattr(route, "dependant") and is_session_only(route)


def _token_principal(request: Request, services: "Services", bearer: str) -> dict:
    principal = services.tokens.authenticate(bearer)
    if principal is None:
        throttle = services.auth.throttle
        key = f"ip:{client_ip(request, services.config)}"
        try:
            throttle.check(key)  # a locked-out client gets 429 + Retry-After instead of the 401 ...
        finally:
            throttle.fail(key)  # ... and the failure is counted either way
        raise _unauthenticated()
    auth = principal["_auth"]
    auth["safe_request"] = request.method in SAFE_METHODS
    request.scope[AUDIT_SCOPE_KEY] = (auth["token_id"], principal["id"])
    if _route_is_session_only(request):
        raise HTTPException(403, SESSION_ONLY_MESSAGE)
    if auth["read_only"] and request.method not in SAFE_METHODS:
        raise HTTPException(403, "Forbidden")  # whatever the route declares
    return principal


def get_current_user(
    request: Request,
    services: ServicesDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """The signed-in principal: a user session (cookie or bearer JWT), or the owner of an API token (bearer ``pat_``).

    A ``pat_`` value is only ever tried as a token, anything else only as a session; a token in the cookie is refused."""
    if creds and creds.credentials:
        bearer = creds.credentials
        if bearer.startswith(TOKEN_PREFIX):
            return _token_principal(request, services, bearer)
        user = services.auth.user_from_token(bearer)
    else:
        cookie = request.cookies.get(COOKIE_NAME)
        user = services.auth.user_from_token(cookie) if cookie and not cookie.startswith(TOKEN_PREFIX) else None
    if not user:
        raise _unauthenticated()
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


def _session_only(user: CurrentUser) -> dict:
    if (user.get("_auth") or {}).get("kind") == "token":  # also enforced in get_current_user; this is the marker
        raise HTTPException(403, SESSION_ONLY_MESSAGE)
    return user


_session_only.session_only = True  # type: ignore[attr-defined]
#: Route dependency for credentials management (tokens, passwords, two-factor, users) and settings writes: an API
#: token gets 403 here whatever its scope. Walked by tests (``is_session_only``) and by ``get_current_user``.
SessionOnly = Depends(_session_only)


def is_session_only(route: Any) -> bool:
    """True when a route carries the ``SessionOnly`` dependency."""

    def walk(dep: Any) -> bool:
        return bool(getattr(dep.call, "session_only", False)) or any(walk(sub) for sub in dep.dependencies)

    return walk(route.dependant)


def Needs(area: str, op: rights.Op = "read", *, full: bool = False) -> Any:
    """Route dependency: the signed-in user's role must have some access (any scope) to `area` for `op`, else 403.

    The requirement is stored on the dependency (``.rbac``) so tests can enumerate it for every route. Routes whose
    scope matters (own records only, restricted views) get the user and ask `rights.scope_for` in the service.
    `full=True` additionally demands the ``all`` scope (for endpoints that have no restricted view).
    """
    if area not in rights.AREAS:
        raise ValueError(f"unknown area {area!r}")

    def check(user: CurrentUser) -> dict:
        got = rights.scope_for(user, area, op)
        if got == rights.Scope.NONE or (full and got != rights.Scope.ALL):
            raise HTTPException(403, "Forbidden")
        return user

    check.rbac = (area, op, full)  # type: ignore[attr-defined]
    return Depends(check)


def declared(route: Any) -> list[tuple[str, str, bool]]:
    """The (area, op, full) requirements found in a route's dependency tree."""
    found: list[tuple[str, str, bool]] = []

    def walk(dep: Any) -> None:
        spec = getattr(dep.call, "rbac", None)
        if spec:
            found.append(spec)
        for sub in dep.dependencies:
            walk(sub)

    walk(route.dependant)
    return found
