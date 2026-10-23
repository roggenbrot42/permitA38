"""Exact-cent money arithmetic: rounding rule table, API behaviour and a property test against an independent
implementation (exact rational numbers, no Decimal and no floats)."""

from decimal import Decimal
from fractions import Fraction
from math import floor

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core import calc
from permita38.core.money import dec, hours_from_minutes, line_amount, q2, split_gross, to_float


def half_up_cents(x: Fraction) -> Fraction:
    """Round a rational number to whole cents, ties away from zero - written independently of money.py."""
    cents = x * 100
    sign = -1 if cents < 0 else 1
    cents = abs(cents)
    r = floor(cents + Fraction(1, 2))
    return Fraction(sign * r, 100)


def reference_totals(lines, default_vat, klein=False):
    """lines: [(qty_str, price_str, vat_str|None)]; returns (net, {rate: vat}, vat_total, gross) as Fractions."""
    groups: dict[Fraction, Fraction] = {}
    net = Fraction(0)
    for qty, price, vat in lines:
        amount = half_up_cents(Fraction(qty) * Fraction(price))
        net += amount
        rate = Fraction(0) if klein else Fraction(vat if vat is not None else default_vat)
        groups[rate] = groups.get(rate, Fraction(0)) + amount
    vats = {r: half_up_cents(a * r / 100) for r, a in groups.items()}
    total = sum(vats.values(), Fraction(0))
    return net, vats, total, net + total


def api_totals(lines, default_vat, klein=False):
    doc = {
        "vat_rate": default_vat,
        "lines": [
            {"qty": float(q), "price": float(p), **({"vat": float(v)} if v is not None else {})} for q, p, v in lines
        ],
    }
    return calc.doc_totals(doc, {"kleinunternehmer": klein})


# ------------------------------------------------------------------ the rounding rule on tricky cases
@pytest.mark.parametrize(
    "qty,price,expected",
    [
        ("1", "621.50", "621.50"),
        ("1.5", "0.01", "0.02"),  # 0.015 -> 0.02 (half up; binary floats give 0.01)
        ("1", "0.005", "0.01"),
        ("-1", "0.005", "-0.01"),  # ties away from zero
        ("3", "0.335", "1.01"),
        ("0.1", "0.1", "0.01"),
        ("2.675", "1", "2.68"),  # classic float trap (round(2.675, 2) == 2.67)
        ("1.005", "1", "1.01"),
        ("100", "19.995", "1999.50"),
        ("33.333", "3", "100.00"),
    ],
)
def test_line_amount_half_up(qty, price, expected):
    assert str(line_amount(qty, price)) == expected


def test_vat_on_621_50_at_19_percent_is_118_09():
    t = calc.doc_totals({"vat_rate": 19, "lines": [{"qty": 1, "price": 621.50}]}, {})
    assert t["net"] == 621.50
    assert t["vat"] == {19.0: 118.09}
    assert t["vat_total"] == 118.09
    assert t["gross"] == 739.59


def test_vat_is_computed_per_rate_group_not_per_line():
    """Ten lines of 0.05 EUR net at 19 %: per-line rounding would give 10 x 0.01 = 0.10; per group 0.50*19% = 0.095 -> 0.10.
    Lines of 0.03: per line 0.0057 -> 0.01 each (0.10 total) vs per group 0.30*19% = 0.057 -> 0.06."""
    lines = {"vat_rate": 19, "lines": [{"qty": 1, "price": 0.03} for _ in range(10)]}
    t = calc.doc_totals(lines, {})
    assert t["net"] == 0.30
    assert t["vat_total"] == 0.06


