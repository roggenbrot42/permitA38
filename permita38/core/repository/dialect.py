"""SQL dialects: everything that differs between SQLite, PostgreSQL and MariaDB.

A `Dialect` turns the neutral description in `schema.py` into DDL, upserts and value conversions.
All of it is plain string generation, so it is unit-tested without any database driver. Only SQLite
is wired to a real driver today (see `sqlite.py`); the PostgreSQL and MariaDB classes prepare the port
(see docs/porting-to-postgres.md).
"""

import json
from typing import Any

from .schema import (
    BOOL,
    FLOAT,
    ID,
    INT,
    JSON,
    STR,
    TEXT,
    Column,
    Condition,
    Eq,
    Guard,
    In,
    Index,
    IsBlank,
    IsSet,
    Table,
    column_type,
)

_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]"
_DATE_REGEX = "^[0-9]{4}-[01][0-9]-[0-3][0-9]$"

# Real calendar dates only (PostgreSQL's CAST(... AS date) raises on 2026-02-30 and on year 0000), so a CHECK never raises.
_LEAP_YEAR = "(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|(?:[02468][048]|[13579][26])00)"
_CALENDAR_DATE_REGEX = (
    "^(?!0000)(?:[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)"
    "|02-(?:0[1-9]|1[0-9]|2[0-8]))"
    f"|{_LEAP_YEAR}-02-29)$"
)

#: guard kinds added in guards v2 that are row-local (rendered by `Dialect.guard_bad_sql`)
ROW_LOCAL_KINDS = frozenset({"one_of", "exactly_one", "required", "max_days", "range", "implies"})


