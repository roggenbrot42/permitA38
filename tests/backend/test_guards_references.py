"""Reference guards (docs/specs/db-guards-v2.md section 3): triggers of a migrated SQLite database, their behaviour on
direct repository calls, grandfathering of dangling values written before the migration, and the delete side.
All data is invented."""

import itertools
import re
import sqlite3

import pytest

from permita38.core.repository import TABLES, ConstraintViolation
from permita38.core.repository.dialect import DIALECTS, SQLiteDialect
from permita38.core.repository.schema import GUARD_KINDS, GUARDS, Guard
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service import integrity

from .test_migrations import CHAIN, DIALECT_NAMES, HEAD, real_chain_copy, use_chain

REF_GUARDS = [g for g in GUARDS if g.kind == "reference"]

# (table, column, target) of every guarded reference, in declaration order
EXPECTED = [
    ("allocations", "project_id", "projects"),
    ("allocations", "user_id", "users"),
    ("allocations", "supplier_id", "suppliers"),
    ("contracts", "project_id", "projects"),
    ("entries", "project_id", "projects"),
    ("offers", "project_id", "projects"),
    ("invoices", "project_id", "projects"),
]
CASES = [pytest.param(t, c, tgt, id=f"{t}.{c}") for t, c, tgt in EXPECTED]


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


def refused_delete(repo, coll, rid):
    """The repository's delete translates the trigger error into a ConstraintViolation(ref.in_use, collection)."""
    with pytest.raises(ConstraintViolation) as e:
        repo.delete(coll, rid)
    assert e.value.code == "ref.in_use"
    assert e.value.collection == coll
    assert repo.get(coll, rid) is not None


_COUNTER = itertools.count(1)


def target_row(repo, target):
    n = next(_COUNTER)
    return repo.save(target, {"name": f"Demo {n}", "username": f"demo-user-{n}"})


def companions(repo, table, col, value):
    """The other fields a row needs: an allocation has exactly one owner (user xor supplier)."""
    if table != "allocations":
        return {}
    if col == "project_id":
        return {"user_id": target_row(repo, "users")["id"]}
    if value is None:  # an unset owner needs the other one
        other = "supplier_id" if col == "user_id" else "user_id"
        return {other: target_row(repo, "suppliers" if other == "supplier_id" else "users")["id"]}
    return {}


def build(repo, table, col, value):
    return {**companions(repo, table, col, value), col: value}


# ------------------------------------------------------------------ (a) declarations
def test_the_guarded_references_are_exactly_the_specified_ones():
    assert [(g.table, g.columns[0], g.target) for g in REF_GUARDS] == EXPECTED
    assert {g.code for g in REF_GUARDS} == {"ref.dangling"}
    assert [g.name for g in REF_GUARDS][0] == "guard_allocations_project_id_ref"
    assert "reference" in GUARD_KINDS
    for g in REF_GUARDS:
        assert TABLES[g.table].column(g.columns[0])
        assert TABLES[g.target].pk == "id"


def test_reference_guard_validation():
    ok = Guard("g", "entries", "reference", ("project_id",), "ref.dangling", target="projects")
    assert ok.read_columns == ("project_id",)
    with pytest.raises(ValueError, match="target"):
        Guard("g", "entries", "reference", ("project_id",), "ref.dangling")
    with pytest.raises(ValueError, match="target"):
        Guard("g", "entries", "reference", ("project_id",), "ref.dangling", target="nowhere")
    with pytest.raises(ValueError, match="does not exist"):
        Guard("g", "entries", "reference", ("no_such_column",), "ref.dangling", target="projects")
    with pytest.raises(ValueError, match="ref.dangling"):
        Guard("g", "entries", "reference", ("project_id",), "ref.other", target="projects")
    with pytest.raises(ValueError, match="does not take target"):
        Guard("g", "entries", "required", ("project_id",), "state.required", target="projects")
    with pytest.raises(ValueError, match="needs 1 column"):
        Guard("g", "entries", "reference", ("project_id", "task_id"), "ref.dangling", target="projects")


def test_every_guarded_reference_is_covered_by_the_check_db_rule():
    for g in REF_GUARDS:
        col = g.columns[0]
        assert integrity.REF_TARGETS[col] == g.target, (g.table, col)
        assert TABLES[g.table].column(col)


