"""Planning service: forecast, what-if simulation, offer feasibility, deterministic suggestions, named scenarios,
project period extensions and the warnings that accompany saved records.

The arithmetic lives in :mod:`permita38.core.service.planning` (engine) and
:mod:`permita38.core.service.planning_warnings` (consistency checks); this module reads and writes the store.
Simulation and suggestions never write.
"""

from __future__ import annotations

import time as _time
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from . import planning as eng
from . import planning_warnings as pw
from .busy import BusyTimeProvider, NullBusyProvider

MAX_SCAN_DAYS = 400
MAX_WINDOW_DAYS = 120


def _cap_message(
    hours: float, free: float, start: date, end: date, over: list[tuple[str, float]], causes: list[str]
) -> str:
    text = f"You offer {_h(hours)} h between {start:%d.%m.%Y} and {end:%d.%m.%Y}"
    if free + 1e-9 < hours:
        text += f" but only {_h(max(free, 0.0))} h are free in that period."
    else:
        text += " and enough hours are free in total, but the even spread overloads some weeks."
    if over:
        parts = ", ".join(f"{w} (over by {_h(o)} h)" for w, o in over)
        text += f" Over capacity in the weeks of {parts}."
    if causes:
        text += " Causes: " + "; ".join(causes) + "."
    return text


