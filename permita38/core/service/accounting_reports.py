"""Journal and report reads for the accounting routes (docs/specs/accounting.md 14, 18).

A thin layer: the journal is loaded once per request, turned into ``PostedLine`` and handed to the pure functions of
``core/accounting/reports.py``. Money in the response models is an exact ``Decimal`` (cents scaled by -2) and goes out
as a decimal string (spec 7a). No SQL here: reads go through the ``Repository`` interface.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import cast

from pydantic import BaseModel

from permita38.core import models
from permita38.core import validation as v
from permita38.core.accounting import reports
from permita38.core.accounting.types import AccountNo, Cents, Line, Role
from permita38.core.charts import Chart, ChartAccount, ChartMissing, ChartName, chart_for
from permita38.core.charts import Role as ChartRole
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record

PAGE = 500
OPEN_ITEM_ROLES = {"receivables": "receivable", "payables": "payable"}
FY_NOT_FOUND = "Fiscal year not found"


def _m(cents: int) -> Decimal:
    """Cents as exact euros (``Money`` fields turn this into a decimal string)."""
    return Decimal(int(cents)).scaleb(-2)


# ---------------------------------------------------------------- response models
class AccountOut(BaseModel):
    account: str
    name: str
    account_class: str
    role: str | None = None
    eur_line: str | None = None
    deductible: bool | None = None
    automatic: str | None = None


class TrialRowOut(BaseModel):
    account: str
    name: str
    debit: v.Money
    credit: v.Money
    balance: v.Money


class TrialBalanceOut(BaseModel):
    year: int
    date_from: str
    date_to: str
    rows: list[TrialRowOut]
    debit_total: v.Money
    credit_total: v.Money
    balance_total: v.Money
    note: str


class LedgerRowOut(BaseModel):
    booking_date: str
    entry_no: int
    role: str
    debit: v.Money
    credit: v.Money
    balance: v.Money


class LedgerOut(BaseModel):
    year: int
    account: str
    name: str
    date_from: str
    date_to: str
    opening: v.Money
    rows: list[LedgerRowOut]
    closing: v.Money


class EurLineOut(BaseModel):
    line: str
    amount: v.Money


class EurOut(BaseModel):
    year: int
    lines: list[EurLineOut]
    vat_received: v.Money
    vat_refunded: v.Money
    input_vat_paid: v.Money
    vat_paid: v.Money
    total_income: v.Money
    total_expense: v.Money
    profit: v.Money
    non_deductible: list[EurLineOut]


class PnlRowOut(BaseModel):
    account: str
    name: str
    amount: v.Money


class PnlOut(BaseModel):
    year: int
    income: list[PnlRowOut]
    expenses: list[PnlRowOut]
    total_income: v.Money
    total_expense: v.Money
    profit: v.Money


class BalanceRowOut(BaseModel):
    account: str
    name: str
    balance: v.Money


class BalanceSheetOut(BaseModel):
    year: int
    as_of: str
    debit_side: list[BalanceRowOut]
    credit_side: list[BalanceRowOut]
    debit_total: v.Money
    credit_total: v.Money
    result: v.Money
    difference: v.Money


class OpenItemOut(BaseModel):
    open_item_id: str
    kind: str  # receivable | payable
    account: str
    source_coll: str | None = None
    source_id: str | None = None
    source_number: str | None = None
    counterparty_id: str | None = None
    open_amount: v.Money  # receivable: debit - credit; payable: credit - debit (negative = overpaid)
    since: str
    age_days: int


class OpenItemsOut(BaseModel):
    year: int
    as_of: str
    items: list[OpenItemOut]
    receivables_total: v.Money
    payables_total: v.Money


# ---------------------------------------------------------------- helpers
def parse_date(text: str | None, name: str) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ServiceError(422, f"{name} must be a date like 2026-01-31") from None


def _opt_date(text: object) -> date | None:
    return date.fromisoformat(str(text)) if text else None


@dataclass(frozen=True)
class Loaded:
    """One request's view: the fiscal year, its chart (extended by the accounts without a role) and the posted lines."""

    year: int
    chart: Chart
    lines: list[reports.PostedLine]


def _extended_chart(chart: Chart) -> Chart:
    """The chart plus a synthetic role ``acct:<no>`` for every account that has no role, so that journal lines posted
    by account number can be reported like role lines."""
    have = {acc.account for _, acc in chart.roles}
    extra = tuple((ChartRole(f"acct:{acc.account}"), acc) for acc in chart.accounts if acc.account not in have)
    return replace(chart, roles=(*chart.roles, *extra)) if extra else chart


