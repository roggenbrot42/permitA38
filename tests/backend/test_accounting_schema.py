"""The accounting journal tables of migration accounting_journal (docs/specs/accounting.md sections 7 and 8.1) on a
migrated SQLite database, written with plain SQL: the rules are enforced by the database itself. Invented data."""

import sqlite3

import pytest

from permita38.core.repository.sqlite import SQLiteRepository

FISCAL_YEAR = {
    "id": "fy1", "year": 2026, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
    "dauerfrist": 0, "small_business": 0, "rules_version": "2026.1", "opened_at": 1700000000, "opened_by": "u1",
}  # fmt: skip
ENTRY = {
    "id": "e1", "fiscal_year": 2026, "entry_no": 1, "document_date": "2026-01-05", "booking_date": "2026-01-05",
    "event": "issue", "event_key": "issue:inv1", "text": "Invoice RE-1", "origin": "auto", "created_at": 1700000000,
    "created_by": "u1", "prev_hash": "0" * 64, "hash": "a" * 64,
}  # fmt: skip
LINE = {"id": "l1", "entry_id": "e1", "line_no": 1, "account": "1400", "amount_cents": 11900}
PAYMENT = {
    "id": "p1", "document_id": "inv1", "date": "2026-01-20", "amount_cents": 11900, "method": "bank",
    "kind": "payment", "created_at": 1700000000, "created_by": "u1",
}  # fmt: skip
LOCK = {"id": "k1", "year": 2026, "month": 1, "locked_at": 1700000000, "locked_by": "u1", "reason": "manual"}

ROWS = {
    "fiscal_years": FISCAL_YEAR,
    "journal_entries": ENTRY,
    "journal_lines": LINE,
    "payments": PAYMENT,
    "period_locks": LOCK,
}
APPEND_ONLY = ("journal_entries", "journal_lines", "payments", "period_locks")
REQUIRED = {
    "fiscal_years": ["year", "chart", "income_basis", "vat_basis", "vat_frequency", "dauerfrist", "small_business",
                     "rules_version", "opened_at", "opened_by"],
    "journal_entries": ["fiscal_year", "entry_no", "document_date", "booking_date", "event", "event_key", "text",
                        "origin", "created_at", "created_by", "prev_hash", "hash"],
    "journal_lines": ["entry_id", "line_no", "account", "amount_cents"],
    "payments": ["document_id", "date", "amount_cents", "method", "kind", "created_at", "created_by"],
    "period_locks": ["year", "month", "locked_at", "locked_by", "reason"],
}  # fmt: skip
REQUIRED_MSG = r"^permita38:state\.required$"
DANGLING_MSG = r"^permita38:ref\.dangling$"
IMPLIES_MSG = r"^permita38:state\.implies:"


