"""Demand and covering per project (rules D1-D6, C1-C6, M1 value). Pure functions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from permita38.core.money import dec, q2
from permita38.core.service.planner.model import Booking, Item, Owner
from permita38.core.service.planner.supply import Supply
from permita38.core.service.planning_warnings import _fmt

_EPS = 1e-9
_NO_PROJECT_NAME = "the team's unassigned time"


@dataclass
class WeekShortfall:
    week: date
    demand: float
    supply: float
    shortfall: float


@dataclass
class ItemVerdict:
    kind: str
    id: str
    title: str
    type: Literal["weekly", "total"]
    start: date
    end: date
    promised: float
    booked: float
    remaining: float
    covered: float
    shortfall: float
    weekly_shortfalls: list[WeekShortfall]
    rate: Decimal
    value: Decimal
    win_probability: float | None
    explanation: str
    suggestions: list[dict]


@dataclass
class ProjectVerdict:
    project_id: str | None
    supply_employees: float
    supply_subcontractors: float
    remaining_contracts: float
    shortfall_contracts: float
    remaining_offers: float
    shortfall_worst_case: float
    shortfall_expected: float
    status: Literal["ok", "at_risk", "short"]
    items: list[ItemVerdict]
    booked_unattributed: float


@dataclass
class _State:
    """Working state of one item."""

    item: Item
    weekly: bool
    demand: dict[date, float] = field(default_factory=dict)  # weekly: week monday -> demand (all weeks of the period)
    got: dict[date, float] = field(default_factory=dict)  # weekly: attributed hours per week
    got_total: float = 0.0
    remaining_weeks: dict[date, float] = field(default_factory=dict)  # weekly: weeks still to check
    short_weeks: dict[date, WeekShortfall] = field(default_factory=dict)
    week_supply: dict[date, float] = field(
        default_factory=dict
    )  # weekly: leftover of the week before the group took any
    remaining: float = 0.0
    shortfall: float = 0.0
    left: float = 0.0  # total: leftover of the window just before the item took any


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _dmy(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _h(x: float) -> str:
    return _fmt(round(x, 2))


def _pos(x: float) -> bool:
    return round(x, 2) > 0


def _sort_key(it: Item) -> tuple[int, date, date, str]:
    return (0 if it.kind == "contract" else 1, it.end, it.start, it.id)


def _weekly_demand(it: Item) -> dict[date, float]:
    """D2: hours per week x Mon-Fri days of the week inside the period / 5."""
    hpw = it.hours_per_week or 0.0
    out: dict[date, float] = {}
    if it.end < it.start:
        return out
    mon = _monday(it.start)
    while mon <= it.end:
        n = sum(1 for i in range(5) if it.start <= mon + timedelta(days=i) <= it.end)
        out[mon] = hpw * n / 5
        mon += timedelta(days=7)
    return out


def _attribute(states: list[_State], bookings: list[Booking]) -> float:
    """D4. Returns the booked hours that fit no item."""
    unattributed = 0.0
    by_day: dict[date, float] = {}
    for b in bookings:
        by_day[b.day] = by_day.get(b.day, 0.0) + b.hours
    weekly = sorted((s for s in states if s.weekly), key=lambda s: _sort_key(s.item))
    totals = sorted((s for s in states if not s.weekly), key=lambda s: _sort_key(s.item))
    for day in sorted(by_day):
        if not any(s.item.start <= day <= s.item.end for s in states):
            continue  # history outside every item period
        rest = by_day[day]
        mon = _monday(day)
        # must before could: all contracts (weekly, then total) before any offer (weekly, then total)
        for kind in ("contract", "offer"):
            for s in weekly:
                if rest <= _EPS:
                    break
                if s.item.kind == kind and s.item.start <= day <= s.item.end:
                    take = min(rest, max(0.0, s.demand.get(mon, 0.0) - s.got.get(mon, 0.0)))
                    if take > 0:
                        s.got[mon] = s.got.get(mon, 0.0) + take
                        rest -= take
            for s in totals:
                if rest <= _EPS:
                    break
                if s.item.kind == kind and s.item.start <= day <= s.item.end:
                    take = min(rest, max(0.0, (s.item.total_hours or 0.0) - s.got_total))
                    if take > 0:
                        s.got_total += take
                        rest -= take
        if rest > _EPS:
            unattributed += rest
    return unattributed


def _cover_weekly(group: list[_State], leftover: dict[date, float]) -> None:
    """C1/C3 for a group of weekly items: week by week from the leftover of the days."""
    weeks = sorted({w for s in group for w, r in s.remaining_weeks.items() if r > 0})
    for w in weeks:
        days = [w + timedelta(days=i) for i in range(7)]
        supply = sum(leftover.get(d, 0.0) for d in days)
        members = [(s, s.remaining_weeks[w]) for s in group if s.remaining_weeks.get(w, 0.0) > 0]
        demand = sum(r for _, r in members)
        taken = min(demand, supply)
        if supply > 0:
            factor = 1 - taken / supply
            for d in days:
                if d in leftover:
                    leftover[d] = max(0.0, leftover[d] * factor)
        short = max(0.0, demand - supply)
        for s, r in members:
            s.week_supply[w] = supply
            sh = short * r / demand
            s.shortfall += sh
            if _pos(sh):
                s.short_weeks[w] = WeekShortfall(w, round(r, 2), round(r - sh, 2), round(sh, 2))


def _window(it: Item, today: date) -> list[date]:
    first = max(it.start, today + timedelta(days=1))
    if first > it.end:
        return []
    return [first + timedelta(days=i) for i in range((it.end - first).days + 1)]


def _cover_total(s: _State, leftover: dict[date, float], today: date) -> None:
    """C2/C3 for one total item: the earliest days' leftover first."""
    days = [d for d in _window(s.item, today) if d in leftover]
    s.left = sum(leftover[d] for d in days)
    need = s.remaining
    for d in days:
        if need <= _EPS:
            break
        take = min(need, leftover[d])
        leftover[d] -= take
        need -= take
    s.shortfall = max(0.0, need)


