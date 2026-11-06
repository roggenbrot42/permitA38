from typing import Annotated, Any

from fastapi import APIRouter, Query

from permita38.api.deps import Needs, ServicesDep
from permita38.core.models import TimerStartIn, TimerStopIn, TimerUpdateIn, Timesheet

router = APIRouter(tags=["time"])
routers = [router]


@router.get("/timer", summary="the running timer of the current user")
def get_timer(user: Annotated[dict, Needs("timer")], services: ServicesDep) -> dict[str, Any]:
    return services.time.get_timer(user)


@router.post("/timer/start")
def start_timer(
    body: TimerStartIn, user: Annotated[dict, Needs("timer", "write")], services: ServicesDep
) -> dict[str, Any]:
    return services.time.start_timer(user, body.project_id, body.task_id, body.note)


@router.patch("/timer", summary="change project, task or note of the running timer (the start time is kept)")
def update_timer(
    body: TimerUpdateIn, user: Annotated[dict, Needs("timer", "write")], services: ServicesDep
) -> dict[str, Any]:
    return services.time.update_timer(user, body.model_dump(exclude_unset=True))


@router.post("/timer/stop", summary="stop the timer and save the time entry")
def stop_timer(
    body: TimerStopIn, user: Annotated[dict, Needs("timer", "write")], services: ServicesDep
) -> dict[str, Any]:
    out = services.time.stop_timer(user, body.note)
    if out.get("entry"):
        out["sanity_warnings"] = services.sanity.check("entries", out["entry"])
    return out


@router.get("/dashboard", summary="weekly goal, remaining hours and per-day breakdown")
def dashboard(
    user: Annotated[dict, Needs("timer")], services: ServicesDep, user_id: str | None = None
) -> dict[str, Any]:
    return services.time.dashboard(user, user_id)


@router.get(
    "/projects/{rid}/timesheet",
    response_model=Timesheet,
    summary="who booked how many hours on a project, per week or month",
)
def project_timesheet(
    rid: str,
    user: Annotated[dict, Needs("time")],
    services: ServicesDep,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Annotated[str | None, Query(max_length=10)] = None,
    group: Annotated[str, Query(max_length=10)] = "week",
):
    return services.timesheet.timesheet(user, rid, from_, to, group)