def _h(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _item_from_plan(kind: str, rid: str, title: str, plan: dict, world: eng.World, binding: bool) -> eng.Item | None:
    start, end = calc.d(plan.get("start")), calc.d(plan.get("end"))
    hpw, total = calc.fnum(plan.get("hours_per_week")) or None, calc.fnum(plan.get("total_hours")) or None
    if not start or not end or end < start or not (hpw or total):
        return None
    ids = [x for x in (plan.get("user_ids") or []) if x in world.users] or ([world.boss] if world.boss else [])
    if not ids:
        return None
    win = min(max(calc.fnum(plan.get("win_probability"), 100.0) / 100.0, 0.0), 1.0)
    return eng.Item(
        kind=kind,
        id=rid,
        title=title,
        start=start,
        end=end,
        hpw=hpw,
        total=total,
        assignees=ids,
        rate=calc.fnum(plan.get("rate"), world.default_rate),
        binding=binding,
        win=win,
    )


class PlanningService:
    def __init__(self, store: Repository, busy: BusyTimeProvider | None = None):
        self.store = store
        self.busy: BusyTimeProvider = busy or NullBusyProvider()

    # ------------------------------------------------------------------ views
    def _world(self, today: date | None = None, changes: dict | None = None, include_offers: bool = True) -> eng.World:
        return eng.load(self.store, today or date.today(), self.busy, changes, include_offers)

    def _rows(self, world: eng.World, first: date | None, weeks: int, user_id: str | None) -> list[dict]:
        first = first or world.today
        return eng.weeks(world, first, weeks, user_id or None)

    @staticmethod
    def _totals(rows: list[dict]) -> dict:
        keys = ("capacity", "confirmed", "binding_offers", "free_after_confirmed", "free_worst_case")
        out = {k: round(sum(r[k] for r in rows), 2) for k in keys}
        out.update(
            contracted_revenue=round(sum(r["contracted_revenue"] for r in rows), 2),
            offer_revenue=round(sum(r["offer_revenue"] for r in rows), 2),
            assumed_revenue=round(sum(r["intended_revenue"] for r in rows if r["basis"] == "intended"), 2),
            revenue=round(sum(r["revenue"] for r in rows), 2),
            over_weeks=sum(1 for r in rows if r["status"] == "over"),
        )
        return out

    @staticmethod
    def _capacity_warnings(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            if r["status"] == "over":
                over = max(-r["free_worst_case"], 0.0)
                out.append(
                    pw.warning(
                        "capacity.over",
                        "warning",
                        "weeks",
                        f"Week of {r['week']}: {_h(r['confirmed'] + r['binding_offers'])} h committed for "
                        f"{_h(r['capacity'])} h of capacity (over by {_h(over)} h).",
                        dict(week=r["week"], over_by=round(over, 2), contributions=r["contributions"]),
                    )
                )
        return out

    def forecast(
        self, weeks: int = 26, user_id: str | None = None, include_offers: bool = True, today: date | None = None
    ) -> dict:
        today = today or date.today()
        world = self._world(today, None, include_offers)
        rows = self._rows(world, eng.week_start(today), weeks, user_id)
        details = []
        lo = date.fromisoformat(rows[0]["week"]) if rows else today
        hi = date.fromisoformat(rows[-1]["week_end"]) if rows else today
        table = eng.day_table(world, lo, hi, user_id or None)
        for it in world.items:
            per = table.item_hours.get(eng._key(it.kind, it.id), {})
            details.append(
                dict(
                    id=it.id,
                    title=it.title,
                    tentative=it.binding,
                    start=it.start.isoformat(),
                    end=it.end.isoformat(),
                    hours_visible=round(sum(per.values()), 1),
                    rate=it.rate,
                )
            )
        return dict(
            weeks=[self._public_week(r) for r in rows],
            contracts=details,
            totals=self._totals(rows),
            expired_offers=world.expired_offers,
            warnings=self._capacity_warnings(rows),
        )

    @staticmethod
    def _public_week(r: dict) -> dict:
        """Precise week values; the keys of the earlier forecast contract are kept as aliases."""
        legacy = eng.week_summary_free(r)
        return {
            **legacy,
            **r,
            "free": r["free_after_confirmed"],
            "free_incl_tentative": r["free_worst_case"],
        }

    def simulate(
        self, changes: dict | None, weeks: int, first: date | None, user_id: str | None, today: date | None = None
    ) -> dict:
        today = today or date.today()
        world = self._world(today, changes)
        rows = self._rows(world, first or eng.week_start(today), weeks, user_id)
        out: dict[str, Any] = dict(
            weeks=[self._public_week(r) for r in rows],
            totals=self._totals(rows),
            warnings=self._capacity_warnings(rows),
        )
        if user_id and user_id in world.users:
            out["entitlement"] = eng.entitlement(world, world.users[user_id], today.year)
            out["entitlement"]["over_entitlement"] = out["entitlement"]["over"]
        return out

    # ------------------------------------------------------------------ feasibility
    def _fit(self, world: eng.World, item: eng.Item) -> tuple[list[dict], eng.World]:
        """Rows of the weeks the item touches, with the item added to the world (replacing an item with the same key)."""
        others = [i for i in world.items if not (i.kind == item.kind and i.id == item.id)]
        w2 = replace(world, items=others + [item])
        lo, hi = eng.week_start(item.start), eng.week_start(item.end) + timedelta(days=6)
        n = (hi - lo).days // 7 + 1
        rows = eng.weeks(w2, lo, n, None)
        return [r for r in rows if r["free_worst_case"] < -1e-6], w2

    def _fits(self, world: eng.World, item: eng.Item) -> bool:
        return not self._fit(world, item)[0]

    def feasibility(self, world: eng.World, item: eng.Item) -> list[dict]:
        over, _ = self._fit(world, item)
        if not over:
            return []
        hours = (item.total or 0.0) if item.total else (item.hpw or 0.0) * ((item.end - item.start).days + 1) / 7.0
        base = replace(world, items=[i for i in world.items if not (i.kind == item.kind and i.id == item.id)])
        free = eng.free_hours_in_range(base, item.start, item.end)
        causes = []
        for r in over:
            for c in r["contributions"]:
                if not c["binding"] or c["id"] != item.id:
                    causes.append(f"{c['title']} ({_h(c['hours'])} h in week of {r['week']})")
            if r["vacation_hours"]:
                causes.append(f"time off ({_h(r['vacation_hours'])} h in week of {r['week']})")
            if r["holiday_hours"]:
                causes.append(f"public holidays ({_h(r['holiday_hours'])} h in week of {r['week']})")
        causes = list(dict.fromkeys(causes))[:6]
        over_pairs = [(r["week"], -r["free_worst_case"]) for r in over]
        options = self._options(world, item, hours)
        msg = _cap_message(hours, free, item.start, item.end, over_pairs, causes)
        return [
            pw.warning(
                "capacity.offer_over",
                "warning",
                "plan",
                msg,
                dict(
                    hours=round(hours, 2),
                    free_hours=round(free, 2),
                    over_weeks=[w for w, _ in over_pairs],
                    causes=causes,
                    options=options,
                ),
            )
        ]

    def _options(self, world: eng.World, item: eng.Item, hours: float) -> list[dict]:
        opts: list[dict] = []
        # shorten: latest end that fits
        for k in range(1, MAX_SCAN_DAYS):
            e2 = item.end - timedelta(days=k)
            if e2 < item.start:
                break
            if self._fits(world, replace(item, end=e2)):
                opts.append(dict(type="shorten", end=e2.isoformat(), message=f"Shorten the end to {e2:%d.%m.%Y}."))
                break
        # move start: earliest start that fits with the same end
        for k in range(1, MAX_SCAN_DAYS):
            s2 = item.start + timedelta(days=k)
            if s2 > item.end:
                break
            if self._fits(world, replace(item, start=s2)):
                opts.append(dict(type="move_start", start=s2.isoformat(), message=f"Start on {s2:%d.%m.%Y} instead."))
                break
        free = eng.free_hours_in_range(
            replace(world, items=[i for i in world.items if not (i.kind == item.kind and i.id == item.id)]),
            item.start,
            item.end,
        )
        if free > 0 and free < hours:
            opts.append(
                dict(type="reduce_scope", hours=round(free, 2), message=f"Reduce the scope to at most {_h(free)} h.")
            )
        return opts

    def check_offer(self, draft: dict, exclude_id: str | None = None, today: date | None = None) -> list[dict]:
        """Feasibility plus consistency warnings for an offer draft (before or after saving)."""
        today = today or date.today()
        settings = effective_settings(self.store.settings())
        out: list[dict] = []
        plan = draft.get("plan") or {}
        if plan:
            world = self._world(today)
            item = _item_from_plan("offer", exclude_id or "draft", "Offer draft", plan, world, True)
            if item:
                out += self.feasibility(world, item)
        ref = self._reference_rate(draft.get("project_id"), draft.get("customer_id"), settings)
        out += pw.document_warnings(draft, kind="offers", settings=settings, reference_rate=ref)
        return out

    def _reference_rate(self, project_id, customer_id, settings) -> float | None:
        proj = self.store.get("projects", project_id) if project_id else None
        cust_id = customer_id or (proj or {}).get("customer_id")
        cust = self.store.get("customers", cust_id) if cust_id else None
        for src in (proj, cust):
            if src and src.get("rate") not in (None, ""):
                return calc.fnum(src["rate"])
        return calc.fnum(settings.get("default_rate")) or None

    # ------------------------------------------------------------------ warnings for saved records
    def warnings_for(self, coll: str, rec: dict) -> list[dict]:
        settings = effective_settings(self.store.settings())
        if coll in ("contracts", "offers"):
            world = self._world()
            out: list[dict] = []
            if coll == "contracts":
                for it in world.items:
                    if it.kind == "contract" and it.id == rec.get("id"):
                        over, _ = self._fit(world, it)
                        if over:
                            out += self.feasibility(world, it)
                return out
            if rec.get("status") == "sent":
                item = _item_from_plan("offer", rec["id"], "Offer", rec.get("plan") or {}, world, True)
                if item:
                    out += self.feasibility(world, item)
            ref = self._reference_rate(rec.get("project_id"), rec.get("customer_id"), settings)
            return out + pw.document_warnings(rec, kind="offers", settings=settings, reference_rate=ref)
        if coll == "invoices":
            tracked = self._tracked(rec.get("project_id"), rec.get("service_from"), rec.get("service_to"))
            billed_other, budget = self._offer_budget(rec)
            ref = self._reference_rate(rec.get("project_id"), rec.get("customer_id"), settings)
            return pw.document_warnings(
                rec,
                kind="invoices",
                settings=settings,
                reference_rate=ref,
                tracked_hours=tracked,
                offer_budget_hours=budget,
                billed_on_offer_hours=billed_other,
            )
        if coll == "projects":
            return self._project_warnings(rec)
        return []

    def _tracked(self, project_id, date_from, date_to) -> float | None:
        if not project_id:
            return None
        lo, hi = date_from or "0000-00-00", date_to or "9999-99-99"
        mins = sum(
            calc.fnum(e.get("minutes"))
            for e in self.store.list("entries")
            if e.get("project_id") == project_id and lo <= str(e.get("date")) <= hi
        )
        return mins / 60.0

    def _offer_budget(self, inv: dict) -> tuple[float | None, float | None]:
        oid = inv.get("offer_id")
        if not oid:
            return None, None
        offer = self.store.get("offers", oid)
        budget = pw.plan_hours((offer or {}).get("plan"))
        if budget is None:
            return None, None
        billed = 0.0
        for other in self.store.list("invoices"):
            if other.get("offer_id") == oid and other.get("id") != inv.get("id") and other.get("status") != "cancelled":
                billed += float(pw.line_hours(other.get("lines") or []))
        return billed, budget

    def _project_warnings(self, project: dict) -> list[dict]:
        entries = [e for e in self.store.list("entries") if e.get("project_id") == project.get("id")]
        plans = []
        for c in self.store.list("contracts"):
            if c.get("project_id") == project.get("id"):
                plans.append(dict(c, title=c.get("title")))
        for o in self.store.list("offers"):
            if o.get("project_id") == project.get("id") and o.get("plan") and o.get("status") == "sent":
                plans.append(dict(o["plan"], id=o["id"], title=o.get("number") or "offer"))
        cust = project.get("customer_id")
        out = pw.project_warnings(project, entries, plans, customer_linked=bool(cust))
        tracked = sum(calc.fnum(e.get("minutes")) for e in entries) / 60.0
        return out + pw.budget_warning(project, tracked)

    # ------------------------------------------------------------------ suggestions
    def suggest_vacation(
        self, user_id: str, days: int, window_from: date, window_to: date, limit: int = 5, today: date | None = None
    ) -> dict:
        today = today or date.today()
        if window_to < window_from:
            raise ServiceError(422, "The window ends before it starts")
        if (window_to - window_from).days > MAX_WINDOW_DAYS:
            raise ServiceError(422, f"The window may be at most {MAX_WINDOW_DAYS} days")
        base = self._world(today)
        user = base.users.get(user_id)
        if not user:
            raise ServiceError(404, "User not found")
        wd = calc._workdays(user)
        cands = []
        x = window_from
        while x <= window_to:
            if x.weekday() in wd and not base.cap.holiday(x) and x not in base.cap.absences[user_id]:
                block: list[date] = []
                y = x
                while len(block) < days and y <= window_to + timedelta(days=days * 7):
                    if y.weekday() in wd and not base.cap.holiday(y) and y not in base.cap.absences[user_id]:
                        block.append(y)
                    y += timedelta(days=1)
                if len(block) == days:
                    cands.append(block)
            x += timedelta(days=1)
        scored = []
        for block in cands:
            first, last = block[0], block[-1]
            change = {
                "absences": [
                    {"user_id": user_id, "kind": "vacation", "start": first.isoformat(), "end": last.isoformat()}
                ],
                "commitments": [],
            }
            w2 = self._world(today, change)
            rows = eng.weeks(
                w2, eng.week_start(first), (eng.week_start(last) - eng.week_start(first)).days // 7 + 1, None
            )
            overload = eng.overload_hours(rows)
            scored.append((overload, -self._bridge_days(base, user, first, last), first, last))
        scored.sort(key=lambda t: (round(t[0], 6), t[1], t[2]))
        return dict(
            options=[
                dict(
                    start=f.isoformat(), end=last_day.isoformat(), days=days, overload_hours=round(o, 2), bridge_days=-b
                )
                for o, b, f, last_day in scored[:limit]
            ],
            considered=len(scored),
        )

    @staticmethod
    def _bridge_days(world: eng.World, user: dict, first: date, last: date) -> int:
        """Free calendar days a block gains: weekends and holidays inside it and directly around it."""
        wd = calc._workdays(user)
        off = 0
        x = first
        while x <= last:
            if x.weekday() not in wd or world.cap.holiday(x):
                off += 1
            x += timedelta(days=1)
        for step in (-1, 1):
            y = (first if step < 0 else last) + timedelta(days=step)
            n = 0
            while n < 3 and (y.weekday() not in wd or world.cap.holiday(y)):
                n += 1
                y += timedelta(days=step)
            off += n
        return off

    def suggest_start(
        self,
        hours: float,
        deadline: date,
        earliest: date | None = None,
        user_ids: list[str] | None = None,
        today: date | None = None,
    ) -> dict:
        """Earliest start for a new offer of ``hours`` (total) that is feasible until ``deadline``."""
        today = today or date.today()
        first = earliest or today
        if deadline < first:
            raise ServiceError(422, "The deadline is before the earliest start")
        world = self._world(today)
        ids = user_ids or ([world.boss] if world.boss else [])
        n_days = (deadline - first).days + 1
        if n_days > MAX_SCAN_DAYS:
            first = deadline - timedelta(days=MAX_SCAN_DAYS - 1)
        # cheap precheck: free hours from the candidate start to the deadline must cover the hours
        free_from: dict[date, float] = {}
        table = eng.day_table(world, first, deadline)
        suffix = 0.0
        x = deadline
        while x >= first:
            suffix += table.days[x].cap - table.days[x].confirmed - table.days[x].binding
            free_from[x] = suffix
            x -= timedelta(days=1)
        for s in sorted(free_from):
            if free_from[s] + 1e-9 < hours:
                continue
            item = eng.Item(
                kind="offer",
                id="suggest",
                title="suggestion",
                start=s,
                end=deadline,
                hpw=None,
                total=hours,
                assignees=ids,
                rate=world.default_rate,
                binding=True,
                win=1.0,
            )
            if ids and self._fits(world, item):
                return dict(start=s.isoformat(), feasible=True, free_hours=round(free_from[s], 2))
        best = max(free_from.values()) if free_from else 0.0
        return dict(
            start=None,
            feasible=False,
            free_hours=round(max(free_from.get(first, 0.0), 0.0), 2),
            max_hours_from_earliest=round(max(free_from.get(first, 0.0), 0.0), 2),
            best_free=round(best, 2),
        )

    def what_must_move(self, plan: dict, today: date | None = None) -> dict:
        """Smallest move of other open offers, or a trim of the draft, that makes the draft fit."""
        today = today or date.today()
        world = self._world(today)
        item = _item_from_plan("offer", "draft", "Draft offer", plan, world, True)
        if not item:
            raise ServiceError(422, "The plan needs a period and hours per week or total hours")
        over, _ = self._fit(world, item)
        if not over:
            return dict(fits=True, suggestions=[])
        over_ids = {(r["week"]) for r in over}
        suggestions: list[dict] = []
        for other in world.items:
            if other.kind != "offer" or not other.binding:
                continue
            touches = any(
                eng.week_start(other.start) <= date.fromisoformat(w) <= eng.week_start(other.end) for w in over_ids
            )
            if not touches:
                continue
            found = None
            for k in range(1, 27):
                for sign in (1, -1):
                    shift = timedelta(days=7 * k * sign)
                    moved = replace(other, start=other.start + shift, end=other.end + shift)
                    w2 = replace(world, items=[i for i in world.items if i is not other] + [moved])
                    if self._fits(w2, item):
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
        hours = (item.total or 0.0) if item.total else (item.hpw or 0.0) * ((item.end - item.start).days + 1) / 7.0
        free = eng.free_hours_in_range(
            replace(world, items=[i for i in world.items if i.id != "draft"]), item.start, item.end
        )
        suggestions.append(
            dict(
                type="trim",
                max_hours=round(max(free, 0.0), 2),
                message=f"Or reduce the draft from {_h(hours)} h to at most {_h(max(free, 0.0))} h.",
            )
        )
        suggestions.sort(key=lambda s: abs(s.get("days", 0)) if s["type"] == "move" else 10**9)
        return dict(fits=False, suggestions=suggestions)

    # ------------------------------------------------------------------ entitlement
    def entitlement(self, user_id: str, year: int, today: date | None = None) -> dict:
        world = self._world(today)
        user = world.users.get(user_id)
        if not user:
            raise ServiceError(404, "User not found")
        return eng.entitlement(world, user, year)

    # ------------------------------------------------------------------ writes: apply, scenarios, extension
    def apply(self, changes: dict, today: date | None = None) -> dict:
        """Write a change set (absences and commitment periods/hours) in one transaction."""
        applied = 0
        with self.store.transaction():
            for ch in changes.get("absences") or []:
                if ch.get("id"):
                    old = self.store.get("absences", ch["id"])
                    if not old:
                        raise ServiceError(404, f"Absence {ch['id']} not found")
                    if ch.get("delete"):
                        self.store.delete("absences", ch["id"])
                    else:
                        merged = {
                            **old,
                            **{k: ch[k] for k in ("user_id", "kind", "start", "end") if ch.get(k) is not None},
                        }
                        self.store.save("absences", merged)
                else:
                    if ch.get("delete") or not ch.get("user_id") or not ch.get("start"):
                        raise ServiceError(422, "A new absence needs a person and a start date")
                    self.store.save(
                        "absences",
                        dict(
                            user_id=ch["user_id"],
                            kind=ch.get("kind") or "vacation",
                            start=ch["start"],
                            end=ch.get("end") or ch["start"],
                        ),
                    )
                applied += 1
            converted = {c.get("offer_id") for c in self.store.list("contracts")}
            for c in changes.get("commitments") or []:
                kind, rid = c["kind"], c["id"]
                if kind == "contract":
                    old = self.store.get("contracts", rid)
                    if not old:
                        raise ServiceError(404, "Contract not found")
                    rec = dict(old)
                    for k in ("start", "end", "hours_per_week", "total_hours"):
                        if c.get(k) is not None:
                            rec[k] = c[k]
                    if rec["end"] < rec["start"]:
                        raise ServiceError(422, "The end must not be before the start")
                    self.store.save("contracts", rec)
                else:
                    old = self.store.get("offers", rid)
                    if not old or old.get("status") != "sent" or rid in converted:
                        raise ServiceError(409, "Only open offers that are not accepted can be moved")
                    plan = dict(old.get("plan") or {})
                    for k in ("start", "end", "hours_per_week", "total_hours"):
                        if c.get(k) is not None:
                            plan[k] = c[k]
                    if plan.get("start") and plan.get("end") and plan["end"] < plan["start"]:
                        raise ServiceError(422, "The end must not be before the start")
                    self.store.save("offers", {**old, "plan": plan})
                applied += 1
        return dict(applied=applied, warnings=self.simulate(None, 26, None, None, today)["warnings"])

    def list_scenarios(self, me: dict) -> list[dict]:
        return [s for s in self.store.list("plan_scenarios") if s.get("user_id") == me["id"]]

    def get_scenario(self, me: dict, rid: str) -> dict:
        s = self.store.get("plan_scenarios", rid)
        if not s or s.get("user_id") != me["id"]:
            raise ServiceError(404, "Not found")
        return s

    def save_scenario(self, me: dict, rid: str | None, name: str, changes: dict) -> dict:
        old = self.get_scenario(me, rid) if rid else None
        rec = {**(old or {}), "name": name, "user_id": me["id"], "changes": changes}
        return self.store.save("plan_scenarios", rec)

    def delete_scenario(self, me: dict, rid: str) -> None:
        self.get_scenario(me, rid)
        self.store.delete("plan_scenarios", rid)

    def apply_scenario(self, me: dict, rid: str) -> dict:
        s = self.get_scenario(me, rid)
        with self.store.transaction():
            return self.apply(s.get("changes") or {})

    def extend_project(self, pid: str, new_end: str, reason: str | None, me: dict) -> dict:
        proj = self.store.get("projects", pid)
        if not proj:
            raise ServiceError(404, "Not found")
        old_end = proj.get("end") or proj.get("start")
        if not old_end:
            raise ServiceError(422, "Set the project start first")
        if new_end < old_end:
            raise ServiceError(422, "A project can only be extended (new end on or after the current end)")
        ext = list(proj.get("extensions") or [])
        ext.append(
            dict(
                kind="manual",
                **{"from": old_end},
                to=new_end,
                reason=reason or "",
                created=int(_time.time()),
                by=me["id"],
            )
        )
        return self.store.save("projects", {**proj, "end": new_end, "extensions": ext})

    @staticmethod
    def cover_plan(project: dict, plan: dict, offer_id: str, now: int | None = None) -> dict:
        """Widen a project's period to cover an accepted offer's plan and record the extension."""
        ps, pe = calc.d(plan.get("start")), calc.d(plan.get("end"))
        if not ps or not pe:
            return project
        cur_s, cur_e = calc.d(project.get("start")), calc.d(project.get("end"))
        new_s = min(x for x in (cur_s, ps) if x) if cur_s else ps
        new_e = max(x for x in (cur_e, pe) if x) if cur_e else pe
        if (new_s, new_e) == (cur_s, cur_e):
            return project
        ext = list(project.get("extensions") or [])
        ext.append(
            dict(
                kind="offer",
                offer_id=offer_id,
                **{"from": project.get("end") or None},
                to=new_e.isoformat(),
                start=new_s.isoformat(),
                reason="accepted offer",
                created=now or int(_time.time()),
            )
        )
        return {**project, "start": new_s.isoformat(), "end": new_e.isoformat(), "extensions": ext}

    def project_timeline(self, pid: str) -> dict:
        proj = self.store.get("projects", pid)
        if not proj:
            raise ServiceError(404, "Not found")
        return dict(project=proj, extensions=proj.get("extensions") or [], warnings=self._project_warnings(proj))