@pytest.fixture
def db(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    repo.conn.execute("INSERT INTO invoices (id) VALUES ('inv1')")  # payments.document_id must exist
    yield repo.conn
    repo.close()


def insert(db, table, row):
    names = ", ".join(f'"{c}"' for c in row)
    db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({", ".join("?" * len(row))})', list(row.values()))


def seed(db):
    """One valid row of every table (the entry before its line, the invoice before its payment)."""
    for table in ROWS:
        insert(db, table, ROWS[table])


def refused(db, table, row, message):
    with pytest.raises(sqlite3.IntegrityError, match=message):
        insert(db, table, row)


def index_columns(db, name):
    return tuple(r[2] for r in db.execute(f'PRAGMA index_info("{name}")'))


def test_tables_and_indexes_exist(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(ROWS) <= tables
    have, unique = set(), set()
    for table in ROWS:
        for _, name, is_unique, *_ in db.execute(f'PRAGMA index_list("{table}")'):
            have.add((table, index_columns(db, name)))
            if is_unique:
                unique.add((table, index_columns(db, name)))
    assert {  # the "Indexes" line of section 8.1
        ("journal_lines", ("vat_date", "tax_key")),
        ("journal_lines", ("income_date",)),
        ("journal_lines", ("open_item_id",)),
        ("journal_entries", ("source_id",)),
        ("journal_entries", ("booking_date",)),
        ("payments", ("document_id",)),
    } <= have
    assert unique == {  # the unique constraints (the table-level ones; the primary keys are not listed as indexes)
        ("fiscal_years", ("year",)),
        ("journal_entries", ("event_key",)),
        ("journal_entries", ("fiscal_year", "entry_no")),
        ("journal_lines", ("entry_id", "line_no")),
        ("period_locks", ("year", "month")),
    } | {(t, ("id",)) for t in ROWS}


def test_valid_rows_insert(db):
    seed(db)
    for table in ROWS:
        assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == (1,)


@pytest.mark.parametrize("table,column", [(t, c) for t, cols in REQUIRED.items() for c in cols])
def test_a_missing_required_field_is_refused(db, table, column):
    seed(db)
    row = {**ROWS[table], "id": "new"}
    if table == "journal_entries":
        row.update(event_key="other", entry_no=2)  # no unique clash: the required guard is the only reason
    if table == "journal_lines":
        row["line_no"] = 2
    if table == "period_locks":
        row["month"] = 2
    if table == "fiscal_years":
        row["year"] = 2027
    del row[column]
    refused(db, table, row, REQUIRED_MSG)
    refused(db, table, {**row, column: None}, REQUIRED_MSG)


@pytest.mark.parametrize(
    "table,column,bad",
    [
        ("fiscal_years", "chart", "skr05"),
        ("fiscal_years", "income_basis", "cash"),
        ("fiscal_years", "vat_basis", "mixed"),
        ("fiscal_years", "vat_frequency", "year"),
        ("journal_entries", "origin", "robot"),
        ("journal_lines", "tax_kind", "other"),
        ("payments", "method", "card"),
        ("payments", "kind", "gift"),
    ],
)
def test_an_invalid_enum_value_is_refused(db, table, column, bad):
    seed(db)
    row = {**ROWS[table], "id": "new", column: bad}
    if table == "journal_lines":
        row.update(line_no=2, tax_key="NONE")  # a kind needs a key, so the vocabulary is the only violation
    refused(db, table, row, r"^permita38:vocab\.unknown$")


def test_period_lock_month_range_and_date_format(db):
    seed(db)
    for month in (0, 13):
        refused(db, "period_locks", {**LOCK, "id": f"m{month}", "month": month}, r"^permita38:range\.bounds$")
    refused(db, "journal_entries", {**ENTRY, "id": "x", "document_date": "05.01.2026"}, r"^permita38:format\.date$")
    refused(db, "payments", {**PAYMENT, "id": "x", "date": "2026-13-45"}, r"^permita38:format\.date$")


def test_unique_event_key_entry_number_line_number_year_and_lock(db):
    seed(db)
    msg = "UNIQUE constraint failed"
    refused(db, "journal_entries", {**ENTRY, "id": "e2", "entry_no": 2}, msg)  # same event_key
    refused(db, "journal_entries", {**ENTRY, "id": "e2", "event_key": "issue:inv2"}, msg)  # same (year, entry_no)
    refused(db, "journal_lines", {**LINE, "id": "l2"}, msg)  # same (entry, line_no)
    refused(db, "fiscal_years", {**FISCAL_YEAR, "id": "fy2"}, msg)
    refused(db, "period_locks", {**LOCK, "id": "k2"}, msg)
    # a new year restarts the entry numbers; another line number and another month are fine
    insert(db, "journal_entries", {**ENTRY, "id": "e3", "fiscal_year": 2027, "event_key": "issue:inv3"})
    insert(db, "journal_lines", {**LINE, "id": "l3", "line_no": 2})
    insert(db, "period_locks", {**LOCK, "id": "k3", "month": 2})


def test_vat_implications(db):
    seed(db)
    line = {**LINE, "id": "x", "line_no": 2}
    refused(db, "journal_lines", {**line, "tax_kind": "tax"}, IMPLIES_MSG)  # a kind needs a key
    for key in ("S19", "S7", "SX", "KU", "EU_SVC", "VST", "RC_EU_SVC", "RC_3C_SVC"):  # every key but NONE needs a date
        refused(db, "journal_lines", {**line, "tax_key": key, "tax_kind": "tax"}, IMPLIES_MSG)
    insert(db, "journal_lines", {**line, "id": "ok1", "tax_key": "NONE", "tax_kind": "base_only"})
    insert(db, "journal_lines", {**line, "id": "ok2", "line_no": 3, "tax_key": "S19", "tax_kind": "tax",
                                 "vat_date": "2026-01-05"})  # fmt: skip


def test_references_reversals_and_closing(db):
    seed(db)
    refused(db, "journal_lines", {**LINE, "id": "x", "line_no": 2, "entry_id": "missing"}, DANGLING_MSG)
    refused(db, "journal_entries", {**ENTRY, "id": "x", "event_key": "k", "entry_no": 2, "reverses_id": "nope"},
            DANGLING_MSG)  # fmt: skip
    refused(db, "payments", {**PAYMENT, "id": "x", "document_id": "missing"}, DANGLING_MSG)
    refused(db, "payments", {**PAYMENT, "id": "x", "kind": "reversal"}, IMPLIES_MSG)  # a reversal names its payment
    insert(db, "payments", {**PAYMENT, "id": "p2", "kind": "reversal", "reverses_id": "p1", "amount_cents": -11900})
    insert(db, "fiscal_years", {**FISCAL_YEAR, "id": "fy2", "year": 2027, "closed_at": 1, "closed_by": "u1"})
    refused(db, "fiscal_years", {**FISCAL_YEAR, "id": "fy3", "year": 2028, "closed_at": 1}, IMPLIES_MSG)


def test_a_payment_amount_of_zero_is_refused_by_the_table_check(db):
    seed(db)
    with pytest.raises(sqlite3.IntegrityError, match="payments_amount_nonzero"):
        insert(db, "payments", {**PAYMENT, "id": "p0", "amount_cents": 0})
    insert(db, "payments", {**PAYMENT, "id": "pm", "amount_cents": -500, "kind": "refund"})


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_update_and_delete_are_refused_but_insert_works(db, table):
    seed(db)
    row = ROWS[table]
    column = next(c for c in row if c != "id")
    lone = row["id"]
    if table == "journal_entries":  # an entry with lines is also protected by the reference guard: use one without
        insert(db, table, {**ENTRY, "id": "lone", "entry_no": 2, "event_key": "issue:lone"})
        lone = "lone"
    statements = (
        (f'UPDATE "{table}" SET "{column}" = "{column}"', []),  # even an update that changes nothing
        (f'UPDATE "{table}" SET "{column}" = ? WHERE "id" = ?', [row[column], row["id"]]),
        (f'DELETE FROM "{table}" WHERE "id" = ?', [lone]),
    )
    for sql, args in statements:
        with pytest.raises(sqlite3.IntegrityError, match=r"^permita38:state\.append_only$"):
            db.execute(sql, args)
    if table != "journal_entries":
        with pytest.raises(sqlite3.IntegrityError, match=r"^permita38:state\.append_only$"):
            db.execute(f'DELETE FROM "{table}"')
    assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == (2 if lone == "lone" else 1,)


def test_fiscal_years_are_not_append_only(db):
    seed(db)  # closing a year updates its row
    db.execute("UPDATE fiscal_years SET closed_at = 5, closed_by = 'u1' WHERE id = 'fy1'")
    assert db.execute("SELECT closed_at FROM fiscal_years").fetchone() == (5,)


def test_an_invoice_with_payments_and_an_entry_with_lines_cannot_be_deleted(db):
    seed(db)
    for table, pk in (("invoices", "inv1"), ("journal_entries", "e1")):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f'DELETE FROM "{table}" WHERE "id" = ?', [pk])
