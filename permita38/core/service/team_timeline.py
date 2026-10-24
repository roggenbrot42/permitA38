"""Team timeline: who is away and when, and which contracts people work on (docs/contracts/team-timeline.md).

The matrix (``rights.py``) decides only whether a role may open the timeline. The viewer's *level* decides what the
response contains: ``off`` (403), ``away``, ``time-off`` or ``full``. Redaction happens here, on the server: a value the
level does not allow is removed or set to ``null``, never masked, so it cannot be recovered from the response.

Own absences are always returned in full (any level above ``off``); own contracts are not (they are not part of the
absences right). Contracts and weekly hours are shown at ``full`` only, weekly hours also on the viewer's own row.
"""

from collections import defaultdict
from datetime import date
from typing import Any

from permita38.core import rights
from permita38.core.errors import ServiceError
from permita38.core.holidays import holidays as public_holidays
from permita38.core.repository import Repository

#: the settings key (stored in the settings row's extra JSON; see docs/roles.md)
SETTING = "team_timeline_visibility"
LEVELS = ("off", "away", "time-off", "full")
#: the roles whose level is configurable; the boss is always ``full``
CONFIGURABLE = ("employee", "accountant")
DEFAULT_VISIBILITY = {"employee": "away", "accountant": "off"}
#: the window: ``end - start`` is at most this many days (366 days inclusive)
MAX_SPAN_DAYS = 365
#: kinds shown at ``time-off`` (allow-list: any other stored kind, legacy or unknown, is shown as plain away)
TIME_OFF_KINDS = ("vacation", "holiday", "other")
#: kinds that carry a label at ``time-off`` (sick days and other kinds never do)
LABELLED_AT_TIME_OFF = ("vacation", "holiday")


def visibility(settings: dict) -> dict[str, str]:
    """The level per configurable role, with the defaults filled in. An unknown stored value counts as the default."""
    stored = settings.get(SETTING)
    stored = stored if isinstance(stored, dict) else {}
    return {r: stored[r] if stored.get(r) in LEVELS else DEFAULT_VISIBILITY[r] for r in CONFIGURABLE}


def level_for(role: object, settings: dict) -> str:
    """The effective level of a role. Roles without the timeline right get ``off``."""
    role = rights.normalize_role(role)
    if role == rights.BOSS:
        return "full"
    if role in CONFIGURABLE:
        return visibility(settings)[role]
    return "off"


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class TeamTimelineService:
    def __init__(self, store: Repository):
        self.store = store

    def timeline(self, viewer: dict, start: date, end: date) -> dict:
        settings = self.store.settings()
        level = level_for(viewer.get("role"), settings)
        if level == "off":
            raise ServiceError(403, "Forbidden")
        if end < start:
            raise ServiceError(422, "end must not be before start")
        if (end - start).days > MAX_SPAN_DAYS:
            raise ServiceError(422, f"the window may span at most {MAX_SPAN_DAYS} days")
        people = self._people(viewer)
        ids = {p["id"] for p in people}
        absences = self._absences(start, end)
        contracts: dict[str, list[dict]] = {}
        if level == "full":  # contracts are not part of the absences right: nothing below full
            contracts = self._contracts(start, end, ids)
        out = []
        for u in people:
            own = u["id"] == viewer.get("id")
            out.append(
                {
                    "id": u["id"],
                    "name": u.get("name"),
                    "color": u.get("color") or None,
                    "weekly_hours": u.get("weekly_hours") if (level == "full" or own) else None,
                    "absences": [_absence(a, level, own) for a in sorted(absences.get(u["id"], []), key=_sort_key)],
                    "contracts": contracts.get(u["id"], []),
                }
            )
        return {
            "level": level,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "holidays": _holidays(settings.get("state") or "", start, end),
            "people": out,
        }

    def _people(self, viewer: dict) -> list[dict]:
        active = [u for u in self.store.list("users") if u.get("active") is not False and u.get("id")]
        return sorted(
            active,
            key=lambda u: (u["id"] != viewer.get("id"), (u.get("name") or "").casefold(), u["id"]),
        )

    def _absences(self, start: date, end: date) -> dict[str, list[dict]]:
        """Whole stored entries that overlap the window, per user; malformed entries are skipped."""
        out: dict[str, list[dict]] = defaultdict(list)
        for a in self.store.list("absences"):
            s = _day(a.get("start"))
            e = _day(a.get("end")) or s
            if s is None or e is None or e < s or e < start or s > end or not a.get("user_id"):
                continue
            out[a["user_id"]].append({"start": s, "end": e, "kind": a.get("kind", "vacation"), "note": a.get("note")})
        return out

    def _contracts(self, start: date, end: date, user_ids: set[str]) -> dict[str, list[dict]]:
        """Contracts that overlap the window and list an active person, per user (open-ended: ``end`` is None)."""
        out: dict[str, list[dict]] = defaultdict(list)
        customers = {c["id"]: c.get("name") for c in self.store.list("customers")}
        for c in self.store.list("contracts"):
            s = _day(c.get("start"))
            e = _day(c.get("end"))
            if s is None or s > end or (e is not None and e < start):
                continue
            entry = {
                "id": c.get("id"),
                "title": c.get("title"),
                "customer": customers.get(c.get("customer_id")) if c.get("customer_id") else None,
                "start": s.isoformat(),
                "end": e.isoformat() if e else None,
                "hours_per_week": c.get("hours_per_week"),
            }
            for uid in c.get("user_ids") or []:
                if uid in user_ids:
                    out[uid].append(entry)
        for entries in out.values():
            entries.sort(key=lambda x: (x["start"], x["id"] or ""))
        return out


def _sort_key(a: dict) -> tuple[date, date]:
    return (a["start"], a["end"])


def _absence(a: dict, level: str, own: bool) -> dict:
    s, e = a["start"].isoformat(), a["end"].isoformat()
    if own or level == "full":
        return {"start": s, "end": e, "kind": a["kind"], "label": a["note"]}
    if level == "time-off" and a["kind"] in TIME_OFF_KINDS:
        label = a["note"] if a["kind"] in LABELLED_AT_TIME_OFF else None
        return {"start": s, "end": e, "kind": a["kind"], "label": label}
    return {"start": s, "end": e, "kind": None, "label": None}


def _holidays(state: str, start: date, end: date) -> dict[str, str]:
    out: dict[str, str] = {}
    for year in range(start.year, end.year + 1):
        for day, name in public_holidays(year, state).items():
            if start <= day <= end:
                out[day.isoformat()] = name
    return dict(sorted(out.items()))
