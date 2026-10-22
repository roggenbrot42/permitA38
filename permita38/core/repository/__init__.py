"""Storage layer. Import from here; never from the backend modules."""

from .backups import auto_backup
from .base import ID_RE, DatabaseRevisionError, Record, RecordList, Repository, new_id
from .factory import create_repository
from .schema import COLLECTIONS, INTERNAL_COLLECTIONS, TABLES

__all__ = [
    "COLLECTIONS",
    "ID_RE",
    "INTERNAL_COLLECTIONS",
    "TABLES",
    "DatabaseRevisionError",
    "Record",
    "RecordList",
    "Repository",
    "auto_backup",
    "create_repository",
    "new_id",
]
