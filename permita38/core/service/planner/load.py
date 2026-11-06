"""Loading the planner's input from the store (WP4b P1, docs/specs/project-centric-planning.md).

``load()`` turns the stored rows into the pure data classes of ``planner/model.py``. Damaged legacy rows are left out
and listed in ``Snapshot.skipped``; they never raise. What-if ``changes`` are applied in memory only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from permita38.core import calc
from permita38.core import holidays as holidays_module
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import Repository
from permita38.core.service.busy import BusyTimeProvider
from permita38.core.service.planner.model import (
    Assignment,
    Booking,
    DayOff,
    Holiday,
    Item,
    Owner,
    WorkLimits,
)
from permita38.core.service.planner.spread import planned_net
from permita38.core.service.planning_warnings import line_hours
from permita38.core.settings import effective_settings

DELETED_PROJECT = "(deleted project)"
MAX_ALLOCATION_DAYS = 1830
MAX_ABSENCE_DAYS = 400
BUSY_WEEKS = 156
CLOSED_PROJECT = ("done", "archived")
DEFAULT_WORKDAYS = (0, 1, 2, 3, 4)
NOT_INVOICED = ("draft", "cancelled")


@dataclass
class Snapshot:
    today: date
    holiday: Holiday
    owners: list[Owner] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    bookings: list[Booking] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    internal: list[dict] = field(default_factory=list)
    invoiced: dict[str, Decimal] = field(default_factory=dict)
    subcontractor_cost: dict[str, Decimal] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)


# -- what-if changes (copied from the former planning.py) ---------------------------------------------------------
def _key(kind: str, rid: str) -> str:
    return f"{kind}:{rid}"


def apply_changes(absences: list[dict], changes: dict | None) -> tuple[list[dict], dict[str, dict]]:
    """The absences after the what-if changes and a map of commitment overrides keyed by kind:id."""
    changes = changes or {}
    base = {a["id"]: dict(a) for a in absences if a.get("id")}
    out: list[dict] = [dict(a) for a in absences if not a.get("id")]
    for i, ch in enumerate(changes.get("absences") or []):
        rid = ch.get("id")
        if rid and rid in base:
            if ch.get("delete"):
                base.pop(rid)
            else:
                merged = dict(base[rid])
                for k in ("user_id", "kind", "start", "end"):
                    if ch.get(k) is not None:
                        merged[k] = ch[k]
                base[rid] = merged
        elif not ch.get("delete") and ch.get("start") and ch.get("user_id"):
            out.append(
                dict(
                    id=rid or f"new{i}",
                    user_id=ch["user_id"],
                    kind=ch.get("kind") or "vacation",
                    start=ch["start"],
                    end=ch.get("end") or ch["start"],
                )
            )
    overrides = {}
    for c in changes.get("commitments") or []:
        overrides[_key(c["kind"], c["id"])] = c
    return list(base.values()) + out, overrides


def _usable_allocation(row: dict) -> bool:
    """A row the engine can use at all (an owner, a project, a valid period); damaged rows are skipped."""
    if bool(row.get("user_id")) == bool(row.get("supplier_id")):
        return False
    for k in ("user_id", "supplier_id", "project_id"):
        if row.get(k) is not None and not (isinstance(row[k], str) and row[k]):
            return False
    if not row.get("project_id"):
        return False
    s, e = calc.d(row.get("start")), calc.d(row.get("end"))
    return bool(s and e and e >= s and isinstance(row.get("start"), str) and isinstance(row.get("end"), str))


def apply_allocation_changes(
    rows: list[dict], changes: dict | None, users: dict[str, dict], projects: dict[str, dict]
) -> list[dict]:
    """The allocation rows after the what-if changes (replace / append / remove) in memory.

    Errors: 404 for an unknown allocation, user or project; 409 for a subcontractor row or an inactive/closed target.
    Cancelled rows pass through unchanged (they cannot be changed here)."""
    base = {r["id"]: dict(r) for r in rows if r.get("id")}
    for i, ch in enumerate((changes or {}).get("allocations") or []):
        rid = ch.get("id")
        if rid:
            old = base.get(rid)
            if old is None:
                raise ServiceError(404, "Allocation not found")
            if old.get("supplier_id") or not old.get("user_id") or old.get("status", "active") != "active":
                raise ServiceError(409, "This allocation cannot be changed here")
            if ch.get("delete"):
                base.pop(rid)
                continue
            merged = dict(old)
            for k in ("user_id", "project_id", "start", "end", "hours_per_week"):
                if ch.get(k) is not None:
                    merged[k] = ch[k]
        else:
            merged = dict(
                id=f"new{i}",
                status="active",
                **{
                    k: ch[k]
                    for k in ("user_id", "project_id", "start", "end", "hours_per_week")
                    if ch.get(k) is not None
                },
            )
        if not all(isinstance(merged.get(k), str) and calc.d(merged.get(k)) for k in ("start", "end")):
            raise ServiceError(422, "The allocation has no valid period")
        user = users.get(merged.get("user_id") or "")
        if user is None:
            raise ServiceError(404, "User not found")
        project = projects.get(merged.get("project_id") or "")
        if project is None:
            raise ServiceError(404, "Project not found")
        if merged["end"] < merged["start"]:
            raise ServiceError(409, "The end date is before the start date")
        if (date.fromisoformat(merged["end"]) - date.fromisoformat(merged["start"])).days > MAX_ALLOCATION_DAYS:
            raise ServiceError(422, f"The period may be at most {MAX_ALLOCATION_DAYS} days")
        if (project.get("status") or "active") in CLOSED_PROJECT:
            raise ServiceError(409, "The project is closed")
        if not user.get("active", True):
            raise ServiceError(409, "The user is not active")
        base[merged["id"]] = merged
    return list(base.values())


# -- helpers ----------------------------------------------------------------------------------------------------
def _holiday_fn(state: str) -> Holiday:
    cache: dict[int, dict[date, str]] = {}

    def holiday(day: date) -> str | None:
        if day.year not in cache:
            cache[day.year] = holidays_module.holidays(day.year, state)
        return cache[day.year].get(day)

    return holiday


def _workdays(row: dict) -> tuple[int, ...]:
    days = row.get("workdays")
    if isinstance(days, list):
        ok = sorted({x for x in days if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6})
        if ok:
            return tuple(ok)
    return DEFAULT_WORKDAYS


def _day_off_kind(kind: Any) -> DayOff:
    return "vacation" if kind == "vacation" else "sick" if kind == "sick" else "other"


def _limits(value: Any) -> WorkLimits:
    """The boss's limits from the setting; a missing or damaged value falls back to the defaults per field."""
    default = WorkLimits()
    if not isinstance(value, dict):
        return default
    vals = {}
    for name in ("info_day", "warn_day", "max_day", "info_week", "info_average"):
        v = calc.fnum(value.get(name), getattr(default, name))
        vals[name] = v if v > 0 else getattr(default, name)
    return WorkLimits(**vals)


