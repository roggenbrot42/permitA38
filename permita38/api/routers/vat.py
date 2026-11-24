"""VAT return (UStVA) and ZM routes (docs/specs/ustva.md and zm.md "API"): skeletons.

Area `vat`: boss and accountant read and write, employee nothing. Handlers answer 501 until the service work packages
land. Every route that changes something is ``SessionOnly``: no write to `vat` through API tokens in v1.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from permita38.api.deps import Needs, ServicesDep, SessionOnly
from permita38.api.routers.accounting import not_implemented
from permita38.core import models
from permita38.core.validation import VatPeriodKind

returns = APIRouter(prefix="/vat-returns", tags=["vat"])
special = APIRouter(prefix="/vat", tags=["vat"])
zm = APIRouter(prefix="/zm", tags=["vat"])
routers = [returns, special, zm]

Read = Annotated[dict, Needs("vat", "read")]
Write = Annotated[dict, Needs("vat", "write")]
Year = Annotated[int, Query(ge=2000, le=2100)]


# ------------------------------------------------------------------ UStVA
@returns.get("", name="list_vat_returns")
def list_returns(year: Year, user: Read, services: ServicesDep):
    not_implemented()


@returns.get("/preview", name="preview_vat_return")
def preview_return(
    year: Year, kind: VatPeriodKind, no: Annotated[int, Query(ge=1, le=12)], user: Read, services: ServicesDep
):
    not_implemented()


@returns.post("", status_code=201, dependencies=[SessionOnly], name="create_vat_return")
def create_return(body: models.VatReturnCreateIn, user: Write, services: ServicesDep):
    not_implemented()


@returns.get("/{rid}", name="get_vat_return")
def get_return(rid: str, user: Read, services: ServicesDep):
    not_implemented()


@returns.post("/{rid}/finalize", dependencies=[SessionOnly], name="finalize_vat_return")
def finalize_return(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@returns.post("/{rid}/reopen", dependencies=[SessionOnly], name="reopen_vat_return")
def reopen_return(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@returns.post("/{rid}/filed", dependencies=[SessionOnly], name="mark_vat_return_filed")
def filed_return(rid: str, body: models.VatReturnFiledIn, user: Write, services: ServicesDep):
    not_implemented()


@returns.delete("/{rid}", dependencies=[SessionOnly], name="delete_vat_return")
def delete_return(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@returns.get("/{rid}/boxes/{kz}", name="get_vat_return_box")
def box_lines(rid: str, kz: str, user: Read, services: ServicesDep):
    not_implemented()


@returns.post("/{rid}/payment", status_code=201, dependencies=[SessionOnly], name="pay_vat_return")
def pay_return(rid: str, body: models.VatReturnPaymentIn, user: Write, services: ServicesDep):
    not_implemented()


@returns.get("/{rid}/export.pdf", name="export_vat_return_pdf")
def export_return_pdf(rid: str, user: Read, services: ServicesDep):
    not_implemented()


@returns.get("/{rid}/export.csv", name="export_vat_return_csv")
def export_return_csv(rid: str, user: Read, services: ServicesDep):
    not_implemented()


@returns.get("/{rid}/export.json", name="export_vat_return_json")
def export_return_json(rid: str, user: Read, services: ServicesDep):
    not_implemented()


# ------------------------------------------------------------------ special prepayment (Dauerfristverlaengerung)
@special.get("/special-prepayment", name="get_special_prepayment")
def get_special_prepayment(year: Year, user: Read, services: ServicesDep):
    not_implemented()


@special.put("/special-prepayment", dependencies=[SessionOnly], name="set_special_prepayment")
def put_special_prepayment(body: models.SpecialPrepaymentIn, user: Write, services: ServicesDep):
    not_implemented()


# ------------------------------------------------------------------ ZM
@zm.get("", name="list_zm")
def list_zm(year: Year, user: Read, services: ServicesDep):
    not_implemented()


@zm.get("/preview", name="preview_zm")
def preview_zm(year: Year, quarter: Annotated[int, Query(ge=1, le=4)], user: Read, services: ServicesDep):
    not_implemented()


@zm.post("", status_code=201, dependencies=[SessionOnly], name="create_zm")
def create_zm(body: models.ZmCreateIn, user: Write, services: ServicesDep):
    not_implemented()


@zm.get("/{rid}", name="get_zm")
def get_zm(rid: str, user: Read, services: ServicesDep):
    not_implemented()


@zm.post("/{rid}/finalize", dependencies=[SessionOnly], name="finalize_zm")
def finalize_zm(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@zm.post("/{rid}/reopen", dependencies=[SessionOnly], name="reopen_zm")
def reopen_zm(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@zm.post("/{rid}/filed", dependencies=[SessionOnly], name="mark_zm_filed")
def filed_zm(rid: str, body: models.ZmFiledIn, user: Write, services: ServicesDep):
    not_implemented()


@zm.delete("/{rid}", dependencies=[SessionOnly], name="delete_zm")
def delete_zm(rid: str, user: Write, services: ServicesDep):
    not_implemented()


@zm.get("/{rid}/export.csv", name="export_zm_csv")
def export_zm_csv(rid: str, user: Read, services: ServicesDep):
    not_implemented()


@zm.get("/{rid}/export.pdf", name="export_zm_pdf")
def export_zm_pdf(rid: str, user: Read, services: ServicesDep):
    not_implemented()
