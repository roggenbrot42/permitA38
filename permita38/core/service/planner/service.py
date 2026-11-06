"""PlannerService (WP4b P2): the planner behind the API.

Loads the store through ``planner/load.py``, builds the supply (``supply.py``), judges the items (``cover.py``) and
renders ``GET /api/plan`` (``views.py``). The writes (apply, scenarios, project extension) are ported unchanged from
the former ``plan_service.py``. Simulation and checks never write.
"""

from __future__ import annotations

import time as _time
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.models import scenario_project_refs
from permita38.core.repository import Repository
from permita38.core.service import planning_warnings as pw
from permita38.core.service.busy import BusyTimeProvider, NullBusyProvider
from permita38.core.service.planner import supply as supply_mod
from permita38.core.service.planner.cover import ProjectVerdict, cover
from permita38.core.service.planner.load import (
    Snapshot,
    _holiday_fn,
    _hours,
    _plan_hours_fields,
    _rate,
    _workdays,
    load,
)
from permita38.core.service.planner.model import Item
from permita38.core.service.planner.spread import planned_hours
from permita38.core.service.planner.views import UNASSIGNED, plan_json, verdict_warnings
from permita38.core.settings import effective_settings

_MISSING = object()


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _dmy(day: date) -> str:
    return day.strftime("%d.%m.%Y")


