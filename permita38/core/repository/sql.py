"""Backend-independent SQL repository logic.

Everything here is generic: records are encoded/decoded from the declarative schema and the
`Dialect`, so a new SQL backend only supplies the driver hooks at the bottom of `SQLRepository`
(connect, execute, begin/commit/rollback, run a migration, read the revision).
"""

from __future__ import annotations

import builtins
import math
import os
import re
import threading
import time
from abc import abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from permita38.core.database import LEGACY_IDS, Migration, list_migrations

from .base import ID_RE, DatabaseRevisionError, Record, RecordList, Repository, StrList, check_collection, new_id
from .dialect import Dialect
from .schema import BOOL, FLOAT, ID, INT, JSON, STR, TABLES, TEXT, Column, Table

MIGRATIONS_TABLE = "schema_migrations"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1
_CONFLICT_MARKERS = (".sync-conflict-", "conflicted copy")


class SQLRepository(Repository):
    dialect: Dialect

    def __init__(self, data_dir: str | Path, auto_migrate: bool = True):
        self.data_dir = Path(data_dir).resolve()
        self.auto_migrate = auto_migrate
        self.lock = threading.RLock()
        self._depth = 0
        self._ready = False
        self._upserts: dict[str, str] = {}

    # ------------------------------------------------------------------ driver hooks
    @abstractmethod
    def _open(self) -> None:
        """Open the connection (idempotent)."""

    @abstractmethod
    def _close(self) -> None: ...

    @abstractmethod
    def _exec(self, sql: str, params: list[Any] | tuple = ()) -> int:
        """Run a statement, return the affected row count."""

    @abstractmethod
    def _rows(self, sql: str, params: list[Any] | tuple = ()) -> list[tuple]: ...

    @abstractmethod
    def _begin(self) -> None: ...

    @abstractmethod
    def _commit(self) -> None: ...

    @abstractmethod
    def _rollback(self) -> None: ...

    @abstractmethod
    def _run_migration(self, migration: Migration, applied_count: int) -> None:
        """Apply one migration atomically: its SQL and the `schema_migrations` row in ONE transaction
        (SQLite also sets `PRAGMA user_version = applied_count` so that old binaries refuse the database)."""

    def _read_legacy_revision(self) -> int:
        """Integer revision of a database written before `schema_migrations` existed (0 = none).

        Server backends were never released with an integer revision, so the default is 0; SQLite overrides this
        with `PRAGMA user_version`."""
        return 0

    # ------------------------------------------------------------------ migration state
    def _migrations_table_exists(self) -> bool:
        return bool(self._rows(self.dialect.table_exists_sql(), (MIGRATIONS_TABLE,)))

    def _migration_state(self, chain: list[Migration]) -> tuple[list[str], list[str], bool]:
        """(applied ids in chain order, unknown ids, applied ids come from the legacy revision).

        Read-only: a missing `schema_migrations` table means nothing is recorded yet (legacy or empty database).
        Only `migrate()` creates the table."""
        ids: set[str] = set()
        if self._migrations_table_exists():
            rows = self._rows(f"SELECT {self.dialect.quote('id')} FROM {self.dialect.quote(MIGRATIONS_TABLE)}")
            ids = {str(r[0]) for r in rows}
        legacy = False
        if not ids:
            rev = self._read_legacy_revision()
            if rev > len(LEGACY_IDS):
                return [], [f"revision {rev}"], False
            if rev > 0:
                ids, legacy = set(LEGACY_IDS[:rev]), True
        known = {m.id for m in chain}
        unknown = sorted(ids - known)
        applied = [m.id for m in chain if m.id in ids]
        return applied, unknown, legacy

    def _check_state(self, chain: list[Migration]) -> tuple[list[str], bool]:
        """Applied ids (chain order) and whether they come from the legacy revision. Refuses newer/holey databases."""
        applied, unknown, legacy = self._migration_state(chain)
        if unknown:
            self.close()
            raise DatabaseRevisionError(
                f"The database contains migrations this version of permitA38 does not know ({', '.join(unknown)}). "
                "It was probably written by a newer version (e.g. on another device). "
                "Update permitA38 on this device; refusing to start to protect your data."
            )
        if applied != [m.id for m in chain[: len(applied)]]:
            missing = [m.id for m in chain[: len(applied)] if m.id not in applied]
            self.close()
            raise DatabaseRevisionError(
                f"The applied migrations are not a prefix of the migration chain (missing: {', '.join(missing)}). "
                "A migration was skipped; refusing to continue. Restore a backup or ask for help."
            )
        return applied, legacy

    # ------------------------------------------------------------------ lifecycle
    def startup(self) -> None:
        with self.lock:
            if self._ready:
                return
            self._open()
            chain = list_migrations(self.dialect.name)
            applied, legacy = self._check_state(chain)
            pending = len(applied) < len(chain)
            if pending:
                if not self.auto_migrate:
                    self.close()
                    raise DatabaseRevisionError(
                        f"The database is at revision {applied[-1] if applied else 'none'}, "
                        f"expected {chain[-1].id}. Run `permita38 migrate`."
                    )
            if (pending or legacy) and self.auto_migrate:
                self.migrate()  # also records a legacy revision in schema_migrations when nothing is pending
            self._ready = True

    def _ensure(self) -> None:
        if not self._ready:
            self.startup()

    def close(self) -> None:
        with self.lock:
            self._close()
            self._ready = False

    def applied_ids(self) -> list[str]:
        """Ids of the applied migrations in chain order (legacy revisions included)."""
        with self.lock:
            self._open()
            return self._migration_state(list_migrations(self.dialect.name))[0]

    def get_revision(self) -> str | None:
        """Id of the last applied migration (None for an empty database)."""
        applied = self.applied_ids()
        return applied[-1] if applied else None

    def migrate(self) -> tuple[str | None, str | None, int]:
        """Apply pending migrations; returns (old id, new id, number applied)."""
        with self.lock:
            self._open()
            chain = list_migrations(self.dialect.name)
            applied, legacy = self._check_state(chain)
            old = applied[-1] if applied else None
            pending = chain[len(applied) :]
            if pending and applied:
                self._pre_migration_backup(chain[-1].id)
            self._exec(self.dialect.create_migrations_table())
            if legacy:
                self._record_applied([m for m in chain if m.id in applied])
            for i, m in enumerate(pending, start=len(applied) + 1):
                self._run_migration(m, i)
            return old, chain[-1].id if chain else None, len(pending)

    def _record_applied(self, migrations: list[Migration]) -> None:
        """Insert bookkeeping rows for already-applied (legacy) migrations in one transaction."""
        q = self.dialect.quote
        ph = self.dialect.placeholder
        stmt = (
            f"INSERT INTO {q('schema_migrations')} ({q('id')}, {q('name')}, {q('applied_at')}) "
            f"VALUES ({ph}, {ph}, {ph})"
        )
        self._begin()
        try:
            for m in migrations:
                self._exec(stmt, (m.id, m.name, _now()))
        except BaseException:
            self._rollback()
            raise
        self._commit()

    def _pre_migration_backup(self, head: str) -> None:
        if self.backup_dir is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.backup(self.backup_dir / f"pre-migration-{stamp}-before-{head}.sqlite3")

    def health(self) -> Record:
        # Read-only: reports the state without migrating (startup() / migrate() do that).
        self._open()
        chain = list_migrations(self.dialect.name)
        applied = self.applied_ids()
        return {
            "backend": self.backend,
            "ok": True,
            "revision": applied[-1] if applied else None,
            "current_revision": chain[-1].id if chain else None,
            "applied": len(applied),
        }

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic and isolated. Nested use joins the outer transaction (no savepoints)."""
        with self.lock:
            self._ensure()
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self._begin()
            self._depth = 1
            try:
                yield
            except BaseException:
                self._depth = 0
                self._rollback()
                raise
            else:
                self._depth = 0
                self._commit()

    # ------------------------------------------------------------------ value coding
    def _coerce(self, col: Column, v: Any) -> tuple[bool, Any]:
        """(ok, database value). Values that do not fit the column type go to the `extra` JSON instead."""
        t, d = col.type, self.dialect
        if t in (ID, STR, TEXT):
            return isinstance(v, str), v
        if t == INT:
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return (isinstance(v, int) and not isinstance(v, bool) and _INT_MIN <= v <= _INT_MAX), v
        if t == FLOAT:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            return ok, (float(v) if ok else v)
        if t == BOOL:
            return isinstance(v, bool), (d.encode_bool(v) if isinstance(v, bool) else v)
        if t == JSON:
            return True, d.encode_json(v)
        return False, v

    def _encode(self, table: Table, obj: Record) -> dict[str, Any]:
        values: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in obj.items():
            if v is None:
                continue
            col = table.column(k)
            if col is None:
                extra[k] = v
                continue
            ok, db = self._coerce(col, v)
            if ok:
                values[k] = db
            else:
                extra[k] = v
        if table.extra_column and extra:
            values[table.extra_column] = self.dialect.encode_json(extra)
        return values

    def _decode(self, table: Table, row: list[Any] | tuple) -> Record:
        d = self.dialect
        out: Record = {}
        extra = None
        for name, v in zip(d.all_columns(table), row):
            if v is None:
                continue
            if name == table.extra_column:
                extra = d.decode_json(v)
                continue
            t = table.column(name).type  # type: ignore[union-attr]
            if t == JSON:
                v = d.decode_json(v)
            elif t == BOOL:
                v = d.decode_bool(v)
            elif t == INT:
                v = int(v)
            elif t == FLOAT:
                v = float(v)
            out[name] = v
        for k, v in (extra or {}).items():
            out.setdefault(k, v)
        return out

    # ------------------------------------------------------------------ records
    def _upsert_sql(self, table: Table) -> str:
        if table.name not in self._upserts:
            self._upserts[table.name] = self.dialect.upsert(table, self.dialect.all_columns(table))
        return self._upserts[table.name]

    def list(self, coll: str) -> RecordList:
        check_collection(coll)
        table = TABLES[coll]
        q = self.dialect.quote
        with self.lock:
            self._ensure()
            rows = self._rows(self.dialect.select_all(table, f"{q('created')}, {q('id')}"))
        return [self._decode(table, r) for r in rows]

    def _where(self, table: Table, equals: dict[str, Any]) -> tuple[str, builtins.list[Any]]:
        """``WHERE`` clause for equality on plain columns (JSON columns and unknown names are rejected)."""
        q = self.dialect.quote
        parts: list[str] = []
        params: builtins.list[Any] = []
        for name, value in equals.items():
            col = table.column(name)
            if col is None or col.type == JSON:
                raise ValueError(f"cannot filter {table.name} by {name!r}")
            ok, db = self._coerce(col, value)
            if not ok:
                raise ValueError(f"bad value for {name!r}")
            parts.append(f"{q(name)} = {self.dialect.placeholder}")
            params.append(db)
        return (" WHERE " + " AND ".join(parts)) if parts else "", params

    def find(self, coll: str, **equals: Any) -> RecordList:
        check_collection(coll)
        table = TABLES[coll]
        q = self.dialect.quote
        where, params = self._where(table, equals)
        sql = self.dialect.select_all(table, f"{q('created')}, {q('id')}")
        marker = " ORDER BY"
        head, sep, tail = sql.partition(marker)
        with self.lock:
            self._ensure()
            rows = self._rows(head + where + sep + tail, params)
        return [self._decode(table, r) for r in rows]

    def delete_where(self, coll: str, **equals: Any) -> int:
        check_collection(coll)
        table = TABLES[coll]
        where, params = self._where(table, equals)
        if not where:
            raise ValueError("delete_where needs at least one condition")
        with self.transaction():
            return self._exec(f"DELETE FROM {self.dialect.quote(table.name)}{where}", params)

    def get(self, coll: str, rid: str) -> Record | None:
        check_collection(coll)
        if not isinstance(rid, str) or not ID_RE.match(rid):
            return None
        table = TABLES[coll]
        with self.lock:
            self._ensure()
            rows = self._rows(self.dialect.select_one(table), [rid])
        return self._decode(table, rows[0]) if rows else None

    def save(self, coll: str, obj: Record, touch: bool = True) -> Record:
        check_collection(coll)
        table = TABLES[coll]
        obj = dict(obj)
        rid = obj.get("id") or new_id()
        if not isinstance(rid, str) or not ID_RE.match(rid):
            raise ValueError("bad id")
        now = int(time.time())
        with self.transaction():
            if not obj.get("created"):
                old = self._rows(
                    f"SELECT {self.dialect.quote('created')} FROM {self.dialect.quote(table.name)} "
                    f"WHERE {self.dialect.quote(table.pk)} = {self.dialect.placeholder}",
                    [rid],
                )
                obj["created"] = (old[0][0] if old and old[0][0] else None) or now
            obj["id"] = rid
            if touch or not obj.get("updated"):
                obj["updated"] = now
            values = self._encode(table, obj)
            names = self.dialect.all_columns(table)
            row = [values.get(c) for c in names]
            self._exec(self._upsert_sql(table), row)
        return self._decode(table, row)

    def delete(self, coll: str, rid: str) -> bool:
        check_collection(coll)
        if not isinstance(rid, str) or not ID_RE.match(rid):
            raise ValueError("bad id")
        with self.transaction():
            return self._exec(self.dialect.delete_one(TABLES[coll]), [rid]) > 0

    # ------------------------------------------------------------------ settings
    def settings(self) -> Record:
        d, table = self.dialect, TABLES["settings"]
        with self.lock:
            self._ensure()
            rows = self._rows(f"SELECT {d.quote('key')}, {d.quote('value')} FROM {d.quote(table.name)}")
        return {k: d.decode_json(v) for k, v in rows if v is not None}

    def save_settings(self, data: Record) -> None:
        d, table = self.dialect, TABLES["settings"]
        with self.transaction():
            self._exec(f"DELETE FROM {d.quote(table.name)}")
            for k, v in data.items():
                if v is not None:
                    self._exec(self._upsert_sql(table), [str(k), d.encode_json(v)])

    # ------------------------------------------------------------------ attachments (files)
    def save_attachment(self, filename: str, blob: bytes) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,5}$", ext):
            ext = ".bin"
        name = new_id() + ext
        folder = self.data_dir / "attachments"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, folder / name)
        return name

    def attachment_path(self, name: str) -> str | None:
        if not re.match(r"^[0-9a-z]{6,32}\.[a-z0-9]{1,5}$", name):
            return None
        p = self.data_dir / "attachments" / name
        return str(p) if p.is_file() else None

    # ------------------------------------------------------------------ sync conflicts
    def conflicts(self) -> StrList:
        found: list[str] = []
        if self.data_dir.is_dir():
            for dp, _, files in os.walk(self.data_dir):
                found += [
                    os.path.relpath(os.path.join(dp, f), self.data_dir)
                    for f in sorted(files)
                    if any(m in f for m in _CONFLICT_MARKERS)
                ]
        return found
