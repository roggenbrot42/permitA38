"""The columns and tables of migration accounting_documents (docs/specs/accounting.md 8.1 and 8.2, ustva.md "Data
model", zm.md "Records, status, drift") on a migrated SQLite database, written with plain SQL: the rules are enforced
by the database itself. Invented data."""

import sqlite3

import pytest

from permita38.core.repository import INTERNAL_COLLECTIONS
from permita38.core.repository.sqlite import SQLiteRepository

VAT_RETURN = {
    "id": "v1", "year": 2026, "period_kind": "month", "period_no": 1, "from_date": "2026-01-01",
    "to_date": "2026-01-31", "due_date": "2026-02-10", "status": "draft", "amends_key": "",
}  # fmt: skip
ZM_REPORT = {"id": "z1", "year": 2026, "quarter": 1, "status": "draft", "due_date": "2026-04-25", "amends_key": ""}
ROWS = {"vat_returns": VAT_RETURN, "zm_reports": ZM_REPORT}
REQUIRED = {
    "vat_returns": ["year", "period_kind", "period_no", "from_date", "to_date", "due_date", "status"],
    "zm_reports": ["year", "quarter", "status", "due_date"],
}
REQUIRED_MSG = r"^permita38:state\.required$"
VOCAB_MSG = r"^permita38:vocab\.unknown$"
DANGLING_MSG = r"^permita38:ref\.dangling$"
RANGE_MSG = r"^permita38:range\.bounds$"
DATE_MSG = r"^permita38:format\.date$"


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    yield r
    r.close()


@pytest.fixture
def db(repo):
    return repo.conn


def insert(db, table, row):
    names = ", ".join(f'"{c}"' for c in row)
    db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({", ".join("?" * len(row))})', list(row.values()))


def refused(db, table, row, message=None):
    with pytest.raises(sqlite3.IntegrityError, match=message):
        insert(db, table, row)


def columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}


def test_new_columns_and_tables_exist(db):
    assert {"kind", "credit_kind", "corrects_id", "tax_treatment", "issued"} <= columns(db, "invoices")
    assert {"tax_treatment", "service_date", "bill_rev", "pay_rev"} <= columns(db, "expenses")
    assert "post_rev" in columns(db, "trips")
    assert "kind" not in columns(db, "offers")  # offers are unchanged
    for table in ROWS:
        assert columns(db, table)
        assert table in INTERNAL_COLLECTIONS


def test_old_rows_without_the_new_columns_still_load_and_save(repo):
    db = repo.conn
    db.execute("INSERT INTO invoices (id, number, status, date) VALUES ('inv001', 'RE-1', 'sent', '2026-01-05')")
    db.execute("INSERT INTO expenses (id, date, net, vat) VALUES ('exp001', '2026-01-06', 10.0, 1.9)")
    db.execute("INSERT INTO trips (id, title) VALUES ('trip01', 'Berlin')")
    db.commit()
    for coll, rid in (("invoices", "inv001"), ("expenses", "exp001"), ("trips", "trip01")):
        row = repo.get(coll, rid)
        assert row is not None
        for new in ("kind", "tax_treatment", "service_date", "bill_rev", "pay_rev", "post_rev", "issued"):
            assert row.get(new) is None  # NULL reads as the default of the spec, nothing was backfilled
        row["note_for_test"] = "kept"  # an unknown field survives (extra JSON)
        saved = repo.save(coll, row)
        assert repo.get(coll, rid)["note_for_test"] == "kept"
        assert saved["id"] == rid


def test_new_rows_with_the_new_columns_round_trip(repo):
    repo.save("invoices", {"id": "inv002", "kind": "credit_note", "credit_kind": "storno", "tax_treatment": "standard",
                           "issued": {"total": "1.00"}})  # fmt: skip
    repo.save("invoices", {"id": "inv003", "corrects_id": "inv002"})
    repo.save("expenses", {"id": "exp002", "tax_treatment": "reverse_charge_eu_service", "service_date": "2026-01-31",
                           "bill_rev": 1, "pay_rev": 0})  # fmt: skip
    repo.save("trips", {"id": "trip02", "post_rev": 2})
    assert repo.get("invoices", "inv002")["issued"] == {"total": "1.00"}
    assert repo.get("invoices", "inv003")["corrects_id"] == "inv002"
    assert repo.get("expenses", "exp002")["pay_rev"] == 0
    assert repo.get("trips", "trip02")["post_rev"] == 2


