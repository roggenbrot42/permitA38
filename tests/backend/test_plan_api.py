"""GET /api/plan and POST /api/plan/simulate through the HTTP API (spec E1, invented data only)."""

from __future__ import annotations

from datetime import date

import pytest

from permita38.core import rights
from permita38.core.service.planner import service as planner_service

TODAY = date(2026, 11, 8)  # Sunday, E1
ADA, SAM = "adaaaaaa01", "samaaaaa01"
CUST, ORCH, ADM = "custaaaa01", "projorch01", "projadm001"


class FrozenDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(planner_service, "date", FrozenDate)


@pytest.fixture
def store(app, boss_client):
    s = app.state.services.store
    s.save_settings({**s.settings(), "state": "BY", "default_rate": 70, "planning_default_win_probability": 50})
    s.save("users", {"id": ADA, "username": "ada", "name": "Ada", "role": "member", "weekly_hours": 40,
                     "workdays": [0, 1, 2, 3, 4], "active": True})  # fmt: skip
    s.save("suppliers", {"id": SAM, "name": "Sam", "kind": "person-capacity", "active": True,
                         "workdays": [0, 1, 2, 3, 4], "rate": 90})  # fmt: skip
    s.save("customers", {"id": CUST, "name": "Orchard Demo", "rate": 120})
    s.save("projects", {"id": ORCH, "name": "Orchard", "customer_id": CUST, "kind": "customer", "status": "active"})
    s.save("projects", {"id": ADM, "name": "Administration", "kind": "internal", "status": "active"})
    return s


@pytest.fixture
def e1(store):
    def alloc(aid, owner, project, **kw):
        row = {"id": aid, "project_id": project, "start": "2026-11-09", "end": "2026-12-18", "status": "active",
               ("supplier_id" if owner == SAM else "user_id"): owner, **kw}  # fmt: skip
        store.save("allocations", row)

    def contract(cid, **kw):
        store.save("contracts", {"id": cid, "title": cid, "project_id": ORCH, "customer_id": CUST,
                                 "start": "2026-11-09", "end": "2026-12-18", "status": "confirmed", "rate": 120,
                                 **kw})  # fmt: skip

    alloc("allocadm01", ADA, ADM, hours_per_week=30)
    alloc("allocada01", ADA, ORCH, hours_per_week=4)
    alloc("allocsam01", SAM, ORCH, total_hours=24, end="2026-12-04", rate=90)
    contract("contrk1001", hours_per_week=4)
    contract("contrk2001", total_hours=50, end="2026-12-04")


def orchard(body):
    return next(p for p in body["projects"] if p["project_id"] == ORCH)


def test_plan_shape_and_e1_numbers(boss_client, e1):
    r = boss_client.get("/api/plan", params={"weeks": 6})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"today", "weeks", "people", "projects", "warnings", "skipped"}
    assert body["today"] == "2026-11-08" and len(body["weeks"]) == 6 and body["skipped"] == []
    o = orchard(body)
    assert o["supply"] == {"employees": 24.0, "subcontractors": 24.0, "total": 48.0}
    assert (o["remaining_contracts"], o["shortfall_contracts"], o["status"]) == (74.0, 26.0, "short")
    assert (o["planned_subcontractor_cost"], o["contract_value"], o["margin"]) == (2160.0, 8880.0, 6720.0)
    k2 = next(i for i in o["items"] if i["id"] == "contrk2001")
    assert (k2["remaining"], k2["covered"], k2["shortfall"]) == (50.0, 24.0, 26.0)
    assert "contract.shortfall" in [w["code"] for w in body["warnings"]]


def test_plan_user_filter_gives_ada_week(boss_client, e1):
    body = boss_client.get("/api/plan", params={"weeks": 6, "user_id": ADA}).json()
    w = next(w for w in body["weeks"] if w["week"] == "2026-11-09")
    assert (w["capacity"], w["assigned_customer"], w["assigned_internal"], w["unassigned"]) == (40.0, 4.0, 30.0, 6.0)
    assert (w["billable_share"], w["subcontractor_hours"]) == (10.0, 0.0)
    assert [p["user_id"] for p in body["people"]] == [ADA]


def test_plan_project_filter_and_unknown_project(boss_client, e1):
    body = boss_client.get("/api/plan", params={"project_id": ORCH}).json()
    assert [p["project_id"] for p in body["projects"]] == [ORCH]
    assert boss_client.get("/api/plan", params={"project_id": "nosuchproj"}).status_code == 404
    assert boss_client.get("/api/plan", params={"weeks": 0}).status_code == 422
    assert boss_client.get("/api/plan", params={"weeks": 53}).status_code == 422


def test_simulate_delta_and_nothing_written(boss_client, e1, store):
    changes = {"allocations": [{"user_id": ADA, "project_id": ORCH, "start": "2026-11-09", "end": "2026-12-04",
                                "hours_per_week": 6}]}  # fmt: skip
    r = boss_client.post("/api/plan/simulate", json={"changes": changes, "weeks": 6})
    assert r.status_code == 200, r.text
    body = r.json()
    assert orchard(body)["shortfall_contracts"] == 2.0
    assert body["delta"]["total"] == {"shortfall_before": 26.0, "shortfall_after": 2.0, "delta": -24.0}
    assert body["delta"]["projects"] == [
        {"project_id": ORCH, "shortfall_before": 26.0, "shortfall_after": 2.0, "delta": -24.0}
    ]
    assert len(store.list("allocations")) == 3
    assert boss_client.post("/api/plan/simulate", json={"project_id": "nosuchproj"}).status_code == 404


def test_removed_routes(boss_client, e1):
    assert boss_client.get("/api/forecast").status_code == 404
    assert boss_client.get("/api/plan/day-table", params={"from": "2026-11-09", "to": "2026-11-13"}).status_code == 404


def test_plan_rights(member_client, monkeypatch):
    assert member_client.get("/api/plan").status_code == 403
    assert member_client.post("/api/plan/simulate", json={}).status_code == 403
    monkeypatch.setitem(rights.MATRIX[rights.EMPLOYEE], "planning", (rights.Scope.ALL, rights.Scope.NONE))
    assert member_client.get("/api/plan").status_code == 200
    assert member_client.post("/api/plan/simulate", json={}).status_code == 200
