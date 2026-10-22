"""Hardened, stdlib-only iCalendar (RFC 5545) reader that returns the busy events of a time window.

What it does: unfolds lines, reads VEVENT components with TZID / UTC / floating / all-day times, DTEND or DURATION,
RRULE, EXDATE, RDATE, RECURRENCE-ID overrides and the common "free" markers (TRANSP:TRANSPARENT, Microsoft
X-MICROSOFT-CDO-BUSYSTATUS:FREE, STATUS:CANCELLED). Output is expanded occurrences inside the window, in UTC.

What it does not do: write calendars, read VTODO/VJOURNAL, honour alarms or attendee status, or reproduce
VTIMEZONE rules exactly. Known zone names use :mod:`zoneinfo`; an unknown custom zone uses the offset of its
VTIMEZONE (approximate across DST changes, reported as a warning). Floating times use the default zone given by
the caller.

Untrusted input: every input is bounded (bytes, lines, nesting, components, occurrences, periods, wall-clock time).
Only :class:`IcsError` is raised for bad input; the caller never sees other exceptions.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from datetime import time as dtime
from zoneinfo import ZoneInfo

from permita38.core.service.calendar.recurrence import ExpansionLimit, RruleError, Rule, iter_dates, parse_rule

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_LINES = 150_000
MAX_LINE_CHARS = 16_384
MAX_DEPTH = 6
MAX_COMPONENTS = 20_000
MAX_OCCURRENCES = 20_000
MAX_GENERATED = 200_000  # per recurring event, including those outside the window
DEFAULT_DEADLINE_SECONDS = 5.0
MAX_TITLE_CHARS = 200
MAX_CATEGORIES = 20
_TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*$")
_OOO_RE = re.compile(r"\booo\b|out of office|urlaub|vacation|abwesen|\bleave\b|\bholidays?\b", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"^([+-])?P(?:(\d{1,4})W)?(?:(\d{1,4})D)?(?:T(?:(\d{1,6})H)?(?:(\d{1,6})M)?(?:(\d{1,6})S)?)?$"
)
_OFFSET_RE = re.compile(r"^([+-])(\d{2})(\d{2})(\d{2})?$")


class IcsError(ValueError):
    """The input is not a usable calendar. ``code`` is stable; the message never contains user data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Occurrence:
    uid: str
    recurrence_id: str | None  # key of the instance this one replaces (overrides), else None
    all_day: bool
    start: str  # "YYYY-MM-DDTHH:MM:SSZ" (UTC) or "YYYY-MM-DD" for all-day events
    end: str  # exclusive, same format
    summary: str
    categories: tuple[str, ...]
    ooo: bool  # looks like an out-of-office / vacation entry (a suggestion, never an absence by itself)


@dataclass(frozen=True)
class ParseResult:
    occurrences: list[Occurrence]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Prop:
    name: str
    params: dict[str, str]
    value: str


@dataclass
class _Comp:
    name: str
    props: list[_Prop] = field(default_factory=list)
    children: list[_Comp] = field(default_factory=list)


@dataclass(frozen=True)
class _Stamp:
    is_date: bool
    wall: datetime  # naive wall clock (midnight for dates)
    tz: tzinfo

    def instant(self) -> datetime:
        return self.wall.replace(tzinfo=self.tz).astimezone(UTC)

    def key(self) -> str:
        return self.wall.date().isoformat() if self.is_date else self.instant().strftime("%Y-%m-%dT%H:%M:%SZ")


class _Bad(Exception):
    """One component or value is unusable; the component is skipped with a warning."""


# --------------------------------------------------------------------------------------------- lines
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"\r\n|\r|\n", text):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] = (lines[-1] + raw[1:])[:MAX_LINE_CHARS]
        else:
            lines.append(raw[:MAX_LINE_CHARS])
        if len(lines) > MAX_LINES:
            raise IcsError("too_large", "The calendar has too many lines.")
    return lines


