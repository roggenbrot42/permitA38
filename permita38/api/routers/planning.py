from typing import Annotated, Any

from fastapi import APIRouter, Query

from permita38.api.deps import CurrentUser, ServicesDep, needs

PlanningUser = Annotated[dict, needs("planning.view")]
router = APIRouter(tags=["planning"])
routers = [router]


@router.get("/forecast", summary="week-by-week capacity vs. contracts")
def forecast(
    _: PlanningUser,
    services: ServicesDep,
    weeks: int = Query(26, ge=1, le=156),
    user_id: str | None = None,
    offers: int = 1,
) -> dict[str, Any]:
    return services.time.forecast(weeks, user_id, bool(offers))


@router.get("/holidays", summary="public holidays of the configured federal state")
def holidays(_: CurrentUser, services: ServicesDep, year: int | None = Query(None, ge=2000, le=2100)) -> dict[str, str]:
    from datetime import date

    return services.time.holidays(year or date.today().year, services.settings.get()["state"])
