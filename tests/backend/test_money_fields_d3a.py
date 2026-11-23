"""D3a: expense, trip and incoming-offer responses carry decimal-string money (decimal-money-api.md rules 1 and 3);
percent, hours and km stay numbers. Fake data only."""

import re

from .test_procurement import (
    capacity_supplier,
    offer_w1,
    post_offer,
    project,  # noqa: F401
)
from .test_procurement import frozen_today as _frozen  # noqa: F401
from .test_trips_api import trip_body

DEC = re.compile(r"^-?[0-9]+\.[0-9]{2,6}$")


def _dec(value, want):
    assert isinstance(value, str) and DEC.match(value), value
    assert value == want, (value, want)


def _num(value):
    assert isinstance(value, (int, float)) and not isinstance(value, bool), value


def _expense_checks(e):
    by = {x["description"]: x for x in e["lines"]}
    room = by["Room"]
    _dec(room["gross"], "160.00")
    _dec(room["vat"], "10.47")
    _dec(room["net"], "149.53")
    _dec(by["City tax"]["vat"], "0.00")
    _num(room["vat_rate"])
    _num(room["deductible"])
    assert isinstance(e["net"], str) and isinstance(e["vat"], str)
    _num(e["deductible"])


def test_itemised_expense_money_strings_and_round_trip(boss_client):
    c = boss_client
    lines = [
        {"description": "Room", "gross": 160.00, "vat_rate": 7},
        {"description": "City tax", "gross": 6.00, "vat_rate": 0},
    ]
    r = c.post("/api/expenses", json={"date": "2026-03-10", "net": 0, "lines": lines, "vendor": "Hotel Beispiel"})
    assert r.status_code in (200, 201), r.text
    made = r.json()
    # 7 %: 160.00 gross -> vat 10.47 (hand: 160 * 7 / 107 = 10.4673), net 149.53; 0 %: net 6.00
    _dec(made["net"], "155.53")
    _dec(made["vat"], "10.47")
    _expense_checks(made)
    got = c.get(f"/api/expenses/{made['id']}").json()
    _expense_checks(got)
    assert got["net"] == made["net"] and got["lines"] == made["lines"]
    assert all(isinstance(x["net"], str) for x in c.get("/api/expenses").json())
    # a PUT of the returned record round-trips
    body = {k: v for k, v in made.items() if k in ("date", "net", "vat", "vendor", "vat_rate", "deductible")}
    body["lines"] = [{k: v for k, v in ln.items() if k in ("description", "gross", "vat_rate")} for ln in made["lines"]]
    u = c.put(f"/api/expenses/{made['id']}", json=body)
    assert u.status_code == 200, u.text
    _expense_checks(u.json())
    assert u.json()["net"] == made["net"] and u.json()["lines"] == made["lines"]


def test_plain_expense_money_strings_and_whole_record_put(boss_client):
    c = boss_client
    r = c.post(
        "/api/expenses", json={"date": "2026-03-10", "net": 100, "vat": 19, "vat_rate": 19, "category": "Material"}
    )
    made = r.json()
    _dec(made["net"], "100.00")
    _dec(made["vat"], "19.00")
    _num(made["vat_rate"])
    # the returned strings go straight back in (decimal strings are accepted on input)
    body = {k: made[k] for k in ("date", "net", "vat", "vat_rate", "category")}
    u = c.put(f"/api/expenses/{made['id']}", json=body)
    assert u.status_code == 200, u.text
    _dec(u.json()["net"], "100.00")
    _dec(u.json()["vat"], "19.00")


def _trip_checks(t):
    _dec(t["allowance_total"], "56.00")
    _dec(t["mileage_total"], "71.25")
    _num(t["mileage_km"])
    assert t["mileage_km"] == 237.5
    assert len(t["days"]) == 3
    for d in t["days"]:
        assert isinstance(d["base"], str) and isinstance(d["amount"], str) and DEC.match(d["amount"])
        _num(d["absence_hours"])
    _dec(t["days"][0]["base"], "14.00")


