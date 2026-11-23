"""Expense postings (accounting.md 9.4) against the hand-computed oracle ``fixtures/accounting_oracle.json``."""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

import pytest

from permita38.core.accounting.postings_expense import (
    Expense,
    ExpenseLine,
    ExpenseTreatment,
    expense_bill_entry,
    expense_pay_entry,
    tax_date_13b,
)
from permita38.core.accounting.types import Bases, Cents, Entry

ORACLE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text(encoding="utf-8")
)
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in ORACLE["journal_cases"]}
FIXTURE_CASES = [
    "A5-rc-eu-100",
    "A5-rc-eu-50",
    "A5-rc-3c",
    "A14-rc-eu-small-business-eur",
    "A6-meal",
    "A15-late-bill-after-lock",
    "A17-rc-eu-private-share-eur",
]
FIRST_DAY = date(2026, 1, 1)


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _expense(event: dict[str, Any], *, small_business: bool = False) -> Expense:
    return Expense(
        id=event["id"],
        date=date.fromisoformat(event["date"]),
        treatment=event["treatment"],
        lines=tuple(
            ExpenseLine(
                net=Cents(ln["net"]),
                vat=Cents(ln["vat"]),
                rate=Decimal(ln["rate"]),
                pct=Decimal(ln["pct"]),
                vat_pct=Decimal(ln["vat_pct"]),
                category=ln["category"],
            )
            for ln in event["lines"]
        ),
        paid_on=_d(event.get("paid_on")),
        service_date=_d(event.get("service_date")),
        small_business=small_business,
    )


def _first_open_day(case: dict[str, Any]) -> date:
    locked = case.get("locked_months")
    if not locked:
        return FIRST_DAY
    year, month = (int(p) for p in sorted(locked)[-1].split("-"))
    return date(year + (month == 12), month % 12 + 1, 1)


def _bases(case: dict[str, Any]) -> Bases:
    return Bases(case["bases"]["income_basis"], case["bases"]["vat_basis"])


def _rows(entry: Entry) -> list[tuple[Any, ...]]:
    """Every line field the oracle states, in order (``tax_rate`` is not part of the oracle)."""
    return [
        (
            ln.role,
            ln.amount_cents,
            ln.tax_key,
            ln.tax_kind,
            ln.tax_base_cents,
            ln.vat_date.isoformat() if ln.vat_date else None,
            ln.income_date.isoformat() if ln.income_date else None,
            ln.open_item_id,
        )
        for ln in entry.lines
    ]


def _oracle_rows(expected: dict[str, Any], expense_id: str) -> list[tuple[Any, ...]]:
    out = []
    for row in expected["lines"]:
        opts = row[2] if len(row) > 2 else {}
        out.append(
            (
                row[0],
                row[1],
                opts.get("tax_key"),
                opts.get("tax_kind"),
                opts.get("tax_base"),
                opts.get("vat_date"),
                opts.get("income_date"),
                expense_id if row[0] == "payables" else None,
            )
        )
    return out


def _vat_sums(entries: list[Entry]) -> dict[str, dict[str, dict[str, int]]]:
    """Booked VAT per month and tax key, as the oracle states it (output positive, input positive)."""
    out: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for entry in entries:
        for ln in entry.lines:
            if ln.tax_key is None or ln.tax_kind is None or ln.vat_date is None:
                continue
            cell = out[ln.vat_date.strftime("%Y-%m")][ln.tax_key]
            if ln.tax_kind == "tax":
                cell["base"] = cell.get("base", 0) + (ln.tax_base_cents or 0)
                cell["tax"] = cell.get("tax", 0) - ln.amount_cents
            elif ln.tax_kind == "input":
                cell["input"] = cell.get("input", 0) + ln.amount_cents
    return {m: {k: dict(v) for k, v in keys.items()} for m, keys in out.items()}


