"""Team timeline: who is away and when (docs/contracts/team-timeline.md). Read-only; the level is the viewer's."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from permita38.api.deps import Needs, ServicesDep
from permita38.core import validation as v
from permita38.core.models import TeamTimeline

router = APIRouter(tags=["team"])
routers = [router]


class TimelineQuery(BaseModel):
    """Unknown parameters are a 422. The window bounds (at most 365 days, end not before start) are checked by the
    service, which answers with a plain 422 message."""

    model_config = ConfigDict(extra="forbid")
    start: v.ISODate
    end: v.ISODate


@router.get("/team/timeline", response_model=TeamTimeline, summary="who is away and when, at the viewer's level")
def team_timeline(
    user: Annotated[dict, Needs("team_timeline")],
    services: ServicesDep,
    query: Annotated[TimelineQuery, Query()],
):
    return services.team_timeline.timeline(user, date.fromisoformat(query.start), date.fromisoformat(query.end))
