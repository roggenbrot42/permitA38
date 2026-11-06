"""The database guards (docs/specs/db-constraints.md): triggers of a migrated SQLite database, their behaviour on
direct `repo.save` calls and the grandfathering of rows written before the migration. All data is invented."""

import re
import sqlite3

import pytest

from permita38.core import database
from permita38.core.repository import TABLES, ConstraintViolation
from permita38.core.repository.dialect import DIALECTS, ROW_LOCAL_KINDS, SQLiteDialect
from permita38.core.repository.schema import CHECKS, GUARD_KINDS, GUARDS
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import integrity

from .test_guards_v2_kinds import CASES as V2_CASES
from .test_migrations import CHAIN, DIALECT_NAMES, HEAD, real_chain_copy, use_chain


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    yield r
    r.close()


def refused(code, fn, *args):
    with pytest.raises(ConstraintViolation) as e:
        fn(*args)
    assert e.value.code == code
    assert isinstance(e.value.__cause__, sqlite3.IntegrityError)
    assert str(e.value.__cause__) == f"permita38:{code}"


# ------------------------------------------------------------------ (a) schema description
def test_guard_declarations_are_well_formed():
    assert set(ROW_LOCAL_KINDS) <= set(GUARD_KINDS)
    used = {g.kind for g in GUARDS}
    assert used <= set(GUARD_KINDS)  # no unknown kind
    # no dead kind: used by a real guard, or a row-local kind (exercised by test_guards_v2_kinds.py)
    assert set(GUARD_KINDS) <= used | ROW_LOCAL_KINDS
    # every row-local kind is covered by the check-db rule (it raises for a kind it does not know)
    samples = {case[0].kind: case[0] for case in V2_CASES.values()}
    for kind in ROW_LOCAL_KINDS:
        assert integrity.guard_violated(samples[kind], {}) in (True, False), kind
    assert len({g.name for g in GUARDS}) == len(GUARDS)
    for g in GUARDS:
        assert g.table in TABLES
        for c in g.columns:
            assert TABLES[g.table].column(c), (g.name, c)
        assert len(g.columns) == (2 if g.kind == "order" else 1)
    assert {g.code for g in GUARDS} == {
        "numbering.duplicate",
        "format.date",
        "range.negative",
        "state.period_reversed",
        "ref.dangling",
    }
    for g in GUARDS:
        if g.kind == "reference":
            assert g.target in TABLES and len(g.columns) == 1, g.name
        else:
            assert g.target is None, g.name
    # trips start/end are local datetimes: only the order guard (plus the project reference)
    assert [g.kind for g in GUARDS if g.table == "trips"] == ["order", "reference"]
    assert CHECKS["allocations"][0] == "allocations_one_owner"


def test_migrated_triggers_equal_guard_sql(repo):
    d = SQLiteDialect()
    want = {}
    for g in GUARDS:
        stmts = d.guard_sql(g)
        suffixes = ("_ins", "_upd", "_del") if g.kind == "reference" else ("_ins", "_upd")
        assert len(stmts) == len(suffixes), g.name  # reference guards: three triggers, all others: two
        for suffix, stmt in zip(suffixes, stmts):
            want[g.name + suffix] = norm(stmt)
    got = {
        name: norm(sql) for name, sql in repo.conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
    }
    assert got == want


def test_sqlite_migration_file_is_exactly_the_generated_statements(tmp_path):
    d = SQLiteDialect()
    v1 = next(m for m in CHAIN if m.name == "guards").sql()
    refs = next(m for m in CHAIN if m.name == "reference_guards").sql()
    v1_statements = [s for g in GUARDS if g.kind != "reference" for s in d.guard_sql(g)]
    ref_statements = [s for g in GUARDS if g.kind == "reference" for s in d.guard_sql(g)]
    assert ref_statements
    # every guard's SQL is in exactly one migration file: v1 guards in 0016, reference guards in 0017
    for s in v1_statements:
        assert s in v1 and s not in refs
    for s in ref_statements:
        assert s in refs and s not in v1
    assert v1.count("CREATE TRIGGER") == len(v1_statements)
    assert refs.count("CREATE TRIGGER") == len(ref_statements)


