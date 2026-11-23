"""Decimal-string money (docs/specs/decimal-money-api.md rules 1 and 2): the string path of a request refuses exactly
what the equal JSON number is refused, and sub-cent unit prices survive a POST -> GET -> PUT round trip. Fake data."""

import pytest

from .test_procurement import (
    capacity_supplier,
    offer_w1,
    post_offer,
    project,  # noqa: F401
)
from .test_procurement import frozen_today as _frozen  # noqa: F401
from .test_trips_api import trip_body

DAY = "2026-03-10"


def _expense_net(c, v):
    return c.post("/api/expenses", json={"date": DAY, "net": v})


def _expense_vat(c, v):
    return c.post("/api/expenses", json={"date": DAY, "net": 1, "vat": v})


def _expense_line_gross(c, v):
    return c.post("/api/expenses", json={"date": DAY, "net": 0, "lines": [{"gross": v, "vat_rate": 19}]})


def _trip_expected(c, v):
    items = [{"date": "2026-11-09", "category": "other", "text": "t", "expected": v}]
    return c.post("/api/trips", json=trip_body(items=items))


def _customer_rate(c, v):
    return c.post("/api/customers", json={"name": "Demo Client", "rate": v})


# field -> (sender, is the sub-cent amount refused?). Refused: ExpenseLineIn.gross and TripItemIn.expected carry a
# whole-cent check. Accepted and stored as sent (check-db reports money.sub_cent): expense net/vat; a rate is a
# UnitPrice, sub-cent is legitimate. The string path must give exactly the outcome of the equal JSON number.
FIELDS = {
    "expense.net": (_expense_net, False),
    "expense.vat": (_expense_vat, False),
    "expense.lines.gross": (_expense_line_gross, True),
    "trip.items.expected": (_trip_expected, True),
    "customer.rate": (_customer_rate, False),
}


@pytest.mark.parametrize("field", FIELDS)
def test_sub_cent_string_gets_the_outcome_of_the_equal_number(boss_client, field):
    send, refused = FIELDS[field]
    by_number = send(boss_client, 12.345)
    by_string = send(boss_client, "12.345")
    assert by_number.status_code == by_string.status_code == (422 if refused else 200), (
        by_number.text,
        by_string.text,
    )
    if refused:
        assert by_number.json()["detail"][0]["msg"] == by_string.json()["detail"][0]["msg"]
        assert "cents" in by_string.json()["detail"][0]["msg"]


@pytest.mark.parametrize("field", FIELDS)
def test_comma_decimal_string_is_refused(boss_client, field):
    send, _ = FIELDS[field]
    r = send(boss_client, "12,5")
    assert r.status_code == 422, r.text
    assert "decimal string" in r.json()["detail"][0]["msg"]


@pytest.mark.parametrize("field", FIELDS)
def test_whole_cent_string_equals_the_number_form(boss_client, field):
    send, _ = FIELDS[field]
    by_number, by_string = send(boss_client, 12.5), send(boss_client, "12.50")
    assert by_number.status_code == by_string.status_code == 200, (by_number.text, by_string.text)

    def strip(x):  # ids and timestamps differ between two creations
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in ("id", "created", "updated", "customer_no")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x

    a, b = strip(by_number.json()), strip(by_string.json())
    if field == "trip.items.expected":  # trip ids and item ids are generated
        for t in (a, b):
            t.pop("number", None)
    assert a == b


def test_invoice_line_sub_cent_price_round_trip(boss_client):
    c = boss_client
    cu = c.post("/api/customers", json={"name": "Demo Client"}).json()
    lines = [{"description": "Unit", "qty": 3, "price": 0.125, "vat": 19}]
    made = c.post("/api/invoices", json={"customer_id": cu["id"], "lines": lines})
    assert made.status_code == 200, made.text
    iid = made.json()["id"]
    first = c.get(f"/api/invoices/{iid}").json()
    (ln,) = first["lines"]
    # hand: 3 x 0.125 = 0.375 -> 0.38 half-up; the unit price keeps its third decimal
    assert (ln["price"], ln["amount"]) == ("0.125", "0.38")
    assert made.json()["lines"][0]["price"] == "0.125"

    # PUT the strings the API sent back: the record is unchanged
    body = {"customer_id": cu["id"], "lines": first["lines"]}
    put = c.put(f"/api/invoices/{iid}", json=body)
    assert put.status_code == 200, put.text
    again = c.get(f"/api/invoices/{iid}").json()
    drop = ("updated",)
    assert {k: v for k, v in again.items() if k not in drop} == {k: v for k, v in first.items() if k not in drop}
    assert again["lines"][0]["price"] == "0.125" and again["lines"][0]["amount"] == "0.38"

    # a price sent as a string is the same record
    by_string = c.post(
        "/api/invoices", json={"customer_id": cu["id"], "lines": [{**lines[0], "price": "0.125"}]}
    ).json()
    assert by_string["lines"][0]["price"] == "0.125" and by_string["lines"][0]["amount"] == "0.38"


def test_incoming_offer_sub_cent_unit_price_round_trip(boss_client, project):  # noqa: F811
    c = boss_client
    s = capacity_supplier(c)
    line = {"description": "Frontend development", "quantity": 3, "unit": "flat", "unit_price": 0.125}
    made = post_offer(c, offer_w1(s["id"], project["id"], lines=[line], plan=None))
    oid = made["id"]
    got = c.get(f"/api/incoming-offers/{oid}").json()
    assert got["lines"][0]["unit_price"] == "0.125"
    # hand: q2(3 x 0.125) = 0.38 net, 19 % VAT = q2(0.0722) = 0.07, gross 0.45
    assert (got["net_total"], got["vat"], got["gross_total"]) == ("0.38", "0.07", "0.45")

    body = offer_w1(s["id"], project["id"], lines=got["lines"], plan=None)
    put = c.put(f"/api/incoming-offers/{oid}", json=body)
    assert put.status_code == 200, put.text
    again = c.get(f"/api/incoming-offers/{oid}").json()
    drop = ("updated",)
    assert {k: v for k, v in again.items() if k not in drop} == {k: v for k, v in got.items() if k not in drop}
    assert again["lines"][0]["unit_price"] == "0.125"
