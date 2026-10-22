from datetime import date
from typing import Any

from fastapi import APIRouter, Query

from permita38.api.deps import BossUser, ServicesDep
from permita38.core import calc

router = APIRouter(tags=["finance"])
routers = [router]


@router.get("/finance", summary="revenue, expenses, profit, VAT and tax estimates for a year")
def finance(_: BossUser, services: ServicesDep, year: int | None = Query(None, ge=2000, le=2100)) -> dict[str, Any]:
    return calc.finance(services.store, year or date.today().year)


@router.get("/project-report", summary="hours, revenue, costs and margin per project")
def project_report(_: BossUser, services: ServicesDep) -> dict[str, Any]:
    return calc.project_report(services.store)