@pytest.mark.parametrize(
    "table,column,good,bad",
    [
        ("invoices", "kind", ["invoice", "credit_note"], ["quote", "Invoice"]),
        (
            "invoices",
            "credit_kind",
            ["storno", "partial", "discount", "write_off", "write_off_reversal"],
            ["refund", "credit_note"],
        ),
        (
            "invoices",
            "tax_treatment",
            ["standard", "small_business", "eu_b2b_service"],
            ["no_input_vat", "reverse_charge_domestic", "export_third_country", "reverse_charge_eu_service"],
        ),
        (
            "expenses",
            "tax_treatment",
            ["standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country"],
            ["small_business", "eu_b2b_service", "reverse_charge"],
        ),
        ("vat_returns", "period_kind", ["month", "quarter"], ["year", "week"]),
        ("vat_returns", "status", ["draft", "final"], ["filed", "open"]),
        ("vat_returns", "basis", ["soll", "ist"], ["cash"]),
        ("vat_returns", "frequency", ["month", "quarter", "none"], ["year"]),
        ("zm_reports", "status", ["draft", "final"], ["filed"]),
    ],
)
def test_enum_columns_accept_their_vocabulary_and_refuse_the_rest(db, table, column, good, bad):
    for n, value in enumerate(good):
        row = {**ROWS.get(table, {}), "id": f"ok{n}", column: value}
        if table == "vat_returns":
            row["period_no"] = n + 1  # the period is unique
        if table == "zm_reports":
            row["quarter"] = n + 1
        insert(db, table, row)
    for n, value in enumerate(bad):
        refused(db, table, {**ROWS.get(table, {}), "id": f"bad{n}", column: value}, VOCAB_MSG)


@pytest.mark.parametrize("table,column", [(t, c) for t, cols in REQUIRED.items() for c in cols])
def test_a_missing_required_field_is_refused(db, table, column):
    row = {**ROWS[table], "id": "new"}
    del row[column]
    refused(db, table, row, REQUIRED_MSG)
    refused(db, table, {**row, column: None}, REQUIRED_MSG)
    if (
        column not in ("year", "period_no", "quarter") and "date" not in column
    ):  # numbers and dates: their own guard answers first for text
        refused(db, table, {**row, column: "  "}, REQUIRED_MSG)


@pytest.mark.parametrize(
    "table,column",
    [
        ("vat_returns", "from_date"),
        ("vat_returns", "to_date"),
        ("vat_returns", "due_date"),
        ("vat_returns", "filed_on"),
        ("zm_reports", "due_date"),
        ("zm_reports", "filed_on"),
        ("expenses", "service_date"),
    ],
)
def test_date_columns_check_the_format(db, table, column):
    base = ROWS.get(table, {})
    refused(db, table, {**base, "id": "bad", column: "05.01.2026"}, DATE_MSG)
    insert(db, table, {**base, "id": "good", column: "2026-01-05"})


@pytest.mark.parametrize("table,column", [("expenses", "bill_rev"), ("expenses", "pay_rev"), ("trips", "post_rev")])
def test_revision_counters_are_not_negative(db, table, column):
    refused(db, table, {"id": "neg", column: -1}, RANGE_MSG)
    insert(db, table, {"id": "zero", column: 0})
    insert(db, table, {"id": "pos", column: 7})


def test_period_ranges(db):
    refused(db, "vat_returns", {**VAT_RETURN, "id": "a", "period_no": 0}, RANGE_MSG)
    refused(db, "vat_returns", {**VAT_RETURN, "id": "b", "period_no": 13}, RANGE_MSG)
    refused(db, "vat_returns", {**VAT_RETURN, "id": "c", "year": 1999}, RANGE_MSG)
    refused(db, "zm_reports", {**ZM_REPORT, "id": "d", "quarter": 0}, RANGE_MSG)
    refused(db, "zm_reports", {**ZM_REPORT, "id": "e", "quarter": 5}, RANGE_MSG)


