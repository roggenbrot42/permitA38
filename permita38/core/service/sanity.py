"""Advisory plausibility warnings for saved records. They are computed after a save, returned next to the record
(``sanity_warnings``, never stored) and never block anything."""

import logging
from datetime import date, timedelta

from permita38.core.holidays import holidays
from permita38.core.repository import ID_RE, Repository
from permita38.core.settings import effective_settings

from . import sanity_rules as rules

log = logging.getLogger(__name__)

#: collections that carry ``sanity_warnings`` in their save responses
CHECKED = ("customers", "entries", "offers", "invoices", "expenses")


def _parse(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


class SanityService:
    def __init__(self, store: Repository):
        self.store = store

    def previous(self, coll: str, rid: str) -> dict | None:
        """The stored record before an update (so untouched fields are not nagged about again)."""
        return self.store.get(coll, rid) if coll in CHECKED and ID_RE.match(rid) else None

    def check(self, coll: str, rec: dict, old: dict | None = None, *, finalize: bool = False) -> list[dict]:
        try:
            return self._check(coll, rec, old, finalize, date.today())
        except Exception:  # advisory: a bug in a rule must never turn a successful save into an error
            log.exception("sanity check of %s failed", coll)
            return []

    def settings_warnings(self, settings: dict) -> list[dict]:
        return rules.check_identifiers(settings, owner="settings")

    def _check(self, coll: str, rec: dict, old: dict | None, finalize: bool, today: date) -> list[dict]:
        if coll == "customers":
            return rules.check_identifiers(rec, owner="customer")
        if coll == "expenses":
            return rules.check_expense(
                rec, old=old, settings=self._settings(), others=self.store.list("expenses"), today=today
            )
        if coll in ("offers", "invoices"):
            return rules.check_document(
                coll,
                rec,
                old=old,
                settings=self._settings(),
                others=self.store.list(coll),
                today=today,
                finalize=finalize,
            )
        if coll == "entries":
            return self._entry(rec, old, today)
        return []

    def _settings(self) -> dict:
        return effective_settings(self.store.settings())

    def _entry(self, rec: dict, old: dict | None, today: date) -> list[dict]:
        uid, day = rec.get("user_id"), _parse(rec.get("date"))
        if not uid or day is None:
            return []
        wanted = {day.isoformat(), (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()}
        mine = [
            e
            for e in self.store.list("entries")
            if e.get("user_id") == uid and e.get("date") in wanted and e.get("id") != rec.get("id")
        ]
        user = self.store.get("users", uid) if ID_RE.match(str(uid)) else None
        employee = user is not None and user.get("role") != "boss"
        holiday = holidays(day.year, self._settings().get("state", "") or "").get(day)
        return rules.check_entry(
            rec,
            same_day=[e for e in mine if e.get("date") == day.isoformat()],
            prev_day=[e for e in mine if e.get("date") == (day - timedelta(days=1)).isoformat()],
            next_day=[e for e in mine if e.get("date") == (day + timedelta(days=1)).isoformat()],
            employee=employee,
            today=today,
            holiday=holiday,
            old=old,
        )
