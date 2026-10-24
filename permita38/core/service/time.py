"""Timer, dashboard and capacity planning."""

import time as _time

from permita38.core import calc, rights
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository


class TimeService:
    def __init__(self, store: Repository):
        self.store = store

    def _write_timer(self, user_id: str, data: dict):
        self.store.save("timers", {**data, "id": user_id})

    def get_timer(self, me: dict) -> dict:
        return self.store.get("timers", me["id"]) or {"running": False}

    def start_timer(self, me: dict, project_id: str | None, task_id: str | None, note: str = "") -> dict:
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

    def stop_timer(self, me: dict, note: str | None) -> dict:
        cur = self.get_timer(me)
        if not cur.get("running"):
            return {"running": False}
        now = int(_time.time())
        minutes = max(1, round((now - cur["started"]) / 60))
        start = _time.localtime(cur["started"])
        entry = self.store.save(
            "entries",
            dict(
                user_id=me["id"],
                date=_time.strftime("%Y-%m-%d", start),
                project_id=cur.get("project_id"),
                task_id=cur.get("task_id"),
                note=note if note is not None else cur.get("note", ""),
                minutes=minutes,
                start=_time.strftime("%H:%M", start),
                end=_time.strftime("%H:%M"),
                billable=True,
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
        return calc.dashboard(self.store, target)

    def holidays(self, year: int, state: str) -> dict:
        from permita38.core.holidays import holidays

        return {k.isoformat(): v for k, v in holidays(year, state).items()}
