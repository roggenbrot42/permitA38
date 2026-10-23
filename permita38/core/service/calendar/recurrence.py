"""RRULE expansion (RFC 5545 section 3.3.10) for the subset that calendar exports use.

Supported: FREQ=DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL (interpreted by the caller), BYDAY (with ordinals
for MONTHLY), BYMONTHDAY, BYMONTH, BYSETPOS, WKST. Anything else (HOURLY, BYHOUR, BYWEEKNO, BYYEARDAY, ...) makes the
rule :class:`RruleError`; the parser then keeps only the first occurrence and reports a warning.

Every loop is bounded: at most ``MAX_PERIODS`` periods and a caller-supplied deadline.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

MAX_PERIODS = 60_000
MAX_INTERVAL = 1000
_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_SUPPORTED = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "WKST"}
_INT_RE = re.compile(r"^[+-]?\d{1,9}$")
_BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


class RruleError(ValueError):
    """The rule is malformed or uses a part this implementation does not support."""


class ExpansionLimit(Exception):
    """Expansion stopped because the period or time budget ran out."""


@dataclass(frozen=True)
class Rule:
    freq: str
    interval: int = 1
    count: int | None = None
    until: str | None = None  # raw UNTIL value, interpreted by the caller
    byday: tuple[tuple[int, int | None], ...] = ()  # (weekday 0=Mon, ordinal or None)
    bymonthday: tuple[int, ...] = ()
    bymonth: tuple[int, ...] = ()
    bysetpos: tuple[int, ...] = ()
    wkst: int = 0


def _int(text: str, low: int, high: int) -> int:
    if not _INT_RE.match(text):
        raise RruleError("bad integer")
    value = int(text)
    if not low <= value <= high:
        raise RruleError("integer out of range")
    return value


def _count(text: str) -> int:
    """COUNT; absurdly large values (a hostile COUNT=1000000000) are clamped, the window bounds the expansion."""
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise RruleError("bad integer")
    return min(int(text), 1_000_000_000)


def parse_rule(text: str) -> Rule:
    parts: dict[str, str] = {}
    for item in text.split(";")[:40]:
        key, sep, value = item.partition("=")
        key = key.strip().upper()
        if not sep or not key or key in parts:
            raise RruleError("malformed rule")
        parts[key] = value.strip()
    if set(parts) - _SUPPORTED:
        raise RruleError("unsupported rule part")
    freq = parts.get("FREQ", "").upper()
    if freq not in _FREQS:
        raise RruleError("unsupported frequency")
    byday: list[tuple[int, int | None]] = []
    for token in [t.strip().upper() for t in parts.get("BYDAY", "").split(",") if t.strip()][:14]:
        m = _BYDAY_RE.match(token)
        if not m:
            raise RruleError("bad BYDAY")
        ordinal = None
        if m.group(1):
            ordinal = _int(m.group(1), -53, 53)
            if ordinal == 0:
                raise RruleError("bad BYDAY ordinal")
        byday.append((_WEEKDAYS[m.group(2)], ordinal))

    def ints(key: str, low: int, high: int, zero: bool) -> tuple[int, ...]:
        values = tuple(_int(t.strip(), low, high) for t in parts.get(key, "").split(",") if t.strip())[:60]
        if not zero and 0 in values:
            raise RruleError("zero is not allowed")
        return values

    wkst_text = parts.get("WKST", "MO").upper() or "MO"
    if wkst_text not in _WEEKDAYS:
        raise RruleError("bad WKST")
    return Rule(
        freq=freq,
        interval=_int(parts["INTERVAL"], 1, MAX_INTERVAL) if "INTERVAL" in parts else 1,
        count=_count(parts["COUNT"]) if "COUNT" in parts else None,
        until=parts.get("UNTIL") or None,
        byday=tuple(byday),
        bymonthday=ints("BYMONTHDAY", -31, 31, zero=False),
        bymonth=ints("BYMONTH", 1, 12, zero=True),
        bysetpos=ints("BYSETPOS", -366, 366, zero=False),
        wkst=_WEEKDAYS[wkst_text],
    )


def _monthday_ok(rule: Rule, day: date, ndays: int) -> bool:
    if not rule.bymonthday:
        return True
    return day.day in rule.bymonthday or (day.day - ndays - 1) in rule.bymonthday


def _ordinal_ok(day: date, ndays: int, ordinal: int | None) -> bool:
    if ordinal is None:
        return True
    if ordinal > 0:
        return (day.day - 1) // 7 + 1 == ordinal
    return (ndays - day.day) // 7 + 1 == -ordinal


def _simple_day_ok(rule: Rule, day: date) -> bool:
    """DAILY and WEEKLY: weekday filter (ordinals are meaningless here and ignored)."""
    if rule.bymonth and day.month not in rule.bymonth:
        return False
    if rule.byday and day.weekday() not in {wd for wd, _ in rule.byday}:
        return False
    return _monthday_ok(rule, day, calendar.monthrange(day.year, day.month)[1])


def _month_days(rule: Rule, year: int, month: int, start: date, explicit: bool) -> list[date]:
    ndays = calendar.monthrange(year, month)[1]
    if not explicit:
        return [date(year, month, start.day)] if start.day <= ndays else []
    out = []
    for day_no in range(1, ndays + 1):
        day = date(year, month, day_no)
        if not _monthday_ok(rule, day, ndays):
            continue
        if rule.byday and not any(
            wd == day.weekday() and _ordinal_ok(day, ndays, ordinal) for wd, ordinal in rule.byday
        ):
            continue
        out.append(day)
    return out


def _year_days(rule: Rule, year: int) -> list[date]:
    """BYDAY in a YEARLY rule without BYMONTH/BYMONTHDAY: "1MO" is the first Monday of the year, "-1FR" the last."""
    first, last = date(year, 1, 1), date(year, 12, 31)
    total = (last - first).days + 1
    out: set[date] = set()
    for weekday, ordinal in rule.byday:
        matching = [first + timedelta(days=i) for i in range(total) if (first + timedelta(days=i)).weekday() == weekday]
        if ordinal is None:
            out.update(matching)
        elif 0 < abs(ordinal) <= len(matching):
            out.add(matching[ordinal - 1] if ordinal > 0 else matching[ordinal])
    return sorted(out)


def period_dates(rule: Rule, start: date, index: int) -> list[date]:
    step = index * rule.interval
    if rule.freq == "DAILY":
        day = start + timedelta(days=step)
        return [day] if _simple_day_ok(rule, day) else []
    if rule.freq == "WEEKLY":
        week_start = start - timedelta(days=(start.weekday() - rule.wkst) % 7) + timedelta(weeks=step)
        days = [week_start + timedelta(days=j) for j in range(7)]
        if not rule.byday:  # default: the weekday of DTSTART
            days = [d for d in days if d.weekday() == start.weekday()]
        return [d for d in days if _simple_day_ok(rule, d)]
    if rule.freq == "MONTHLY":
        total = start.year * 12 + start.month - 1 + step
        year, month = divmod(total, 12)
        explicit = bool(rule.bymonthday or rule.byday)
        if rule.bymonth and month + 1 not in rule.bymonth:
            return []
        return _month_days(rule, year, month + 1, start, explicit)
    # YEARLY
    year = start.year + step
    if rule.byday and not rule.bymonth and not rule.bymonthday:
        return _year_days(rule, year)  # RFC 5545: without BYMONTH an ordinal counts within the whole year
    explicit = bool(rule.bymonthday or rule.byday)
    months = sorted(set(rule.bymonth)) if rule.bymonth else (list(range(1, 13)) if explicit else [start.month])
    out: list[date] = []
    for month in months:
        out.extend(_month_days(rule, year, month, start, explicit))
    return out


def iter_dates(rule: Rule, start: date, clock: Callable[[], float], deadline: float) -> Iterator[date]:
    """Dates generated by ``rule`` from ``start`` on, ascending. ``start`` itself is yielded when the rule matches it.

    Raises :class:`ExpansionLimit` when the period budget or the deadline is exhausted.
    """
    for index in range(MAX_PERIODS):
        if index % 256 == 255 and clock() > deadline:
            raise ExpansionLimit()
        try:
            dates = period_dates(rule, start, index)
        except (
            ValueError,
            OverflowError,
        ):  # ran past the calendar range (year 1 or 9999): stop, nothing after it exists
            return
        if rule.bysetpos and dates:
            picked = set()
            for pos in rule.bysetpos:
                idx = pos - 1 if pos > 0 else len(dates) + pos
                if 0 <= idx < len(dates):
                    picked.add(dates[idx])
            dates = sorted(picked)
        for day in dates:
            if day >= start:
                yield day
    raise ExpansionLimit()