def _split_outside_quotes(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quoted = False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _parse_prop(line: str) -> _Prop | None:
    quoted, head_end = False, -1
    for idx, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head_end = idx
            break
    if head_end < 0:
        return None
    pieces = _split_outside_quotes(line[:head_end], ";")
    name = pieces[0].strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{1,64}", name):
        return None
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, value = piece.partition("=")
        key = key.strip().upper()
        if key and key not in params:
            params[key] = value.strip().strip('"')[:200]
    return _Prop(name, params, line[head_end + 1 :])


def _tree(lines: Iterable[str]) -> _Comp:
    root = _Comp("ROOT")
    stack = [root]
    components = 0
    for line in lines:
        if not line.strip():
            continue
        prop = _parse_prop(line)
        if prop is None:
            continue
        if prop.name == "BEGIN":
            components += 1
            if components > MAX_COMPONENTS:
                raise IcsError("too_large", "The calendar has too many components.")
            if len(stack) > MAX_DEPTH:
                raise IcsError("too_deep", "The calendar is nested too deeply.")
            comp = _Comp(prop.value.strip().upper())
            stack[-1].children.append(comp)
            stack.append(comp)
        elif prop.name == "END":
            name = prop.value.strip().upper()
            for k in range(len(stack) - 1, 0, -1):
                if stack[k].name == name:
                    del stack[k:]
                    break
        else:
            stack[-1].props.append(prop)
    return root


# ---------------------------------------------------------------------------------------- values
def _unescape(value: str) -> str:
    out = value.replace("\\n", " ").replace("\\N", " ")
    return re.sub(r"\\([,;\\])", r"\1", out).replace("\x00", "")


def _split_values(value: str) -> list[str]:
    return [v for v in re.split(r"(?<!\\),", value) if v.strip()]


def _fixed_offset(text: str) -> timezone | None:
    m = _OFFSET_RE.match(text.strip())
    if not m:
        return None
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)), seconds=int(m.group(4) or 0))
    if delta > timedelta(hours=14):
        return None
    return timezone(-delta if m.group(1) == "-" else delta)


class _Zones:
    """Resolves TZID names: IANA names via zoneinfo, otherwise the offset of the matching VTIMEZONE."""

    def __init__(self, default: tzinfo, warnings: list[str]):
        self.default = default
        self.warnings = warnings
        self.custom: dict[str, tzinfo] = {}
        self.approximate: dict[str, bool] = {}
        self._cache: dict[str, tzinfo] = {}

    def add_vtimezone(self, comp: _Comp) -> None:
        tzid = next((p.value.strip().strip('"') for p in comp.props if p.name == "TZID"), "")
        offsets = []
        for child in comp.children:
            if child.name not in ("STANDARD", "DAYLIGHT"):
                continue
            for prop in child.props:
                if prop.name == "TZOFFSETTO":
                    offsets.append((child.name, _fixed_offset(prop.value)))
        if not tzid or not offsets:
            return
        standard = [o for name, o in offsets if name == "STANDARD" and o is not None]
        chosen = (standard or [o for _, o in offsets if o is not None])[-1:]
        if chosen:
            self.custom.setdefault(tzid, chosen[0])
            self.approximate[tzid] = len(offsets) > 1

    def resolve(self, tzid: str | None) -> tzinfo:
        if not tzid:
            return self.default
        name = tzid.strip().strip('"').lstrip("/")
        if name in self._cache:
            return self._cache[name]
        zone: tzinfo | None = None
        if _TZ_NAME_RE.match(name) and len(name) <= 64:
            try:
                zone = ZoneInfo(name)
            except (ValueError, KeyError, OSError):
                zone = None
        if zone is None:
            key = name if name in self.custom else tzid.strip().strip('"')
            zone = self.custom.get(key)
            if zone is not None and self.approximate.get(key):
                self.warnings.append("approximate_timezone")
        if zone is None:
            self.warnings.append("unknown_timezone")
            zone = self.default
        self._cache[name] = zone
        return zone


def _stamp(prop: _Prop, zones: _Zones) -> _Stamp:
    value = prop.value.strip()
    if prop.params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value)
        if not m:
            raise _Bad()
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            raise _Bad() from None
        return _Stamp(True, datetime.combine(day, dtime()), zones.default)
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not m:
        raise _Bad()
    try:
        wall = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        raise _Bad() from None
    if m.group(3) == "Z":
        return _Stamp(False, wall, UTC)
    return _Stamp(False, wall, zones.resolve(prop.params.get("TZID")))


