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
    tax_provision_settlement_entry,
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
    r = trip_reversal_entry(e, first_open_day=date(2026, 1, 1), year_closed=False)
    assert _lines(r) == [("travel_costs", -5600, "2026-05-08"), ("private_deposit", 5600, None)]
    assert r.event_key == "trip:t:r1:rev"
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
    assert c is not None
    assert c.booking_date == date(2027, 2, 12) and c.document_date == date(2027, 2, 10)
    assert c.event_key == "vat:s1c" and p.event_key == "vat:s1"
    _check(p)
    _check(c)
    assert special_prepayment_credit_entry("z", date(2027, 2, 10), Cents(0)) is None


def test_trade_tax_provision_and_payment_from_it() -> None:
    p = tax_provision_entry("trade_tax", "g", date(2026, 12, 31), Cents(120000))
    assert _lines(p) == [("trade_tax_expense", 120000, "2026-12-31"), ("trade_tax_provision", -120000, None)]
    assert p.event_key == "manual:g"
    pay = tax_payment_entry("trade_tax", "h", date(2027, 3, 1), Cents(120000), from_provision=True)
    assert _lines(pay) == [("trade_tax_provision", 120000, None), ("bank", -120000, None)]
    _check(p)
    _check(pay)


def test_corporate_tax() -> None:
    pay = tax_payment_entry("corporate_tax", "c", date(2026, 6, 10), Cents(10000))
    assert _lines(pay) == [("corporate_tax_expense", 10000, "2026-06-10"), ("bank", -10000, None)]
    prov = tax_provision_entry("corporate_tax", "d", date(2026, 12, 31), Cents(5000))
    assert _lines(prov) == [("corporate_tax_expense", 5000, "2026-12-31"), ("corporate_tax_provision", -5000, None)]


def test_refunds() -> None:
    g = tax_refund_entry("trade_tax", "r", date(2026, 9, 1), Cents(15000))
    assert _lines(g) == [("bank", 15000, None), ("tax_refunds", -15000, "2026-09-01")]
    i = tax_refund_entry("income_tax", "r2", date(2026, 9, 1), Cents(7000))
    assert _lines(i) == [("bank", 7000, None), ("private_withdrawal", -7000, None)]
    _check(g)
    _check(i)


def test_event_keys() -> None:
    d = date(2026, 6, 1)
    assert vat_payment_entry("a", d, Cents(1)).event_key == "vat:a"
    assert vat_refund_entry("b", d, Cents(1)).event_key == "vat:b"
    assert special_prepayment_payment_entry("c", d, Cents(1)).event_key == "vat:c"
    credit = special_prepayment_credit_entry("d", d, Cents(1))
    assert credit is not None and credit.event_key == "vat:d"
    assert tax_payment_entry("trade_tax", "e", d, Cents(1)).event_key == "manual:e"
    assert tax_provision_entry("trade_tax", "f", d, Cents(1)).event_key == "manual:f"
    assert tax_refund_entry("trade_tax", "g", d, Cents(1)).event_key == "manual:g"
    assert tax_provision_settlement_entry("trade_tax", "h", Cents(5), Cents(5), d).event_key == "manual:h"
    trip = trip_entry(TripEvent("t", 3, d, Cents(1), Cents(0)), "owner")
    assert trip is not None and trip.event_key == "trip:t:r3"
    rev = trip_reversal_entry(trip, first_open_day=d, year_closed=False)
    assert rev.event_key == "trip:t:r3:rev"


def test_corporate_refund_and_provision_payment() -> None:
    r = tax_refund_entry("corporate_tax", "r", date(2026, 9, 1), Cents(4000))
    assert _lines(r) == [("bank", 4000, None), ("corporate_tax_expense", -4000, "2026-09-01")]
    p = tax_payment_entry("corporate_tax", "p", date(2027, 3, 1), Cents(5000), from_provision=True)
    assert _lines(p) == [("corporate_tax_provision", 5000, None), ("bank", -5000, None)]
    _check(r)
    _check(p)


def test_income_tax_has_no_provision() -> None:
    with pytest.raises(ValueError):
        tax_payment_entry("income_tax", "x", date(2027, 3, 1), Cents(100), from_provision=True)


def test_employee_trip_reversal() -> None:
    e = trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(5600), Cents(0)), "employee")
    assert e is not None
    r = trip_reversal_entry(e, first_open_day=date(2026, 1, 1), year_closed=False)
    assert _lines(r) == [("travel_costs", -5600, "2026-05-08"), ("payables_employee", 5600, None)]
    assert r.booking_date == date(2026, 5, 8)
    _check(r)


