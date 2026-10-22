"""Dependency injection: services container and authentication."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from permita38.core.config import Config
from permita38.core.repository import Repository, auto_backup, create_repository
from permita38.core.service.auth import AuthService
from permita38.core.service.records import RecordService
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
    totp: TotpService
    records: RecordService
    sales: SalesService
    time: TimeService
    settings: SettingsService

    @classmethod
    def create(cls, config: Config) -> "Services":
        store = create_repository(config)
        records = RecordService(store)
        auth = AuthService(store, config.secret_key, config.token_ttl_hours)
        return cls(
            config=config,
            store=store,
            auth=auth,
            totp=TotpService(auth),
            records=records,
            sales=SalesService(store, records),
            time=TimeService(store),
            settings=SettingsService(store),
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


def require_boss(user: CurrentUser) -> dict:
    if user["role"] != "boss":
        raise HTTPException(403, "Forbidden")
    return user


BossUser = Annotated[dict, Depends(require_boss)]
