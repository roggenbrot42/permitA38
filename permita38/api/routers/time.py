from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from permita38.api.deps import CurrentUser, ServicesDep

router = APIRouter(tags=["time"])
routers = [router]


class TimerStart(BaseModel):
    project_id: str | None = None
    task_id: str | None = None
    note: str = Field("", max_length=5000)
    at: int | None = Field(None, ge=0, le=4_102_444_800, description="client time (epoch seconds) of a queued action")


class TimerStop(BaseModel):
    note: str | None = Field(None, max_length=5000)
    at: int | None = Field(None, ge=0, le=4_102_444_800, description="client time (epoch seconds) of a queued action")


@router.get("/timer", summary="the running timer of the current user")
def get_timer(user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    return services.time.get_timer(user)


@router.post("/timer/start")
def start_timer(body: TimerStart, user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    return services.time.start_timer(user, body.project_id, body.task_id, body.note, body.at)


@router.post("/timer/stop", summary="stop the timer and save the time entry")
def stop_timer(body: TimerStop, user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    return services.time.stop_timer(user, body.note, body.at)


@router.get("/dashboard", summary="weekly goal, remaining hours and per-day breakdown")
def dashboard(user: CurrentUser, services: ServicesDep, user_id: str | None = None) -> dict[str, Any]:
    return services.time.dashboard(user, user_id)
