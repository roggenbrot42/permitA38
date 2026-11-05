"""Planner views (WP4a views.py): the JSON of GET /api/plan, worked examples E1 and E7 of the planning spec."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from permita38.core.service.planner.cover import cover
from permita38.core.service.planner.model import Assignment, Booking, Item, Owner
from permita38.core.service.planner.supply import build
from permita38.core.service.planner.views import plan_json, verdict_warnings

D = date
NAMES = {"orchard": "Orchard", "pine": "Pine", "adm": "Administration"}
WEEK = (0, 1, 2, 3, 4)
START, END, SAM_END = D(2026, 11, 9), D(2026, 12, 18), D(2026, 12, 4)
TODAY = D(2026, 11, 8)
LO, HI = D(2026, 11, 9), D(2026, 12, 20)


def _no_holiday(_d: date) -> str | None:
    return None


def _asg(aid, owner, project, kind, start, end, hpw=None, total=None) -> Assignment:
    return Assignment(aid, owner, project, kind, start, end, hpw, total)


def _item(kind, iid, project, start, end, hpw=None, total=None, rate="120.00", win=1.0) -> Item:
    return Item(kind, iid, iid, project, start, end, hpw, total, Decimal(rate), win)


def _scene(
    extra_items=(), bookings=(), owners_extra=(), assignments_extra=(), internal=None, user_id=None, project_id=None
):
    owners = [
        Owner("ada", "Ada", "employee", 40.0, WEEK),
        Owner("sam", "Sam", "subcontractor", 0.0, WEEK),
        *owners_extra,
    ]
    assignments = [
        _asg("a1", "ada", "adm", "internal", START, END, hpw=30.0),
        _asg("a2", "ada", "orchard", "customer", START, END, hpw=4.0),
        _asg("a3", "sam", "orchard", "customer", START, SAM_END, total=24.0),
        *assignments_extra,
    ]
    supply = build(owners, assignments, _no_holiday, TODAY, LO, HI)
    items = [
        _item("contract", "K1", "orchard", START, END, hpw=4.0),
        _item("contract", "K2", "orchard", START, SAM_END, total=50.0),
        *extra_items,
    ]
    verdicts = cover(items, list(bookings), supply, owners, TODAY, NAMES)
    warnings = verdict_warnings(verdicts, NAMES)
    if internal is None:
        internal = [{"project_id": "adm", "name": "Administration", "budget_hours": None, "booked": 0.0}]
    body = plan_json(
        supply,
        owners,
        assignments,
        verdicts,
        internal,
        NAMES,
        {"orchard": Decimal("2160.00")},
        {},
        warnings,
        holiday=_no_holiday,
        user_id=user_id,
        project_id=project_id,
    )
    return body, verdicts, warnings


def _project(body, pid):
    return next(p for p in body["projects"] if p["project_id"] == pid)


def test_e1_week_row():
    body, _, _ = _scene()
    assert body["today"] == "2026-11-08"
    assert len(body["weeks"]) == 6
    assert body["weeks"][0] == {
        "week": "2026-11-09",
        "week_end": "2026-11-15",
        "capacity": 40.0,
        "holiday_hours": 0.0,
        "vacation_hours": 0.0,
        "absence_hours": 0.0,
        "busy_hours": 0.0,
        "assigned_customer": 4.0,
        "assigned_internal": 30.0,
        "unassigned": 6.0,
        "over_assigned": 0.0,
        "billable_share": 10.0,
        "subcontractor_hours": 6.0,
    }
    # the last week: Sam is finished, Ada keeps her 4 + 30 hours
    assert body["weeks"][-1]["subcontractor_hours"] == 0.0
    assert body["weeks"][-1]["week"] == "2026-12-14"


def test_e1_people():
    body, _, _ = _scene()
    assert body["people"] == [
        {
            "user_id": "ada",
            "name": "Ada",
            "capacity": 240.0,
            "assigned": 204.0,
            "unassigned": 36.0,
            "assignments": [
                {"id": "a1", "project_id": "adm", "hours": 180.0},
                {"id": "a2", "project_id": "orchard", "hours": 24.0},
            ],
        }
    ]


def test_e1_project_and_item():
    body, _, _ = _scene()
    p = _project(body, "orchard")
    assert p["name"] == "Orchard" and p["kind"] == "customer"
    assert p["supply"] == {"employees": 24.0, "subcontractors": 24.0, "total": 48.0}
    assert p["remaining_contracts"] == 74.0 and p["shortfall_contracts"] == 26.0
    assert p["remaining_offers"] == 0.0
    assert p["shortfall_worst_case"] == 26.0 and p["shortfall_expected"] == 26.0
    assert p["status"] == "short"
    assert p["planned_subcontractor_cost"] == 2160.0
    assert p["contract_value"] == 8880.0
    assert p["margin"] == 6720.0
    k2 = next(i for i in p["items"] if i["id"] == "K2")
    assert k2 == {
        "kind": "contract",
        "id": "K2",
        "title": "K2",
        "type": "total",
        "start": "2026-11-09",
        "end": "2026-12-04",
        "promised": 50.0,
        "booked": 0.0,
        "remaining": 50.0,
        "covered": 24.0,
        "shortfall": 26.0,
        "weekly_shortfalls": [],
        "rate": 120.0,
        "value": 6000.0,
        "invoiced": 0.0,
        "open": 6000.0,
        "win_probability": None,
        "expected_value": None,
        "explanation": (
            "50 h promised − 0 h booked = 50 h remaining. Until 04.12.2026 Orchard has 24 h of assigned time "
            "left after its weekly contracts. Short 26 h."
        ),
        "suggestions": [{"user_id": "ada", "name": "Ada", "hours": 24.0, "from": "2026-11-09", "to": "2026-12-04"}],
    }


def test_e1_internal_entry_and_warning():
    body, verdicts, warnings = _scene()
    assert _project(body, "adm") == {
        "project_id": "adm",
        "name": "Administration",
        "kind": "internal",
        "assigned": 180.0,
        "booked": 0.0,
        "budget_hours": None,
        "status": "ok",
    }
    assert [w["code"] for w in warnings] == ["contract.shortfall"]
    w = warnings[0]
    assert w["severity"] == "warning" and w["message"] == next(i for i in verdicts[0].items if i.id == "K2").explanation
    assert w["details"] == {
        "project_id": "orchard",
        "item_id": "K2",
        "kind": "contract",
        "shortfall": 26.0,
        "weekly_shortfalls": [],
    }
    assert body["warnings"] == warnings


def test_e7_offer():
    offer = _item("offer", "O1", "orchard", D(2026, 11, 16), SAM_END, total=10.0, win=0.5)
    body, _, warnings = _scene(extra_items=[offer])
    p = _project(body, "orchard")
    assert p["shortfall_contracts"] == 26.0
    assert p["shortfall_worst_case"] == 36.0
    assert p["shortfall_expected"] == 31.0
    assert p["status"] == "short"
    o1 = next(i for i in p["items"] if i["id"] == "O1")
    assert o1["value"] == 1200.0 and o1["expected_value"] == 600.0
    assert o1["win_probability"] == 50.0
    assert o1["shortfall"] == 10.0
    assert o1["invoiced"] == 0.0 and o1["open"] == 1200.0
    assert p["contract_value"] == 8880.0  # offers are not contract value
    assert [w["code"] for w in warnings] == ["contract.shortfall", "offer.shortfall"]


def test_invoiced_only_for_contracts():
    body, _, _ = _scene()
    owners = [Owner("ada", "Ada", "employee", 40.0, WEEK)]
    supply = build(owners, [], _no_holiday, TODAY, LO, HI)
    verdicts = cover([_item("contract", "K1", "orchard", START, END, hpw=4.0)], [], supply, owners, TODAY, NAMES)
    out = plan_json(supply, owners, [], verdicts, [], NAMES, {}, {"K1": Decimal("1000.50")}, [], holiday=_no_holiday)
    item = out["projects"][0]["items"][0]
    assert item["invoiced"] == 1000.5 and item["open"] == 2880.0 - 1000.5
    assert body  # the shared scene still builds


def test_user_filter():
    body, _, _ = _scene(
        owners_extra=[Owner("ben", "Ben", "employee", 20.0, WEEK)],
        assignments_extra=[_asg("a4", "ben", "pine", "customer", START, END, hpw=10.0)],
        user_id="ben",
    )
    assert [p["user_id"] for p in body["people"]] == ["ben"]
    assert body["people"][0]["capacity"] == 120.0 and body["people"][0]["assigned"] == 60.0
    row = body["weeks"][0]
    assert row["capacity"] == 20.0 and row["assigned_customer"] == 10.0 and row["unassigned"] == 10.0
    assert all(w["subcontractor_hours"] == 0.0 for w in body["weeks"])
    unfiltered, _, _ = _scene(owners_extra=[Owner("ben", "Ben", "employee", 20.0, WEEK)])
    assert unfiltered["weeks"][0]["capacity"] == 60.0 and unfiltered["weeks"][0]["subcontractor_hours"] == 6.0


def test_project_filter():
    internal = [{"project_id": "adm", "name": "Administration", "budget_hours": None, "booked": 0.0}]
    body, _, _ = _scene(project_id="orchard", internal=internal)
    assert [p["project_id"] for p in body["projects"]] == ["orchard"]
    body, _, _ = _scene(project_id="adm")
    assert [p["project_id"] for p in body["projects"]] == ["adm"]
    body, _, _ = _scene(project_id="unassigned")
    assert body["projects"] == []
    loose = _item("contract", "L1", None, START, SAM_END, total=10.0)
    body, _, _ = _scene(extra_items=[loose], project_id="unassigned")
    (p,) = body["projects"]
    assert p["project_id"] == "unassigned" and p["name"] == "Unassigned" and p["kind"] == "unassigned"
    assert p["planned_subcontractor_cost"] == 0.0
    assert p["items"][0]["id"] == "L1"
    # people and weeks are not filtered by project
    assert len(body["people"]) == 1 and len(body["weeks"]) == 6


def test_internal_over_budget():
    internal = [{"project_id": "adm", "name": "Administration", "budget_hours": 150.0, "booked": 10.0}]
    body, _, _ = _scene(internal=internal)
    e = _project(body, "adm")
    assert e["status"] == "over_budget" and e["assigned"] == 180.0 and e["booked"] == 10.0
    assert e["budget_hours"] == 150.0
    internal = [{"project_id": "adm", "name": "Administration", "budget_hours": 190.0, "booked": 10.0}]
    body, _, _ = _scene(internal=internal)
    assert _project(body, "adm")["status"] == "ok"  # 10 + 180 is exactly the budget


def test_no_supply_warning():
    pine = _item("contract", "P1", "pine", START, END, hpw=5.0)
    body, _, warnings = _scene(extra_items=[pine])
    (w,) = [w for w in warnings if w["code"] == "project.no_supply"]
    assert w["severity"] == "warning" and w["details"]["project_id"] == "pine"
    assert "Pine" in w["message"]
    # Pine is ordered after Orchard (by name), and its own shortfall follows the no_supply warning
    codes = [(x["code"], x["details"]["project_id"]) for x in warnings]
    assert codes == [
        ("contract.shortfall", "orchard"),
        ("project.no_supply", "pine"),
        ("contract.shortfall", "pine"),
    ]
    assert not any(x["code"] == "project.no_supply" and x["details"]["project_id"] == "orchard" for x in warnings)


def test_booked_unattributed():
    booking = Booking("orchard", D(2026, 11, 2), 3.0)
    owners = [Owner("ada", "Ada", "employee", 40.0, WEEK)]
    asg = [_asg("a2", "ada", "orchard", "customer", D(2026, 11, 2), END, hpw=4.0)]
    supply = build(owners, asg, _no_holiday, TODAY, LO, HI)
    item = _item("contract", "K9", "orchard", D(2026, 11, 9), END, hpw=4.0)
    verdicts = cover([item], [booking], supply, owners, TODAY, NAMES)
    assert verdicts[0].booked_unattributed == 3.0
    (w,) = [w for w in verdict_warnings(verdicts, NAMES) if w["code"] == "booked.unattributed"]
    assert w["severity"] == "info" and w["details"] == {"project_id": "orchard", "hours": 3.0}
    assert "3 h" in w["message"] and "Orchard" in w["message"]


def test_contract_no_project():
    loose = _item("contract", "L1", None, START, SAM_END, total=10.0)
    _, verdicts, warnings = _scene(extra_items=[loose])
    codes = [(w["code"], w["details"].get("item_id")) for w in warnings]
    assert ("contract.no_project", "L1") in codes
    w = next(w for w in warnings if w["code"] == "contract.no_project")
    assert w["severity"] == "info" and w["details"]["kind"] == "contract"
    # the unassigned scope is last and has no project.no_supply
    assert codes[0] == ("contract.shortfall", "K2")
    assert not any(c == "project.no_supply" for c, _ in codes)


def test_json_serialisable():
    offer = _item("offer", "O1", "orchard", D(2026, 11, 16), SAM_END, total=10.0, win=0.5)
    loose = _item("contract", "L1", None, START, SAM_END, total=10.0)
    body, _, _ = _scene(extra_items=[offer, loose])
    assert json.loads(json.dumps(body)) == body
