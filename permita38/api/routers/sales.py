import re
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from permita38.api.deps import ServicesDep, needs
from permita38.core import validation as v
from permita38.core.models import Document

InvoiceManager = Annotated[dict, needs("invoices.manage")]
InvoiceViewer = Annotated[dict, needs("invoices.view")]
OfferManager = Annotated[dict, needs("offers.manage")]
OfferViewer = Annotated[dict, needs("offers.view")]
BillTime = Annotated[dict, needs("invoices.manage", "time.view_all")]
AcceptOffer = Annotated[dict, needs("offers.manage", "projects.manage", "planning.manage")]
OfferToInvoice = Annotated[dict, needs("offers.view", "invoices.manage")]
router = APIRouter(tags=["sales"])
routers = [router]


class FromTime(BaseModel):
    customer_id: v.OptId = None
    project_id: v.OptId = None
    date_from: v.OptDate = Field(None, alias="from")
    date_to: v.OptDate = Field(None, alias="to")


def _pdf(services, coll: str, rid: str) -> Response:
    data, name = services.sales.pdf(coll, rid)
    safe = re.sub(r"[^\w.-]", "_", name)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{safe}"'})


@router.post("/invoices/from-time", response_model=Document, response_model_exclude_none=True)
def invoice_from_time(body: FromTime, _: BillTime, services: ServicesDep):
    """Group unbilled, billable time by project/task/rate into a draft invoice."""
    return services.sales.invoice_from_time(body.customer_id, body.project_id, body.date_from, body.date_to)


@router.post("/invoices/{rid}/finalize", response_model=Document, response_model_exclude_none=True)
def finalize_invoice(rid: str, _: InvoiceManager, services: ServicesDep):
    return services.sales.finalize("invoices", rid)


@router.get("/invoices/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def invoice_pdf(rid: str, _: InvoiceViewer, services: ServicesDep):
    return _pdf(services, "invoices", rid)


@router.post("/offers/{rid}/finalize", response_model=Document, response_model_exclude_none=True)
def finalize_offer(rid: str, _: OfferManager, services: ServicesDep):
    return services.sales.finalize("offers", rid)


@router.get("/offers/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def offer_pdf(rid: str, _: OfferViewer, services: ServicesDep):
    return _pdf(services, "offers", rid)


@router.post("/offers/{rid}/accept", summary="mark accepted; creates a project and, if planned, a contract")
def accept_offer(rid: str, _: AcceptOffer, services: ServicesDep) -> dict[str, Any]:
    return services.sales.accept_offer(rid)


@router.post("/offers/{rid}/to-invoice", response_model=Document, response_model_exclude_none=True)
def offer_to_invoice(rid: str, _: OfferToInvoice, services: ServicesDep):
    return services.sales.offer_to_invoice(rid)
