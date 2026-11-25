"""Sondervorauszahlung (docs/specs/ustva.md "Sondervorauszahlung", accounting.md 9.6).

The **proposal** for year Y comes from the current (not replaced) final VAT returns of Y-1 through the pure
``ustva.special_prepayment``: ``(sum Kz 83 + Kz 39) * 12 / months / 11``, floored at 0. The **user value** and the
payment are kept on the year's ``fiscal_years`` row in its ``extra`` JSON (unknown fields survive a round-trip):
``special_prepayment`` (decimal string, e.g. ``"600.00"``), ``special_prepayment_paid_on`` (ISO date) and
``special_prepayment_entry`` (the id of the payment entry, key ``vat:<id>``, so a repeated PUT replays instead of
booking twice). Booked only through :class:`TaxBookingService`. No SQL here.
"""

import uuid
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from permita38.core.errors import ServiceError
from permita38.core.money import dec
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.tax_bookings import TaxBookingService
from permita38.core.settings import effective_settings
from permita38.core.vat import ustva
from permita38.core.vat.deadlines import ustva_due

__all__ = ["SpecialPrepaymentService", "stored_cents", "to_cents"]

KEY = "special_prepayment"
PAID_ON = "special_prepayment_paid_on"
ENTRY = "special_prepayment_entry"


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}{whole}.{frac:02d}"


def to_cents(value: Any) -> int:
    """A money value (float from the strict body, decimal string) as exact cents; more than 2 places is a 422."""
    scaled = dec(value, None) * 100
    if scaled != scaled.to_integral_value():
        raise ServiceError(422, "The amount has at most 2 decimal places")
    return int(scaled)


def stored_cents(fy: Mapping[str, Any]) -> int:
    """The user value of a fiscal year row in cents (0 when none was entered or the stored value is not a decimal)."""
    got = _stored(fy)
    return 0 if got is None else got


def _stored(fy: Mapping[str, Any]) -> int | None:
    raw = fy.get(KEY)
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
        if not value.is_finite():
            return None
        return int((value * 100).to_integral_value())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _applies(fy: Mapping[str, Any]) -> bool:
    """A Sondervorauszahlung exists for a monthly filer with Dauerfrist only."""
    return bool(fy.get("dauerfrist")) and fy.get("vat_frequency") == "month"


class SpecialPrepaymentService:
    def __init__(self, store: Repository, bookings: TaxBookingService):
        self.store = store
        self.bookings = bookings

    def _year(self, year: int) -> Record:
        rows = self.store.find("fiscal_years", year=year)
        if not rows:
            raise ServiceError(422, f"Accounting is not initialised for {year}")
        return rows[0]

    def _first_month(self, year: int) -> int | None:
        """The first month of ``year`` with a journal entry (not the opening balance) or a return record (draft or
        final), ``None`` when there is none."""
        found: list[int] = []
        for r in self.store.find("vat_returns", year=year):
            no = int(r.get("period_no") or 1)
            found.append(no if r.get("period_kind") == "month" else (no - 1) * 3 + 1)
        first = min(found) if found else 13
        for m in range(1, first):
            last = (date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).isoformat()
            entries = self.store.list_journal_entries(date_from=f"{year}-{m:02d}-01", date_to=last, limit=50)
            if any(e.get("event") != "opening_balance" for e in entries):
                return m
        return first if first <= 12 else None

    def _proposal(self, year: int) -> tuple[int, int | None, int, int]:
        """(proposal cents, months, sum Kz 83, sum Kz 39) from the current final returns of the previous year;
        amended returns replace the originals. ``months`` = months of the previous year from the first month with a
        journal entry or a return record up to December (at most 12)."""
        rows = [
            r
            for r in self.store.find("vat_returns", year=year - 1)
            if r.get("status") == "final" and not r.get("replaced_by")
        ]
        kz83 = sum(int(r.get("result_cents") or 0) for r in rows)
        kz39 = sum(int(r.get("special_prepayment_credit_cents") or 0) for r in rows)
        first = self._first_month(year - 1)
        if first is None:
            return 0, None, kz83, kz39
        months = min(12, 13 - first)
        return int(ustva.special_prepayment(kz83, kz39, months)), months, kz83, kz39

    def _payment(self, fy: Record) -> dict[str, Any] | None:
        entry_id = fy.get(ENTRY)
        if not entry_id:
            return None
        return {
            "entry_key": f"vat:{entry_id}",
            "paid_on": fy.get(PAID_ON),
            "amount": _money(stored_cents(fy)) if _stored(fy) is not None else None,
            "reversed": self.store.get_journal_entry_by_event_key(f"vat:{entry_id}:rev") is not None,
        }

    def get(self, year: int) -> dict[str, Any]:
        fy = self._year(year)
        proposal, months, kz83, kz39 = self._proposal(year) if _applies(fy) else (0, None, 0, 0)
        state = str(effective_settings(self.store.settings()).get("state") or "")
        return {
            "year": year,
            "dauerfrist": bool(fy.get("dauerfrist")),
            "proposal": _money(proposal),
            "months": months,
            "previous_kz83": _money(kz83),
            "previous_kz39": _money(kz39),
            "amount": _money(stored_cents(fy)) if _stored(fy) is not None else None,
            "due_date": ustva_due(year, "month", 1, False, state).isoformat(),
            "payment": self._payment(fy),
        }

    def put(self, year: int, amount: Any, paid_on: str | None, user: Mapping[str, Any]) -> dict[str, Any]:
        """Store the user value; with ``paid_on`` also book the payment (once: the entry id is kept)."""
        cents = to_cents(amount)
        if cents < 0:
            raise ServiceError(422, "The amount must not be negative")
        with self.store.transaction():
            fy = self._year(year)
            if fy.get("closed_at") is not None:
                raise ServiceError(409, f"Fiscal year {year} is closed")
            if not _applies(fy):
                raise ServiceError(422, f"{year} has no monthly VAT filing with Dauerfrist: no Sondervorauszahlung")
            entry_id = fy.get(ENTRY)
            live = bool(entry_id) and self.store.get_journal_entry_by_event_key(f"vat:{entry_id}:rev") is None
            if live and cents != stored_cents(fy):
                raise ServiceError(409, "The payment is booked: reverse it before changing the amount")
            changes: Record = {KEY: _money(cents)}
            if paid_on:
                new_id = str(entry_id) if live else str(uuid.uuid4())
                self.bookings.special_prepayment(year, date.fromisoformat(paid_on), cents, user, new_id)
                changes.update({ENTRY: new_id, PAID_ON: paid_on})
            self.store.save("fiscal_years", {**fy, **changes})
        return self.get(year)
