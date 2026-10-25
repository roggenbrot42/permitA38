"""Capacity planning engine: per-day capacity against confirmed and binding commitments, weekly rows, what-if
simulation, vacation entitlement and deterministic suggestions.

Rules (plain-language version in docs/planning.md):

* Capacity of a person per day = target hours (weekly hours / workdays; zero on non-workdays, public holidays and
  absence days) minus busy hours from ``services.busy``; never below zero.
* Confirmed commitments = contracts with status != ``done``. ``hours_per_week`` is spread over the assignee's
  workdays of every ISO week (a partial week gets its share of the full week); ``total_hours`` is spread evenly over
  the assignee's AVAILABLE days of the contract period (workdays that are neither a public holiday nor an absence
  day; with none available the nominal spread over all workdays is used). ``hours_per_week`` stays nominal: holidays
  and absences do NOT reduce it, so a conflict shows up as over-capacity.
* Binding offers = offers with status ``sent``, a plan, no contract yet and ``valid_until`` not passed (or unset).
  Worst case: all of them are accepted. The expected view weights each one by its win probability.
* Assignees are the active users in ``user_ids`` (no user -> the boss). Hours are split equally among them.

* Capacity is ring-fenced to projects (docs/specs/planner-per-project.md). A person's real capacity of a day is
  given to the projects they are allocated to (``hours_per_week / workdays`` each, scaled down pro rata when the sum is
  more than the real capacity); the remainder is ``unassigned``. Demand lands in the scope of the item's project
  (``unassigned`` without a project). Gaps are ``max(0, assigned - capacity)`` per scope, pool and DAY, summed up.

Everything here is computed per day and summed into weeks, so ranges, weeks and the simulation share one rule set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .busy import BusyTimeProvider, NullBusyProvider

STATUS_TIGHT = 90.0
UNASSIGNED = "unassigned"
DELETED_PROJECT = "(deleted project)"
EPS = 1e-9
MAX_ALLOCATION_DAYS = 1830
CLOSED_PROJECT = ("done", "archived")


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
    project_id: str | None = None  # None = the unassigned scope


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
    allocations: list[dict] = field(default_factory=list)  # active employee allocations (after what-if changes)
    project_names: dict[str, str] = field(default_factory=dict)

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


def apply_allocation_changes(
    rows: list[dict], changes: dict | None, users: dict[str, dict], projects: dict[str, dict]
) -> list[dict]:
    """The active employee allocations after the what-if changes (replace / append / remove) in memory.

    Errors: 404 for an unknown allocation, user or project; 409 for an external (subcontractor) row.
    """
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
    return [r for r in base.values() if usable_allocation(r) and r.get("status", "active") == "active"]


def usable_allocation(row: dict) -> bool:
    """An employee allocation the engine can use. A damaged row (from an older version or a hand-edited file) is
    skipped instead of breaking every planner view."""
    if not isinstance(row.get("user_id"), str) or not row["user_id"] or row.get("supplier_id"):
        return False
    if not isinstance(row.get("project_id"), str) or not row["project_id"]:
        return False
    s, e = calc.d(row.get("start")), calc.d(row.get("end"))
    return bool(s and e and e >= s and isinstance(row.get("start"), str) and isinstance(row.get("end"), str))


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
    settings = effective_settings(ctx["settings"])
    default_rate = calc.fnum(settings.get("default_rate"))
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
                calc.fnum(settings.get("planning_default_win_probability"), 100.0),
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
            project_id=project_id or None,
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
        settings=settings,
        expired_offers=expired,
        allocations=apply_allocation_changes(store.list("allocations"), changes, ctx["users"], projects),
        project_names={pid: p.get("name") or DELETED_PROJECT for pid, p in projects.items()},
    )


@dataclass
class Pool:
    """Capacity and demand of one scope and one pool (internal = employees, external = subcontractors) on one day."""

    cap: float = 0.0
    confirmed: float = 0.0
    binding: float = 0.0
    expected: float = 0.0

    @property
    def assigned(self) -> float:
        return self.confirmed + self.binding

    @staticmethod
    def _gap(x: float) -> float:
        return x if x > EPS else 0.0

    @property
    def gap(self) -> float:
        return self._gap(self.assigned - self.cap)

    @property
    def gap_confirmed(self) -> float:
        return self._gap(self.confirmed - self.cap)

    @property
    def gap_expected(self) -> float:
        return self._gap(self.confirmed + self.expected - self.cap)


@dataclass
class ScopeDay:
    internal: Pool = field(default_factory=Pool)
    external: Pool = field(default_factory=Pool)

    def pools(self) -> tuple[tuple[str, Pool], tuple[str, Pool]]:
        return (("internal", self.internal), ("external", self.external))

    @property
    def cap(self) -> float:
        return self.internal.cap + self.external.cap

    @property
    def active(self) -> bool:
        return any(p.cap > EPS or p.confirmed > EPS or p.binding > EPS for _, p in self.pools())


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
    scopes: dict[str, ScopeDay] = field(default_factory=dict)

    def scope(self, key: str) -> ScopeDay:
        sd = self.scopes.get(key)
        if sd is None:
            sd = self.scopes[key] = ScopeDay()
        return sd


@dataclass
class DayTable:
    days: dict[date, Day]
    item_hours: dict[str, dict[date, float]]  # item key -> day -> hours (assigned share)
    items: dict[str, Item]


def _daterange(a: date, b: date) -> Iterable[date]:
    while a <= b:
        yield a
        a += timedelta(days=1)


def scope_of(item: Item) -> str:
    return item.project_id or UNASSIGNED


def day_table(world: World, lo: date, hi: date, user_id: str | None = None) -> DayTable:
    """Per-day capacity and commitments for ``lo..hi`` (inclusive), optionally for one person only.

    Every hour of capacity and of demand belongs to one scope (``Day.scopes``): ``Day.cap`` is the sum of all scopes.
    """
    days = {x: Day() for x in _daterange(lo, hi)}
    cap = world.cap
    by_user: dict[str, list[dict]] = {}
    for a in world.allocations:
        by_user.setdefault(a["user_id"], []).append(a)
    for u in world.user_list(user_id):
        wd = calc._workdays(u)
        per_day = calc.fnum(u.get("weekly_hours")) / len(wd)
        busy = world.busy.busy_hours(u["id"], lo, hi) or {}
        allocs = [
            (a["start"], a["end"], a["project_id"], calc.fnum(a.get("hours_per_week")) / len(wd))
            for a in by_user.get(u["id"], [])
        ]
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
            real = max(per_day - b, 0.0)
            days[x].cap += real
            # ring fence: the real capacity is shared out to the allocated projects (pro rata when over-allocated)
            iso = x.isoformat()
            today_allocs = [(pid, h) for s, e, pid, h in allocs if s <= iso <= e]
            total = sum(h for _, h in today_allocs)
            scale = real / total if total > real + EPS else 1.0
            for pid, h in today_allocs:
                days[x].scope(pid).internal.cap += h * scale
            if real - total > EPS:
                days[x].scope(UNASSIGNED).internal.cap += real - total
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
                # spread over the days of the week the person can actually work (workdays that are neither a public
                # holiday nor an absence day), so a holiday does not push a day over capacity while the week fits.
                # With no available day left the nominal spread over all workdays stays, so the overload is visible.
                off_days = cap.absences[uid]
                weeks_cache: dict[date, tuple[int, int]] = {}
                for x in span:
                    if x.weekday() not in wdset:
                        continue
                    ws = x - timedelta(days=x.weekday())
                    if ws not in weeks_cache:
                        week = [ws + timedelta(days=k) for k in range(7)]
                        work = [y for y in week if y.weekday() in wdset]
                        avail = [y for y in work if not cap.holiday(y) and y not in off_days]
                        weeks_cache[ws] = (len(work), len(avail))
                    n_work, n_avail = weeks_cache[ws]
                    if n_avail:
                        if cap.holiday(x) or x in off_days:
                            continue
                        per_item[x] = per_item.get(x, 0.0) + it.hpw * share / n_avail
                    elif n_work:
                        per_item[x] = per_item.get(x, 0.0) + it.hpw * share / n_work
            elif it.total:
                # spread over the days the person can actually work: workdays that are neither a public holiday nor an
                # absence day. With none left the nominal spread over all workdays stays, so the overload is visible.
                off_days = cap.absences[uid]
                period = [y for y in _daterange(it.start, it.end) if y.weekday() in wdset]
                free = [y for y in period if not cap.holiday(y) and y not in off_days]
                days_used = free or period
                if days_used:
                    chosen = set(days_used)
                    for x in span:
                        if x in chosen:
                            per_item[x] = per_item.get(x, 0.0) + it.total * share / len(days_used)
        if per_item:
            item_hours[_key(it.kind, it.id)] = per_item
            key = scope_of(it)
            for x, h in per_item.items():
                pool = days[x].scope(key).internal
                if it.binding:
                    days[x].binding += h
                    days[x].expected += h * it.win
                    pool.binding += h
                    pool.expected += h * it.win
                else:
                    days[x].confirmed += h
                    pool.confirmed += h
    return DayTable(days=days, item_hours=item_hours, items=items)


def scope_name(world: World, key: str) -> str:
    if key == UNASSIGNED:
        return "Unassigned"
    return world.project_names.get(key) or DELETED_PROJECT


def day_table_json(world: World, lo: date, hi: date, user_id: str | None = None, scope: str | None = None) -> dict:
    """The day table as the API sends it (docs/contracts/planner-per-project.md, ``GET /api/plan/day-table``)."""
    table = day_table(world, lo, hi, user_id)

    def block(p: Pool) -> dict:
        return dict(
            cap=round(p.cap, 6),
            assigned=round(p.assigned, 6),
            confirmed=round(p.confirmed, 6),
            binding=round(p.binding, 6),
            expected=round(p.expected, 6),
            gap=round(p.gap, 6),
            gap_confirmed=round(p.gap_confirmed, 6),
        )

    names: dict[str, str] = {}
    out_days = []
    for x, d in table.days.items():
        scopes = {}
        for key, sd in d.scopes.items():
            if (scope and key != scope) or not sd.active:
                continue
            scopes[key] = dict(internal=block(sd.internal), external=block(sd.external))
            names[key] = scope_name(world, key)
        out_days.append(
            dict(
                date=x.isoformat(),
                cap=round(d.cap, 6),
                busy=round(d.busy, 6),
                vacation=round(d.vacation, 6),
                holiday=round(d.holiday, 6),
                absence_other=round(d.absence_other, 6),
                confirmed=round(d.confirmed, 6),
                binding=round(d.binding, 6),
                expected=round(d.expected, 6),
                names=list(d.names),
                scopes=scopes,
            )
        )
    return dict(**{"from": lo.isoformat(), "to": hi.isoformat()}, scope_names=names, days=out_days)


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


@dataclass
class _Sum:
    """Hours of one scope in one week, per pool."""

    cap: list[float] = field(default_factory=lambda: [0.0, 0.0])  # internal, external
    conf: list[float] = field(default_factory=lambda: [0.0, 0.0])
    bind: list[float] = field(default_factory=lambda: [0.0, 0.0])
    exp: list[float] = field(default_factory=lambda: [0.0, 0.0])
    gap: list[float] = field(default_factory=lambda: [0.0, 0.0])
    gap_c: list[float] = field(default_factory=lambda: [0.0, 0.0])
    gap_e: list[float] = field(default_factory=lambda: [0.0, 0.0])

    def add(self, sd: ScopeDay) -> None:
        for i, (_, p) in enumerate(sd.pools()):
            self.cap[i] += p.cap
            self.conf[i] += p.confirmed
            self.bind[i] += p.binding
            self.exp[i] += p.expected
            self.gap[i] += p.gap
            self.gap_c[i] += p.gap_confirmed
            self.gap_e[i] += p.gap_expected


def _project_entry(world: World, key: str, sm: _Sum) -> dict:
    cap_h, conf, bind = sum(sm.cap), sum(sm.conf), sum(sm.bind)
    gap = sum(sm.gap)
    status, util = status_for(cap_h, conf + bind)
    if gap > EPS:
        status = "over"

    def pool(i: int) -> dict:
        return dict(capacity=round(sm.cap[i], 6), assigned=round(sm.conf[i] + sm.bind[i], 6), gap=round(sm.gap[i], 6))

    return dict(
        project_id=None if key == UNASSIGNED else key,
        name=scope_name(world, key),
        capacity=round(cap_h, 6),
        confirmed=round(conf, 6),
        binding_offers=round(bind, 6),
        expected_offers=round(sum(sm.exp), 6),
        free_after_confirmed=round(cap_h - conf, 6),
        free_worst_case=round(cap_h - conf - bind, 6),
        utilisation=util,
        status=status,
        gap=round(gap, 6),
        gap_confirmed=round(sum(sm.gap_c), 6),
        gap_expected=round(sum(sm.gap_e), 6),
        internal=pool(0),
        external=pool(1),
        external_cost=0.0,
    )


def weeks(world: World, first: date, count: int, user_id: str | None = None, scope: str | None = None) -> list[dict]:
    """Weekly rows starting at the Monday of ``first`` (see :func:`weeks_detail`)."""
    return weeks_detail(world, first, count, user_id, scope)[0]


def weeks_detail(
    world: World, first: date, count: int, user_id: str | None = None, scope: str | None = None
) -> tuple[list[dict], dict[tuple[str, str], list[dict]]]:
    """Weekly rows starting at the Monday of ``first``, and the contributions per (week, scope).

    Revenue follows the basis rule: contract (confirmed hours exist that week), else offer-binding (binding hours
    exist), else intended (capacity x default rate). With ``scope`` (a project id or ``unassigned``) the hours,
    contributions and revenue are that scope's; ``projects`` then holds only that scope. Without it the row is the
    team's and ``projects`` lists every scope (see docs/contracts/planner-per-project.md).
    """
    start = week_start(first)
    lo, hi = start, start + timedelta(days=7 * count - 1)
    table = day_table(world, lo, hi, user_id)
    rows: list[dict] = []
    detail: dict[tuple[str, str], list[dict]] = {}
    allocs = [a for a in world.allocations if not user_id or a["user_id"] == user_id]
    for i in range(count):
        ws = start + timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        days = [ws + timedelta(days=k) for k in range(7)]
        sums: dict[str, _Sum] = {}
        for x in days:
            for key, sd in table.days[x].scopes.items():
                sums.setdefault(key, _Sum()).add(sd)
        for a in allocs:  # an allocation shows its project even in a week without capacity or demand
            if a["start"] <= we.isoformat() and a["end"] >= ws.isoformat():
                sums.setdefault(a["project_id"], _Sum())
        sums.setdefault(UNASSIGNED, _Sum())
        if scope:
            sums = {scope: sums.get(scope, _Sum())}
            cap_h = sum(sums[scope].cap)
            conf = sum(sums[scope].conf)
            bind = sum(sums[scope].bind)
            exp = sum(sums[scope].exp)
        else:
            cap_h = sum(table.days[x].cap for x in days)
            conf = sum(table.days[x].confirmed for x in days)
            bind = sum(table.days[x].binding for x in days)
            exp = sum(table.days[x].expected for x in days)
        gap = sum(sum(sm.gap) for sm in sums.values())
        gap_c = sum(sum(sm.gap_c) for sm in sums.values())
        gap_e = sum(sum(sm.gap_e) for sm in sums.values())
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
        per_scope: dict[str, list[dict]] = {}
        for key, per in table.item_hours.items():
            h = sum(per.get(x, 0.0) for x in days)
            if h <= 1e-9:
                continue
            it = table.items[key]
            entry = dict(kind=it.kind, id=it.id, title=it.title, hours=round(h, 2), binding=it.binding)
            per_scope.setdefault(scope_of(it), []).append(entry)
            if scope and scope_of(it) != scope:
                continue
            contrib.append(entry)
            amt = q2(dec(round(h, 6)) * dec(it.rate))
            if it.binding:
                rev_bind_d += amt
            else:
                rev_conf_d += amt
        for key, lst in per_scope.items():
            detail[(ws.isoformat(), key)] = lst
        intended_hours = cap_h
        basis, revenue = "intended", float(q2(dec(round(intended_hours, 6)) * dec(world.default_rate)))
        if conf > 1e-9:
            basis, revenue = "contract", float(q2(rev_conf_d))
        elif bind > 1e-9:
            basis, revenue = "offer-binding", float(q2(rev_bind_d))
        demand = conf + bind
        status, util = status_for(cap_h, demand)
        if gap > EPS:
            status = "over"
        events = list(names)
        if abs_o or vac:
            events.append("%d h time off" % round(vac + abs_o))
        projects = [_project_entry(world, key, sm) for key, sm in sums.items()]
        projects.sort(key=lambda p: (p["project_id"] is None, p["name"].lower(), p["project_id"] or ""))
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
                gap=round(gap, 6),
                gap_confirmed=round(gap_c, 6),
                gap_expected=round(gap_e, 6),
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
                projects=projects,
            )
        )
    return rows, detail


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
    """Hours that no scope can cover in these rows (the sum of the scope gaps)."""
    return sum(r["gap"] for r in rows)


def over_weeks(rows: list[dict]) -> list[str]:
    return [r["week"] for r in rows if r["gap"] > 1e-6]


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


def free_hours_in_range(
    world: World, lo: date, hi: date, user_id: str | None = None, scope: str | None = None
) -> float:
    """Capacity minus demand in the range; with ``scope`` only that scope's (a project id or ``unassigned``)."""
    t = day_table(world, lo, hi, user_id)
    if scope is None:
        return sum(d.cap - d.confirmed - d.binding for d in t.days.values())
    total = 0.0
    for d in t.days.values():
        sd = d.scopes.get(scope)
        if sd is not None:
            total += sd.internal.cap + sd.external.cap - sd.internal.assigned - sd.external.assigned
    return total
