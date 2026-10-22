"""Database revision and migration files.

Migrations are numbered SQL files, one folder per supported SQL dialect::

    permita38/core/schema/migrations/sqlite/0001_initial.sql
    permita38/core/schema/migrations/postgresql/0001_initial.sql
    permita38/core/schema/migrations/mariadb/0001_initial.sql

Rules (enforced by tests):

* every migration exists for every dialect folder, with the same number and name,
* numbers are contiguous, starting at 1,
* ``CURRENT_REVISION`` equals the highest migration number.

Add a migration with ``permita38 create-migration <name>`` (creates the next file in every dialect
folder), write the SQL, then bump ``CURRENT_REVISION`` below.
"""

import re
from dataclasses import dataclass
from pathlib import Path

#: Revision of the schema this version of the app expects. Bump together with a new migration file.
CURRENT_REVISION = 2

MIGRATIONS_DIR = Path(__file__).parent / "schema" / "migrations"
DIALECT_NAMES = ("sqlite", "postgresql", "mariadb")
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def list_migrations(dialect: str, directory: Path | None = None) -> list[Migration]:
    folder = (directory or MIGRATIONS_DIR) / dialect
    found = []
    for p in sorted(folder.glob("*.sql")):
        m = _FILE_RE.match(p.name)
        if not m:
            raise ValueError(f"bad migration file name: {p} (expected 0001_name.sql)")
        found.append(Migration(int(m.group(1)), m.group(2), p))
    for i, mig in enumerate(found, start=1):
        if mig.version != i:
            raise ValueError(f"migrations in {folder} must be numbered 1..n without gaps (found {mig.path.name})")
    return found


def create_migration(name: str, directory: Path | None = None) -> list[Path]:
    """Create the next numbered migration file in every dialect folder."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise ValueError("migration name must contain letters or digits")
    root = directory or MIGRATIONS_DIR
    nxt = max((m.version for m in list_migrations("sqlite", root)), default=0) + 1
    created = []
    for dialect in DIALECT_NAMES:
        folder = root / dialect
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{nxt:04d}_{slug}.sql"
        p.write_text(f"-- permitA38 migration {nxt:04d}: {name} ({dialect})\n\n", encoding="utf-8")
        created.append(p)
    return created
