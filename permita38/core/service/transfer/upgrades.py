"""Upgrade functions for archives written with an older schema (migration id -> function(records_by_collection)).

A data migration (one that rewrites values, not only adds columns) needs an entry in ``UPGRADES``. A migration that
needs none is listed in ``NO_UPGRADE_NEEDED`` with the reason. tests/backend/test_data_import.py fails when a
migration after the first is in neither, so a new migration cannot be forgotten."""

from collections.abc import Callable

Records = dict[str, list[dict]]


def _roles(records: Records) -> None:
    """Migration 0006: the role ``member`` is called ``employee``."""
    for user in records.get("users", []):
        if user.get("role") == "member":
            user["role"] = "employee"


UPGRADES: dict[str, Callable[[Records], None]] = {
    "4d5e0131": _roles,  # 0006-roles
}
NO_UPGRADE_NEEDED: dict[str, str] = {
    "00000002": "0002-totp: adds columns that are never exported",
    "00000003": "0003-planning: adds tables and columns, existing values unchanged",
    "ab46b196": "0004-expense_vat_deductible: adds a column; absent means empty",
    "6c7ec897": "0005-numbering: adds tables; counters are seeded lazily from the documents",
    "c41e7d90": "0007-calendars: adds tables and columns",
    "d8a69cde": "0008-expense_lines: adds a column; absent means no lines",
    "9897dd11": "0009-trips: adds a table",
    "c8af1eca": "0010-api_tokens: adds tables that are never exported",
}


def upgrade(records: Records, archive_head: str, chain: list[str]) -> None:
    """Apply, in chain order, every upgrade for a migration that came after ``archive_head``."""
    if archive_head not in chain:
        return
    for mid in chain[chain.index(archive_head) + 1 :]:
        fn = UPGRADES.get(mid)
        if fn:
            fn(records)
