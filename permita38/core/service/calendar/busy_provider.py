"""The planner's view of calendars: busy hours per day inside each person's working time (BusyTimeProvider)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from permita38.core.repository import Repository
from permita38.core.service.calendar.service import event_bounds, user_zone

DEFAULT_WORK_START = time(9, 0)
DEFAULT_WORK_END = time(17, 0)
MAX_SPAN_DAYS = 400  # one query; longer ranges are answered in chunks of this size
MAX_TOTAL_DAYS = 3660  # about ten years


def _clock(text: str | None, default: time) -> time:
    if not text:
        return default
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


class CalendarBusyProvider:
    """Hours per day that calendars take away from a person's capacity.

    Rules: only the part of an event inside the person's working time counts (work_start..work_end on their
    workdays, in their time zone); overlapping events are merged; the result is capped at the person's daily target
    (weekly_hours / number of workdays); all-day events count as the whole working day only when the source has
    ``all_day_blocks``; sources with ``busy_default`` false are ignored. ``start`` and ``end`` are both included.

    ``on_request`` is called first (non-blocking) so a stale subscription can refresh in the background.
    """

    def __init__(self, store: Repository, *, on_request: Callable[[str], Any] | None = None):
        self.store = store
        self.on_request = on_request

    def busy_hours(self, user_id: str, start: date, end: date) -> dict[date, float]:
        """Busy hours per day. Long ranges (the planner asks for up to 156 weeks) are answered in chunks; days beyond
        the stored calendar horizon simply have no events, i.e. no busy time."""
        if end < start or (end - start).days > MAX_TOTAL_DAYS:
            return {}
        out: dict[date, float] = {}
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=MAX_SPAN_DAYS))
            out.update(self._busy_range(user_id, chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)
        return out

    def _busy_range(self, user_id: str, start: date, end: date) -> dict[date, float]:
        sources = {
            s["id"]: s for s in self.store.find("calendar_sources", user_id=user_id) if s.get("busy_default", True)
        }
        if not sources:
            return {}
        if self.on_request is not None:
            self.on_request(user_id)
        user = self.store.get("users", user_id)
        if not user:
            return {}
        zone = user_zone(user)
        work_start = _clock(user.get("work_start"), DEFAULT_WORK_START)
        work_end = _clock(user.get("work_end"), DEFAULT_WORK_END)
        raw_days = user.get("workdays")
        workdays = {int(d) for d in (raw_days if raw_days is not None else [0, 1, 2, 3, 4])}
        if work_end <= work_start or not workdays:
            return {}
        target = float(user.get("weekly_hours") or 40) / len(workdays)
        if target <= 0:
            return {}

        def window(day: date) -> tuple[float, float]:
            return (
                datetime.combine(day, work_start, tzinfo=zone).timestamp(),
                datetime.combine(day, work_end, tzinfo=zone).timestamp(),
            )

        intervals: dict[date, list[tuple[float, float]]] = defaultdict(list)
        for ev in self._events(sources):
            src = sources[ev["source_id"]]
            if ev.get("all_day"):
                if not src.get("all_day_blocks"):
                    continue
                first = max(date.fromisoformat(ev["start_utc"]), start)
                last = min(date.fromisoformat(ev["end_utc"]) - timedelta(days=1), end)
                for day in _days(first, last):
                    if day.weekday() in workdays:
                        intervals[day].append(window(day))
                continue
            s, e = (b.timestamp() for b in event_bounds(ev))
            if e <= s:
                continue
            first = max(datetime.fromtimestamp(s, zone).date(), start)
            last = min(datetime.fromtimestamp(e, zone).date(), end)
            for day in _days(first, last):
                if day.weekday() not in workdays:
                    continue
                a, b = window(day)
                lo, hi = max(s, a), min(e, b)
                if hi > lo:
                    intervals[day].append((lo, hi))

        out: dict[date, float] = {}
        for day, spans in intervals.items():
            hours = min(_merged_seconds(spans) / 3600.0, target)
            if hours > 0:
                out[day] = round(hours, 4)
        return dict(sorted(out.items()))

    def _events(self, sources: dict[str, dict]) -> list[dict]:
        found: list[dict] = []
        for source_id in sources:
            found.extend(self.store.find("calendar_events", source_id=source_id))
        return found


def _days(first: date, last: date) -> list[date]:
    if last < first:
        return []
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _merged_seconds(spans: list[tuple[float, float]]) -> float:
    """Total length of the union of the intervals."""
    total = 0.0
    current: tuple[float, float] | None = None
    for lo, hi in sorted(spans):
        if current is None:
            current = (lo, hi)
        elif lo > current[1]:
            total += current[1] - current[0]
            current = (lo, hi)
        else:
            current = (current[0], max(current[1], hi))
    if current is not None:
        total += current[1] - current[0]
    return total