def _run(case: dict[str, Any]) -> list[Entry]:
    event = case["events"][0]
    expense = _expense(event, small_business=bool(case["bases"].get("small_business")))
    bases, first = _bases(case), _first_open_day(case)
    entries = [expense_bill_entry(expense, bases, first), expense_pay_entry(expense, bases, first)]
    return [e for e in entries if e is not None]


@pytest.mark.parametrize("case_id", FIXTURE_CASES)
def test_expense_entries_match_the_oracle(case_id: str) -> None:
    case = CASES[case_id]
    entries = _run(case)
    expected = case["entries"]
    assert len(entries) == len(expected)
    expense_id = case["events"][0]["id"]
    for entry, exp in zip(entries, expected, strict=True):
        assert _rows(entry) == _oracle_rows(exp, expense_id)
        assert entry.booking_date.isoformat() == exp["booking_date"]
        assert entry.document_date.isoformat() == exp.get("document_date", exp["booking_date"])
        assert entry.balance == 0
    assert _vat_sums(entries) == case["vat"]


def test_event_keys_and_revisions() -> None:
    case = CASES["A17-rc-eu-private-share-eur"]
    expense = _expense(case["events"][0])
    bases, first = _bases(case), FIRST_DAY
    bill = expense_bill_entry(expense, bases, first)
    pay = expense_pay_entry(expense, bases, first)
    assert bill is not None and pay is not None
    assert (bill.event_key, pay.event_key) == ("expense:x17:bill:r1", "expense:x17:pay:r1")
    again = Expense(**{**expense.__dict__, "bill_rev": 2, "pay_rev": 3})
    bill2 = expense_bill_entry(again, bases, first)
    pay2 = expense_pay_entry(again, bases, first)
    assert bill2 is not None and pay2 is not None
    assert (bill2.event_key, pay2.event_key) == ("expense:x17:bill:r2", "expense:x17:pay:r3")


def test_accrual_unpaid_has_no_payment_entry_but_eur_assumes_paid_on_the_date() -> None:
    accrual = CASES["A6-meal"]
    expense = _expense(accrual["events"][0])
    assert expense.paid_on is None
    assert expense_pay_entry(expense, _bases(accrual), FIRST_DAY) is None
    eur = Bases("eur", "ist")
    pay = expense_pay_entry(expense, eur, FIRST_DAY)
    assert pay is not None
    assert pay.document_date == pay.booking_date == date(2026, 2, 10)
    assert pay.event_key == "expense:x6:pay:r1"


def _simple(
    *,
    treatment: ExpenseTreatment = "standard",
    lines: tuple[ExpenseLine, ...],
    day: date = date(2026, 3, 5),
    paid_on: date | None = None,
    small_business: bool = False,
) -> Expense:
    return Expense("e1", day, treatment, lines, paid_on=paid_on, small_business=small_business)


def _line(net: int, vat: int, rate: str, *, pct: str = "100", vp: str = "100", cat: str = "other") -> ExpenseLine:
    return ExpenseLine(Cents(net), Cents(vat), Decimal(rate), Decimal(pct), Decimal(vp), cat)


def _rows_of(entry: Entry | None) -> list[tuple[str, int]]:
    assert entry is not None
    return [(ln.role, ln.amount_cents) for ln in entry.lines]


ACCRUAL = Bases("accrual", "soll")
EUR = Bases("eur", "ist")


