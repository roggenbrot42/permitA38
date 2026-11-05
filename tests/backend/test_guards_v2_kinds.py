"""Guards v2, part A: the row-local kinds one_of, exactly_one, required, max_days, range and implies
(docs/specs/db-guards-v2.md, section 1). A small invented table is written through the SQLite upsert, exactly as the
repository writes. All data is invented."""

import re
import sqlite3
from typing import Literal

import pytest

from permita38.core.repository import schema
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
    fits,
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
    # a plain UPDATE statement (not an upsert) is guarded as well, for every bad row
    for row in bad_rows:
        bad = {k: v for k, v in {**EMPTY, **row}.items() if k != "id"}
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
        # parameters the kind does not use
        dict(kind="required", columns=("a",), values=("x",)),
        dict(kind="required", columns=("a",), lo=1),
        dict(kind="required", columns=("a",), days=3),
        dict(kind="one_of", columns=("a",), values=("x",), hi=3),
        dict(kind="one_of", columns=("a",), values=("x",), if_=IsSet("a")),
        dict(kind="range", columns=("n",), lo=1, values=("x",)),
        dict(kind="range", columns=("n",), lo=1, days=3),
        dict(kind="max_days", columns=("s", "e"), days=3, lo=1),
        dict(kind="exactly_one", columns=("a", "b"), lo_exclusive=True),
        dict(kind="order", columns=("s", "e"), days=3),
        dict(kind="implies", columns=(), code="state.implies:g", if_=Eq("a", "x"), then=IsSet("b"), values=("x",)),
        # identical columns
        dict(kind="exactly_one", columns=("a", "a")),
        dict(kind="order", columns=("s", "s")),
        dict(kind="max_days", columns=("s", "s"), days=3),
        # lo > hi, empty range
        dict(kind="range", columns=("n",), lo=5, hi=4),
        dict(kind="range", columns=("n",), lo=5, hi=5, lo_exclusive=True),
        # implies code must be state.implies:<name>
        dict(kind="implies", columns=(), code="state.required", if_=Eq("a", "x"), then=IsSet("b")),
        dict(kind="implies", columns=(), code="state.implies:other", if_=Eq("a", "x"), then=IsSet("b")),
    ],
)
def test_guard_validation_errors(kwargs):
    with pytest.raises(ValueError):
        Guard("g", "t", **{"code": "c", **kwargs})


def test_implies_derives_its_read_columns():
    g = Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("kind", "done"), then=In("a", ("x",)))
    assert g.read_columns == g.columns == ("kind", "a")
    assert Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("kind", "d"), then=IsSet("kind")).read_columns == (
        "kind",
    )


def test_new_kinds_are_registered_and_v1_guards_are_unchanged():
    assert set(GUARD_KINDS) >= {"one_of", "exactly_one", "required", "max_days", "range", "implies"}
    g = Guard("g", "t", "order", ("s", "e"), "state.period_reversed")
    assert g.read_columns == ("s", "e") and g.values == () and g.days is None


def test_a_range_of_lo_equal_hi_is_allowed():
    Guard("g", "t", "range", ("n",), "range.bounds", lo=5, hi=5)


@pytest.fixture
def table_t(monkeypatch):
    """Make the invented table known to schema.TABLES (literal type checks, PostgreSQL/MariaDB column types)."""
    monkeypatch.setitem(schema.TABLES, "t", TABLE)


TYPED_BAD = [
    dict(kind="one_of", columns=("kind",), values=(1,)),  # str column, int literal
    dict(kind="one_of", columns=("kind",), values=("a", 1)),
    dict(kind="one_of", columns=("n",), values=("x",)),  # float column, str literal
    dict(kind="one_of", columns=("n",), values=(True,)),  # float column, bool literal
    dict(kind="implies", columns=(), if_=Eq("kind", 1), then=IsSet("a")),
    dict(kind="implies", columns=(), if_=IsSet("a"), then=Eq("n", "5")),
    dict(kind="implies", columns=(), if_=IsSet("a"), then=In("n", (1, "5"))),
    dict(kind="implies", columns=(), if_=IsSet("a"), then=Eq("n", True)),
    dict(kind="implies", columns=(), if_=IsSet("a"), then=In("kind", (1.5,))),
]


