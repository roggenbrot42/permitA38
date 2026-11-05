"""Supply per day and the plausibility warnings about it (rules S2-S6). Pure functions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from permita38.core.service.planner.model import Assignment, Holiday, Owner, WorkLimits
from permita38.core.service.planner.spread import capacity, spread
from permita38.core.service.planning_warnings import _fmt, warning

_EPS = 1e-9
#: S6, §3 ArbZG: 10 h a day, 6 working days of 10 h a week, 24-week average of 8 h x 6 days
_LAW_DAY = 10.0
_LAW_WEEK = 60.0
_LAW_AVERAGE = 48.0
_AVERAGE_WEEKS = 24


@dataclass
class PersonDay:
    capacity: float
    assigned: float
    assigned_customer: float
    assigned_internal: float
    holiday: float
    vacation: float
    absence: float
    busy: float  # hours lost (weekly_hours / workdays per lost workday)


@dataclass
class Supply:
    lo: date
    hi: date
    today: date
    people: dict[str, dict[date, PersonDay]] = field(default_factory=dict)  # employees and boss only
    # project id -> day -> (employees, subcontractors)
    project: dict[str, dict[date, tuple[float, float]]] = field(default_factory=dict)

    def unassigned(self, owner_id: str, day: date) -> float:
        pd = self.people.get(owner_id, {}).get(day)
        return max(0.0, pd.capacity - pd.assigned) if pd else 0.0


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _day_row(owner: Owner, day: date, holiday: Holiday) -> PersonDay:
    daily = owner.weekly_hours / len(owner.workdays) if owner.workdays else 0.0
    cap = capacity(owner, day, holiday)
    hol = vac = absn = busy = 0.0
    if day.weekday() in owner.workdays:
        if holiday(day) is not None:
            hol = daily
        elif day in owner.days_off:
            if owner.days_off[day] == "vacation":
                vac = daily
            else:
                absn = daily
        else:
            busy = min(owner.busy.get(day, 0.0), daily)
    return PersonDay(cap, 0.0, 0.0, 0.0, hol, vac, absn, busy)


def build(
    owners: Sequence[Owner], assignments: Sequence[Assignment], holiday: Holiday, today: date, lo: date, hi: date
) -> Supply:
    """Days after ``today`` inside ``lo..hi`` only."""
    sup = Supply(lo=lo, hi=hi, today=today)
    first = max(lo, today + timedelta(days=1))
    by_id = {o.id: o for o in owners}
    days = [first + timedelta(days=i) for i in range(max(0, (hi - first).days + 1))]
    for o in owners:
        if o.kind != "subcontractor":
            sup.people[o.id] = {d: _day_row(o, d, holiday) for d in days}
    for a in assignments:
        owner = by_id.get(a.owner_id)
        if owner is None:
            continue
        for d, h in spread(owner, a, holiday, today).items():
            if d < first or d > hi:
                continue
            day_map = sup.project.setdefault(a.project_id, {})
            emp, sub = day_map.get(d, (0.0, 0.0))
            if owner.kind == "subcontractor":
                day_map[d] = (emp, sub + h)
                continue
            day_map[d] = (emp + h, sub)
            pd = sup.people[owner.id][d]
            pd.assigned += h
            if a.project_kind == "internal":
                pd.assigned_internal += h
            else:
                pd.assigned_customer += h
    return sup


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _h(x: float) -> str:
    return _fmt(round(x, 2))


def _planned_weeks(o: Owner, assignments: Sequence[Assignment], holiday: Holiday) -> dict[date, float]:
    weeks: dict[date, float] = {}
    for a in assignments:
        if a.owner_id != o.id:
            continue
        for d, h in spread(o, a, holiday, None).items():
            weeks[_monday(d)] = weeks.get(_monday(d), 0.0) + h
    return weeks


def _first_high_average(weeks: dict[date, float], limit: float) -> tuple[date, float] | None:
    for w in sorted(weeks):
        if weeks[w] <= 0:
            continue
        total = sum(weeks.get(w - timedelta(weeks=i), 0.0) for i in range(_AVERAGE_WEEKS))
        avg = round(total / _AVERAGE_WEEKS, 6)
        if avg > limit + _EPS:
            return w, avg
    return None


def _over_capacity(
    o: Owner,
    rows: dict[date, PersonDay],
    ds: list[date],
    mon: date,
    per_project: dict[str, float],
) -> dict | None:
    assigned = sum(rows[d].assigned for d in ds)
    cap = sum(rows[d].capacity for d in ds)
    over_by = round(sum(v for v in (max(0.0, rows[d].assigned - rows[d].capacity) for d in ds) if v > _EPS), 2)
    law_days = [d for d in ds if rows[d].assigned > _LAW_DAY + _EPS] if o.kind == "employee" else []
    law_week = o.kind == "employee" and assigned > _LAW_WEEK + _EPS
    reasons: list[str] = []
    if law_days or law_week:
        reasons.append("working_time_law")
    if over_by > 0:
        lost = any(rows[d].holiday + rows[d].vacation + rows[d].absence > 0 for d in ds)
        reasons.append("time_off" if lost else "weekly_hours")
    if not reasons:
        return None
    parts = ", ".join(f"{p} {_h(h)} h" for p, h in sorted(per_project.items()))
    sentences = [
        f"{_h(rows[d].assigned)} h planned on {_ddmmyyyy(d)}; the law allows at most 10 h a day (§3 ArbZG)."
        for d in law_days
    ]
    if law_week:
        sentences.append(
            f"{_h(assigned)} h planned in the week of {_ddmmyyyy(mon)}; the law allows at most 60 h a week (§3 ArbZG)."
        )
    if over_by > 0:
        sentences.append(
            f"Week of {_ddmmyyyy(mon)}: {o.name} is assigned {_h(assigned)} h ({parts}) but has "
            f"{_h(cap)} h of capacity: over by {_h(over_by)} h. The projects still count the full hours."
        )
    return warning(
        "assignment.over_capacity",
        "warning",
        None,
        " ".join(sentences),
        {
            "user_id": o.id,
            "week": mon.isoformat(),
            "assigned": round(assigned, 2),
            "capacity": round(cap, 2),
            "over_by": over_by,
            "reason": reasons[0],
            "reasons": reasons,
            "projects": [{"project_id": p, "hours": round(h, 2)} for p, h in sorted(per_project.items())],
        },
    )


def _boss_warnings(o: Owner, rows: dict[date, PersonDay], ds: list[date], mon: date) -> list[dict]:
    lim = o.limits or WorkLimits()
    out: list[dict] = []
    for d in ds:
        h = rows[d].assigned
        if h > lim.max_day + _EPS:
            code, sev = "assignment.impossible_day", "warning"
            msg = f"{_h(h)} h planned on {_ddmmyyyy(d)}; that is more than your maximum of {_h(lim.max_day)} h."
        elif h > lim.warn_day + _EPS:
            code, sev = "assignment.very_long_day", "warning"
            msg = f"{_h(h)} h planned on {_ddmmyyyy(d)}; that is more than {_h(lim.warn_day)} h a day."
        elif h > lim.info_day + _EPS:
            code, sev = "assignment.long_day", "info"
            msg = f"{_h(h)} h planned on {_ddmmyyyy(d)}; that is more than {_h(lim.info_day)} h a day."
        else:
            continue
        out.append(warning(code, sev, None, msg, {"user_id": o.id, "day": d.isoformat(), "hours": round(h, 2)}))
    assigned = sum(rows[d].assigned for d in ds)
    if assigned > lim.info_week + _EPS:
        out.append(
            warning(
                "assignment.long_week",
                "info",
                None,
                f"{_h(assigned)} h planned in the week of {_ddmmyyyy(mon)}; "
                f"that is more than {_h(lim.info_week)} h a week.",
                {"user_id": o.id, "week": mon.isoformat(), "hours": round(assigned, 2)},
            )
        )
    return out


def _average_warning(o: Owner, assignments: Sequence[Assignment], holiday: Holiday) -> dict | None:
    limit = (o.limits or WorkLimits()).info_average if o.kind == "boss" else _LAW_AVERAGE
    hit = _first_high_average(_planned_weeks(o, assignments, holiday), limit)
    if hit is None:
        return None
    w, avg = hit
    where = f"{_AVERAGE_WEEKS} weeks up to the week of {_ddmmyyyy(w)}"
    details = {"user_id": o.id, "week": w.isoformat(), "average": avg}
    if o.kind == "employee":
        return warning(
            "assignment.working_time",
            "warning",
            None,
            f"Planned hours average {_h(avg)} h a week over the {where}; "
            "the law allows an average of 48 h a week (§3 ArbZG).",
            details,
        )
    return warning(
        "assignment.high_average",
        "info",
        None,
        f"Planned hours average {_h(avg)} h a week over the {where}, more than your {_h(limit)} h.",
        details,
    )


def warnings(
    owners: Sequence[Owner], assignments: Sequence[Assignment], holiday: Holiday, today: date, lo: date, hi: date
) -> list[dict]:
    sup = build(owners, assignments, holiday, today, lo, hi)
    out: list[dict] = []
    for o in owners:
        if o.kind == "subcontractor":
            continue
        rows = sup.people.get(o.id, {})
        weeks: dict[date, list[date]] = {}
        for d in sorted(rows):
            weeks.setdefault(_monday(d), []).append(d)
        for mon in sorted(weeks):
            ds = weeks[mon]
            per_project: dict[str, float] = {}
            for a in assignments:
                if a.owner_id == o.id:
                    h = sum(v for d, v in spread(o, a, holiday, today).items() if d in rows and d in ds)
                    if h > 0:
                        per_project[a.project_id] = per_project.get(a.project_id, 0.0) + h
            w = _over_capacity(o, rows, ds, mon, per_project)
            if w:
                out.append(w)
            if o.kind == "boss":
                out.extend(_boss_warnings(o, rows, ds, mon))
        avg = _average_warning(o, assignments, holiday)
        if avg:
            out.append(avg)
    return out
