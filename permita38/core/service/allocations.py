"""Employee allocations: hours per week of one person given to one project for a period (the ring fence of the
planner, docs/specs/planner-per-project.md, API contract docs/contracts/planner-per-project.md).

Only employee rows are written here. Rows of the ``allocations`` collection with a ``supplier_id`` belong to the
subcontractor feature: they are listed (for callers who may see them) and answer 409 on change. Over-allocation is
never an error, it is a warning that comes back with the saved row.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.money import ZERO, dec, q2
from permita38.core.repository import Repository

from . import external_capacity as ext
from . import planning as eng
from . import planning_warnings as pw

CLOSED_PROJECT = eng.CLOSED_PROJECT
MAX_BOOTSTRAP = 500
MAX_PERIOD_DAYS = eng.MAX_ALLOCATION_DAYS
MAX_HOURS = 80.0
Rows = list[dict]  # alias: a method named `list` shadows the builtin inside the class body


def _is_external(row: dict) -> bool:
    return bool(row.get("supplier_id")) or not row.get("user_id")


def _active(row: dict) -> bool:
    return row.get("status", "active") == "active"


class AllocationService:
    def __init__(self, store: Repository):
        self.store = store

    # ------------------------------------------------------------------ presentation
    def _planned_hours(self, cap: calc.Capacity, user: dict | None, row: dict) -> float | None:
        if _is_external(row):
            return None
        if not user:
            return 0.0
        wd = calc._workdays(user)
        per_day = calc.fnum(row.get("hours_per_week")) / len(wd)
        s, e = calc.d(row.get("start")), calc.d(row.get("end"))
        if not s or not e:
            return 0.0
        total = 0.0
        x = s
        while x <= e:
            if x.weekday() in wd and not cap.holiday(x) and x not in cap.absences[user["id"]]:
                total += per_day
            x += timedelta(days=1)
        return round(total, 2)

    # ------------------------------------------------------------------ external rows (subcontractors)
    def invoiced(self) -> dict[str, Decimal]:
        """Net of the expenses linked to each allocation."""
        out: dict[str, Decimal] = {}
        for e in self.store.list("expenses"):
            aid = e.get("allocation_id")
            if aid:
                out[aid] = out.get(aid, ZERO) + dec(e.get("net"))
        return out

    def external_figures(self, row: dict, supplier: dict | None, cap: calc.Capacity, invoiced: Decimal) -> dict:
        f = ext.figures(supplier or {}, row, cap)
        planned = f.planned_net
        return dict(
            planned_hours=f.planned_hours,
            planned_net=float(planned),
            invoiced_net=float(q2(invoiced)),
            open_net=float(max(ZERO, planned - q2(invoiced))),
        )

    def present_external(
        self, row: dict, cap: calc.Capacity, suppliers: dict, projects: dict, offers: dict, invoiced: dict, days=False
    ) -> dict:
        supplier = suppliers.get(row.get("supplier_id") or "")
        project = projects.get(row.get("project_id") or "")
        out = {k: v for k, v in row.items() if v is not None}
        out["kind"] = "external"
        out["status"] = row.get("status") or "active"
        out["project_name"] = (project or {}).get("name")
        out["supplier_name"] = (supplier or {}).get("name")
        offer = offers.get(row.get("incoming_offer_id") or "")
        if offer and offer.get("reference"):
            out["offer_reference"] = offer["reference"]
        out.update(self.external_figures(row, supplier, cap, invoiced.get(row["id"], ZERO)))
        if days:
            out["days"] = [
                {"date": x.isoformat(), "hours": round(h, 6)}
                for x, h in sorted(ext.allocation_days(supplier or {}, row, cap).items())
            ]
        return out

    def _external_context(self) -> tuple[dict, dict, dict]:
        suppliers = {s["id"]: s for s in self.store.list("suppliers")}
        offers = {o["id"]: o for o in self.store.list("incoming_offers")}
        return suppliers, offers, self.invoiced()

    def present_one(self, row: dict, days: bool = False) -> dict:
        """One stored row as the API shows it (employee or external)."""
        cap, users, projects = self._context()
        if _is_external(row):
            suppliers, offers, invoiced = self._external_context()
            return self.present_external(row, cap, suppliers, projects, offers, invoiced, days)
        return self._present(row, cap, users, projects)

    def _present(self, row: dict, cap: calc.Capacity, users: dict, projects: dict) -> dict:
        user = users.get(row.get("user_id") or "")
        project = projects.get(row.get("project_id") or "")
        out = {k: v for k, v in row.items() if v is not None}
        out["kind"] = "external" if _is_external(row) else "employee"
        out["status"] = row.get("status") or "active"
        out["project_name"] = (project or {}).get("name")  # null when the project is gone
        if not _is_external(row):
            out["user_name"] = (user.get("name") or user.get("username") or "") if user else None
        planned = self._planned_hours(cap, user, row)
        if planned is not None:
            out["planned_hours"] = planned
        return out

    def _context(self) -> tuple[calc.Capacity, dict, dict]:
        ctx = calc.context(self.store)
        return calc.Capacity(self.store, ctx), ctx["users"], ctx["projects"]

    # ------------------------------------------------------------------ reads
    def list(
        self,
        project_id: str | None = None,
        user_id: str | None = None,
        supplier_id: str | None = None,
        status: str | None = None,
        include_external: bool = False,
    ) -> Rows:
        rows = []
        for r in self.store.list("allocations"):
            if _is_external(r) and not include_external:
                continue
            if project_id and r.get("project_id") != project_id:
                continue
            if user_id and r.get("user_id") != user_id:
                continue
            if supplier_id and r.get("supplier_id") != supplier_id:
                continue
            if status and (r.get("status") or "active") != status:
                continue
            rows.append(r)
        rows.sort(key=lambda r: str(r.get("id") or ""))
        rows.sort(key=lambda r: str(r.get("start") or ""), reverse=True)
        cap, users, projects = self._context()
        suppliers, offers, invoiced = self._external_context() if any(_is_external(r) for r in rows) else ({}, {}, {})
        return [
            self.present_external(r, cap, suppliers, projects, offers, invoiced)
            if _is_external(r)
            else self._present(r, cap, users, projects)
            for r in rows
        ]

    # ------------------------------------------------------------------ writes
    def _check(self, body: dict) -> tuple[dict, dict]:
        user = self.store.get("users", body["user_id"])
        if not user:
            raise ServiceError(404, "User not found")
        project = self.store.get("projects", body["project_id"])
        if not project:
            raise ServiceError(404, "Project not found")
        if body["end"] < body["start"]:
            raise ServiceError(409, "The end date is before the start date")
        if (date.fromisoformat(body["end"]) - date.fromisoformat(body["start"])).days > eng.MAX_ALLOCATION_DAYS:
            raise ServiceError(422, f"The period may be at most {eng.MAX_ALLOCATION_DAYS} days")
        if (project.get("status") or "active") in CLOSED_PROJECT:
            raise ServiceError(409, "The project is closed")
        if not user.get("active", True):
            raise ServiceError(409, "The user is not active")
        return user, project

    @staticmethod
    def _fields(body: dict, project: dict) -> dict:
        row = {k: body[k] for k in ("user_id", "project_id", "start", "end", "hours_per_week")}
        if project.get("customer_id"):
            row["customer_id"] = project["customer_id"]
        return row

    def _employee_rows(self) -> Rows:
        return [
            r for r in self.store.list("allocations") if not _is_external(r) and _active(r) and eng.usable_allocation(r)
        ]

    def _answer(self, saved: dict, user: dict, project: dict) -> dict:
        cap, users, projects = self._context()
        out = self._present(saved, cap, users, projects)
        rows = [r for r in self._employee_rows() if r.get("user_id") == user["id"]]
        out["planning_warnings"] = pw.over_allocation_on_save(user, rows, saved["start"], saved["end"]) + (
            pw.overlap_same_project(
                rows, user["id"], saved["project_id"], saved["start"], saved["end"], project.get("name") or ""
            )
        )
        return out

    def create(self, body: dict) -> dict:
        user, project = self._check(body)
        with self.store.transaction():
            saved = self.store.save("allocations", {**self._fields(body, project), "status": "active"})
        return self._answer(saved, user, project)

    def update(self, rid: str, body: dict) -> dict:
        old = self.store.get("allocations", rid)
        if not old:
            raise ServiceError(404, "Not found")
        if _is_external(old) or not _active(old):
            raise ServiceError(409, "This allocation cannot be changed here")
        user, project = self._check(body)
        with self.store.transaction():
            fields = self._fields(body, project)
            merged = {k: v for k, v in old.items() if k != "customer_id"}
            saved = self.store.save("allocations", {**merged, **fields})
        return self._answer(saved, user, project)

    def delete(self, rid: str) -> None:
        old = self.store.get("allocations", rid)
        if not old:
            raise ServiceError(404, "Not found")
        if _is_external(old) or not _active(old):
            raise ServiceError(409, "This allocation cannot be changed here")
        self.store.delete("allocations", rid)

    # ------------------------------------------------------------------ what-if rows written by plan/apply
    def apply_change(self, ch: dict) -> Rows:
        """Write one ``AllocationChange`` (create, update or delete); returns the D2 warnings of the row written."""
        rid = ch.get("id")
        if rid:
            old = self.store.get("allocations", rid)
            if not old:
                raise ServiceError(404, "Allocation not found")
            if _is_external(old) or not _active(old):
                raise ServiceError(409, "This allocation cannot be changed here")
            if ch.get("delete"):
                self.store.delete("allocations", rid)
                return []
            body = {
                k: ch[k] if ch.get(k) is not None else old[k]
                for k in ("user_id", "project_id", "start", "end", "hours_per_week")
            }
            return self.update(rid, body)["planning_warnings"]
        body = {k: ch[k] for k in ("user_id", "project_id", "start", "end", "hours_per_week")}
        return self.create(body)["planning_warnings"]

    # ------------------------------------------------------------------ bootstrap
    @staticmethod
    def _round_up_quarter(x: float) -> float:
        return math.ceil(x * 4 - 1e-6) / 4

    def _proposals(self, today: date) -> tuple[Rows, Rows, Rows]:
        """(proposals, skipped, clamped). One day table per assignee over the whole range of the eligible contracts."""
        world = eng.load(self.store, today)
        existing = [r for r in self._employee_rows()]
        eligible: list[tuple[eng.Item, dict]] = []
        for it in world.items:
            if it.kind != "contract" or not it.project_id or it.end < today:
                continue  # offers are not allocated; a contract that is over needs no capacity
            project = self.store.get("projects", it.project_id)
            if not project or (project.get("status") or "active") in CLOSED_PROJECT:
                continue
            eligible.append((it, project))
        proposals: Rows = []
        skipped: Rows = []
        clamped: Rows = []
        tables: dict[str, eng.DayTable] = {}
        usable = [(it, pr) for it, pr in eligible if (it.end - it.start).days <= MAX_PERIOD_DAYS]
        if usable:
            lo, hi = min(it.start for it, _ in usable), max(it.end for it, _ in usable)
            for uid in sorted({u for it, _ in usable for u in it.assignees if u in world.users}):
                tables[uid] = eng.day_table(world, lo, hi, uid)
        for it, project in eligible:
            contract = self.store.get("contracts", it.id) or {}
            for uid in it.assignees:
                if uid not in world.users:  # subcontractors are never proposed as employee allocations
                    continue
                base = dict(user_id=uid, project_id=it.project_id, source_contract_id=it.id)
                if (it.end - it.start).days > MAX_PERIOD_DAYS:
                    skipped.append({**base, "reason": "period_too_long"})
                    continue
                per = tables[uid].item_hours.get(eng._key(it.kind, it.id), {})
                weekly: dict[date, float] = {}
                for x, h in per.items():
                    weekly[eng.week_start(x)] = weekly.get(eng.week_start(x), 0.0) + h
                raw = self._round_up_quarter(max(weekly.values(), default=0.0))
                peak = min(raw, MAX_HOURS)
                if peak <= 0:
                    continue
                s, e = it.start.isoformat(), it.end.isoformat()
                if any(
                    a["user_id"] == uid and a["project_id"] == it.project_id and a["start"] <= s and a["end"] >= e
                    for a in existing
                ):
                    skipped.append({**base, "reason": "already_allocated"})
                    continue
                if raw > MAX_HOURS:
                    clamped.append({**base, "peak": raw, "hours_per_week": MAX_HOURS})
                user = world.users.get(uid) or {}
                proposals.append(
                    dict(
                        user_id=uid,
                        user_name=user.get("name") or "",
                        project_id=it.project_id,
                        project_name=project.get("name") or eng.DELETED_PROJECT,
                        start=s,
                        end=e,
                        hours_per_week=peak,
                        source_contract_id=it.id,
                        source_contract_title=contract.get("title") or it.title,
                    )
                )
        if len(proposals) > MAX_BOOTSTRAP:
            raise ServiceError(409, f"Too many allocations to create at once (max {MAX_BOOTSTRAP})")
        return proposals, skipped, clamped

    def bootstrap(self, confirm: bool, today: date | None = None) -> dict:
        """Proposals (``confirm`` false) or writes (true) one allocation per active contract with a project and
        assignee: the contract's period and the assignee's peak weekly demand, rounded up to 0.25 h."""
        proposals, skipped, clamped = self._proposals(today or date.today())
        warnings = self._bootstrap_warnings(proposals, clamped)  # before the write: preview and confirm are equal
        out_rows = proposals
        if confirm:
            out_rows = []
            with self.store.transaction():
                for p in proposals:
                    body = {k: p[k] for k in ("user_id", "project_id", "start", "end", "hours_per_week")}
                    project = self.store.get("projects", p["project_id"]) or {}
                    saved = self.store.save("allocations", {**self._fields(body, project), "status": "active"})
                    out_rows.append({**p, "id": saved["id"], "kind": "employee"})
        return dict(confirm=confirm, created=confirm, allocations=out_rows, skipped=skipped, warnings=warnings)

    def _bootstrap_warnings(self, proposals: Rows, clamped: Rows) -> Rows:
        """Warnings of the resulting set (stored rows plus proposals): at most 10 weeks per person and code."""
        merged = list(self._employee_rows())
        for i, p in enumerate(proposals):
            merged.append(
                dict(
                    id=f"proposal{i}",
                    **{k: p[k] for k in ("user_id", "project_id", "start", "end", "hours_per_week")},
                )
            )
        out: Rows = []
        for c in clamped:
            out.append(
                pw.warning(
                    "allocation.peak_clamped",
                    "info",
                    "allocations",
                    f"The peak weekly demand of a contract is {c['peak']:g} h; the allocation is limited to "
                    f"{c['hours_per_week']:g} h per week.",
                    c,
                    confirmable=False,
                )
            )
        users = {u["id"]: u for u in self.store.list("users")}
        over_weeks: dict[str, dict[str, dict]] = {}
        overlap_weeks: dict[tuple[str, str], dict[str, dict]] = {}
        for p in proposals:
            user = users.get(p["user_id"])
            mine = [a for a in merged if a["user_id"] == p["user_id"]]
            if user:  # only the weeks of the proposal itself are looked at
                for w in pw.over_allocation_on_save(user, mine, p["start"], p["end"]):
                    over_weeks.setdefault(p["user_id"], {}).setdefault(w["details"]["week"], w)
            key = (p["user_id"], p["project_id"])
            same = [a for a in mine if a["project_id"] == p["project_id"]]
            for w in pw.overlap_same_project(same, key[0], key[1], p["start"], p["end"], p["project_name"]):
                overlap_weeks.setdefault(key, {}).setdefault(w["details"]["week"], w)
        for table in (*over_weeks.values(), *overlap_weeks.values()):
            out += [table[wk] for wk in sorted(table)[:10]]
        return out