def test_mixed_rates_credit_lines_and_klein():
    doc = {
        "vat_rate": 19,
        "lines": [
            {"qty": 10, "price": 95.5},  # 955.00 @19
            {"qty": 1, "price": 120, "vat": 7},  # 120.00 @7
            {"qty": 1, "price": -33.33},  # credit note line @19
        ],
    }
    t = calc.doc_totals(doc, {})
    assert t["net"] == 1041.67
    assert t["vat"] == {19.0: 175.12, 7.0: 8.4}  # 921.67 * 19 % = 175.1173 -> 175.12
    assert t["gross"] == round(t["net"] + t["vat_total"], 2)
    k = calc.doc_totals(doc, {"kleinunternehmer": True})
    assert k["vat_total"] == 0 and k["gross"] == k["net"] == 1041.67


def test_hours_from_minutes_half_up():
    assert str(hours_from_minutes(100)) == "1.67"
    assert str(hours_from_minutes(7.5)) == "0.13"  # 0.125 -> 0.13
    assert str(hours_from_minutes(3)) == "0.05"


def test_dec_is_tolerant_but_strict_on_request():
    assert dec("abc") == 0 and dec(float("nan")) == 0 and dec(float("inf")) == 0 and dec(None) == 0 and dec("") == 0
    assert dec(10**400) == 0  # absurd values never reach the arithmetic
    with pytest.raises(ValueError):
        dec("abc", default=None)
    assert to_float(q2(dec("1.005"))) == 1.01


def test_many_small_lines_do_not_drift():
    t = calc.doc_totals({"vat_rate": 0, "lines": [{"qty": 1, "price": 0.1} for _ in range(1000)]}, {})
    assert t["net"] == 100.0  # 1000 x 0.1 in binary floats is 99.9999999999986


# ------------------------------------------------------------------ property test against the reference
_qty = st.decimals(min_value="-1000", max_value="1000", places=3, allow_nan=False, allow_infinity=False)
_price = st.decimals(min_value="-5000", max_value="5000", places=3, allow_nan=False, allow_infinity=False)
_vat = st.sampled_from([None, "0", "5", "7", "16", "19"])


@settings(max_examples=400, deadline=None, derandomize=True)
@given(
    lines=st.lists(st.tuples(_qty.map(str), _price.map(str), _vat), min_size=0, max_size=12),
    default_vat=st.sampled_from(["0", "7", "19"]),
    klein=st.booleans(),
)
def test_doc_totals_equal_independent_reference(lines, default_vat, klein):
    net, vats, total, gross = reference_totals(lines, default_vat, klein)
    got = api_totals(lines, float(default_vat), klein)
    assert Fraction(str(got["net"])) == net
    assert Fraction(str(got["vat_total"])) == total
    assert Fraction(str(got["gross"])) == gross
    assert {Fraction(str(r)): Fraction(str(a)) for r, a in got["vat"].items()} == {r: a for r, a in vats.items()}


@settings(max_examples=200, deadline=None, derandomize=True)
@given(minutes=st.integers(min_value=0, max_value=100_000))
def test_hours_from_minutes_matches_reference(minutes):
    assert Fraction(str(hours_from_minutes(minutes))) == half_up_cents(Fraction(minutes, 60) / 100 * 100)


# ------------------------------------------------------------------ through the API
def test_api_invoice_totals_and_finance_use_exact_cents(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "ACME GmbH"}).json()
    inv = c.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-03-01", "vat_rate": 19, "lines": [{"qty": 1, "price": 621.5}]},
    ).json()
    assert inv["lines"][0]["amount"] == 621.5
    done = c.post(f"/api/invoices/{inv['id']}/finalize").json()
    done.update(status="paid", paid_date="2026-03-02")
    assert c.put(f"/api/invoices/{inv['id']}", json=done).status_code == 200
    fin = c.get("/api/finance", params={"year": 2026}).json()
    assert fin["totals"]["revenue"] == 621.5
    assert fin["totals"]["vat_out"] == 118.09  # the bug: 118.08