def test_trip_money_strings_get_create_update_preview(boss_client):
    c = boss_client
    mileage = [{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}]
    item = {"id": "", "date": "2026-11-09", "category": "hotel", "text": "Hotel", "expected": 120}
    body = trip_body(mileage=mileage, items=[item])
    pv = c.post("/api/trips/preview", json=body)
    assert pv.status_code == 200, pv.text
    _trip_checks(pv.json())
    _dec(pv.json()["mileage_rate"], "0.30")

    made = c.post("/api/trips", json=body).json()
    _trip_checks(made)
    _dec(made["items"][0]["expected"], "120.00")
    _dec(made["items_expected_total"], "120.00")
    # a linked bill: net 100, vat 19 -> bills and the gross actual total
    c.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 100, "vat": 19, "vat_rate": 19, "category": "Travel", "trip_id": made["id"]},
    )
    got = c.get(f"/api/trips/{made['id']}").json()
    _trip_checks(got)
    assert got["bills"]["count"] == 1
    _dec(got["bills"]["net"], "100.00")
    _dec(got["bills"]["vat"], "19.00")
    _dec(got["items_actual_total"], "119.00")
    _dec(got["items_expected_total"], "120.00")
    assert all(isinstance(r["sum"], str) and DEC.match(r["sum"]) for r in got["checklist"])
    listed = [t for t in c.get("/api/trips").json() if t["id"] == made["id"]][0]
    _trip_checks(listed)

    u = c.put(f"/api/trips/{made['id']}", json={**body, "items": got["items"]})
    assert u.status_code == 200, u.text
    _trip_checks(u.json())
    _dec(u.json()["items"][0]["expected"], "120.00")


def test_incoming_offer_money_strings(boss_client, project):  # noqa: F811
    c = boss_client
    s = capacity_supplier(c)
    made = post_offer(c, offer_w1(s["id"], project["id"]))
    for o in (made, c.get(f"/api/incoming-offers/{made['id']}").json(), c.get("/api/incoming-offers").json()[0]):
        _dec(o["net_total"], "6400.00")
        _dec(o["vat"], "1216.00")
        _dec(o["gross_total"], "7616.00")
        _num(o["vat_rate"])
        _num(o["hours"])
    body = offer_w1(s["id"], project["id"], title="Frontend capacity, spring II")
    u = c.put(f"/api/incoming-offers/{made['id']}", json=body)
    assert u.status_code == 200, u.text
    _dec(u.json()["net_total"], "6400.00")
    _dec(u.json()["gross_total"], "7616.00")


def test_incoming_offer_line_unit_price_is_a_decimal_string_everywhere(boss_client, project):  # noqa: F811
    c = boss_client
    s = capacity_supplier(c)
    made = post_offer(c, offer_w1(s["id"], project["id"]))
    oid = made["id"]
    seen = [made, c.get(f"/api/incoming-offers/{oid}").json(), c.get("/api/incoming-offers").json()[0]]
    u = c.put(f"/api/incoming-offers/{oid}", json=offer_w1(s["id"], project["id"], title="Frontend capacity II"))
    assert u.status_code == 200, u.text
    seen.append(u.json())
    r = c.post(f"/api/incoming-offers/{oid}/review")
    assert r.status_code == 200, r.text
    seen.append(r.json())
    for o in seen:
        assert o["lines"], o
        for line in o["lines"]:
            assert isinstance(line["unit_price"], str) and DEC.match(line["unit_price"]), line
            _num(line["quantity"])
        _dec(o["lines"][0]["unit_price"], "80.00")
    acc = c.post(f"/api/incoming-offers/{oid}/accept", json={})
    assert acc.status_code == 200, acc.text
    _dec(acc.json()["offer"]["lines"][0]["unit_price"], "80.00")
