"""VAT return (UStVA) and ZM routes (docs/specs/ustva.md and zm.md "API"): partly implemented (UStVA records); the rest answers 501.

Area `vat`: boss and accountant read and write, employee nothing. Handlers answer 501 until the service work packages
land. Every route that changes something is ``SessionOnly``: no write to `vat` through API tokens in v1.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from permita38.api.deps import Needs, ServicesDep, SessionOnly
from permita38.core import models
from permita38.core.errors import ServiceError
from permita38.core.service.special_prepayment import to_cents
from permita38.core.service.vat_returns import VatReturnListItem
from permita38.core.service.zm_reports import ZmListItem
from permita38.core.validation import VatPeriodKind
from permita38.core.vat import ustva_export, zm_export

returns = APIRouter(prefix="/vat-returns", tags=["vat"])
special = APIRouter(prefix="/vat", tags=["vat"])
zm = APIRouter(prefix="/zm", tags=["vat"])
routers = [returns, special, zm]

Read = Annotated[dict, Needs("vat", "read")]
Write = Annotated[dict, Needs("vat", "write")]
Year = Annotated[int, Query(ge=2000, le=2100)]


# ------------------------------------------------------------------ UStVA
@returns.get("", name="list_vat_returns", response_model=list[VatReturnListItem])
def list_returns(year: Year, user: Read, services: ServicesDep):
    return services.vat_returns.list(year)


@returns.get("/preview", name="preview_vat_return", response_model=models.VatReturn)
def preview_return(
    year: Year, kind: VatPeriodKind, no: Annotated[int, Query(ge=1, le=12)], user: Read, services: ServicesDep
):
    return models.VatReturn(**services.vat_returns.preview(year, kind, no))


@returns.post(
    "", status_code=201, dependencies=[SessionOnly], name="create_vat_return", response_model=models.VatReturn
)
def create_return(body: models.VatReturnCreateIn, user: Write, services: ServicesDep):
    return models.VatReturn(**services.vat_returns.create_draft(body.year, body.kind, body.no, body.amends_id))


@returns.get("/{rid}", name="get_vat_return", response_model=models.VatReturn)
def get_return(rid: str, user: Read, services: ServicesDep):
    return models.VatReturn(**services.vat_returns.get(rid))


@returns.post(
    "/{rid}/finalize", dependencies=[SessionOnly], name="finalize_vat_return", response_model=models.VatReturn
)
def finalize_return(rid: str, user: Write, services: ServicesDep, confirm_gap: Annotated[bool, Query()] = False):
    return models.VatReturn(**services.vat_returns.finalize(rid, user, confirm_gap))


@returns.post("/{rid}/reopen", dependencies=[SessionOnly], name="reopen_vat_return", response_model=models.VatReturn)
def reopen_return(rid: str, user: Write, services: ServicesDep):
    return models.VatReturn(**services.vat_returns.reopen(rid, user))


@returns.post("/{rid}/filed", dependencies=[SessionOnly], name="mark_vat_return_filed", response_model=models.VatReturn)
def filed_return(rid: str, body: models.VatReturnFiledIn, user: Write, services: ServicesDep):
    return models.VatReturn(**services.vat_returns.filed(rid, body.filed_on, body.reference, body.note))


@returns.delete("/{rid}", status_code=204, dependencies=[SessionOnly], name="delete_vat_return")
def delete_return(rid: str, user: Write, services: ServicesDep):
    services.vat_returns.delete(rid)
    return Response(status_code=204)


@returns.get("/{rid}/boxes/{kz}", name="get_vat_return_box")
def box_lines(rid: str, kz: str, user: Read, services: ServicesDep):
    return services.vat_returns.lines(rid, kz)


@returns.post(
    "/{rid}/payment",
    status_code=201,
    dependencies=[SessionOnly],
    name="pay_vat_return",
    response_model=models.VatReturn,
)
def pay_return(rid: str, body: models.VatReturnPaymentIn, user: Write, services: ServicesDep):
    cents = to_cents(body.amount)
    return models.VatReturn(**services.vat_returns.pay(rid, date.fromisoformat(body.date), cents, body.kind, user))


def _export_view(rid: str, services: ServicesDep) -> tuple[dict, dict[str, list[dict]]]:
    """The return as the API shows it and its source lines per box."""
    ret = models.VatReturn(**services.vat_returns.get(rid)).model_dump(mode="json")
    lines = {kz: services.vat_returns.lines(rid, kz)["lines"] for kz in ret["boxes"]}
    return ret, lines


def _export_name(ret: dict, ext: str) -> str:
    return f"ustva-{ret['year']}-{ret.get('period_kind') or 'period'}{int(ret.get('period_no') or 0):02d}.{ext}"


@returns.get(
    "/{rid}/export.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    name="export_vat_return_pdf",
)
def export_return_pdf(rid: str, user: Read, services: ServicesDep):
    ret, lines = _export_view(rid, services)
    cfg = services.settings.get()
    data = ustva_export.return_pdf(
        ret,
        lines,
        business=str(cfg.get("company_name") or ""),
        tax_number=str(cfg.get("tax_number") or ""),
        unconfirmed=ustva_export.rules_unconfirmed(int(ret["year"])),
    )
    disp = f'inline; filename="{_export_name(ret, "pdf")}"'
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": disp})


@returns.get(
    "/{rid}/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
    name="export_vat_return_csv",
)
def export_return_csv(
    rid: str, user: Read, services: ServicesDep, lines: Annotated[bool, Query(description="the source lines")] = False
):
    ret, by_box = _export_view(rid, services)
    data = ustva_export.return_csv(ret, by_box if lines else None)
    name = "export-lines.csv" if lines else _export_name(ret, "csv")
    return Response(
        data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@returns.get("/{rid}/export.json", name="export_vat_return_json", response_model=models.VatReturn)
def export_return_json(rid: str, user: Read, services: ServicesDep):
    return models.VatReturn(**ustva_export.return_json(services.vat_returns.get(rid)))


# ------------------------------------------------------------------ special prepayment (Dauerfristverlaengerung)
@special.get("/special-prepayment", name="get_special_prepayment", response_model=models.SpecialPrepaymentOut)
def get_special_prepayment(year: Year, user: Read, services: ServicesDep):
    return models.SpecialPrepaymentOut(**services.special_prepayment.get(year))


@special.put(
    "/special-prepayment",
    dependencies=[SessionOnly],
    name="set_special_prepayment",
    response_model=models.SpecialPrepaymentOut,
)
def put_special_prepayment(body: models.SpecialPrepaymentIn, user: Write, services: ServicesDep):
    return models.SpecialPrepaymentOut(**services.special_prepayment.put(body.year, body.amount, body.paid_on, user))


# ------------------------------------------------------------------ ZM
@zm.get("", name="list_zm", response_model=list[ZmListItem])
def list_zm(year: Year, user: Read, services: ServicesDep):
    return services.zm_reports.list(year)


@zm.get("/preview", name="preview_zm", response_model=models.ZmReport)
def preview_zm(year: Year, quarter: Annotated[int, Query(ge=1, le=4)], user: Read, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.preview(year, quarter))


@zm.post("", status_code=201, dependencies=[SessionOnly], name="create_zm", response_model=models.ZmReport)
def create_zm(body: models.ZmCreateIn, user: Write, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.create_draft(body.year, body.quarter, body.amends_id))


@zm.get("/{rid}", name="get_zm", response_model=models.ZmReport)
def get_zm(rid: str, user: Read, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.get(rid))


@zm.post("/{rid}/finalize", dependencies=[SessionOnly], name="finalize_zm", response_model=models.ZmReport)
def finalize_zm(rid: str, user: Write, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.finalize(rid, user))


@zm.post("/{rid}/reopen", dependencies=[SessionOnly], name="reopen_zm", response_model=models.ZmReport)
def reopen_zm(rid: str, user: Write, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.reopen(rid))


@zm.post("/{rid}/filed", dependencies=[SessionOnly], name="mark_zm_filed", response_model=models.ZmReport)
def filed_zm(rid: str, body: models.ZmFiledIn, user: Write, services: ServicesDep):
    return models.ZmReport(**services.zm_reports.filed(rid, body.filed_on, body.reference, body.note))


@zm.delete("/{rid}", status_code=204, dependencies=[SessionOnly], name="delete_zm")
def delete_zm(rid: str, user: Write, services: ServicesDep):
    services.zm_reports.delete(rid)
    return Response(status_code=204)


def _zm_name(rep: dict, ext: str) -> str:
    return f"zm-{rep['year']}-q{rep['quarter']}.{ext}"


@zm.get(
    "/{rid}/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, 422: {"description": "The list cannot be exported"}},
    name="export_zm_csv",
)
def export_zm_csv(
    rid: str,
    user: Read,
    services: ServicesDep,
    readable: Annotated[bool, Query(description="a readable CSV instead of the BZSt import file")] = False,
):
    rep, rows = services.zm_reports.export_lines(rid)
    correction = bool(rep.get("amends_id"))
    try:
        if readable:
            data = zm_export.readable_csv(rows, correction=correction)
        else:
            data = zm_export.bzst_csv(rows, correction=correction)
    except zm_export.ZmExportError as e:
        raise ServiceError(
            422, str(e), body={"rows": [{"vat_id": r.vat_id, "reason": r.reason} for r in e.rows]}
        ) from None
    media = "text/csv; charset=utf-8" if readable else "text/csv; charset=iso-8859-15"
    name = _zm_name(rep, "csv") if not readable else _zm_name(rep, "readable.csv")
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@zm.get(
    "/{rid}/export.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    name="export_zm_pdf",
)
def export_zm_pdf(rid: str, user: Read, services: ServicesDep):
    rep, _rows = services.zm_reports.export_lines(rid)
    cfg = services.settings.get()
    data = zm_export.zm_pdf(
        rep, business=str(cfg.get("company_name") or ""), unconfirmed=ustva_export.rules_unconfirmed(int(rep["year"]))
    )
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_zm_name(rep, "pdf")}"'},
    )
