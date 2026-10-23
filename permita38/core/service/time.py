"""Timer, dashboard and capacity planning."""

import time as _time

from permita38.core import calc
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository


MAX_CLOCK_SKEW = 120  # seconds a client clock may run ahead of the server
MAX_OFFLINE_AGE = 7 * 24 * 3600  # queued timer actions older than this are rejected
REPLAY_TOLERANCE = 5  # seconds: a start within this window of the running timer is a replay
MAX_ENTRY_MINUTES = 1440


class TimeService:
    def __init__(self, store: Repository):
        self.store = store

    def _write_timer(self, user_id: str, data: dict):
        self.store.save("timers", {**data, "id": user_id})

    def get_timer(self, me: dict) -> dict:
        return self.store.get("timers", me["id"]) or {"running": False}

    @staticmethod
    def _client_time(at: int | None) -> int:
        """The moment a (possibly queued-while-offline) timer action really happened, validated against the server clock."""
        now = int(_time.time())
        if at is None:
            return now
        if at > now + MAX_CLOCK_SKEW:
            raise ServiceError(422, "at: the time is in the future")
        if at < now - MAX_OFFLINE_AGE:
            raise ServiceError(422, "at: the time is more than 7 days ago")
        return min(at, now)

    def start_timer(
        self, me: dict, project_id: str | None, task_id: str | None, note: str = "", at: int | None = None
    ) -> dict:
        started = self._client_time(at)
        current = self.get_timer(me)
        if current.get("running"):
            same = current.get("project_id") == project_id and current.get("task_id") == task_id
            if at is not None and same and abs(current["started"] - started) <= REPLAY_TOLERANCE:
                return current  # replay of a start that already reached the server
            self.stop_timer(me, None, at=started if started >= current["started"] else None)
        t = dict(
            id=me["id"],
            running=True,
            started=started,
            project_id=project_id,
            task_id=task_id,
            note=note or "",
        )
        self._write_timer(me["id"], t)
        return t

    def stop_timer(self, me: dict, note: str | None, at: int | None = None) -> dict:
        cur = self.get_timer(me)
        if not cur.get("running"):
            return {"running": False}  # nothing running: a replayed stop is a harmless no-op
        now = self._client_time(at)
        if now < cur["started"]:
            raise ServiceError(422, "at: the stop time is before the start")
        minutes = min(MAX_ENTRY_MINUTES, max(1, round((now - cur["started"]) / 60)))
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
                end=_time.strftime("%H:%M", _time.localtime(now)),
                billable=True,
            ),
        )
        self._write_timer(me["id"], dict(id=me["id"], running=False))
        return {"running": False, "entry": entry}

    def dashboard(self, me: dict, user_id: str | None) -> dict:
        target = me
        if user_id and user_id != me["id"]:
            if me["role"] != "boss":
                raise ServiceError(403, "Forbidden")
            found = self.store.get("users", user_id)
            if not found:
                raise ServiceError(404, "User not found")
            target = found
        return calc.dashboard(self.store, target)

    def forecast(self, weeks: int, user_id: str | None, include_offers: bool) -> dict:
        return calc.forecast(self.store, weeks, user_id or None, include_offers)

    def holidays(self, year: int, state: str) -> dict:
        from permita38.core.holidays import holidays

        return {k.isoformat(): v for k, v in holidays(year, state).items()}