@pytest.mark.parametrize("kwargs", TYPED_BAD)
def test_literal_type_must_match_the_column_type(table_t, kwargs):
    with pytest.raises(ValueError, match="wrong type"):
        Guard("g", "t", code="state.implies:g" if kwargs["kind"] == "implies" else "c", **kwargs)


def test_literal_of_the_right_type_is_accepted_and_unknown_tables_are_not_checked(table_t):
    Guard("g", "t", "one_of", ("kind",), "c", values=("a", "b"))
    Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("n", 1), then=In("n", (2, 3.5)))
    Guard("g", "unknown_table", "one_of", ("kind",), "c", values=(1,))


def test_literal_type_check_covers_bool_columns(monkeypatch):
    monkeypatch.setitem(schema.TABLES, "t", Table("t", (Column("id", "id"), Column("flag", "bool"))))
    Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("flag", True), then=IsSet("id"))
    with pytest.raises(ValueError, match="wrong type"):
        Guard("g", "t", "implies", (), "state.implies:g", if_=Eq("flag", 1), then=IsSet("id"))


def test_bool_versus_int_equality_on_the_sql_side():
    # Python: True is not 1. SQL stores True as 1, so a raw True in a numeric column equals 1 there. The two can only
    # differ for a value the repository never stores in a numeric column (schema.fits sends it to `extra`).
    assert Eq("n", 1).holds({"n": True}) is False
    assert fits("float", True) is False and fits("int", True) is False
    g = Guard("g", "t", "implies", (), "state.implies:g", if_=IsSet("id"), then=Eq("n", 1))
    db = new_db(g)
    save(db, {"id": "r1", "n": 1})
    save(db, {"id": "r2", "n": True})
    assert db.execute("SELECT n FROM t WHERE id = 'r2'").fetchone() == (1,)
    refused(db, "state.implies:g", {"id": "r3", "n": 2})
    refused(db, "state.implies:g", {"id": "r4", "n": False})


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


PG_DATE = (
    r"^(?!0000)(?:[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)"
    r"|02-(?:0[1-9]|1[0-9]|2[0-8]))|(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])"
    r"|(?:[02468][048]|[13579][26])00)-02-29)$"
)
PG_SET = r"""("{c}" IS NOT NULL AND btrim(CAST("{c}" AS TEXT), E' \t\n\r\f\v') <> '')"""
MA_SET = r"""(`{c}` IS NOT NULL AND `{c}` NOT REGEXP '^[\\x20\\x09-\\x0d]*$')"""
G_RANGE_HI = Guard("g_range_hi", "t", "range", ("n",), "range.bounds", hi=7.5)
G_RANGE_TEXT = Guard("g_range_text", "t", "range", ("x",), "range.bounds", lo=5, hi=9)
G_BACKSLASH = Guard("g_bs", "t", "one_of", ("x",), "vocab.unknown", values=("a\\b",))


def wrap(dialect, name, cond, false) -> str:
    q = dialect.quote
    return f"ALTER TABLE {q('t')} ADD CONSTRAINT {q(name)} CHECK (NOT COALESCE(({cond}), {false}));"


def test_postgres_checks(table_t):
    def check(guard, cond):
        assert PG.guard_sql(guard) == [wrap(PG, guard.name, cond, "FALSE")]

    a, b, k = (PG_SET.format(c=c) for c in ("a", "b", "kind"))
    check(G_ONE_OF, k + " AND \"kind\" NOT IN ('open', 'done', 'it''s')")
    check(G_BACKSLASH, PG_SET.format(c="x") + " AND \"x\" NOT IN ('a\\b')")  # standard_conforming_strings: no escape
    check(G_REQUIRED, f"NOT {a}")
    check(G_EXACTLY, f"({a} AND {b}) OR (NOT {a} AND NOT {b})")
    # numeric columns get the NULL test only ('' would raise "invalid input syntax" on PostgreSQL)
    check(G_RANGE_EXCL, '"n" IS NOT NULL AND ("n" <= 0 OR "n" > 80)')
    check(G_RANGE_LO, '"n" IS NOT NULL AND ("n" < 5)')
    check(G_RANGE_HI, '"n" IS NOT NULL AND ("n" > 7.5)')
    check(G_RANGE_TEXT, '"x" IS NOT NULL AND "x" <> \'\' AND ("x" < 5 OR "x" > 9)')
    check(
        G_DAYS,
        f"""COALESCE((CASE WHEN "s" ~ '{PG_DATE}' AND "e" ~ '{PG_DATE}' THEN CAST("e" AS date) - CAST("s" AS date) END) > 10, FALSE)""",
    )
    check(G_IMPLIES, f"""COALESCE("kind" = 'done', FALSE) AND NOT COALESCE({a}, FALSE)""")