def test_itemised_hotel_bill_splits_input_vat_per_rate() -> None:
    # room 160.00 gross @7 %: v = 160 x 7 / 107 = 10.467 -> 10.47, net 149.53
    # parking 30.00 gross @19 %: v = 30 x 19 / 119 = 4.790 -> 4.79, net 25.21; city tax 6.00 @0 %
    exp = _simple(
        lines=(
            _line(14953, 1047, "7", cat="hotel"),
            _line(2521, 479, "19", cat="hotel"),
            _line(600, 0, "0", cat="hotel"),
        )
    )
    bill = expense_bill_entry(exp, ACCRUAL, FIRST_DAY)
    # cost 149.53 + 25.21 + 6.00 = 180.74; payable = 160.00 + 30.00 + 6.00 = 196.00
    assert _rows_of(bill) == [
        ("expense_hotel", 18074),
        ("input_vat_s19", 479),
        ("input_vat_s7", 1047),
        ("payables", -19600),
    ]
    assert bill is not None and bill.balance == 0
    pay = expense_pay_entry(_simple(lines=exp.lines, paid_on=date(2026, 3, 9)), ACCRUAL, FIRST_DAY)
    assert _rows_of(pay) == [("payables", 19600), ("bank", -19600)]


def test_half_private_phone_bill_under_accrual() -> None:
    # net 100.00, VAT 19.00, 50 % income share, VAT fully deductible: iv = 19.00, c = 100.00, cd = 50.00
    full = expense_bill_entry(_simple(lines=(_line(10000, 1900, "19", pct="50", cat="phone"),)), ACCRUAL, FIRST_DAY)
    assert _rows_of(full) == [
        ("expense_phone", 5000),
        ("private_withdrawal", 5000),
        ("input_vat_s19", 1900),
        ("payables", -11900),
    ]
    # VAT share 50 %: iv = 9.50, nd = 9.50, c = 109.50, cd = q2(54.75) = 54.75, private 54.75
    half = expense_bill_entry(
        _simple(lines=(_line(10000, 1900, "19", pct="50", vp="50", cat="phone"),)), ACCRUAL, FIRST_DAY
    )
    assert _rows_of(half) == [
        ("expense_phone", 5475),
        ("private_withdrawal", 5475),
        ("input_vat_s19", 950),
        ("payables", -11900),
    ]
    assert half is not None and half.balance == 0


def test_standard_eur_posts_clearing_on_the_bill_and_cost_on_payment() -> None:
    exp = _simple(lines=(_line(10000, 1900, "19", pct="80", cat="software"),), paid_on=date(2026, 4, 2))
    # iv = 19.00; c = 100.00; cd = 80.00; private 20.00
    bill = expense_bill_entry(exp, EUR, FIRST_DAY)
    assert _rows_of(bill) == [("input_vat_s19", 1900), ("input_vat_clearing", -1900)]
    assert bill is not None
    assert bill.lines[0].vat_date == date(2026, 3, 5) and bill.lines[0].tax_key == "VST"
    pay = expense_pay_entry(exp, EUR, FIRST_DAY)
    assert _rows_of(pay) == [
        ("expense_software", 8000),
        ("private_withdrawal", 2000),
        ("input_vat_clearing", 1900),
        ("bank", -11900),
    ]
    assert pay is not None
    assert [ln.income_date for ln in pay.lines] == [date(2026, 4, 2), None, date(2026, 4, 2), None]
    assert pay.balance == 0 and bill.balance == 0


def test_no_input_vat_and_small_business_take_the_gross_as_cost() -> None:
    lines = (_line(10000, 1900, "19", cat="software"),)
    for exp in (
        _simple(treatment="no_input_vat", lines=lines),
        _simple(lines=lines, small_business=True),
    ):
        assert _rows_of(expense_bill_entry(exp, ACCRUAL, FIRST_DAY)) == [
            ("expense_software", 11900),
            ("payables", -11900),
        ]
        assert expense_bill_entry(exp, EUR, FIRST_DAY) is None  # EÜR: no bill entry
        assert _rows_of(expense_pay_entry(exp, EUR, FIRST_DAY)) == [("expense_software", 11900), ("bank", -11900)]


def test_meals_rest_is_a_nondeductible_business_expense() -> None:
    # gross 107.00 = net 100.00 + 7.00; 70 %: cd = 70.00, rest 30.00 on meals_nondeductible
    bill = expense_bill_entry(_simple(lines=(_line(10000, 700, "7", pct="70", cat="meals"),)), ACCRUAL, FIRST_DAY)
    assert _rows_of(bill) == [
        ("meals_deductible", 7000),
        ("meals_nondeductible", 3000),
        ("input_vat_s7", 700),
        ("payables", -10700),
    ]


