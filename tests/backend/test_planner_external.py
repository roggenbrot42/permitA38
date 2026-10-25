"""Planner with subcontractors over HTTP: the ring fence, the external pool and the weekly figures, against the hand
computed examples W4, W5 and W14 of docs/contracts/subcontractors.md. Literal numbers only; today is 2026-03-02."""

from datetime import date

import pytest

import permita38.api.routers.incoming_offers as offers_router
import permita38.core.service.plan_service as plan_module
import permita38.core.service.procurement as procurement_module

TODAY = date(2026, 3, 2)


class _Frozen(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    for mod in (plan_module, offers_router, procurement_module):
        monkeypatch.setattr(mod, "date", _Frozen)


@pytest.fixture
def boss_id(app):
    return next(u["id"] for u in app.state.services.store.list("users") if u["role"] == "boss")


def new_project(c, name):
    r = c.post("/api/projects", json={"name": name})
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


def accept_plan(c, sid, pid, start, end, hpw, net_hours=80, reference="ANG-2026-117"):
    body = {
        "supplier_id": sid,
        "reference": reference,
        "received_on": "2026-02-20",
        "valid_until": "2026-03-31",
        "lines": [{"quantity": net_hours, "unit": "h", "unit_price": 80.0}],
        "plan": {"start": start, "end": end, "hours_per_week": hpw},
        "project_id": pid,
    }
    r = c.post("/api/incoming-offers", json=body)
    assert r.status_code == 201, r.text
    a = c.post(f"/api/incoming-offers/{r.json()['id']}/accept", json={})
    assert a.status_code == 200, a.text
    return a.json()


def contract(c, title, pid, user_ids, hpw, rate, start="2026-03-02", end="2026-03-27"):
    r = c.post(
        "/api/contracts",
        json={
            "title": title,
            "project_id": pid,
            "start": start,
            "end": end,
            "hours_per_week": hpw,
            "rate": rate,
            "user_ids": user_ids,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def forecast(c, weeks=4, include_external=None):
    q = f"?weeks={weeks}" + ("" if include_external is None else f"&include_external={include_external}")
    r = c.get(f"/api/forecast{q}")
    assert r.status_code == 200, r.text
    return r.json()


def p_row(week, pid):
    return next(p for p in week["projects"] if p["project_id"] == pid)


# ----------------------------------------------------------------------------- W4: the ring fence and the split
def test_w4_contract_split_to_the_subcontractor_leaves_an_external_gap(boss_client, boss_id):
    p = new_project(boss_client, "Demo project P")
    c_row = contract(boss_client, "Contract C_P", p["id"], [boss_id], 50.0, 100.0)
    alloc = boss_client.post(
        "/api/allocations",
        json={
            "user_id": boss_id,
            "project_id": p["id"],
            "start": "2026-03-02",
            "end": "2026-03-27",
            "hours_per_week": 40.0,
        },
    )
    assert alloc.status_code == 201, alloc.text
    s = supplier(boss_client)
    accepted = accept_plan(boss_client, s["id"], p["id"], "2026-03-02", "2026-03-27", 20.0)
    assert accepted["allocation"]["planned_net"] == 6400.0

    # before: C is the boss alone -> internal cap 40, assigned 50, gap 10
    boss_only = forecast(boss_client, 4, include_external=0)
    assert boss_only["weeks"][0]["capacity"] == 40.0

    # after: user_ids = [boss, S] -> C splits 25 h each
    contract_body = {k: c_row[k] for k in ("title", "project_id", "start", "end", "hours_per_week", "rate")}
    r = boss_client.put(
        f"/api/contracts/{c_row['id']}", json={**contract_body, "user_ids": [boss_id, s["id"]], "status": "confirmed"}
    )
    assert r.status_code == 200, r.text
    fc = forecast(boss_client, 4)
    week = fc["weeks"][0]
    assert (week["capacity"], week["confirmed"], week["free_after_confirmed"], week["free_worst_case"]) == (
        60.0,
        50.0,
        10.0,
        10.0,
    )
    assert (week["utilisation"], week["status"], week["gap"]) == (83.3, "over", 5.0)
    assert (week["revenue"], week["basis"], week["contracted_revenue"]) == (5000.0, "contract", 5000.0)
    assert (week["external_capacity"], week["external_assigned"], week["external_gap"], week["external_cost"]) == (
        20.0,
        25.0,
        5.0,
        1600.0,
    )
    assert week["external_by_project"] == [
        {
            "project_id": p["id"],
            "external_capacity": 20.0,
            "external_assigned": 25.0,
            "external_gap": 5.0,
            "external_cost": 1600.0,
        }
    ]
    assert week["contributions"] == [
        {"kind": "contract", "id": c_row["id"], "title": "Contract C_P", "hours": 50.0, "binding": False}
    ]
    row = p_row(week, p["id"])
    assert row["internal"] == {"capacity": 40.0, "assigned": 25.0, "gap": 0.0}
    assert row["external"] == {"capacity": 20.0, "assigned": 25.0, "gap": 5.0}
    assert row["external_cost"] == 1600.0
    assert all(w["external_gap"] == 5.0 and w["external_cost"] == 1600.0 for w in fc["weeks"])

    t = fc["totals"]
    assert (t["capacity"], t["confirmed"], t["free_after_confirmed"], t["free_worst_case"]) == (
        240.0,
        200.0,
        40.0,
        40.0,
    )
    assert (t["contracted_revenue"], t["revenue"], t["over_weeks"], t["gap"]) == (20000.0, 20000.0, 4, 20.0)
    assert t["gap_by_project"] == [{"project_id": p["id"], "name": "Demo project P", "gap": 20.0}]
    assert (t["external_capacity"], t["external_gap"], t["external_cost"], t["margin"]) == (80.0, 20.0, 6400.0, 13600.0)
    pool_warnings = [w for w in fc["warnings"] if w["code"] == "capacity.gap" and w["details"]["pool"] == "external"]
    assert len(pool_warnings) == 4
    assert pool_warnings[0]["details"] == {
        "project_id": p["id"],
        "project_name": "Demo project P",
        "week": "2026-03-02",
        "pool": "external",
        "assigned": 25.0,
        "capacity": 20.0,
        "gap": 5.0,
        "contributions": [],
    }


# ----------------------------------------------------------------------------- W5: without the subcontractor
def test_w5_without_suppliers_the_numbers_are_the_old_ones(boss_client, boss_id):
    p = new_project(boss_client, "Demo project P")
    c_row = contract(boss_client, "Contract C_P", p["id"], [boss_id], 50.0, 100.0)
    boss_client.post(
        "/api/allocations",
        json={
            "user_id": boss_id,
            "project_id": p["id"],
            "start": "2026-03-02",
            "end": "2026-03-27",
            "hours_per_week": 40.0,
        },
    )
    s = supplier(boss_client)
    accept_plan(boss_client, s["id"], p["id"], "2026-03-02", "2026-03-27", 20.0)
    body = {k: c_row[k] for k in ("title", "project_id", "start", "end", "hours_per_week", "rate")}
    assert (
        boss_client.put(
            f"/api/contracts/{c_row['id']}", json={**body, "user_ids": [boss_id, s["id"]], "status": "confirmed"}
        ).status_code
        == 200
    )

    fc = forecast(boss_client, 4, include_external=0)
    week = fc["weeks"][0]
    assert (week["capacity"], week["confirmed"], week["free_after_confirmed"], week["free_worst_case"]) == (
        40.0,
        50.0,
        -10.0,
        -10.0,
    )
    assert (week["utilisation"], week["status"], week["gap"], week["gap_confirmed"]) == (125.0, "over", 10.0, 10.0)
    assert (week["external_capacity"], week["external_assigned"], week["external_gap"], week["external_cost"]) == (
        0.0,
        0.0,
        0.0,
        0.0,
    )
    assert week["external_by_project"] == []
    row = p_row(week, p["id"])
    assert row["internal"] == {"capacity": 40.0, "assigned": 50.0, "gap": 10.0}
    assert row["external"] == {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}
    assert row["external_cost"] == 0.0
    t = fc["totals"]
    assert (t["gap"], t["external_capacity"], t["external_gap"], t["external_cost"], t["margin"]) == (
        40.0,
        0.0,
        0.0,
        0.0,
        20000.0,
    )
    assert t["revenue"] == 20000.0


# ----------------------------------------------------------------------------- W14: bought capacity, ring fence
def test_w14_bought_capacity_without_demand_is_paid_and_warned(boss_client):
    p = new_project(boss_client, "Demo project P")
    s = supplier(boss_client)
    accept_plan(boss_client, s["id"], p["id"], "2026-03-02", "2026-03-27", 20.0)
    fc = forecast(boss_client, 1)
    row = p_row(fc["weeks"][0], p["id"])
    assert row["external"] == {"capacity": 20.0, "assigned": 0.0, "gap": 0.0}
    assert row["external_cost"] == 1600.0
    nodemand = [w for w in fc["warnings"] if w["code"] == "allocation.no_demand"]
    assert len(nodemand) == 1 and nodemand[0]["severity"] == "info"
    assert nodemand[0]["details"]["capacity"] == 20.0 and nodemand[0]["details"]["week"] == "2026-03-02"


def test_w14_ring_fence_demand_of_another_project_draws_no_hours_from_p(boss_client):
    p = new_project(boss_client, "Demo project P")
    q = new_project(boss_client, "Demo project Q")
    s = supplier(boss_client)
    accept_plan(boss_client, s["id"], p["id"], "2026-03-02", "2026-03-27", 20.0)
    d = contract(boss_client, "Contract D_Q", q["id"], [s["id"]], 10.0, 80.0)
    fc = forecast(boss_client, 1)
    week = fc["weeks"][0]
    q_row = p_row(week, q["id"])
    assert q_row["external"] == {"capacity": 0.0, "assigned": 10.0, "gap": 10.0}
    assert q_row["status"] == "over"
    assert p_row(week, p["id"])["external"] == {"capacity": 20.0, "assigned": 0.0, "gap": 0.0}
    assert week["gap"] == 10.0 and week["external_gap"] == 10.0
    entry = next(c for c in week["contributions"] if c["id"] == d["id"])
    assert entry["external"] is True and entry["hours"] == 10.0  # every assignee is a subcontractor
    assert next(x for x in fc["contracts"] if x["id"] == d["id"])["hours_visible"] == 10.0


# ----------------------------------------------------------------------------- W2: Easter Monday inside the window
def test_w2_easter_monday_is_not_made_up_in_the_weekly_figures(boss_client):
    p = new_project(boss_client, "Demo project P")
    s = supplier(boss_client)
    acc = accept_plan(boss_client, s["id"], p["id"], "2026-04-06", "2026-04-17", 20.0, net_hours=35)
    assert (acc["allocation"]["planned_hours"], acc["allocation"]["planned_net"]) == (35.0, 2800.0)
    weeks = forecast(boss_client, 7)["weeks"]
    assert [w["week"] for w in weeks][5:] == ["2026-04-06", "2026-04-13"]
    assert (weeks[5]["external_capacity"], weeks[5]["external_cost"]) == (15.0, 1200.0)
    assert (weeks[6]["external_capacity"], weeks[6]["external_cost"]) == (20.0, 1600.0)
    assert weeks[0]["external_capacity"] == 0.0  # no hours before the allocation starts


# ----------------------------------------------------------------------------- W3: a contract spreads around S.unavailable
def test_w3_contract_of_a_subcontractor_spreads_around_its_unavailable_window(boss_client):
    q = new_project(boss_client, "Demo project Q")
    s = boss_client.post("/api/suppliers", json={
        "name": "Example Freelance Studio (Demo)", "kind": "person-capacity", "workdays": [0, 1, 2, 3, 4],
        "weekly_hours": 45, "rate": 75.0, "country": "DE",
        "unavailable": [{"from": "2026-03-09", "to": "2026-03-11"}],
    }).json()
    contract(boss_client, "Contract D_Q", q["id"], [s["id"]], 10.0, 75.0, end="2026-03-20")
    days = boss_client.get("/api/plan/day-table?from=2026-03-09&to=2026-03-13").json()["days"]
    by_date = {d["date"]: d for d in days}
    # Monday to Wednesday are unavailable: no demand there, the week's 10 h go to Thursday and Friday (5 h each)
    for day in ("2026-03-09", "2026-03-10", "2026-03-11"):  # (the boss's own unassigned hours stay in the table)
        assert q["id"] not in by_date[day]["scopes"], day
        assert all(sc["external"]["assigned"] == 0.0 for sc in by_date[day]["scopes"].values()), day
    for day in ("2026-03-12", "2026-03-13"):
        assert by_date[day]["scopes"][q["id"]]["external"]["assigned"] == 5.0, day


# ----------------------------------------------------------------------------- W9: the forecast half of a cancel
def test_w9_after_the_cancel_the_released_hours_are_free(boss_client):
    p = new_project(boss_client, "Demo project P")
    s = supplier(boss_client)
    acc = accept_plan(boss_client, s["id"], p["id"], "2026-03-02", "2026-03-27", 20.0)
    r = boss_client.post(f"/api/allocations/{acc['allocation']['id']}/cancel",
                         json={"effective_from": "2026-03-13", "reason": "project paused"})
    assert r.status_code == 200, r.text
    weeks = forecast(boss_client, 4)["weeks"]
    assert [w["external_capacity"] for w in weeks] == [20.0, 20.0, 0.0, 0.0]