@pytest.mark.parametrize("dialect", ["postgresql", "mariadb"])
def test_native_dialects_cover_every_guard(dialect):
    d = DIALECTS[dialect]
    migrations = database.list_migrations(dialect)
    sql = next(m for m in migrations if m.name == "guards").sql()
    ref_sql = next(m for m in migrations if m.name == "reference_guards").sql()
    for g in GUARDS:
        want = ref_sql if g.kind == "reference" else sql
        other = sql if g.kind == "reference" else ref_sql
        for s in d.guard_sql(g):
            assert s in want and s not in other  # exactly one migration file
    assert "CHECK" in sql and "UNIQUE INDEX" in sql


def test_every_dialect_folder_has_the_guards_migration():
    for d in DIALECT_NAMES:
        assert any(m.name == "guards" for m in database.list_migrations(d)), d
    database.validate_dialects()


# ------------------------------------------------------------------ (b) indexes
def test_number_indexes_exist(repo):
    names = {r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_invoices_number", "idx_offers_number"} <= names
    for t in ("invoices", "offers"):
        assert any(i.name == f"idx_{t}_number" and not i.unique for i in TABLES[t].indexes)


# ------------------------------------------------------------------ (c) behaviour of direct saves
@pytest.mark.parametrize("table", ["invoices", "offers"])
def test_duplicate_number_is_refused_and_empty_number_never(repo, table):
    a = repo.save(table, {"number": "RE-2026-0001"})
    refused("numbering.duplicate", repo.save, table, {"number": "RE-2026-0001"})
    assert len(repo.list(table)) == 1  # the refused row was not written
    ok = repo.save(table, {"number": "RE-2026-0002"})
    assert ok["number"] == "RE-2026-0002"
    # re-saving the same row and another field change is fine
    repo.save(table, {**a, "title": "changed"})
    # many rows with an empty or missing number
    repo.save(table, {"number": ""})
    repo.save(table, {"number": ""})
    repo.save(table, {"title": "draft one"})
    repo.save(table, {"title": "draft two"})
    # renumbering into an existing number is refused
    refused("numbering.duplicate", repo.save, table, {**ok, "number": "RE-2026-0001"})
    assert repo.get(table, ok["id"])["number"] == "RE-2026-0002"


def test_invoice_and_offer_numbers_are_independent(repo):
    repo.save("invoices", {"number": "RE-2026-0001"})
    repo.save("offers", {"number": "RE-2026-0001"})  # cross-table uniqueness is numbering.py's job


DATE_CASES = [
    ("entries", "date"),
    ("invoices", "date"),
    ("invoices", "due_date"),
    ("invoices", "valid_until"),
    ("invoices", "paid_date"),
    ("invoices", "service_from"),
    ("invoices", "service_to"),
    ("offers", "valid_until"),
    ("expenses", "date"),
    ("expenses", "paid_on"),
    ("absences", "start"),
    ("contracts", "end"),
    ("projects", "start"),
    ("allocations", "cancelled_from"),
    ("incoming_offers", "received_on"),
    ("incoming_offers", "valid_until"),
]


def _owner(repo, table):
    """The owner columns a row of `table` needs: an allocation points at an existing (invented) user."""
    if table == "allocations":
        return {"user_id": repo.save("users", {"name": "inv001"})["id"]}
    return {}


@pytest.mark.parametrize(("table", "col"), DATE_CASES)
def test_date_guard_refuses_a_bad_format_and_accepts_iso(repo, table, col):
    refused("format.date", repo.save, table, {**_owner(repo, table), col: "2026-1-5"})
    refused("format.date", repo.save, table, {**_owner(repo, table), col: "05.01.2026"})
    ok = repo.save(table, {**_owner(repo, table), col: "2026-01-05"})
    assert ok[col] == "2026-01-05"
    refused("format.date", repo.save, table, {**ok, col: "tomorrow"})
    assert repo.get(table, ok["id"])[col] == "2026-01-05"
    repo.save(table, {**_owner(repo, table), col: None})  # not set
    repo.save(table, {**_owner(repo, table)})


@pytest.mark.parametrize(("table", "col"), DATE_CASES)
def test_date_and_order_guards_accept_empty_string_date(repo, table, col):
    ok = repo.save(table, {**_owner(repo, table), col: ""})
    assert ok[col] == ""


def test_date_guard_covers_every_declared_column():
    guarded = {(g.table, g.columns[0]) for g in GUARDS if g.kind == "date"}
    assert set(DATE_CASES) <= guarded
    assert ("trips", "start") not in guarded and ("entries", "start") not in guarded


NEGATIVE_CASES = [
    ("entries", "minutes"),
    ("entries", "rate"),
    ("customers", "rate"),
    ("projects", "rate"),
    ("projects", "budget_hours"),
    ("tasks", "rate"),
    ("contracts", "rate"),
    ("allocations", "rate"),
    ("contracts", "hours_per_week"),
    ("contracts", "total_hours"),
    ("allocations", "hours_per_week"),
    ("allocations", "total_hours"),
    ("suppliers", "rate"),
    ("expenses", "net"),
    ("expenses", "vat"),
]


@pytest.mark.parametrize(("table", "col"), NEGATIVE_CASES)
def test_negative_guard_refuses_below_zero_and_accepts_zero(repo, table, col):
    refused("range.negative", repo.save, table, {**_owner(repo, table), col: -1})
    ok = repo.save(table, {**_owner(repo, table), col: 0})
    assert ok[col] == 0
    repo.save(table, {**_owner(repo, table), col: 1})
    refused("range.negative", repo.save, table, {**ok, col: -2})
    repo.save(table, {**_owner(repo, table)})  # not set


def test_negative_guard_covers_every_declared_column():
    guarded = {(g.table, g.columns[0]) for g in GUARDS if g.kind == "non_negative"}
    assert set(NEGATIVE_CASES) == guarded


ORDER_CASES = [
    ("absences", "start", "end", "2026-03-10", "2026-03-09"),
    ("contracts", "start", "end", "2026-03-10", "2026-03-09"),
    ("projects", "start", "end", "2026-03-10", "2026-03-09"),
    ("allocations", "start", "end", "2026-03-10", "2026-03-09"),
    ("trips", "start", "end", "2026-03-10T09:00", "2026-03-10T08:59"),
    ("invoices", "service_from", "service_to", "2026-03-10", "2026-03-09"),
    ("offers", "service_from", "service_to", "2026-03-10", "2026-03-09"),
]


@pytest.mark.parametrize(("table", "a", "b", "late", "early"), ORDER_CASES)
def test_period_guard_refuses_a_reversed_period_and_accepts_equal_ends(repo, table, a, b, late, early):
    o = _owner(repo, table)
    refused("state.period_reversed", repo.save, table, {**o, a: late, b: early})
    ok = repo.save(table, {**o, a: early, b: late})
    assert (ok[a], ok[b]) == (early, late)
    same = repo.save(table, {**o, a: late, b: late})  # start == end
    assert (same[a], same[b]) == (late, late)
    refused("state.period_reversed", repo.save, table, {**ok, b: "2000-01-01"[: len(early)]})
    refused("state.period_reversed", repo.save, table, {**ok, a: "9999-01-01"[: len(early)]})
    repo.save(table, {**o, a: late})  # an open end
    repo.save(table, {**o, b: early})  # an open start


@pytest.mark.parametrize(("table", "a", "b", "late", "early"), ORDER_CASES)
def test_date_and_order_guards_accept_empty_string(repo, table, a, b, late, early):
    o = _owner(repo, table)
    for first, second in (("", late), (late, ""), ("", early)):
        ok = repo.save(table, {**o, a: first, b: second})
        assert (ok[a], ok[b]) == (first, second)


def test_negative_fraction_is_refused(repo):
    refused("range.negative", repo.save, "customers", {"rate": -0.5})


def test_entries_clock_times_may_run_over_midnight(repo):
    repo.save("entries", {"date": "2026-03-10", "minutes": 120, "start": "23:00", "end": "01:00"})


def test_negative_document_totals_are_not_guarded(repo):
    repo.save("invoices", {"number": "RE-2026-0001", "total": -50, "lines": [{"price": -5}]})


# ------------------------------------------------------------------ (d) grandfathering
@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database at the previous head (0016 removed from a copy of the real chain) holding violations."""
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        # 0017 requires 0016: remove every migration after 0015
        for later in (root / d).glob("*.sql"):
            if int(later.name[:4]) > 15:
                later.unlink()
    data = tmp_path / "data"
    with monkeypatch.context() as m:
        use_chain(m, root)
        old = SQLiteRepository(data)
        old.startup()
        assert old.get_revision() != HEAD
        assert old.conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0] == 0
        planted = {
            "inv_a": old.save("invoices", {"number": "RE-2026-0001", "title": "inv001 first"}),
            "inv_b": old.save("invoices", {"number": "RE-2026-0001", "title": "inv001 second"}),
            "absence": old.save("absences", {"user_id": "inv001", "start": "2026-03-10", "end": "2026-03-01"}),
            "bad_date": old.save("entries", {"date": "5.3.2026", "minutes": -5}),
        }
        old.close()
    return data, planted


def test_migration_keeps_old_violations_and_they_stay_writable(old_db):
    data, planted = old_db
    repo = SQLiteRepository(data)
    repo.startup()  # must not fail on the duplicate or the reversed period
    try:
        assert repo.get_revision() == HEAD
        for table, key in (
            ("invoices", "inv_a"),
            ("invoices", "inv_b"),
            ("absences", "absence"),
            ("entries", "bad_date"),
        ):
            assert repo.get(table, planted[key]["id"]) == planted[key]
        a, b, ab, e = planted["inv_a"], planted["inv_b"], planted["absence"], planted["bad_date"]
        # unchanged re-save and a change of another field
        repo.save("invoices", a)
        repo.save("invoices", {**a, "title": "inv001 renamed"})
        repo.save("invoices", {**b, "notes": "note"})
        repo.save("absences", ab)
        repo.save("absences", {**ab, "note": "invented"})
        repo.save("entries", e)
        repo.save("entries", {**e, "note": "invented"})
        # the guarded value changed into another violation
        third = repo.save("invoices", {"number": "RE-2026-0003"})
        refused("numbering.duplicate", repo.save, "invoices", {**b, "number": third["number"]})
        refused("numbering.duplicate", repo.save, "invoices", {**third, "number": a["number"]})
        repo.save("invoices", {**b, "number": "RE-2026-0002"})  # a fix is allowed
        refused("state.period_reversed", repo.save, "absences", {**ab, "end": "2026-02-01"})
        refused("state.period_reversed", repo.save, "absences", {**ab, "start": "2026-04-01"})
        refused("format.date", repo.save, "entries", {**e, "date": "6.3.2026"})
        refused("range.negative", repo.save, "entries", {**e, "minutes": -6})
        repo.save("absences", {**ab, "end": "2026-03-10"})  # fixing the period is allowed
        # a new row with the same duplicate is refused
        refused("numbering.duplicate", repo.save, "invoices", {"number": "RE-2026-0001"})
    finally:
        repo.close()


def test_migration_does_not_change_old_rows(old_db):
    data, _ = old_db
    raw = sqlite3.connect(data / DB_FILENAME)
    before = {
        t: raw.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in ("invoices", "absences", "entries")
    }
    raw.close()
    repo = SQLiteRepository(data)
    repo.startup()
    after = {t: repo.conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in before}
    repo.close()
    assert after == before
    assert sum(len(v) for v in before.values()) == 4


def test_checkdb_reports_grandfathered_rows_after_migration(old_db):
    from permita38.core.service.integrity import IntegrityService

    data, planted = old_db
    repo = SQLiteRepository(data)
    repo.startup()
    try:
        findings = IntegrityService(repo).run(
            only=("numbering.duplicate", "state.period_reversed", "format.date", "range.negative")
        )
        got = sorted((f.code, f.collection, f.id, f.field) for f in findings)
    finally:
        repo.close()
    a, b, ab, e = (planted[k]["id"] for k in ("inv_a", "inv_b", "absence", "bad_date"))
    assert got == sorted(
        [
            ("numbering.duplicate", "invoices", a, "number"),
            ("numbering.duplicate", "invoices", b, "number"),
            ("state.period_reversed", "absences", ab, "end"),
            ("format.date", "entries", e, "date"),
            ("range.negative", "entries", e, "minutes"),
        ]
    )
