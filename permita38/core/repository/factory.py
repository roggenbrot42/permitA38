"""Select the storage backend from the configuration."""

from typing import TYPE_CHECKING

from .base import Repository

if TYPE_CHECKING:
    from permita38.core.config import Config


def create_repository(config: "Config") -> Repository:
    db = config.database
    if db.backend == "sqlite":
        from .sqlite import SQLiteRepository

        return SQLiteRepository(config.data_dir, db_path=db.path, wal=db.sqlite_wal, auto_migrate=db.auto_migrate)
    raise NotImplementedError(
        f"The {db.backend!r} backend is prepared (dialect + contract tests) but not implemented yet. "
        "See docs/porting-to-postgres.md."
    )
