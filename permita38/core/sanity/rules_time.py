"""Working-time plausibility, modelled on the German Arbeitszeitgesetz (ArbZG).

These are *plausibility aids*, not legal advice. Strictly speaking the ArbZG protects employees; for the boss the same
findings are shown as information only. Rules that need clock times only look at days where every entry has a start
and an end.
"""

from datetime import date, timedelta

from .base import Ctx, Finding, parse_clock, parse_date, rule

DAY = 1440


def _interval(e: dict) -> tuple[int, int] | None:
    """(start, end) minutes since midnight of the entry's own day; end may exceed 1440 for a night shift."""
    s, t = parse_clock(e.get("start")), parse_clock(e.get("end"))
    if s is None or t is None:
        return None
    return (s, t if t > s else t + DAY)


def _day_intervals(ctx: Ctx, uid, day: str, include_record: bool) -> list[tuple[int, int]] | None:
    """Intervals of a person on a day, or None if some entry on that day has no clock times."""
    entries = list(ctx.entries_of(uid, day))
    if include_record:
        entries.append(ctx.record)
    out = []
    for e in entries:
        iv = _interval(e)
        if iv is None:
            return None
        out.append(iv)
    return sorted(out)


def _severity(ctx: Ctx, uid) -> str:
    return "warning" if ctx.is_employee(uid) else "info"


def _record_day(ctx: Ctx):
    r = ctx.record
    day = parse_date(r.get("date"))
    uid = ctx.user_of(r)
    if not day or not uid:
        return None
    return uid, day


@rule(
    "time.day_over_10h",
    "More than 10 hours worked in a day",
    "Employees may work at most 8 hours a day, extendable to 10 hours if the average stays at 8 hours over six months "
    "(§3 ArbZG). Longer days are a compliance risk for the employer.",
    "critical",
    "entries",
    thresholds={"max_hours": 10},
    threshold_help={"max_hours": "daily hours above which the finding appears"},
)
def day_over(ctx: Ctx):
    rd = _record_day(ctx)
    if not rd:
        return
    uid, day = rd
    total = int(ctx.record.get("minutes") or 0) + sum(int(e.get("minutes") or 0) for e in ctx.entries_of(uid, day.isoformat()))
    limit = ctx.threshold("max_hours") * 60
    if total > limit:
        sev = "critical" if ctx.is_employee(uid) else "info"
        yield Finding(
            f"{total / 60:.1f} hours on {day.isoformat()} (limit {ctx.threshold('max_hours'):g} h).",
            "minutes",
            {"hours": round(total / 60, 2)},
            severity=sev,
        )


@rule(
    "time.rest_period",
    "Less than 11 hours of rest",
    "After the end of the daily working time employees need an uninterrupted rest of at least 11 hours (§5 ArbZG).",
    "warning",
    "entries",
    thresholds={"min_rest_hours": 11},
    threshold_help={"min_rest_hours": "required rest between two working days"},
)
def rest_period(ctx: Ctx):
    rd = _record_day(ctx)
    iv = _interval(ctx.record)
    if not rd or iv is None:
        return
    uid, day = rd
    need = ctx.threshold("min_rest_hours") * 60
    mine = (day.toordinal() * DAY + iv[0], day.toordinal() * DAY + iv[1])
    for delta in (-1, 1):
        other_day = (day + timedelta(days=delta)).isoformat()
        for e in ctx.entries_of(uid, other_day):
            oiv = _interval(e)
            if oiv is None:
                continue
            base = (day + timedelta(days=delta)).toordinal() * DAY
            other = (base + oiv[0], base + oiv[1])
            gap = mine[0] - other[1] if delta < 0 else other[0] - mine[1]
            if 0 <= gap < need:
                yield Finding(
                    f"Only {gap / 60:.1f} hours of rest between this entry and the one on {other_day} (11 h required).",
                    "start" if delta < 0 else "end",
                    {"rest_hours": round(gap / 60, 2), "other_day": other_day},
                    severity=_severity(ctx, uid),
                )
                return