def test_lookup_indexes_exist_in_the_schema_and_the_database(repo):
    names = {r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    for t in ("contracts", "offers", "invoices"):
        assert any(
            i.name == f"idx_{t}_project" and i.columns == ("project_id",) and not i.unique for i in TABLES[t].indexes
        )
        assert f"idx_{t}_project" in names


# ------------------------------------------------------------------ (b) migration files and drift
def reference_statements(dialect_name):
    d = DIALECTS[dialect_name]
    return [s for g in REF_GUARDS for s in d.guard_sql(g)]


def test_sqlite_triggers_equal_guard_sql_including_the_delete_side(repo):
    want = {}
    for g in REF_GUARDS:
        ins, upd, dele = SQLiteDialect().guard_sql(g)
        want[f"{g.name}_ins"] = norm(ins)
        want[f"{g.name}_upd"] = norm(upd)
        want[f"{g.name}_del"] = norm(dele)
    assert len(want) == 21
    got = {
        name: norm(sql)
        for name, sql in repo.conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        if name in want
    }
    assert got == want
    for g in REF_GUARDS:  # the delete trigger sits on the target table and raises ref.in_use
        row = repo.conn.execute(
            "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (g.name + "_del",)
        ).fetchone()
        assert row[0] == g.target
        assert "permita38:ref.in_use" in row[1]


def test_sqlite_migration_file_is_exactly_the_generated_statements_and_indexes():
    m = next(m for m in CHAIN if m.name == "reference_guards")
    assert m.requires == "e4b70dba"
    sql = m.sql()
    statements = reference_statements("sqlite")
    assert len(statements) == 21
    for s in statements:
        assert s in sql
    assert sql.count("CREATE TRIGGER") == 21
    assert sql.count("CREATE INDEX") == 3
    for t in ("contracts", "offers", "invoices"):
        assert f'CREATE INDEX "idx_{t}_project" ON "{t}" ("project_id");' in sql
    assert m.id == HEAD


@pytest.mark.parametrize("dialect", ["postgresql", "mariadb"])
def test_native_migration_files_contain_the_generated_statements(dialect):
    from permita38.core import database

    sql = next(m for m in database.list_migrations(dialect) if m.name == "reference_guards").sql()
    statements = reference_statements(dialect)
    assert statements
    for s in statements:
        assert s in sql
    assert sql.count("CREATE INDEX") == 3
    assert sql.count("permita38:ref.dangling") == (7 if dialect == "postgresql" else 14)
    assert sql.count("permita38:ref.in_use") == 7


def test_every_dialect_folder_has_the_migration():
    from permita38.core import database

    for d in DIALECT_NAMES:
        assert any(m.name == "reference_guards" for m in database.list_migrations(d)), d
    database.validate_dialects()


def test_postgresql_and_mariadb_statements_signal_the_codes():
    pg = "\n".join(reference_statements("postgresql"))
    assert "RAISE EXCEPTION 'permita38:ref.dangling'" in pg
    assert "RAISE EXCEPTION 'permita38:ref.in_use'" in pg
    assert pg.count("BEFORE DELETE ON") == 7
    maria = "\n".join(reference_statements("mariadb"))
    assert "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.dangling'" in maria
    assert "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:ref.in_use'" in maria
    assert maria.count("BEFORE DELETE ON") == 7


# ------------------------------------------------------------------ (c) insert / update side
@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_insert_with_a_missing_target_is_refused_and_stores_nothing(repo, table, col, target):
    row = build(repo, table, col, "ghost-id-1")
    before = len(repo.list(table))
    refused("ref.dangling", repo.save, table, row)
    assert len(repo.list(table)) == before


@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_insert_with_an_existing_target_is_accepted(repo, table, col, target):
    t = target_row(repo, target)
    saved = repo.save(table, build(repo, table, col, t["id"]))
    assert saved[col] == t["id"]
    assert repo.get(table, saved["id"])[col] == t["id"]


@pytest.mark.parametrize(("table", "col", "target"), CASES)
@pytest.mark.parametrize("unset", [None, "", "   "])
def test_an_unset_reference_is_accepted(repo, table, col, target, unset):
    row = build(repo, table, col, unset)
    saved = repo.save(table, row)
    assert saved.get(col) == unset


@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_update_to_a_missing_target_is_refused_and_to_an_existing_one_accepted(repo, table, col, target):
    first, second = target_row(repo, target), target_row(repo, target)
    saved = repo.save(table, build(repo, table, col, first["id"]))
    refused("ref.dangling", repo.save, table, {**saved, col: "ghost-id-2"})
    assert repo.get(table, saved["id"])[col] == first["id"]
    moved = repo.save(table, {**saved, col: second["id"]})
    assert moved[col] == second["id"]
    # re-saving unchanged and changing another field stays fine
    repo.save(table, moved)
    repo.save(table, {**moved, "note": "invented"} if table == "entries" else {**moved, "title": "invented"})


@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_a_direct_sql_update_to_a_missing_target_is_refused(repo, table, col, target):
    t = target_row(repo, target)
    saved = repo.save(table, build(repo, table, col, t["id"]))
    with pytest.raises(sqlite3.IntegrityError) as e:
        repo.conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE "id" = ?', ("ghost-id-3", saved["id"]))
    assert str(e.value) == "permita38:ref.dangling"


# ------------------------------------------------------------------ (d) grandfathering of dangling values
@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database at the previous head (the reference migration removed) holding rows that point at nothing."""
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        (root / d / "0017-reference_guards.sql").unlink()
    data = tmp_path / "data"
    planted = {}
    with monkeypatch.context() as m:
        use_chain(m, root)
        old = SQLiteRepository(data)
        old.startup()
        assert old.get_revision() != HEAD
        assert (
            old.conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE '%\\_ref\\_%' ESCAPE '\\'").fetchone()[
                0
            ]
            == 0
        )
        for table, col, target in EXPECTED:
            planted[(table, col)] = old.save(table, build(old, table, col, f"ghost-{table}-{col}"))
        old.close()
    return data, planted


@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_legacy_dangling_value_stays_writable_but_cannot_be_changed_to_another_missing_one(old_db, table, col, target):
    data, planted = old_db
    repo = SQLiteRepository(data)
    repo.startup()
    try:
        assert repo.get_revision() == HEAD
        legacy = planted[(table, col)]
        assert legacy[col] == f"ghost-{table}-{col}"
        assert repo.get(table, legacy["id"]) == legacy  # the migration did not touch the row
        repo.save(table, legacy)  # unchanged re-save (the upsert fires the insert trigger)
        edit = {"note": "invented"} if table == "entries" else {"title": "invented"}
        repo.save(table, {**legacy, **edit})
        refused("ref.dangling", repo.save, table, {**legacy, col: "ghost-other"})
        fixed = repo.save(table, {**legacy, col: target_row(repo, target)["id"]})  # fixing it is allowed
        assert fixed[col] != legacy[col]
        # a new row with the same dangling value is refused
        refused("ref.dangling", repo.save, table, build(repo, table, col, legacy[col]))
    finally:
        repo.close()


# ------------------------------------------------------------------ (e) delete side
@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_deleting_a_referenced_target_is_refused_and_an_unreferenced_one_is_allowed(repo, table, col, target):
    used, unused = target_row(repo, target), target_row(repo, target)
    saved = repo.save(table, build(repo, table, col, used["id"]))
    refused_delete(repo, target, used["id"])
    assert repo.delete(target, unused["id"]) is True
    assert repo.get(target, unused["id"]) is None
    # once the referencing row is gone, the target can go
    assert repo.delete(table, saved["id"]) is True
    assert repo.delete(target, used["id"]) is True
    assert repo.get(target, used["id"]) is None


@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_a_target_is_protected_only_while_a_row_still_points_at_it(repo, table, col, target):
    """Moving the reference frees the old target and protects the new one."""
    a = target_row(repo, target)
    b = target_row(repo, target)
    saved = repo.save(table, build(repo, table, col, a["id"]))
    repo.save(table, {**saved, col: b["id"]})  # moved: a is free now, b is in use
    assert repo.delete(target, a["id"]) is True
    refused_delete(repo, target, b["id"])


def test_an_unrelated_record_can_always_be_deleted(repo):
    p = repo.save("projects", {"name": "Demo"})
    c = repo.save("customers", {"name": "Demo customer"})
    assert repo.delete("customers", c["id"]) is True
    assert repo.delete("projects", p["id"]) is True


# ------------------------------------------------------------------ check-db finds the legacy dangling values
@pytest.mark.parametrize(("table", "col", "target"), CASES)
def test_check_db_names_a_planted_dangling_reference(repo, unguarded, table, col, target):
    ghost = f"ghost-{table}-{col}"
    saved = unguarded(repo, table, build(repo, table, col, ghost), "ref.dangling")
    found = integrity.IntegrityService(repo).run(only=["ref.dangling"])
    assert [(f.code, f.collection, f.id, f.field) for f in found] == [("ref.dangling", table, saved["id"], col)]
