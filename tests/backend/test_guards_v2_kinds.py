"""Guards v2, part A: the row-local kinds one_of, exactly_one, required, max_days, range and implies
(docs/specs/db-guards-v2.md, section 1). A small invented table is written through the SQLite upsert, exactly as the
repository writes. All data is invented."""

import re
import sqlite3
from typing import Literal

import pytest

from permita38.core.repository.dialect import DIALECTS, SQLiteDialect
from permita38.core.repository.importer import RESTORE_SUSPENDED
from permita38.core.repository.schema import (
    GUARD_KINDS,
    SQL_BLANK,
    Column,
    Eq,
    Guard,
    In,
    IsBlank,
    IsSet,
    Table,
    one_of_from_literal,
)
from permita38.core.repository.sqlite import SQLiteRepository

from .repository_registry import make_sqlite

DIALECT = SQLiteDialect()
TABLE = Table(
    "t",
    tuple(Column(n, t) for n, t in [("id", "id"), ("kind", "str"), ("a", "str"), ("b", "str"),
                                    ("s", "str"), ("e", "str"), ("n", "float"), ("x", "str")]),
    pk="id",
    extra_column=None,
)  # fmt: skip
COLS = TABLE.column_names
EMPTY = {c: None for c in COLS}


def new_db(guard: Guard | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute(DIALECT.create_table(TABLE))
    if guard:
        install(db, guard)
    return db


def install(db: sqlite3.Connection, guard: Guard) -> None:
    for stmt in DIALECT.guard_sql(guard):
        db.execute(stmt)


def save(db: sqlite3.Connection, row: dict) -> None:
    """The repository always upserts."""
    full = {**EMPTY, **row}
    db.execute(DIALECT.upsert(TABLE, COLS), [full[c] for c in COLS])


def refused(db: sqlite3.Connection, code: str, row: dict) -> None:
    with pytest.raises(sqlite3.IntegrityError) as e:
        save(db, row)
    assert str(e.value) == f"permita38:{code}"


G_ONE_OF = Guard("g_one_of", "t", "one_of", ("kind",), "vocab.unknown", values=("open", "done", "it's"))
G_EXACTLY = Guard("g_exactly", "t", "exactly_one", ("a", "b"), "state.exactly_one")
G_REQUIRED = Guard("g_required", "t", "required", ("a",), "state.required")
G_DAYS = Guard("g_days", "t", "max_days", ("s", "e"), "range.too_long", days=10)
G_RANGE_EXCL = Guard("g_range_excl", "t", "range", ("n",), "range.bounds", lo=0, hi=80, lo_exclusive=True)
G_RANGE_LO = Guard("g_range_lo", "t", "range", ("n",), "range.bounds", lo=5)
G_IMPLIES = Guard("g_implies", "t", "implies", (), "state.implies:g_implies", if_=Eq("kind", "done"), then=IsSet("a"))

# guard, base row, refused new rows, accepted edge rows, grandfathered violating row, change of the guarded column
CASES = {
    "one_of": (
        G_ONE_OF,
        {},
        [{"kind": "bogus"}, {"kind": "Open"}, {"kind": "open "}],
        [{"kind": "open"}, {"kind": "it's"}, {"kind": None}, {"kind": ""}, {"kind": "  "}],
        {"kind": "old"},
        {"kind": "older"},
    ),
    "exactly_one": (
        G_EXACTLY,
        {"a": "p"},
        [{"a": "p", "b": "q"}, {}, {"a": "", "b": " "}, {"a": None, "b": "\t\n"}],
        [{"a": "p"}, {"b": "q"}, {"a": "", "b": "q"}, {"a": " ", "b": "q"}, {"a": "p", "b": ""}],
        {"a": "p", "b": "q"},
        {"a": "z"},
    ),
    "required": (
        G_REQUIRED,
        {"a": "p"},
        [{"a": None}, {"a": ""}, {"a": "   "}, {"a": " \t\r\n"}],
        [{"a": "p"}, {"a": "0"}, {"a": " p "}],
        {"a": None},
        {"a": ""},
    ),
    "max_days": (
        G_DAYS,
        {},
        [{"s": "2026-01-01", "e": "2026-01-12"}, {"s": "2025-12-25", "e": "2026-01-05"}],
        [
            {"s": "2026-01-01", "e": "2026-01-11"},  # exactly 10 days
            {"s": "2026-01-01", "e": "2026-01-01"},
            {"s": "2026-01-12", "e": "2026-01-01"},  # reversed: not this guard's business
            {"s": None, "e": "2026-06-01"},
            {"s": "2026-01-01", "e": None},
            {"s": "", "e": "2026-06-01"},
            {"s": "abc", "e": "2026-06-01"},
            {"s": "2026-01-01", "e": "2026-13-45"},
            {"s": "5.1.2026", "e": "2026-06-01"},
        ],
        {"s": "2026-01-01", "e": "2026-03-01"},
        {"e": "2026-04-01"},
    ),
    "range_exclusive_min": (
        G_RANGE_EXCL,
        {},
        [{"n": 0}, {"n": 0.0}, {"n": -1}, {"n": 80.5}, {"n": 1000}],
        [{"n": 0.25}, {"n": 80}, {"n": 80.0}, {"n": None}, {"n": ""}],
        {"n": 100},
        {"n": 200},
    ),
    "range_min_only": (
        G_RANGE_LO,
        {},
        [{"n": 4.99}, {"n": 0}, {"n": -3}],
        [{"n": 5}, {"n": 5.01}, {"n": 1e9}, {"n": None}, {"n": ""}],
        {"n": 1},
        {"n": 2},
    ),
    "implies": (
        G_IMPLIES,
        {},
        [{"kind": "done"}, {"kind": "done", "a": ""}, {"kind": "done", "a": "   "}],
        [{"kind": "done", "a": "p"}, {"kind": "open"}, {"kind": None}, {}, {"kind": "open", "a": None}],
        {"kind": "done"},
        {"a": ""},
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_refused_new_row_and_accepted_edges(name):
    guard, base, bad_rows, good_rows, _legacy, _change = CASES[name]
    db = new_db(guard)
    for i, row in enumerate(bad_rows):
        refused(db, guard.code, {**row, "id": f"bad{i}"})
    assert db.execute("SELECT count(*) FROM t").fetchone() == (0,)
    for i, row in enumerate(good_rows):
        save(db, {**row, "id": f"good{i}"})
    assert db.execute("SELECT count(*) FROM t").fetchone() == (len(good_rows),)


@pytest.mark.parametrize("name", CASES)
def test_update_of_a_stored_row_into_a_violation_is_refused(name):
    guard, base, bad_rows, good_rows, _legacy, _change = CASES[name]
    db = new_db(guard)
    save(db, {**good_rows[0], "id": "r1"})
    for row in bad_rows:
        refused(db, guard.code, {**row, "id": "r1"})
    # a plain UPDATE statement (not an upsert) is guarded as well
    bad = {k: v for k, v in {**EMPTY, **bad_rows[0]}.items() if k != "id"}
    sets = ", ".join(f'"{c}" = ?' for c in bad)
    with pytest.raises(sqlite3.IntegrityError) as e:
        db.execute(f"UPDATE t SET {sets} WHERE \"id\" = 'r1'", list(bad.values()))
    assert str(e.value) == f"permita38:{guard.code}"


@pytest.mark.parametrize("name", CASES)
def test_grandfathered_row_stays_writable_but_not_changed_into_a_violation(name):
    guard, base, _bad, _good, legacy, change = CASES[name]
    db = new_db()  # the row exists before the guard does
    row = {**legacy, "id": "old1", "x": "keep"}
    save(db, row)
    install(db, guard)
    save(db, row)  # re-saved unchanged
    save(db, {**row, "x": "changed"})  # another column changed
    assert db.execute("SELECT x FROM t WHERE id = 'old1'").fetchone() == ("changed",)
    refused(db, guard.code, {**row, **change, "x": "changed"})  # changed into another violation
    assert db.execute("SELECT x FROM t WHERE id = 'old1'").fetchone() == ("changed",)
    # a new row with the same violation is refused
    refused(db, guard.code, {**row, "id": "new1"})


def test_grandfathering_clause_covers_every_read_column_of_implies():
    # kind is only read through the condition: changing it alone must be seen
    db = new_db()
    save(db, {"id": "r1", "kind": "open", "a": None})
    install(db, G_IMPLIES)
    save(db, {"id": "r1", "kind": "open", "a": None, "x": "other"})
    refused(db, "state.implies:g_implies", {"id": "r1", "kind": "done", "a": None})
    save(db, {"id": "r1", "kind": "done", "a": "p"})
    refused(db, "state.implies:g_implies", {"id": "r1", "kind": "done", "a": None})


def test_exactly_one_changing_the_untouched_column_is_seen():
    db = new_db(G_EXACTLY)
    save(db, {"id": "r1", "a": "p"})
    refused(db, "state.exactly_one", {"id": "r1", "a": "p", "b": "q"})
    refused(db, "state.exactly_one", {"id": "r1", "a": None, "b": None})
    save(db, {"id": "r1", "a": None, "b": "q"})


def test_max_days_boundary_is_in_days_not_in_dates():
    db = new_db(G_DAYS)
    save(db, {"id": "a1", "s": "2024-02-25", "e": "2024-03-06"})  # leap year: 10 days
    refused(db, "range.too_long", {"id": "a2", "s": "2024-02-25", "e": "2024-03-07"})


def test_trigger_shape_is_the_v1_shape():
    ins, upd = DIALECT.guard_sql(G_REQUIRED)
    assert ins.startswith('CREATE TRIGGER "g_required_ins" BEFORE INSERT ON "t"\nWHEN ')
    assert 'AND NOT EXISTS (SELECT 1 FROM "t" WHERE "id" = NEW."id" AND "a" IS NEW."a")' in ins
    assert ins.endswith("BEGIN SELECT RAISE(ABORT, 'permita38:state.required'); END;")
    assert upd.startswith('CREATE TRIGGER "g_required_upd" BEFORE UPDATE OF "a" ON "t"\nWHEN ')
    assert 'AND (NEW."a" IS NOT OLD."a")' in upd
    ins, upd = DIALECT.guard_sql(G_IMPLIES)
    assert 'BEFORE UPDATE OF "kind", "a" ON "t"' in upd
    assert 'AND "kind" IS NEW."kind" AND "a" IS NEW."a")' in ins
    assert 'NEW."kind" IS NOT OLD."kind" OR NEW."a" IS NOT OLD."a"' in upd


# ------------------------------------------------------------------ conditions
def test_condition_values_are_quoted_as_sql_literals():
    g = Guard(
        "g",
        "t",
        "implies",
        (),
        "state.implies:g",
        if_=Eq("kind", "o'; DROP TABLE t; --"),
        then=In("a", ("x", 2, 1.5, True)),
    )
    sql = DIALECT.guard_sql(g)[0]
    assert "NEW.\"kind\" = 'o''; DROP TABLE t; --'" in sql
    assert "NEW.\"a\" IN ('x', 2, 1.5, 1)" in sql
    db = new_db(g)
    save(db, {"id": "r1", "kind": "o'; DROP TABLE t; --", "a": "x"})
    refused(db, "state.implies:g", {"id": "r2", "kind": "o'; DROP TABLE t; --", "a": "y"})
    assert db.execute("SELECT count(*) FROM t").fetchone() == (1,)


@pytest.mark.parametrize("bad", [None, [1], {"a": 1}, float("nan"), float("inf"), b"x"])
def test_condition_values_must_be_plain_scalars(bad):
    with pytest.raises(ValueError):
        Eq("kind", bad)
    with pytest.raises(ValueError):
        In("kind", (bad,))


def test_in_needs_values():
    with pytest.raises(ValueError):
        In("kind", ())


def test_implies_with_in_isblank_and_isset_conditions():
    g = Guard("g", "t", "implies", (), "state.implies:g", if_=In("kind", ("a", "b")), then=IsBlank("x"))
    db = new_db(g)
    save(db, {"id": "r1", "kind": "a", "x": ""})
    save(db, {"id": "r2", "kind": "c", "x": "full"})
    save(db, {"id": "r3", "kind": None, "x": "full"})
    refused(db, "state.implies:g", {"id": "r4", "kind": "b", "x": "full"})
    refused(db, "state.implies:g", {"id": "r5", "kind": "b", "x": "   x"})


@pytest.mark.parametrize(
    "cond, row, expected",
    [
        (Eq("kind", "done"), {"kind": "done"}, True),
        (Eq("kind", "done"), {"kind": None}, False),
        (Eq("n", 1), {"n": True}, False),
        (In("kind", ("a", "b")), {"kind": "b"}, True),
        (In("kind", ("a", "b")), {"kind": "c"}, False),
        (In("kind", ("a",)), {}, False),
        (IsSet("a"), {"a": "p"}, True),
        (IsSet("a"), {"a": 0}, True),
        (IsSet("a"), {"a": ""}, False),
        (IsSet("a"), {"a": " \t\n"}, False),
        (IsSet("a"), {"a": None}, False),
        (IsSet("a"), {}, False),
        (IsSet("a"), {"a": "\u00a0"}, True),  # NBSP is not trimmed by SQL trim(): set in both
        (IsSet("a"), {"a": "   "}, False),
        (IsBlank("a"), {"a": "\u00a0"}, False),
        (IsBlank("a"), {"a": "   "}, True),
        (IsBlank("a"), {"a": "  "}, True),
        (IsBlank("a"), {"a": "p"}, False),
    ],
)
def test_conditions_evaluate_in_python_like_in_sql(cond, row, expected):
    assert cond.holds(row) is expected
    if isinstance(cond, (Eq, In)) and isinstance(row.get("n"), bool):
        return
    g = Guard("g", "t", "implies", (), "state.implies:g", if_=IsSet("id"), then=cond)
    db = new_db(g)
    full = {"id": "r1", **row}
    if expected:
        save(db, full)
    else:
        refused(db, "state.implies:g", full)


def test_sql_blank_constant_equals_what_the_dialect_renders():
    sql = SQLiteDialect()._is_set("x")
    codes = [int(c) for c in re.search(r"char\(([\d, ]+)\)", sql).group(1).split(",")]
    assert "' '" in sql
    assert " " + "".join(chr(c) for c in codes) == SQL_BLANK


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "kwargs",
    [
        dict(kind="nonsense", columns=("a",)),
        dict(kind="one_of", columns=("a",)),  # no values
        dict(kind="one_of", columns=("a",), values=([1],)),
        dict(kind="one_of", columns=("a", "b"), values=("x",)),
        dict(kind="exactly_one", columns=("a",)),
        dict(kind="required", columns=()),
        dict(kind="required", columns=("a", "b")),
        dict(kind="max_days", columns=("s", "e")),  # no days
        dict(kind="max_days", columns=("s", "e"), days=-1),
        dict(kind="max_days", columns=("s",), days=3),
        dict(kind="range", columns=("n",)),  # no bound
        dict(kind="range", columns=("n",), lo_exclusive=True, hi=3),
        dict(kind="range", columns=("n",), lo="a"),
        dict(kind="range", columns=("n", "x"), lo=1),
        dict(kind="implies", columns=()),  # no conditions
        dict(kind="implies", columns=(), if_=Eq("a", 1)),
        dict(kind="implies", columns=("a",), if_=Eq("a", 1), then=IsSet("b")),  # not the derived columns
        dict(kind="order", columns=("s",)),
    ],
)
def test_guard_validation_errors(kwargs):
    with pytest.raises(ValueError):
        Guard("g", "t", code="c", **kwargs)


def test_implies_derives_its_read_columns():
    g = Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("kind", "done"), then=In("a", ("x",)))
    assert g.read_columns == g.columns == ("kind", "a")
    assert Guard("g", "t", "implies", (), "c", if_=Eq("kind", "d"), then=IsSet("kind")).read_columns == ("kind",)


def test_new_kinds_are_registered_and_v1_guards_are_unchanged():
    assert set(GUARD_KINDS) >= {"one_of", "exactly_one", "required", "max_days", "range", "implies"}
    g = Guard("g", "t", "order", ("s", "e"), "state.period_reversed")
    assert g.read_columns == ("s", "e") and g.values == () and g.days is None


# ------------------------------------------------------------------ Literal -> one_of
def test_one_of_from_literal_values_equal_get_args():
    Status = Literal["planned", "active", "archived"]
    g = one_of_from_literal("guard_t_kind_vocab", "t", "kind", Status)
    assert g == Guard("guard_t_kind_vocab", "t", "one_of", ("kind",), "vocab.unknown",
                      values=("planned", "active", "archived"))  # fmt: skip
    assert one_of_from_literal("g", "t", "kind", Status, code="vocab.custom").code == "vocab.custom"
    db = new_db(g)
    save(db, {"id": "r1", "kind": "active"})
    refused(db, "vocab.unknown", {"id": "r2", "kind": "paused"})


def test_one_of_from_literal_needs_a_literal():
    with pytest.raises(ValueError):
        one_of_from_literal("g", "t", "kind", str)


def test_real_model_literals_work_with_one_of_from_literal():
    from typing import get_args

    from permita38.core import models

    lit = next(
        t for n, t in vars(models).items() if getattr(t, "__origin__", None) is Literal and len(get_args(t)) >= 2
    )
    assert one_of_from_literal("g", "t", "kind", lit).values == get_args(lit)


# ------------------------------------------------------------------ PostgreSQL / MariaDB strings
PG, MARIA = DIALECTS["postgresql"], DIALECTS["mariadb"]


def one_check(dialect, guard) -> str:
    stmts = dialect.guard_sql(guard)
    assert len(stmts) == 1
    q = dialect.quote
    prefix = f"ALTER TABLE {q('t')} ADD CONSTRAINT {q(guard.name)} CHECK (NOT COALESCE(("
    assert stmts[0].startswith(prefix) and stmts[0].endswith("), %s));" % dialect._false())
    return stmts[0][len(prefix) : -len("), %s));" % dialect._false())]


def test_postgres_checks():
    assert one_check(PG, G_ONE_OF).endswith("\"kind\" NOT IN ('open', 'done', 'it''s')")
    assert (
        one_check(PG, G_REQUIRED) == "NOT (\"a\" IS NOT NULL AND btrim(CAST(\"a\" AS TEXT), E' \\t\\n\\r\\f\\v') <> '')"
    )
    assert one_check(PG, G_RANGE_EXCL) == '"n" IS NOT NULL AND "n" <> \'\' AND ("n" <= 0 OR "n" > 80)'
    assert one_check(PG, G_RANGE_LO) == '"n" IS NOT NULL AND "n" <> \'\' AND ("n" < 5)'
    days = one_check(PG, G_DAYS)
    assert days.startswith("COALESCE((CASE WHEN ") and days.endswith(
        'CAST("e" AS date) - CAST("s" AS date) END) > 10, FALSE)'
    )
    ex = one_check(PG, G_EXACTLY)
    assert " AND " in ex and "OR (NOT " in ex
    imp = one_check(PG, G_IMPLIES)
    assert imp.startswith("COALESCE(\"kind\" = 'done', FALSE) AND NOT COALESCE((") and imp.endswith(", FALSE)")
    assert "NEW." not in imp


def test_mariadb_checks():
    assert one_check(MARIA, G_ONE_OF).endswith("`kind` NOT IN ('open', 'done', 'it''s')")
    assert one_check(MARIA, G_REQUIRED) == "NOT (`a` IS NOT NULL AND TRIM(BOTH '\\t\\n\\r ' FROM `a`) <> '')"
    assert one_check(MARIA, G_RANGE_EXCL) == "`n` IS NOT NULL AND `n` <> '' AND (`n` <= 0 OR `n` > 80)"
    assert one_check(MARIA, G_DAYS).endswith("DATEDIFF(`e`, `s`) END) > 10, 0)")
    assert one_check(MARIA, G_IMPLIES).startswith("COALESCE(`kind` = 'done', 0) AND NOT COALESCE((")


# ------------------------------------------------------------------ restore
def test_restore_suspends_the_new_row_local_kinds():
    assert {"one_of", "exactly_one", "required", "max_days", "range", "implies"} <= RESTORE_SUSPENDED
    assert "unique" not in RESTORE_SUSPENDED


def test_guards_suspended_lifts_new_kinds_and_puts_them_back(tmp_path, monkeypatch):
    import permita38.core.repository.sql as sql_module

    guard = Guard("guard_customers_name_required", "customers", "required", ("name",), "state.required")
    repo = make_sqlite(tmp_path)
    assert isinstance(repo, SQLiteRepository)
    repo.startup()
    try:
        monkeypatch.setattr(sql_module, "GUARDS", (*sql_module.GUARDS, guard))
        with repo.lock:
            for stmt in repo.dialect.guard_sql(guard):
                repo._exec(stmt)

        def names() -> set[str]:
            return {r[0] for r in repo._rows("SELECT name FROM sqlite_master WHERE type = 'trigger'")}

        before = names()
        assert {"guard_customers_name_required_ins", "guard_customers_name_required_upd"} <= before
        with pytest.raises(Exception) as e:
            repo.save("customers", {"name": ""})
        assert getattr(e.value, "code", None) == "state.required"
        with repo.transaction():
            with repo.guards_suspended({"required"}):
                assert not {n for n in names() if n.startswith("guard_customers_name_required")}
                repo.save("customers", {"name": " "})
            assert names() == before
        assert len(repo.list("customers")) == 1
        with pytest.raises(Exception) as e2:
            repo.save("customers", {"name": ""})
        assert getattr(e2.value, "code", None) == "state.required"
    finally:
        repo.close()
