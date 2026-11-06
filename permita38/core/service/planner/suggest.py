"""Suggestions on the planner engine (WP4b P3): vacation windows, the first start of an offer, what must move.

Pure functions on a ``Snapshot``. The supply does not depend on the items, so it is built once and only the items of
the one project scope are judged again per candidate; for vacation the supply is rebuilt for the one person whose days
off change. Every search is bounded (MAX_SCAN_DAYS, MAX_WINDOW_DAYS, MAX_SHIFT_WEEKS).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from permita38.core.errors import ServiceError
from permita38.core.service.planner import supply as supply_mod
from permita38.core.service.planner.cover import ItemVerdict, cover
from permita38.core.service.planner.load import Snapshot
from permita38.core.service.planner.model import DayOff, Item, Owner
from permita38.core.service.planner.spread import spread
from permita38.core.service.planning_warnings import _fmt

MAX_SCAN_DAYS = 400
MAX_WINDOW_DAYS = 120
MAX_SHIFT_WEEKS = 26
DRAFT_ID = "draft"
_EPS = 1e-9


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _owner(snap: Snapshot, owner_id: str) -> Owner:
    for o in snap.owners:
        if o.id == owner_id and o.kind != "subcontractor":
            return o
    raise ServiceError(404, "User not found")


# ------------------------------------------------------------------------------------------------ vacation
def _bridge_days(snap: Snapshot, owner: Owner, first: date, last: date) -> int:
    """Free calendar days a block gains: weekends and holidays inside it and directly around it."""

    def off(d: date) -> bool:
        return d.weekday() not in owner.workdays or snap.holiday(d) is not None

    n_off = sum(1 for i in range((last - first).days + 1) if off(first + timedelta(days=i)))
    for step in (-1, 1):
        y = (first if step < 0 else last) + timedelta(days=step)
        n = 0
        while n < 3 and off(y):
            n += 1
            y += timedelta(days=step)
        n_off += n
    return n_off


def suggest_vacation(
    snap: Snapshot, user_id: str, days: int, window_from: date, window_to: date, limit: int = 5
) -> dict:
    """Windows of ``days`` working days, ranked by the increase of the total contract shortfall, then by bridge days."""
    if window_to < window_from:
        raise ServiceError(422, "The window ends before it starts")
    if (window_to - window_from).days > MAX_WINDOW_DAYS:
        raise ServiceError(422, f"The window may be at most {MAX_WINDOW_DAYS} days")
    owner = _owner(snap, user_id)
    hol = snap.holiday

    def free(d: date) -> bool:
        return d.weekday() in owner.workdays and hol(d) is None and d not in owner.days_off

    blocks: list[list[date]] = []
    x = window_from
    while x <= window_to:
        if free(x):
            block: list[date] = []
            y = x
            while len(block) < days and y <= window_to + timedelta(days=days * 7):
                if free(y):
                    block.append(y)
                y += timedelta(days=1)
            if len(block) == days:
                blocks.append(block)
        x += timedelta(days=1)

    hi = max([i.end for i in snap.items] + [window_to + timedelta(days=days * 7), snap.today])
    lo = _monday(snap.today)
    base_futures = supply_mod._spread_all(snap.owners, snap.assignments, hol, snap.today)
    mine = [k for k, a in enumerate(snap.assignments) if a.owner_id == user_id]

    def shortfall(o: Owner) -> float:
        owners = [o if p.id == user_id else p for p in snap.owners]
        futures = list(base_futures)
        for k in mine:
            futures[k] = spread(o, snap.assignments[k], hol, snap.today)
        sup = supply_mod._build(owners, snap.assignments, futures, hol, snap.today, lo, hi)
        return sum(v.shortfall_contracts for v in cover(snap.items, snap.bookings, sup, owners, snap.today, snap.names))

    base = shortfall(owner)
    scored = []
    for block in blocks:
        first, last = block[0], block[-1]
        off: dict[date, DayOff] = {first + timedelta(days=i): "vacation" for i in range((last - first).days + 1)}
        after = shortfall(replace(owner, days_off={**owner.days_off, **off}))
        scored.append((round(max(0.0, after - base), 6), -_bridge_days(snap, owner, first, last), first, last))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return dict(
        options=[
            dict(start=f.isoformat(), end=last.isoformat(), days=days, overload_hours=round(o, 2), bridge_days=-b)
            for o, b, f, last in scored[:limit]
        ],
        considered=len(scored),
    )


# ------------------------------------------------------------------------------------------------ scope helpers
def _scope_items(snap: Snapshot, project_id: str | None) -> list[Item]:
    return [i for i in snap.items if i.project_id == project_id]


def _draft_verdict(
    snap: Snapshot, supply: supply_mod.Supply, items: list[Item], draft: Item, project_id: str | None
) -> ItemVerdict:
    verdicts = cover(items, snap.bookings, supply, snap.owners, snap.today, snap.names)
    for v in verdicts:
        if v.project_id == project_id:
            for it in v.items:
                if it.id == draft.id:
                    return it
    raise ValueError("draft not judged")  # pragma: no cover


def _scope_supply(supply: supply_mod.Supply, project_id: str | None, day: date) -> float:
    if project_id is None:
        return sum(supply.unassigned(p, day) for p in supply.people)
    e, b = supply.project.get(project_id, {}).get(day, (0.0, 0.0))
    return e + b


# ------------------------------------------------------------------------------------------------ first start
def suggest_start(
    snap: Snapshot,
    hours: float,
    deadline: date,
    earliest: date | None,
    project_id: str | None,
    rate: Decimal = Decimal(0),
) -> dict:
    """The first day with supply for the project scope (``None``: the team's unassigned time) from which an offer of
    ``hours`` in total until ``deadline`` has no shortfall next to the other items."""
    first = max(earliest or snap.today, snap.today + timedelta(days=1))
    if deadline < (earliest or snap.today):
        raise ServiceError(422, "The deadline is before the earliest start")
    if (deadline - first).days + 1 > MAX_SCAN_DAYS:
        first = deadline - timedelta(days=MAX_SCAN_DAYS - 1)
    others = _scope_items(snap, project_id)
    hi = max([deadline] + [i.end for i in others])
    supply = supply_mod.build(snap.owners, snap.assignments, snap.holiday, snap.today, _monday(snap.today), hi)

    def fits(start: date, total: float) -> ItemVerdict:
        draft = Item("offer", DRAFT_ID, "suggestion", project_id, start, deadline, None, total, rate, 1.0)
        return _draft_verdict(snap, supply, others + [draft], draft, project_id)

    days = [first + timedelta(days=i) for i in range(max(0, (deadline - first).days + 1))]
    avail = [d for d in days if _scope_supply(supply, project_id, d) > _EPS]
    for s in avail:
        if fits(s, hours).shortfall <= 0.005:
            free = sum(_scope_supply(supply, project_id, d) for d in days if d >= s)
            return dict(start=s.isoformat(), feasible=True, free_hours=round(free, 2))
    total = sum(_scope_supply(supply, project_id, d) for d in days)
    best = round(fits(avail[0], total + 1.0).covered, 2) if avail else 0.0
    return dict(start=None, feasible=False, free_hours=best, max_hours_from_earliest=best, best_free=best)


# ------------------------------------------------------------------------------------------------ what must move
def what_must_move(snap: Snapshot, draft: Item) -> dict:
    """For an offer draft: the smallest shift (whole weeks, either way) of one other open offer of the same project
    that removes its shortfall; plus the largest total that fits."""
    pid = draft.project_id
    others = _scope_items(snap, pid)
    hi = max([draft.end] + [i.end for i in others]) + timedelta(weeks=MAX_SHIFT_WEEKS)
    supply = supply_mod.build(snap.owners, snap.assignments, snap.holiday, snap.today, _monday(snap.today), hi)
    own = _draft_verdict(snap, supply, others + [draft], draft, pid)
    if own.shortfall <= 0.005:
        return dict(fits=True, suggestions=[])
    suggestions: list[dict] = []
    for other in others:
        if other.kind != "offer" or other.start > draft.end or other.end < draft.start:
            continue
        rest = [i for i in others if i is not other]
        found = None
        for k in range(1, MAX_SHIFT_WEEKS + 1):
            for sign in (1, -1):
                shift = timedelta(days=7 * k * sign)
                if other.start + shift <= snap.today:
                    continue
                moved = replace(other, start=other.start + shift, end=other.end + shift)
                if _draft_verdict(snap, supply, rest + [moved, draft], draft, pid).shortfall <= 0.005:
                    found = (7 * k * sign, moved)
                    break
            if found:
                break
        if found:
            days, moved = found
            suggestions.append(
                dict(
                    type="move",
                    kind=other.kind,
                    id=other.id,
                    title=other.title,
                    days=days,
                    message=f"Move {other.title} by {days:+d} days (to {moved.start:%d.%m.%Y} – {moved.end:%d.%m.%Y}).",
                )
            )
    fit = max(0.0, own.promised - own.shortfall)
    suggestions.append(
        dict(
            type="trim",
            max_hours=round(fit, 2),
            message=f"Or reduce the draft from {_fmt(round(own.promised, 2))} h to at most {_fmt(round(fit, 2))} h.",
        )
    )
    suggestions.sort(key=lambda s: abs(s.get("days", 0)) if s["type"] == "move" else 10**9)
    return dict(fits=False, suggestions=suggestions)


__all__ = ["suggest_vacation", "suggest_start", "what_must_move"]
