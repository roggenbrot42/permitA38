"""Employee allocations: hours per week of one person given to one project for a period (the ring fence of the
planner, docs/specs/planner-per-project.md, API contract docs/contracts/planner-per-project.md).

Only employee rows are written here. Rows of the ``allocations`` collection with a ``supplier_id`` belong to the
subcontractor feature: they are listed (for callers who may see them) and answer 409 on change. Over-allocation is
never an error, it is a warning that comes back with the saved row.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

from . import planning as eng
from . import planning_warnings as pw

CLOSED_PROJECT = ("done", "archived")
MAX_BOOTSTRAP = 500
MAX_PERIOD_DAYS = 1830
MAX_HOURS = 80.0


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

    def _present(self, row: dict, cap: calc.Capacity, users: dict, projects: dict) -> dict:
        user = users.get(row.get("user_id") or "")
        project = projects.get(row.get("project_id") or "")
        out = {k: v for k, v in row.items() if v is not None}
        out["kind"] = "external" if _is_external(row) else "employee"
        out["status"] = row.get("status") or "active"
        out["project_name"] = (project or {}).get("name") or eng.DELETED_PROJECT
        if user:
            out["user_name"] = user.get("name") or user.get("username") or ""
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
    ) -> list[dict]:
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
        rows.sort(key=lambda r: r.get("id") or "")
        rows.sort(key=lambda r: r.get("start") or "", reverse=True)
        cap, users, projects = self._context()
        return [self._present(r, cap, users, projects) for r in rows]

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

    def _employee_rows(self) -> list[dict]:
        return [r for r in self.store.list("allocations") if not _is_external(r) and _active(r)]

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
        if _is_external(old):
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
        if _is_external(old):
            raise ServiceError(409, "This allocation cannot be changed here")
        self.store.delete("allocations", rid)

    # ------------------------------------------------------------------ what-if rows written by plan/apply
    def apply_change(self, ch: dict) -> list[dict]:
        """Write one ``AllocationChange`` (create, update or delete); returns the D2 warnings of the row written."""
        rid = ch.get("id")
        if rid:
            old = self.store.get("allocations", rid)
            if not old:
                raise ServiceError(404, "Allocation not found")
            if _is_external(old):
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

    def _proposals(self, today: date) -> tuple[list[dict], list[dict]]:
        world = eng.load(self.store, today)
        existing = [r for r in self._employee_rows()]
        proposals: list[dict] = []
        skipped: list[dict] = []
        for it in world.items:
            if it.kind != "contract" or not it.project_id:
                continue
            project = self.store.get("projects", it.project_id)
            if not project or (project.get("status") or "active") in CLOSED_PROJECT:
                continue
            contract = self.store.get("contracts", it.id) or {}
            for uid in it.assignees:
                base = dict(user_id=uid, project_id=it.project_id, source_contract_id=it.id)
                if (it.end - it.start).days > MAX_PERIOD_DAYS:
                    skipped.append({**base, "reason": "period_too_long"})
                    continue
                per = eng.day_table(world, it.start, it.end, uid).item_hours.get(eng._key(it.kind, it.id), {})
                weekly: dict[date, float] = {}
                for x, h in per.items():
                    weekly[eng.week_start(x)] = weekly.get(eng.week_start(x), 0.0) + h
                peak = min(self._round_up_quarter(max(weekly.values(), default=0.0)), MAX_HOURS)
                if peak <= 0:
                    continue
                s, e = it.start.isoformat(), it.end.isoformat()
                if any(
                    a["user_id"] == uid and a["project_id"] == it.project_id and a["start"] <= s and a["end"] >= e
                    for a in existing
                ):
                    skipped.append({**base, "reason": "already_allocated"})
                    continue
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
        return proposals, skipped

    def bootstrap(self, confirm: bool, today: date | None = None) -> dict:
        """Proposals (``confirm`` false) or writes (true) one allocation per active contract with a project and
        assignee: the contract's period and the assignee's peak weekly demand, rounded up to 0.25 h."""
        proposals, skipped = self._proposals(today or date.today())
        out_rows = proposals
        if confirm:
            out_rows = []
            with self.store.transaction():
                for p in proposals:
                    body = {k: p[k] for k in ("user_id", "project_id", "start", "end", "hours_per_week")}
                    project = self.store.get("projects", p["project_id"]) or {}
                    saved = self.store.save("allocations", {**self._fields(body, project), "status": "active"})
                    out_rows.append({**p, "id": saved["id"], "kind": "employee"})
        return dict(
            confirm=confirm,
            created=confirm,
            allocations=out_rows,
            skipped=skipped,
            warnings=self._bootstrap_warnings(proposals),
        )

    def _bootstrap_warnings(self, proposals: list[dict]) -> list[dict]:
        """Warnings of the resulting set. Computed from the stored rows plus the proposals, so a preview and a
        confirmed run give the same list (the written rows are not counted twice)."""
        stored = self._employee_rows()
        known = {(a["user_id"], a["project_id"], a["start"], a["end"], a.get("hours_per_week")) for a in stored}
        merged = list(stored)
        for i, p in enumerate(proposals):
            if (p["user_id"], p["project_id"], p["start"], p["end"], p["hours_per_week"]) in known:
                continue  # already written by this run
            merged.append(dict(id=p.get("id") or f"proposal{i}", **{k: p[k] for k in (
                "user_id", "project_id", "start", "end", "hours_per_week")}))
        out: list[dict] = []
        users = {u["id"]: u for u in self.store.list("users")}
        seen_over: set[str] = set()
        seen_overlap: set[tuple[str, str]] = set()
        for p in proposals:
            user = users.get(p["user_id"])
            if user and p["user_id"] not in seen_over:
                seen_over.add(p["user_id"])
                mine = [a for a in merged if a["user_id"] == p["user_id"]]
                lo, hi = min(a["start"] for a in mine), max(a["end"] for a in mine)
                out += pw.over_allocation_on_save(user, mine, lo, hi)
            key = (p["user_id"], p["project_id"])
            if key not in seen_overlap:
                seen_overlap.add(key)
                mine = [a for a in merged if (a["user_id"], a["project_id"]) == key]
                out += pw.overlap_same_project(
                    mine, key[0], key[1], min(a["start"] for a in mine), max(a["end"] for a in mine), p["project_name"]
                )
        return out
