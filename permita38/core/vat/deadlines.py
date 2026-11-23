"""Filing periods and due dates for the UStVA and the ZM (§ 18 UStG, § 18a UStG, § 108 Abs. 3 AO)."""

from calendar import monthrange
from datetime import date, timedelta
from typing import Literal

from permita38.core.holidays import next_working_day

PeriodKind = Literal["month", "quarter"]


def _end_month(kind: PeriodKind, no: int) -> int:
    months = 3 * no if kind == "quarter" else no
    if not (1 <= no <= (4 if kind == "quarter" else 12)):
        raise ValueError(f"invalid period number {no} for {kind}")
    return months


def period_bounds(year: int, kind: PeriodKind, no: int) -> tuple[date, date]:
    """First and last day (inclusive) of the period."""
    end_month = _end_month(kind, no)
    start_month = end_month - (2 if kind == "quarter" else 0)
    return date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1])


def _plus_months(year: int, month: int, add: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + add
    return index // 12, index % 12 + 1


def ustva_due(year: int, kind: PeriodKind, no: int, dauerfrist: bool, state: str = "") -> date:
    """10th day after the period end (one month later with Dauerfrist), shifted to a working day."""
    end = period_bounds(year, kind, no)[1]
    y, m = _plus_months(end.year, end.month, 2 if dauerfrist else 1)
    return next_working_day(date(y, m, 10), state)


def zm_due(year: int, quarter: int, state: str = "") -> date:
    """25th day after the quarter end, shifted to a working day (no Dauerfrist for the ZM)."""
    end = period_bounds(year, "quarter", quarter)[1]
    return next_working_day(end + timedelta(days=25), state)
