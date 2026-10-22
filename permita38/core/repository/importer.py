"""Import the legacy file-per-record JSON layout (permitA38/Timely 0.1/0.2) into a repository."""

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .base import ID_RE, Repository
from .schema import COLLECTIONS, INTERNAL_COLLECTIONS

_FILE_RE = re.compile(r"^[0-9a-z]{6,32}\.json$")


@dataclass
class ImportReport:
    imported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    settings: bool = False
    attachments: int = 0
    usernames_derived: list[str] = field(default_factory=list)
    users_without_password: list[str] = field(default_factory=list)


def is_legacy_layout(folder: Path) -> bool:
    folder = Path(folder)
    return (folder / "settings.json").is_file() or any(
        (folder / c).is_dir() and any(_FILE_RE.match(p.name) for p in (folder / c).iterdir())
        for c in (*COLLECTIONS, *INTERNAL_COLLECTIONS)
    )


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", (name or "user").lower()).strip("-._")
    return (s if len(s) >= 2 else "user")[:32]


def import_json(repo: Repository, old_dir: Path) -> ImportReport:
    """Idempotent: records whose id already exists are skipped, settings are only imported into an empty DB."""
    old = Path(old_dir)
    if not old.is_dir():
        raise FileNotFoundError(f"{old} is not a directory")
    report = ImportReport()
    with repo.transaction():
        if (old / "settings.json").is_file() and not repo.settings():
            repo.save_settings(json.loads((old / "settings.json").read_text(encoding="utf-8")))
            report.settings = True
        taken = {u.get("username") for u in repo.list("users")}
        for coll in (*COLLECTIONS, *INTERNAL_COLLECTIONS):
            folder = old / coll
            imported = skipped = 0
            if folder.is_dir():
                for p in sorted(folder.iterdir()):
                    if not _FILE_RE.match(p.name):
                        continue  # e.g. *.sync-conflict-* copies
                    try:
                        rec = json.loads(p.read_text(encoding="utf-8"))
                    except ValueError:
                        skipped += 1
                        continue
                    rid = rec.get("id") or p.stem
                    if not ID_RE.match(str(rid)) or repo.get(coll, rid):
                        skipped += 1
                        continue
                    rec["id"] = rid
                    if coll == "users":
                        if not rec.get("username"):
                            base = _slug(rec.get("name", ""))
                            name, n = base, 1
                            while name in taken:
                                n += 1
                                name = f"{base[:28]}-{n}"
                            rec["username"] = name
                            report.usernames_derived.append(f"{rec.get('name', '?')} -> {name}")
                        taken.add(rec["username"])
                        if not rec.get("password_hash"):
                            report.users_without_password.append(rec["username"])
                    repo.save(coll, rec, touch=False)
                    imported += 1
            if imported or skipped:
                report.imported[coll], report.skipped[coll] = imported, skipped
    src_att, dst_att = old / "attachments", Path(getattr(repo, "data_dir", old)) / "attachments"
    if src_att.is_dir() and src_att.resolve() != dst_att.resolve():
        dst_att.mkdir(parents=True, exist_ok=True)
        for f in src_att.iterdir():
            if f.is_file() and not (dst_att / f.name).exists():
                shutil.copy2(f, dst_att / f.name)
                report.attachments += 1
    return report
