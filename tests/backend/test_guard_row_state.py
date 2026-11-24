"""The row-state guard kinds `frozen_when` and `set_once` (docs/specs/db-constraints.md, accounting spec section 8.1).
A throwaway table spec shaped like `vat_returns`; SQLite is exercised for real, PostgreSQL and MariaDB at DDL string
level. The kinds are not attached to a real table yet. Invented data."""

import sqlite3

import pytest

from permita38.core.repository import schema
from permita38.core.repository.dialect import DIALECTS, TRANSITION_KINDS, SQLiteDialect
from permita38.core.repository.schema import GUARD_KINDS, Column, Guard, Table

DIALECT = SQLiteDialect()
TABLE = Table(
    "returns",
    tuple(Column(n, t) for n, t in [("id", "id"), ("status", "str"), ("amount", "int"), ("filed_on", "str")])
    + tuple(Column(n, "str") for n in ("reference", "note", "finalised_at", "replaced_by"))
    + (Column("boxes", "json"),),
    pk="id",
)
FROZEN = Guard(
    "guard_returns_frozen",
    "returns",
    "frozen_when",
    ("status",),
    "state.frozen",
    values=("final",),
    mutable=("filed_on", "reference", "note", "replaced_by"),
    reopen_if_null="filed_on",
    reopen_clears=("finalised_at", "boxes"),
)
ONCE = Guard("guard_returns_filed_on_once", "returns", "set_once", ("filed_on",), "state.set_once")
FROZEN_RE = r"^permita38:state\.frozen$"
ONCE_RE = r"^permita38:state\.set_once$"


@pytest.fixture(autouse=True)
def throwaway_table(monkeypatch):
    monkeypatch.setitem(schema.TABLES, "returns", TABLE)


def new_db(guards=(FROZEN, ONCE)) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute(DIALECT.create_table(TABLE))
    for g in guards:
        for stmt in DIALECT.guard_sql(g):
            db.execute(stmt)
    db.execute("INSERT INTO returns (id, status, amount, finalised_at, boxes) VALUES ('d', 'draft', 1, NULL, NULL)")
    db.execute(
        "INSERT INTO returns (id, status, amount, finalised_at, boxes) VALUES ('f', 'final', 5, 't0', '{\"a\":1}')"
    )
    return db


def row(db, id_):
    return db.execute(
        "SELECT status, amount, filed_on, reference, note, finalised_at, boxes FROM returns WHERE id = ?", [id_]
    ).fetchone()


def test_kinds_are_registered():
    assert {"frozen_when", "set_once"} <= set(GUARD_KINDS)
    assert TRANSITION_KINDS == {"frozen_when", "set_once"}
    assert not {g.kind for g in schema.GUARDS} & TRANSITION_KINDS  # not attached to a table yet (next work package)


def test_validation():
    with pytest.raises(ValueError):
        Guard("g", "returns", "frozen_when", ("status",), "state.other", values=("final",))
    with pytest.raises(ValueError):
        Guard("g", "returns", "frozen_when", ("status",), "state.frozen")  # no frozen value
    with pytest.raises(ValueError):
        Guard("g", "returns", "frozen_when", ("status",), "state.frozen", values=("a", "b"))
    with pytest.raises(ValueError):
        Guard("g", "returns", "frozen_when", ("status",), "state.frozen", values=("final",), mutable=("status",))
    with pytest.raises(ValueError):
        Guard("g", "returns", "frozen_when", ("status",), "state.frozen", values=("final",), mutable=("nope",))
    with pytest.raises(ValueError):  # clears without a reopen condition
        Guard("g", "returns", "frozen_when", ("status",), "state.frozen", values=("final",), reopen_clears=("boxes",))
    with pytest.raises(ValueError):
        Guard(
            "g",
            "returns",
            "frozen_when",
            ("status",),
            "state.frozen",
            values=("final",),
            mutable=("note",),
            reopen_if_null="filed_on",
            reopen_clears=("note",),
        )
    with pytest.raises(ValueError):
        Guard("g", "returns", "set_once", ("filed_on",), "state.frozen")
    with pytest.raises(ValueError):
        Guard("g", "returns", "set_once", ("filed_on", "note"), "state.set_once")
    with pytest.raises(ValueError):
        Guard("g", "returns", "set_once", ("filed_on",), "state.set_once", mutable=("note",))


