"""The JSON of ``GET /api/plan`` and the verdict warnings (WP4a). Pure functions, no storage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

from permita38.core.money import dec, q2
from permita38.core.service.planner.cover import ItemVerdict, ProjectVerdict, WeekShortfall
from permita38.core.service.planner.model import Assignment, Holiday, Owner
from permita38.core.service.planner.spread import spread
from permita38.core.service.planner.supply import Supply
from permita38.core.service.planning_warnings import _fmt, warning

UNASSIGNED = "unassigned"
_UNASSIGNED_NAME = "Unassigned"


def _h(x: float) -> str:
    return _fmt(round(x, 2))


def _r(x: float) -> float:
    return round(x, 2)


def _money(x: Decimal) -> float:
    return float(q2(x))


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _weekly_json(w: WeekShortfall) -> dict:
    return {"week": w.week.isoformat(), "demand": _r(w.demand), "supply": _r(w.supply), "shortfall": _r(w.shortfall)}


def verdict_warnings(verdicts: Sequence[ProjectVerdict], names: Mapping[str, str]) -> list[dict]:
    """Shortfall, missing supply, unattributed booked hours and items without a project (order: project, then items)."""
    out: list[dict] = []
    for v in verdicts:
        pid = v.project_id
        name = names.get(pid, pid) if pid else _UNASSIGNED_NAME
        remaining = v.remaining_contracts + v.remaining_offers
        supply = v.supply_employees + v.supply_subcontractors
        if pid is not None and round(remaining, 2) > 0 and round(supply, 2) <= 0:
            out.append(
                warning(
                    "project.no_supply",
                    "warning",
                    None,
                    f"{name} has {_h(remaining)} h of remaining work and 0 h of assigned time; "
                    "nobody is assigned to it in the planned period.",
                    {"project_id": pid, "remaining": _r(remaining), "supply": _r(supply)},
                )
            )
        if round(v.booked_unattributed, 2) > 0:
            out.append(
                warning(
                    "booked.unattributed",
                    "info",
                    None,
                    f"{_h(v.booked_unattributed)} h booked on {name} fit no contract or offer of the project.",
                    {"project_id": pid, "hours": _r(v.booked_unattributed)},
                )
            )
        for it in v.items:
            if it.shortfall > 0:
                code = "contract.shortfall" if it.kind == "contract" else "offer.shortfall"
                out.append(
                    warning(
                        code,
                        "warning",
                        None,
                        it.explanation,
                        {
                            "project_id": pid,
                            "item_id": it.id,
                            "kind": it.kind,
                            "shortfall": _r(it.shortfall),
                            "weekly_shortfalls": [_weekly_json(w) for w in it.weekly_shortfalls],
                        },
                    )
                )
            if pid is None:
                label = "Contract" if it.kind == "contract" else "Offer"
                out.append(
                    warning(
                        "contract.no_project",
                        "info",
                        None,
                        f"{label} {it.title} has no project; its {_h(it.remaining)} h remaining are covered "
                        "by the team's unassigned time.",
                        {"item_id": it.id, "kind": it.kind, "remaining": _r(it.remaining)},
                    )
                )
    return out


def _item_json(it: ItemVerdict, invoiced: Mapping[str, Decimal]) -> dict:
    paid = dec(invoiced.get(it.id, 0)) if it.kind == "contract" else Decimal(0)
    expected = None
    if it.kind == "offer":
        expected = _money(it.value * dec(round(it.win_probability or 0.0, 6)) / 100)
    return {
        "kind": it.kind,
        "id": it.id,
        "title": it.title,
        "type": it.type,
        "start": it.start.isoformat(),
        "end": it.end.isoformat(),
        "promised": _r(it.promised),
        "booked": _r(it.booked),
        "remaining": _r(it.remaining),
        "covered": _r(it.covered),
        "shortfall": _r(it.shortfall),
        "weekly_shortfalls": [_weekly_json(w) for w in it.weekly_shortfalls],
        "rate": _money(it.rate),
        "value": _money(it.value),
        "invoiced": _money(paid),
        "open": _money(it.value - paid),
        "win_probability": None if it.win_probability is None else _r(it.win_probability),
        "expected_value": expected,
        "explanation": it.explanation,
        "suggestions": [dict(s) for s in it.suggestions],
    }


def _project_json(
    v: ProjectVerdict, names: Mapping[str, str], cost: Mapping[str, Decimal], invoiced: Mapping[str, Decimal]
) -> dict:
    pid = v.project_id
    value = sum((q2(i.value) for i in v.items if i.kind == "contract"), Decimal(0))
    spent = q2(dec(cost.get(pid, 0))) if pid is not None else Decimal(0)
    return {
        "project_id": pid if pid is not None else UNASSIGNED,
        "name": names.get(pid, pid) if pid is not None else _UNASSIGNED_NAME,
        "kind": "customer" if pid is not None else UNASSIGNED,
        "supply": {
            "employees": _r(v.supply_employees),
            "subcontractors": _r(v.supply_subcontractors),
            "total": _r(v.supply_employees + v.supply_subcontractors),
        },
        "remaining_contracts": _r(v.remaining_contracts),
        "shortfall_contracts": _r(v.shortfall_contracts),
        "remaining_offers": _r(v.remaining_offers),
        "shortfall_worst_case": _r(v.shortfall_worst_case),
        "shortfall_expected": _r(v.shortfall_expected),
        "status": v.status,
        "planned_subcontractor_cost": _money(spent),
        "contract_value": _money(value),
        "margin": _money(value - spent),
        "items": [_item_json(i, invoiced) for i in v.items],
    }


def _internal_json(entry: Mapping, supply: Supply) -> dict:
    pid = entry["project_id"]
    assigned = sum(e + s for e, s in supply.project.get(pid, {}).values())
    budget = entry.get("budget_hours")
    booked = float(entry.get("booked") or 0.0)
    over = budget is not None and budget > 0 and booked + assigned > float(budget) + 1e-9
    return {
        "project_id": pid,
        "name": entry.get("name", pid),
        "kind": "internal",
        "assigned": _r(assigned),
        "booked": _r(booked),
        "budget_hours": None if budget is None else _r(float(budget)),
        "status": "over_budget" if over else "ok",
    }


def _week_rows(supply: Supply, owner_ids: Sequence[str], with_subcontractors: bool) -> list[dict]:
    rows: list[dict] = []
    mon = _monday(supply.lo)
    while mon <= supply.hi:
        days = [mon + timedelta(days=i) for i in range(7)]
        cap = hol = vac = absn = busy = cust = intr = unas = over = 0.0
        for oid in owner_ids:
            for d in days:
                pd = supply.people.get(oid, {}).get(d)
                if pd is None:
                    continue
                cap += pd.capacity
                hol += pd.holiday
                vac += pd.vacation
                absn += pd.absence
                busy += pd.busy
                cust += pd.assigned_customer
                intr += pd.assigned_internal
                unas += max(0.0, pd.capacity - pd.assigned)
                over += max(0.0, pd.assigned - pd.capacity)
        subs = 0.0
        if with_subcontractors:
            for per_day in supply.project.values():
                subs += sum(per_day[d][1] for d in days if d in per_day)
        rows.append(
            {
                "week": mon.isoformat(),
                "week_end": days[6].isoformat(),
                "capacity": _r(cap),
                "holiday_hours": _r(hol),
                "vacation_hours": _r(vac),
                "absence_hours": _r(absn),
                "busy_hours": _r(busy),
                "assigned_customer": _r(cust),
                "assigned_internal": _r(intr),
                "unassigned": _r(unas),
                "over_assigned": _r(over),
                "billable_share": _r(cust / cap * 100) if _r(cap) > 0 else None,
                "subcontractor_hours": _r(subs),
            }
        )
        mon += timedelta(days=7)
    return rows


def plan_json(
    supply: Supply,
    owners: Sequence[Owner],
    assignments: Sequence[Assignment],
    verdicts: Sequence[ProjectVerdict],
    internal: Sequence[dict],
    names: Mapping[str, str],
    subcontractor_cost: Mapping[str, Decimal],
    invoiced: Mapping[str, Decimal],
    warnings: Sequence[dict],
    *,
    holiday: Holiday,
    user_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """The body of ``GET /api/plan``. ``holiday`` is the same calendar the supply was built with (required: the
    per-assignment hours of ``people`` are spread again)."""
    people_owners = [o for o in owners if o.kind != "subcontractor" and (user_id is None or o.id == user_id)]
    weeks = _week_rows(supply, [o.id for o in people_owners], user_id is None)

    people: list[dict] = []
    for o in people_owners:
        rows = supply.people.get(o.id, {})
        cap = sum(pd.capacity for pd in rows.values())
        assigned = sum(pd.assigned for pd in rows.values())
        unassigned = sum(max(0.0, pd.capacity - pd.assigned) for pd in rows.values())
        listed = []
        for a in assignments:
            if a.owner_id != o.id:
                continue
            hours = sum(h for d, h in spread(o, a, holiday, supply.today).items() if d in rows)
            if _r(hours) > 0:
                listed.append({"id": a.id, "project_id": a.project_id, "hours": _r(hours)})
        people.append(
            {
                "user_id": o.id,
                "name": o.name,
                "capacity": _r(cap),
                "assigned": _r(assigned),
                "unassigned": _r(unassigned),
                "assignments": listed,
            }
        )

    projects = [_project_json(v, names, subcontractor_cost, invoiced) for v in verdicts]
    projects.extend(_internal_json(e, supply) for e in sorted(internal, key=lambda e: str(e.get("name", ""))))
    if project_id is not None:
        projects = [p for p in projects if p["project_id"] == project_id]

    return {
        "today": supply.today.isoformat(),
        "weeks": weeks,
        "people": people,
        "projects": projects,
        "warnings": list(warnings),
    }
