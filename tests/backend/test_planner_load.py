"""planner/load.py (WP4b P1): the store turned into the planner's input. Real SQLite store, invented data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.calendar.busy_provider import CalendarBusyProvider
from permita38.core.service.planner.load import load
from permita38.core.service.planner.model import WorkLimits

D = date
TODAY = D(2026, 11, 6)  # a Friday
BOSS, EMP, GONE = "bossaaaa01", "empaaaaa01", "gone000001"
SUP, SUPOFF = "supact0001", "supoff0001"
CUST, P1, P2, P3 = "custaaaa01", "projaaaa01", "projaaaa02", "projaaaa03"


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    r.save_settings({"state": "", "default_rate": 70, "planning_default_win_probability": 50})
    r.save("users", {"id": BOSS, "username": "boss", "name": "Bea Boss", "role": "boss", "weekly_hours": 40,
                     "workdays": [0, 1, 2, 3, 4], "active": True})  # fmt: skip
    r.save("users", {"id": EMP, "username": "emp", "name": "Eli Employee", "role": "member", "weekly_hours": 32,
                     "workdays": [0, 1, 2, 3], "active": True})  # fmt: skip
    r.save("users", {"id": GONE, "username": "gone", "name": "Gone", "role": "member", "weekly_hours": 40,
                     "active": False})  # fmt: skip
    r.save("suppliers", {"id": SUP, "name": "Studio Demo", "kind": "person-capacity", "active": True,
                         "workdays": [0, 1, 2], "weekly_hours": 24, "rate": 80,
                         "unavailable": [{"from": "2026-11-16", "to": "2026-11-17"}]})  # fmt: skip
    r.save("suppliers", {"id": SUPOFF, "name": "Studio Off", "kind": "person-capacity", "active": False})
    r.save("suppliers", {"id": "suppother1", "name": "Paper Co", "kind": "goods", "active": True})
    r.save("customers", {"id": CUST, "name": "Orchard Demo", "rate": 90})
    r.save("projects", {"id": P1, "name": "Orchard", "customer_id": CUST, "kind": "customer", "status": "active"})
    r.save("projects", {"id": P2, "name": "Administration", "kind": "internal", "status": "active",
                        "budget_hours": 10})  # fmt: skip
    r.save("projects", {"id": P3, "name": "Pine", "kind": "customer", "status": "active", "rate": 100})
    yield r
    r.close()


def owner(snap, oid):
    return next(o for o in snap.owners if o.id == oid)


def contract(repo, cid, **kw):
    row = {"id": cid, "title": cid, "project_id": P1, "customer_id": CUST, "start": "2026-11-09", "end": "2026-11-27",
           "hours_per_week": 10, "status": "confirmed"}  # fmt: skip
    row.update(kw)
    return repo.save("contracts", row)


def offer(repo, oid, **kw):
    row = {"id": oid, "number": "A-" + oid[-3:], "status": "sent", "project_id": P1, "customer_id": CUST,
           "date": "2026-11-02", "valid_until": "2026-12-31", "lines": [],
           "plan": {"start": "2026-12-01", "end": "2026-12-18", "hours_per_week": 8}}  # fmt: skip
    row.update(kw)
    return repo.save("offers", row)


def alloc(repo, aid, owner_id, project=P1, **kw):
    row = {"id": aid, "project_id": project, "start": "2026-11-09", "end": "2026-11-27", "hours_per_week": 20,
           "status": "active", ("supplier_id" if owner_id in (SUP, SUPOFF) else "user_id"): owner_id}  # fmt: skip
    row.update(kw)
    return repo.save("allocations", row)


def entry(repo, i, user, day, minutes, project):
    repo.save("entries", {"id": f"ent{i:07d}", "user_id": user, "date": day, "minutes": minutes,
                          "project_id": project})  # fmt: skip


# -- owners -------------------------------------------------------------------------------------------------
def test_owners_boss_employee_and_active_suppliers_only(repo):
    snap = load(repo, TODAY)
    assert sorted(o.id for o in snap.owners) == sorted([BOSS, EMP, SUP])
    boss, emp, sup = owner(snap, BOSS), owner(snap, EMP), owner(snap, SUP)
    assert (boss.kind, boss.weekly_hours, boss.workdays, boss.name) == ("boss", 40.0, (0, 1, 2, 3, 4), "Bea Boss")
    assert (emp.kind, emp.weekly_hours, emp.workdays, emp.limits) == ("employee", 32.0, (0, 1, 2, 3), None)
    assert (sup.kind, sup.weekly_hours, sup.workdays, sup.limits) == ("subcontractor", 0.0, (0, 1, 2), None)
    assert sup.days_off == {D(2026, 11, 16): "unavailable", D(2026, 11, 17): "unavailable"}
    assert snap.holiday(D(2026, 12, 25)) == "1. Weihnachtstag"


def test_absence_kinds_become_days_off(repo):
    for aid, kind, a, b in [("abs0000001", "vacation", "2026-11-09", "2026-11-10"),
                            ("abs0000002", "sick", "2026-11-11", "2026-11-11"),
                            ("abs0000003", "holiday", "2026-11-12", "2026-11-12")]:  # fmt: skip
        repo.save("absences", {"id": aid, "user_id": EMP, "kind": kind, "start": a, "end": b})
    assert owner(load(repo, TODAY), EMP).days_off == {
        D(2026, 11, 9): "vacation",
        D(2026, 11, 10): "vacation",
        D(2026, 11, 11): "sick",
        D(2026, 11, 12): "other",
    }


def test_boss_limits_from_the_setting_and_defaults(repo):
    assert owner(load(repo, TODAY), BOSS).limits == WorkLimits()
    s = repo.settings()
    s[f"work_limits.{BOSS}"] = {"info_day": 9, "warn_day": 11, "max_day": 14, "info_week": 55, "info_average": 45}
    repo.save_settings(s)
    assert owner(load(repo, TODAY), BOSS).limits == WorkLimits(9.0, 11.0, 14.0, 55.0, 45.0)
    s[f"work_limits.{BOSS}"] = {"info_day": 8}  # partial: the rest keeps the defaults
    repo.save_settings(s)
    assert owner(load(repo, TODAY), BOSS).limits == WorkLimits(info_day=8.0)


def test_busy_comes_from_the_provider_in_ooo_mode(repo):
    calls = []

    class Fake:
        def busy_hours(self, user_id, start, end, *, ooo_only=False):
            calls.append((user_id, ooo_only))
            return {D(2026, 11, 10): 4.0} if user_id == EMP else {}

    snap = load(repo, TODAY, busy=Fake())
    assert owner(snap, EMP).busy == {D(2026, 11, 10): 4.0}
    assert sorted(calls) == [(BOSS, True), (EMP, True)]  # never for a subcontractor


def test_calendar_provider_ooo_only_counts_ooo_and_all_day_blocks(repo):
    repo.save("calendar_sources", {"id": "srcaaaaa01", "user_id": EMP, "name": "s", "kind": "ics_file", "url": None,
                                   "refresh_minutes": 360, "title_policy": "none", "all_day_blocks": True,
                                   "busy_default": True, "include_keywords": [], "exclude_keywords": [],
                                   "last_status": "ok", "event_count": 0})  # fmt: skip

    def ev(start, end, **kw):
        repo.save("calendar_events", {"source_id": "srcaaaaa01", "start_utc": start, "end_utc": end, "all_day": False,
                                       "busy": True, "ooo": False, "title": None, "categories": [], **kw})  # fmt: skip

    ev("2026-11-09T09:00:00Z", "2026-11-09T11:00:00Z")  # ordinary meeting
    ev("2026-11-10T09:00:00Z", "2026-11-10T12:00:00Z", ooo=True)  # out of office
    ev("2026-11-11", "2026-11-12", all_day=True)  # all-day block (8 h = the daily target)
    provider = CalendarBusyProvider(repo)
    both = provider.busy_hours(EMP, D(2026, 11, 9), D(2026, 11, 13))
    ooo = provider.busy_hours(EMP, D(2026, 11, 9), D(2026, 11, 13), ooo_only=True)
    assert both == {D(2026, 11, 9): 2.0, D(2026, 11, 10): 3.0, D(2026, 11, 11): 8.0}
    assert ooo == {D(2026, 11, 10): 3.0, D(2026, 11, 11): 8.0}
    assert owner(load(repo, TODAY, busy=provider), EMP).busy == ooo


# -- assignments --------------------------------------------------------------------------------------------
def test_assignment_kinds_and_cancelled_cut(repo):
    alloc(repo, "alloc00001", EMP)
    alloc(repo, "alloc00002", EMP, project=P2, hours_per_week=None, total_hours=30)
    alloc(repo, "alloc00003", EMP, status="cancelled", cancelled_from="2026-11-16")
    alloc(repo, "alloc00004", SUP, rate=75)
    alloc(repo, "alloc00005", GONE)  # inactive owner: out of the plan, not damaged
    snap = load(repo, TODAY)
    by = {a.id: a for a in snap.assignments}
    assert sorted(by) == ["alloc00001", "alloc00002", "alloc00003", "alloc00004"]
    assert (by["alloc00001"].project_kind, by["alloc00001"].hours_per_week) == ("customer", 20.0)
    a2 = by["alloc00002"]
    assert (a2.project_kind, a2.total_hours, a2.hours_per_week) == ("internal", 30.0, None)
    assert by["alloc00003"].cut == D(2026, 11, 16) and by["alloc00003"].start == D(2026, 11, 9)
    assert by["alloc00004"].owner_id == SUP
    assert snap.skipped == []


def test_cancelled_before_it_starts_is_dropped(repo):
    alloc(repo, "alloc00001", EMP, status="cancelled", cancelled_from="2026-11-09")
    assert load(repo, TODAY).assignments == []


def test_booked_hours_of_a_total_assignment(repo):
    alloc(repo, "alloc00001", EMP, hours_per_week=None, total_hours=30, start="2026-11-02", end="2026-11-27")
    alloc(repo, "alloc00002", EMP, project=P3, hours_per_week=10)
    entry(repo, 1, EMP, "2026-11-02", 120, P1)  # counts
    entry(repo, 2, EMP, "2026-11-05", 90, P1)  # counts
    entry(repo, 3, BOSS, "2026-11-04", 600, P1)  # another person
    entry(repo, 4, EMP, "2026-10-30", 60, P1)  # before the period
    entry(repo, 5, EMP, "2026-11-10", 60, P1)  # after today
    entry(repo, 6, EMP, "2026-11-03", 60, P3)  # another project
    by = {a.id: a for a in load(repo, TODAY).assignments}
    assert by["alloc00001"].booked == pytest.approx(3.5)
    assert by["alloc00002"].booked == 0.0  # weekly assignments carry no booked hours


# -- items --------------------------------------------------------------------------------------------------
def test_which_commitments_become_items(repo):
    contract(repo, "contr00001")
    contract(repo, "contr00002", status="done")
    offer(repo, "offer00001")  # sent with plan: yes
    offer(repo, "offer00002", status="draft")
    offer(repo, "offer00003", valid_until="2026-11-05")  # expired
    offer(repo, "offer00004")  # converted
    contract(repo, "contr00004", offer_id="offer00004", status="done")
    offer(repo, "offer00005", plan=None)
    snap = load(repo, TODAY)
    assert sorted((i.kind, i.id) for i in snap.items) == [("contract", "contr00001"), ("offer", "offer00001")]
    c, o = (next(i for i in snap.items if i.kind == k) for k in ("contract", "offer"))
    assert (c.hours_per_week, c.total_hours, c.start, c.end, c.project_id, c.win) == (
        10.0, None, D(2026, 11, 9), D(2026, 11, 27), P1, 1.0)  # fmt: skip
    assert (o.hours_per_week, o.start, o.end, o.title) == (8.0, D(2026, 12, 1), D(2026, 12, 18), "Offer A-001")


def test_offer_plan_without_hours_takes_the_h_lines(repo):
    lines = [{"description": "Work", "qty": 12, "unit": "h", "price": 90},
             {"description": "More", "qty": "8.5", "unit": "Std", "price": 90},
             {"description": "Licence", "qty": 3, "unit": "pcs", "price": 10}]  # fmt: skip
    offer(repo, "offer00001", lines=lines, plan={"start": "2026-12-01", "end": "2026-12-18"})
    (item,) = load(repo, TODAY).items
    assert (item.total_hours, item.hours_per_week) == (20.5, None)


def test_rate_fallback_chain(repo):
    contract(repo, "contr00001", rate=120)  # its own
    contract(repo, "contr00002", project_id=P3, customer_id=None)  # the project's (100)
    contract(repo, "contr00003")  # the customer's (90)
    contract(repo, "contr00004", project_id=None, customer_id=None)  # default_rate (70), unassigned scope
    rates = {i.id: (i.rate, i.project_id) for i in load(repo, TODAY).items}
    assert rates == {
        "contr00001": (Decimal("120"), P1),
        "contr00002": (Decimal("100"), P3),
        "contr00003": (Decimal("90"), P1),
        "contr00004": (Decimal("70"), None),
    }
    offer(repo, "offer00001", plan={"start": "2026-12-01", "end": "2026-12-18", "hours_per_week": 8, "rate": 111})
    assert next(i for i in load(repo, TODAY).items if i.id == "offer00001").rate == Decimal("111")


def test_win_probability_default_and_plan_value(repo):
    offer(repo, "offer00001")  # setting 50
    offer(repo, "offer00002", plan={"start": "2026-12-01", "end": "2026-12-18", "hours_per_week": 8,
                                    "win_probability": 25})  # fmt: skip
    wins = {i.id: i.win for i in load(repo, TODAY).items}
    assert wins == {"offer00001": 0.5, "offer00002": 0.25}


# -- bookings, internal projects, invoiced, cost -------------------------------------------------------------
def test_bookings_per_project_and_day_up_to_today(repo):
    entry(repo, 1, EMP, "2026-11-04", 90, P1)
    entry(repo, 2, BOSS, "2026-11-04", 30, P1)  # same project and day: summed
    entry(repo, 3, EMP, "2026-11-06", 60, P1)  # today counts
    entry(repo, 4, EMP, "2026-11-07", 60, P1)  # after today
    entry(repo, 5, EMP, "2026-11-04", 120, P3)
    entry(repo, 6, EMP, "2026-11-04", 45, None)  # no project
    entry(repo, 7, EMP, "2026-11-03", 30, P2)
    snap = load(repo, TODAY)
    assert [(b.project_id, b.day, b.hours) for b in snap.bookings] == [
        (P2, D(2026, 11, 3), 0.5),
        (P1, D(2026, 11, 4), 2.0),
        (P3, D(2026, 11, 4), 2.0),
        (P1, D(2026, 11, 6), 1.0),
    ]
    assert snap.internal == [{"project_id": P2, "name": "Administration", "budget_hours": 10.0, "booked": 0.5}]
    assert snap.names == {P1: "Orchard", P2: "Administration", P3: "Pine"}


def test_invoiced_is_attributed_to_the_contract_whose_period_contains_the_date(repo):
    contract(repo, "contr00001", start="2026-10-01", end="2026-10-31")
    contract(repo, "contr00002", start="2026-11-01", end="2026-11-30")
    contract(repo, "contr00003", start="2026-11-01", end="2026-11-20")  # overlaps: the earlier end wins
    contract(repo, "contr00004", project_id=P3, start="2026-11-01", end="2026-11-30")

    def inv(iid, day, status="sent", project=P1, price=100, qty=1):
        repo.save("invoices", {"id": iid, "status": status, "date": day, "project_id": project, "customer_id": CUST,
                               "vat_rate": 19, "lines": [{"description": "x", "qty": qty, "price": price}]})  # fmt: skip

    inv("inv0000001", "2026-10-15")  # contr1: 100
    inv("inv0000002", "2026-11-10", price=50, qty=3)  # contr3 (ends first): 150
    inv("inv0000003", "2026-11-25", status="paid", price=33.33)  # only contr2 contains it: 33.33
    inv("inv0000004", "2026-11-10", status="draft")  # not counted
    inv("inv0000005", "2026-11-10", status="cancelled")  # not counted
    inv("inv0000006", "2026-09-10")  # no contract contains it
    inv("inv0000007", "2026-11-10", project=P3, price=10)  # contr4: 10
    assert load(repo, TODAY).invoiced == {
        "contr00001": Decimal("100.00"),
        "contr00003": Decimal("150.00"),
        "contr00002": Decimal("33.33"),
        "contr00004": Decimal("10.00"),
    }


def test_subcontractor_cost_per_project(repo):
    # weekly 12 h over 2026-11-09..11-20: SUP works Mon-Wed; 11-16 and 11-17 are unavailable -> week 1: 12 h,
    # week 2: all 12 h on Wednesday 11-18 -> 24 h x 75 = 1800.00
    alloc(repo, "alloc00001", SUP, hours_per_week=12, start="2026-11-09", end="2026-11-20", rate=75)
    # total 10 h x 80 on P3
    alloc(repo, "alloc00002", SUP, project=P3, hours_per_week=None, total_hours=10, rate=80)
    alloc(repo, "alloc00003", EMP)  # an employee: no cost
    assert load(repo, TODAY).subcontractor_cost == {P1: Decimal("1800.00"), P3: Decimal("800.00")}


# -- damaged rows ---------------------------------------------------------------------------------------------
def test_damaged_rows_are_skipped_and_listed(repo, unguarded):
    alloc(repo, "alloc00001", EMP)
    contract(repo, "contr00001")
    plant = unguarded
    plant(repo, "contracts", {"id": "contr00002", "title": "t", "project_id": P1, "status": "confirmed", "start": "2026-11-20",
                              "end": "2026-11-10", "hours_per_week": 5}, "state.period_reversed")  # fmt: skip
    plant(repo, "contracts", {"id": "contr00003", "title": "t", "project_id": P1, "status": "confirmed", "start": "2026-11-09",
                              "end": "2026-11-20", "hours_per_week": 5, "total_hours": 20},
          "state.exactly_one")  # fmt: skip
    plant(repo, "allocations", {"id": "alloc00002", "user_id": EMP, "project_id": P1, "start": "2026-11-09",
                                "end": "2026-11-20", "status": "active"}, "state.exactly_one")  # fmt: skip
    plant(repo, "allocations", {"id": "alloc00003", "user_id": EMP, "project_id": P1, "start": "2026-11-09",
                                "end": "2026-11-20", "hours_per_week": 8, "total_hours": 8, "status": "active"},
          "state.exactly_one")  # fmt: skip
    plant(repo, "allocations", {"id": "alloc00004", "user_id": EMP, "start": "2026-11-09", "end": "2026-11-20",
                                "hours_per_week": 8, "status": "active"},
          ("state.required", "ref.dangling"))  # fmt: skip
    snap = load(repo, TODAY)
    assert [a.id for a in snap.assignments] == ["alloc00001"]
    assert [i.id for i in snap.items] == ["contr00001"]
    assert sorted((s["coll"], s["id"]) for s in snap.skipped) == [
        ("allocations", "alloc00002"),
        ("allocations", "alloc00003"),
        ("allocations", "alloc00004"),
        ("contracts", "contr00002"),
        ("contracts", "contr00003"),
    ]
    assert all(s["reason"] for s in snap.skipped)


# -- changes ---------------------------------------------------------------------------------------------------
def test_changes_apply_in_memory_without_writing(repo):
    contract(repo, "contr00001")
    offer(repo, "offer00001")
    alloc(repo, "alloc00001", EMP)
    alloc(repo, "alloc00002", EMP, project=P3)
    before = {c: repo.list(c) for c in ("absences", "contracts", "offers", "allocations")}
    changes = {
        "absences": [{"user_id": EMP, "kind": "vacation", "start": "2026-11-16", "end": "2026-11-17"}],
        "commitments": [
            {"kind": "contract", "id": "contr00001", "hours_per_week": 25, "end": "2026-12-04"},
            {"kind": "offer", "id": "offer00001", "total_hours": 40},
        ],
        "allocations": [
            {"id": "alloc00001", "hours_per_week": 12},
            {"id": "alloc00002", "delete": True},
            {"user_id": BOSS, "project_id": P3, "start": "2026-11-09", "end": "2026-11-13", "hours_per_week": 5},
        ],
    }
    snap = load(repo, TODAY, changes=changes)
    assert owner(snap, EMP).days_off == {D(2026, 11, 16): "vacation", D(2026, 11, 17): "vacation"}
    c = next(i for i in snap.items if i.id == "contr00001")
    assert (c.hours_per_week, c.total_hours, c.end) == (25.0, None, D(2026, 12, 4))
    o = next(i for i in snap.items if i.id == "offer00001")
    assert (o.hours_per_week, o.total_hours) == (None, 40.0)  # switching the kind of hours replaces the other
    by = {a.id: a for a in snap.assignments}
    assert by["alloc00001"].hours_per_week == 12.0 and "alloc00002" not in by
    (new,) = (a for a in snap.assignments if a.id not in ("alloc00001", "alloc00002"))
    assert (new.owner_id, new.project_id, new.hours_per_week) == (BOSS, P3, 5.0)
    assert {c: repo.list(c) for c in before} == before  # nothing written


def test_changes_edit_a_total_hours_allocation_in_memory(repo):
    alloc(repo, "alloc00001", EMP, hours_per_week=None, total_hours=60)
    alloc(repo, "alloc00002", EMP, project=P3)
    before = repo.list("allocations")
    snap = load(repo, TODAY, changes={"allocations": [
        {"id": "alloc00001", "total_hours": 80, "start": "2026-11-10", "end": "2026-11-30"},
        {"id": "alloc00002", "total_hours": 30},  # switching the kind clears hours_per_week
    ]})  # fmt: skip
    by = {a.id: a for a in snap.assignments}
    a1, a2 = by["alloc00001"], by["alloc00002"]
    assert (a1.hours_per_week, a1.total_hours, a1.start, a1.end) == (None, 80.0, D(2026, 11, 10), D(2026, 11, 30))
    assert (a2.hours_per_week, a2.total_hours) == (None, 30.0)
    snap = load(repo, TODAY, changes={"allocations": [{"id": "alloc00001", "hours_per_week": 10}]})
    a1 = next(a for a in snap.assignments if a.id == "alloc00001")
    assert (a1.hours_per_week, a1.total_hours) == (10.0, None)
    assert repo.list("allocations") == before


def test_changes_to_an_unknown_allocation_raise_not_found(repo):
    with pytest.raises(ServiceError) as e:
        load(repo, TODAY, changes={"allocations": [{"id": "nothere001", "delete": True}]})
    assert e.value.status == 404
