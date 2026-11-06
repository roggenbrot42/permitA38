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


_PROJECT_REFS = ("offers", "contracts", "invoices")


def referenced_project_ids(records: Records) -> set[str]:
    """Ids of the projects that an offer, contract or invoice of ``records`` points to."""
    return {r["project_id"] for coll in _PROJECT_REFS for r in records.get(coll, []) if r.get("project_id")}


def normalise_planning_record(coll: str, rec: dict, referenced: set[str]) -> None:
    """Migration 0017 for one record (in place); ``referenced`` is ``referenced_project_ids``.

    Also used by the legacy JSON import (core/repository/importer.py), so the rules exist once."""
    if coll == "projects":
        if rec.get("kind") in (None, ""):
            customer = rec.get("customer_id")
            rec["kind"] = (
                "customer" if (customer is not None and customer != "") or rec.get("id") in referenced else "internal"
            )
        rec.pop("personal", None)
        rec.pop("member_ids", None)  # assignments decide who sees a project
        if rec.get("status") is None:
            rec["status"] = "active"
    elif coll in ("contracts", "allocations"):
        if rec.get("hours_per_week") is not None and rec.get("total_hours") is not None:
            rec["total_hours"] = None
        if rec.get("status") is None:
            rec["status"] = "confirmed" if coll == "contracts" else "active"


def _project_planning(records: Records) -> None:
    """Migration 0017: projects get a kind and lose ``personal`` and ``member_ids``; a record with both hour
    fields keeps only hours_per_week; a missing status becomes the default."""
    referenced = referenced_project_ids(records)
    for coll in ("projects", "contracts", "allocations"):
        for rec in records.get(coll, []):
            normalise_planning_record(coll, rec, referenced)


UPGRADES: dict[str, Callable[[Records], None]] = {
    "4d5e0131": _roles,  # 0006-roles
    "7ca84bdc": _project_planning,  # 0017-project_planning
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
    "72306069": "0011-allocations: adds a table; without rows every person is entirely unassigned",
    "c6d8d0be": "0012-suppliers: adds tables and nullable expense columns; absent means no supplier",
    "f12b52ff": "0013-trip_item_id: adds a nullable expense column; absent means an unplanned bill",
    "f3fcc516": "0014-receipt_scans: adds a table and nullable expense columns; absent paid_on means paid on the bill date",
    "2fe799c1": "0015-document_scans: adds a table; without rows no old document was scanned",
    "e4b70dba": "0016-guards: adds triggers and indexes only; values are unchanged (the restore checks duplicate numbers up front)",
}


def upgrade(records: Records, archive_head: str, chain: list[str]) -> None:
    """Apply, in chain order, every upgrade for a migration that came after ``archive_head``."""
    if archive_head not in chain:
        return
    for mid in chain[chain.index(archive_head) + 1 :]:
        fn = UPGRADES.get(mid)
        if fn:
            fn(records)
