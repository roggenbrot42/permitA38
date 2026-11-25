"""Accounting side of the importer (docs/specs/accounting.md 10, 11, 16, 22 E19): the open-month check of an imported
invoice and the reversal of a posted entry when a batch is undone."""

from __future__ import annotations

from datetime import date
from typing import Any

from permita38.core.repository import Repository
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import first_unlocked_from
from permita38.core.settings import accounting_start

LOCKED_MESSAGE = "the date lies in a locked period; use a date in an open month"


def locked_problem(store: Repository, day: date) -> str | None:
    """Why an invoice dated ``day`` cannot be issued (a locked month or a closed year), else ``None``. Nothing is
    locked before the accounting start or without initialisation."""
    start = accounting_start(store.settings())
    if start is None or day < start:
        return None
    first_open = first_unlocked_from(store.list("period_locks"))
    years = store.find("fiscal_years", year=day.year)
    if (years and years[0].get("closed_at") is not None) or (first_open is not None and day < first_open):
        return LOCKED_MESSAGE
    return None


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def reverse_entry(store: Repository, journal: JournalService, event_key: str, user_id: str = "system") -> bool:
    """Post ``<event_key>:rev`` (every amount flipped, dates and tax fields kept) unless the entry does not exist or
    is reversed already. 409 when the original's booking date lies in a locked month. Returns whether it posted."""
    stored = store.get_journal_entry_by_event_key(event_key)
    if stored is None or store.get_journal_entry_by_event_key(event_key + ":rev") is not None:
        return False
    journal.check_reversible(stored)
    lines = []
    for ln in stored["lines"]:
        base = ln.get("tax_base_cents")
        lines.append(
            PostLine(
                amount_cents=-int(ln["amount_cents"]),
                role=ln.get("role"),
                account=None if ln.get("role") else ln.get("account"),
                tax_key=ln.get("tax_key"),
                tax_kind=ln.get("tax_kind"),
                tax_rate=ln.get("tax_rate"),
                tax_base_cents=None if base is None else -int(base),
                vat_date=_day(ln.get("vat_date")),
                income_date=_day(ln.get("income_date")),
                open_item_id=ln.get("open_item_id"),
                treatment=ln.get("treatment"),
            )
        )
    source = JournalSource(
        event="import_undo",
        created_by=user_id,
        source_coll=stored.get("source_coll"),
        source_id=stored.get("source_id"),
        source_number=stored.get("source_number"),
        reverses_id=str(stored["id"]),
        counterparty_id=stored.get("counterparty_id"),
        cp_vat_id=stored.get("cp_vat_id"),
        cp_country=stored.get("cp_country"),
    )
    document = _day(stored["document_date"])
    assert document is not None
    journal.post(event_key + ":rev", document, f"Reversal: {stored['text']}"[:200], source, lines)
    return True
