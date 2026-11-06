"""Timer, dashboard and capacity planning."""

import logging
import time as _time
from datetime import date, timedelta

from permita38.core import calc, rights
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

from .project_customer import project_kind

log = logging.getLogger("permita38.time")


class TimeService:
    def __init__(self, store: Repository):
        self.store = store

    def _write_timer(self, user_id: str, data: dict):
        self.store.save("timers", {**data, "id": user_id})

    def get_timer(self, me: dict) -> dict:
        return self.store.get("timers", me["id"]) or {"running": False}

    def start_timer(self, me: dict, project_id: str | None, task_id: str | None, note: str = "") -> dict:
        if project_id and not self.store.get("projects", project_id):
            raise ServiceError(422, "Unknown project")
        if self.get_timer(me).get("running"):
            self.stop_timer(me, None)
        t = dict(
            id=me["id"],
            running=True,
            started=int(_time.time()),
            project_id=project_id,
            task_id=task_id,
            note=note or "",
        )
        self._write_timer(me["id"], t)
        return t

    def update_timer(self, me: dict, changes: dict) -> dict:
        cur = self.get_timer(me)
        if not cur.get("running"):
            raise ServiceError(409, "No timer is running")
        project_id = changes["project_id"] if "project_id" in changes else cur.get("project_id")
        task_id = changes["task_id"] if "task_id" in changes else cur.get("task_id")
        if "project_id" in changes and "task_id" not in changes and project_id != cur.get("project_id"):
            task_id = None  # the old task belongs to the old project
        if project_id and not self.store.get("projects", project_id):
            raise ServiceError(422, "Unknown project")
        if task_id:
            task = self.store.get("tasks", task_id)
            if not task:
                raise ServiceError(422, "Unknown task")
            if task.get("project_id") != project_id:
                raise ServiceError(422, "The task does not belong to the project")
        t = {**cur, "project_id": project_id, "task_id": task_id}
        if changes.get("note") is not None:
            t["note"] = changes["note"]
        self._write_timer(me["id"], t)
        return self.get_timer(me)

    def stop_timer(self, me: dict, note: str | None, discard: bool = False) -> dict:
        cur = self.get_timer(me)
        if not cur.get("running"):
            return {"running": False}
        if discard:
            self._write_timer(me["id"], dict(id=me["id"], running=False))
            return {"running": False}
        now = int(_time.time())
        start = _time.localtime(cur["started"])
        end_text = _time.strftime("%H:%M")
        if date.fromtimestamp(now) > date(start.tm_year, start.tm_mon, start.tm_mday):
            # past midnight: the entry ends at 23:59 of its start day (a 24 h day cannot be exceeded)
            end_text = "23:59"
            minutes = max(1, (23 * 60 + 59) - (start.tm_hour * 60 + start.tm_min))
        else:
            minutes = max(1, round((now - cur["started"]) / 60))
        project_id, task_id = cur.get("project_id"), cur.get("task_id")
        if project_id and not self.store.get("projects", project_id):
            # a timer from before the reference guards may point at a project deleted back then: keep the time
            task = self.store.get("tasks", task_id) if task_id else None
            if task is None or task.get("project_id") == project_id:
                task_id = None
            log.info(
                "stop_timer: project %s of the running timer of user %s is gone; booking without it",
                project_id,
                me["id"],
            )
            project_id = None
        project = self.store.get("projects", project_id) if project_id else None
        internal = project is not None and project_kind(project) == "internal"
        entry = self.store.save(
            "entries",
            dict(
                user_id=me["id"],
                date=_time.strftime("%Y-%m-%d", start),
                project_id=project_id,
                task_id=task_id,
                note=note if note is not None else cur.get("note", ""),
                minutes=minutes,
                start=_time.strftime("%H:%M", start),
                end=end_text,
                billable=not internal,
            ),
        )
        self._write_timer(me["id"], dict(id=me["id"], running=False))
        return {"running": False, "entry": entry}

    def dashboard(self, me: dict, user_id: str | None) -> dict:
        target = me
        if user_id and user_id != me["id"]:
            if rights.scope_for(me, "time") != rights.Scope.ALL:
                raise ServiceError(403, "Forbidden")
            found = self.store.get("users", user_id)
            if not found:
                raise ServiceError(404, "User not found")
            target = found
        today = date.today()
        out = calc.dashboard(self.store, target, today)
        out["projects"] = self._project_hours(target["id"], today)
        return out

    def _project_hours(self, user_id: str, today: date) -> list[dict]:
        """Planned (S1, whole period) and booked hours of one person per project, today and in the ISO week."""
        from permita38.core.service.planner import load as planner_load
        from permita38.core.service.planner.spread import spread

        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        snap = planner_load.load(self.store, today)
        owner = next((o for o in snap.owners if o.id == user_id), None)
        rows: dict[str, dict] = {}
        task_minutes: dict[str, dict[str | None, float]] = {}

        def row(pid: str) -> dict:
            return rows.setdefault(
                pid, {"planned_today": 0.0, "planned_week": 0.0, "booked_today": 0.0, "booked_week": 0.0}
            )

        if owner is not None:
            for a in snap.assignments:
                if a.owner_id != user_id:
                    continue
                last = a.end if a.cut is None else min(a.end, a.cut - timedelta(days=1))
                if a.start > sunday or last < monday:
                    continue
                r = row(a.project_id)
                for day, h in spread(owner, a, snap.holiday, None).items():
                    if monday <= day <= sunday:
                        r["planned_week"] += h
                    if day == today:
                        r["planned_today"] += h
        for e in self.store.find("entries", user_id=user_id):
            pid, day = e.get("project_id"), calc.d(e.get("date"))
            if not pid or day is None or not monday <= day <= sunday:
                continue
            r = row(pid)
            hours = calc.fnum(e.get("minutes")) / 60.0
            r["booked_week"] += hours
            tid = e.get("task_id") or None
            if tid:
                per = task_minutes.setdefault(pid, {})
                per[tid] = per.get(tid, 0.0) + hours
            if day == today:
                r["booked_today"] += hours
        projects = {pid: self.store.get("projects", pid) or {} for pid in rows}
        task_ids = {tid for per in task_minutes.values() for tid in per if tid}
        task_names = {tid: (self.store.get("tasks", tid) or {}).get("name") for tid in task_ids}
        out = []
        for pid, r in rows.items():
            p = projects[pid]
            vals = {k: round(v, 2) for k, v in r.items()}
            tasks: list[dict] = [
                {"task_id": tid, "name": task_names.get(tid) or "(deleted task)", "booked_week": round(h, 2)}
                for tid, h in task_minutes.get(pid, {}).items()
            ]
            tasks.sort(key=lambda x: (-x["booked_week"], x["name"]))
            out.append(
                {
                    "project_id": pid,
                    "name": p.get("name") or snap.names.get(pid) or pid,
                    "kind": "internal" if p.get("kind") == "internal" else "customer",
                    **vals,
                    "tasks": tasks,
                    "left_week": round(max(0.0, r["planned_week"] - r["booked_week"]), 2),
                }
            )
        out.sort(key=lambda x: (-x["left_week"], x["name"]))
        return out

    def holidays(self, year: int, state: str) -> dict:
        from permita38.core.holidays import holidays

        return {k.isoformat(): v for k, v in holidays(year, state).items()}
