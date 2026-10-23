"""Busy time that reduces a person's capacity (calendar meetings etc.). Implemented by the calendar module."""

from datetime import date
from typing import Protocol


class BusyTimeProvider(Protocol):
    def busy_hours(self, user_id: str, start: date, end: date) -> dict[date, float]:
        """Hours per day (inside the person's working time, overlaps merged) that are NOT available for project work."""
        ...


class NullBusyProvider:
    def busy_hours(self, user_id: str, start: date, end: date) -> dict[date, float]:
        return {}
