"""Postings of an expense: bill and payment entries (docs/specs/accounting.md 9.4). Pure functions, integer cents."""

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from permita38.core.accounting.types import Bases, Cents, Entry, Line, Role, TaxKey
from permita38.core.money import q2

__all__ = [
    "Expense",
    "ExpenseLine",
    "ExpenseTreatment",
    "category_role",
    "expense_bill_entry",
    "expense_pay_entry",
    "tax_date_13b",
]

ExpenseTreatment = Literal["standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country"]

_Day = date  # the dataclass field `date` shadows the type inside `Expense`
_MEALS = "meals"
_RC = ("reverse_charge_eu_service", "reverse_charge_third_country")


@dataclass(frozen=True)
class ExpenseLine:
    """One expense line (classic expense = one line). ``pct`` = income share, ``vat_pct`` = VAT share (spec 9.4).

    ``role`` is the cost role of the category (the chart's category table); ``None`` = derived by ``category_role``.
    """

    net: Cents
    vat: Cents
    rate: Decimal
    pct: Decimal
    vat_pct: Decimal
    category: str
    role: Role | None = None


@dataclass(frozen=True)
class Expense:
    """The posting-relevant data of an expense; ``bill_rev`` / ``pay_rev`` are the revisions of the event keys."""

    id: str
    date: date
    treatment: ExpenseTreatment
    lines: tuple[ExpenseLine, ...]
    paid_on: _Day | None = None
    service_date: _Day | None = None
    small_business: bool = False
    bill_rev: int = 1
    pay_rev: int = 1


def category_role(category: str) -> Role:
    """Fallback category to role mapping (the real table is the chart's); meals post on ``meals_deductible``."""
    return Role("meals_deductible" if category == _MEALS else f"expense_{category}")


def _share(cents: int, pct: Decimal) -> Cents:
    """``q2(cents x pct / 100)`` expressed in cents, half-up."""
    return Cents(int(q2(Decimal(cents) * pct / Decimal(10000)) * 100))


def _rate_str(rate: Decimal) -> str:
    return format(rate.normalize(), "f")


def _suffix(rate: Decimal) -> str:
    text = _rate_str(rate)
    return text if text in ("19", "7") else "x"


def _income_date(day: date, booking: date, first_open_day: date) -> date:
    """The income date follows the document date within an open fiscal year, else the booking date (spec 11)."""
    return day if day.year >= first_open_day.year else booking


