from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel

from permita38.api.deps import Needs, ServicesDep

router = APIRouter(tags=["time"])
routers = [router]


class TimerStart(BaseModel):
    project_id: str | None = None
    task_id: str | None = None
    note: str = ""


class TimerStop(BaseModel):
    note: str | None = None


@router.get("/timer", summary="the running timer of the current user")
def get_timer(user: Annotated[dict, Needs("timer")], services: ServicesDep) -> dict[str, Any]:
    return services.time.get_timer(user)


@router.post("/timer/start")
def start_timer(
    body: TimerStart, user: Annotated[dict, Needs("timer", "write")], services: ServicesDep
) -> dict[str, Any]:
    return services.time.start_timer(user, body.project_id, body.task_id, body.note)


@router.post("/timer/stop", summary="stop the timer and save the time entry")
def stop_timer(
    body: TimerStop, user: Annotated[dict, Needs("timer", "write")], services: ServicesDep
) -> dict[str, Any]:
    return services.time.stop_timer(user, body.note)


@router.get("/dashboard", summary="weekly goal, remaining hours and per-day breakdown")
def dashboard(
    user: Annotated[dict, Needs("timer")], services: ServicesDep, user_id: str | None = None
) -> dict[str, Any]:
    return services.time.dashboard(user, user_id)