def test_api_from_time_quantity_is_half_up(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Kunde", "rate": 100}).json()
    proj = c.post("/api/projects", json={"name": "P", "customer_id": cust["id"]}).json()
    me = c.get("/api/auth/me").json()
    c.post("/api/entries", json={"user_id": me["id"], "date": "2026-03-02", "minutes": 100, "project_id": proj["id"]})
    inv = c.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    assert inv["lines"][0]["qty"] == 1.67 and inv["lines"][0]["amount"] == 167.0


# ------------------------------------------------------------------ split_gross (largest remainder, itemised bills)
D = Decimal


def _exact(gross, rate):
    return gross * rate / (100 + rate)


def test_split_two_halves_at_7_percent_tie_goes_to_the_later_line():
    assert split_gross([(D("0.50"), D(7)), (D("0.50"), D(7))]) == [(D("0.47"), D("0.03")), (D("0.46"), D("0.04"))]


def test_five_lines_of_004_at_19_percent_never_go_negative():
    out = split_gross([(D("0.04"), D(19))] * 5)
    assert [v for _, v in out] == [D("0.00"), D("0.00"), D("0.01"), D("0.01"), D("0.01")]
    assert sum((v for _, v in out), D(0)) == q2(D("0.20") * 19 / 119) == D("0.03")
    assert all(n >= 0 and v >= 0 for n, v in out)


def test_tiny_line_next_to_a_rounding_up_pair_keeps_non_negative_vat():
    out = split_gross([(D("0.01"), D(7)), (D("2.52"), D(7)), (D("0.00"), D(7))])
    assert out == [(D("0.01"), D("0.00")), (D("2.35"), D("0.17")), (D("0.00"), D("0.00"))]


def test_zero_gross_line_has_zero_vat_and_zero_net():
    out = split_gross([(D("0"), D(19)), (D("6.00"), D(0))])
    assert out == [(D("0"), D("0")), (D("6.00"), D("0"))]


def test_rest_zero_when_every_share_is_exact_or_below_the_group_rounding():
    # one line: group VAT == floor share, nothing to hand out
    assert split_gross([(D("119.00"), D(19))]) == [(D("100.00"), D("19.00"))]


def test_exact_tie_between_unequal_grosses_goes_to_the_later_line():
    # 28.53 and 116.59 at 19 %: both cent remainders are exactly 62/119 (a true tie). Decimal division used to break it
    # by rounding noise; the later line (116.59) must get the extra cent.
    out = split_gross([(D("28.53"), D(19)), (D("116.59"), D(19))])
    assert out == [(D("23.98"), D("4.55")), (D("97.97"), D("18.62"))]


def test_hotel_bill_literals_are_unchanged():
    out = split_gross([(D("160.00"), D(7)), (D("30.00"), D(19)), (D("6.00"), D(0))])
    assert out == [(D("149.53"), D("10.47")), (D("25.21"), D("4.79")), (D("6.00"), D("0.00"))]


def test_extra_cent_goes_to_the_larger_fraction_among_several_lines():
    # group 7: VAT 0.07; floors 0.00 + 0.03 + 0.03 -> one extra cent; the two 0.50 lines tie, the later one gets it
    out = split_gross([(D("0.01"), D(7)), (D("0.50"), D(7)), (D("0.50"), D(7))])
    assert sum((v for _, v in out), D(0)) == q2(D("1.01") * 7 / 107)
    assert all(n >= 0 and v >= 0 for n, v in out)


@settings(max_examples=300, deadline=None)
@given(
    lines=st.lists(
        st.tuples(st.integers(min_value=0, max_value=50_000), st.sampled_from([0, 7, 19])),
        min_size=1,
        max_size=15,
    )
)
def test_split_properties_hold_for_any_lines(lines):
    gl = [(D(g) / 100, D(r)) for g, r in lines]
    out = split_gross(gl)
    for (g, r), (n, v) in zip(gl, out, strict=True):
        assert n >= 0 and v >= 0
        assert n + v == g
        assert abs(v - _exact(g, r)) < D("0.01")
    for r in {r for _, r in gl}:
        group = sum((g for g, rr in gl if rr == r), D(0))
        assert sum((v for (_, v), (_, rr) in zip(out, gl, strict=True) if rr == r), D(0)) == q2(_exact(group, r))
