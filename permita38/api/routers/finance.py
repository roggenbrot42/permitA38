from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from permita38.api.deps import Needs, ServicesDep
from permita38.core import calc
from permita38.core.validation import ID_PATTERN

router = APIRouter(tags=["finance"])
routers = [router]


@router.get("/finance", summary="revenue, expenses, profit, VAT and tax estimates for a year")
def finance(
    _: Annotated[dict, Needs("finance")], services: ServicesDep, year: int | None = Query(None, ge=2000, le=2100)
) -> dict[str, Any]:
    return calc.finance(services.store, year or date.today().year)


@router.get("/project-report", summary="hours, revenue, costs and margin per project")
def project_report(_: Annotated[dict, Needs("finance")], services: ServicesDep) -> dict[str, Any]:
    rows = calc.project_report(services.store)
    committed = services.procurement.committed_by_project()
    for pid, row in rows.items():
        row["committed_external_cost"] = committed.get(pid, 0.0)
    return rows


@router.get("/finance/commitments", summary="open commitments to suppliers and the customer share hint")
def commitments(
    _: Annotated[dict, Needs("procurement", "read")],
    services: ServicesDep,
    supplier_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    project_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
) -> dict[str, Any]:
    return services.procurement.commitments(supplier_id, project_id)