def test_reversal_across_closed_year() -> None:
    e = trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(5600), Cents(0)), "owner")
    assert e is not None
    r = trip_reversal_entry(e, first_open_day=date(2027, 2, 1), year_closed=True)
    assert r.booking_date == date(2027, 2, 1)
    assert r.document_date == date(2026, 5, 8)
    assert _lines(r) == [("travel_costs", -5600, "2027-02-01"), ("private_deposit", 5600, None)]
    assert r.event_key == "trip:t:r1:rev"
    r3 = trip_reversal_entry(e, first_open_day=date(2026, 7, 1), year_closed=False)
    assert r3.booking_date == date(2026, 7, 1)
    assert _lines(r3)[0] == ("travel_costs", -5600, "2026-05-08")


def test_booking_date_override_on_payment() -> None:
    p = tax_payment_entry("trade_tax", "a", date(2026, 2, 15), Cents(100), booking_date=date(2026, 3, 1))
    assert p.booking_date == date(2026, 3, 1) and p.document_date == date(2026, 2, 15)
    assert _lines(p)[0] == ("trade_tax_expense", 100, "2026-02-15")
    v = vat_payment_entry("v", date(2026, 2, 15), Cents(100), booking_date=date(2026, 3, 1))
    assert v.booking_date == date(2026, 3, 1)


def _role_sum(e: Entry, role: str) -> int:
    return sum(ln.amount_cents for ln in e.lines if ln.role == role)


def test_provision_settlement_release() -> None:
    s = tax_provision_settlement_entry("trade_tax", "s", Cents(120000), Cents(118000), date(2027, 3, 1))
    assert _lines(s) == [
        ("trade_tax_provision", 120000, None),
        ("bank", -118000, None),
        ("tax_refunds", -2000, "2027-03-01"),
    ]
    prov = tax_provision_entry("trade_tax", "p", date(2026, 12, 31), Cents(120000))
    assert _role_sum(prov, "trade_tax_provision") + _role_sum(s, "trade_tax_provision") == 0
    _check(s)


def test_provision_settlement_extra_expense() -> None:
    s = tax_provision_settlement_entry("trade_tax", "s", Cents(120000), Cents(125000), date(2027, 3, 1))
    assert _lines(s) == [
        ("trade_tax_provision", 120000, None),
        ("bank", -125000, None),
        ("trade_tax_expense", 5000, "2027-03-01"),
    ]
    prov = tax_provision_entry("trade_tax", "p", date(2026, 12, 31), Cents(120000))
    assert _role_sum(prov, "trade_tax_provision") + _role_sum(s, "trade_tax_provision") == 0
    _check(s)


def test_provision_settlement_exact_and_corporate() -> None:
    s = tax_provision_settlement_entry("corporate_tax", "s", Cents(5000), Cents(5000), date(2027, 3, 1))
    assert _lines(s) == [("corporate_tax_provision", 5000, None), ("bank", -5000, None)]
    s2 = tax_provision_settlement_entry("corporate_tax", "s", Cents(5000), Cents(5500), date(2027, 3, 1))
    assert _lines(s2)[2] == ("corporate_tax_expense", 500, "2027-03-01")
    _check(s2)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_rejected(bad: int) -> None:
    d = date(2026, 1, 1)
    c = Cents(bad)
    calls = [
        lambda: vat_payment_entry("x", d, c),
        lambda: vat_refund_entry("x", d, c),
        lambda: special_prepayment_payment_entry("x", d, c),
        lambda: tax_payment_entry("income_tax", "x", d, c),
        lambda: tax_payment_entry("trade_tax", "x", d, c, from_provision=True),
        lambda: tax_provision_entry("trade_tax", "x", d, c),
        lambda: tax_refund_entry("trade_tax", "x", d, c),
        lambda: tax_provision_settlement_entry("trade_tax", "x", c, Cents(5), d),
        lambda: tax_provision_settlement_entry("trade_tax", "x", Cents(5), c, d),
    ]
    if bad < 0:
        calls.append(lambda: special_prepayment_credit_entry("x", d, c))
    for call in calls:
        with pytest.raises(ValueError):
            call()


def test_negative_trip_amounts_rejected() -> None:
    with pytest.raises(ValueError):
        trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(-1), Cents(0)), "owner")
    with pytest.raises(ValueError):
        trip_entry(TripEvent("t", 1, date(2026, 5, 8), Cents(0), Cents(-1)), "owner")
