"""Accounting routes (docs/specs/accounting.md 17-18, credit-notes-payments.md 3-4): skeletons.

Every route exists with its rights; the handlers answer 501 until the service work packages land. Routers stay thin.
API tokens: reads follow the owner's rights; every route that changes something is ``SessionOnly`` (no write to
`accounting` or `vat` through tokens in v1).
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, TypeAdapter, ValidationError

from permita38.api.deps import CurrentUser, Needs, ServicesDep, SessionOnly
from permita38.api.responses import present
from permita38.core import models, rights
from permita38.core import validation as v
from permita38.core.money import dec, q2
from permita38.core.service.accounting_reports import (
    AccountOut,
    BalanceSheetOut,
    EurOut,
    LedgerOut,
    OpenItemsOut,
    PnlOut,
    TrialBalanceOut,
    parse_date,
)
from permita38.core.service.accounting_status import AccountingStatusOut
from permita38.core.service.integrity import ACCOUNTING_RULES, IntegrityService, summary
from permita38.core.service.manual_entries import EntryTemplate
from permita38.core.service.year_closing import ProvisionInput

router = APIRouter(prefix="/accounting", tags=["accounting"])
payments_router = APIRouter(tags=["accounting"])
routers = [router, payments_router]

Year = Annotated[int | None, Query(ge=2000, le=2100)]
NOT_IMPLEMENTED = "not implemented yet"
_PROVISION_AMOUNT: TypeAdapter[Decimal] = TypeAdapter(v.PositiveMoneyIn)


def not_implemented() -> NoReturn:
    raise HTTPException(501, NOT_IMPLEMENTED)


def _boss_only(user: CurrentUser) -> dict:
    if rights.normalize_role(user.get("role")) != rights.BOSS:
        raise HTTPException(403, "Forbidden")
    return user


# `accounting` write for everyone who passes (the matrix says boss and accountant), and the role must be the boss.
_boss_only.rbac = ("accounting", "write", True)  # type: ignore[attr-defined]
_boss_only.boss_only = True  # type: ignore[attr-defined]
BossOnly = Depends(_boss_only)


def _invoices_or_accounting(user: CurrentUser) -> dict:
    if not any(rights.allowed_for(user, a, "write") for a in rights.PAYMENT_WRITE_AREAS):
        raise HTTPException(403, "Forbidden")
    return user


# Either-or rule: write on `invoices` OR `accounting`. With the current matrix the set of roles that pass equals the set
# that has `accounting` write, so `.rbac` (what the route table test compares) names that area; `.rbac_any` is the rule.
_invoices_or_accounting.rbac = ("accounting", "write", False)  # type: ignore[attr-defined]
_invoices_or_accounting.rbac_any = tuple((a, "write") for a in rights.PAYMENT_WRITE_AREAS)  # type: ignore[attr-defined]
NeedsInvoicesOrAccountingWrite = Depends(_invoices_or_accounting)

Read = Annotated[dict, Needs("accounting", "read")]
Write = Annotated[dict, Needs("accounting", "write")]
Boss = Annotated[dict, BossOnly]
PaymentWrite = Annotated[dict, NeedsInvoicesOrAccountingWrite]
InvoiceRead = Annotated[dict, Needs("invoices", "read")]


# ------------------------------------------------------------------ previews (read only)
def _euros(cents: int) -> Decimal:
    return Decimal(int(cents)).scaleb(-2)


class LockMonthOut(BaseModel):
    year: int
    month: int


class PlanLineOut(BaseModel):
    account: str
    amount: v.Money  # debit positive, credit negative


class PlannedEntryOut(BaseModel):
    event_key: str
    event: str
    text: str
    lines: list[PlanLineOut]


class ProvisionProposalOut(BaseModel):
    kind: str
    prepayments_expensed: v.Money
    booked: v.Money


class YearClosePreviewOut(BaseModel):
    year: int
    income_basis: str
    blockers: list[str]
    provisions: list[ProvisionProposalOut]
    plan: list[PlannedEntryOut]
    next_year_opening: list[PlanLineOut]


def _plan_lines(lines) -> list[PlanLineOut]:
    return [PlanLineOut(account=ln.account, amount=_euros(ln.amount_cents)) for ln in lines]


def _accounting_or_vat_read(user: CurrentUser) -> dict:
    if not any(rights.allowed_for(user, a, "read") for a in ("accounting", "vat")):
        raise HTTPException(403, "Forbidden")
    return user


# Either-or rule: read on `accounting` OR `vat` (`.rbac` names `accounting`, the same role set with the current matrix).
_accounting_or_vat_read.rbac = ("accounting", "read", False)  # type: ignore[attr-defined]
_accounting_or_vat_read.rbac_any = (("accounting", "read"), ("vat", "read"))  # type: ignore[attr-defined]
StatusRead = Annotated[dict, Depends(_accounting_or_vat_read)]


# ------------------------------------------------------------------ status, fiscal years, initialisation
@router.get("/status", name="accounting_status", response_model=AccountingStatusOut)
def status(user: StatusRead, services: ServicesDep):
    """Initialised or not, the years with their bases and whether the rules in use are not yet adviser-confirmed."""
    return services.accounting_status.status()


@router.get("/years", name="list_fiscal_years", response_model=list[models.FiscalYear])
def list_years(user: Read, services: ServicesDep):
    return [models.FiscalYear(**r) for r in services.fiscal_years.list()]


@router.post(
    "/years", status_code=201, dependencies=[SessionOnly], name="create_fiscal_year", response_model=models.FiscalYear
)
def create_year(body: models.FiscalYearIn, user: Write, services: ServicesDep):
    return models.FiscalYear(**services.fiscal_years.create(user, body))


@router.get("/years/{year}", name="get_fiscal_year", response_model=models.FiscalYear)
def get_year(year: int, user: Read, services: ServicesDep):
    return models.FiscalYear(**services.fiscal_years.get(year))


@router.get("/years/{year}/close-preview", name="preview_year_close", response_model=YearClosePreviewOut)
def close_preview(
    year: int,
    user: Read,
    services: ServicesDep,
    trade_tax: Annotated[str | None, Query(max_length=20)] = None,
    corporate_tax: Annotated[str | None, Query(max_length=20)] = None,
):
    """What closing the year would do: the blockers, the provision proposals (prepayments already expensed) and the
    planned entries. Optional ``trade_tax`` / ``corporate_tax`` (decimal strings) are provisions the user is about to
    confirm; the plan includes them. Writes nothing."""
    provisions: list[ProvisionInput] = []
    typed: tuple[tuple[Literal["trade_tax", "corporate_tax"], str | None], ...] = (
        ("trade_tax", trade_tax),
        ("corporate_tax", corporate_tax),
    )
    for kind, raw in typed:
        if raw is not None and raw != "":
            try:
                amount = _PROVISION_AMOUNT.validate_python(raw)
            except ValidationError:
                raise HTTPException(422, f"{kind} must be a positive amount with at most two decimals") from None
            provisions.append(ProvisionInput(kind, int(q2(dec(amount)) * 100)))
    p = services.year_closing.preview(year, provisions)
    return YearClosePreviewOut(
        year=p.year,
        income_basis=p.income_basis,
        blockers=list(p.blockers),
        provisions=[
            ProvisionProposalOut(
                kind=x.kind, prepayments_expensed=_euros(x.prepayments_expensed_cents), booked=_euros(x.booked_cents)
            )
            for x in p.provisions
        ],
        plan=[
            PlannedEntryOut(event_key=e.event_key, event=e.event, text=e.text, lines=_plan_lines(e.lines))
            for e in p.plan
        ],
        next_year_opening=_plan_lines(p.next_year_opening),
    )


@router.post(
    "/years/{year}/close", dependencies=[SessionOnly], name="close_fiscal_year", response_model=models.FiscalYear
)
def close_year(year: int, user: Boss, services: ServicesDep, body: models.YearCloseIn | None = None):
    """Close the year (boss only): accrual posts the confirmed provisions, the VAT transfer and the closing entries,
    EÜR just ends the year. 409 when blocked or closed already."""
    provisions = [ProvisionInput(p.kind, int(q2(dec(p.amount)) * 100)) for p in (body.provisions if body else [])]
    return models.FiscalYear(**services.year_closing.close(year, provisions, user))


@router.post("/init", dependencies=[SessionOnly], name="init_accounting")
def init_accounting(body: models.AccountingInitIn, user: Boss, services: ServicesDep):
    """Initialise the accounting (boss only): fiscal year, opening entry, snapshots, backfill of every event on or
    after the start, in one transaction. ``review_resolutions`` answers the review list, ``external_returns`` carries
    the returns filed outside the app. 409 when already initialised or when review rows are unanswered (listed)."""
    return services.accounting_init.run(body, user).as_dict()


@router.get("/init/review", name="accounting_init_review")
def init_review(
    user: Read,
    services: ServicesDep,
    chart: Annotated[str, Query(pattern="^(skr03|skr04)$")] = "skr03",
    income_basis: Annotated[str, Query(pattern="^(eur|accrual)$")] = "eur",
    accounting_start: Annotated[str | None, Query(max_length=10)] = None,
    vat_basis: Annotated[str, Query(pattern="^(soll|ist)$")] = "soll",
    frequency: Annotated[str, Query(pattern="^(month|quarter|none)$")] = "month",
    small_business: bool = False,
):
    """The records that need a decision before the accounting can start, each with its allowed resolutions, and the
    ended VAT periods for which a return filed outside the app may be entered (read only)."""
    params = models.AccountingInitIn(
        chart=chart,  # type: ignore[arg-type]
        income_basis=income_basis,  # type: ignore[arg-type]
        vat_basis=vat_basis,  # type: ignore[arg-type]
        frequency=frequency,  # type: ignore[arg-type]
        small_business=small_business,
        accounting_start=accounting_start,  # type: ignore[arg-type]
    )
    return {
        "review": [r.as_dict() for r in services.accounting_init.check(params)],
        "external_returns": [p.as_dict() for p in services.accounting_init.external_periods(params)],
    }


# ------------------------------------------------------------------ journal, accounts
@router.get("/journal", name="list_journal", response_model=list[models.JournalEntry])
def journal(
    user: Read,
    services: ServicesDep,
    date_from: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    date_to: Annotated[str | None, Query(alias="to", max_length=10)] = None,
    account: Annotated[str | None, Query(max_length=10)] = None,
    tax_key: Annotated[str | None, Query(max_length=20)] = None,
    source: Annotated[str | None, Query(max_length=40)] = None,
    offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    return services.accounting_reports.journal(
        parse_date(date_from, "from"), parse_date(date_to, "to"), account, tax_key, source, offset, limit
    )


@router.post(
    "/entries",
    status_code=201,
    dependencies=[SessionOnly],
    name="create_manual_entry",
    response_model=models.JournalEntry,
)
def create_entry(body: models.ManualEntryIn, user: Write, services: ServicesDep):
    return services.manual_entries.post(body, user)


@router.get("/entry-templates", name="list_entry_templates", response_model=list[EntryTemplate])
def entry_templates(user: Read, services: ServicesDep):
    return services.manual_entries.templates()


@router.post(
    "/entries/{entry_id}/reverse",
    status_code=201,
    dependencies=[SessionOnly],
    name="reverse_entry",
    response_model=models.JournalEntry,
)
def reverse_entry(entry_id: str, body: models.EntryReverseIn, user: Write, services: ServicesDep):
    return services.manual_entries.reverse(entry_id, body.date, body.text, user)


@router.get("/accounts", name="list_accounts", response_model=list[AccountOut])
def accounts(user: Read, services: ServicesDep, year: Year = None):
    return services.accounting_reports.accounts(year)


# ------------------------------------------------------------------ reports (read only)
DateFrom = Annotated[str | None, Query(alias="from", max_length=10)]
DateTo = Annotated[str | None, Query(alias="to", max_length=10)]


@router.get("/reports/trial-balance", name="report_trial_balance", response_model=TrialBalanceOut)
def report_trial_balance(
    user: Read, services: ServicesDep, year: Year = None, date_from: DateFrom = None, date_to: DateTo = None
):
    return services.accounting_reports.trial_balance(year, parse_date(date_from, "from"), parse_date(date_to, "to"))


@router.get("/reports/eur", name="report_eur", response_model=EurOut)
def report_eur(user: Read, services: ServicesDep, year: Year = None):
    return services.accounting_reports.eur(year)


@router.get("/reports/pnl", name="report_pnl", response_model=PnlOut)
def report_pnl(user: Read, services: ServicesDep, year: Year = None):
    return services.accounting_reports.pnl(year)


@router.get("/reports/balance-sheet", name="report_balance_sheet", response_model=BalanceSheetOut)
def report_balance_sheet(user: Read, services: ServicesDep, year: Year = None, date_to: DateTo = None):
    """Cumulated up to ``to`` (default: 31 December of the year)."""
    return services.accounting_reports.balance_sheet(year, parse_date(date_to, "to"))


@router.get("/reports/open-items", name="report_open_items", response_model=OpenItemsOut)
def report_open_items(user: Read, services: ServicesDep, year: Year = None, date_to: DateTo = None):
    """Open receivables and payables up to ``to`` (default: 31 December of the year)."""
    return services.accounting_reports.open_items(year, parse_date(date_to, "to"))


@router.get("/reports/ledger", name="report_ledger", response_model=LedgerOut)
def report_ledger(
    user: Read,
    services: ServicesDep,
    year: Year = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    account: Annotated[str | None, Query(max_length=10)] = None,
    role: Annotated[str | None, Query(max_length=60)] = None,
):
    """The postings of one account (``account`` or ``role`` is required) with a running balance."""
    return services.accounting_reports.ledger(
        year, account, role, parse_date(date_from, "from"), parse_date(date_to, "to")
    )


# ------------------------------------------------------------------ locks, DATEV, integrity
@router.get("/locks", name="list_period_locks", response_model=list[models.PeriodLock])
def locks(user: Read, services: ServicesDep):
    return [models.PeriodLock(**r) for r in services.locks.all()]


@router.get("/locks/preview", name="preview_period_lock", response_model=list[LockMonthOut])
def lock_preview(
    user: Read,
    services: ServicesDep,
    year: Annotated[int, Query(ge=1900, le=2999)],
    month: Annotated[int, Query(ge=1, le=12)],
):
    """The months a lock up to the given one would lock now, in order (empty when all are locked)."""
    return [LockMonthOut(year=y, month=m) for y, m in services.locks.preview_lock_up_to(year, month)]


@router.post(
    "/locks", status_code=201, dependencies=[SessionOnly], name="lock_period", response_model=list[models.PeriodLock]
)
def lock_period(body: models.PeriodLockIn, user: Write, services: ServicesDep):
    """Lock every month up to the given one (in order); answers the newly locked months."""
    return [models.PeriodLock(**r) for r in services.locks.lock_up_to(body.year, body.month, "manual", str(user["id"]))]


@router.get(
    "/datev", name="datev_export", response_class=Response, responses={200: {"content": {"application/zip": {}}}}
)
def datev(
    user: Read,
    services: ServicesDep,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
):
    """DATEV EXTF export of the bookings in the range (one fiscal year) as a zip with the report."""
    data = services.datev_export.export(date_from, date_to, user)
    name = f"datev-export_{date_from.isoformat()}_{date_to.isoformat()}.zip"
    return Response(
        data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


class IntegrityFinding(BaseModel):
    code: str
    severity: str
    collection: str | None = None
    id: str | None = None
    field: str | None = None
    message: str


class IntegritySummary(BaseModel):
    errors: int
    warnings: int
    info: int


class AccountingIntegrity(BaseModel):
    findings: list[IntegrityFinding]
    summary: IntegritySummary


@router.get("/integrity", name="accounting_integrity", response_model=AccountingIntegrity)
def integrity(user: Read, services: ServicesDep):
    """Balance and hash chain per fiscal year plus the accounting sanity warnings (spec 7.1, 7.6, 7.9)."""
    found = IntegrityService(services.store).run(rules=ACCOUNTING_RULES)
    return {"findings": [f.as_dict() for f in found], "summary": summary(found)}


# ------------------------------------------------------------------ payments and credit notes on invoices
# Write: `invoices` or `accounting` (accountants have the latter), session only for API tokens in v1.
def _payment_result(services, result: dict) -> dict:
    inv = services.store.get("invoices", result.pop("invoice_id"))
    return {**result, "invoice": present(models.Document, services.sales.invoice_view(inv))}


@payments_router.get("/invoices/{rid}/payments", name="list_payments")
def list_payments(rid: str, user: InvoiceRead, services: ServicesDep) -> list[dict[str, Any]]:
    return services.sales.payments.list(rid)


@payments_router.post("/invoices/{rid}/payments", status_code=201, dependencies=[SessionOnly], name="create_payment")
def create_payment(rid: str, body: models.PaymentIn, user: PaymentWrite, services: ServicesDep) -> dict[str, Any]:
    """Record a payment or refund; answers the payment, the updated invoice and warnings (overpayment, VAT drift)."""
    result = services.sales.payments.record(
        rid, date=body.date, amount=body.amount, kind=body.kind, method=body.method, note=body.note, user=user
    )
    return _payment_result(services, result)


@payments_router.post(
    "/payments/{payment_id}/reverse", status_code=201, dependencies=[SessionOnly], name="reverse_payment"
)
def reverse_payment(
    payment_id: str, body: models.PaymentReverseIn, user: PaymentWrite, services: ServicesDep
) -> dict[str, Any]:
    result = services.sales.payments.reverse(payment_id, date=body.date, note=body.note, user=user)
    return _payment_result(services, result)


@payments_router.post(
    "/invoices/{rid}/payments/legacy/reverse",
    status_code=201,
    dependencies=[SessionOnly],
    name="reverse_legacy_payment",
)
def reverse_legacy_payment(
    rid: str, body: models.PaymentReverseIn, user: PaymentWrite, services: ServicesDep
) -> dict[str, Any]:
    """Reverse the virtual payment of a legacy paid invoice (credit-notes-payments.md 2.3)."""
    result = services.sales.payments.reverse_legacy(rid, user, date=body.date, note=body.note)
    return _payment_result(services, result)


@payments_router.post(
    "/invoices/{rid}/settle-discount", status_code=201, dependencies=[SessionOnly], name="settle_discount"
)
def settle_discount(
    rid: str, body: models.SettleDiscountIn, user: PaymentWrite, services: ServicesDep
) -> dict[str, Any]:
    """Settle with a cash discount: a discount credit note for the rest, then the payment, in one transaction."""
    result = services.credit_notes.settle_discount(
        rid, date=body.date, amount_paid=body.amount_paid, note=body.note, user=user
    )
    return _payment_result(services, result)


@payments_router.get("/invoices/{rid}/credit-notes", name="list_credit_notes")
def list_credit_notes(rid: str, user: InvoiceRead, services: ServicesDep) -> list[dict[str, Any]]:
    return services.credit_notes.list(rid)


@payments_router.post(
    "/invoices/{rid}/credit-notes", status_code=201, dependencies=[SessionOnly], name="create_credit_note"
)
def create_credit_note(
    rid: str, body: models.CreditNoteIn, user: PaymentWrite, services: ServicesDep
) -> dict[str, Any]:
    """Issue a storno, partial or write-off credit note (a write-off has no number); snapshot, number and posting in
    one transaction."""
    return services.credit_notes.create(
        rid,
        credit_kind=body.credit_kind,
        date=body.date,
        reason=body.reason,
        amounts=body.amounts,
        user=user,
    )
