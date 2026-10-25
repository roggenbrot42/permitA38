"""Hand-computed oracle for the spreading of external allocations (docs/contracts/subcontractors.md W1-W3, W9, W11).
Every expected number is written out by hand; none is taken from the code under test."""

from datetime import date
from decimal import Decimal

from permita38.core.holidays import holidays
from permita38.core.service import external_capacity as ec


class Cal:
    """Public holidays of Berlin (no state-specific day in the windows used here)."""

    def __init__(self):
        self._h: dict = {}

    def holiday(self, day):
        if day.year not in self._h:
            self._h[day.year] = holidays(day.year, "BE")
        return self._h[day.year].get(day)


S = {"workdays": [0, 1, 2, 3], "weekly_hours": 24, "rate": 80.0}


def alloc(start, end, **kw):
    return {"start": start, "end": end, "rate": 80.0, "status": "active", **kw}


def test_w1_twenty_hours_four_workdays():
    f = ec.figures(S, alloc("2026-03-02", "2026-03-27", hours_per_week=20.0), Cal())
    assert len(f.days) == 16 and set(f.days.values()) == {5.0}
    assert f.planned_hours == 80.0 and f.planned_net == Decimal("6400.00")
    weeks = ec.weekly_hours(f.days)
    assert sorted(weeks.values()) == [20.0] * 4
    assert ec.week_cost(20.0, 80.0) == Decimal("1600.00")
    assert date(2026, 3, 6) not in f.days  # Friday is not a workday of S


def test_w2_easter_monday_is_not_made_up():
    f = ec.figures(S, alloc("2026-04-06", "2026-04-17", hours_per_week=20.0), Cal())
    weeks = ec.weekly_hours(f.days)
    assert weeks == {date(2026, 4, 6): 15.0, date(2026, 4, 13): 20.0}
    assert ec.week_cost(15.0, 80.0) == Decimal("1200.00")
    assert f.planned_hours == 35.0 and f.planned_net == Decimal("2800.00")


def test_w3_total_hours_with_unavailable_window():
    s = {
        "workdays": [0, 1, 2, 3, 4],
        "weekly_hours": 40,
        "unavailable": [{"from": "2026-03-09", "to": "2026-03-11"}],
    }
    a = alloc("2026-03-02", "2026-03-20", total_hours=100.0, rate=75.0)
    f = ec.figures(s, a, Cal())
    assert len(f.days) == 12  # 15 weekdays - 3 unavailable
    weeks = ec.weekly_hours(f.days)
    assert [round(weeks[date(2026, 3, d)], 6) for d in (2, 9, 16)] == [41.666667, 16.666667, 41.666667]
    assert [ec.week_cost(weeks[date(2026, 3, d)], 75.0) for d in (2, 9, 16)] == [
        Decimal("3125.00"),
        Decimal("1250.00"),
        Decimal("3125.00"),
    ]
    assert f.planned_hours == 100.0 and f.planned_net == Decimal("7500.00")


def test_w9_cancelled_keeps_the_days_before_the_cut():
    a = alloc("2026-03-02", "2026-03-27", hours_per_week=20.0, status="cancelled", cancelled_from="2026-03-13")
    f = ec.figures(S, a, Cal())
    assert len(f.days) == 8 and f.planned_hours == 40.0 and f.planned_net == Decimal("3200.00")
    assert max(f.days) == date(2026, 3, 12)


def test_cancelled_total_hours_is_pro_rata():
    s = {"workdays": [0, 1, 2, 3, 4]}
    a = alloc("2026-03-02", "2026-03-13", total_hours=100.0, rate=50.0, status="cancelled", cancelled_from="2026-03-09")
    f = ec.figures(s, a, Cal())  # 10 days originally, 5 kept: 100 x 5 / 10 = 50 h
    assert f.planned_hours == 50.0 and f.planned_net == Decimal("2500.00")


def test_no_available_day_books_nothing():
    s = {"workdays": [5, 6]}
    f = ec.figures(s, alloc("2026-03-02", "2026-03-06", total_hours=10.0), Cal())
    assert f.days == {} and f.planned_net == Decimal("0")


def test_w11_two_allocations_sum_to_28_hours_in_the_week():
    a = ec.allocation_days(S, alloc("2026-03-02", "2026-03-06", hours_per_week=20.0), Cal())
    b = ec.allocation_days(S, alloc("2026-03-02", "2026-03-06", hours_per_week=8.0), Cal())
    week = date(2026, 3, 2)
    assert ec.weekly_hours(a)[week] + ec.weekly_hours(b)[week] == 28.0
    single = ec.allocation_days(S, alloc("2026-03-02", "2026-03-06", hours_per_week=25.0), Cal())
    assert ec.weekly_hours(single)[week] == 25.0
