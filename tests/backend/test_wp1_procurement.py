"""Accepting a capacity offer whose plan names no hour field or both: the booked total comes from the hour lines.
Literal numbers; invented data only."""

from datetime import date

import pytest

import permita38.api.routers.incoming_offers as offers_router
import permita38.core.service.planner.service as planner_module
import permita38.core.service.procurement as procurement_module

TODAY = date(2026, 3, 2)


class _Frozen(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    for mod in (planner_module, offers_router, procurement_module):
        if hasattr(mod, "date"):
            monkeypatch.setattr(mod, "date", _Frozen)


@pytest.fixture
def boss_id(app):
    return next(u["id"] for u in app.state.services.store.list("users") if u["role"] == "boss")


def new_project(c, name):
    cu = c.post("/api/customers", json={"name": f"{name} Client (Demo)"})
    assert cu.status_code == 200, cu.text
    r = c.post("/api/projects", json={"name": name, "customer_id": cu.json()["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def supplier(c):
    r = c.post(
        "/api/suppliers",
        json={
            "name": "Example Freelance Studio (Demo)",
            "kind": "person-capacity",
            "workdays": [0, 1, 2, 3],
            "weekly_hours": 24,
            "rate": 80.0,
            "country": "DE",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


SUM_MSG = "The offer's hour lines must add up to more than 0 and at most 100000 h"


def offer(c, sid, pid, lines, plan_extra):
    body = {
        "supplier_id": sid,
        "reference": "ANG-2026-201",
        "received_on": "2026-02-20",
        "valid_until": "2026-03-31",
        "lines": lines,
        "plan": {"start": "2026-03-02", "end": "2026-03-27", **plan_extra},
        "project_id": pid,
    }
    r = c.post("/api/incoming-offers", json=body)
    assert r.status_code == 201, r.text
    return c.post(f"/api/incoming-offers/{r.json()['id']}/accept", json={})


def setup(c):
    return supplier(c)["id"], new_project(c, "Demo Build")["id"]


def hours(q, p=80.0):
    return {"quantity": q, "unit": "h", "unit_price": p}


def test_no_hour_field_takes_the_total_from_the_lines(boss_client):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, [hours(40), hours(20.125)], {})
    assert a.status_code == 200, a.text
    alloc = a.json()["allocation"]
    assert alloc["total_hours"] == 60.13 and not alloc.get("hours_per_week")


def test_both_fields_take_the_total_from_the_lines(boss_client):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, [hours(50)], {"hours_per_week": 10, "total_hours": 30})
    assert a.status_code == 200, a.text
    alloc = a.json()["allocation"]
    assert alloc["total_hours"] == 50 and not alloc.get("hours_per_week")


def test_hours_per_week_only_books_the_hour_lines(boss_client):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, [hours(80)], {"hours_per_week": 20})
    assert a.status_code == 200, a.text
    alloc = a.json()["allocation"]
    assert alloc["total_hours"] == 80 and not alloc.get("hours_per_week")


def test_total_hours_only_books_the_hour_lines(boss_client):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, [hours(80)], {"total_hours": 40})
    assert a.status_code == 200, a.text
    alloc = a.json()["allocation"]
    assert alloc["total_hours"] == 80 and not alloc.get("hours_per_week")


def test_e6_24_hours_at_90_book_a_total_of_24_and_planned_net_2160(boss_client):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, [hours(24, 90.0)], {"hours_per_week": 8})
    assert a.status_code == 200, a.text
    alloc = a.json()["allocation"]
    assert alloc["total_hours"] == 24 and alloc["planned_net"] == 2160.00


@pytest.mark.parametrize(
    "lines,message",
    [
        ([hours(0)], SUM_MSG),
        ([hours(100000.01, 1.0)], SUM_MSG),
        ([hours(10, 10001.0)], "The hourly rate of the offer is above 10000"),
    ],
)
def test_out_of_range_sums_and_rates_are_refused(boss_client, lines, message):
    sid, pid = setup(boss_client)
    a = offer(boss_client, sid, pid, lines, {})
    assert a.status_code == 422, a.text
    assert a.json()["detail"] == message
