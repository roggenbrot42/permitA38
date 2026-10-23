"""Business trips (docs/specs/business-trips.md). Thin: rights are declared here, the logic is in core/service/trips.py."""

from typing import Annotated

from fastapi import APIRouter, Response

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import stored
from permita38.core import models

router = APIRouter(prefix="/trips", tags=["trips"])
routers = [router]

_PDF = {200: {"content": {"application/pdf": {}}}}


def _body(body: models.TripIn) -> dict:
    return body.model_dump(by_alias=True, exclude_none=True)


@router.get("", response_model=list[models.Trip], response_model_exclude_none=True, name="list_trips")
def list_trips(user: Annotated[dict, Needs("trips", "read")], services: ServicesDep):
    return stored(models.Trip, services.trips.list(user))


@router.post("", response_model=models.Trip, response_model_exclude_none=True, name="create_trip")
def create_trip(
    body: models.TripIn,
    user: Annotated[dict, Needs("trips", "write")],
    services: ServicesDep,
    force: bool = False,  # skips the checklist check when status is final (contract: Finalizing)
):
    return stored(models.Trip, services.trips.create(user, _body(body), force))


@router.post("/preview", response_model=models.TripFigures, name="preview_trip")
def preview_trip(body: models.TripIn, user: Annotated[dict, Needs("trips", "write")], services: ServicesDep):
    """The per-day figures and totals of a trip body; nothing is saved. Needs write access (contract)."""
    return stored(models.TripFigures, services.trips.preview(user, _body(body)))


@router.get("/{rid}", response_model=models.Trip, response_model_exclude_none=True, name="get_trip")
def get_trip(rid: str, user: Annotated[dict, Needs("trips", "read")], services: ServicesDep):
    return stored(models.Trip, services.trips.get(user, rid))


@router.put("/{rid}", response_model=models.Trip, response_model_exclude_none=True, name="update_trip")
def update_trip(
    rid: str,
    body: models.TripIn,
    user: Annotated[dict, Needs("trips", "write")],
    services: ServicesDep,
    force: bool = False,
):
    return stored(models.Trip, services.trips.update(user, rid, _body(body), force))


@router.delete("/{rid}", status_code=204, name="delete_trip")
def delete_trip(rid: str, user: Annotated[dict, Needs("trips", "write")], services: ServicesDep):
    services.trips.delete(user, rid)
    return Response(status_code=204)


@router.post("/{rid}/reopen", response_model=models.Trip, response_model_exclude_none=True, name="reopen_trip")
def reopen_trip(rid: str, user: Annotated[dict, Needs("trips", "write", full=True)], services: ServicesDep):
    """Boss only: a final trip becomes a draft again so that it can be changed."""
    return stored(models.Trip, services.trips.reopen(user, rid))


@router.get("/{rid}/statement.pdf", response_class=Response, responses=_PDF, name="trip_statement_pdf")
def trip_statement(rid: str, user: Annotated[dict, Needs("trips", "read")], services: ServicesDep):
    data, name = services.trips.statement(user, rid)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})
