"""Capacity planning engine: per-day capacity against confirmed and binding commitments, weekly rows, what-if
simulation, vacation entitlement and deterministic suggestions.

Rules (plain-language version in docs/planning.md):

* Capacity of a person per day = target hours (weekly hours / workdays; zero on non-workdays, public holidays and
  absence days) minus busy hours from ``services.busy``; never below zero.
* Confirmed commitments = contracts with status != ``done``. ``hours_per_week`` is spread over the assignee's
  workdays of every ISO week (a partial week gets its share of the full week); ``total_hours`` is spread evenly over
  the assignee's workdays of the contract period. Commitments are nominal: holidays and absences do NOT reduce them,
  so a conflict shows up as over-capacity.
* Binding offers = offers with status ``sent``, a plan, no contract yet and ``valid_until`` not passed (or unset).
  Worst case: all of them are accepted. The expected view weights each one by its win probability.
* Assignees are the active users in ``user_ids`` (no user -> the boss). Hours are split equally among them.

Everything here is computed per day and summed into weeks, so ranges, weeks and the simulation share one rule set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from permita38.core import calc
from permita38.core.money import dec, q2
from permita38.core.repository import Repository

from .busy import BusyTimeProvider, NullBusyProvider

STATUS_TIGHT = 90.0


def _key(kind: str, rid: str) -> str:
    return f"{kind}:{rid}"


class _View:
    """Read-only view of the store where the absences can be replaced (what-if). Used by calc.Capacity."""

    def __init__(self, store: Repository, absences: list[dict]):
        self._store = store
        self._absences = absences

    def list(self, coll: str) -> list[dict]:
        return self._absences if coll == "absences" else self._store.list(coll)

    def settings(self) -> dict:
        return self._store.settings()


@dataclass
class Item:
    kind: str  # "contract" or "offer"
    id: str
    title: str
    start: date
    end: date
    hpw: float | None
    total: float | None
    assignees: list[str]
    rate: float
    binding: bool  # False for confirmed contracts
    win: float  # 0..1, used by the expected view


@dataclass
class World:
    today: date
    users: dict[str, dict]
    cap: calc.Capacity
    items: list[Item]
    boss: str | None
    default_rate: float
    busy: BusyTimeProvider
    absences: list[dict]
    settings: dict
    expired_offers: list[str] = field(default_factory=list)

    def user_list(self, user_id: str | None) -> list[dict]:
        return [u for u in self.users.values() if user_id is None or u["id"] == user_id]


def _iso(x: date) -> str:
    return x.isoformat()


def _workset(user: dict) -> set[int]:
    return set(calc._workdays(user))


def _item_rate(src_rate: Any, project: dict | None, customer: dict | None, default: float) -> float:
    for r in (src_rate, (project or {}).get("rate"), (customer or {}).get("rate")):
        if r not in (None, ""):
            return calc.fnum(r)
    return calc.fnum(default)


def apply_changes(absences: list[dict], changes: dict | None) -> tuple[list[dict], dict[str, dict]]:
    """Return the absences after the what-if changes and a map of commitment overrides keyed by kind:id."""
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


def load(
    store: Repository,
    today: date,
    busy: BusyTimeProvider | None = None,
    changes: dict | None = None,
    include_offers: bool = True,
) -> World:
    absences, overrides = apply_changes(store.list("absences"), changes)
    view = _View(store, absences)
    ctx = calc.context(view)
    cap = calc.Capacity(view, ctx)
    users = {u["id"]: u for u in ctx["users"].values() if u.get("active", True)}
    boss = next((u["id"] for u in users.values() if u.get("role") == "boss"), next(iter(users), None))
    projects, customers = ctx["projects"], ctx["customers"]
    default_rate = calc.fnum(ctx["settings"].get("default_rate"))
    items: list[Item] = []
    expired: list[str] = []
    contracts = store.list("contracts")
    converted = {c.get("offer_id") for c in contracts}

    def make(kind: str, rid: str, src: dict, plan: dict, binding: bool, rate_src: Any, project_id, customer_id, title):
        ov = overrides.get(_key(kind, rid), {})
        start = calc.d(ov.get("start") or plan.get("start"))
        end = calc.d(ov.get("end") or plan.get("end"))
        hpw = ov.get("hours_per_week", plan.get("hours_per_week"))
        total = ov.get("total_hours", plan.get("total_hours"))
        if not start or not end or end < start:
            return None
        if not calc.fnum(hpw) and not calc.fnum(total):
            return None
        ids = [x for x in (plan.get("user_ids") or src.get("user_ids") or []) if x in users] or ([boss] if boss else [])
        if not ids:
            return None
        project = projects.get(project_id)
        customer = customers.get(customer_id or (project or {}).get("customer_id"))
        win = (
            calc.fnum(
                plan.get("win_probability"),
                calc.fnum(ctx["settings"].get("planning_default_win_probability", 100), 100.0),
            )
            / 100.0
        )
        return Item(
            kind=kind,
            id=rid,
            title=title,
            start=start,
            end=end,
            hpw=calc.fnum(hpw) or None,
            total=calc.fnum(total) or None,
            assignees=ids,
            rate=_item_rate(rate_src, project, customer, default_rate),
            binding=binding,
            win=min(max(win, 0.0), 1.0),
        )

    for c in contracts:
        if c.get("status") == "done":
            continue
        it = make(
            "contract",
            c["id"],
            c,
            c,
            False,
            c.get("rate"),
            c.get("project_id"),
            c.get("customer_id"),
            c.get("title") or "Contract",
        )
        if it:
            items.append(it)
    if include_offers:
        for o in store.list("offers"):
            plan = o.get("plan")
            if o.get("status") != "sent" or not plan or o["id"] in converted:
                continue
            valid = calc.d(o.get("valid_until"))
            if valid and valid < today:
                expired.append(o["id"])
                continue
            it = make(
                "offer",
                o["id"],
                o,
                plan,
                True,
                plan.get("rate", o.get("rate")),
                o.get("project_id"),
                o.get("customer_id"),
                "Offer %s" % (o.get("number") or "(draft)"),
            )
            if it:
                items.append(it)
    return World(
        today=today,
        users=users,
        cap=cap,
        items=items,
        boss=boss,
        default_rate=default_rate,
        busy=busy or NullBusyProvider(),
        absences=absences,
        settings=ctx["settings"],
        expired_offers=expired,
    )


@dataclass
class Day:
    cap: float = 0.0
    busy: float = 0.0
    vacation: float = 0.0
    holiday: float = 0.0
    absence_other: float = 0.0
    confirmed: float = 0.0
    binding: float = 0.0
    expected: float = 0.0
    names: list[str] = field(default_factory=list)


@dataclass
class DayTable:
    days: dict[date, Day]
    item_hours: dict[str, dict[date, float]]  # item key -> day -> hours (assigned share)
    items: dict[str, Item]


def _daterange(a: date, b: date) -> Iterable[date]:
    while a <= b:
        yield a
        a += timedelta(days=1)


def day_table(world: World, lo: date, hi: date, user_id: str | None = None) -> DayTable:
    """Per-day capacity and commitments for ``lo..hi`` (inclusive), optionally for one person only."""
    days = {x: Day() for x in _daterange(lo, hi)}
    cap = world.cap
    for u in world.user_list(user_id):
        wd = calc._workdays(u)
        per_day = calc.fnum(u.get("weekly_hours")) / len(wd)
        busy = world.busy.busy_hours(u["id"], lo, hi) or {}
        for x in days:
            if x.weekday() not in wd:
                continue
            hol = cap.holiday(x)
            absent = x in cap.absences[u["id"]]
            if hol or absent:
                lost = per_day
                if hol:
                    days[x].holiday += lost
                    days[x].names.append(hol)
                elif cap.absence_info[u["id"]].get(x) == "vacation":
                    days[x].vacation += lost
                else:
                    days[x].absence_other += lost
                continue
            b = calc.fnum(busy.get(x))
            days[x].busy += min(max(b, 0.0), per_day)
            days[x].cap += max(per_day - b, 0.0)
    item_hours: dict[str, dict[date, float]] = {}
    items = {}
    for it in world.items:
        items[_key(it.kind, it.id)] = it
        span = [x for x in _daterange(max(it.start, lo), min(it.end, hi))]
        if not span:
            continue
        per_item: dict[date, float] = {}
        for uid in it.assignees:
            if user_id and uid != user_id:
                continue
            member = world.users.get(uid)
            if member is None:
                continue
            wdset = _workset(member)
            share = 1.0 / len(it.assignees)
            if it.hpw:
                for x in span:
                    if x.weekday() not in wdset:
                        continue
                    week = [x - timedelta(days=x.weekday()) + timedelta(days=k) for k in range(7)]
                    n = sum(1 for y in week if y.weekday() in wdset)
                    if n:
                        per_item[x] = per_item.get(x, 0.0) + it.hpw * share / n
            elif it.total:
                k_all = sum(1 for y in _daterange(it.start, it.end) if y.weekday() in wdset)
                if k_all:
                    for x in span:
                        if x.weekday() in wdset:
                            per_item[x] = per_item.get(x, 0.0) + it.total * share / k_all
        if per_item:
            item_hours[_key(it.kind, it.id)] = per_item
            for x, h in per_item.items():
                if it.binding:
                    days[x].binding += h
                    days[x].expected += h * it.win
                else:
                    days[x].confirmed += h
    return DayTable(days=days, item_hours=item_hours, items=items)


# ---- weekly view ------------------------------------------------------------------------------------------------
def week_start(x: date) -> date:
    return x - timedelta(days=x.weekday())


def status_for(cap_h: float, demand: float) -> tuple[str, float | None]:
    if demand <= 1e-9:
        return "ok", 0.0
    if cap_h <= 1e-9:
        return "over", None
    util = demand / cap_h * 100.0
    if util > 100.0 + 1e-9:
        return "over", round(util, 1)
    if util > STATUS_TIGHT:
        return "tight", round(util, 1)
    return "ok", round(util, 1)


def weeks(world: World, first: date, count: int, user_id: str | None = None) -> list[dict]:
    """Weekly rows starting at the Monday of ``first``. Revenue follows the basis rule: contract (confirmed hours
    exist that week), else offer-binding (binding hours exist), else intended (capacity x default rate)."""
    start = week_start(first)
    lo, hi = start, start + timedelta(days=7 * count - 1)
    table = day_table(world, lo, hi, user_id)
    rows: list[dict] = []
    for i in range(count):
        ws = start + timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        days = [ws + timedelta(days=k) for k in range(7)]
        cap_h = sum(table.days[x].cap for x in days)
        conf = sum(table.days[x].confirmed for x in days)
        bind = sum(table.days[x].binding for x in days)
        exp = sum(table.days[x].expected for x in days)
        vac = sum(table.days[x].vacation for x in days)
        hol = sum(table.days[x].holiday for x in days)
        abs_o = sum(table.days[x].absence_other for x in days)
        busy = sum(table.days[x].busy for x in days)
        names: list[str] = []
        for x in days:
            for n in table.days[x].names:
                if n not in names:
                    names.append(n)
        contrib = []
        rev_conf_d, rev_bind_d = Decimal(0), Decimal(0)
        for key, per in table.item_hours.items():
            h = sum(per.get(x, 0.0) for x in days)
            if h <= 1e-9:
                continue
            it = table.items[key]
            contrib.append(dict(kind=it.kind, id=it.id, title=it.title, hours=round(h, 2), binding=it.binding))
            amt = q2(dec(round(h, 6)) * dec(it.rate))
            if it.binding:
                rev_bind_d += amt
            else:
                rev_conf_d += amt
        intended_hours = cap_h
        basis, revenue = "intended", float(q2(dec(round(intended_hours, 6)) * dec(world.default_rate)))
        if conf > 1e-9:
            basis, revenue = "contract", float(q2(rev_conf_d))
        elif bind > 1e-9:
            basis, revenue = "offer-binding", float(q2(rev_bind_d))
        demand = conf + bind
        status, util = status_for(cap_h, demand)
        events = list(names)
        if abs_o or vac:
            events.append("%d h time off" % round(vac + abs_o))
        rows.append(
            dict(
                week=_iso(ws),
                week_end=_iso(we),
                capacity=round(cap_h, 6),
                confirmed=round(conf, 6),
                binding_offers=round(bind, 6),
                expected_offers=round(exp, 6),
                tentative=round(bind, 6),
                free_after_confirmed=round(cap_h - conf, 6),
                free_worst_case=round(cap_h - demand, 6),
                utilisation=util,
                status=status,
                vacation_hours=round(vac, 6),
                holiday_hours=round(hol, 6),
                absence_hours=round(abs_o, 6),
                busy_hours=round(busy, 6),
                contributions=contrib,
                events=events,
                revenue=float(revenue),
                basis=basis,
                contracted_revenue=float(q2(rev_conf_d)),
                offer_revenue=float(q2(rev_bind_d)),
                intended_revenue=float(q2(dec(round(intended_hours, 6)) * dec(world.default_rate))),
                tentative_revenue=float(q2(rev_bind_d)),
                tentative_expected=round(exp, 6),
            )
        )
    return rows


def week_summary_free(row: dict) -> dict:
    """The fields the older forecast contract uses (rounded to 1 decimal hour)."""
    return dict(
        week=row["week"],
        capacity=round(row["capacity"], 1),
        confirmed=round(row["confirmed"], 1),
        tentative=round(row["binding_offers"], 1),
        free=round(row["free_after_confirmed"], 1),
        free_incl_tentative=round(row["free_worst_case"], 1),
    )


def overload_hours(rows: list[dict]) -> float:
    return sum(max(-r["free_worst_case"], 0.0) for r in rows)


def over_weeks(rows: list[dict]) -> list[str]:
    return [r["week"] for r in rows if r["free_worst_case"] < -1e-6]


# ---- vacation entitlement -------------------------------------------------------------------------------------
def entitlement(world: World, user: dict, year: int) -> dict:
    """Vacation days (working days only, public holidays excluded) for one person and year."""
    per_year = user.get("vacation_days_per_year")
    carry = user.get("vacation_carry_over")
    wd = calc._workdays(user)
    used = planned = 0
    for a in world.absences:
        if a.get("user_id") != user["id"] or a.get("kind", "vacation") != "vacation":
            continue
        s = calc.d(a.get("start"))
        e = calc.d(a.get("end") or a.get("start"))
        if not s or not e:
            continue
        for x in _daterange(max(s, date(year, 1, 1)), min(e, date(year, 12, 31))):
            if x.weekday() in wd and not world.cap.holiday(x):
                if x < world.today:
                    used += 1
                else:
                    planned += 1
    total = None
    if per_year not in (None, ""):
        total = calc.fnum(per_year) + calc.fnum(carry)
    remaining = None if total is None else total - used - planned
    return dict(
        user_id=user["id"],
        year=year,
        entitlement=total,
        used=used,
        planned=planned,
        remaining=remaining,
        over=bool(remaining is not None and remaining < 0),
    )


def free_hours_in_range(world: World, lo: date, hi: date, user_id: str | None = None) -> float:
    t = day_table(world, lo, hi, user_id)
    return sum(d.cap - d.confirmed - d.binding for d in t.days.values())