def _suggestions(it: Item, supply: Supply, owners: Sequence[Owner], today: date) -> list[dict]:
    """C6: people with unassigned time inside the item's window."""
    days = _window(it, today)
    if not days:
        return []
    owner_names = {o.id: o.name for o in owners}
    out: list[dict] = []
    for pid in supply.people:
        hours = sum(supply.unassigned(pid, d) for d in days if supply.lo <= d <= supply.hi)
        if round(hours, 2) > 0:
            out.append(
                {
                    "user_id": pid,
                    "name": owner_names.get(pid, pid),
                    "hours": round(hours, 2),
                    "from": days[0].isoformat(),
                    "to": days[-1].isoformat(),
                }
            )
    out.sort(key=lambda r: (-r["hours"], r["name"]))
    return out


def _explain(s: _State, project_name: str, today: date) -> str:
    it = s.item
    if s.weekly:
        head = f"{_h(it.hours_per_week or 0.0)} h per week from {_dmy(it.start)} to {_dmy(it.end)}:"
        if not s.short_weeks:
            return f"{head} covered every week."
        weeks = ", ".join(w.strftime("%d.%m.") for w in sorted(s.short_weeks))
        text = f"{head} short {_h(s.shortfall)} h in {len(s.short_weeks)} {'week' if len(s.short_weeks) == 1 else 'weeks'} ({weeks})."
        this_week = _monday(today)
        if this_week in s.short_weeks:
            text += (
                f" This week {_h(s.got.get(this_week, 0.0))} of {_h(it.hours_per_week or 0.0)} h are booked "
                f"and {_h(s.week_supply.get(this_week, 0.0))} h of assigned time is left after today."
            )
        return text
    promised, after = ("promised", "its weekly contracts") if it.kind == "contract" else ("offered", "its contracts")
    if _pos(s.shortfall) and not _window(it, today):
        return (
            f"{_h(it.total_hours or 0.0)} h {promised} − {_h(s.got_total)} h booked = {_h(s.remaining)} h remaining. "
            f"Ended on {_dmy(it.end)} with {_h(s.shortfall)} h not delivered."
        )
    tail = f"Short {_h(s.shortfall)} h." if _pos(s.shortfall) else "Covered."
    return (
        f"{_h(it.total_hours or 0.0)} h {promised} − {_h(s.got_total)} h booked = {_h(s.remaining)} h remaining. "
        f"Until {_dmy(it.end)} {project_name} has {_h(s.left)} h of assigned time left after {after}. {tail}"
    )


