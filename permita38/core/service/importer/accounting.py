"""Accounting side of the importer (docs/specs/accounting.md 10, 11, 16, 22 E19): the open-month check of an imported
invoice."""

from __future__ import annotations

from datetime import date

from permita38.core.repository import Repository
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
