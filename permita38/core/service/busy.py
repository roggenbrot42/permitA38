"""Busy time that reduces a person's capacity (calendar meetings etc.). Implemented by the calendar module."""

from datetime import date
from typing import Protocol


class BusyTimeProvider(Protocol):
    def busy_hours(self, user_id: str, start: date, end: date, *, ooo_only: bool = False) -> dict[date, float]:
        """Hours per day (inside the person's working time, overlaps merged) that are NOT available for project work.

        ``ooo_only``: count only out-of-office events and all-day blocks (the planner, rule D10)."""
        ...


class NullBusyProvider:
    def busy_hours(self, user_id: str, start: date, end: date, *, ooo_only: bool = False) -> dict[date, float]:
        return {}
