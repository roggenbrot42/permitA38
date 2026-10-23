"""SQLite backend (the default).

One database file inside the (synced) data folder. Choices that matter for syncing with Syncthing & co:
rollback journal instead of WAL (a single file, no `-wal`/`-shm` side files), `synchronous=FULL`, short
transactions, atomic backups. One shared connection guarded by a lock: simple and fast enough for a
single-user/small-team app.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from permita38.core.database import CURRENT_REVISION

from .base import Record
from .dialect import SQLiteDialect
from .sql import SQLRepository

DB_FILENAME = "permita38.sqlite3"


class SQLiteRepository(SQLRepository):
    backend = "sqlite"
    dialect = SQLiteDialect()

    def __init__(
        self,
        data_dir: str | Path,
        db_path: str | Path | None = None,
        wal: bool = False,
        auto_migrate: bool = True,
    ):
        super().__init__(data_dir, auto_migrate)
        p = Path(db_path) if db_path else Path(DB_FILENAME)
        self.db_path = p if p.is_absolute() else self.data_dir / p
        self.wal = wal
        self._conn: sqlite3.Connection | None = None

    # ---- driver hooks ----
    def _open(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "attachments").mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = %s" % ("WAL" if self.wal else "DELETE"))
        conn.execute("PRAGMA synchronous = FULL")
        self._conn = conn

    def _close(self) -> None:
        if self._conn is None:
            return
        try:
            if self.wal:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        assert self._conn is not None
        return self._conn

    def _exec(self, sql: str, params: list[Any] | tuple = ()) -> int:
        return self.conn.execute(sql, params).rowcount

    def _rows(self, sql: str, params: list[Any] | tuple = ()) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    def _begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self.conn.execute("COMMIT")

    def _rollback(self) -> None:
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")

    def _read_revision(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def _run_migration(self, version: int, name: str, sql: str) -> None:
        script = f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {int(version)};\nCOMMIT;"
        try:
            self.conn.executescript(script)
        except sqlite3.Error as e:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise RuntimeError(f"Migration {version:04d}_{name} failed and was rolled back: {e}") from e

    # ---- operations ----
    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def backup(self, dest: Path | None = None) -> Path:
        """Consistent copy of the database (sqlite3 online backup API); written atomically."""
        dest = Path(dest) if dest else self.backup_dir / time.strftime("permita38-%Y%m%d-%H%M%S.sqlite3")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        with self.lock:
            self._open()
            out = sqlite3.connect(tmp)
            try:
                self.conn.backup(out)
            finally:
                out.close()
        os.replace(tmp, dest)
        return dest

    def file_root(self) -> Path:
        return self.data_dir

    @property
    def snapshot_name(self) -> str:
        return DB_FILENAME

    @staticmethod
    def _open_readonly(path: Path) -> sqlite3.Connection:
        uri = "file:" + Path(path).resolve().as_posix().replace("?", "%3f").replace("#", "%23") + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5)

    def verify_snapshot(self, path: Path) -> list[str]:
        problems: list[str] = []
        try:
            conn = self._open_readonly(path)
        except sqlite3.Error as e:
            return [f"cannot open the database snapshot: {e}"]
        try:
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
            if rows != ["ok"]:
                problems.append("integrity_check failed: " + "; ".join(rows[:5]))
            rev = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if rev > CURRENT_REVISION:
                problems.append(f"the snapshot has schema revision {rev}, newer than this version ({CURRENT_REVISION})")
        except sqlite3.Error as e:
            problems.append(f"the snapshot is not a readable database: {e}")
        finally:
            conn.close()
        return problems

    def snapshot_counts(self, path: Path) -> dict[str, int]:
        conn = self._open_readonly(path)
        try:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            return {
                n: int(conn.execute('SELECT COUNT(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0]) for n in names
            }
        finally:
            conn.close()

    def snapshot_invariants(self, path: Path) -> list[str]:
        """If the accounting tables exist, every journal entry must balance (debit == credit, in cents)."""
        conn = self._open_readonly(path)
        problems: list[str] = []
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "journal_lines" in tables:
                cols = {r[1] for r in conn.execute('PRAGMA table_info("journal_lines")')}
                if {"entry_id", "debit", "credit"} <= cols:
                    bad = conn.execute(
                        "SELECT entry_id FROM journal_lines GROUP BY entry_id "
                        "HAVING ABS(COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0)) > 0.004 LIMIT 5"
                    ).fetchall()
                    if bad:
                        problems.append("unbalanced journal entries: " + ", ".join(str(r[0]) for r in bad))
        except sqlite3.Error as e:
            problems.append(f"invariant check failed: {e}")
        finally:
            conn.close()
        return problems

    def health(self) -> Record:
        h = super().health()
        h["path"] = str(self.db_path)
        h["journal_mode"] = "wal" if self.wal else "delete"
        return h
