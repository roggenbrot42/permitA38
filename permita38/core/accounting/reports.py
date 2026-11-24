"""Reports over the journal (docs/specs/accounting.md section 14, items 2-5): pure functions, integer cents.

Input is a sequence of :class:`PostedLine` (a journal line with the booking date and number of its entry); the chart
maps roles to accounts, account classes and ``eur_line`` / ``deductible`` flags (section 5). Amounts follow the journal:
positive = debit, negative = credit. The EÜR, P&L and balance sheet report *income-signed* amounts instead
(income positive, expense negative) because that is how the forms read.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from permita38.core.accounting.types import AccountNo, Cents, Line
from permita38.core.charts import AccountNo as ChartAccountNo
from permita38.core.charts import Chart, ChartAccount

__all__ = [
    "VAT_ROUNDING_NOTE",
    "BalanceRow",
    "BalanceSheet",
    "EurLineTotal",
    "EurReport",
    "LedgerReport",
    "LedgerRow",
    "PnlReport",
    "PnlRow",
    "PostedLine",
    "TrialBalance",
    "TrialRow",
    "balance_sheet",
    "eur_report",
    "ledger",
    "pnl",
    "trial_balance",
]

VAT_ROUNDING_NOTE = "VAT rounding to be settled by the annual return"

_PREPAYMENT_ROLES = ("vat_prepayments", "vat_special_prepayment")
_VAT_RECEIVED_PREFIXES = ("vat_soll_clearing_", "vat_out_")


@dataclass(frozen=True)
class PostedLine:
    """A journal line together with the booking date and the number of its entry."""

    line: Line
    booking_date: date
    entry_no: int


@dataclass(frozen=True)
class TrialRow:
    account: AccountNo
    name: str
    debit: Cents
    credit: Cents

    @property
    def balance(self) -> Cents:
        return Cents(self.debit - self.credit)


@dataclass(frozen=True)
class TrialBalance:
    rows: tuple[TrialRow, ...]
    debit_total: Cents
    credit_total: Cents
    note: str = VAT_ROUNDING_NOTE

    @property
    def balance_total(self) -> Cents:
        return Cents(self.debit_total - self.credit_total)


@dataclass(frozen=True)
class LedgerRow:
    booking_date: date
    entry_no: int
    role: str
    debit: Cents
    credit: Cents
    balance: Cents  # running balance (debit - credit) after this row


@dataclass(frozen=True)
class LedgerReport:
    account: AccountNo
    opening: Cents  # balance of all lines booked before ``date_from``
    rows: tuple[LedgerRow, ...]

    @property
    def closing(self) -> Cents:
        return self.rows[-1].balance if self.rows else self.opening


@dataclass(frozen=True)
class EurLineTotal:
    line: str
    amount: Cents  # income-signed: income positive, expense negative


@dataclass(frozen=True)
class EurReport:
    year: int
    lines: tuple[EurLineTotal, ...]  # deductible eur lines (revenue and expenses)
    vat_received: Cents
    vat_refunded: Cents
    input_vat_paid: Cents
    vat_paid: Cents
    total_income: Cents
    total_expense: Cents
    profit: Cents
    non_deductible: tuple[EurLineTotal, ...]  # below the profit: "not deductible, added back" / non-taxable


@dataclass(frozen=True)
class PnlRow:
    account: AccountNo
    name: str
    amount: Cents  # income-signed


@dataclass(frozen=True)
class PnlReport:
    year: int
    income: tuple[PnlRow, ...]
    expenses: tuple[PnlRow, ...]
    total_income: Cents
    total_expense: Cents  # positive number
    profit: Cents


@dataclass(frozen=True)
class BalanceRow:
    account: AccountNo
    name: str
    balance: Cents  # debit - credit


@dataclass(frozen=True)
class BalanceSheet:
    as_of: date
    debit_side: tuple[BalanceRow, ...]  # accounts with a debit balance
    credit_side: tuple[BalanceRow, ...]  # accounts with a credit balance (balance negative)
    result: Cents  # P&L result not yet closed (income - expense), cumulative up to ``as_of``

    @property
    def debit_total(self) -> Cents:
        return Cents(sum(r.balance for r in self.debit_side))

    @property
    def credit_total(self) -> Cents:
        return Cents(-sum(r.balance for r in self.credit_side))

    @property
    def difference(self) -> Cents:
        """``debit_total - credit_total - result``: 0 for a balanced journal."""
        return Cents(self.debit_total - self.credit_total - self.result)


def _accounts_by_role(chart: Chart) -> dict[str, ChartAccount]:
    return {str(role): acc for role, acc in chart.roles}


def _account_names(chart: Chart) -> dict[ChartAccountNo, str]:
    names: dict[ChartAccountNo, str] = {}
    for _, acc in chart.roles:
        names.setdefault(acc.account, acc.name)
    for acc in chart.accounts:
        names.setdefault(acc.account, acc.name)
    return names


def _role_account(by_role: dict[str, ChartAccount], role: str) -> ChartAccount:
    try:
        return by_role[role]
    except KeyError:
        raise KeyError(f"role '{role}' is not in the chart") from None


def _within(lines: Iterable[PostedLine], date_from: date | None, date_to: date | None) -> list[PostedLine]:
    return [
        p
        for p in lines
        if (date_from is None or p.booking_date >= date_from) and (date_to is None or p.booking_date <= date_to)
    ]


def _ordered(lines: Iterable[PostedLine]) -> list[PostedLine]:
    return sorted(lines, key=lambda p: (p.booking_date, p.entry_no))  # stable: lines of one entry keep their order


def trial_balance(
    lines: Sequence[PostedLine], chart: Chart, date_from: date | None, date_to: date | None
) -> TrialBalance:
    """Debit, credit and balance per account over the lines booked in ``[date_from, date_to]`` (inclusive)."""
    by_role = _accounts_by_role(chart)
    names = _account_names(chart)
    debit: dict[ChartAccountNo, int] = {}
    credit: dict[ChartAccountNo, int] = {}
    for p in _within(lines, date_from, date_to):
        no = _role_account(by_role, str(p.line.role)).account
        debit.setdefault(no, 0)
        credit.setdefault(no, 0)
        if p.line.amount_cents >= 0:
            debit[no] += p.line.amount_cents
        else:
            credit[no] -= p.line.amount_cents
    rows = tuple(
        TrialRow(AccountNo(no), names.get(no, ""), Cents(debit[no]), Cents(credit[no])) for no in sorted(debit)
    )
    return TrialBalance(rows, Cents(sum(debit.values())), Cents(sum(credit.values())))


def ledger(
    lines: Sequence[PostedLine], chart: Chart, account: AccountNo, date_from: date | None, date_to: date | None
) -> LedgerReport:
    """The postings of one account in ``[date_from, date_to]`` with a running balance (debit - credit).

    The opening balance is the sum of the lines of the account booked before ``date_from``."""
    by_role = _accounts_by_role(chart)
    mine = [p for p in _ordered(lines) if _role_account(by_role, str(p.line.role)).account == str(account)]
    opening = 0
    if date_from is not None:
        opening = sum(p.line.amount_cents for p in mine if p.booking_date < date_from)
    running = opening
    rows: list[LedgerRow] = []
    for p in _within(mine, date_from, date_to):
        amount = p.line.amount_cents
        running += amount
        rows.append(
            LedgerRow(
                p.booking_date,
                p.entry_no,
                str(p.line.role),
                Cents(max(amount, 0)),
                Cents(max(-amount, 0)),
                Cents(running),
            )
        )
    return LedgerReport(account, Cents(opening), tuple(rows))


def _totals(amounts: dict[str, int]) -> tuple[EurLineTotal, ...]:
    return tuple(EurLineTotal(name, Cents(value)) for name, value in amounts.items())  # insertion order


def eur_report(lines: Sequence[PostedLine], chart: Chart, year: int) -> EurReport:
    """The EÜR in gross form from the lines whose ``income_date`` lies in ``year`` (spec 14 item 4).

    * Lines of accounts with an ``eur_line`` are summed per line, income-signed; ``deductible`` lines make the profit,
      the others are listed in ``non_deductible``.
    * ``vat_soll_clearing_*`` / ``vat_out_*`` credits are "VAT received"; ``input_vat_clearing`` debits "input VAT
      paid"; ``vat_prepayments`` and ``vat_special_prepayment`` debits are "VAT paid to the tax office", their credits
      "VAT refunded"; ``vat_paid_private`` reduces "VAT paid". Everything else (private shares, balance accounts) is
      ignored, as are lines without ``income_date``. A debit on ``vat_soll_clearing_*`` / ``vat_out_*`` (credit note,
      refund) reduces "VAT received". A reversed VAT payment (credit on a prepayment role) shows in "VAT refunded"
      and the original in "VAT paid"; the profit is unchanged by the pair.
    * profit = deductible lines + VAT received + VAT refunded - input VAT paid - VAT paid.
    """
    by_role = _accounts_by_role(chart)
    deductible: dict[str, int] = {}
    other: dict[str, int] = {}
    vat_received = vat_refunded = input_vat_paid = vat_paid = 0
    for p in lines:
        d = p.line.income_date
        if d is None or d.year != year:
            continue
        role, amount = str(p.line.role), p.line.amount_cents
        acc = _role_account(by_role, role)
        if role in _PREPAYMENT_ROLES:
            if amount >= 0:
                vat_paid += amount
            else:
                vat_refunded -= amount
        elif role == "vat_paid_private":
            vat_paid += amount  # a credit: the private share reduces the VAT paid
        elif role == "input_vat_clearing":
            input_vat_paid += amount
        elif role.startswith(_VAT_RECEIVED_PREFIXES):
            vat_received -= amount
        elif acc.eur_line is not None:
            target = deductible if acc.eur_line.deductible else other
            target[acc.eur_line.line] = target.get(acc.eur_line.line, 0) - amount
    line_totals = _totals(deductible)
    income = sum(t.amount for t in line_totals if t.amount > 0) + vat_received + vat_refunded
    expense = -sum(t.amount for t in line_totals if t.amount < 0) + input_vat_paid + vat_paid
    return EurReport(
        year=year,
        lines=line_totals,
        vat_received=Cents(vat_received),
        vat_refunded=Cents(vat_refunded),
        input_vat_paid=Cents(input_vat_paid),
        vat_paid=Cents(vat_paid),
        total_income=Cents(income),
        total_expense=Cents(expense),
        profit=Cents(income - expense),
        non_deductible=_totals(other),
    )


def _pl_amounts(
    lines: Iterable[PostedLine], by_role: dict[str, ChartAccount], keep: Callable[[PostedLine], bool]
) -> dict[ChartAccountNo, int]:
    """Income-signed sum per P&L-class account over the lines ``keep`` accepts."""
    out: dict[ChartAccountNo, int] = {}
    for p in lines:
        acc = _role_account(by_role, str(p.line.role))
        if acc.account_class == "pl" and keep(p):
            out[acc.account] = out.get(acc.account, 0) - p.line.amount_cents
    return out


def pnl(lines: Sequence[PostedLine], chart: Chart, year: int) -> PnlReport:
    """Accrual P&L: the P&L-class accounts, income-signed, for the lines whose ``income_date`` lies in ``year``.

    Lines without ``income_date`` are ignored (no fallback to the booking date), like in :func:`eur_report`: the year
    closing moves the P&L accounts to ``profit_carryforward`` without ``income_date`` and so leaves the P&L of the
    year unchanged. Accounts with a net of 0 are not listed. Compare :func:`balance_sheet`, which uses the booking
    date."""
    names = _account_names(chart)
    amounts = _pl_amounts(
        lines, _accounts_by_role(chart), lambda p: p.line.income_date is not None and p.line.income_date.year == year
    )
    rows = [PnlRow(AccountNo(no), names.get(no, ""), Cents(v)) for no, v in sorted(amounts.items()) if v]
    income = tuple(r for r in rows if r.amount > 0)
    expenses = tuple(r for r in rows if r.amount < 0)
    total_income = sum(r.amount for r in income)
    total_expense = -sum(r.amount for r in expenses)
    return PnlReport(
        year, income, expenses, Cents(total_income), Cents(total_expense), Cents(total_income - total_expense)
    )


def balance_sheet(lines: Sequence[PostedLine], chart: Chart, as_of: date) -> BalanceSheet:
    """Accrual balance sheet: the balance-class accounts cumulated up to ``as_of`` (by booking date), split by the sign
    of the balance; zero balances are left out. ``result`` is the not yet closed P&L result up to ``as_of``, also by
    booking date (not ``income_date``), so the sheet always balances: a cost booked on 30 Dec with ``income_date``
    2 Jan is in the result of 31 Dec here, but in the next year's P&L, and ``difference`` stays 0. The year closing
    (P&L accounts to ``profit_carryforward``) turns the result into a balance account."""
    by_role = _accounts_by_role(chart)
    names = _account_names(chart)
    balances: dict[ChartAccountNo, int] = {}
    for p in _within(lines, None, as_of):
        acc = _role_account(by_role, str(p.line.role))
        if acc.account_class == "balance":
            balances[acc.account] = balances.get(acc.account, 0) + p.line.amount_cents
    rows = [BalanceRow(AccountNo(no), names.get(no, ""), Cents(v)) for no, v in sorted(balances.items()) if v]
    result = sum(_pl_amounts(lines, by_role, lambda p: p.booking_date <= as_of).values())
    return BalanceSheet(
        as_of,
        tuple(r for r in rows if r.balance > 0),
        tuple(r for r in rows if r.balance < 0),
        Cents(result),
    )
