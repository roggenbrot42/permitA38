"""Adapter between a business trip and the journal (docs/specs/accounting.md 7, 9.5, 10, 11, 16).

:class:`TripPostingService` is the hook of ``TripService``:

* a trip that is **final** posts ``trip:<id>:r<n>`` (D ``travel_costs`` / C ``private_deposit`` for the owner or
  ``payables_employee`` for a team member, no VAT) through :class:`~permita38.core.service.journal.JournalService`
  with the pure engine ``core/accounting/postings_other.py``;
* a trip that is no longer final (reopened) gets the reversal of its live entry (key + ``:rev``), booked like any
  reversal (spec 7.8); a changed trip is reversed and reposted as revision ``n + 1``. The revision is stored on the
  record (``post_rev`` = the highest revision posted, server-owned, never taken from a client);
* a reversal whose entry lies in a locked month is refused with 409 and nothing is changed (the caller's
  transaction rolls back).

Nothing posts before the accounting is initialised (settings key ``accounting.start``) or for a trip that ends
before the start. No SQL here.
"""

import dataclasses
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal, cast

from permita38.core import rights
from permita38.core.accounting.postings_other import TripEvent, trip_entry, trip_reversal_entry
from permita38.core.accounting.types import Cents, Entry, Line, Role
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import JournalService, JournalSource
from permita38.core.service.locks import first_unlocked_from
from permita38.core.service.postings import post_line
from permita38.core.settings import accounting_start
from permita38.core.trips_calc import effective_last

__all__ = ["TripPostingService"]

LOCKED = "The period is locked; book a correction"


def _cents(value: Any) -> Cents:
    return Cents(int(q2(dec(value)) * 100))


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def _rev(rec: Mapping[str, Any]) -> int:
    return int(rec.get("post_rev") or 0)


class TripPostingService:
    def __init__(self, store: Repository, journal: JournalService | None = None):
        self.store = store
        self.journal = journal or JournalService(store)

    # ---------- engine input
    def _closed(self, day: date) -> bool:
        rows = self.store.find("fiscal_years", year=day.year)
        return bool(rows) and rows[0].get("closed_at") is not None

    def _payee(self, trip: Mapping[str, Any]) -> Literal["owner", "employee"]:
        """Spec 9.5: the owner is a user with the role boss (``private_deposit``); any other traveller, accountant
        included, or an unknown user is owed money as ``payables_employee``."""
        user = self.store.get("users", str(trip["user_id"])) if trip.get("user_id") else None
        role = rights.normalize_role(user.get("role")) if user else ""
        return "owner" if role == rights.BOSS else "employee"

    def _event(self, trip: Mapping[str, Any], rev: int) -> TripEvent | None:
        """The engine input for a final trip ending on/after the accounting start, else ``None``."""
        start = accounting_start(self.store.settings())
        if start is None or trip.get("status") != "final" or not trip.get("start") or not trip.get("end"):
            return None
        first = datetime.strptime(str(trip["start"]), "%Y-%m-%dT%H:%M")
        last = effective_last(first, datetime.strptime(str(trip["end"]), "%Y-%m-%dT%H:%M"))
        if last < start:
            return None
        return TripEvent(
            trip_id=str(trip["id"]),
            revision=rev,
            end_date=last,
            allowance=_cents(trip.get("allowance_total")),
            mileage=_cents(trip.get("mileage_total")),
        )

    def _entry(self, trip: Mapping[str, Any], rev: int) -> Entry | None:
        """The entry the engine gives for the trip now (``None`` posts nothing); the ``income_date`` becomes the
        booking date when the trip's fiscal year is closed (spec 11)."""
        event = self._event(trip, rev)
        if event is None:
            return None
        self.journal.ensure_year(event.end_date.year, "system")
        entry = trip_entry(event, self._payee(trip))
        if entry is None or not self._closed(event.end_date):
            return entry
        booking = self.journal._booking_date(event.end_date)
        lines = tuple(dataclasses.replace(ln, income_date=booking if ln.income_date else None) for ln in entry.lines)
        return dataclasses.replace(entry, lines=lines)

    # ---------- journal access
    @staticmethod
    def _key(trip_id: str, rev: int) -> str:
        return f"trip:{trip_id}:r{rev}"

    def _live(self, trip_id: str, rev: int) -> Record | None:
        """The stored entry of revision ``rev`` unless it was reversed already."""
        if rev <= 0:
            return None
        key = self._key(trip_id, rev)
        entry = self.store.get_journal_entry_by_event_key(key)
        if entry is None or self.store.get_journal_entry_by_event_key(key + ":rev") is not None:
            return None
        return entry

    @staticmethod
    def _source(trip: Mapping[str, Any], user: Mapping[str, Any] | None, reversal: bool) -> JournalSource:
        return JournalSource(
            event="trip_reversal" if reversal else "trip",
            created_by=str((user or {}).get("id") or "system"),
            source_coll="trips",
            source_id=str(trip["id"]),
        )

    @staticmethod
    def _text(trip: Mapping[str, Any], reversal: bool = False) -> str:
        name = str(trip.get("title") or trip.get("purpose") or trip["id"])
        return f"{'Reversal: ' if reversal else ''}Trip {name}"[:200]

    def _reverse(self, trip: Mapping[str, Any], stored: Record, user: Mapping[str, Any] | None) -> None:
        """Post the reversal of a stored entry with the pure engine (409 when its booking date is locked)."""
        try:
            self.journal.check_reversible(stored)
        except ServiceError:
            raise ServiceError(409, LOCKED) from None
        original = Entry(
            lines=tuple(
                Line(
                    Role(str(ln["role"])),
                    Cents(int(ln["amount_cents"])),
                    income_date=_day(ln.get("income_date")),
                    open_item_id=ln.get("open_item_id"),
                )
                for ln in stored["lines"]
            ),
            booking_date=cast(date, _day(stored["booking_date"])),
            document_date=cast(date, _day(stored["document_date"])),
            event_key=str(stored["event_key"]),
        )
        first_open = first_unlocked_from(self.store.list("period_locks")) or date.min
        # booked into a later year because the document's own year was closed: income_date follows the booking date
        closed = self._closed(original.document_date)
        reversal = trip_reversal_entry(original, first_open_day=first_open, year_closed=closed)
        source = dataclasses.replace(self._source(trip, user, True), reverses_id=str(stored["id"]))
        self.journal.post(
            reversal.event_key,
            original.document_date,
            self._text(trip, True),
            source,
            [post_line(ln) for ln in reversal.lines],
        )

    # ---------- the hook
    def sync(self, saved: dict[str, Any], user: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Bring the journal in line with a saved trip (inside the caller's transaction); returns the record with
        its revision. 409 when an entry to be reversed lies in a locked month."""
        rev = _rev(saved)
        if accounting_start(self.store.settings()) is None and not rev:
            return saved
        live = self._live(str(saved["id"]), rev)
        new = self._entry(saved, rev + 1)
        if live is None and new is None:
            return saved
        # a final trip cannot be updated (TripService), so a live entry only meets ``new is None`` (reopen)
        # or ``new`` with ``live is None`` (finalise / refinalise): no "unchanged" case exists
        if live is not None:
            self._reverse(saved, live, user)
        if new is None:
            return saved
        self.journal.post(
            new.event_key,
            new.document_date,
            self._text(saved),
            self._source(saved, user, False),
            [post_line(ln) for ln in new.lines],
        )
        return dict(self.store.save("trips", {**saved, "post_rev": rev + 1}))