def tax_date_13b(expense: Expense) -> date:
    """§ 13b tax date: EU service = ``service_date``; third country = ``min(date, last day of the next month)``."""
    service = expense.service_date or expense.date
    if expense.treatment == "reverse_charge_eu_service":
        return service
    year, month = (service.year + 1, 1) if service.month == 12 else (service.year, service.month + 1)
    return min(expense.date, date(year, month, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class _Cost:
    role: Role
    meals: bool
    amount: Cents
    pct: Decimal


def _cost_lines(costs: list[_Cost], income: date | None) -> list[Line]:
    """Deductible part per category role, then the meals rest and the private rest; zero amounts are omitted."""
    deductible: dict[Role, int] = defaultdict(int)
    meals_rest = 0
    private_rest = 0
    for cost in costs:
        cd = _share(cost.amount, cost.pct)
        deductible[cost.role] += cd
        if cost.meals:
            meals_rest += cost.amount - cd
        else:
            private_rest += cost.amount - cd
    out = [Line(role, Cents(v), income_date=income) for role, v in deductible.items() if v]
    if meals_rest:
        out.append(Line(Role("meals_nondeductible"), Cents(meals_rest), income_date=income))
    if private_rest:
        out.append(Line(Role("private_withdrawal"), Cents(private_rest)))  # equity, no income date (oracle A17)
    return out


def _role_of(line: ExpenseLine) -> Role:
    return line.role if line.role is not None else category_role(line.category)


@dataclass(frozen=True)
class _StdSplit:
    iv_by_rate: dict[Decimal, int]
    costs: list[_Cost]
    gross: Cents


def _standard_split(expense: Expense) -> _StdSplit:
    """Per line ``iv = q2(v x vp)``, ``nd = v - iv``, cost ``c = n + nd`` (no input VAT: ``iv = 0``, ``c = g``)."""
    input_vat_allowed = expense.treatment == "standard" and not expense.small_business
    iv_by_rate: dict[Decimal, int] = defaultdict(int)
    costs: list[_Cost] = []
    gross = 0
    for ln in expense.lines:
        iv = _share(ln.vat, ln.vat_pct) if input_vat_allowed else 0
        iv_by_rate[ln.rate] += iv
        costs.append(_Cost(_role_of(ln), ln.category == _MEALS, Cents(ln.net + ln.vat - iv), ln.pct))
        gross += ln.net + ln.vat
    return _StdSplit(iv_by_rate, costs, Cents(gross))


@dataclass(frozen=True)
class _RcGroup:
    rate: Decimal
    net: Cents
    tax: Cents
    input_vat: Cents
    cost: _Cost  # accrual: net plus the non-deductible tax


def _rc_groups(expense: Expense) -> list[_RcGroup]:
    """Per (rate, VAT share, income share, category) group: ``t = q2(n x r)``, ``ri = q2(t x vp)`` (0: small business)."""
    keyed: dict[tuple[Decimal, Decimal, Decimal, str, Role], int] = defaultdict(int)
    for ln in expense.lines:
        keyed[(ln.rate, ln.vat_pct, ln.pct, ln.category, _role_of(ln))] += ln.net
    out: list[_RcGroup] = []
    for (rate, vp, pct, category, role), net in keyed.items():
        tax = _share(net, rate)
        ri = Cents(0) if expense.small_business else _share(tax, vp)
        out.append(_RcGroup(rate, Cents(net), tax, ri, _Cost(role, category == _MEALS, Cents(net + tax - ri), pct)))
    return out


def _rc_tax_lines(expense: Expense, groups: list[_RcGroup], vat_date: date) -> list[Line]:
    """Per rate: C ``rc_vat_out_*`` t (base n) and D ``rc_input_vat`` ri, both on the § 13b tax date."""
    eu = expense.treatment == "reverse_charge_eu_service"
    key = TaxKey("RC_EU_SVC" if eu else "RC_3C_SVC")
    role = Role("rc_vat_out_eu" if eu else "rc_vat_out_3c")
    by_rate: dict[Decimal, list[int]] = {}
    for g in groups:
        acc = by_rate.setdefault(g.rate, [0, 0, 0])
        acc[0] += g.net
        acc[1] += g.tax
        acc[2] += g.input_vat
    out: list[Line] = []
    for rate in sorted(by_rate, reverse=True):
        net, tax, ri = by_rate[rate]
        if not tax:
            continue
        out.append(
            Line(
                role,
                Cents(-tax),
                tax_key=key,
                tax_kind="tax",
                tax_rate=_rate_str(rate),
                tax_base_cents=Cents(net),
                vat_date=vat_date,
            )
        )
        if ri:
            out.append(
                Line(
                    Role("rc_input_vat"),
                    Cents(ri),
                    tax_key=key,
                    tax_kind="input",
                    tax_rate=_rate_str(rate),
                    vat_date=vat_date,
                )
            )
    return out


def _input_vat_lines(split: _StdSplit, vat_date: date) -> list[Line]:
    return [
        Line(
            Role(f"input_vat_s{_suffix(rate)}"),
            Cents(iv),
            tax_key=TaxKey("VST"),
            tax_kind="input",
            tax_rate=_rate_str(rate),
            vat_date=vat_date,
        )
        for rate, iv in sorted(split.iv_by_rate.items(), key=lambda kv: kv[0], reverse=True)
        if iv
    ]


def _entry(lines: list[Line], booking: date, doc_date: date, key: str) -> Entry | None:
    return Entry(tuple(lines), booking, doc_date, key) if lines else None


def _rc_bill_eur_lines(groups: list[_RcGroup], booking: date) -> list[Line]:
    """EÜR: the non-deductible tax waits in ``rc_vat_nondeductible``; its private share is corrected via
    ``vat_paid_private`` (spec 9.4)."""
    lines: list[Line] = []
    rest = sum(g.tax - g.input_vat for g in groups)
    if rest:
        lines.append(Line(Role("rc_vat_nondeductible"), Cents(rest)))
    private = sum((g.tax - g.input_vat) - _share(g.tax - g.input_vat, g.cost.pct) for g in groups)
    if private:
        lines.append(Line(Role("private_withdrawal"), Cents(private)))
        lines.append(Line(Role("vat_paid_private"), Cents(-private), income_date=booking))
    return lines


def expense_bill_entry(expense: Expense, bases: Bases, first_open_day: date) -> Entry | None:
    """The bill entry (``expense:<id>:bill:r<n>``); ``None`` when nothing is posted (EÜR ``no_input_vat``, all zero)."""
    eur = bases.income == "eur"
    booking = max(expense.date, first_open_day)
    key = f"expense:{expense.id}:bill:r{expense.bill_rev}"
    income = _income_date(expense.date, booking, first_open_day)
    lines: list[Line] = []
    if expense.treatment in _RC:
        groups = _rc_groups(expense)
        lines.extend(_rc_tax_lines(expense, groups, tax_date_13b(expense)))
        if eur:
            lines.extend(_rc_bill_eur_lines(groups, booking))
        else:
            lines.extend(_cost_lines([g.cost for g in groups], income))
            lines.append(Line(Role("payables"), Cents(-sum(g.net for g in groups)), open_item_id=expense.id))
        return _entry(lines, booking, expense.date, key)

    split = _standard_split(expense)
    vat_date = expense.date if expense.date >= first_open_day else booking
    input_lines = _input_vat_lines(split, vat_date)
    if eur:
        if not input_lines:
            return None
        lines.extend(input_lines)
        lines.append(Line(Role("input_vat_clearing"), Cents(-sum(ln.amount_cents for ln in input_lines))))
    else:
        lines.extend(_cost_lines(split.costs, income))
        lines.extend(input_lines)
        lines.append(Line(Role("payables"), Cents(-split.gross), open_item_id=expense.id))
    return _entry(lines, booking, expense.date, key)


def expense_pay_entry(expense: Expense, bases: Bases, first_open_day: date) -> Entry | None:
    """The payment entry (``expense:<id>:pay:r<n>``); ``None`` for an unpaid expense under accrual."""
    eur = bases.income == "eur"
    paid = expense.paid_on or (expense.date if eur else None)
    if paid is None:
        return None
    booking = max(paid, first_open_day)
    income = _income_date(paid, booking, first_open_day)
    key = f"expense:{expense.id}:pay:r{expense.pay_rev}"
    lines: list[Line] = []
    if expense.treatment in _RC:
        groups = _rc_groups(expense)
        net = Cents(sum(g.net for g in groups))
        if eur:
            # the non-deductible tax was booked on the bill entry, so the cost here is the net only
            lines.extend(_cost_lines([_Cost(g.cost.role, g.cost.meals, g.net, g.cost.pct) for g in groups], income))
        else:
            lines.append(Line(Role("payables"), net, open_item_id=expense.id))
        lines.append(Line(Role("bank"), Cents(-net)))
        return _entry(lines, booking, paid, key)

    split = _standard_split(expense)
    if eur:
        lines.extend(_cost_lines(split.costs, income))
        clearing = sum(split.iv_by_rate.values())
        if clearing:
            lines.append(Line(Role("input_vat_clearing"), Cents(clearing), income_date=income))
    else:
        lines.append(Line(Role("payables"), split.gross, open_item_id=expense.id))
    if lines:
        lines.append(Line(Role("bank"), Cents(-split.gross)))
    return _entry(lines, booking, paid, key)
