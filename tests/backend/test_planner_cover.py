"""Planner covering (WP3 cover.py): worked examples E1, E2, E3, E5b, E7 of docs/specs/project-centric-planning.md."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from permita38.core.service.planner.cover import cover
from permita38.core.service.planner.model import Assignment, Booking, Item, Owner
from permita38.core.service.planner.supply import build

NAMES = {"orchard": "Orchard", "pine": "Pine", "adm": "Administration"}
WEEK = (0, 1, 2, 3, 4)


def _no_holiday(_d: date) -> str | None:
    return None


def _ada() -> Owner:
    return Owner("ada", "Ada", "employee", 40.0, WEEK)


def _sam() -> Owner:
    return Owner("sam", "Sam", "subcontractor", 0.0, WEEK)


def _asg(aid, owner, project, kind, start, end, hpw=None, total=None, booked=0.0) -> Assignment:
    return Assignment(aid, owner, project, kind, start, end, hpw, total, booked)


def _item(kind, iid, project, start, end, hpw=None, total=None, rate="120.00", win=1.0) -> Item:
    return Item(kind, iid, iid, project, start, end, hpw, total, Decimal(rate), win)


D = date
START, END, SAM_END = D(2026, 11, 9), D(2026, 12, 18), D(2026, 12, 4)


def _orchard(today: date, extra_items=(), bookings=()):
    owners = [_ada(), _sam()]
    assignments = [
        _asg("a1", "ada", "adm", "internal", START, END, hpw=30.0),
        _asg("a2", "ada", "orchard", "customer", START, END, hpw=4.0),
        _asg("a3", "sam", "orchard", "customer", START, SAM_END, total=24.0),
    ]
    supply = build(owners, assignments, _no_holiday, today, today, D(2026, 12, 31))
    items = [
        _item("contract", "K1", "orchard", START, END, hpw=4.0),
        _item("contract", "K2", "orchard", START, SAM_END, total=50.0),
        *extra_items,
    ]
    return cover(items, list(bookings), supply, owners, today, NAMES)


def _by_id(verdict):
    return {i.id: i for i in verdict.items}


def test_e1_weekly_and_total_contract():
    (v,) = _orchard(D(2026, 11, 8))
    items = _by_id(v)
    k1, k2 = items["K1"], items["K2"]
    assert k1.shortfall == 0
    assert k1.explanation == "4 h per week from 09.11.2026 to 18.12.2026: covered every week."
    assert k2.promised == 50 and k2.booked == 0 and k2.remaining == 50
    assert k2.covered == 24 and k2.shortfall == 26
    assert k2.explanation == (
        "50 h promised − 0 h booked = 50 h remaining. Until 04.12.2026 Orchard has 24 h of assigned time "
        "left after its weekly contracts. Short 26 h."
    )
    assert (v.supply_employees, v.supply_subcontractors) == (24, 24)
    assert v.remaining_contracts == 74 and v.shortfall_contracts == 26
    assert v.status == "short"
    assert k1.value == Decimal("2880.00") and k2.value == Decimal("6000.00")
    assert k1.win_probability is None
    assert k2.suggestions == [
        {"user_id": "ada", "name": "Ada", "hours": 24.0, "from": "2026-11-09", "to": "2026-12-04"}
    ]
    assert k1.suggestions == []


def test_e2_booked_hours_attributed_to_weekly_first():
    bookings = [Booking("orchard", D(2026, 11, 9) + (D(2026, 11, 10) - D(2026, 11, 9)) * i, 2.0) for i in range(5)]
    (v,) = _orchard(D(2026, 11, 15), bookings=bookings)
    items = _by_id(v)
    assert items["K1"].booked == 4
    assert items["K2"].booked == 6
    assert items["K2"].remaining == 44 and items["K2"].covered == 24 and items["K2"].shortfall == 20
    assert items["K1"].remaining == 20 and items["K1"].shortfall == 0


def test_e3_earliest_end_first():
    owners = [Owner("ben", "Ben", "employee", 40.0, WEEK)]
    asg = [_asg("b1", "ben", "pine", "customer", START, D(2026, 12, 4), hpw=10.0)]
    today = D(2026, 11, 8)
    supply = build(owners, asg, _no_holiday, today, today, D(2026, 12, 31))
    items = [
        _item("contract", "T2", "pine", START, D(2026, 12, 4), total=20.0),
        _item("contract", "T1", "pine", START, D(2026, 11, 27), total=25.0),
    ]
    (v,) = cover(items, [], supply, owners, today, NAMES)
    by = _by_id(v)
    assert [i.id for i in v.items] == ["T1", "T2"]
    assert by["T1"].shortfall == 0
    assert by["T2"].covered == 15 and by["T2"].shortfall == 5


def test_e5b_weekly_demand_counts_weekdays_inside_period():
    owners = [_ada()]
    today = D(2026, 11, 8)
    supply = build(owners, [], _no_holiday, today, today, D(2026, 12, 31))
    it = _item("contract", "W", "orchard", D(2026, 11, 11), D(2026, 11, 20), hpw=10.0)
    (v,) = cover([it], [], supply, owners, today, NAMES)
    w = v.items[0]
    assert w.weekly_shortfalls[0].week == D(2026, 11, 9)
    assert w.weekly_shortfalls[0].demand == 6
    assert w.weekly_shortfalls[1].demand == 10  # 16.11.-20.11. is a full week
    assert w.promised == 16


def test_e7_offer_after_contracts():
    offer = _item("offer", "O1", "orchard", D(2026, 11, 16), SAM_END, total=10.0, win=0.5)
    (v,) = _orchard(D(2026, 11, 8), extra_items=[offer])
    o1 = _by_id(v)["O1"]
    assert o1.shortfall == 10 and o1.covered == 0
    assert o1.win_probability == 50.0
    assert o1.explanation.startswith("10 h offered − 0 h booked = 10 h remaining. Until 04.12.2026 Orchard has 0 h")
    assert "after its contracts. Short 10 h." in o1.explanation
    assert v.shortfall_contracts == 26 and v.shortfall_worst_case == 36 and v.shortfall_expected == 31
    assert v.remaining_offers == 10
    assert v.status == "short"


def test_weekly_shortfall_text_and_list():
    owners = [_ada()]
    asg = [_asg("a", "ada", "orchard", "customer", START, D(2026, 11, 20), hpw=3.0)]
    today = D(2026, 11, 8)
    supply = build(owners, asg, _no_holiday, today, today, D(2026, 12, 31))
    it = _item("contract", "W", "orchard", START, D(2026, 11, 20), hpw=5.0)
    (v,) = cover([it], [], supply, owners, today, NAMES)
    w = v.items[0]
    assert w.shortfall == 4 and w.covered == 6 and w.remaining == 10
    assert [(s.week, s.demand, s.supply, s.shortfall) for s in w.weekly_shortfalls] == [
        (D(2026, 11, 9), 5, 3, 2),
        (D(2026, 11, 16), 5, 3, 2),
    ]
    assert w.explanation == "5 h per week from 09.11.2026 to 20.11.2026: short 4 h in 2 weeks (09.11., 16.11.)."
    assert v.status == "short"


def test_item_without_project_uses_unassigned_time():
    owners = [Owner("cleo", "Cleo", "employee", 40.0, WEEK)]
    today = D(2026, 11, 8)
    supply = build(owners, [], _no_holiday, today, today, D(2026, 12, 31))
    ok = _item("contract", "U1", None, START, D(2026, 11, 13), total=10.0)
    (v,) = cover([ok], [], supply, owners, today, NAMES)
    assert v.project_id is None
    assert v.items[0].shortfall == 0
    assert v.supply_employees == 312 and v.supply_subcontractors == 0  # 39 workdays x 8 h to 31.12.
    assert "the team's unassigned time has 40 h of assigned time left" in v.items[0].explanation

    big = _item("contract", "U2", None, START, D(2026, 11, 13), total=50.0)
    (v,) = cover([big], [], supply, owners, today, NAMES)
    assert v.items[0].covered == 40 and v.items[0].shortfall == 10
    assert v.items[0].suggestions[0]["name"] == "Cleo" and v.items[0].suggestions[0]["hours"] == 40


def test_booked_hours_exceeding_contracts_are_unattributed():
    owners = [_ada()]
    today = D(2026, 11, 15)
    supply = build(owners, [], _no_holiday, today, today, D(2026, 12, 31))
    it = _item("contract", "W", "orchard", START, D(2026, 11, 13), hpw=4.0)
    bookings = [Booking("orchard", D(2026, 11, 10), 10.0), Booking("orchard", D(2026, 11, 11), 3.0)]
    (v,) = cover([it], bookings, supply, owners, today, NAMES)
    assert v.items[0].booked == 4 and v.items[0].remaining == 0
    assert v.booked_unattributed == 9


def test_internal_only_project_does_not_appear_and_projects_sorted_by_name():
    owners = [_ada()]
    asg = [
        _asg("a", "ada", "adm", "internal", START, END, hpw=30.0),
        _asg("b", "ada", "orchard", "customer", START, END, hpw=2.0),
        _asg("c", "ada", "pine", "customer", START, END, hpw=2.0),
    ]
    today = D(2026, 11, 8)
    supply = build(owners, asg, _no_holiday, today, today, D(2026, 12, 31))
    items = [
        _item("contract", "X", None, START, END, total=1.0),
        _item("contract", "P", "pine", START, END, hpw=2.0),
        _item("contract", "O", "orchard", START, END, hpw=2.0),
    ]
    verdicts = cover(items, [], supply, owners, today, NAMES)
    assert [v.project_id for v in verdicts] == ["orchard", "pine", None]
    assert all(v.status == "ok" for v in verdicts)


# ---------------------------------------------------------------- architect decisions after the review (2026-11-05)

WK = (D(2026, 11, 9), D(2026, 11, 13))


def _one(owner_hpw, items, bookings, today, hi=D(2026, 12, 31), project="orchard"):
    owners = [_ada()]
    asg = [_asg("a", "ada", project, "customer", START, D(2026, 11, 20), hpw=owner_hpw)]
    supply = build(owners, asg, _no_holiday, today, today, hi)
    (v,) = cover(items, bookings, supply, owners, today, NAMES)
    return v, _by_id(v)


def test_mid_week_today_remaining_covered_shortfall():
    today = D(2026, 11, 11)
    it = _item("contract", "W", "orchard", *WK, hpw=20.0)
    bookings = [Booking("orchard", D(2026, 11, d), 2.0) for d in (9, 10, 11)]
    _, items = _one(20.0, [it], bookings, today)
    w = items["W"]
    assert (w.remaining, w.covered, w.shortfall) == (14, 8, 6)


def test_weekly_value_has_no_float_noise():
    it = _item("contract", "W", "orchard", D(2026, 11, 9), D(2026, 11, 29), hpw=4.3, rate="7.05")
    _, items = _one(10.0, [it], [], D(2026, 11, 8))
    assert items["W"].value == Decimal("90.95")


def test_booking_outside_every_item_period_is_ignored():
    it = _item("contract", "C", "orchard", D(2026, 11, 16), D(2026, 11, 20), total=5.0)
    v, items = _one(5.0, [it], [Booking("orchard", D(2026, 11, 2), 5.0)], D(2026, 11, 15))
    assert v.booked_unattributed == 0 and items["C"].booked == 0


def test_total_items_take_bookings_contracts_first_then_earliest_end():
    offer = _item("offer", "O", "orchard", D(2026, 11, 9), D(2026, 11, 11), total=5.0)
    contract = _item("contract", "C", "orchard", D(2026, 11, 9), D(2026, 11, 20), total=5.0)
    later = _item("contract", "L", "orchard", D(2026, 11, 9), D(2026, 11, 27), total=5.0)
    _, items = _one(10.0, [offer, later, contract], [Booking("orchard", D(2026, 11, 10), 7.0)], D(2026, 11, 11))
    assert (items["C"].booked, items["L"].booked, items["O"].booked) == (5, 2, 0)


def test_ended_total_item_explanation():
    it = _item("contract", "T", "orchard", *WK, total=10.0)
    _, items = _one(0.0, [it], [Booking("orchard", D(2026, 11, 10), 4.0)], D(2026, 11, 15))
    t = items["T"]
    assert (t.remaining, t.shortfall) == (6, 6)
    assert t.explanation == "10 h promised − 4 h booked = 6 h remaining. Ended on 13.11.2026 with 6 h not delivered."


def test_supply_ending_before_item_raises():
    it = _item("contract", "T", "orchard", START, D(2026, 12, 18), total=10.0)
    owners = [_ada()]
    supply = build(owners, [], _no_holiday, D(2026, 11, 8), D(2026, 11, 8), D(2026, 12, 4))
    with pytest.raises(ValueError, match="supply ends before item T"):
        cover([it], [], supply, owners, D(2026, 11, 8), NAMES)


def test_c1_weekly_takes_proportionally_before_total():
    weekly_c = _item("contract", "W", "orchard", *WK, hpw=5.0)
    total_c = _item("contract", "T", "orchard", D(2026, 11, 11), D(2026, 11, 13), total=4.0)
    _, items = _one(10.0, [weekly_c, total_c], [], D(2026, 11, 8))
    assert (items["T"].covered, items["T"].shortfall) == (3, 1)


def test_two_weekly_contracts_share_shortfall_pro_rata():
    a = _item("contract", "A", "orchard", *WK, hpw=6.0)
    b = _item("contract", "B", "orchard", *WK, hpw=4.0)
    _, items = _one(5.0, [a, b], [], D(2026, 11, 8))
    assert (items["A"].shortfall, items["B"].shortfall) == (3, 2)


def test_weekly_offer_comes_after_contracts():
    c = _item("contract", "C", "orchard", *WK, hpw=6.0)
    o = _item("offer", "O", "orchard", *WK, hpw=6.0)
    _, items = _one(10.0, [c, o], [], D(2026, 11, 8))
    assert (items["C"].shortfall, items["O"].shortfall) == (0, 2)


def test_total_items_earliest_end_not_earliest_start():
    t1 = _item("contract", "T1", "orchard", D(2026, 11, 9), D(2026, 11, 20), total=10.0)
    t2 = _item("contract", "T2", "orchard", D(2026, 11, 12), D(2026, 11, 13), total=4.0)
    _, items = _one(10.0, [t1, t2], [], D(2026, 11, 8))
    assert (items["T1"].shortfall, items["T2"].shortfall) == (0, 0)
