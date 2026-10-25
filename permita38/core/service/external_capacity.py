"""Hours and cost of an external (subcontractor) allocation: the one place that spreads them over days
(docs/contracts/subcontractors.md, "Spreading the hours").

Pure functions of the supplier row, the allocation row and a capacity helper that knows the public holidays
(``cap.holiday(day)``). Used by the planner engine, the allocation figures, the commitments and the warnings, so
there is a single implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

from permita38.core import calc
from permita38.core.money import dec, q2

DEFAULT_WORKDAYS = (0, 1, 2, 3, 4)


class HolidayCalendar(Protocol):
    def holiday(self, day: date) -> Any: ...


def week_start(x: date) -> date:
    return x - timedelta(days=x.weekday())


def workdays(supplier: dict) -> list[int]:
    days = supplier.get("workdays")
    if isinstance(days, list) and days and all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        return sorted(set(days))
    return list(DEFAULT_WORKDAYS)


def unavailable_windows(supplier: dict) -> list[tuple[date, date]]:
    out = []
    for w in supplier.get("unavailable") or []:
        if not isinstance(w, dict):
            continue
        s, e = calc.d(w.get("from")), calc.d(w.get("to"))
        if s and e and e >= s:
            out.append((s, e))
    return out


def effective_end(allocation: dict) -> date | None:
    """Last day with capacity: the day before ``cancelled_from`` for a cancelled allocation, else ``end``."""
    end = calc.d(allocation.get("end"))
    if allocation.get("status") == "cancelled" and calc.d(allocation.get("cancelled_from")):
        cut = calc.d(allocation["cancelled_from"]) - timedelta(days=1)  # type: ignore[operator]
        return min(end, cut) if end else cut
    return end


def available_days(supplier: dict, start: date, end: date, cap: HolidayCalendar) -> list[date]:
    wd = set(workdays(supplier))
    off = unavailable_windows(supplier)
    out = []
    x = start
    while x <= end:
        if x.weekday() in wd and not cap.holiday(x) and not any(a <= x <= b for a, b in off):
            out.append(x)
        x += timedelta(days=1)
    return out


def allocation_days(supplier: dict, allocation: dict, cap: HolidayCalendar) -> dict[date, float]:
    """Hours per available day. ``hours_per_week``: a ceiling of hours_per_week / len(workdays) per available day, not
    moved to other days. ``total_hours``: spread evenly over the available days of the original period (a cancelled
    allocation keeps the days before the cut). No available day: empty."""
    start, end = calc.d(allocation.get("start")), calc.d(allocation.get("end"))
    last = effective_end(allocation)
    if not start or not end or end < start or not last:
        return {}
    hpw = calc.fnum(allocation.get("hours_per_week"))
    total = calc.fnum(allocation.get("total_hours"))
    if hpw:
        per_day = hpw / len(workdays(supplier))
        return {x: per_day for x in available_days(supplier, start, last, cap)}
    if total:
        original = available_days(supplier, start, end, cap)
        if not original:
            return {}
        per_day = total / len(original)
        return {x: per_day for x in original if x <= last}
    return {}


def weekly_hours(days: dict[date, float]) -> dict[date, float]:
    """Hours per Monday-Sunday week (keyed by the Monday)."""
    out: dict[date, float] = {}
    for x, h in days.items():
        ws = week_start(x)
        out[ws] = out.get(ws, 0.0) + h
    return out


def week_cost(hours: float, rate: Any) -> Decimal:
    """Cost of the hours of one week: ``q2(dec(round(hours, 6)) x rate)``."""
    return q2(dec(round(hours, 6)) * dec(rate))


@dataclass
class Figures:
    days: dict[date, float]
    planned_hours: float  # 2 decimals
    planned_net: Decimal  # exact cents

    @property
    def hours_exact(self) -> float:
        return sum(self.days.values())


def figures(supplier: dict, allocation: dict, cap: HolidayCalendar) -> Figures:
    """Planned hours and net cost. ``total_hours`` allocation: ``q2(total x rate)`` when nothing was cancelled, else
    ``q2(total x kept days / n x rate)`` (n = available days of the original period)."""
    days = allocation_days(supplier, allocation, cap)
    exact = round(sum(days.values()), 6)
    rate = dec(allocation.get("rate"))
    total = calc.fnum(allocation.get("total_hours"))
    if total and not calc.fnum(allocation.get("hours_per_week")):
        start, end = calc.d(allocation.get("start")), calc.d(allocation.get("end"))
        n = len(available_days(supplier, start, end, cap)) if start and end else 0  # type: ignore[arg-type]
        if n == 0:
            net = Decimal(0)
        elif allocation.get("status") == "cancelled":
            net = q2(dec(total) * len(days) / n * rate)
        else:
            net = q2(dec(total) * rate)
    else:
        net = q2(dec(exact) * rate)
    return Figures(days=days, planned_hours=round(exact, 2), planned_net=net)
