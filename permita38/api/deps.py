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
from permita38.core.service.auth import AuthService
from permita38.core.service.busy import BusyTimeProvider, NullBusyProvider
from permita38.core.service.importer import ImportService
from permita38.core.service.impressum import ImpressumService
from permita38.core.service.plan_service import PlanningService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService
from permita38.core.service.sanity import SanityService
from permita38.core.service.settings import SettingsService
from permita38.core.service.time import TimeService
from permita38.core.service.totp import TotpService

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
    busy: BusyTimeProvider
    planning: PlanningService
    sanity: SanityService

    @classmethod
    def create(cls, config: Config) -> "Services":
        store = create_repository(config)
        records = RecordService(store)
        auth = AuthService(store, config.secret_key, config.token_ttl_hours)
        busy: BusyTimeProvider = NullBusyProvider()  # the calendar module replaces this (shared contract)
        return cls(
            busy=busy,
            planning=PlanningService(store, busy),
            sanity=SanityService(store),
            config=config,
            store=store,
            auth=auth,
            totp=TotpService(auth),
            records=records,
            sales=SalesService(store, records),
            imports=ImportService(store, numbering=None),
            time=TimeService(store),
            settings=SettingsService(store),
            impressum=ImpressumService(
                SafeFetcher(f"permitA38/{__version__} (+https://github.com/roggenbrot42/permitA38)"),
                enabled=config.features.impressum_lookup,
            ),
        )


def startup_services(services: Services) -> None:
    """Open the database (migrating if needed; refuses a newer one) and take the daily backup."""
    services.store.startup()
    db = services.config.database
    if db.auto_backup:
        auto_backup(services.store, db.backup_keep)


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]
_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/auth/login")


def get_current_user(
    request: Request,
    services: ServicesDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    token = creds.credentials if creds else request.cookies.get(COOKIE_NAME)
    user = services.auth.user_from_token(token) if token else None
    if not user:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


def Needs(area: str, op: rights.Op = "read", *, full: bool = False) -> Any:
    """Route dependency: the signed-in user's role must have some access (any scope) to `area` for `op`, else 403.

    The requirement is stored on the dependency (``.rbac``) so tests can enumerate it for every route. Routes whose
    scope matters (own records only, restricted views) get the user and ask `rights.scope` in the service.
    `full=True` additionally demands the ``all`` scope (for endpoints that have no restricted view).
    """
    if area not in rights.AREAS:
        raise ValueError(f"unknown area {area!r}")

    def check(user: CurrentUser) -> dict:
        got = rights.scope(user.get("role"), area, op)
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