def test_input_vat_moves_to_the_booking_date_in_a_locked_month_and_the_year_closes_income() -> None:
    exp = _simple(lines=(_line(10000, 1900, "19", cat="software"),), day=date(2025, 12, 20), paid_on=date(2025, 12, 28))
    first = date(2026, 2, 1)
    bill = expense_bill_entry(exp, ACCRUAL, first)
    assert bill is not None
    assert bill.booking_date == date(2026, 2, 1) and bill.document_date == date(2025, 12, 20)
    assert bill.lines[0].income_date == date(2026, 2, 1)  # closed year: the booking date
    assert bill.lines[1].vat_date == date(2026, 2, 1)
    pay = expense_pay_entry(exp, ACCRUAL, first)
    assert pay is not None and (pay.booking_date, pay.document_date) == (date(2026, 2, 1), date(2025, 12, 28))
    # a locked month inside an open year keeps the document date as income date
    inside = expense_bill_entry(_simple(lines=exp.lines, day=date(2026, 1, 20)), ACCRUAL, first)
    assert inside is not None and inside.lines[0].income_date == date(2026, 1, 20)
    assert inside.lines[1].vat_date == date(2026, 2, 1)


def test_13b_tax_dates() -> None:
    line = (_line(20050, 0, "19", cat="software"),)
    eu = _simple(treatment="reverse_charge_eu_service", lines=line, day=date(2026, 6, 2))
    assert tax_date_13b(eu) == date(2026, 6, 2)  # service_date defaults to the date
    third = _simple(treatment="reverse_charge_third_country", lines=line, day=date(2026, 6, 2))
    assert tax_date_13b(third) == date(2026, 6, 2)  # min(date, 30 Jun)
    dec = Expense("e", date(2027, 3, 1), "reverse_charge_third_country", third.lines, service_date=date(2026, 12, 5))
    assert tax_date_13b(dec) == date(2027, 1, 31)
    # a tax date in a locked month is kept
    locked = expense_bill_entry(
        Expense("e", date(2026, 3, 1), "reverse_charge_eu_service", eu.lines, service_date=date(2026, 1, 15)),
        ACCRUAL,
        date(2026, 3, 1),
    )
    assert locked is not None and locked.lines[0].vat_date == date(2026, 1, 15)


def test_rc_with_two_rates_balances_and_posts_one_tax_line_per_rate() -> None:
    # 19 %: t = 100.00 x 19 % = 19.00; 7 %: t = 50.00 x 7 % = 3.50; payable = 150.00
    exp = _simple(
        treatment="reverse_charge_eu_service",
        lines=(_line(10000, 0, "19", cat="software"), _line(5000, 0, "7", cat="software")),
    )
    bill = expense_bill_entry(exp, ACCRUAL, FIRST_DAY)
    assert _rows_of(bill) == [
        ("rc_vat_out_eu", -1900),
        ("rc_input_vat", 1900),
        ("rc_vat_out_eu", -350),
        ("rc_input_vat", 350),
        ("expense_software", 15000),
        ("payables", -15000),
    ]
    assert bill is not None and bill.balance == 0


def test_treatment_literal_lists_the_four_treatments() -> None:
    assert set(get_args(ExpenseTreatment)) == {
        "standard",
        "no_input_vat",
        "reverse_charge_eu_service",
        "reverse_charge_third_country",
    }


def test_nothing_is_posted_for_all_zero_amounts() -> None:
    exp = _simple(lines=(_line(0, 0, "19"),), paid_on=date(2026, 3, 6))
    assert expense_bill_entry(exp, EUR, FIRST_DAY) is None
    assert expense_pay_entry(exp, EUR, FIRST_DAY) is None