class AccountingReportService:
    def __init__(self, store: Repository):
        self.store = store

    # ---------- year and chart
    def fiscal_year(self, year: int | None) -> Record:
        rows = self.store.list("fiscal_years")
        if year is None:
            if not rows:
                raise ServiceError(404, FY_NOT_FOUND)
            return max(rows, key=lambda r: int(r["year"]))
        for r in rows:
            if int(r["year"]) == year:
                return r
        raise ServiceError(404, FY_NOT_FOUND)

    @staticmethod
    def _chart(row: Record) -> Chart:
        year = int(row["year"])
        try:
            return chart_for(cast(ChartName, row["chart"]), year)
        except ChartMissing as e:
            raise ServiceError(409, f"No chart of accounts for fiscal year {year}: {e}") from None

    def _all_entries(self, date_from: date | None = None, date_to: date | None = None) -> Iterator[Record]:
        offset = 0
        start = date_from.isoformat() if date_from else None
        end = date_to.isoformat() if date_to else None
        while True:
            page = self.store.list_journal_entries(None, start, end, PAGE, offset)
            yield from page
            if len(page) < PAGE:
                return
            offset += PAGE

    def load(self, year: int | None) -> Loaded:
        """The fiscal year's chart and every posted line of the journal (all years: the balance sheet cumulates, the
        EÜR / P&L filter by ``income_date`` themselves)."""
        fy = self.fiscal_year(year)
        chart = _extended_chart(self._chart(fy))
        by_account: dict[str, str] = {}
        for role, acc in chart.roles:
            by_account.setdefault(str(acc.account), str(role))
        lines: list[reports.PostedLine] = []
        for e in self._all_entries():
            booking = date.fromisoformat(str(e["booking_date"]))
            for row in e.get("lines", []):
                name = row.get("role") or by_account.get(str(row["account"]), f"acct:{row['account']}")
                line = Line(
                    role=Role(str(name)),
                    amount_cents=Cents(int(row["amount_cents"])),
                    income_date=_opt_date(row.get("income_date")),
                    vat_date=_opt_date(row.get("vat_date")),
                    open_item_id=row.get("open_item_id"),
                )
                lines.append(reports.PostedLine(line, booking, int(e["entry_no"])))
        return Loaded(int(fy["year"]), chart, lines)

    # ---------- journal and accounts
    def journal(
        self,
        date_from: date | None,
        date_to: date | None,
        account: str | None,
        tax_key: str | None,
        source: str | None,
        offset: int,
        limit: int,
    ) -> list[models.JournalEntry]:
        """Entries (with all their lines) in ``(year, entry_no)`` order that have a line on ``account`` / with
        ``tax_key`` and come from ``source`` (``source_coll``, e.g. ``invoices``); paged after filtering."""
        out: list[models.JournalEntry] = []
        skipped = 0
        for e in self._all_entries(date_from, date_to):
            rows = e.get("lines", [])
            if source is not None and e.get("source_coll") != source:
                continue
            if account is not None and not any(r.get("account") == account for r in rows):
                continue
            if tax_key is not None and not any(r.get("tax_key") == tax_key for r in rows):
                continue
            if skipped < offset:
                skipped += 1
                continue
            out.append(models.JournalEntry.model_validate(e))
            if len(out) >= limit:
                break
        return out

    def accounts(self, year: int | None) -> list[AccountOut]:
        chart = self._chart(self.fiscal_year(year))
        roles: dict[str, str] = {}
        for role, acc in chart.roles:
            roles.setdefault(str(acc.account), str(role))
        seen: dict[str, ChartAccount] = {}
        for acc in (*(a for _, a in chart.roles), *chart.accounts):
            seen.setdefault(str(acc.account), acc)
        return [
            AccountOut(
                account=no,
                name=acc.name,
                account_class=acc.account_class,
                role=roles.get(no),
                eur_line=acc.eur_line.line if acc.eur_line else None,
                deductible=acc.eur_line.deductible if acc.eur_line else None,
                automatic=acc.automatic,
            )
            for no, acc in sorted(seen.items())
        ]

    # ---------- reports
    @staticmethod
    def _period(year: int, date_from: date | None, date_to: date | None) -> tuple[date, date]:
        start, end = date_from or date(year, 1, 1), date_to or date(year, 12, 31)
        if start > end:
            raise ServiceError(422, "from must not be after to")
        return start, end

    def trial_balance(self, year: int | None, date_from: date | None, date_to: date | None) -> TrialBalanceOut:
        data = self.load(year)
        start, end = self._period(data.year, date_from, date_to)
        tb = reports.trial_balance(data.lines, data.chart, start, end)
        return TrialBalanceOut(
            year=data.year,
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            rows=[
                TrialRowOut(
                    account=r.account, name=r.name, debit=_m(r.debit), credit=_m(r.credit), balance=_m(r.balance)
                )
                for r in tb.rows
            ],
            debit_total=_m(tb.debit_total),
            credit_total=_m(tb.credit_total),
            balance_total=_m(tb.balance_total),
            note=tb.note,
        )

    def ledger(
        self, year: int | None, account: str | None, role: str | None, date_from: date | None, date_to: date | None
    ) -> LedgerOut:
        if not account and not role:
            raise ServiceError(422, "The ledger needs an account or a role")
        data = self.load(year)
        by_role = {str(r): acc for r, acc in data.chart.roles}
        if role:
            if role not in by_role:
                raise ServiceError(422, f"Unknown role '{role}'")
            no = str(by_role[role].account)
            if account and account != no:
                raise ServiceError(422, f"role '{role}' is account {no}, not {account}")
        else:
            no = str(account)
        names = {str(acc.account): acc.name for acc in by_role.values()}
        if no not in names:
            raise ServiceError(422, f"Unknown account '{no}'")
        start, end = self._period(data.year, date_from, date_to)
        led = reports.ledger(data.lines, data.chart, AccountNo(no), start, end)
        return LedgerOut(
            year=data.year,
            account=no,
            name=names[no],
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            opening=_m(led.opening),
            rows=[
                LedgerRowOut(
                    booking_date=r.booking_date.isoformat(),
                    entry_no=r.entry_no,
                    role=r.role,
                    debit=_m(r.debit),
                    credit=_m(r.credit),
                    balance=_m(r.balance),
                )
                for r in led.rows
            ],
            closing=_m(led.closing),
        )

    def eur(self, year: int | None) -> EurOut:
        data = self.load(year)
        r = reports.eur_report(data.lines, data.chart, data.year)
        return EurOut(
            year=r.year,
            lines=[EurLineOut(line=t.line, amount=_m(t.amount)) for t in r.lines],
            vat_received=_m(r.vat_received),
            vat_refunded=_m(r.vat_refunded),
            input_vat_paid=_m(r.input_vat_paid),
            vat_paid=_m(r.vat_paid),
            total_income=_m(r.total_income),
            total_expense=_m(r.total_expense),
            profit=_m(r.profit),
            non_deductible=[EurLineOut(line=t.line, amount=_m(t.amount)) for t in r.non_deductible],
        )

    def pnl(self, year: int | None) -> PnlOut:
        data = self.load(year)
        r = reports.pnl(data.lines, data.chart, data.year)

        def rows(items: Sequence[reports.PnlRow]) -> list[PnlRowOut]:
            return [PnlRowOut(account=i.account, name=i.name, amount=_m(i.amount)) for i in items]

        return PnlOut(
            year=r.year,
            income=rows(r.income),
            expenses=rows(r.expenses),
            total_income=_m(r.total_income),
            total_expense=_m(r.total_expense),
            profit=_m(r.profit),
        )

    def balance_sheet(self, year: int | None, as_of: date | None) -> BalanceSheetOut:
        data = self.load(year)
        day = as_of or date(data.year, 12, 31)
        b = reports.balance_sheet(data.lines, data.chart, day)

        def rows(items: Sequence[reports.BalanceRow]) -> list[BalanceRowOut]:
            return [BalanceRowOut(account=i.account, name=i.name, balance=_m(i.balance)) for i in items]

        return BalanceSheetOut(
            year=data.year,
            as_of=day.isoformat(),
            debit_side=rows(b.debit_side),
            credit_side=rows(b.credit_side),
            debit_total=_m(b.debit_total),
            credit_total=_m(b.credit_total),
            result=_m(b.result),
            difference=_m(b.difference),
        )

    def open_items(self, year: int | None, as_of: date | None) -> OpenItemsOut:
        """Receivables and payables per document: the lines on the ``receivables`` / ``payables`` accounts with an
        ``open_item_id``, summed per item up to ``as_of`` (booking date); items at 0 are settled and left out."""
        data = self.load(year)
        day = as_of or date(data.year, 12, 31)
        accounts = {str(r): str(a.account) for r, a in data.chart.roles if str(r) in OPEN_ITEM_ROLES}
        role_of = {no: role for role, no in accounts.items()}
        totals: dict[tuple[str, str], int] = {}
        first: dict[tuple[str, str], tuple[date, int, Record]] = {}
        for e in self._all_entries():
            booking = date.fromisoformat(str(e["booking_date"]))
            if booking > day:
                continue
            for row in e.get("lines", []):
                item = row.get("open_item_id")
                role = role_of.get(str(row.get("account")))
                if not item or role is None:
                    continue
                key = (str(item), role)
                totals[key] = totals.get(key, 0) + int(row["amount_cents"])
                rank = (booking, int(e["entry_no"]))
                if key not in first or rank < first[key][:2]:
                    first[key] = (booking, int(e["entry_no"]), e)
        items: list[OpenItemOut] = []
        receivables = payables = 0
        for key in sorted(totals, key=lambda k: (first[k][0], first[k][1], k)):
            total = totals[key]
            if total == 0:
                continue
            item, role = key
            kind = OPEN_ITEM_ROLES[role]
            amount = total if kind == "receivable" else -total
            since, _no, entry = first[key]
            if kind == "receivable":
                receivables += amount
            else:
                payables += amount
            items.append(
                OpenItemOut(
                    open_item_id=item,
                    kind=kind,
                    account=accounts[role],
                    source_coll=entry.get("source_coll"),
                    source_id=entry.get("source_id"),
                    source_number=entry.get("source_number"),
                    counterparty_id=entry.get("counterparty_id"),
                    open_amount=_m(amount),
                    since=since.isoformat(),
                    age_days=(day - since).days,
                )
            )
        return OpenItemsOut(
            year=data.year,
            as_of=day.isoformat(),
            items=items,
            receivables_total=_m(receivables),
            payables_total=_m(payables),
        )
