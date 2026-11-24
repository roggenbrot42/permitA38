"""Accounting routes (docs/specs/accounting.md 17-18, credit-notes-payments.md 3-4): skeletons.

Every route exists with its rights; the handlers answer 501 until the service work packages land. Routers stay thin.
API tokens: reads follow the owner's rights; every route that changes something is ``SessionOnly`` (no write to
`accounting` or `vat` through tokens in v1).
"""

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from permita38.api.deps import CurrentUser, Needs, ServicesDep, SessionOnly
from permita38.api.responses import present
from permita38.core import models, rights
from permita38.core.service.manual_entries import EntryTemplate

router = APIRouter(prefix="/accounting", tags=["accounting"])
payments_router = APIRouter(tags=["accounting"])
routers = [router, payments_router]

NOT_IMPLEMENTED = "not implemented yet"


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


# ------------------------------------------------------------------ fiscal years, initialisation
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


@router.post("/years/{year}/close", dependencies=[SessionOnly], name="close_fiscal_year")
def close_year(year: int, user: Boss, services: ServicesDep):
    not_implemented()


@router.post("/init", dependencies=[SessionOnly], name="init_accounting")
def init_accounting(body: models.AccountingInitIn, user: Boss, services: ServicesDep):
    not_implemented()


@router.get("/init/review", name="accounting_init_review")
def init_review(user: Read, services: ServicesDep):
    not_implemented()


# ------------------------------------------------------------------ journal, accounts
@router.get("/journal", name="list_journal")
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
    not_implemented()


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


@router.get("/accounts", name="list_accounts")
def accounts(user: Read, services: ServicesDep, year: Annotated[int | None, Query(ge=2000, le=2100)] = None):
    not_implemented()


# ------------------------------------------------------------------ reports (read only)
def _report_route(name: str) -> None:
    @router.get(f"/reports/{name}", name=f"report_{name.replace('-', '_')}")
    def report(
        user: Read,
        services: ServicesDep,
        year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
        date_from: Annotated[str | None, Query(alias="from", max_length=10)] = None,
        date_to: Annotated[str | None, Query(alias="to", max_length=10)] = None,
        account: Annotated[str | None, Query(max_length=10)] = None,
    ):
        not_implemented()


for _name in ("trial-balance", "eur", "pnl", "balance-sheet", "open-items", "ledger"):
    _report_route(_name)


# ------------------------------------------------------------------ locks, DATEV, integrity
@router.get("/locks", name="list_period_locks", response_model=list[models.PeriodLock])
def locks(user: Read, services: ServicesDep):
    return [models.PeriodLock(**r) for r in services.locks.all()]


@router.post(
    "/locks", status_code=201, dependencies=[SessionOnly], name="lock_period", response_model=list[models.PeriodLock]
)
def lock_period(body: models.PeriodLockIn, user: Write, services: ServicesDep):
    """Lock every month up to the given one (in order); answers the newly locked months."""
    return [models.PeriodLock(**r) for r in services.locks.lock_up_to(body.year, body.month, "manual", str(user["id"]))]


@router.get("/datev", name="datev_export")
def datev(
    user: Read,
    services: ServicesDep,
    date_from: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    date_to: Annotated[str | None, Query(alias="to", max_length=10)] = None,
):
    not_implemented()


@router.get("/integrity", name="accounting_integrity")
def integrity(user: Read, services: ServicesDep):
    not_implemented()


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
