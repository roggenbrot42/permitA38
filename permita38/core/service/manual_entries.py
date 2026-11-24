"""Manual journal entries and their reversal (docs/specs/accounting.md 7, 9.8, 11, 17, 18).

A manual entry is free and balanced; ``JournalService`` checks balance, chart accounts, the tax-line rules and that no
``vat_date`` lies in a locked month (manual entries never drift). A reversal flips every line and is booked on
``max(date, first open day)`` by ``JournalService``. Only manual entries are reversed here: the automatic entries of
invoices, payments, credit notes, expenses and trips are corrected through their source document.
"""

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from permita38.core import models
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.service.journal import MAX_TEXT, JournalService, JournalSource, PostLine

EVENT = "manual_entry"
REVERSAL_EVENT = "manual_reversal"
REVERSAL_SUFFIX = ":rev"

# Templates named by accounting.md 9.8 (service data; no route in the contract yet). Lines are filled by the user.
TEMPLATES: tuple[Mapping[str, str], ...] = (
    {"key": "opening_balance", "label": "Opening balance"},
    {"key": "private_withdrawal", "label": "Private withdrawal"},
    {"key": "private_deposit", "label": "Private deposit"},
    {"key": "vat_payment", "label": "VAT payment"},
    {"key": "income_taxes", "label": "Income-related taxes (9.7)"},
    {"key": "depreciation", "label": "Depreciation (AfA, amount typed by the user)"},
    {"key": "bank_fees", "label": "Bank fees"},
)


def _cents(value: Any) -> int:
    return int((value * 100).to_integral_value())


def _date(value: str) -> date:
    return date.fromisoformat(value)


class ManualEntryService:
    def __init__(self, store: Repository, journal: JournalService):
        self.store = store
        self.journal = journal

    @staticmethod
    def templates() -> list[dict[str, str]]:
        return [dict(t) for t in TEMPLATES]

    def post(self, body: models.ManualEntryIn, user: Mapping[str, Any]) -> models.JournalEntry:
        lines = [
            PostLine(
                amount_cents=_cents(ln.amount),
                role=ln.role,
                account=ln.account,
                tax_key=ln.tax_key,
                tax_kind=ln.tax_kind,
                tax_rate=ln.tax_rate,
                tax_base_cents=None if ln.tax_base is None else _cents(ln.tax_base),
                vat_date=None if ln.vat_date is None else _date(ln.vat_date),
                income_date=None if ln.income_date is None else _date(ln.income_date),
            )
            for ln in body.lines
        ]
        source = JournalSource(event=EVENT, created_by=str(user["id"]), origin="manual")
        return self.journal.post(f"manual:{uuid.uuid4()}", _date(body.date), body.text, source, lines)

    def _load(self, entry_id: str) -> Record:
        row = self.store.get("journal_entries", entry_id)
        full = self.store.get_journal_entry_by_event_key(str(row["event_key"])) if row else None
        if full is None:
            raise ServiceError(404, "Not found")
        return full

    def reverse(
        self, entry_id: str, date_: str | None, text: str | None, user: Mapping[str, Any]
    ) -> models.JournalEntry:
        """Reverse a manual entry: 404 unknown, 409 not manual ("reverse through the source document"), already
        reversed, a reversal itself, or the original's booking date in a locked period."""
        with self.store.transaction():
            orig = self._load(entry_id)
            key = str(orig["event_key"])
            if orig.get("reverses_id") or key.endswith(REVERSAL_SUFFIX):
                raise ServiceError(409, "A reversal cannot be reversed")
            if orig.get("origin") != "manual":
                raise ServiceError(409, "Reverse through the source document")
            if self.store.get_journal_entry_by_event_key(key + REVERSAL_SUFFIX) is not None:
                raise ServiceError(409, "The entry is already reversed")
            self.journal.check_reversible(orig)
            day = _date(date_) if date_ else _date(str(orig["booking_date"]))
            booking = self.journal.booking_date(day)
            label = text if text else f"Reversal: {orig['text']}"
            lines = [self._flip(ln, orig, booking) for ln in sorted(orig["lines"], key=lambda r: int(r["line_no"]))]
            source = JournalSource(
                event=REVERSAL_EVENT, created_by=str(user["id"]), origin="manual", reverses_id=str(orig["id"])
            )
            return self.journal.post(key + REVERSAL_SUFFIX, day, label[:MAX_TEXT], source, lines)

    def _flip(self, ln: Mapping[str, Any], orig: Mapping[str, Any], booking: date) -> PostLine:
        income = ln.get("income_date")
        income_date: date | None = None
        if income:
            wanted = _date(str(income))
            rows = self.store.find("fiscal_years", year=int(orig["fiscal_year"]))
            closed = bool(rows) and rows[0].get("closed_at") is not None
            # the original income date while its year is open and the reversal stays in that year (spec 9.8)
            income_date = wanted if not closed and wanted.year == booking.year else booking
        base = ln.get("tax_base_cents")
        return PostLine(
            amount_cents=-int(ln["amount_cents"]),
            role=ln.get("role"),
            account=ln["account"],
            tax_key=ln.get("tax_key"),
            tax_kind=ln.get("tax_kind"),
            tax_rate=None if ln.get("tax_rate") is None else str(ln["tax_rate"]),
            tax_base_cents=None if base is None else -int(base),
            vat_date=None if not ln.get("vat_date") else _date(str(ln["vat_date"])),
            income_date=income_date,
            open_item_id=ln.get("open_item_id"),
            treatment=ln.get("treatment"),
        )
