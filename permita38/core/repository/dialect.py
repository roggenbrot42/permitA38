"""SQL dialects: everything that differs between SQLite, PostgreSQL and MariaDB.

A `Dialect` turns the neutral description in `schema.py` into DDL, upserts and value conversions.
All of it is plain string generation, so it is unit-tested without any database driver. Only SQLite
is wired to a real driver today (see `sqlite.py`); the PostgreSQL and MariaDB classes prepare the port
(see docs/porting-to-postgres.md).
"""

import json
from typing import Any

from .schema import BOOL, FLOAT, ID, INT, JSON, STR, TEXT, Column, Index, Table


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

    def create_index(self, table: Table, index: Index) -> str:
        unique = "UNIQUE " if index.unique else ""
        cols = ", ".join(self.quote(c) for c in index.columns)
        return f"CREATE {unique}INDEX {self.quote(index.name)} ON {self.quote(table.name)} ({cols});"

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

    def upsert(self, table: Table, columns: list[str]) -> str:
        updates = ", ".join(f"{self.quote(c)} = excluded.{self.quote(c)}" for c in columns if c != table.pk)
        return self._insert_head(table, columns) + f" ON CONFLICT ({self.quote(table.pk)}) DO UPDATE SET {updates}"


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

    def encode_bool(self, value: bool) -> Any:
        return bool(value)


class MariaDBDialect(Dialect):
    """MariaDB / MySQL (`%s` placeholders, backtick quoting). Prepared, not wired to a driver yet."""

    name = "mariadb"
    placeholder = "%s"
    json_placeholder = "%s"
    quote_char = "`"
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
