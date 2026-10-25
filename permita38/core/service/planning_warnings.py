"""Non-blocking warnings: numbers that do not add up on offers and invoices, and project periods that do not fit
their time entries, contracts or plans.

Format (shared with the sanity layer, which may later use ``confirmable`` in its confirm flow)::

    {"code": "rate.below_minimum", "severity": "warning", "field": "lines", "message": "...",
     "details": {...}, "confirmable": True}

Pure functions: they get the data they need as arguments and never touch the store.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from permita38.core import calc
from permita38.core.money import dec, q2

#: tolerance before a mismatch between hours in lines and hours in the plan is reported
HOURS_TOLERANCE = 0.5


def warning(
    code: str, severity: str, field: str | None, message: str, details: dict | None = None, confirmable: bool = True
) -> dict:
    return dict(
        code=code, severity=severity, field=field, message=message, details=details or {}, confirmable=confirmable
    )


def _fmt(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def plan_hours(plan: dict | None) -> float | None:
    """Hours a plan promises: total hours, or hours per week times the weeks of the period."""
    if not plan:
        return None
    total = calc.fnum(plan.get("total_hours"))
    if total:
        return total
    hpw = calc.fnum(plan.get("hours_per_week"))
    s, e = calc.d(plan.get("start")), calc.d(plan.get("end"))
    if hpw and s and e and e >= s:
        return hpw * ((e - s).days + 1) / 7.0
    return None


def line_hours(lines: list[dict]) -> Decimal:
    """Sum of quantities of the lines billed in hours (unit ``h``)."""
    total = Decimal(0)
    for ln in lines or []:
        if isinstance(ln, dict) and str(ln.get("unit") or "").strip().lower() in (
            "h",
            "std",
            "stunde",
            "stunden",
            "hour",
            "hours",
        ):
            total += dec(ln.get("qty"))
    return total


def document_warnings(
    doc: dict,
    *,
    kind: str,
    settings: dict,
    reference_rate: float | None = None,
    tracked_hours: float | None = None,
    offer_budget_hours: float | None = None,
    billed_on_offer_hours: float | None = None,
) -> list[dict]:
    """Consistency warnings for an offer (``kind="offers"``) or an invoice (``kind="invoices"``).

    ``reference_rate``: project / customer / default rate. ``tracked_hours``: time tracked for the invoice's
    selection. ``offer_budget_hours``: hours of the offer's plan (invoice only). ``billed_on_offer_hours``: hours
    already billed on other invoices of the same offer.
    """
    out: list[dict] = []
    lines = [ln for ln in (doc.get("lines") or []) if isinstance(ln, dict)]
    hours = float(line_hours(lines))
    net = sum((dec(ln.get("amount")) for ln in lines), Decimal(0))
    if kind == "offers" and doc.get("plan"):
        promised = plan_hours(doc.get("plan"))
        if promised and hours and abs(hours - promised) > max(HOURS_TOLERANCE, promised * 0.01):
            out.append(
                warning(
                    "offer.hours_mismatch",
                    "warning",
                    "lines",
                    f"The lines bill {_fmt(hours)} h, but the plan promises {_fmt(promised)} h.",
                    dict(line_hours=hours, plan_hours=promised),
                )
            )
    if kind == "invoices" and offer_budget_hours is not None and billed_on_offer_hours is not None:
        total = billed_on_offer_hours + hours
        if total > offer_budget_hours + 1e-9:
            out.append(
                warning(
                    "invoice.exceeds_offer_hours",
                    "warning",
                    "lines",
                    f"Billed {_fmt(total)} h on this offer, but it only covers {_fmt(offer_budget_hours)} h.",
                    dict(billed_hours=total, offer_hours=offer_budget_hours),
                )
            )
    if kind == "invoices" and tracked_hours is not None and hours:
        if hours > tracked_hours + 1e-9:
            out.append(
                warning(
                    "invoice.hours_exceed_tracked",
                    "warning",
                    "lines",
                    f"The invoice bills {_fmt(hours)} h, but only {_fmt(tracked_hours)} h were tracked.",
                    dict(line_hours=hours, tracked_hours=tracked_hours),
                )
            )
    minimum = calc.fnum(settings.get("planning_min_rate"))
    for i, ln in enumerate(lines, 1):
        price = dec(ln.get("price"))
        is_hours = str(ln.get("unit") or "").strip().lower() in ("h", "std", "stunde", "stunden", "hour", "hours")
        if not is_hours or price <= 0:
            continue
        if minimum and float(price) < minimum:
            out.append(
                warning(
                    "rate.below_minimum",
                    "warning",
                    "lines",
                    f"Line {i}: {_fmt(float(price))} EUR per hour is below your minimum rate of {_fmt(minimum)} EUR.",
                    dict(line=i, price=float(price), minimum=minimum),
                )
            )
        if reference_rate and float(price) < reference_rate:
            out.append(
                warning(
                    "rate.below_reference",
                    "warning",
                    "lines",
                    f"Line {i}: {_fmt(float(price))} EUR per hour is below the rate of {_fmt(reference_rate)} EUR "
                    "for this project or customer.",
                    dict(line=i, price=float(price), reference=reference_rate),
                )
            )
    if hours and net:
        effective = float(q2(net / Decimal(str(hours))))
        if minimum and effective < minimum:
            out.append(
                warning(
                    "rate.effective_below_minimum",
                    "warning",
                    "lines",
                    f"The effective hourly rate is {_fmt(effective)} EUR, below your minimum of {_fmt(minimum)} EUR.",
                    dict(effective_rate=effective, minimum=minimum),
                )
            )
    return out


def project_warnings(project: dict, entries: list[dict], plans: list[dict], customer_linked: bool) -> list[dict]:
    """Project period checks: missing dates, time or contracts outside the period, budget overruns."""
    out: list[dict] = []
    s, e = calc.d(project.get("start")), calc.d(project.get("end"))
    if not s or not e:
        if customer_linked and not project.get("personal"):
            out.append(
                warning(
                    "project.no_dates",
                    "warning",
                    "start",
                    "This customer project has no start and end date yet. Set the dates.",
                    confirmable=True,
                )
            )
        return out
    outside = 0
    for x in entries:
        d = calc.d(x.get("date"))
        if d and (d < s or d > e):
            outside += 1
    if outside:
        out.append(
            warning(
                "project.entries_outside_period",
                "warning",
                "start",
                f"{outside} time entr{'y' if outside == 1 else 'ies'} fall outside the project period.",
                dict(count=outside, start=s.isoformat(), end=e.isoformat()),
            )
        )
    for p in plans:
        ps, pe = calc.d(p.get("start")), calc.d(p.get("end"))
        if ps and pe and (ps < s or pe > e):
            out.append(
                warning(
                    "project.plan_outside_period",
                    "warning",
                    "end",
                    f"The contract or offer “{p.get('title') or '?'}” runs outside the project period.",
                    dict(id=p.get("id"), start=ps.isoformat(), end=pe.isoformat()),
                )
            )
    return out


def budget_warning(project: dict, tracked_hours: float) -> list[dict]:
    budget = calc.fnum(project.get("budget_hours"))
    if budget and tracked_hours > budget + 1e-9:
        return [
            warning(
                "project.over_budget",
                "warning",
                "budget_hours",
                f"{_fmt(tracked_hours)} h tracked, the budget is {_fmt(budget)} h.",
                dict(tracked_hours=tracked_hours, budget_hours=budget),
            )
        ]
    return []


# ---- allocations (docs/contracts/planner-per-project.md, "Warning codes") ---------------------------------------


SAVE_WARNING_WEEKS = 10  # at most this many weeks per warning code when an allocation is saved


def _monday(x: date) -> date:
    return x - timedelta(days=x.weekday())


def week_starts(start: date, end: date) -> list[date]:
    """The Mondays of every week that touches ``start..end`` (earliest first)."""
    out, ws = [], _monday(start)
    while ws <= end:
        out.append(ws)
        ws += timedelta(days=7)
    return out


def overlaps_week(alloc: dict, ws: date) -> bool:
    return alloc["start"] <= (ws + timedelta(days=6)).isoformat() and alloc["end"] >= ws.isoformat()


def employee_over_allocated(
    user_id: str,
    name: str,
    week: str,
    allocated: float,
    weekly_hours: float,
    reason: str,
    field_name: str = "weeks",
) -> dict:
    over_by = round(allocated - weekly_hours, 6)
    if reason == "time_off":
        text = (
            f"Week of {week}: {name} is allocated {_fmt(allocated)} h but only {_fmt(weekly_hours)} h of capacity "
            f"remain after holidays and time off (over by {_fmt(over_by)} h). The planner scales the allocations down."
        )
    else:
        text = (
            f"Week of {week}: {name} is allocated {_fmt(allocated)} h but works {_fmt(weekly_hours)} h per week "
            f"(over by {_fmt(over_by)} h). The planner scales the allocations down."
        )
    return warning(
        "allocation.employee_over_allocated",
        "warning",
        field_name,
        text,
        dict(
            user_id=user_id,
            week=week,
            allocated=round(allocated, 6),
            weekly_hours=round(weekly_hours, 6),
            over_by=over_by,
            reason=reason,
        ),
        confirmable=True,
    )


def over_allocation_on_save(user: dict, allocs: list[dict], start: str, end: str) -> list[dict]:
    """``reason: "weekly_hours"`` warnings for the weeks of ``start..end`` in which the person's active employee
    allocations (``allocs``, the saved row included) add up to more than their weekly hours. Earliest first, at most 10."""
    weekly = calc.fnum(user.get("weekly_hours"))
    out: list[dict] = []
    for ws in week_starts(date.fromisoformat(start), date.fromisoformat(end)):
        allocated = sum(calc.fnum(a.get("hours_per_week")) for a in allocs if overlaps_week(a, ws))
        if allocated > weekly + 1e-9:
            out.append(
                employee_over_allocated(
                    user["id"], user.get("name") or "?", ws.isoformat(), allocated, weekly, "weekly_hours", "allocations"
                )
            )
            if len(out) >= SAVE_WARNING_WEEKS:
                break
    return out


def overlap_same_project(
    allocs: list[dict], user_id: str, project_id: str, start: str, end: str, project_name: str = ""
) -> list[dict]:
    """One info warning per week of ``start..end`` in which the person has two or more allocations to the project."""
    mine = [a for a in allocs if a.get("user_id") == user_id and a.get("project_id") == project_id]
    out: list[dict] = []
    for ws in week_starts(date.fromisoformat(start), date.fromisoformat(end)):
        ids = [a["id"] for a in mine if overlaps_week(a, ws)]
        if len(ids) >= 2:
            out.append(
                warning(
                    "allocation.overlap_same_project",
                    "info",
                    "allocations",
                    f"Week of {ws.isoformat()}: {len(ids)} allocations of the same person to "
                    f"{project_name or 'the same project'} overlap; their hours add up.",
                    dict(user_id=user_id, project_id=project_id, week=ws.isoformat(), allocation_ids=ids),
                    confirmable=False,
                )
            )
            if len(out) >= SAVE_WARNING_WEEKS:
                break
    return out