def _verdict(s: _State, project_name: str, supply: Supply, owners: Sequence[Owner], today: date) -> ItemVerdict:
    it = s.item
    promised = sum(s.demand.values()) if s.weekly else (it.total_hours or 0.0)
    booked = sum(s.got.values()) if s.weekly else s.got_total
    return ItemVerdict(
        kind=it.kind,
        id=it.id,
        title=it.title,
        type="weekly" if s.weekly else "total",
        start=it.start,
        end=it.end,
        promised=round(promised, 2),
        booked=round(booked, 2),
        remaining=round(s.remaining, 2),
        covered=round(max(0.0, s.remaining - s.shortfall), 2),
        shortfall=round(s.shortfall, 2),
        weekly_shortfalls=[s.short_weeks[w] for w in sorted(s.short_weeks)],
        rate=it.rate,
        value=q2(dec(round(promised, 6)) * it.rate),
        win_probability=it.win * 100 if it.kind == "offer" else None,
        explanation=_explain(s, project_name, today),
        suggestions=_suggestions(it, supply, owners, today) if _pos(s.shortfall) else [],
    )


def _project(
    pid: str | None,
    items: list[Item],
    bookings: list[Booking],
    supply: Supply,
    owners: Sequence[Owner],
    today: date,
    names: Mapping[str, str],
) -> ProjectVerdict:
    if pid is None:
        first = max(today + timedelta(days=1), supply.lo)
        days = [first + timedelta(days=i) for i in range(max(0, (supply.hi - first).days + 1))]
        parts = {d: (sum(supply.unassigned(p, d) for p in supply.people), 0.0) for d in days}
        name = _NO_PROJECT_NAME
    else:
        parts = {d: v for d, v in supply.project.get(pid, {}).items() if d > today}
        name = names.get(pid, pid)
    leftover = {d: e + b for d, (e, b) in parts.items()}

    states = [_State(it, it.hours_per_week is not None) for it in sorted(items, key=_sort_key)]
    this_week = _monday(today)
    for s in states:
        if s.weekly:
            s.demand = _weekly_demand(s.item)
    unattributed = _attribute(states, bookings)
    for s in states:
        if s.weekly:
            for w, dmd in s.demand.items():
                if w < _monday(max(s.item.start, today)):
                    continue
                s.remaining_weeks[w] = max(0.0, dmd - s.got.get(w, 0.0)) if w == this_week else dmd
            s.remaining = sum(s.remaining_weeks.values())
        else:
            s.remaining = max(0.0, (s.item.total_hours or 0.0) - s.got_total)

    for kind in ("contract", "offer"):
        _cover_weekly([s for s in states if s.weekly and s.item.kind == kind], leftover)
        for s in states:
            if not s.weekly and s.item.kind == kind:
                _cover_total(s, leftover, today)

    verdicts = [_verdict(s, name, supply, owners, today) for s in states]
    contracts = [s for s in states if s.item.kind == "contract"]
    offers = [s for s in states if s.item.kind == "offer"]
    sh_contracts = sum(s.shortfall for s in contracts)
    sh_offers = sum(s.shortfall for s in offers)
    sh_expected = sh_contracts + sum(s.shortfall * s.item.win for s in offers)
    status: Literal["ok", "at_risk", "short"] = (
        "short" if _pos(sh_contracts) else "at_risk" if _pos(sh_contracts + sh_offers) else "ok"
    )
    return ProjectVerdict(
        project_id=pid,
        supply_employees=round(sum(e for e, _ in parts.values()), 2),
        supply_subcontractors=round(sum(b for _, b in parts.values()), 2),
        remaining_contracts=round(sum(s.remaining for s in contracts), 2),
        shortfall_contracts=round(sh_contracts, 2),
        remaining_offers=round(sum(s.remaining for s in offers), 2),
        shortfall_worst_case=round(sh_contracts + sh_offers, 2),
        shortfall_expected=round(sh_expected, 2),
        status=status,
        items=verdicts,
        booked_unattributed=round(unattributed, 2),
    )


def cover(
    items: Sequence[Item],
    bookings: Sequence[Booking],
    supply: Supply,
    owners: Sequence[Owner],
    today: date,
    names: Mapping[str, str],
) -> list[ProjectVerdict]:
    """One verdict per project with an item (the caller filtered, D1); ``None`` for the unassigned scope (C5)."""
    groups: dict[str | None, list[Item]] = {}
    for it in items:
        if it.end > supply.hi:
            raise ValueError(f"supply ends before item {it.id}")
        groups.setdefault(it.project_id, []).append(it)
    past = [b for b in bookings if b.day <= today]
    out = [
        _project(pid, its, [b for b in past if b.project_id == pid], supply, owners, today, names)
        for pid, its in groups.items()
    ]
    out.sort(key=lambda v: (v.project_id is None, names.get(v.project_id, v.project_id) if v.project_id else ""))
    return out