def test_frozen_row_allows_only_the_mutable_columns():
    db = new_db()
    db.execute("UPDATE returns SET reference = 'R1', note = 'n', replaced_by = 'x' WHERE id = 'f'")
    db.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'f'")
    for sql in (
        "UPDATE returns SET amount = 6 WHERE id = 'f'",
        "UPDATE returns SET finalised_at = 't1' WHERE id = 'f'",
        "UPDATE returns SET boxes = NULL WHERE id = 'f'",
        "UPDATE returns SET extra = '{}' WHERE id = 'f'",
        "UPDATE returns SET id = 'g' WHERE id = 'f'",
        "UPDATE returns SET status = 'draft' WHERE id = 'f'",  # filed already
        "UPDATE returns SET amount = 6, note = 'ok' WHERE id = 'f'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
            db.execute(sql)
    assert row(db, "f") == ("final", 5, "2026-02-10", "R1", "n", "t0", '{"a":1}')


def test_same_value_updates_and_non_final_rows_are_free():
    db = new_db()
    db.execute("UPDATE returns SET amount = 5, status = 'final' WHERE id = 'f'")  # nothing changes
    db.execute("UPDATE returns SET amount = 9, status = 'draft', filed_on = '2026-01-01' WHERE id = 'd'")
    db.execute("UPDATE returns SET status = 'final', amount = 10 WHERE id = 'd'")  # becoming final is a draft update
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET amount = 11 WHERE id = 'd'")


def test_filed_on_goes_from_null_to_a_date_but_never_back():
    db = new_db()
    db.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'f'")
    for value in (None, "", "2026-02-11"):
        with pytest.raises(sqlite3.IntegrityError, match=ONCE_RE):
            db.execute("UPDATE returns SET filed_on = ? WHERE id = 'f'", [value])
    db.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'f'")  # the same value is not a change
    assert row(db, "f")[2] == "2026-02-10"
    # without the set_once guard the frozen guard alone would let a mutable column go back
    db2 = new_db(guards=(FROZEN,))
    db2.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'f'")
    db2.execute("UPDATE returns SET filed_on = NULL WHERE id = 'f'")


def test_set_once_treats_blank_as_unset():
    db = new_db(guards=(ONCE,))
    db.execute("UPDATE returns SET filed_on = '' WHERE id = 'd'")
    db.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'd'")
    with pytest.raises(sqlite3.IntegrityError, match=ONCE_RE):
        db.execute("UPDATE returns SET filed_on = '2026-02-12' WHERE id = 'd'")


def test_final_to_draft_only_while_filed_on_is_null():
    db = new_db()
    db.execute(
        "UPDATE returns SET status = 'draft', finalised_at = NULL, boxes = NULL, note = 'reopened' WHERE id = 'f'"
    )
    assert row(db, "f") == ("draft", 5, None, None, "reopened", None, None)
    db = new_db()
    db.execute("UPDATE returns SET filed_on = '2026-02-10' WHERE id = 'f'")
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET status = 'draft', finalised_at = NULL, boxes = NULL WHERE id = 'f'")
    assert row(db, "f")[0] == "final"


def test_reopening_cannot_change_other_columns_or_file_at_the_same_time():
    db = new_db()
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET status = 'draft', amount = 99 WHERE id = 'f'")
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET status = 'draft', filed_on = '2026-02-10' WHERE id = 'f'")
    # the clearable columns change only together with a reopen
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET boxes = NULL WHERE id = 'f'")


def test_state_never_leaves_without_a_reopen_condition():
    g = Guard(
        "guard_returns_frozen",
        "returns",
        "frozen_when",
        ("status",),
        "state.frozen",
        values=("final",),
        mutable=("note",),
    )
    db = new_db(guards=(g,))
    db.execute("UPDATE returns SET note = 'x' WHERE id = 'f'")
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("UPDATE returns SET status = 'draft' WHERE id = 'f'")


def test_delete_of_a_final_row_is_refused_a_draft_may_go():
    db = new_db()
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("DELETE FROM returns WHERE id = 'f'")
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute("DELETE FROM returns")
    db.execute("DELETE FROM returns WHERE id = 'd'")
    assert db.execute("SELECT id FROM returns").fetchall() == [("f",)]


def test_upsert_of_the_repository_is_checked_like_an_update():
    db = new_db()
    cols = TABLE.column_names
    vals = {"id": "f", "status": "final", "amount": 7, "filed_on": None}
    with pytest.raises(sqlite3.IntegrityError, match=FROZEN_RE):
        db.execute(DIALECT.upsert(TABLE, cols), [vals.get(c) for c in cols])


def test_sqlite_triggers():
    stmts = DIALECT.guard_sql(FROZEN)
    assert len(stmts) == 2
    assert 'BEFORE UPDATE ON "returns"' in stmts[0] and 'BEFORE DELETE ON "returns"' in stmts[1]
    assert all("RAISE(ABORT, 'permita38:state.frozen')" in s for s in stmts)
    once = DIALECT.guard_sql(ONCE)
    assert len(once) == 1 and 'BEFORE UPDATE OF "filed_on" ON "returns"' in once[0]
    assert "RAISE(ABORT, 'permita38:state.set_once')" in once[0]
    got = {r[0] for r in new_db().execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert got == {"guard_returns_frozen_upd", "guard_returns_frozen_del", "guard_returns_filed_on_once_upd"}


def test_postgresql_ddl():
    d = DIALECTS["postgresql"]
    f = d.guard_sql(FROZEN)
    assert len(f) == 4
    assert "CREATE FUNCTION" in f[0] and "RAISE EXCEPTION 'permita38:state.frozen'" in f[0] and "RETURN NEW;" in f[0]
    assert 'NEW."amount" IS DISTINCT FROM OLD."amount"' in f[0]
    assert 'BEFORE UPDATE ON "returns" FOR EACH ROW EXECUTE FUNCTION "guard_returns_frozen_upd_fn"()' in f[1]
    assert "RETURN OLD;" in f[2] and "RAISE EXCEPTION 'permita38:state.frozen'" in f[2]
    assert 'BEFORE DELETE ON "returns" FOR EACH ROW EXECUTE FUNCTION "guard_returns_frozen_del_fn"()' in f[3]
    o = d.guard_sql(ONCE)
    assert len(o) == 2 and "RAISE EXCEPTION 'permita38:state.set_once'" in o[0]
    assert 'BEFORE UPDATE OF "filed_on" ON "returns"' in o[1]


def test_mariadb_ddl():
    d = DIALECTS["mariadb"]
    f = d.guard_sql(FROZEN)
    assert len(f) == 2
    assert "BEFORE UPDATE ON `returns` FOR EACH ROW" in f[0] and "BEFORE DELETE ON `returns` FOR EACH ROW" in f[1]
    assert all("SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'permita38:state.frozen'" in s for s in f)
    assert "NOT (NEW.`amount` <=> OLD.`amount`)" in f[0]  # numbers compare as they are
    assert "NOT (BINARY NEW.`id` <=> BINARY OLD.`id`)" in f[0]  # strings as bytes
    assert "BINARY OLD.`status` = 'final'" in f[0]
    o = d.guard_sql(ONCE)
    assert len(o) == 1 and "permita38:state.set_once" in o[0] and "BEFORE UPDATE ON `returns`" in o[0]


def test_every_dialect_locks_every_column_not_named():
    for name, d in DIALECTS.items():
        upd = d.frozen_conditions(FROZEN)[0]
        for c in ("id", "amount", "extra"):
            assert d.quote(c) in upd, (name, c)
        for c in ("reference", "note", "replaced_by"):  # mutable: only mentioned nowhere in the update condition
            assert d.quote(c) not in upd, (name, c)


def test_a_frozen_guard_needs_its_table_spec():
    other = Guard("g_other", "unknown_table", "frozen_when", ("status",), "state.frozen", values=("final",))
    with pytest.raises(ValueError, match="needs table"):
        DIALECT.guard_sql(other)
