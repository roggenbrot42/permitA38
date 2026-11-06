"""Spreading an assignment over days (rules S1, S2). Pure functions."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from permita38.core.money import dec, q2
from permita38.core.service.planner.model import Assignment, Holiday, Owner


def is_available(owner: Owner, day: date, holiday: Holiday) -> bool:
    """A workday that is neither a public holiday nor a day off / unavailable day."""
    return day.weekday() in owner.workdays and holiday(day) is None and day not in owner.days_off


def capacity(owner: Owner, day: date, holiday: Holiday) -> float:
    """Real hours of a person on a day; 0 for a subcontractor and on days that are not available."""
    if owner.kind == "subcontractor" or not owner.workdays or not is_available(owner, day, holiday):
        return 0.0
    return max(0.0, owner.weekly_hours / len(owner.workdays) - owner.busy.get(day, 0.0))


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _spread_full(owner: Owner, a: Assignment, holiday: Holiday, today: date | None) -> dict[date, float]:
    """Hours per available day of the assignment. ``today=None``: the whole period; else only days after ``today``."""
    out: dict[date, float] = {}
    if a.end < a.start:
        return out
    first = a.start if today is None else max(a.start, today + timedelta(days=1))
    if first > a.end:
        return out
    days = [first + timedelta(days=i) for i in range((a.end - first).days + 1)]
    if a.hours_per_week is not None:
        weekly: dict[date, int] = {}
        for d in days:
            mon = _monday(d)
            if mon not in weekly:
                weekly[mon] = sum(1 for i in range(7) if is_available(owner, mon + timedelta(days=i), holiday))
        for d in days:
            n = weekly[_monday(d)]
            if n and is_available(owner, d, holiday):
                out[d] = a.hours_per_week / n
        return out
    total = a.total_hours or 0.0
    if today is not None:
        total = max(0.0, total - a.booked)
    avail = [d for d in days if is_available(owner, d, holiday)]
    if avail and total > 0:
        for d in avail:
            out[d] = total / len(avail)
    return out


def spread(owner: Owner, a: Assignment, holiday: Holiday, today: date | None) -> dict[date, float]:
    """Hours per available day. A cancelled assignment (``cut``) is spread over its ORIGINAL period exactly like an
    uncut one; then the days from ``cut`` on are dropped (S1)."""
    out = _spread_full(owner, a, holiday, today)
    if a.cut is not None:
        out = {d: h for d, h in out.items() if d < a.cut}
    return out


def planned_hours(owner: Owner, a: Assignment, holiday: Holiday) -> float:
    return round(sum(spread(owner, a, holiday, None).values()), 2)


def planned_net(owner: Owner, a: Assignment, holiday: Holiday, rate: Decimal) -> Decimal:
    """M1: total assignment ``q2(total_hours x rate)``; weekly: the spread hours x rate."""
    if a.total_hours is not None and a.cut is None:
        return q2(dec(a.total_hours) * rate)
    exact = sum(spread(owner, a, holiday, None).values())
    return q2(dec(round(exact, 6)) * rate)


def weekly_hours(days: dict[date, float]) -> dict[date, float]:
    """Hours per Monday-Sunday week (keyed by the Monday)."""
    out: dict[date, float] = {}
    for d, h in days.items():
        mon = _monday(d)
        out[mon] = out.get(mon, 0.0) + h
    return out