def test_postgres_date_regex_admits_only_real_calendar_dates(table_t):
    # the regex of the CHECK must never let CAST(... AS date) raise: compare with the calendar
    import datetime

    rx = re.compile(PG_DATE)
    years = [*range(0, 2500), 2800, 3000, 9999]
    for y in years:
        for m in range(0, 14):
            for d in range(0, 33):
                try:
                    datetime.date(y, m, d)
                    real = y > 0
                except ValueError:
                    real = False
                assert bool(rx.match(f"{y:04d}-{m:02d}-{d:02d}")) is real, (y, m, d)
    for bad in ("2026-02-30", "2026-13-45", "2025-02-29", "1900-02-29", "0000-01-01", "2026-1-01", "2026-01-01 ", ""):
        assert not rx.match(bad), bad
    assert rx.match("2024-02-29") and rx.match("2000-02-29")
    assert PG_DATE in PG.guard_sql(G_DAYS)[0]


def test_mariadb_checks(table_t):
    def check(guard, cond):
        assert MARIA.guard_sql(guard) == [wrap(MARIA, guard.name, cond, "0")]

    a, b, k = (MA_SET.format(c=c) for c in ("a", "b", "kind"))
    # the default collation is case-insensitive: strings are compared as bytes (BINARY)
    check(G_ONE_OF, k + " AND BINARY `kind` NOT IN ('open', 'done', 'it''s')")
    check(G_BACKSLASH, MA_SET.format(c="x") + " AND BINARY `x` NOT IN ('a\\\\b')")  # backslash escaped
    check(G_REQUIRED, f"NOT {a}")
    check(G_EXACTLY, f"({a} AND {b}) OR (NOT {a} AND NOT {b})")
    # `0 <> ''` is false in MariaDB: numeric columns get the NULL test only
    check(G_RANGE_EXCL, "`n` IS NOT NULL AND (`n` <= 0 OR `n` > 80)")
    check(G_RANGE_LO, "`n` IS NOT NULL AND (`n` < 5)")
    check(G_RANGE_HI, "`n` IS NOT NULL AND (`n` > 7.5)")
    check(G_RANGE_TEXT, "`x` IS NOT NULL AND `x` <> '' AND (`x` < 5 OR `x` > 9)")
    date = "^[0-9]{4}-[01][0-9]-[0-3][0-9]$"
    check(
        G_DAYS,
        f"COALESCE((CASE WHEN `s` REGEXP '{date}' AND `e` REGEXP '{date}' THEN DATEDIFF(`e`, `s`) END) > 10, 0)",
    )
    check(G_IMPLIES, f"COALESCE(BINARY `kind` = 'done', 0) AND NOT COALESCE({a}, 0)")


def test_mariadb_blank_set_equals_sql_blank():
    # the regex class \x20 \x09-\x0d is exactly SQL_BLANK
    assert "".join(chr(c) for c in sorted([0x20, *range(0x09, 0x0E)])) == "".join(sorted(SQL_BLANK, key=ord))
    assert MARIA._is_set("x") == "(x IS NOT NULL AND x NOT REGEXP '^[\\\\x20\\\\x09-\\\\x0d]*$')"


def test_sqlite_range_keeps_the_blank_test_for_every_column():
    assert DIALECT.guard_bad_sql(G_RANGE_EXCL) == '"n" IS NOT NULL AND "n" <> \'\' AND ("n" <= 0 OR "n" > 80)'


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