def _duration(text: str) -> timedelta:
    m = _DURATION_RE.match(text.strip().upper())
    if not m or not any(m.group(i) for i in range(2, 7)):
        raise _Bad()
    delta = timedelta(
        weeks=int(m.group(2) or 0),
        days=int(m.group(3) or 0),
        hours=int(m.group(4) or 0),
        minutes=int(m.group(5) or 0),
        seconds=int(m.group(6) or 0),
    )
    if delta > timedelta(days=366):
        raise _Bad()
    return -delta if m.group(1) == "-" else delta


# ---------------------------------------------------------------------------------- expansion
@dataclass
class _Master:
    uid: str
    start: _Stamp
    duration: timedelta
    summary: str
    categories: tuple[str, ...]
    ooo: bool
    cancelled: bool
    transparent: bool
    rule: Rule | None
    until: _Stamp | None
    rdates: list[_Stamp]
    exdates: set[str]
    rid: str | None = None  # RECURRENCE-ID key when this component overrides one instance


class _Context:
    def __init__(
        self,
        zones: _Zones,
        window: tuple[datetime, datetime],
        keep: Callable[[str, tuple[str, ...]], bool],
        clock: Callable[[], float],
        deadline: float,
        warnings: list[str],
    ):
        self.zones = zones
        self.window_start, self.window_end = window
        self.keep = keep
        self.clock = clock
        self.deadline = deadline
        self.warnings = warnings

    def in_window(self, start: _Stamp, end: datetime) -> bool:
        return end > self.window_start and start.instant() < self.window_end

    def check_time(self) -> None:
        if self.clock() > self.deadline:
            raise IcsError("too_complex", "The calendar is too complex to process in time.")


def _text(props: dict[str, list[_Prop]], name: str) -> str:
    return _unescape(props[name][0].value) if name in props else ""


def _categories(props: dict[str, list[_Prop]]) -> tuple[str, ...]:
    values: list[str] = []
    for prop in props.get("CATEGORIES", []):
        values.extend(_unescape(v).strip() for v in _split_values(prop.value))
    return tuple(v[:100] for v in values if v)[:MAX_CATEGORIES]


def _first(props: dict[str, list[_Prop]], name: str) -> _Prop | None:
    return props[name][0] if props.get(name) else None


def _read_event(comp: _Comp, ctx: _Context) -> _Master | None:
    props: dict[str, list[_Prop]] = {}
    for p in comp.props:
        props.setdefault(p.name, []).append(p)
    start_prop = _first(props, "DTSTART")
    if start_prop is None:
        raise _Bad()
    start = _stamp(start_prop, ctx.zones)
    duration = timedelta(days=1) if start.is_date else timedelta(0)
    end_prop = _first(props, "DTEND")
    dur_prop = _first(props, "DURATION")
    if end_prop is not None:
        end = _stamp(end_prop, ctx.zones)
        if start.is_date != end.is_date:
            raise _Bad()
        if start.is_date:
            duration = max(timedelta(days=1), end.wall.date() - start.wall.date())
        else:
            duration = max(timedelta(0), end.instant() - start.instant())
    elif dur_prop is not None:
        duration = _duration(dur_prop.value)
        duration = max(timedelta(days=1), duration) if start.is_date else max(timedelta(0), duration)
    if start.is_date:
        duration = timedelta(days=max(1, duration.days))

    status = _text(props, "STATUS").strip().upper()
    transp = _text(props, "TRANSP").strip().upper()
    busy_prop = _first(props, "X-MICROSOFT-CDO-BUSYSTATUS")
    busystatus = busy_prop.value.strip().upper() if busy_prop else ""
    uid = _text(props, "UID")[:255] or f"no-uid-{start.key()}"
    summary = _text(props, "SUMMARY")[:MAX_TITLE_CHARS]
    categories = _categories(props)
    ooo = bool(_OOO_RE.search(summary) or any(_OOO_RE.search(c) for c in categories))
    rid_prop = _first(props, "RECURRENCE-ID")
    rid = _stamp(rid_prop, ctx.zones).key() if rid_prop is not None else None

    rule = None
    until = None
    rrule_prop = _first(props, "RRULE")
    if rrule_prop is not None:
        try:
            rule = parse_rule(rrule_prop.value)
        except RruleError:
            ctx.warnings.append("unsupported_rrule")
            rule = None
        else:
            if rule.until:
                until = _until(rule.until, start, ctx.zones)

    rdates: list[_Stamp] = []
    for prop in props.get("RDATE", []):
        for value in _split_values(prop.value)[:500]:
            try:
                rdates.append(_stamp(_Prop(prop.name, prop.params, value.split("/")[0]), ctx.zones))
            except _Bad:
                ctx.warnings.append("bad_value")
    exdates: set[str] = set()
    for prop in props.get("EXDATE", []):
        for value in _split_values(prop.value)[:5000]:
            try:
                exdates.add(_stamp(_Prop(prop.name, prop.params, value), ctx.zones).key())
            except _Bad:
                ctx.warnings.append("bad_value")

    return _Master(
        uid=uid,
        start=start,
        duration=duration,
        summary=summary,
        categories=categories,
        ooo=ooo,
        cancelled=status == "CANCELLED",
        transparent=transp == "TRANSPARENT" or busystatus == "FREE",
        rule=rule,
        until=until,
        rdates=rdates,
        exdates=exdates,
        rid=rid,
    )


