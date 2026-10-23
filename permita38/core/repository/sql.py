"""Backend-independent SQL repository logic.

Everything here is generic: records are encoded/decoded from the declarative schema and the
`Dialect`, so a new SQL backend only supplies the driver hooks at the bottom of `SQLRepository`
(connect, execute, begin/commit/rollback, run a migration, read the revision).
"""

import math
import os
import re
import threading
import time
from abc import abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from permita38.core.database import CURRENT_REVISION, list_migrations

from .base import ID_RE, DatabaseRevisionError, Record, RecordList, Repository, StrList, check_collection, new_id
from .dialect import Dialect
from .schema import BOOL, FLOAT, ID, INT, JSON, STR, TABLES, TEXT, Column, Table

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
    def _read_revision(self) -> int: ...

    @abstractmethod
    def _run_migration(self, version: int, name: str, sql: str) -> None:
        """Apply one migration atomically and record the new revision."""

    # ------------------------------------------------------------------ lifecycle
    def startup(self) -> None:
        with self.lock:
            if self._ready:
                return
            self._open()
            rev = self._read_revision()
            if rev > CURRENT_REVISION:
                self.close()
                raise DatabaseRevisionError(
                    f"The database is at revision {rev}, but this version of permitA38 only supports revision "
                    f"{CURRENT_REVISION}. It was probably written by a newer version (e.g. on another device). "
                    "Update permitA38 on this device; refusing to start to protect your data."
                )
            if rev < CURRENT_REVISION:
                if not self.auto_migrate:
                    self.close()
                    raise DatabaseRevisionError(
                        f"The database is at revision {rev}, expected {CURRENT_REVISION}. Run `permita38 migrate`."
                    )
                self.migrate()
            self._ready = True

    def _ensure(self) -> None:
        if not self._ready:
            self.startup()

    def close(self) -> None:
        with self.lock:
            self._close()
            self._ready = False

    def get_revision(self) -> int:
        with self.lock:
            self._open()
            return self._read_revision()

    def migrate(self) -> tuple[int, int]:
        with self.lock:
            self._open()
            old = self._read_revision()
            if old > CURRENT_REVISION:
                raise DatabaseRevisionError(
                    f"The database is at revision {old}, newer than the supported {CURRENT_REVISION}."
                )
            pending = [m for m in list_migrations(self.dialect.name) if old < m.version <= CURRENT_REVISION]
            if not pending:
                return old, old
            if old > 0:
                self._pre_migration_backup(old, pending[-1].version)
            for m in pending:
                self._run_migration(m.version, m.name, m.sql())
            return old, pending[-1].version

    def _pre_migration_backup(self, old: int, new: int) -> None:
        if self.backup_dir is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.backup(self.backup_dir / f"pre-migration-r{old}-to-r{new}-{stamp}.sqlite3")

    def health(self) -> Record:
        self._ensure()
        return {
            "backend": self.backend,
            "ok": True,
            "revision": self.get_revision(),
            "current_revision": CURRENT_REVISION,
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

    def restore_attachment(self, name: str, blob: bytes) -> bool:
        if not re.match(r"^[0-9a-z]{6,32}\.[a-z0-9]{1,5}$", name):
            raise ValueError("bad attachment name")
        folder = self.data_dir / "attachments"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if target.exists():
            return False
        tmp = folder / (name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, target)
        return True

    def remove_attachment(self, name: str) -> bool:
        path = self.attachment_path(name)
        if not path:
            return False
        os.remove(path)
        return True

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
