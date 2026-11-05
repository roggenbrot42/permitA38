"""Planner engine WP2: spread and supply. Literal numbers of docs/specs/project-centric-planning.md (E1-E5d)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from permita38.core.holidays import holidays
from permita38.core.service.planner import supply
from permita38.core.service.planner.model import Assignment, Owner, WorkLimits
from permita38.core.service.planner.spread import capacity, planned_hours, planned_net, spread

_CACHE: dict[int, dict] = {}


def by_holiday(d: date) -> str | None:
    if d.year not in _CACHE:
        _CACHE[d.year] = holidays(d.year, "BY")
    return _CACHE[d.year].get(d)


def no_holiday(d: date) -> str | None:
    return None


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def person(oid="p", name="Pat", kind="employee", hours=40.0, off=None, **kw) -> Owner:
    return Owner(
        id=oid,
        name=name,
        kind=kind,
        weekly_hours=hours if kind != "subcontractor" else 0.0,
        workdays=(0, 1, 2, 3, 4),
        days_off=off or {},
        **kw,
    )


def weekly(oid, project, start, end, hpw, kind="customer", aid=None) -> Assignment:
    return Assignment(aid or f"{oid}-{project}", oid, project, kind, start, end, hpw, None)


def total(oid, project, start, end, hours, booked=0.0) -> Assignment:
    return Assignment(f"{oid}-{project}", oid, project, "customer", start, end, None, hours, booked)


def codes(ws: list[dict]) -> list[str]:
    return [w["code"] for w in ws]


def approx(x):
    return pytest.approx(x, abs=1e-6)


# ---------------------------------------------------------------- E1 / E2


def e1_setup():
    ada = person("ada", "Ada")
    sam = person("sam", "Sam", kind="subcontractor")
    a = date(2026, 11, 9)
    end = date(2026, 12, 18)
    assigns = [
        weekly("ada", "admin", a, end, 30.0, kind="internal"),
        weekly("ada", "orchard", a, end, 4.0),
        total("sam", "orchard", a, date(2026, 12, 4), 24.0),
    ]
    return [ada, sam], assigns


def test_e1_supply_figures():
    owners, assigns = e1_setup()
    sup = supply.build(owners, assigns, by_holiday, date(2026, 11, 8), date(2026, 11, 9), date(2026, 12, 18))
    orchard = sup.project["orchard"]
    for d in days(date(2026, 11, 9), date(2026, 12, 4)):
        if d.weekday() < 5:
            assert orchard[d] == (approx(0.8), approx(1.2))
    for d in days(date(2026, 12, 7), date(2026, 12, 18)):
        if d.weekday() < 5:
            assert orchard[d] == (approx(0.8), 0.0)
    emp = sum(v[0] for v in orchard.values())
    subs = sum(v[1] for v in orchard.values())
    assert emp == approx(24.0)
    assert subs == approx(24.0)
    # Ada: week of 09.11.: capacity 40, customer 4, internal 30, unassigned 6
    week = [sup.people["ada"][d] for d in days(date(2026, 11, 9), date(2026, 11, 13))]
    assert sum(p.capacity for p in week) == approx(40.0)
    assert sum(p.assigned_customer for p in week) == approx(4.0)
    assert sum(p.assigned_internal for p in week) == approx(30.0)
    assert sum(sup.unassigned("ada", d) for d in days(date(2026, 11, 9), date(2026, 11, 13))) == approx(6.0)
    assert "sam" not in sup.people
    assert supply.warnings(owners, assigns, by_holiday, date(2026, 11, 8), date(2026, 11, 9), date(2026, 12, 18)) == []


def test_e1_per_day_spread_and_planned_cost():
    owners, assigns = e1_setup()
    ada, sam = owners
    s = spread(sam, assigns[2], by_holiday, date(2026, 11, 8))
    assert len(s) == 20
    assert all(v == approx(1.2) for v in s.values())
    assert sum(s.values()) == approx(24.0)
    assert planned_net(sam, assigns[2], by_holiday, Decimal("90.00")) == Decimal("2160.00")
    assert planned_hours(sam, assigns[2], by_holiday) == 24.0
    ada_orchard = spread(ada, assigns[1], by_holiday, date(2026, 11, 8))
    assert all(v == approx(0.8) for v in ada_orchard.values())
    assert len(ada_orchard) == 30


def test_e2_total_assignment_counts_from_day_after_today():
    owners, assigns = e1_setup()
    sam = owners[1]
    s = spread(sam, assigns[2], by_holiday, date(2026, 11, 15))
    assert min(s) == date(2026, 11, 16)
    assert len(s) == 15
    assert all(v == approx(1.6) for v in s.values())


def test_total_with_booked_reduces_open_hours():
    ada = person("ada")
    a = total("ada", "x", date(2026, 11, 9), date(2026, 11, 13), 20.0, booked=10.0)
    s = spread(ada, a, by_holiday, date(2026, 11, 10))
    assert sorted(s) == days(date(2026, 11, 11), date(2026, 11, 13))
    assert sum(s.values()) == approx(10.0)
    # booked more than promised: nothing left
    assert (
        spread(
            ada,
            total("ada", "x", date(2026, 11, 9), date(2026, 11, 13), 5.0, booked=9.0),
            by_holiday,
            date(2026, 11, 8),
        )
        == {}
    )
    # today=None: the whole period, booked ignored
    assert sum(spread(ada, a, by_holiday, None).values()) == approx(20.0)


# ---------------------------------------------------------------- spread edge cases


def test_planned_hours_without_today_counts_the_past():
    ada = person("ada")
    a = weekly("ada", "x", date(2026, 11, 9), date(2026, 11, 20), 10.0)
    assert planned_hours(ada, a, by_holiday) == 20.0
    assert sum(spread(ada, a, by_holiday, date(2026, 11, 15)).values()) == approx(10.0)


def test_planned_net_of_total_assignment_and_weekly():
    sam = person("sam", "Sam", kind="subcontractor")
    t = total("sam", "orchard", date(2026, 11, 9), date(2026, 12, 4), 24.0)
    assert planned_net(sam, t, by_holiday, Decimal("90.00")) == Decimal("2160.00")
    w = weekly("sam", "orchard", date(2026, 11, 9), date(2026, 11, 20), 6.0)
    assert planned_hours(sam, w, by_holiday) == 12.0
    assert planned_net(sam, w, by_holiday, Decimal("90.00")) == Decimal("1080.00")


def test_assignment_ending_mid_week_gets_its_share():
    ada = person("ada")
    # ends Wednesday: 10 h / 5 available days = 2 per day on 3 days
    a = weekly("ada", "x", date(2026, 11, 9), date(2026, 11, 11), 10.0)
    s = spread(ada, a, by_holiday, None)
    assert sorted(s) == days(date(2026, 11, 9), date(2026, 11, 11))
    assert all(v == approx(2.0) for v in s.values())


def test_owner_without_available_day_in_week_is_empty():
    mon = date(2026, 11, 9)
    off = Owner("o", "Oli", "employee", 8.0, (0,), {mon: "vacation"})
    a = weekly("o", "x", mon, mon + timedelta(days=6), 8.0)
    assert spread(off, a, by_holiday, None) == {}
    assert planned_hours(off, a, by_holiday) == 0.0
    t = total("o", "x", mon, mon + timedelta(days=6), 8.0)
    assert spread(off, t, by_holiday, None) == {}
    sup = supply.build([off], [a], by_holiday, date(2026, 11, 8), mon, mon + timedelta(days=6))
    assert sup.unassigned("o", mon) == 0.0
    assert supply.warnings([off], [a], by_holiday, date(2026, 11, 8), mon, mon + timedelta(days=6)) == []


def test_capacity_rules():
    ada = person("ada", hours=40.0, busy={date(2026, 11, 9): 3.0, date(2026, 11, 10): 20.0})
    assert capacity(ada, date(2026, 11, 9), by_holiday) == 5.0
    assert capacity(ada, date(2026, 11, 10), by_holiday) == 0.0
    assert capacity(ada, date(2026, 11, 14), by_holiday) == 0.0
    assert capacity(person("s", kind="subcontractor"), date(2026, 11, 9), by_holiday) == 0.0


# ---------------------------------------------------------------- E4


def test_e4_over_capacity_message_and_numbers():
    cleo = person("cleo", "Cleo", hours=20.0)
    mon, fri = date(2026, 11, 9), date(2026, 11, 13)
    assigns = [
        weekly("cleo", "padmin", mon, fri, 12.0, kind="internal"),
        weekly("cleo", "pelm", mon, fri, 10.0),
    ]
    today = date(2026, 11, 8)
    sup = supply.build([cleo], assigns, by_holiday, today, mon, fri)
    assert sum(v[0] for v in sup.project["pelm"].values()) == approx(10.0)  # nothing scaled
    week = [sup.people["cleo"][d] for d in days(mon, fri)]
    assert sum(p.capacity for p in week) == approx(20.0)
    assert sum(p.assigned_internal for p in week) == approx(12.0)
    assert sum(p.assigned_customer for p in week) == approx(10.0)
    assert sum(sup.unassigned("cleo", d) for d in days(mon, fri)) == 0.0
    ws = supply.warnings([cleo], assigns, by_holiday, today, mon, fri, {"padmin": "Administration", "pelm": "Elm"})
    assert codes(ws) == ["assignment.over_capacity"]
    w = ws[0]
    assert w["severity"] == "warning"
    assert w["message"] == (
        "Week of 09.11.2026: Cleo is assigned 22 h (Administration 12 h, Elm 10 h) but has 20 h of capacity: "
        "over by 2 h. The projects still count the full hours."
    )
    d = w["details"]
    assert (d["user_id"], d["week"], d["assigned"], d["capacity"], d["over_by"]) == (
        "cleo",
        "2026-11-09",
        22.0,
        20.0,
        2.0,
    )
    assert d["reason"] == "weekly_hours"
    assert d["reasons"] == ["weekly_hours"]
    assert d["projects"] == [{"project_id": "padmin", "hours": 12.0}, {"project_id": "pelm", "hours": 10.0}]


# ---------------------------------------------------------------- E5 / E5c / E5d


def test_e5_holiday_and_vacation_move_hours():
    dan = person("dan", "Dan", off={date(2026, 12, 23): "vacation"})
    mon, sun = date(2026, 12, 21), date(2026, 12, 27)
    a = weekly("dan", "Ash", mon, sun, 20.0)
    today = date(2026, 12, 20)
    sup = supply.build([dan], [a], by_holiday, today, mon, sun)
    assert sum(v[0] for v in sup.project["Ash"].values()) == approx(20.0)
    assert sorted(sup.project["Ash"]) == [date(2026, 12, 21), date(2026, 12, 22), date(2026, 12, 24)]
    rows = [sup.people["dan"][d] for d in days(mon, sun)]
    assert sum(r.capacity for r in rows) == approx(24.0)
    assert sum(r.holiday for r in rows) == approx(8.0)
    assert sum(r.vacation for r in rows) == approx(8.0)
    assert sum(r.assigned_customer for r in rows) == approx(20.0)
    assert sum(sup.unassigned("dan", d) for d in days(mon, sun)) == approx(4.0)
    assert supply.warnings([dan], [a], by_holiday, today, mon, sun) == []


def test_e5_variant_two_vacation_days_over_by_four_time_off():
    dan = person("dan", "Dan", off={date(2026, 12, 22): "vacation", date(2026, 12, 23): "vacation"})
    mon, sun = date(2026, 12, 21), date(2026, 12, 27)
    a = weekly("dan", "Ash", mon, sun, 20.0)
    today = date(2026, 12, 20)
    sup = supply.build([dan], [a], by_holiday, today, mon, sun)
    assert sum(v[0] for v in sup.project["Ash"].values()) == approx(20.0)
    ws = supply.warnings([dan], [a], by_holiday, today, mon, sun)
    assert codes(ws) == ["assignment.over_capacity"]
    assert ws[0]["details"]["over_by"] == 4.0
    assert ws[0]["details"]["reason"] == "time_off"
    assert ws[0]["details"]["reasons"] == ["time_off"]


def test_e5c_week_partly_covered_and_week_with_holiday():
    for kind in ("employee", "subcontractor"):
        o = person("eve", "Eve", kind=kind)
        # ends Thu 19.11.2026, no holiday in BY that week: 10 / 5 = 2 on the 4 days inside the period = 8
        a = weekly("eve", "Ash", date(2026, 11, 2), date(2026, 11, 19), 10.0)
        s = spread(o, a, by_holiday, None)
        wk = sum(v for d, v in s.items() if date(2026, 11, 16) <= d <= date(2026, 11, 22))
        assert wk == approx(8.0)
        assert all(v == approx(2.0) for d, v in s.items() if d >= date(2026, 11, 16))
        # ends Thu 31.12.2026; 01.01.2027 is a holiday, so the week has 4 available days, all inside: 10
        a = weekly("eve", "Ash", date(2026, 12, 1), date(2026, 12, 31), 10.0)
        s = spread(o, a, by_holiday, None)
        wk = sum(v for d, v in s.items() if date(2026, 12, 28) <= d <= date(2027, 1, 3))
        assert wk == approx(10.0)
        # week of 21.12.2026 (25.12. holiday): 10 / 4 = 2.5 on 4 days = 10
        wk = sum(v for d, v in s.items() if date(2026, 12, 21) <= d <= date(2026, 12, 27))
        assert wk == approx(10.0)
        assert s[date(2026, 12, 21)] == approx(2.5)
    sam = person("s", kind="subcontractor")
    one_week = weekly("s", "Ash", date(2026, 12, 21), date(2026, 12, 27), 10.0)
    assert planned_net(sam, one_week, by_holiday, Decimal("90.00")) == Decimal("900.00")


def e5d_assign(hours: float, who: str) -> Assignment:
    return weekly(who, "Ash", date(2026, 12, 21), date(2026, 12, 27), hours)


def test_e5d_employee_boss_subcontractor():
    off = {date(2026, 12, 22): "vacation", date(2026, 12, 23): "vacation"}
    fay = person("fay", "Fay", off=off)
    boss = person("boss", "Bea", kind="boss", off=off)
    sam = person(
        "sam", "Sam", kind="subcontractor", off={date(2026, 12, 22): "unavailable", date(2026, 12, 23): "unavailable"}
    )
    mon, sun = date(2026, 12, 21), date(2026, 12, 27)
    today = date(2026, 12, 20)
    owners = [fay, boss, sam]
    assigns = [e5d_assign(40.0, "fay"), e5d_assign(40.0, "boss"), e5d_assign(40.0, "sam")]
    ws = supply.warnings(owners, assigns, by_holiday, today, mon, sun)
    by_user = {}
    for w in ws:
        by_user.setdefault(w["details"]["user_id"], []).append(w)
    assert "sam" not in by_user
    assert codes(by_user["fay"]) == ["assignment.over_capacity"]
    fw = by_user["fay"][0]
    assert fw["details"]["reason"] == "working_time_law"
    assert fw["details"]["reasons"] == ["working_time_law", "time_off"]
    assert "20 h planned on 21.12.2026; the law allows at most 10 h a day (§3 ArbZG)" in fw["message"]
    assert sorted(codes(by_user["boss"])) == [
        "assignment.impossible_day",
        "assignment.impossible_day",
        "assignment.over_capacity",
    ]
    bw = [w for w in by_user["boss"] if w["code"] == "assignment.over_capacity"][0]
    assert bw["details"]["reason"] == "time_off"
    assert bw["details"]["reasons"] == ["time_off"]
    imp = [w for w in by_user["boss"] if w["code"] == "assignment.impossible_day"]
    assert imp[0]["message"] == "20 h planned on 21.12.2026; that is more than your maximum of 16 h."
    assert imp[0]["severity"] == "warning"


def test_e5d_only_sam_gets_nothing():
    sam = person("sam", "Sam", kind="subcontractor", off={date(2026, 12, 22): "unavailable"})
    assert (
        supply.warnings(
            [sam], [e5d_assign(80.0, "sam")], by_holiday, date(2026, 12, 20), date(2026, 12, 21), date(2026, 12, 27)
        )
        == []
    )


def test_e5d_boss_24_hours_gives_long_day_only():
    off = {date(2026, 12, 22): "vacation", date(2026, 12, 23): "vacation"}
    boss = person("boss", "Bea", kind="boss", off=off)
    ws = supply.warnings(
        [boss], [e5d_assign(24.0, "boss")], by_holiday, date(2026, 12, 20), date(2026, 12, 21), date(2026, 12, 27)
    )
    assert sorted(codes(ws)) == ["assignment.long_day", "assignment.long_day", "assignment.over_capacity"]
    assert {w["severity"] for w in ws if w["code"] == "assignment.long_day"} == {"info"}


def test_boss_limits_week_and_very_long_day():
    boss = person(
        "boss",
        "Bea",
        kind="boss",
        hours=80.0,
        limits=WorkLimits(info_day=8.0, warn_day=9.0, max_day=40.0, info_week=30.0),
    )
    mon, fri = date(2026, 11, 9), date(2026, 11, 13)
    # 80 h capacity (16/day), assigned 50 h: 10 h per day
    ws = supply.warnings([boss], [weekly("boss", "x", mon, fri, 50.0)], by_holiday, date(2026, 11, 8), mon, fri)
    assert codes(ws).count("assignment.very_long_day") == 5
    assert "assignment.long_week" in codes(ws)
    assert all(w["severity"] == "warning" for w in ws if w["code"] == "assignment.very_long_day")
    assert next(w for w in ws if w["code"] == "assignment.long_week")["severity"] == "info"


# ---------------------------------------------------------------- 24-week average


def _weeks_assignment(hpw: float, oid="emp") -> Assignment:
    # 24 ISO weeks: Monday 11.01.2027 to Sunday 27.06.2027
    return weekly(oid, "Ash", date(2027, 1, 11), date(2027, 6, 27), hpw)


def test_employee_24_week_average_over_48_warns():
    emp = person("emp", "Eli", hours=60.0)
    today = date(2027, 1, 1)
    ws = supply.warnings([emp], [_weeks_assignment(50.0)], by_holiday, today, date(2027, 1, 11), date(2027, 1, 17))
    avg = [w for w in ws if w["code"] == "assignment.working_time"]
    assert len(avg) == 1
    assert avg[0]["details"]["average"] == 50.0
    assert avg[0]["details"]["week"] == "2027-06-21"  # first week whose 24 weeks average above 48
    assert avg[0]["severity"] == "warning"


def test_employee_24_week_average_of_exactly_48_does_not_warn():
    emp = person("emp", "Eli", hours=60.0)
    ws = supply.warnings(
        [emp], [_weeks_assignment(48.0)], by_holiday, date(2027, 1, 1), date(2027, 1, 11), date(2027, 1, 17)
    )
    assert "assignment.working_time" not in codes(ws)


def test_boss_high_average_is_info():
    boss = person("emp", "Bea", kind="boss", hours=60.0)
    ws = supply.warnings(
        [boss], [_weeks_assignment(50.0)], by_holiday, date(2027, 1, 1), date(2027, 1, 11), date(2027, 1, 17)
    )
    avg = [w for w in ws if w["code"] == "assignment.high_average"]
    assert len(avg) == 1 and avg[0]["severity"] == "info"
    assert "assignment.working_time" not in codes(ws)
