from typing import Any

from fastapi import APIRouter, Query

from permita38.api.deps import BossUser, CurrentUser, ServicesDep

router = APIRouter(tags=["planning"])
routers = [router]


@router.get("/forecast", summary="week-by-week capacity vs. contracts")
def forecast(
    _: BossUser,
    services: ServicesDep,
    weeks: int = Query(26, ge=1, le=156),
    user_id: str | None = None,
    offers: int = 1,
) -> dict[str, Any]:
    return services.time.forecast(weeks, user_id, bool(offers))


@router.get("/holidays", summary="public holidays of the configured federal state")
def holidays(_: CurrentUser, services: ServicesDep, year: int | None = None) -> dict[str, str]:
    from datetime import date

    return services.time.holidays(year or date.today().year, services.settings.get()["state"])