def _until(text: str, start: _Stamp, zones: _Zones) -> _Stamp:
    value = text.strip().upper()
    if re.fullmatch(r"\d{8}", value):
        day = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        # a date-only UNTIL includes the whole day
        return _Stamp(start.is_date, datetime.combine(day, dtime(23, 59, 59)), start.tz)
    prop = _Prop("UNTIL", {}, value)
    stamp = _stamp(prop, zones)
    if stamp.is_date:
        raise _Bad()
    return stamp


def _expand(master: _Master, overrides: set[str], ctx: _Context) -> list[tuple[_Stamp, str]]:
    """In-window (start, key) pairs of one master, minus EXDATE and overridden instances."""
    seen = {master.start.key()}
    found: list[tuple[_Stamp, str]] = []
    generated = 0

    if master.start.key() not in overrides and master.start.key() not in master.exdates:
        if (
            not master.cancelled
            and not master.transparent
            and ctx.in_window(master.start, _end_of(master.start, master.duration))
        ):
            found.append((master.start, master.start.key()))
    if master.rule is not None:
        rule = master.rule
        for day in iter_dates(rule, master.start.wall.date(), ctx.clock, ctx.deadline):
            if day == master.start.wall.date():
                continue
            wall = datetime.combine(day, master.start.wall.time())
            stamp = _Stamp(master.start.is_date, wall, master.start.tz)
            if master.until is not None and stamp.instant() > master.until.instant():
                break
            generated += 1
            if rule.count is not None and generated + 1 > rule.count:
                break
            if generated > MAX_GENERATED:
                raise IcsError("too_complex", "A recurring event has too many occurrences.")
            key = stamp.key()
            if key in seen:
                continue
            seen.add(key)
            if key not in master.exdates and key not in overrides:
                if (
                    not master.cancelled
                    and not master.transparent
                    and ctx.in_window(stamp, _end_of(stamp, master.duration))
                ):
                    found.append((stamp, key))
            if stamp.instant() > ctx.window_end:  # ascending: nothing later can fall inside the window
                break
            if generated % 1024 == 0:
                ctx.check_time()
    for stamp in master.rdates:
        key = stamp.key()
        if key in seen:
            continue
        seen.add(key)
        if key not in master.exdates and key not in overrides:
            if (
                not master.cancelled
                and not master.transparent
                and ctx.in_window(stamp, _end_of(stamp, master.duration))
            ):
                found.append((stamp, key))
    return found


def _end_of(start: _Stamp, duration: timedelta) -> datetime:
    if start.is_date:
        return datetime.combine(start.wall.date() + duration, dtime(), tzinfo=start.tz).astimezone(UTC)
    return start.instant() + duration


