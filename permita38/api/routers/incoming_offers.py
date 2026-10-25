"""Incoming offers of suppliers (docs/contracts/subcontractors.md, "Incoming offers"). Thin: the logic is in
core/service/procurement.py."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import StoredJSON, stored
from permita38.core import models
from permita38.core import validation as v

router = APIRouter(prefix="/incoming-offers", tags=["procurement"])
routers = [router]

OfferId = Annotated[str, Path(pattern=v.ID_PATTERN)]
Reader = Annotated[dict, Needs("procurement", "read")]
Writer = Annotated[dict, Needs("procurement", "write")]


@router.get(
    "",
    response_model=list[models.IncomingOffer],
    response_model_exclude_none=True,
    name="list_incoming_offers",
    summary="offers of suppliers, newest first; status is the stored status or expired",
)
def list_offers(
    _: Reader,
    services: ServicesDep,
    supplier_id: Annotated[str | None, Query(pattern=v.ID_PATTERN)] = None,
    status: Literal["received", "under_review", "accepted", "rejected", "expired"] | None = None,
    project_id: Annotated[str | None, Query(pattern=v.ID_PATTERN)] = None,
):
    return stored(models.IncomingOffer, services.procurement.list(supplier_id, status, project_id, date.today()))


@router.post(
    "",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    status_code=201,
    name="create_incoming_offer",
)
def create_offer(body: models.IncomingOfferIn, _: Writer, services: ServicesDep):
    return StoredJSON(services.procurement.create(body.model_dump(), date.today()), status_code=201)


@router.get(
    "/{incoming_offer_id}",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="get_incoming_offer",
)
def get_offer(incoming_offer_id: OfferId, _: Reader, services: ServicesDep):
    return stored(models.IncomingOffer, services.procurement.get(incoming_offer_id, date.today()))


@router.put(
    "/{incoming_offer_id}",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="update_incoming_offer",
)
def update_offer(incoming_offer_id: OfferId, body: models.IncomingOfferIn, _: Writer, services: ServicesDep):
    return stored(models.IncomingOffer, services.procurement.update(incoming_offer_id, body.model_dump(), date.today()))


@router.delete("/{incoming_offer_id}", status_code=204, name="delete_incoming_offer")
def delete_offer(incoming_offer_id: OfferId, _: Writer, services: ServicesDep):
    services.procurement.delete(incoming_offer_id)
    return Response(status_code=204)


@router.post(
    "/{incoming_offer_id}/review",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="review_incoming_offer",
)
def review_offer(incoming_offer_id: OfferId, user: Writer, services: ServicesDep):
    return stored(
        models.IncomingOffer, services.procurement.transition(incoming_offer_id, "review", user, None, date.today())
    )


@router.post(
    "/{incoming_offer_id}/accept",
    response_model=dict,
    name="accept_incoming_offer",
    summary="accept an offer: a capacity supplier gets an external allocation, others a flat commitment",
)
def accept_offer(incoming_offer_id: OfferId, body: models.AcceptIn, user: Writer, services: ServicesDep):
    return services.procurement.accept(incoming_offer_id, body.model_dump(), user, date.today())


@router.post(
    "/{incoming_offer_id}/reject",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="reject_incoming_offer",
)
def reject_offer(incoming_offer_id: OfferId, body: models.RejectIn, user: Writer, services: ServicesDep):
    return stored(
        models.IncomingOffer,
        services.procurement.transition(incoming_offer_id, "reject", user, body.model_dump(), date.today()),
    )


@router.post(
    "/{incoming_offer_id}/reopen",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="reopen_incoming_offer",
)
def reopen_offer(incoming_offer_id: OfferId, user: Writer, services: ServicesDep):
    return stored(
        models.IncomingOffer, services.procurement.transition(incoming_offer_id, "reopen", user, None, date.today())
    )


@router.post(
    "/{incoming_offer_id}/unaccept",
    response_model=models.IncomingOffer,
    response_model_exclude_none=True,
    name="unaccept_incoming_offer",
)
def unaccept_offer(incoming_offer_id: OfferId, user: Writer, services: ServicesDep):
    return stored(models.IncomingOffer, services.procurement.unaccept(incoming_offer_id, user, date.today()))
