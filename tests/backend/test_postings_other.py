"""Trip, VAT payment and tax postings (accounting.md 9.5-9.7), A10 from the hand-computed oracle."""

import json
from datetime import date
from pathlib import Path

import pytest

from permita38.core.accounting.postings_other import (
    TripEvent,
    special_prepayment_credit_entry,
    special_prepayment_payment_entry,
    tax_payment_entry,
    tax_provision_entry,
    tax_refund_entry,
    trip_entry,
    trip_reversal_entry,
    vat_payment_entry,
    vat_refund_entry,
)
from permita38.core.accounting.types import Cents, Entry

ORACLE = Path(__file__).parent / "fixtures" / "accounting_oracle.json"


def _lines(e: Entry) -> list[tuple[str, int, str | None]]:
    return [(ln.role, ln.amount_cents, ln.income_date.isoformat() if ln.income_date else None) for ln in e.lines]


def _check(e: Entry) -> None:
    assert e.balance == 0
    for ln in e.lines:
        assert ln.tax_key is None and ln.tax_kind is None and ln.vat_date is None and ln.tax_base_cents is None
        assert isinstance(ln.amount_cents, int)


def test_a10_matches_oracle() -> None:
    case = next(c for c in json.loads(ORACLE.read_text())["journal_cases"] if c["id"] == "A10-taxes-eur")
    trade = tax_payment_entry("trade_tax", "a", date(2026, 2, 15), Cents(case["events"][0]["amount"]))
    income = tax_payment_entry("income_tax", "b", date(2026, 3, 10), Cents(case["events"][1]["amount"]))
    for entry, expected in zip([trade, income], case["entries"], strict=True):
        assert entry.booking_date.isoformat() == expected["booking_date"]
        want = [(ln[0], ln[1], (ln[2] if len(ln) > 2 else {}).get("income_date")) for ln in expected["lines"]]
        assert _lines(entry) == want
        _check(entry)


def test_trip_owner() -> None:
    e = trip_entry(TripEvent("t1", 2, date(2026, 5, 8), Cents(5600), Cents(7125)), "owner")
    assert e is not None
    assert _lines(e) == [("travel_costs", 12725, "2026-05-08"), ("private_deposit", -12725, None)]
    assert e.event_key == "trip:t1:r2"
    assert e.booking_date == date(2026, 5, 8)
    _check(e)


def test_trip_employee() -> None:
    e = trip_entry(TripEvent("t1", 1, date(2026, 5, 8), Cents(5600), Cents(7125)), "employee")
    assert e is not None
    assert _lines(e) == [("travel_costs", 12725, "2026-05-08"), ("payables_employee", -12725, None)]
    _check(e)


def test_trip_zero_and_reversal() -> None:
    assert trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(0), Cents(0)), "owner") is None
    e = trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(5600), Cents(0)), "owner")
    assert e is not None
    r = trip_reversal_entry(e)
    assert _lines(r) == [("travel_costs", -5600, "2026-05-08"), ("private_deposit", 5600, None)]
    assert r.event_key == "trip:t:r1:reversal"
    _check(r)


def test_vat_payment_and_refund() -> None:
    p = vat_payment_entry("v1", date(2026, 4, 10), Cents(58900))
    assert _lines(p) == [("vat_prepayments", 58900, "2026-04-10"), ("bank", -58900, None)]
    r = vat_refund_entry("v2", date(2026, 5, 12), Cents(20500))
    assert _lines(r) == [("bank", 20500, None), ("vat_prepayments", -20500, "2026-05-12")]
    _check(p)
    _check(r)


def test_special_prepayment() -> None:
    p = special_prepayment_payment_entry("s1", date(2026, 2, 10), Cents(60000))
    assert _lines(p) == [("vat_special_prepayment", 60000, "2026-02-10"), ("bank", -60000, None)]
    c = special_prepayment_credit_entry("s1c", date(2027, 2, 10), Cents(60000), booking_date=date(2027, 2, 12))
    assert _lines(c) == [("vat_prepayments", 60000, None), ("vat_special_prepayment", -60000, None)]
    assert c.booking_date == date(2027, 2, 12) and c.document_date == date(2027, 2, 10)
    _check(p)
    _check(c)


def test_trade_tax_provision_and_payment_from_it() -> None:
    p = tax_provision_entry("trade_tax", "g", date(2026, 12, 31), Cents(120000))
    assert _lines(p) == [("trade_tax_expense", 120000, None), ("trade_tax_provision", -120000, None)]
    assert p.event_key == "manual:g"
    pay = tax_payment_entry("trade_tax", "h", date(2027, 3, 1), Cents(120000), from_provision=True)
    assert _lines(pay) == [("trade_tax_provision", 120000, None), ("bank", -120000, None)]
    _check(p)
    _check(pay)


def test_corporate_tax() -> None:
    pay = tax_payment_entry("corporate_tax", "c", date(2026, 6, 10), Cents(10000))
    assert _lines(pay) == [("corporate_tax_expense", 10000, "2026-06-10"), ("bank", -10000, None)]
    prov = tax_provision_entry("corporate_tax", "d", date(2026, 12, 31), Cents(5000))
    assert _lines(prov) == [("corporate_tax_expense", 5000, None), ("corporate_tax_provision", -5000, None)]


def test_refunds() -> None:
    g = tax_refund_entry("trade_tax", "r", date(2026, 9, 1), Cents(15000))
    assert _lines(g) == [("bank", 15000, None), ("tax_refunds", -15000, "2026-09-01")]
    i = tax_refund_entry("income_tax", "r2", date(2026, 9, 1), Cents(7000))
    assert _lines(i) == [("bank", 7000, None), ("private_withdrawal", -7000, None)]
    _check(g)
    _check(i)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        vat_payment_entry("x", date(2026, 1, 1), Cents(bad))
    with pytest.raises(ValueError):
        tax_payment_entry("income_tax", "x", date(2026, 1, 1), Cents(bad))