def _occurrence(master: _Master, start: _Stamp, recurrence_id: str | None) -> Occurrence:
    if start.is_date:
        end_day = start.wall.date() + master.duration
        return Occurrence(
            master.uid,
            recurrence_id,
            True,
            start.key(),
            end_day.isoformat(),
            master.summary,
            master.categories,
            master.ooo,
        )
    end = _end_of(start, master.duration)
    return Occurrence(
        master.uid,
        recurrence_id,
        False,
        start.key(),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        master.summary,
        master.categories,
        master.ooo,
    )


def parse_ics(
    data: bytes,
    *,
    default_tz: tzinfo,
    window: tuple[datetime, datetime],
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ParseResult:
    """Busy occurrences of a calendar that fall inside ``window`` (aware datetimes), expanded and in UTC.

    Keyword filters match SUMMARY and CATEGORIES case-insensitively (substring). Raises :class:`IcsError`.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_INPUT_BYTES:
        raise IcsError("too_large", "The calendar file is too large (max 5 MB).")
    text = bytes(data).decode("utf-8", errors="replace").lstrip("﻿")
    root = _tree(_unfold(text))
    calendars = [c for c in root.children if c.name == "VCALENDAR"]
    if not calendars:
        raise IcsError("invalid_ics", "This is not an iCalendar (.ics) file.")
    warnings: list[str] = []
    zones = _Zones(default_tz, warnings)
    deadline = clock() + deadline_seconds
    include = [k.lower() for k in include_keywords if k]
    exclude = [k.lower() for k in exclude_keywords if k]

    def keep(summary: str, categories: tuple[str, ...]) -> bool:
        haystack = " ".join([summary, *categories]).lower()
        if include and not any(k in haystack for k in include):
            return False
        return not any(k in haystack for k in exclude)

    ctx = _Context(zones, (window[0].astimezone(UTC), window[1].astimezone(UTC)), keep, clock, deadline, warnings)
    events: list[_Comp] = []
    for cal in calendars:
        for comp in cal.children:
            if comp.name == "VTIMEZONE":
                zones.add_vtimezone(comp)
            elif comp.name == "VEVENT":
                events.append(comp)

    masters: list[_Master] = []
    overrides: dict[str, dict[str, _Master | None]] = {}
    for comp in events:
        try:
            master = _read_event(comp, ctx)
        except (_Bad, ValueError, OverflowError):  # ValueError/OverflowError: a date outside the calendar range
            warnings.append("bad_event")
            continue
        if master is None:
            continue
        if master.rid is not None:
            overrides.setdefault(master.uid, {})[master.rid] = master
        else:
            masters.append(master)

    out: list[Occurrence] = []
    for master in masters:
        ctx.check_time()
        if not ctx.keep(master.summary, master.categories):
            continue
        if master.cancelled:
            continue
        uid_overrides = overrides.get(master.uid, {})
        try:
            for stamp, _key in _expand(master, set(uid_overrides), ctx):
                out.append(_occurrence(master, stamp, None))
        except ExpansionLimit:
            raise IcsError("too_complex", "The calendar is too complex to process in time.") from None
        except (ValueError, OverflowError):
            warnings.append("out_of_range")
            continue
        if len(out) > MAX_OCCURRENCES:
            raise IcsError("too_many_events", "The calendar has too many events in the period (max 20,000).")
    for uid, by_key in overrides.items():
        for key, ov in by_key.items():
            if ov is None or ov.cancelled or ov.transparent or not ctx.keep(ov.summary, ov.categories):
                continue
            if ctx.in_window(ov.start, _end_of(ov.start, ov.duration)):
                out.append(_occurrence(ov, ov.start, key))
    if len(out) > MAX_OCCURRENCES:
        raise IcsError("too_many_events", "The calendar has too many events in the period (max 20,000).")
    out.sort(key=lambda o: (o.start, o.uid, o.recurrence_id or ""))
    return ParseResult(occurrences=out, warnings=tuple(sorted(set(warnings))))
