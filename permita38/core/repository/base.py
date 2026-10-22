"""The storage interface the rest of the application depends on.

Services, calculations, documents and the API only ever talk to `Repository`. Nothing outside
`permita38.core.repository` may import a database driver or contain SQL, so a backend can be swapped
(see docs/porting-to-postgres.md).

Contract (verified for every backend by tests/backend/test_repository_contract.py):

* Records are plain ``dict``s. ``None`` values are never stored; absent keys stay absent.
* ``list`` is ordered by ``(created, id)``. ``get`` returns ``None`` for unknown or malformed ids.
* ``save`` replaces the whole record, creates ``id``/``created`` when missing, refreshes ``updated``
  (unless ``touch=False``) and returns the record exactly as ``get`` would.
* Unknown fields survive a round-trip; ints, floats, bools, lists and dicts keep their types.
* ``transaction()`` makes everything inside atomic and isolated; an exception rolls it all back.
"""

import re
import uuid
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .schema import COLLECTIONS, INTERNAL_COLLECTIONS

__all__ = [
    "COLLECTIONS",
    "ID_RE",
    "DatabaseRevisionError",
    "Record",
    "RecordList",
    "Repository",
    "new_id",
]

Record = dict[str, Any]
RecordList = list[Record]  # aliases: a method named `list` shadows the builtin inside the class body
StrList = list[str]

ID_RE = re.compile(r"^[0-9a-z]{6,32}$")


class DatabaseRevisionError(RuntimeError):
    """The database schema revision does not match what this version of the app expects."""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Repository(ABC):
    backend: str = "abstract"

    # ---- lifecycle ----
    @abstractmethod
    def startup(self) -> None:
        """Connect, check the schema revision and apply pending migrations. Idempotent."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def health(self) -> Record:
        """{"backend", "revision", "current_revision", "ok", ...}"""

    # ---- schema revision / migrations ----
    @abstractmethod
    def get_revision(self) -> int: ...

    @abstractmethod
    def migrate(self) -> tuple[int, int]:
        """Apply pending migrations; returns (old_revision, new_revision)."""

    # ---- records ----
    @abstractmethod
    def list(self, coll: str) -> RecordList: ...

    @abstractmethod
    def get(self, coll: str, rid: str) -> Record | None: ...

    @abstractmethod
    def save(self, coll: str, obj: Record, touch: bool = True) -> Record: ...

    @abstractmethod
    def delete(self, coll: str, rid: str) -> bool: ...

    def find(self, coll: str, **equals: Any) -> RecordList:
        """Records whose columns equal the given values (``find("calendar_events", source_id=x)``), ordered like ``list``.

        Only plain (non-JSON) columns can be matched. Backends should override this with an indexed query; this default
        filters ``list`` and is only meant as a fallback for new backends.
        """
        return [r for r in self.list(coll) if all(r.get(k) == v for k, v in equals.items())]

    def delete_where(self, coll: str, **equals: Any) -> int:
        """Delete every record matching ``equals`` (see ``find``) atomically; returns how many were deleted."""
        with self.transaction():
            rows = self.find(coll, **equals)
            for r in rows:
                self.delete(coll, r["id"])
        return len(rows)

    def save_many(self, coll: str, objs: "RecordList") -> RecordList:
        """Save several records atomically (all or none)."""
        with self.transaction():
            return [self.save(coll, o) for o in objs]

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]: ...

    # ---- settings (one key/value document per installation) ----
    @abstractmethod
    def settings(self) -> Record: ...

    @abstractmethod
    def save_settings(self, data: Record) -> None:
        """Replace all settings."""

    # ---- attachments (files such as receipts and the logo) ----
    @abstractmethod
    def save_attachment(self, filename: str, blob: bytes) -> str: ...

    @abstractmethod
    def attachment_path(self, name: str) -> str | None: ...

    # ---- operations ----
    @abstractmethod
    def conflicts(self) -> StrList:
        """Sync-conflict copies of the data that need a human look (empty for server backends)."""

    def backup(self, dest: Path | None = None) -> Path:
        raise NotImplementedError(f"backup is not implemented for the {self.backend} backend (use native tooling)")

    @property
    def backup_dir(self) -> Path | None:
        """Where automatic backups go, or None if the backend does not do file backups."""
        return None


def check_collection(coll: str) -> None:
    if coll not in COLLECTIONS and coll not in INTERNAL_COLLECTIONS:
        raise KeyError(coll)
