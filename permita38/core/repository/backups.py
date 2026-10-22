"""Automatic daily backups with retention (works for any backend that implements `backup()`)."""

import re
import time
from pathlib import Path

from .base import Repository

_NAME_RE = re.compile(r"^permita38-(\d{8})-\d{6}\.sqlite3$")


def auto_backup(repo: Repository, keep: int = 14) -> Path | None:
    """Create today's backup if there is none yet and prune old ones. Returns the new file, if any."""
    folder = repo.backup_dir
    if folder is None:
        return None
    today = time.strftime("%Y%m%d")
    existing = (
        sorted(p for p in folder.glob("permita38-*.sqlite3") if _NAME_RE.match(p.name)) if folder.is_dir() else []
    )
    created = None
    if not any(_NAME_RE.match(p.name).group(1) == today for p in existing):  # type: ignore[union-attr]
        created = repo.backup()
        existing = sorted([*existing, created])
    for old in existing[: max(len(existing) - keep, 0)]:
        old.unlink(missing_ok=True)
    return created
