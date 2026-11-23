"""The table-level `append_only` guard kind (docs/specs/accounting.md section 7, docs/specs/db-constraints.md).
A small invented table; SQLite is exercised for real, PostgreSQL and MariaDB at DDL string level. Invented data."""

import re
import sqlite3

import pytest

from permita38.core.repository.dialect import DIALECTS, SQLiteDialect
from permita38.core.repository.schema import GUARD_KINDS, GUARDS, Column, Guard, Table

DIALECT = SQLiteDialect()
TABLE = Table("ledger", (Column("id", "id"), Column("note", "str")), pk="id", extra_column=None)
GUARD = Guard("guard_ledger_append_only", "ledger", "append_only", (), "state.append_only")
REFUSED = r"^permita38:state\.append_only$"


def new_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute(DIALECT.create_table(TABLE))
    for stmt in DIALECT.guard_sql(GUARD):
        db.execute(stmt)
    return db


def test_kind_is_registered_and_attached_to_no_table_yet():
    assert "append_only" in GUARD_KINDS
    assert not [g for g in GUARDS if g.kind == "append_only"]


def test_validation():
    with pytest.raises(ValueError):
        Guard("g", "ledger", "append_only", ("note",), "state.append_only")
    with pytest.raises(ValueError):
        Guard("g", "ledger", "append_only", (), "state.other")
    with pytest.raises(ValueError):
        Guard("g", "ledger", "append_only", (), "state.append_only", days=3)


def test_sqlite_insert_works_update_and_delete_are_refused():
    db = new_db()
    db.execute("INSERT INTO ledger (id, note) VALUES ('a', 'first')")
    db.execute("INSERT INTO ledger (id, note) VALUES ('b', 'second')")
    with pytest.raises(sqlite3.IntegrityError, match=REFUSED):
        db.execute("UPDATE ledger SET note = 'changed' WHERE id = 'a'")
    with pytest.raises(sqlite3.IntegrityError, match=REFUSED):
        db.execute("DELETE FROM ledger WHERE id = 'a'")
    with pytest.raises(sqlite3.IntegrityError, match=REFUSED):
        db.execute("DELETE FROM ledger")
    # the repository's upsert on an existing id is an update and is refused too
    with pytest.raises(sqlite3.IntegrityError, match=REFUSED):
        db.execute(DIALECT.upsert(TABLE, TABLE.column_names), ["a", "again"])
    assert db.execute("SELECT id, note FROM ledger ORDER BY id").fetchall() == [("a", "first"), ("b", "second")]


def test_sqlite_triggers():
    stmts = DIALECT.guard_sql(GUARD)
    assert len(stmts) == 2
    assert 'BEFORE UPDATE ON "ledger"' in stmts[0] and 'BEFORE DELETE ON "ledger"' in stmts[1]
    assert all("RAISE(ABORT, 'permita38:state.append_only')" in s for s in stmts)


def test_postgresql_ddl():
    stmts = DIALECTS["postgresql"].guard_sql(GUARD)
    assert len(stmts) == 4
    assert "CREATE FUNCTION" in stmts[0] and "RAISE EXCEPTION 'permita38:state.append_only'" in stmts[0]
    assert 'BEFORE UPDATE ON "ledger" FOR EACH ROW EXECUTE FUNCTION' in stmts[1]
    assert 'BEFORE DELETE ON "ledger" FOR EACH ROW EXECUTE FUNCTION' in stmts[2]
    assert (
        '"guard_ledger_append_only_trunc" BEFORE TRUNCATE ON "ledger" FOR EACH STATEMENT EXECUTE FUNCTION' in stmts[3]
    )
    assert stmts[3].split("EXECUTE FUNCTION")[1] == stmts[1].split("EXECUTE FUNCTION")[1]  # the same function


def test_mariadb_ddl():
    stmts = DIALECTS["mariadb"].guard_sql(GUARD)
    assert len(stmts) == 2
    assert "BEFORE UPDATE ON `ledger` FOR EACH ROW" in stmts[0]
    assert "BEFORE DELETE ON `ledger` FOR EACH ROW" in stmts[1]
    assert all("SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.append_only'" in s for s in stmts)


def test_guards_suspended_refuses_append_only(tmp_path):
    from permita38.core.repository.sqlite import SQLiteRepository

    repo = SQLiteRepository(tmp_path / "x.db")
    with repo.transaction():
        with pytest.raises(ValueError, match="append_only"):
            with repo.guards_suspended({"append_only"}):
                pass


def test_schema_checks_understand_the_append_only_shape():
    """The trigger-name suffixes the schema tests expect, for a throwaway table spec with an append_only guard."""
    from tests.backend.test_guards_schema import guard_suffixes

    assert guard_suffixes(GUARD, "sqlite") == ("_upd", "_del")
    assert guard_suffixes(GUARD, "postgresql") == ("_fn", "_upd", "_del", "_trunc")
    assert guard_suffixes(GUARD, "mariadb") == ("_upd", "_del")
    for name, suffixes in (("sqlite", 2), ("postgresql", 4), ("mariadb", 2)):
        assert len(DIALECTS[name].guard_sql(GUARD)) == suffixes == len(guard_suffixes(GUARD, name))
    names = {
        n for n in re.findall(r'"?`?(guard_ledger_append_only_\w+)', "".join(DIALECTS["postgresql"].guard_sql(GUARD)))
    }
    assert names == {"guard_ledger_append_only" + x for x in ("_fn", "_upd", "_del", "_trunc")}
    # a real SQLite database carries exactly the `_upd` / `_del` triggers the helper predicts
    got = {r[0] for r in new_db().execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert got == {GUARD.name + x for x in guard_suffixes(GUARD, "sqlite")}
