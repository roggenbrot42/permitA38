import re
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import Field

from permita38.api import money_out
from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import present, stored
from permita38.api.routers.records import _result, _with_warnings
from permita38.core import validation as v
from permita38.core.models import Contract, Document, InvoiceIn, Project

router = APIRouter(tags=["sales"])
routers = [router]


class DocumentResult(Document):
    """A saved offer or invoice plus its non-blocking planning warnings (never stored)."""

    planning_warnings: list[dict] | None = None
    sanity_warnings: list[dict] | None = None


def _doc(services, coll: str, doc: dict, finalize: bool = False) -> dict:
    return money_out.convert_warnings(
        {
            **doc,
            "planning_warnings": services.planner.warnings_for(coll, doc),
            "sanity_warnings": services.sanity.check(coll, doc, None, finalize=finalize),
        }
    )


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
    """Group unbilled, billable time of one customer by project/task/rate into a draft invoice.

    The customer is ``customer_id`` or, when only ``project_id`` is given, that project's customer; one of them is
    required. Personal projects are never billed.
    """
    inv = services.sales.invoice_from_time(body.customer_id, body.project_id, body.date_from, body.date_to)
    return _doc(services, "invoices", inv)


@router.post("/invoices/{rid}/finalize", response_model=DocumentResult, response_model_exclude_none=True)
def finalize_invoice(rid: str, user: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    return _doc(services, "invoices", services.sales.finalize("invoices", rid, user), finalize=True)


@router.get("/invoices/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def invoice_pdf(rid: str, _: Annotated[dict, Needs("invoices")], services: ServicesDep):
    return _pdf(services, "invoices", rid)


@router.post("/offers/{rid}/finalize", response_model=DocumentResult, response_model_exclude_none=True)
def finalize_offer(rid: str, user: Annotated[dict, Needs("offers", "write")], services: ServicesDep):
    return _doc(services, "offers", services.sales.finalize("offers", rid, user), finalize=True)


@router.get("/offers/{rid}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def offer_pdf(rid: str, _: Annotated[dict, Needs("offers")], services: ServicesDep):
    return _pdf(services, "offers", rid)


@router.post("/offers/{rid}/accept", summary="mark accepted; creates or extends a project and, if planned, a contract")
def accept_offer(rid: str, _: Annotated[dict, Needs("offers", "write")], services: ServicesDep) -> dict[str, Any]:
    out = services.sales.accept_offer(rid)
    out["planning_warnings"] = services.planner.warnings_for("projects", out["project"])
    if out.get("contract"):
        out["planning_warnings"] += services.planner.warnings_for("contracts", out["contract"])
    out["offer"] = present(Document, out["offer"])
    out["project"] = present(Project, out["project"])
    if out.get("contract"):
        out["contract"] = present(Contract, out["contract"])
    return money_out.convert_warnings(out)


@router.post("/offers/{rid}/to-invoice", response_model=DocumentResult, response_model_exclude_none=True)
def offer_to_invoice(rid: str, _: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    return _doc(services, "invoices", services.sales.offer_to_invoice(rid))


# Invoices: GET one/list/PUT/DELETE of `/invoices` shadow the generic CRUD routes of `records.py` (this module is
# included first), so an issued invoice carries its derived payment fields, is frozen and cannot be deleted
# (docs/specs/credit-notes-payments.md 2). They are hidden from the schema: the contract of those paths stays the one
# of the generic routes (same models).
_invoice_result = _result(Document)


@router.get("/invoices", include_in_schema=False, name="list_invoices_derived")
def list_invoices(user: Annotated[dict, Needs("invoices")], services: ServicesDep):
    return stored(Document, services.sales.list_invoices(user))


@router.get("/invoices/{rid}", include_in_schema=False, name="get_invoice_derived")
def get_invoice(rid: str, user: Annotated[dict, Needs("invoices")], services: ServicesDep):
    return stored(Document, services.sales.get_invoice(user, rid))


@router.put("/invoices/{rid}", include_in_schema=False, name="update_invoice_frozen")
def update_invoice(rid: str, body: InvoiceIn, user: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    old = services.sanity.previous("invoices", rid)
    nulls = [k for k in body.model_fields_set if getattr(body, k, None) is None]
    rec = services.sales.update_invoice(
        user,
        rid,
        body.model_dump(exclude_none=True),
        body.model_dump(exclude_none=True, exclude_unset=True),
        nulls,
    )
    return stored(_invoice_result, money_out.convert_warnings(_with_warnings(services, "invoices", rec, user, old)))


@router.delete("/invoices/{rid}", status_code=204, include_in_schema=False, name="delete_invoice_frozen")
def delete_invoice(rid: str, user: Annotated[dict, Needs("invoices", "write")], services: ServicesDep):
    services.sales.delete_invoice(user, rid)
    return Response(status_code=204)
