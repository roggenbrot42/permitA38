from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from permita38.api.deps import BossUser, CurrentUser, ServicesDep
from permita38.core import validation as v
from permita38.core.errors import ServiceError
from permita38.core.models import DocumentIn, PlanIn, ScenarioChanges, ScenarioIn

router = APIRouter(tags=["planning"])
routers = [router]


class SimulateIn(BaseModel):
    changes: ScenarioChanges = ScenarioChanges()
    weeks: int = Field(26, ge=1, le=156)
    first: v.OptDate = None
    user_id: v.OptId = None


class MoveCheckIn(BaseModel):
    plan: PlanIn


class SuggestVacationIn(BaseModel):
    user_id: v.Id
    days: int = Field(..., ge=1, le=60)
    window_from: v.ISODate
    window_to: v.ISODate


class SuggestStartIn(BaseModel):
    hours: v.TotalHours
    deadline: v.ISODate
    earliest: v.OptDate = None
    user_ids: v.IdList = []


class ApplyIn(BaseModel):
    changes: ScenarioChanges = ScenarioChanges()
    confirm: bool = False


class ConfirmIn(BaseModel):
    confirm: bool = False


class ExtendIn(BaseModel):
    new_end: v.ISODate
    reason: v.Note | None = None


def _need_confirm(flag: bool) -> None:
    if not flag:
        raise ServiceError(400, "Confirm the change (confirm: true) before anything is written")


@router.get("/forecast", summary="week-by-week capacity vs. commitments, with revenue basis")
def forecast(
    _: BossUser,
    services: ServicesDep,
    weeks: int = Query(26, ge=1, le=156),
    user_id: str | None = None,
    offers: int = 1,
) -> dict[str, Any]:
    return services.planning.forecast(weeks, user_id or None, bool(offers))


@router.post("/plan/simulate", summary="what-if: recompute the weeks for a change set (writes nothing)")
def simulate(body: SimulateIn, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    first = date.fromisoformat(body.first) if body.first else None
    return services.planning.simulate(body.changes.model_dump(), body.weeks, first, body.user_id)


@router.post("/plan/check-offer", summary="feasibility and consistency warnings for an offer draft")
def check_offer(body: DocumentIn, _: BossUser, services: ServicesDep, offer_id: v.OptId = None) -> list[dict]:
    return services.planning.check_offer(body.model_dump(exclude_none=True), offer_id)


@router.post("/plan/what-must-move", summary="smallest moves or trims that make a plan fit")
def what_must_move(body: MoveCheckIn, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    return services.planning.what_must_move(body.plan.model_dump(exclude_none=True))


@router.post("/plan/suggest-vacation", summary="best windows for N working days of vacation")
def suggest_vacation(body: SuggestVacationIn, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    return services.planning.suggest_vacation(
        body.user_id, body.days, date.fromisoformat(body.window_from), date.fromisoformat(body.window_to)
    )


@router.post("/plan/suggest-start", summary="earliest feasible start for an offer of N hours")
def suggest_start(body: SuggestStartIn, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    return services.planning.suggest_start(
        body.hours,
        date.fromisoformat(body.deadline),
        date.fromisoformat(body.earliest) if body.earliest else None,
        body.user_ids or None,
    )


@router.get("/plan/vacation", summary="vacation entitlement: used, planned and remaining working days")
def vacation(_: BossUser, services: ServicesDep, user_id: str, year: int = Query(..., ge=2000, le=2100)) -> dict:
    return services.planning.entitlement(user_id, year)


@router.post("/plan/apply", summary="write a change set (needs confirm: true)")
def apply(body: ApplyIn, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    _need_confirm(body.confirm)
    return services.planning.apply(body.changes.model_dump())


@router.get("/plan/scenarios", summary="your saved what-if scenarios")
def list_scenarios(user: BossUser, services: ServicesDep) -> list[dict]:
    return services.planning.list_scenarios(user)


@router.post("/plan/scenarios", summary="save a named scenario")
def create_scenario(body: ScenarioIn, user: BossUser, services: ServicesDep) -> dict:
    return services.planning.save_scenario(user, None, body.name, body.changes.model_dump())


@router.put("/plan/scenarios/{rid}")
def update_scenario(rid: str, body: ScenarioIn, user: BossUser, services: ServicesDep) -> dict:
    return services.planning.save_scenario(user, rid, body.name, body.changes.model_dump())


@router.delete("/plan/scenarios/{rid}", status_code=204)
def delete_scenario(rid: str, user: BossUser, services: ServicesDep) -> None:
    services.planning.delete_scenario(user, rid)


@router.post("/plan/scenarios/{rid}/apply", summary="apply a saved scenario (needs confirm: true)")
def apply_scenario(rid: str, body: ConfirmIn, user: BossUser, services: ServicesDep) -> dict[str, Any]:
    _need_confirm(body.confirm)
    return services.planning.apply_scenario(user, rid)


@router.post("/projects/{rid}/extend", summary="extend the end of a project period")
def extend_project(rid: str, body: ExtendIn, user: BossUser, services: ServicesDep) -> dict[str, Any]:
    proj = services.planning.extend_project(rid, body.new_end, body.reason, user)
    return {"project": proj, "planning_warnings": services.planning.warnings_for("projects", proj)}


@router.get("/projects/{rid}/planning", summary="project timeline, extensions and period warnings")
def project_planning(rid: str, _: BossUser, services: ServicesDep) -> dict[str, Any]:
    return services.planning.project_timeline(rid)


@router.get("/holidays", summary="public holidays of the configured federal state")
def holidays(_: CurrentUser, services: ServicesDep, year: int | None = Query(None, ge=2000, le=2100)) -> dict[str, str]:
    from datetime import date as _date

    return services.time.holidays(year or _date.today().year, services.settings.get()["state"])
