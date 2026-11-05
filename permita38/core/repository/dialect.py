"""SQL dialects: everything that differs between SQLite, PostgreSQL and MariaDB.

A `Dialect` turns the neutral description in `schema.py` into DDL, upserts and value conversions.
All of it is plain string generation, so it is unit-tested without any database driver. Only SQLite
is wired to a real driver today (see `sqlite.py`); the PostgreSQL and MariaDB classes prepare the port
(see docs/porting-to-postgres.md).
"""

import json
from typing import Any

from .schema import BOOL, FLOAT, ID, INT, JSON, STR, TEXT, Column, Guard, Index, Table

_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]"
_DATE_REGEX = "^[0-9]{4}-[01][0-9]-[0-3][0-9]$"


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
        cols = [q(c) for c in guard.columns]
        c = cols[0]
        if guard.kind == "unique":
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

    def upsert(self, table: Table, columns: list[str]) -> str:
        updates = ", ".join(f"{self.quote(c)} = EXCLUDED.{self.quote(c)}" for c in columns if c != table.pk)
        return self._insert_head(table, columns) + f" ON CONFLICT ({self.quote(table.pk)}) DO UPDATE SET {updates}"

    def guard_sql(self, guard: Guard) -> list[str]:
        """Native constraints. Unlike the SQLite triggers they validate existing rows (docs/porting-to-postgres.md)."""
        q, t = self.quote, self.quote(guard.table)
        cols = [q(c) for c in guard.columns]
        c = cols[0]
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
