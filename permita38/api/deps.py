"""Dependency injection: services container and authentication."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from permita38 import __version__
from permita38.api.rbac import Rbac
from permita38.core.config import Config
from permita38.core.netsafe import SafeFetcher
from permita38.core.repository import Repository, auto_backup, create_repository
from permita38.core.rights import ALL_RIGHTS
from permita38.core.service.auth import AuthService
from permita38.core.service.impressum import ImpressumService
from permita38.core.service.records import RecordService
from permita38.core.service.roles import RoleService, rights_of_principal
from permita38.core.service.sales import SalesService
from permita38.core.service.settings import SettingsService
from permita38.core.service.time import TimeService
from permita38.core.service.totp import TotpService

COOKIE_NAME = "authorization"


@dataclass
class Services:
    config: Config
    store: Repository
    auth: AuthService
    roles: RoleService
    totp: TotpService
    records: RecordService
    sales: SalesService
    time: TimeService
    settings: SettingsService
    impressum: ImpressumService

    @classmethod
    def create(cls, config: Config) -> "Services":
        store = create_repository(config)
        records = RecordService(store)
        auth = AuthService(store, config.secret_key, config.token_ttl_hours)
        return cls(
            config=config,
            store=store,
            auth=auth,
            roles=auth.roles,
            totp=TotpService(auth),
            records=records,
            sales=SalesService(store, records),
            time=TimeService(store),
            settings=SettingsService(store, auth),
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
    return services.auth.principal(user)


CurrentUser = Annotated[dict, Depends(get_current_user)]


def needs(*all_of: str, any_of: tuple[str, ...] = ()):
    """Dependency: the user must hold all of `all_of` and at least one of `any_of` (if given), else 403.

    The declaration is attached to the dependency so that tests can enumerate what every route requires.
    """
    unknown = set(all_of) | set(any_of)
    unknown -= ALL_RIGHTS
    if unknown:
        raise ValueError(f"unknown right(s): {sorted(unknown)}")
    spec = Rbac(tuple(all_of), tuple(any_of))

    def dependency(user: CurrentUser) -> dict:
        if not spec.allows(rights_of_principal(user)):
            raise HTTPException(403, "Forbidden")
        return user

    dependency.rbac = spec  # type: ignore[attr-defined]
    return Depends(dependency)
