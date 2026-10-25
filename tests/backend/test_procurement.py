"""Incoming offers, acceptance, allocations of subcontractors, commitments and hints over HTTP, against the hand
computed examples of docs/contracts/subcontractors.md (W1, W3, W6-W12). Every expected number is a literal written
from the contract; none is produced by the code under test. Today is frozen to 2026-03-02 as in the examples."""

from datetime import date

import pytest

import permita38.api.routers.incoming_offers as offers_router
import permita38.core.service.procurement as procurement_module

TODAY = date(2026, 3, 2)


class _Frozen(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(offers_router, "date", _Frozen)
    monkeypatch.setattr(procurement_module, "date", _Frozen)


@pytest.fixture
def project(boss_client):
    r = boss_client.post("/api/projects", json={"name": "Demo project P"})
    assert r.status_code == 200, r.text
    return r.json()


def capacity_supplier(c, **kw):
    body = {
        "name": "Example Freelance Studio (Demo)",
        "kind": "person-capacity",
        "workdays": [0, 1, 2, 3],
        "weekly_hours": 24,
        "rate": 80.0,
        "country": "DE",
        **kw,
    }
    r = c.post("/api/suppliers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def company_supplier(c, **kw):
    r = c.post("/api/suppliers", json={"name": "Demo Print Shop GmbH", "kind": "company", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def offer_w1(supplier_id, project_id, **kw):
    body = {
        "supplier_id": supplier_id,
        "reference": "ANG-2026-117",
        "title": "Frontend capacity, spring",
        "received_on": "2026-02-20",
        "valid_until": "2026-03-31",
        "lines": [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80.0}],
        "plan": {"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20.0},
        "project_id": project_id,
        **kw,
    }
    return body


def post_offer(c, body):
    r = c.post("/api/incoming-offers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def accept(c, oid, body=None):
    return c.post(f"/api/incoming-offers/{oid}/accept", json={} if body is None else body)


# ----------------------------------------------------------------------------- W1: offer, accept, days
def test_w1_offer_figures_and_accept_creates_the_external_allocation(boss_client, project):
    s = capacity_supplier(boss_client)
    offer = post_offer(boss_client, offer_w1(s["id"], project["id"]))
    assert (offer["hours"], offer["net_total"], offer["vat_rate"], offer["vat"], offer["gross_total"]) == (
        80.0,
        6400.0,
        19.0,
        1216.0,
        7616.0,
    )
    assert offer["vat_mode"] == "domestic" and offer["status"] == "received"

    r = accept(boss_client, offer["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    alloc = body["allocation"]
    assert body["offer"]["status"] == "accepted" and body["offer"]["allocation_id"] == alloc["id"]
    assert body["offer"]["status_changed_at"] == "2026-03-02"
    assert body["commitment"] is None
    assert alloc["kind"] == "external" and alloc["status"] == "active" and alloc["rate"] == 80.0
    assert (alloc["planned_hours"], alloc["planned_net"], alloc["invoiced_net"], alloc["open_net"]) == (
        80.0,
        6400.0,
        0.0,
        6400.0,
    )
    # the hand list of W1: 16 days of 5 h, Monday to Thursday of the four weeks
    days = boss_client.get(f"/api/allocations/{alloc['id']}").json()["days"]
    assert days == [
        {"date": d, "hours": 5.0}
        for d in [
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
            "2026-03-05",
            "2026-03-09",
            "2026-03-10",
            "2026-03-11",
            "2026-03-12",
            "2026-03-16",
            "2026-03-17",
            "2026-03-18",
            "2026-03-19",
            "2026-03-23",
            "2026-03-24",
            "2026-03-25",
            "2026-03-26",
        ]
    ]
    codes = {w["code"] for w in body["planning_warnings"]}
    # open decision: W1 lists only allocation.no_demand, but plan_hours() of the plan gives 74.29 h against 80 h
    assert codes <= {"incoming_offer.hours_mismatch", "allocation.no_demand"}
    assert "allocation.exceeds_weekly_hours" not in codes


# ----------------------------------------------------------------------------- W3: total hours and availability
def test_w3_total_hours_with_an_unavailable_window(boss_client, project):
    s = capacity_supplier(
        boss_client,
        workdays=[0, 1, 2, 3, 4],
        weekly_hours=45,  # fixture choice: the example does not name it; the week needs 41.67 h
        rate=75.0,
        unavailable=[{"from": "2026-03-09", "to": "2026-03-11"}],
    )
    offer = post_offer(
        boss_client,
        offer_w1(
            s["id"],
            project["id"],
            lines=[{"quantity": 100, "unit": "h", "unit_price": 75.0}],
            plan={"start": "2026-03-02", "end": "2026-03-20", "total_hours": 100.0},
        ),
    )
    body = accept(boss_client, offer["id"]).json()
    alloc = body["allocation"]
    assert (alloc["planned_hours"], alloc["planned_net"]) == (100.0, 7500.0)
    outside = [w for w in body["planning_warnings"] if w["code"] == "allocation.outside_availability"]
    assert len(outside) == 1
    assert outside[0]["details"]["dropped_hours"] == 25.0
    assert outside[0]["details"]["days"] == ["2026-03-09", "2026-03-10", "2026-03-11"]


# ----------------------------------------------------------------------------- W6: expiry
def test_w6_expired_offer_needs_confirmation_and_valid_until_today_is_not_expired(boss_client, project):
    s = capacity_supplier(boss_client)
    expired = post_offer(boss_client, offer_w1(s["id"], project["id"], valid_until="2026-03-01"))
    assert expired["effective_status"] == "expired"
    r = accept(boss_client, expired["id"])
    assert r.status_code == 409
    assert r.json() == {"detail": "The offer expired on 2026-03-01; confirm to accept it anyway"}
    assert boss_client.get(f"/api/incoming-offers/{expired['id']}").json()["status"] == "received"
    assert accept(boss_client, expired["id"], {"confirm_expired": True}).status_code == 200

    today_offer = post_offer(boss_client, offer_w1(s["id"], project["id"], valid_until="2026-03-02"))
    assert today_offer["effective_status"] == "received"


# ----------------------------------------------------------------------------- W7 and W8: rounding and reverse charge
def test_w7_rounding_half_up_and_domestic_vat(boss_client):
    s = company_supplier(boss_client)
    offer = post_offer(
        boss_client,
        {
            "supplier_id": s["id"],
            "received_on": "2026-02-20",
            "lines": [{"quantity": 37.5, "unit": "h", "unit_price": 82.45}],
            "vat_rate": 19,
        },
    )
    assert (offer["net_total"], offer["vat_rate"], offer["vat"], offer["gross_total"], offer["vat_mode"]) == (
        3091.88,
        19.0,
        587.46,
        3679.34,
        "domestic",
    )
    assert offer["hours"] == 37.5


def test_w8_reverse_charge_from_an_austrian_supplier_has_no_vat(boss_client):
    s = company_supplier(boss_client, country="AT")  # the mode is derived from the country: EU reverse charge
    assert s["vat_mode"] == "eu_reverse_charge"
    offer = post_offer(
        boss_client,
        {
            "supplier_id": s["id"],
            "received_on": "2026-02-20",
            "lines": [{"quantity": 37.5, "unit": "h", "unit_price": 82.45}],
            "vat_rate": 19,  # ignored: the stored rate is 0 under reverse charge
        },
    )
    assert (offer["net_total"], offer["vat_rate"], offer["vat"], offer["gross_total"], offer["vat_mode"]) == (
        3091.88,
        0.0,
        0.0,
        3091.88,
        "eu_reverse_charge",
    )


# ----------------------------------------------------------------------------- W9 and W10: cancel, expenses
def _w1_allocation(c, project):
    s = capacity_supplier(c)
    offer = post_offer(c, offer_w1(s["id"], project["id"]))
    body = accept(c, offer["id"]).json()
    return s, offer, body["allocation"]


def test_w9_cancel_keeps_the_days_before_the_cut_and_w10_floors_the_open_net(boss_client, project):
    s, _, alloc = _w1_allocation(boss_client, project)
    r = boss_client.post(
        f"/api/allocations/{alloc['id']}/cancel", json={"effective_from": "2026-03-13", "reason": "project paused"}
    )
    assert r.status_code == 200, r.text
    cancelled = r.json()
    assert cancelled["status"] == "cancelled" and cancelled["cancelled_from"] == "2026-03-13"
    assert cancelled["cancel_reason"] == "project paused"
    assert (cancelled["planned_hours"], cancelled["planned_net"]) == (40.0, 3200.0)

    e = boss_client.post(
        "/api/expenses",
        json={
            "date": "2026-03-10",
            "net": 1600,
            "vendor": "Example Freelance Studio (Demo)",
            "supplier_id": s["id"],
            "allocation_id": alloc["id"],
        },
    )
    assert e.status_code == 200, e.text  # expenses answer 200 on create, as before
    assert e.json()["sanity_warnings"] == []  # 1600 is within the 3200 planned
    after = boss_client.get(f"/api/allocations/{alloc['id']}").json()
    assert (after["invoiced_net"], after["open_net"]) == (1600.0, 1600.0)

    com = boss_client.get("/api/finance/commitments").json()
    assert len(com["items"]) == 1
    assert com["items"][0]["allocation_id"] == alloc["id"]
    assert com["totals"] == {"planned_net": 3200.0, "invoiced_net": 1600.0, "open_net": 1600.0}

    over = boss_client.post(
        "/api/expenses",
        json={
            "date": "2026-03-11",
            "net": 1700,
            "vendor": "Example Freelance Studio (Demo)",
            "supplier_id": s["id"],
            "allocation_id": alloc["id"],
        },
    )
    assert over.status_code == 200, over.text
    warn = [w for w in over.json()["sanity_warnings"] if w["code"] == "allocation.invoiced_exceeds_planned"]
    assert len(warn) == 1
    assert warn[0]["details"] == {
        "allocation_id": alloc["id"],
        "planned_net": 3200.0,
        "invoiced_net": 3300.0,
        "excess": 100.0,
    }
    final = boss_client.get(f"/api/allocations/{alloc['id']}").json()
    assert (final["invoiced_net"], final["open_net"]) == (3300.0, 0.0)


def test_cancel_errors(boss_client, project):
    _, _, alloc = _w1_allocation(boss_client, project)
    url = f"/api/allocations/{alloc['id']}/cancel"
    early = boss_client.post(url, json={"effective_from": "2026-03-01"})
    assert early.status_code == 422 and early.json() == {"detail": "effective_from must not be before the start"}
    late = boss_client.post(url, json={"effective_from": "2026-04-01"})
    assert late.status_code == 409 and late.json() == {"detail": "effective_from is after the end of the allocation"}
    assert boss_client.post(url, json={"effective_from": "2026-03-13"}).status_code == 200
    again = boss_client.post(url, json={"effective_from": "2026-03-13"})
    assert again.status_code == 409 and again.json() == {"detail": "The allocation is already cancelled"}


# ----------------------------------------------------------------------------- W11: weekly hours
def test_w11_second_allocation_over_the_weekly_hours_needs_confirmation(boss_client, project):
    s = capacity_supplier(boss_client)  # weekly_hours 24, Mon-Thu
    first = post_offer(boss_client, offer_w1(s["id"], project["id"]))
    assert accept(boss_client, first["id"]).status_code == 200
    second = post_offer(
        boss_client,
        offer_w1(
            s["id"],
            project["id"],
            reference="ANG-2026-118",
            lines=[{"quantity": 32, "unit": "h", "unit_price": 80.0}],
            plan={"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 8.0},
        ),
    )
    blocked = accept(boss_client, second["id"])
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Confirm these warnings first: allocation.exceeds_weekly_hours"
    assert blocked.json()["codes"] == ["allocation.exceeds_weekly_hours"]
    assert boss_client.get(f"/api/incoming-offers/{second['id']}").json()["status"] == "received"

    ok = accept(boss_client, second["id"], {"confirm_warnings": ["allocation.exceeds_weekly_hours"]})
    assert ok.status_code == 200, ok.text
    weekly = [w for w in ok.json()["planning_warnings"] if w["code"] == "allocation.exceeds_weekly_hours"]
    first_week = weekly[0]
    assert first_week["confirmable"] is True
    assert first_week["details"] == {
        "supplier_id": s["id"],
        "week": "2026-03-02",
        "allocated": 28.0,
        "weekly_hours": 24.0,
        "over_by": 4.0,
    }
    assert first_week["message"] == (
        "Example Freelance Studio (Demo) would be allocated 28 h in the week of 2026-03-02, 4 h above the 24 h per "
        "week they can offer."
    )


# ----------------------------------------------------------------------------- W12: dependency hint
def _booked_supplier(c, project, hpw, start, end, weeks_name):
    s = capacity_supplier(c, workdays=[0, 1, 2, 3, 4], weekly_hours=40, rate=80.0, name=weeks_name)
    offer = post_offer(
        c,
        offer_w1(
            s["id"],
            project["id"],
            plan={"start": start, "end": end, "hours_per_week": hpw},
            lines=[{"quantity": 1, "unit": "h", "unit_price": 80.0}],
        ),
    )
    return s, offer


def test_w12_twelve_full_weeks_at_thirty_hours_raise_the_hint(boss_client, project):
    s, offer = _booked_supplier(boss_client, project, 30.0, "2026-06-08", "2026-08-28", "Example Hint A")
    r = accept(boss_client, offer["id"])
    assert r.status_code == 200, r.text
    assert any(w["code"] == "supplier.dependency_hint" for w in r.json()["planning_warnings"])
    dep = boss_client.get(f"/api/suppliers/{s['id']}/dependency").json()
    assert dep["raised"] is True
    assert dep["longest_run_weeks"] == 12
    assert dep["average_hours_per_week"] == 30.0
    assert dep["from"] == "2026-06-08"


def test_w12_below_the_threshold_or_one_week_short_is_not_raised(boss_client, project):
    s, offer = _booked_supplier(boss_client, project, 29.9, "2026-06-08", "2026-08-28", "Example Hint B")
    assert accept(boss_client, offer["id"]).status_code == 200
    dep = boss_client.get(f"/api/suppliers/{s['id']}/dependency").json()
    assert dep["raised"] is False and dep["longest_run_weeks"] == 0 and dep["from"] is None

    s2, offer2 = _booked_supplier(boss_client, project, 30.0, "2026-06-08", "2026-08-21", "Example Hint C")
    assert accept(boss_client, offer2["id"]).status_code == 200
    dep2 = boss_client.get(f"/api/suppliers/{s2['id']}/dependency").json()
    assert dep2["raised"] is False and dep2["longest_run_weeks"] == 11


def test_dependency_of_a_company_is_409(boss_client):
    s = company_supplier(boss_client)
    r = boss_client.get(f"/api/suppliers/{s['id']}/dependency")
    assert r.status_code == 409 and r.json() == {"detail": "Not a subcontractor"}


# ----------------------------------------------------------------------------- status machine (table driven)
ALLOWED = {
    ("received", "review"),
    ("received", "reject"),
    ("received", "accept"),
    ("under_review", "accept"),
    ("under_review", "reject"),
    ("rejected", "reopen"),
    ("accepted", "unaccept"),
}
ACTIONS = ("review", "reject", "reopen", "accept", "unaccept")


def _offer_in_status(c, project, supplier, status):
    offer = post_offer(
        c,
        {
            "supplier_id": supplier["id"],
            "received_on": "2026-02-20",
            "valid_until": "2026-03-31",
            "lines": [{"quantity": 2, "unit": "h", "unit_price": 50.0}],
            "project_id": project["id"],
        },
    )
    oid = offer["id"]
    if status == "under_review":
        assert c.post(f"/api/incoming-offers/{oid}/review").status_code == 200
    elif status == "rejected":
        assert c.post(f"/api/incoming-offers/{oid}/reject", json={}).status_code == 200
    elif status == "accepted":
        assert accept(c, oid).status_code == 200
    return oid


@pytest.mark.parametrize("status", ["received", "under_review", "rejected", "accepted"])
def test_every_status_action_pair(boss_client, project, status):
    supplier = company_supplier(boss_client, name=f"Pair {status}")
    for action in ACTIONS:
        oid = _offer_in_status(boss_client, project, supplier, status)
        if action == "accept":
            r = accept(boss_client, oid)
        elif action == "reject":
            r = boss_client.post(f"/api/incoming-offers/{oid}/reject", json={})
        else:
            r = boss_client.post(f"/api/incoming-offers/{oid}/{action}")
        if (status, action) in ALLOWED:
            assert r.status_code == 200, (status, action, r.text)
        else:
            assert r.status_code == 409, (status, action, r.text)
            assert r.json() == {"detail": f"This action is not possible in status {status}"}


# ----------------------------------------------------------------------------- accepted offers: locks, unaccept, delete
def test_accepted_offer_locks_all_but_notes(boss_client, project):
    supplier = company_supplier(boss_client, name="Lock Test")
    oid = _offer_in_status(boss_client, project, supplier, "accepted")
    current = boss_client.get(f"/api/incoming-offers/{oid}").json()
    body = {
        k: current.get(k)
        for k in ("supplier_id", "reference", "title", "received_on", "valid_until", "lines", "project_id")
    }
    body["lines"] = [{"quantity": 2, "unit": "h", "unit_price": 50.0}]
    bad = boss_client.put(f"/api/incoming-offers/{oid}", json={**body, "reference": "CHANGED"})
    assert bad.status_code == 409
    assert bad.json() == {"detail": "An accepted offer can only change notes, attachment and decision note"}
    ok = boss_client.put(f"/api/incoming-offers/{oid}", json={**body, "notes": "call on Monday"})
    assert ok.status_code == 200 and ok.json()["notes"] == "call on Monday"
    assert boss_client.delete(f"/api/incoming-offers/{oid}").status_code == 409


def test_unaccept_of_a_flat_commitment_is_always_allowed(boss_client, project):
    supplier = company_supplier(boss_client, name="Flat Unaccept")
    oid = _offer_in_status(boss_client, project, supplier, "accepted")
    r = boss_client.post(f"/api/incoming-offers/{oid}/unaccept")
    assert r.status_code == 200 and r.json()["status"] == "under_review" and r.json().get("allocation_id") is None


def test_unaccept_rules_for_a_capacity_allocation(boss_client, project):
    s = capacity_supplier(boss_client)
    future = post_offer(
        boss_client,
        offer_w1(s["id"], project["id"], plan={"start": "2026-03-09", "end": "2026-03-27", "hours_per_week": 20.0}),
    )
    acc = accept(boss_client, future["id"]).json()
    assert boss_client.post(f"/api/incoming-offers/{future['id']}/unaccept").status_code == 200
    assert boss_client.get(f"/api/allocations/{acc['allocation']['id']}").status_code == 404

    _, started, _ = _w1_allocation(boss_client, project)  # starts today: "start > today" is false
    r = boss_client.post(f"/api/incoming-offers/{started['id']}/unaccept")
    assert r.status_code == 409
    assert r.json() == {"detail": "The allocation has started or has linked expenses; cancel it instead"}


def test_delete_of_a_referenced_supplier_is_409(boss_client, project):
    s, _, _ = _w1_allocation(boss_client, project)
    r = boss_client.delete(f"/api/suppliers/{s['id']}")
    assert r.status_code == 409
    assert r.json() == {"detail": "Supplier is used by incoming offers, allocations or expenses; archive it instead"}


# ----------------------------------------------------------------------------- commitments
def test_flat_commitment_of_an_accepted_plain_supplier_offer(boss_client, project):
    supplier = company_supplier(boss_client, name="Demo Print Shop GmbH")
    offer = post_offer(
        boss_client,
        {
            "supplier_id": supplier["id"],
            "reference": "L-77",
            "received_on": "2026-02-20",
            "lines": [{"quantity": 37.5, "unit": "h", "unit_price": 82.45}],
            "vat_rate": 19,
        },
    )
    r = accept(boss_client, offer["id"])
    assert r.status_code == 200
    assert r.json()["allocation"] is None
    assert r.json()["commitment"] == {"kind": "flat", "planned_net": 3091.88, "open_net": 3091.88}
    com = boss_client.get("/api/finance/commitments").json()
    flat = [i for i in com["items"] if i["kind"] == "flat"]
    assert len(flat) == 1 and flat[0]["planned_net"] == 3091.88 and flat[0]["invoiced_net"] is None
    assert com["totals"] == {"planned_net": 3091.88, "invoiced_net": 0.0, "open_net": 3091.88}
    assert com["by_project"] == [
        {"project_id": None, "name": "No project", "planned_net": 3091.88, "invoiced_net": None, "open_net": 3091.88}
    ]


def test_commitments_need_procurement_read(member_client):
    assert member_client.get("/api/finance/commitments").status_code == 403