class Dialect:
    name = "generic"
    placeholder = "?"
    quote_char = '"'
    #: logical type -> SQL type
    types: dict[str, str] = {}
    #: SQL fragment used for a JSON value placeholder (e.g. PostgreSQL needs a cast)
    json_placeholder = "?"

    def quote(self, ident: str) -> str:
        q = self.quote_char
        return f"{q}{ident.replace(q, q + q)}{q}"

    def sql_type(self, column: Column) -> str:
        return self.types[column.type]

    def value_placeholder(self, column: Column) -> str:
        return self.json_placeholder if column.type == JSON else self.placeholder

    # ---- DDL ----
    def create_table(self, table: Table) -> str:
        cols = []
        for c in table.columns:
            line = f"    {self.quote(c.name)} {self.sql_type(c)}"
            if c.name == table.pk:
                line += " PRIMARY KEY"
            cols.append(line)
        if table.extra_column and not table.column(table.extra_column):
            cols.append(f"    {self.quote(table.extra_column)} {self.types[JSON]}")
        return f"CREATE TABLE {self.quote(table.name)} (\n" + ",\n".join(cols) + "\n);"

    #: schema expression for information_schema lookups (SQLite overrides table_exists_sql)
    current_schema_sql = "current_schema()"

    def table_exists_sql(self) -> str:
        """SELECT returning one row when the table named by the single placeholder exists in the current schema."""
        return (
            "SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = {self.current_schema_sql} AND table_name = {self.placeholder}"
        )

    def create_migrations_table(self) -> str:
        """Bookkeeping table of applied migrations; created by the runner itself, not by a migration file."""
        q = self.quote
        return (
            f"CREATE TABLE IF NOT EXISTS {q('schema_migrations')} (\n"
            f"    {q('id')} {self.types[ID]} PRIMARY KEY,\n"
            f"    {q('name')} {self.types[STR]} NOT NULL,\n"
            f"    {q('applied_at')} {self.types[STR]} NOT NULL\n);"
        )

    def create_index(self, table: Table, index: Index) -> str:
        unique = "UNIQUE " if index.unique else ""
        cols = ", ".join(self.quote(c) for c in index.columns)
        return f"CREATE {unique}INDEX {self.quote(index.name)} ON {self.quote(table.name)} ({cols});"

    # ---- row-local guard conditions (guards v2), rendered from the AST in schema.py
    def _ref(self, col: str, prefix: str) -> str:
        """Column reference; the prefix is "NEW." in a SQLite trigger and empty in a CHECK constraint."""
        return prefix + self.quote(col)

    def _literal(self, v: Any) -> str:
        if isinstance(v, bool):
            return str(int(v))
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        raise ValueError(f"unsupported condition value {v!r}")

    def _is_set(self, ref: str) -> str:
        """SQL boolean: not NULL, not '' and not only whitespace."""
        raise NotImplementedError

    def _days_between(self, start: str, end: str) -> str:
        """SQL number: end - start in days (NULL when either is not a valid date)."""
        raise NotImplementedError

    def _false(self) -> str:
        return "0"

    def _exact(self, ref: str, values: tuple[Any, ...]) -> str:
        """Reference used in an exact equality / IN test (MariaDB overrides it for its case-insensitive collation)."""
        return ref

    def _in_list(self, ref: str, values: tuple[Any, ...], negate: bool = False) -> str:
        lits = ", ".join(self._literal(v) for v in values)
        return f"{self._exact(ref, values)} {'NOT IN' if negate else 'IN'} ({lits})"

    def _range_blank_test(self, guard: Guard) -> bool:
        """Does a range guard also exclude '' (text columns)? SQLite compares loosely, so it always does."""
        return True

    def cond_sql(self, cond: Condition, prefix: str = "") -> str:
        ref = self._ref(cond.col, prefix)
        if isinstance(cond, Eq):
            return f"{self._exact(ref, (cond.value,))} = {self._literal(cond.value)}"
        if isinstance(cond, In):
            return self._in_list(ref, cond.values)
        if isinstance(cond, IsSet):
            return self._is_set(ref)
        if isinstance(cond, IsBlank):
            return f"NOT {self._is_set(ref)}"
        raise ValueError(f"unknown condition {cond!r}")

    def guard_bad_sql(self, guard: Guard, prefix: str = "") -> str:
        """SQL boolean that is true when a row violates a guard of one of the new row-local kinds."""
        lit, false = self._literal, self._false()
        cols = [self._ref(c, prefix) for c in guard.columns]
        x = cols[0]
        if guard.kind == "one_of":
            return f"{self._is_set(x)} AND {self._in_list(x, guard.values, negate=True)}"
        if guard.kind == "exactly_one":
            a, b = self._is_set(cols[0]), self._is_set(cols[1])
            return f"({a} AND {b}) OR (NOT {a} AND NOT {b})"
        if guard.kind == "required":
            return f"NOT {self._is_set(x)}"
        if guard.kind == "max_days":
            return f"COALESCE({self._days_between(cols[0], cols[1])} > {guard.days}, {false})"
        if guard.kind == "range":
            parts = []
            if guard.lo is not None:
                parts.append(f"{x} {'<=' if guard.lo_exclusive else '<'} {lit(guard.lo)}")
            if guard.hi is not None:
                parts.append(f"{x} > {lit(guard.hi)}")
            blank = f" AND {x} <> ''" if self._range_blank_test(guard) else ""
            return f"{x} IS NOT NULL{blank} AND ({' OR '.join(parts)})"
        if guard.kind == "implies":
            assert guard.if_ is not None and guard.then is not None
            return (
                f"COALESCE({self.cond_sql(guard.if_, prefix)}, {false}) "
                f"AND NOT COALESCE({self.cond_sql(guard.then, prefix)}, {false})"
            )
        raise ValueError(f"unknown guard kind {guard.kind!r}")

    def _check_guard_sql(self, guard: Guard) -> list[str]:
        """Native CHECK constraint for the row-local new kinds (PostgreSQL, MariaDB)."""
        cond = f"NOT COALESCE(({self.guard_bad_sql(guard)}), {self._false()})"
        return [f"ALTER TABLE {self.quote(guard.table)} ADD CONSTRAINT {self.quote(guard.name)} CHECK ({cond});"]

    def guard_sql(self, guard: Guard) -> list[str]:
        """DDL statements (each ending with ';') that create the guard."""
        raise NotImplementedError

    def ddl(self, tables: dict[str, Table]) -> str:
        out = []
        for t in tables.values():
            out.append(self.create_table(t))
            out += [self.create_index(t, i) for i in t.indexes]
            out.append("")
        return "\n".join(out)

    # ---- DML ----
    def upsert(self, table: Table, columns: list[str]) -> str:
        """Insert or fully overwrite the row with the same primary key."""
        raise NotImplementedError

    def select_all(self, table: Table, order_by: str = "") -> str:
        cols = ", ".join(self.quote(c) for c in self._all_columns(table))
        return f"SELECT {cols} FROM {self.quote(table.name)}" + (f" ORDER BY {order_by}" if order_by else "")

    def select_one(self, table: Table) -> str:
        return self.select_all(table) + f" WHERE {self.quote(table.pk)} = {self.placeholder}"

    def delete_one(self, table: Table) -> str:
        return f"DELETE FROM {self.quote(table.name)} WHERE {self.quote(table.pk)} = {self.placeholder}"

    def _all_columns(self, table: Table) -> list[str]:
        cols = table.column_names
        if table.extra_column and table.extra_column not in cols:
            cols.append(table.extra_column)
        return cols

    def all_columns(self, table: Table) -> list[str]:
        return self._all_columns(table)

    def _insert_head(self, table: Table, columns: list[str]) -> str:
        names = ", ".join(self.quote(c) for c in columns)
        values = ", ".join(self.value_placeholder(table.column(c) or Column(c, JSON)) for c in columns)
        return f"INSERT INTO {self.quote(table.name)} ({names}) VALUES ({values})"

    # ---- value conversion (driver independent) ----
    def encode_json(self, value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def decode_json(self, value: Any) -> Any:
        return json.loads(value) if isinstance(value, (str, bytes)) else value

    def encode_bool(self, value: bool) -> Any:
        return int(value)

    def decode_bool(self, value: Any) -> bool:
        return bool(value)


class SQLiteDialect(Dialect):
    name = "sqlite"
    types = {ID: "TEXT", STR: "TEXT", TEXT: "TEXT", INT: "INTEGER", FLOAT: "REAL", BOOL: "INTEGER", JSON: "TEXT"}

    def _is_set(self, ref: str) -> str:
        return f"({ref} IS NOT NULL AND trim({ref}, ' ' || char(9, 10, 11, 12, 13)) <> '')"

    def _days_between(self, start: str, end: str) -> str:
        ok = " AND ".join(f"{x} GLOB '{_DATE_GLOB}' AND julianday({x}) IS NOT NULL" for x in (start, end))
        return f"(CASE WHEN {ok} THEN julianday({end}) - julianday({start}) END)"

    def table_exists_sql(self) -> str:
        return "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"

    def upsert(self, table: Table, columns: list[str]) -> str:
        updates = ", ".join(f"{self.quote(c)} = excluded.{self.quote(c)}" for c in columns if c != table.pk)
        return self._insert_head(table, columns) + f" ON CONFLICT ({self.quote(table.pk)}) DO UPDATE SET {updates}"

    def guard_sql(self, guard: Guard) -> list[str]:
        """The BEFORE INSERT and BEFORE UPDATE OF triggers of a guard (named `<guard.name>_ins` / `_upd`).

        They fire only for new or changed values, so rows that already violate the rule stay writable (grandfathered).
        The repository writes with an upsert, so the INSERT trigger fires on every re-save: it skips a row whose
        guarded values equal the stored ones."""
        q, t = self.quote, self.quote(guard.table)
        cols = [q(c) for c in guard.read_columns]
        c = cols[0]
        if guard.kind in ROW_LOCAL_KINDS:
            bad = f"({self.guard_bad_sql(guard, 'NEW.')})"  # parenthesised: it may contain a top-level OR
        elif guard.kind == "unique":
            bad = (
                f"NEW.{c} IS NOT NULL AND NEW.{c} <> '' "
                f'AND EXISTS (SELECT 1 FROM {t} WHERE {c} = NEW.{c} AND "id" <> NEW."id")'
            )
        elif guard.kind == "date":
            bad = f"NEW.{c} IS NOT NULL AND NEW.{c} <> '' AND NOT (NEW.{c} GLOB '{_DATE_GLOB}')"
        elif guard.kind == "non_negative":
            bad = f"NEW.{c} IS NOT NULL AND NEW.{c} < 0"
        elif guard.kind == "order":
            e = cols[1]
            bad = (
                f"NEW.{c} IS NOT NULL AND NEW.{c} <> '' AND NEW.{e} IS NOT NULL AND NEW.{e} <> '' AND NEW.{e} < NEW.{c}"
            )
        else:
            raise ValueError(f"unknown guard kind {guard.kind!r}")
        same = " AND ".join(f"{x} IS NEW.{x}" for x in cols)
        unchanged = f'EXISTS (SELECT 1 FROM {t} WHERE "id" = NEW."id" AND {same})'
        changed = " OR ".join(f"NEW.{x} IS NOT OLD.{x}" for x in cols)
        raise_ = f"BEGIN SELECT RAISE(ABORT, 'permita38:{guard.code}'); END;"
        return [
            f"CREATE TRIGGER {q(guard.name + '_ins')} BEFORE INSERT ON {t}\nWHEN {bad}\n AND NOT {unchanged}\n{raise_}",
            f"CREATE TRIGGER {q(guard.name + '_upd')} BEFORE UPDATE OF {', '.join(cols)} ON {t}\n"
            f"WHEN {bad}\n AND ({changed})\n{raise_}",
        ]


class PostgresDialect(Dialect):
    """PostgreSQL (psycopg-style `%s` placeholders). Prepared, not wired to a driver yet."""

    name = "postgresql"
    placeholder = "%s"
    json_placeholder = "%s::jsonb"
    types = {
        ID: "TEXT",
        STR: "TEXT",
        TEXT: "TEXT",
        INT: "BIGINT",
        FLOAT: "DOUBLE PRECISION",
        BOOL: "BOOLEAN",
        JSON: "JSONB",
    }

    def _is_set(self, ref: str) -> str:
        return f"({ref} IS NOT NULL AND btrim(CAST({ref} AS TEXT), E' \\t\\n\\r\\f\\v') <> '')"

    def _days_between(self, start: str, end: str) -> str:
        # CAST(... AS date) raises on 2026-02-30, so the regex admits real calendar dates only (never raises)
        ok = " AND ".join(f"{x} ~ '{_CALENDAR_DATE_REGEX}'" for x in (start, end))
        return f"(CASE WHEN {ok} THEN CAST({end} AS date) - CAST({start} AS date) END)"

    def _range_blank_test(self, guard: Guard) -> bool:
        # `numeric <> ''` raises "invalid input syntax" on PostgreSQL: only text columns get the blank test
        return column_type(guard.table, guard.columns[0]) in (ID, STR, TEXT)

    def _false(self) -> str:
        return "FALSE"

    def upsert(self, table: Table, columns: list[str]) -> str:
        updates = ", ".join(f"{self.quote(c)} = EXCLUDED.{self.quote(c)}" for c in columns if c != table.pk)
        return self._insert_head(table, columns) + f" ON CONFLICT ({self.quote(table.pk)}) DO UPDATE SET {updates}"

    def guard_sql(self, guard: Guard) -> list[str]:
        """Native constraints. Unlike the SQLite triggers they validate existing rows (docs/porting-to-postgres.md)."""
        q, t = self.quote, self.quote(guard.table)
        cols = [q(c) for c in guard.columns]
        c = cols[0]
        if guard.kind in ROW_LOCAL_KINDS:
            return self._check_guard_sql(guard)
        if guard.kind == "unique":
            return [f"CREATE UNIQUE INDEX {q(guard.name)} ON {t} ({c}) WHERE {c} IS NOT NULL AND {c} <> '';"]
        if guard.kind == "date":
            cond = f"{c} IS NULL OR {c} = '' OR {c} ~ '{_DATE_REGEX}'"
        elif guard.kind == "non_negative":
            cond = f"{c} IS NULL OR {c} >= 0"
        elif guard.kind == "order":
            e = cols[1]
            cond = f"{c} IS NULL OR {c} = '' OR {e} IS NULL OR {e} = '' OR {e} >= {c}"
        else:
            raise ValueError(f"unknown guard kind {guard.kind!r}")
        return [f"ALTER TABLE {t} ADD CONSTRAINT {q(guard.name)} CHECK ({cond});"]

    def encode_bool(self, value: bool) -> Any:
        return bool(value)


class MariaDBDialect(Dialect):
    """MariaDB / MySQL (`%s` placeholders, backtick quoting). Prepared, not wired to a driver yet."""

    name = "mariadb"
    placeholder = "%s"
    json_placeholder = "%s"
    quote_char = "`"
    current_schema_sql = "DATABASE()"
    types = {
        ID: "VARCHAR(32)",
        STR: "VARCHAR(255)",
        TEXT: "LONGTEXT",
        INT: "BIGINT",
        FLOAT: "DOUBLE",
        BOOL: "TINYINT(1)",
        JSON: "LONGTEXT",
    }

    def _literal(self, v: Any) -> str:
        if isinstance(v, str):  # the default sql_mode treats the backslash as an escape character
            return "'" + v.replace("\\", "\\\\").replace("'", "''") + "'"
        return super()._literal(v)

    def _exact(self, ref: str, values: tuple[Any, ...]) -> str:
        # the default collation (utf8mb4_general_ci) is case-insensitive: compare strings as bytes
        return f"BINARY {ref}" if any(isinstance(v, str) for v in values) else ref

    def _range_blank_test(self, guard: Guard) -> bool:
        # `0 <> ''` is false in MariaDB ('' coerces to 0): only text columns get the blank test
        return column_type(guard.table, guard.columns[0]) in (ID, STR, TEXT)

    def _is_set(self, ref: str) -> str:
        # the same set as schema.SQL_BLANK (space, \t \n \v \f \r); TRIM(BOTH ...) cannot strip \v and \f
        return f"({ref} IS NOT NULL AND {ref} NOT REGEXP '^[\\\\x20\\\\x09-\\\\x0d]*$')"

    def _days_between(self, start: str, end: str) -> str:
        ok = " AND ".join(f"{x} REGEXP '{_DATE_REGEX}'" for x in (start, end))
        return f"(CASE WHEN {ok} THEN DATEDIFF({end}, {start}) END)"

    def sql_type(self, column: Column) -> str:
        # primary keys / index keys must be bounded strings in MariaDB
        return self.types[column.type]

    def upsert(self, table: Table, columns: list[str]) -> str:
        updates = ", ".join(f"{self.quote(c)} = VALUES({self.quote(c)})" for c in columns if c != table.pk)
        return self._insert_head(table, columns) + f" ON DUPLICATE KEY UPDATE {updates}"

    def guard_sql(self, guard: Guard) -> list[str]:
        """Native constraints (a generated column carries the unique key). Unlike the SQLite triggers they validate existing rows (docs/porting-to-postgres.md)."""
        q, t = self.quote, self.quote(guard.table)
        cols = [q(c) for c in guard.columns]
        c = cols[0]
        if guard.kind in ROW_LOCAL_KINDS:
            return self._check_guard_sql(guard)
        if guard.kind == "unique":
            key = q(guard.columns[0] + "_key")
            return [
                f"ALTER TABLE {t} ADD COLUMN {key} VARCHAR(255) AS (NULLIF({c}, '')) VIRTUAL;",
                f"CREATE UNIQUE INDEX {q(guard.name)} ON {t} ({key});",
            ]
        if guard.kind == "date":
            cond = f"{c} IS NULL OR {c} = '' OR {c} REGEXP '{_DATE_REGEX}'"
        elif guard.kind == "non_negative":
            cond = f"{c} IS NULL OR {c} >= 0"
        elif guard.kind == "order":
            e = cols[1]
            cond = f"{c} IS NULL OR {c} = '' OR {e} IS NULL OR {e} = '' OR {e} >= {c}"
        else:
            raise ValueError(f"unknown guard kind {guard.kind!r}")
        return [f"ALTER TABLE {t} ADD CONSTRAINT {q(guard.name)} CHECK ({cond});"]

    def create_table(self, table: Table) -> str:
        return super().create_table(table).rstrip(";") + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"


DIALECTS: dict[str, Dialect] = {d.name: d for d in (SQLiteDialect(), PostgresDialect(), MariaDBDialect())}


def initial_migration_sql(dialect_name: str) -> str:
    """Content of `0001_initial.sql` for a dialect (revision 1 snapshot of the neutral schema)."""
    from .schema import TABLES

    return (
        f"-- permitA38 migration 0001: initial schema ({dialect_name})\n-- generated from core/repository/schema.py\n\n"
        + DIALECTS[dialect_name].ddl(TABLES)
    )