@rule(
    "time.break_missing",
    "Not enough breaks",
    "Employees need a break of 30 minutes after more than 6 hours of work and 45 minutes after more than 9 hours; "
    "work must not run longer than 6 hours without a break (§4 ArbZG, breaks count from 15 minutes).",
    "warning",
    "entries",
    thresholds={"mid_hours": 6, "long_hours": 9, "break_mid": 30, "break_long": 45, "min_pause": 15},
    threshold_help={
        "mid_hours": "working hours after which the short break is needed",
        "long_hours": "working hours after which the long break is needed",
        "break_mid": "minutes of break after 'mid' hours",
        "break_long": "minutes of break after 'long' hours",
        "min_pause": "shortest gap that counts as a break (minutes)",
    },
)
def break_missing(ctx: Ctx):
    rd = _record_day(ctx)
    if not rd or _interval(ctx.record) is None:
        return
    uid, day = rd
    ivs = _day_intervals(ctx, uid, day.isoformat(), include_record=True)
    if not ivs:
        return
    pause = ctx.threshold("min_pause")
    work = sum(b - a for a, b in ivs)
    breaks = 0
    run_start, run_end, longest = ivs[0][0], ivs[0][1], 0
    for a, b in ivs[1:]:
        gap = a - run_end
        if gap >= pause:
            breaks += gap
            longest = max(longest, run_end - run_start)
            run_start, run_end = a, b
        else:
            run_end = max(run_end, b)
    longest = max(longest, run_end - run_start)
    sev = _severity(ctx, uid)
    if work > ctx.threshold("long_hours") * 60 and breaks < ctx.threshold("break_long"):
        yield Finding(
            f"{work / 60:.1f} hours of work with only {breaks} minutes of break (45 required).", "end", {"break_minutes": breaks}, severity=sev
        )
    elif work > ctx.threshold("mid_hours") * 60 and breaks < ctx.threshold("break_mid"):
        yield Finding(
            f"{work / 60:.1f} hours of work with only {breaks} minutes of break (30 required).", "end", {"break_minutes": breaks}, severity=sev
        )
    elif longest > ctx.threshold("mid_hours") * 60:
        yield Finding(f"{longest / 60:.1f} hours in a row without a break of {int(pause)} minutes.", "end", severity=sev)


@rule(
    "time.avg_48h",
    "Average above 48 hours per week",
    "The weekly working time may not exceed 48 hours on average over 24 weeks (§3 ArbZG).",
    "info",
    "entries",
    thresholds={"max_avg_hours": 48, "weeks": 24},
    threshold_help={"max_avg_hours": "average weekly hours", "weeks": "averaging period in weeks"},
)
def avg_week(ctx: Ctx):
    rd = _record_day(ctx)
    if not rd:
        return
    uid, day = rd
    weeks = int(ctx.threshold("weeks"))
    total = int(ctx.record.get("minutes") or 0)
    for i in range(weeks * 7):
        d: date = day - timedelta(days=i)
        total += sum(int(e.get("minutes") or 0) for e in ctx.entries_of(uid, d.isoformat()) if not (i == 0 and False))
    avg = total / 60 / weeks
    if avg > ctx.threshold("max_avg_hours"):
        yield Finding(f"{avg:.1f} hours per week on average over the last {weeks} weeks.", "minutes", {"average": round(avg, 1)})


@rule(
    "time.weekend_holiday",
    "Work on a weekend or public holiday",
    "Employees may not work on Sundays and public holidays except in special cases (§§9-10 ArbZG). Public holidays "
    "also change your capacity and invoicing assumptions.",
    "info",
    "entries",
)
def weekend_holiday(ctx: Ctx):
    day = parse_date(ctx.record.get("date"))
    if not day or not ctx.changed("date"):
        return
    name = ctx.holiday(day)
    if name:
        yield Finding(f"{day.isoformat()} is a public holiday ({name}).", "date")
    elif day.weekday() == 6:
        yield Finding(f"{day.isoformat()} is a Sunday.", "date")
    elif day.weekday() == 5:
        yield Finding(f"{day.isoformat()} is a Saturday.", "date")


@rule(
    "entry.overlap",
    "Overlapping time entries",
    "One person cannot work two things at the same time; overlapping entries count hours twice.",
    "warning",
    "entries",
)
def entry_overlap(ctx: Ctx):
    rd = _record_day(ctx)
    iv = _interval(ctx.record)
    if not rd or iv is None:
        return
    uid, day = rd
    for e in ctx.entries_of(uid, day.isoformat()):
        oiv = _interval(e)
        if oiv and iv[0] < oiv[1] and oiv[0] < iv[1]:
            yield Finding(
                f"Overlaps the entry {e.get('start')}–{e.get('end')} on the same day.", "start", {"overlaps": e.get("id")}
            )
            return
