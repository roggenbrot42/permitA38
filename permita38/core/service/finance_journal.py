"""The Money page's figures from the journal (docs/specs/accounting.md section 14, last paragraph; work package O12).

Once accounting is initialised for a year, ``calc.finance`` takes revenue, costs and VAT per month from the journal:
revenue and costs by ``income_date`` (EÜR: payment date, accrual: document date; the lines carry the right date for
the basis), output and input VAT by ``vat_date``, § 13b included (accounting.md 9.4: the RC tax lines are output VAT,
their input lines input VAT). Money stays exact (``Decimal`` euros, cents from the journal). The response shape is the
old one; the caller adds ``approximate`` (``True`` for the old computation).

The page shows the booked tax (the VAT accounts); UStVA Kz 83 uses the form tax, so a cent difference between the two
stays until the annual return (accounting.md 9.6).
"""

from datetime import date
from decimal import Decimal
from typing import Any

from permita38.core import doc_totals
from permita38.core.money import ZERO
from permita38.core.repository import Repository
from permita38.core.service.accounting_reports import AccountingReportService
from permita38.core.settings import accounting_start

__all__ = ["figures", "initialised_year"]

# roles that make the Money page's revenue / costs (net of VAT); everything else on the journal is VAT, balance sheet,
# taxes or the owner's private share and has no place there (the old page had none either)
_REVENUE_PREFIX = "revenue_"
_REVENUE_REDUCTIONS = ("bad_debts", "cash_discount_granted")
_COST_PREFIX = "expense_"
_COST_ROLES = ("meals_deductible", "travel_costs")
_OUTPUT_KEYS = ("S19", "S7", "SX", "RC_EU_SVC", "RC_3C_SVC")  # tax_kind "tax"
_INPUT_KEYS = ("VST", "RC_EU_SVC", "RC_3C_SVC")  # tax_kind "input"
_FAR_FUTURE = date(9999, 12, 31)


def _eur(cents: int) -> Decimal:
    return Decimal(int(cents)).scaleb(-2)


def initialised_year(store: Repository, year: int) -> dict[str, Any] | None:
    """The fiscal year row when the journal covers all of ``year`` (it has a row and the accounting start is not
    later than 1 January of it), else ``None``: a year that began before the start stays on the old computation."""
    start = accounting_start(store.settings())
    if start is None or start > date(year, 1, 1):
        return None
    for row in store.list("fiscal_years"):
        if int(row["year"]) == year:
            return row
    return None


def _category(chart: Any, role: str) -> str:
    for name, target in chart.categories:
        if str(target) == role:
            return name
    return "Other" if role == "expense_other" else role.removeprefix(_COST_PREFIX)


def figures(store: Repository, year: int) -> tuple[list[dict[str, Any]], dict[str, Decimal], Decimal] | None:
    """``(months, categories, open_receivables)`` from the journal, or ``None`` when ``year`` is not initialised."""
    fy = initialised_year(store, year)
    if fy is None:
        return None
    reports = AccountingReportService(store)
    data = reports.load_income_year(fy)
    months = doc_totals.empty_months()
    cats: dict[str, Decimal] = {}
    for p in data.lines:
        when, role, cents = p.line.income_date, str(p.line.role), int(p.line.amount_cents)
        if when is None or when.year != year or not cents:
            continue
        row = months[when.month - 1]
        if role.startswith(_REVENUE_PREFIX) or role in _REVENUE_REDUCTIONS:
            row["revenue"] -= _eur(cents)  # revenue is a credit, a write-off or discount a debit
        elif role.startswith(_COST_PREFIX) or role in _COST_ROLES:
            row["expenses"] += _eur(cents)
            name = _category(data.chart, role)
            cats[name] = cats.get(name, ZERO) + _eur(cents)
    for rec in store.journal_lines_by_vat_date(date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()):
        when = date.fromisoformat(str(rec["vat_date"]))
        cents, key, kind = int(rec["amount_cents"]), rec.get("tax_key"), rec.get("tax_kind")
        if kind == "tax" and key in _OUTPUT_KEYS:
            months[when.month - 1]["vat_out"] -= _eur(cents)  # output VAT is a credit
        elif kind == "input" and key in _INPUT_KEYS:
            months[when.month - 1]["vat_in"] += _eur(cents)
    return months, cats, _open_receivables(store, reports, fy)


def _open_receivables(store: Repository, reports: AccountingReportService, fy: dict[str, Any]) -> Decimal:
    """Accrual years book receivables: their journal balance. A cash-basis (EÜR) year books none, so what is still
    owed comes from the documents like before (it is a position, not part of the profit)."""
    if str(fy.get("income_basis")) == "accrual":
        total = reports.open_items(int(fy["year"]), _FAR_FUTURE).receivables_total  # a pydantic float field
        return _eur(round(Decimal(str(total)) * 100))  # back to whole cents, never float arithmetic on the figure
    return doc_totals.open_receivables_of_documents(store)
