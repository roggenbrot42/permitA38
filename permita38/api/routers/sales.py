import re
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import Field

from permita38.api.deps import Needs, ServicesDep
from permita38.core import validation as v
from permita38.core.models import Document

router = APIRouter(tags=["sales"])
routers = [router]


class DocumentResult(Document):
    """A saved offer or invoice plus its non-blocking planning warnings (never stored)."""

    planning_warnings: list[dict] | None = None
    sanity_warnings: list[dict] | None = None


def _doc(services, coll: str, doc: dict, finalize: bool = False) -> dict:
    return {
        **doc,
        "planning_warnings": services.planning.warnings_for(coll, doc),
        "sanity_warnings": services.sanity.check(coll, doc, None, finalize=finalize),
    }


class FromTime(v.CheckedBody):
    customer_id: v.OptId = None
    project_id: v.OptId = None
    date_from: v.OptDate = Field(None, alias="from")
    date_to: v.OptDate = Field(None, alias="to")


def _pdf(services, coll: str, rid: str) -> Response:
    data, name = services.sales.pdf(coll, rid)
    safe = re.sub(r"[^\w.-]", "_", name)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{safe}"'})


@router.post("/invoices/from-time", response_model=DocumentResult, response_model_exclude_none=True)
def invoice_from_time(body: FromTime, _: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    """Group unbilled, billable time by project/task/rate into a draft invoice."""
    inv = services.sales.invoice_from_time(body.customer_id, body.project_id, body.date_from, body.date_to)
    return _doc(services, "invoices", inv)


@router.post("/invoices/{rid}/finalize", response_model=DocumentResult, response_model_exclude_none=True)
def finalize_invoice(rid: str, _: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    return _doc(services, "invoices", services.sales.finalize("invoices", rid), finalize=True)


@router.get("/invoices/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def invoice_pdf(rid: str, _: Annotated[dict, Needs("invoices")], services: ServicesDep):
    return _pdf(services, "invoices", rid)


@router.post("/offers/{rid}/finalize", response_model=DocumentResult, response_model_exclude_none=True)
def finalize_offer(rid: str, _: Annotated[dict, Needs("offers", "write")], services: ServicesDep):
    return _doc(services, "offers", services.sales.finalize("offers", rid), finalize=True)


@router.get("/offers/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def offer_pdf(rid: str, _: Annotated[dict, Needs("offers")], services: ServicesDep):
    return _pdf(services, "offers", rid)


@router.post("/offers/{rid}/accept", summary="mark accepted; creates or extends a project and, if planned, a contract")
def accept_offer(rid: str, _: Annotated[dict, Needs("offers", "write")], services: ServicesDep) -> dict[str, Any]:
    out = services.sales.accept_offer(rid)
    out["planning_warnings"] = services.planning.warnings_for("projects", out["project"])
    if out.get("contract"):
        out["planning_warnings"] += services.planning.warnings_for("contracts", out["contract"])
    return out


@router.post("/offers/{rid}/to-invoice", response_model=DocumentResult, response_model_exclude_none=True)
def offer_to_invoice(rid: str, _: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    return _doc(services, "invoices", services.sales.offer_to_invoice(rid))