class PlannerService:
    def __init__(self, store: Repository, busy: BusyTimeProvider | None = None):
        self.store = store
        self.busy: BusyTimeProvider = busy or NullBusyProvider()
        from permita38.core.service.allocations import AllocationService  # deferred: avoids an import cycle

        self.allocs = AllocationService(store)

    # ------------------------------------------------------------------ the plan
    def _check_scope(self, project_id: str | None) -> str | None:
        if not project_id:
            return None
        if project_id != UNASSIGNED and not self.store.get("projects", project_id):
            raise ServiceError(404, "Project not found")
        return project_id

    @staticmethod
    def _verdicts(snap: Snapshot, items: Sequence[Item], hi: date) -> tuple[list[ProjectVerdict], supply_mod.Supply]:
        """Judge ``items`` against a supply that reaches ``hi`` or the latest item end, whichever is later."""
        hi = max([hi] + [i.end for i in items])
        supply = supply_mod.build(snap.owners, snap.assignments, snap.holiday, snap.today, _monday(snap.today), hi)
        return cover(items, snap.bookings, supply, snap.owners, snap.today, snap.names), supply

    def _no_demand(self, snap: Snapshot, verdicts: Sequence[ProjectVerdict]) -> list[dict]:
        owners = {o.id: o for o in snap.owners}
        items_of = {v.project_id: v.items for v in verdicts}
        out: list[dict] = []
        for a in snap.assignments:
            owner = owners.get(a.owner_id)
            if owner is None or owner.kind != "subcontractor" or a.project_kind != "customer" or a.end <= snap.today:
                continue
            if any(i.start <= a.end and i.end >= a.start and i.remaining > 0 for i in items_of.get(a.project_id, [])):
                continue
            hours = planned_hours(owner, a, snap.holiday)
            out.append(
                pw.warning(
                    "assignment.no_demand",
                    "info",
                    None,
                    f"{owner.name} is assigned {pw._fmt(hours)} h to {snap.names.get(a.project_id, a.project_id)} "
                    f"from {_dmy(a.start)} to {_dmy(a.end)}, but no contract or offer of the project needs hours "
                    "in that period.",
                    {
                        "assignment_id": a.id,
                        "project_id": a.project_id,
                        "supplier_id": a.owner_id,
                        "planned_hours": round(hours, 2),
                    },
                )
            )
        return out

    def _run(
        self, snap: Snapshot, weeks: int, user_id: str | None, project_id: str | None
    ) -> tuple[dict, list[ProjectVerdict]]:
        lo = _monday(snap.today)
        hi = lo + timedelta(weeks=weeks) - timedelta(days=1)
        verdicts, wide = self._verdicts(snap, snap.items, hi)
        supply = (
            wide if wide.hi == hi else supply_mod.build(snap.owners, snap.assignments, snap.holiday, snap.today, lo, hi)
        )
        body = plan_json(
            supply,
            snap.owners,
            snap.assignments,
            verdicts,
            snap.internal,
            snap.names,
            snap.subcontractor_cost,
            snap.invoiced,
            [],
            holiday=snap.holiday,
            user_id=user_id,
            project_id=project_id,
        )
        warnings = supply_mod.warnings(snap.owners, snap.assignments, snap.holiday, snap.today, lo, hi)
        warnings += verdict_warnings(verdicts, snap.names)
        for p in body["projects"]:
            if p["kind"] == "internal" and p["status"] == "over_budget":
                total = p["booked"] + p["assigned"]
                warnings.append(
                    pw.warning(
                        "internal.over_budget",
                        "warning",
                        None,
                        f"{p['name']}: {pw._fmt(p['booked'])} h booked + {pw._fmt(p['assigned'])} h assigned = "
                        f"{pw._fmt(total)} h, more than the budget of {pw._fmt(p['budget_hours'])} h.",
                        {
                            "project_id": p["project_id"],
                            "booked": p["booked"],
                            "assigned": p["assigned"],
                            "budget_hours": p["budget_hours"],
                        },
                    )
                )
        warnings += self._no_demand(snap, verdicts)
        scope = None if project_id == UNASSIGNED else project_id
        kept = []
        for w in warnings:
            d = w.get("details") or {}
            if project_id is not None and d.get("project_id", _MISSING) not in (_MISSING, scope):
                continue
            if user_id is not None and d.get("user_id", _MISSING) not in (_MISSING, user_id):
                continue
            kept.append(w)
        body["warnings"] = kept
        body["skipped"] = list(snap.skipped)
        return body, verdicts

    def plan(
        self, weeks: int = 12, user_id: str | None = None, project_id: str | None = None, today: date | None = None
    ) -> dict:
        scope = self._check_scope(project_id)
        snap = load(self.store, today or date.today(), self.busy)
        return self._run(snap, weeks, user_id or None, scope)[0]

    def simulate(
        self,
        changes: dict | None,
        weeks: int = 12,
        user_id: str | None = None,
        project_id: str | None = None,
        today: date | None = None,
    ) -> dict:
        today = today or date.today()
        scope = self._check_scope(project_id)
        body, after = self._run(load(self.store, today, self.busy, changes), weeks, user_id or None, scope)
        before = self._run(load(self.store, today, self.busy), weeks, user_id or None, scope)[1]

        def shortfalls(vs: Sequence[ProjectVerdict]) -> dict[str, float]:
            return {(v.project_id or UNASSIGNED): round(v.shortfall_contracts, 2) for v in vs}

        b, a = shortfalls(before), shortfalls(after)
        keys = [k for k in set(a) | set(b) if (scope is None or k == scope) and (a.get(k, 0) > 0 or b.get(k, 0) > 0)]
        projects: list[dict[str, Any]] = [
            {
                "project_id": k,
                "shortfall_before": b.get(k, 0.0),
                "shortfall_after": a.get(k, 0.0),
                "delta": round(a.get(k, 0.0) - b.get(k, 0.0), 2),
            }
            for k in sorted(keys, key=lambda k: (-a.get(k, 0.0), k))
        ]
        tb = round(sum(p["shortfall_before"] for p in projects), 2)
        ta = round(sum(p["shortfall_after"] for p in projects), 2)
        body["delta"] = {
            "projects": projects,
            "total": {"shortfall_before": tb, "shortfall_after": ta, "delta": round(ta - tb, 2)},
        }
        return body

    # ------------------------------------------------------------------ offers and warnings for saved records
    def _reference_rate(self, project_id, customer_id, settings) -> float | None:
        proj = self.store.get("projects", project_id) if project_id else None
        cust_id = customer_id or (proj or {}).get("customer_id")
        cust = self.store.get("customers", cust_id) if cust_id else None
        for src in (proj, cust):
            if src and src.get("rate") not in (None, ""):
                return calc.fnum(src["rate"])
        return calc.fnum(settings.get("default_rate")) or None

    def _draft_item(self, draft: dict, item_id: str, settings: dict) -> Item | None:
        plan = draft.get("plan") or {}
        start, end = calc.d(plan.get("start")), calc.d(plan.get("end"))
        if not start or not end or end < start:
            return None
        hours = _hours(_plan_hours_fields(plan, draft))
        if hours is None:
            return None
        project = self.store.get("projects", draft["project_id"]) if draft.get("project_id") else None
        cust_id = draft.get("customer_id") or (project or {}).get("customer_id")
        customer = self.store.get("customers", cust_id) if cust_id else None
        default_win = calc.fnum(settings.get("planning_default_win_probability"), 100.0)
        win = calc.fnum(plan.get("win_probability"), default_win) / 100.0
        return Item(
            kind="offer",
            id=item_id,
            title="Offer draft",
            project_id=draft.get("project_id") or None,
            start=start,
            end=end,
            hours_per_week=hours[0],
            total_hours=hours[1],
            rate=_rate(
                plan.get("rate", draft.get("rate")),
                (project or {}).get("rate"),
                (customer or {}).get("rate"),
                settings.get("default_rate"),
            ),
            win=min(max(win, 0.0), 1.0),
        )

    def _offer_shortfall(self, draft: dict, exclude_id: str | None, settings: dict, today: date) -> list[dict]:
        item = self._draft_item(draft, exclude_id or "draft", settings)
        if item is None:
            return []
        snap = load(self.store, today, self.busy)
        items = [i for i in snap.items if not (i.kind == "offer" and i.id == item.id)] + [item]
        verdicts, _ = self._verdicts(snap, items, today)
        return [
            w
            for w in verdict_warnings(verdicts, snap.names)
            if w["code"] == "offer.shortfall" and w["details"].get("item_id") == item.id
        ]

    def check_offer(self, draft: dict, exclude_id: str | None = None, today: date | None = None) -> list[dict]:
        """``offer.shortfall`` of a draft next to the other items plus the document warnings."""
        today = today or date.today()
        settings = effective_settings(self.store.settings())
        out = self._offer_shortfall(draft, exclude_id, settings, today) if draft.get("plan") else []
        ref = self._reference_rate(draft.get("project_id"), draft.get("customer_id"), settings)
        return out + pw.document_warnings(draft, kind="offers", settings=settings, reference_rate=ref)

    def warnings_for(self, coll: str, rec: dict, today: date | None = None) -> list[dict]:
        settings = effective_settings(self.store.settings())
        today = today or date.today()
        if coll == "contracts":
            snap = load(self.store, today, self.busy)
            verdicts, _ = self._verdicts(snap, snap.items, today)
            return [
                w
                for w in verdict_warnings(verdicts, snap.names)
                if w["code"] == "contract.shortfall" and w["details"].get("item_id") == rec.get("id")
            ]
        if coll == "offers":
            out: list[dict] = []
            if rec.get("status") == "sent":
                out += self._offer_shortfall(rec, rec.get("id"), settings, today)
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

    # ------------------------------------------------------------------ entitlement
    def entitlement(self, user_id: str, year: int, today: date | None = None) -> dict:
        """Vacation days (working days only, public holidays excluded) for one person and year."""
        today = today or date.today()
        user = self.store.get("users", user_id)
        if not user:
            raise ServiceError(404, "User not found")
        holiday = _holiday_fn(effective_settings(self.store.settings()).get("state") or "")
        workdays = _workdays(user)
        used = planned = 0
        for a in self.store.list("absences"):
            if a.get("user_id") != user_id or a.get("kind", "vacation") != "vacation":
                continue
            s, e = calc.d(a.get("start")), calc.d(a.get("end") or a.get("start"))
            if not s or not e:
                continue
            for x in calc.daterange(max(s, date(year, 1, 1)), min(e, date(year, 12, 31))):
                if x.weekday() in workdays and not holiday(x):
                    if x < today:
                        used += 1
                    else:
                        planned += 1
        per_year, carry = user.get("vacation_days_per_year"), user.get("vacation_carry_over")
        total = calc.fnum(per_year) + calc.fnum(carry) if per_year not in (None, "") else None
        remaining = None if total is None else total - used - planned
        return dict(
            user_id=user_id,
            year=year,
            entitlement=total,
            used=used,
            planned=planned,
            remaining=remaining,
            over=bool(remaining is not None and remaining < 0),
        )

    # ------------------------------------------------------------------ writes: apply, scenarios, extension
    def _check_absence(self, absence: dict) -> None:
        if not self.store.get("users", absence.get("user_id") or ""):
            raise ServiceError(422, "Unknown person for an absence")
        if absence.get("end") and absence.get("start") and absence["end"] < absence["start"]:
            raise ServiceError(422, "The end must not be before the start")

    def apply(self, changes: dict, today: date | None = None) -> dict:
        """Write a change set (absences, commitment periods/hours, allocations) in one transaction."""
        applied = 0
        alloc_warnings: list[dict] = []
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
                        self._check_absence(merged)
                        self.store.save("absences", merged)
                else:
                    if ch.get("delete") or not ch.get("user_id") or not ch.get("start"):
                        raise ServiceError(422, "A new absence needs a person and a start date")
                    new = dict(
                        user_id=ch["user_id"],
                        kind=ch.get("kind") or "vacation",
                        start=ch["start"],
                        end=ch.get("end") or ch["start"],
                    )
                    self._check_absence(new)
                    self.store.save("absences", new)
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
                    for k, other in (("hours_per_week", "total_hours"), ("total_hours", "hours_per_week")):
                        if c.get(k) is not None:
                            rec[other] = None
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
                    for k, other in (("hours_per_week", "total_hours"), ("total_hours", "hours_per_week")):
                        if c.get(k) is not None:
                            plan[other] = None
                    if plan.get("start") and plan.get("end") and plan["end"] < plan["start"]:
                        raise ServiceError(422, "The end must not be before the start")
                    self.store.save("offers", {**old, "plan": plan})
                applied += 1
            for ch in changes.get("allocations") or []:
                alloc_warnings += self.allocs.apply_change(ch)
                applied += 1
        return dict(
            applied=applied,
            warnings=self.plan(26, today=today)["warnings"],
            allocation_warnings=alloc_warnings,
        )

    def list_scenarios(self, me: dict) -> list[dict]:
        return [s for s in self.store.list("plan_scenarios") if s.get("user_id") == me["id"]]

    def get_scenario(self, me: dict, rid: str) -> dict:
        s = self.store.get("plan_scenarios", rid)
        if not s or s.get("user_id") != me["id"]:
            raise ServiceError(404, "Not found")
        return s

    def save_scenario(self, me: dict, rid: str | None, name: str, changes: dict) -> dict:
        old = self.get_scenario(me, rid) if rid else None
        known = {pid for _, pid in scenario_project_refs((old or {}).get("changes"))}
        for _, pid in scenario_project_refs(changes):
            if pid not in known and not self.store.get("projects", pid):
                raise ServiceError(422, "Unknown project")
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
        if new_end <= old_end:
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


__all__ = ["PlannerService"]
