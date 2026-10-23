"""Migration files: identifiers and the `requires` chain.

Migrations are SQL files, one folder per supported SQL dialect::

    permita38/core/schema/migrations/sqlite/0004-calendars.sql
    permita38/core/schema/migrations/postgresql/0004-calendars.sql
    permita38/core/schema/migrations/mariadb/0004-calendars.sql

The first two lines of every file are the header::

    -- migration: 7ecb5a30
    -- requires: 3f9a1c02

The identity of a migration is its random 8-hex-character id; the order is the chain of ``requires`` (the first
migration has ``requires: none``). The ``NNNN`` in the file name is a human-readable ordinal only: it must not
decrease along the chain, but ties are allowed, so two branches that picked the same number do not conflict.

Rules (enforced by ``list_migrations`` / ``validate_dialects`` and the tests):

* ids are unique, each ``requires`` names an existing migration, exactly one migration has ``requires: none``,
  every migration is required by at most one other (linear chain, no forks), hence exactly one head;
* ordinals are non-decreasing along the chain;
* all dialect folders contain the same ids, slugs and ``requires`` values.

Add a migration with ``permita38 create-migration <name>``: it picks a random id, points ``requires`` at the current
head and writes the file in every dialect folder. When two branches both added a migration (validation reports a
fork), the second one to merge re-points the ``requires`` of its first new migration at the new head.

The first three migrations carry the fixed ids of the legacy integer revisions (``LEGACY_IDS``): databases written by
alpha.1 and earlier hold the revision number in ``PRAGMA user_version``.
"""

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "schema" / "migrations"
DIALECT_NAMES = ("sqlite", "postgresql", "mariadb")
#: Ids of the migrations that existed as integer revisions 1..3 (revision n = the first n ids are applied).
LEGACY_IDS = ("00000001", "00000002", "00000003")

_FILE_RE = re.compile(r"^(\d{4})-([a-z0-9_]+)\.sql$")
_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_ID_LINE = re.compile(r"^-- migration: (\S+)$")
_REQ_LINE = re.compile(r"^-- requires: (\S+)$")


@dataclass(frozen=True)
class Migration:
    id: str
    ordinal: int
    name: str  # the slug
    requires: str | None
    path: Path

    @property
    def file_name(self) -> str:
        return f"{self.ordinal:04d}-{self.name}.sql"

    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def _parse(path: Path) -> Migration:
    m = _FILE_RE.match(path.name)
    if not m:
        raise ValueError(f"bad migration file name: {path} (expected 0001-name.sql)")
    lines = path.read_text(encoding="utf-8").split("\n")[:2]
    idm = _ID_LINE.match(lines[0]) if len(lines) > 0 else None
    rqm = _REQ_LINE.match(lines[1]) if len(lines) > 1 else None
    if not idm or not rqm:
        raise ValueError(
            f"bad migration header in {path}: the first two lines must be '-- migration: <id>' and '-- requires: <id|none>'"
        )
    mid, req = idm.group(1), rqm.group(1)
    if not _ID_RE.match(mid):
        raise ValueError(f"bad migration id {mid!r} in {path} (8 lowercase hex characters)")
    if req != "none" and not _ID_RE.match(req):
        raise ValueError(f"bad requires {req!r} in {path} (8 lowercase hex characters or 'none')")
    return Migration(mid, int(m.group(1)), m.group(2), None if req == "none" else req, path)


def list_migrations(dialect: str, directory: Path | None = None) -> list[Migration]:
    """The validated migration chain of one dialect folder, root first. Raises ValueError on any rule violation."""
    folder = (directory or MIGRATIONS_DIR) / dialect
    found = [_parse(p) for p in sorted(folder.glob("*.sql"))]
    if not found:
        return []
    by_id: dict[str, Migration] = {}
    for mig in found:
        if mig.id in by_id:
            raise ValueError(
                f"duplicate migration id {mig.id} ({by_id[mig.id].path.name} and {mig.path.name}) in {folder}"
            )
        by_id[mig.id] = mig
    roots = [m for m in found if m.requires is None]
    if len(roots) != 1:
        raise ValueError(
            f"{folder} must contain exactly one migration with 'requires: none', found {len(roots)}"
            + (f": {', '.join(m.path.name for m in roots)}" if roots else "")
        )
    successors: dict[str, list[Migration]] = {}
    for mig in found:
        if mig.requires is None:
            continue
        if mig.requires not in by_id:
            raise ValueError(f"{mig.path.name} requires unknown migration {mig.requires} in {folder}")
        successors.setdefault(mig.requires, []).append(mig)
    for rid, succ in successors.items():
        if len(succ) > 1:
            raise ValueError(
                f"fork in {folder}: {', '.join(s.path.name for s in succ)} all require {rid} ({by_id[rid].path.name}). "
                "Re-point the 'requires' of the first migration of the branch that merges second onto the new head."
            )
    chain = [roots[0]]
    while chain[-1].id in successors:
        nxt = successors[chain[-1].id][0]
        chain.append(nxt)
        if len(chain) > len(found):  # unreachable for valid input; guards against endless loops
            break
    if len(chain) != len(found):
        left = sorted(m.path.name for m in found if m not in chain)
        raise ValueError(f"migrations not reachable from the root (cycle?) in {folder}: {', '.join(left)}")
    for a, b in zip(chain, chain[1:]):
        if b.ordinal < a.ordinal:
            raise ValueError(f"ordinal decreases along the chain in {folder}: {a.path.name} -> {b.path.name}")
    return chain


def validate_dialects(directory: Path | None = None) -> dict[str, list[Migration]]:
    """Validate every dialect folder and check that they agree on ids, slugs and requires."""
    chains = {d: list_migrations(d, directory) for d in DIALECT_NAMES}
    sig = {d: [(m.id, m.name, m.requires) for m in c] for d, c in chains.items()}
    for d in DIALECT_NAMES[1:]:
        if sig[d] != sig[DIALECT_NAMES[0]]:
            raise ValueError(f"dialect folders differ: {DIALECT_NAMES[0]} and {d} do not contain the same migrations")
    return chains


def head_id(directory: Path | None = None, dialect: str = "sqlite") -> str | None:
    chain = list_migrations(dialect, directory)
    return chain[-1].id if chain else None


def create_migration(name: str, directory: Path | None = None) -> list[Path]:
    """Create a new migration (random id, ``requires`` = current head) in every dialect folder."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise ValueError("migration name must contain letters or digits")
    root = directory or MIGRATIONS_DIR
    for d in DIALECT_NAMES:
        (root / d).mkdir(parents=True, exist_ok=True)
    chains = validate_dialects(root)
    chain = chains["sqlite"]
    used = {m.id for m in chain}
    new_id = secrets.token_hex(4)
    while new_id in used:
        new_id = secrets.token_hex(4)
    ordinal = (chain[-1].ordinal if chain else 0) + 1
    requires = chain[-1].id if chain else "none"
    created = []
    for dialect in DIALECT_NAMES:
        p = root / dialect / f"{ordinal:04d}-{slug}.sql"
        p.write_text(
            f"-- migration: {new_id}\n-- requires: {requires}\n-- permitA38 migration: {name} ({dialect})\n\n",
            encoding="utf-8",
        )
        created.append(p)
    return created