def _rate(*sources: Any) -> Decimal:
    for r in sources:
        if r not in (None, ""):
            return dec(r)
    return Decimal(0)


def _hours(row: dict) -> tuple[float | None, float | None] | None:
    """(hours_per_week, total_hours) with exactly one set, else None."""
    hpw, total = calc.fnum(row.get("hours_per_week")), calc.fnum(row.get("total_hours"))
    if (hpw > 0) == (total > 0):
        return None
    return (hpw, None) if hpw > 0 else (None, total)


def _skip(skipped: list[dict], coll: str, row: dict, reason: str) -> None:
    skipped.append({"coll": coll, "id": row.get("id"), "reason": reason})


def _plan_hours_fields(plan: dict, offer: dict) -> dict:
    """The plan's hour fields; without any, the sum of the offer's ``h`` lines is its total (like accepting)."""
    out = dict(plan)
    if not calc.fnum(plan.get("hours_per_week")) and not calc.fnum(plan.get("total_hours")):
        lines = line_hours(offer.get("lines") or [])
        if lines > 0:
            out["total_hours"] = float(lines)
    return out


# -- load -------------------------------------------------------------------------------------------------------
def load(store: Repository, today: date, busy: BusyTimeProvider | None = None, changes: dict | None = None) -> Snapshot:
    skipped: list[dict] = []
    absences, overrides = apply_changes(store.list("absences"), changes)
    ctx = calc.context(store)
    settings = effective_settings(ctx["settings"])
    holiday = _holiday_fn(settings.get("state") or "")
    users, projects, customers = ctx["users"], ctx["projects"], ctx["customers"]
    default_rate = settings.get("default_rate")

    # absences -> days off per user
    off: dict[str, dict[date, DayOff]] = defaultdict(dict)
    for a in absences:
        uid, s = a.get("user_id"), calc.d(a.get("start"))
        e = calc.d(a.get("end") or a.get("start"))
        if not uid or not s or not e or e < s:
            _skip(skipped, "absences", a, "incomplete or reversed period")
            continue
        kind = _day_off_kind(a.get("kind"))
        for day in calc.daterange(s, min(e, s + timedelta(days=MAX_ABSENCE_DAYS))):
            off[uid][day] = kind

    # owners
    owners: dict[str, Owner] = {}
    busy_lo = today - timedelta(days=today.weekday())
    busy_hi = busy_lo + timedelta(weeks=BUSY_WEEKS) - timedelta(days=1)
    for u in users.values():
        if not u.get("active", True):
            continue
        is_boss = u.get("role") == "boss"
        owners[u["id"]] = Owner(
            id=u["id"],
            name=u.get("name") or u.get("username") or u["id"],
            kind="boss" if is_boss else "employee",
            weekly_hours=calc.fnum(u.get("weekly_hours")),
            workdays=_workdays(u),
            days_off=dict(off.get(u["id"], {})),
            busy=dict(busy.busy_hours(u["id"], busy_lo, busy_hi, ooo_only=True) or {}) if busy else {},
            limits=_limits(ctx["settings"].get(f"work_limits.{u['id']}")) if is_boss else None,
        )
    suppliers = {
        s["id"]: s for s in store.list("suppliers") if s.get("kind") == "person-capacity" and s.get("active", True)
    }
    supplier_rate: dict[str, Any] = {}
    for sid, s in suppliers.items():
        days_off: dict[date, DayOff] = {}
        for w in s.get("unavailable") or []:
            if not isinstance(w, dict):
                continue
            a, b = calc.d(w.get("from")), calc.d(w.get("to"))
            if a and b and b >= a:
                for day in calc.daterange(a, min(b, a + timedelta(days=MAX_ABSENCE_DAYS))):
                    days_off[day] = "unavailable"
        owners[sid] = Owner(
            id=sid,
            name=s.get("name") or sid,
            kind="subcontractor",
            weekly_hours=0.0,
            workdays=_workdays(s),
            days_off=days_off,
        )
        supplier_rate[sid] = s.get("rate")

    # time entries -> bookings
    per_day: dict[tuple[str, date], float] = defaultdict(float)
    per_user: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
    booked_total: dict[str, float] = defaultdict(float)
    for e in store.list("entries"):
        pid = e.get("project_id")
        if not pid:
            continue
        day = calc.d(e.get("date"))
        if day is None:
            _skip(skipped, "entries", e, "invalid date")
            continue
        if day > today:
            continue
        worked = calc.fnum(e.get("minutes")) / 60.0
        per_day[(pid, day)] += worked
        booked_total[pid] += worked
        if e.get("user_id"):
            per_user[(e["user_id"], pid)][day] += worked
    bookings = [Booking(pid, day, h) for (pid, day), h in sorted(per_day.items(), key=lambda kv: (kv[0][1], kv[0][0]))]

    # assignments
    rows = apply_allocation_changes(store.list("allocations"), changes, users, projects)
    assignments: list[Assignment] = []
    cost: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        status = r.get("status", "active")
        if status not in ("active", "cancelled"):
            continue
        if not _usable_allocation(r):
            _skip(skipped, "allocations", r, "no owner, no project or invalid period")
            continue
        hours = _hours(r)
        if hours is None:
            _skip(skipped, "allocations", r, "needs exactly one of hours_per_week and total_hours")
            continue
        owner_id = r.get("user_id") or r["supplier_id"]
        owner = owners.get(owner_id)
        if owner is None:
            if owner_id not in users and owner_id not in {s["id"] for s in store.list("suppliers")}:
                _skip(skipped, "allocations", r, "unknown owner")
            continue  # inactive owner or not a capacity supplier: out of the plan
        if (r.get("supplier_id") is not None) != (owner.kind == "subcontractor"):
            continue
        start, end = calc.d(r["start"]), calc.d(r["end"])
        assert start and end
        cut: date | None = None
        if status == "cancelled":
            cut = calc.d(r.get("cancelled_from"))
            if cut is not None and cut <= start:
                continue
        pid = r["project_id"]
        booked = 0.0
        if hours[1] is not None:
            booked = sum(
                h
                for day, h in per_user.get((owner_id, pid), {}).items()
                if start <= day <= end and (cut is None or day < cut)
            )
        project = projects.get(pid)
        asg = Assignment(
            id=r.get("id") or "",
            owner_id=owner_id,
            project_id=pid,
            project_kind="internal" if (project or {}).get("kind") == "internal" else "customer",
            start=start,
            end=end,
            hours_per_week=hours[0],
            total_hours=hours[1],
            booked=booked,
            cut=cut,
        )
        assignments.append(asg)
        if owner.kind == "subcontractor":
            cost[pid] += planned_net(owner, asg, holiday, _rate(r.get("rate"), supplier_rate.get(owner_id)))

    # items
    items: list[Item] = []
    contracts = store.list("contracts")
    converted = {c.get("offer_id") for c in contracts if c.get("offer_id")}
    default_win = calc.fnum(settings.get("planning_default_win_probability"), 100.0)

    def make(kind: str, rid: str, row: dict, src: dict, rate_src: Any, title: str, win: float) -> Item | None:
        ov = overrides.get(_key(kind, rid), {})
        start = calc.d(ov.get("start") or src.get("start"))
        end = calc.d(ov.get("end") or src.get("end"))
        if ov.get("hours_per_week") is not None:
            fields = {"hours_per_week": ov["hours_per_week"], "total_hours": ov.get("total_hours")}
        elif ov.get("total_hours") is not None:
            fields = {"hours_per_week": None, "total_hours": ov["total_hours"]}
        else:
            fields = {"hours_per_week": src.get("hours_per_week"), "total_hours": src.get("total_hours")}
        if not start or not end or end < start:
            _skip(skipped, "contracts" if kind == "contract" else "offers", row, "invalid period")
            return None
        hours = _hours(fields)
        if hours is None:
            _skip(
                skipped,
                "contracts" if kind == "contract" else "offers",
                row,
                "needs exactly one of hours_per_week and total_hours",
            )
            return None
        project = projects.get(row.get("project_id"))
        customer = customers.get(row.get("customer_id") or (project or {}).get("customer_id"))
        return Item(
            kind="contract" if kind == "contract" else "offer",
            id=rid,
            title=title,
            project_id=row.get("project_id") or None,
            start=start,
            end=end,
            hours_per_week=hours[0],
            total_hours=hours[1],
            rate=_rate(rate_src, (project or {}).get("rate"), (customer or {}).get("rate"), default_rate),
            win=min(max(win, 0.0), 1.0),
        )

    for c in contracts:
        if c.get("status") != "confirmed":
            continue
        it = make("contract", c["id"], c, c, c.get("rate"), c.get("title") or "Contract", 1.0)
        if it:
            items.append(it)
    for o in store.list("offers"):
        plan = o.get("plan")
        if o.get("status") != "sent" or not isinstance(plan, dict) or o["id"] in converted:
            continue
        if not plan.get("start") or not plan.get("end"):
            continue
        valid = calc.d(o.get("valid_until"))
        if valid and valid < today:
            continue
        fields = _plan_hours_fields(plan, o)
        win = calc.fnum(plan.get("win_probability"), default_win) / 100.0
        it = make(
            "offer",
            o["id"],
            o,
            fields,
            plan.get("rate", o.get("rate")),
            "Offer %s" % (o.get("number") or "(draft)"),
            win,
        )
        if it:
            items.append(it)

    # invoiced net per contract
    invoiced = _invoiced(store, settings, [i for i in items if i.kind == "contract"])

    # names and internal projects
    wanted = {a.project_id for a in assignments} | {i.project_id for i in items if i.project_id}
    wanted |= {b.project_id for b in bookings}
    names = {pid: p.get("name") or DELETED_PROJECT for pid, p in projects.items()}
    for pid in wanted:
        names.setdefault(pid, DELETED_PROJECT)
    internal = [
        {
            "project_id": pid,
            "name": p.get("name") or DELETED_PROJECT,
            "budget_hours": calc.fnum(p.get("budget_hours")) or None,
            "booked": booked_total.get(pid, 0.0),
        }
        for pid, p in projects.items()
        if p.get("kind") == "internal" and (p.get("status") or "active") not in CLOSED_PROJECT
    ]
    return Snapshot(
        today=today,
        holiday=holiday,
        owners=list(owners.values()),
        assignments=assignments,
        items=items,
        bookings=bookings,
        names=names,
        internal=internal,
        invoiced=invoiced,
        subcontractor_cost=dict(cost),
        skipped=skipped,
    )


def _invoiced(store: Repository, settings: dict, contracts: list[Item]) -> dict[str, Decimal]:
    """Net of the invoices of a project (not draft, not cancelled) attributed to the contract of that project whose
    period contains the invoice date, earliest end first; invoices that fit no contract are not counted."""
    by_project: dict[str, list[Item]] = defaultdict(list)
    for c in sorted(contracts, key=lambda c: (c.end, c.start, c.id)):
        if c.project_id:
            by_project[c.project_id].append(c)
    out: dict[str, Decimal] = {}
    if not by_project:
        return out
    for inv in store.list("invoices"):
        if inv.get("status") in NOT_INVOICED or inv.get("project_id") not in by_project:
            continue
        day = calc.d(inv.get("date"))
        if day is None:
            continue
        target = next((c for c in by_project[inv["project_id"]] if c.start <= day <= c.end), None)
        if target is None:
            continue
        net = q2(dec(calc.invoice_amounts(inv, settings)["net"]))
        out[target.id] = out.get(target.id, Decimal(0)) + net
    return out


__all__ = ["Snapshot", "load", "apply_changes", "apply_allocation_changes"]