def test_corrects_id_must_point_at_an_invoice(db):
    refused(db, "invoices", {"id": "c1", "corrects_id": "nope"}, DANGLING_MSG)
    insert(db, "invoices", {"id": "inv"})
    insert(db, "invoices", {"id": "c1", "kind": "credit_note", "credit_kind": "storno", "corrects_id": "inv"})


@pytest.mark.parametrize("table", ["vat_returns", "zm_reports"])
@pytest.mark.parametrize("column", ["amends_id", "replaced_by"])
def test_amendment_references_point_at_the_same_table(db, table, column):
    insert(db, table, ROWS[table])
    other = {**ROWS[table], "id": "n1"}
    other["period_no" if table == "vat_returns" else "quarter"] = 2
    refused(db, table, {**other, column: "nope", "amends_key": "nope" if column == "amends_id" else ""}, DANGLING_MSG)
    ok = {**other, "id": "n2", column: ROWS[table]["id"]}
    if column == "amends_id":
        ok["amends_key"] = ROWS[table]["id"]
    insert(db, table, ok)


@pytest.mark.parametrize("table", ["vat_returns", "zm_reports"])
def test_amends_key_must_equal_amends_id_or_the_empty_string(db, table):
    insert(db, table, ROWS[table])
    amended = {**ROWS[table], "id": "a1", "amends_id": ROWS[table]["id"]}
    refused(db, table, {**amended, "amends_key": ""}, "CHECK")  # amends_id set, key empty
    refused(db, table, {**amended, "amends_key": "other"}, "CHECK")
    refused(db, table, {**amended, "amends_key": None}, "CHECK")  # NOT NULL through the CHECK
    refused(db, table, {**ROWS[table], "id": "a2", "year": 2027, "amends_key": "x"}, "CHECK")  # key set, no amends_id
    refused(db, table, {**ROWS[table], "id": "a3", "year": 2027, "amends_key": None}, "CHECK")
    insert(db, table, {**amended, "amends_key": ROWS[table]["id"]})


def test_a_second_vat_return_for_the_same_period_is_refused_unless_it_amends(db):
    insert(db, "vat_returns", VAT_RETURN)
    refused(db, "vat_returns", {**VAT_RETURN, "id": "v2"}, "UNIQUE")  # same period, not amended
    insert(db, "vat_returns", {**VAT_RETURN, "id": "v3", "period_no": 2, "from_date": "2026-02-01"})  # other period
    insert(db, "vat_returns", {**VAT_RETURN, "id": "v4", "period_kind": "quarter"})  # other kind
    amended = {**VAT_RETURN, "id": "v5", "amends_id": "v1", "amends_key": "v1"}
    insert(db, "vat_returns", amended)
    refused(db, "vat_returns", {**amended, "id": "v6"}, "UNIQUE")  # a second amendment of the same return
    insert(db, "vat_returns", {**VAT_RETURN, "id": "v7", "amends_id": "v5", "amends_key": "v5"})  # amends the amendment


def test_a_second_zm_report_for_the_same_quarter_is_refused_unless_it_amends(db):
    insert(db, "zm_reports", ZM_REPORT)
    refused(db, "zm_reports", {**ZM_REPORT, "id": "z2"}, "UNIQUE")
    insert(db, "zm_reports", {**ZM_REPORT, "id": "z3", "quarter": 2})
    insert(db, "zm_reports", {**ZM_REPORT, "id": "z4", "amends_id": "z1", "amends_key": "z1"})
    refused(db, "zm_reports", {**ZM_REPORT, "id": "z5", "amends_id": "z1", "amends_key": "z1"}, "UNIQUE")


def test_unique_indexes_have_the_spec_columns(db):
    found = {}
    for table in ROWS:
        for _, name, unique, *_ in db.execute(f'PRAGMA index_list("{table}")'):
            if unique and not name.startswith("sqlite_autoindex"):
                found[table] = tuple(r[2] for r in db.execute(f'PRAGMA index_info("{name}")'))
    assert found == {
        "vat_returns": ("year", "period_kind", "period_no", "amends_key"),
        "zm_reports": ("year", "quarter", "amends_key"),
    }
