"""Import the legacy file-per-record JSON layout (permitA38/Timely 0.1/0.2) into a repository."""

import json
import logging
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import schema as _schema
from .base import ID_RE, Repository
from .schema import COLLECTIONS, INTERNAL_COLLECTIONS

log = logging.getLogger("permita38.import")

#: guard kinds a verbatim restore may suspend; the number guard (``unique``) never is
RESTORE_SUSPENDED = {
    "date",
    "non_negative",
    "order",
    "one_of",
    "exactly_one",
    "required",
    "max_days",
    "range",
    "implies",
}
_BASE_FINDING_CODES = ("format.date", "range.negative", "state.period_reversed")


class _RestoreFindingCodes(Sequence):
    """The finding-code prefixes a restore reports: the three base codes plus the code (before any ':') of every
    guard whose kind a restore suspends. Derived from ``schema.GUARDS`` each time it is read, so guards added later
    (or patched in tests) are covered."""

    @staticmethod
    def _codes() -> tuple[str, ...]:
        derived = (g.code.split(":", 1)[0] for g in _schema.GUARDS if g.kind in RESTORE_SUSPENDED)
        return tuple(dict.fromkeys((*_BASE_FINDING_CODES, *derived)))

    def __len__(self) -> int:
        return len(self._codes())

    def __getitem__(self, index):
        return self._codes()[index]


RESTORE_FINDING_CODES = _RestoreFindingCodes()
NUMBERED = ("invoices", "offers")

_FILE_RE = re.compile(r"^[0-9a-z]{6,32}\.json$")


class ImportRefused(ValueError):
    """The import was refused as a whole; ``code`` names the reason (e.g. ``numbering.duplicate``)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ImportReport:
    imported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    settings: bool = False
    attachments: int = 0
    usernames_derived: list[str] = field(default_factory=list)
    users_without_password: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def drop_empty_number(rec: dict) -> None:
    """A document with ``number == ""`` has no number; drop the key (shared by the up-front check and the write)."""
    if rec.get("number") == "":
        del rec["number"]


def duplicate_numbers(numbers: Iterable[Any]) -> list[str]:
    """The numbers that occur more than once (exact strings), sorted."""
    seen: set[str] = set()
    dup: set[str] = set()
    for n in numbers:
        if isinstance(n, str) and n:
            (dup if n in seen else seen).add(n)
    return sorted(dup)


def is_legacy_layout(folder: Path) -> bool:
    folder = Path(folder)
    return (folder / "settings.json").is_file() or any(
        (folder / c).is_dir() and any(_FILE_RE.match(p.name) for p in (folder / c).iterdir())
        for c in (*COLLECTIONS, *INTERNAL_COLLECTIONS)
    )


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", (name or "user").lower()).strip("-._")
    return (s if len(s) >= 2 else "user")[:32]


def _read_new(repo: Repository, folder: Path, coll: str) -> Iterable[tuple[str, dict]]:
    """(id, record) of the files that would be imported (valid JSON, valid id, not stored yet)."""
    if not folder.is_dir():
        return
    seen: set[str] = set()
    for p in sorted(folder.iterdir()):
        if not _FILE_RE.match(p.name):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id") or p.stem
        if not ID_RE.match(str(rid)) or repo.get(coll, rid):
            continue
        if rid in seen:  # the write loop skips a second file with the same id too
            continue
        seen.add(rid)
        yield rid, rec


def _check_numbers(repo: Repository, old: Path) -> None:
    """Refuse the whole import when it would create a duplicate invoice or offer number."""
    for coll in NUMBERED:
        incoming: list[str] = []
        for _, rec in _read_new(repo, old / coll, coll):
            drop_empty_number(rec)
            if isinstance(rec.get("number"), str):
                incoming.append(rec["number"])
        stored = [r.get("number") for r in repo.list(coll)]
        dup = duplicate_numbers(incoming)
        taken = {n for n in stored if isinstance(n, str) and n}
        dup = sorted({*dup, *(n for n in incoming if n in taken)})
        if dup:
            raise ImportRefused(
                "numbering.duplicate",
                f"numbering.duplicate: duplicate {coll} numbers, nothing was imported: {', '.join(dup)}."
                " Run `permita38 check-db` to see the records.",
            )


def import_json(repo: Repository, old_dir: Path) -> ImportReport:
    """Idempotent: records whose id already exists are skipped, settings are only imported into an empty DB."""
    old = Path(old_dir)
    if not old.is_dir():
        raise FileNotFoundError(f"{old} is not a directory")
    report = ImportReport()
    _check_numbers(repo, old)
    with repo.transaction(), repo.guards_suspended(RESTORE_SUSPENDED):
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
                    if coll in NUMBERED:
                        drop_empty_number(rec)
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
        from permita38.core.service.integrity import IntegrityService

        for finding in IntegrityService(repo).run(only=RESTORE_FINDING_CODES):
            report.warnings.append(finding.as_dict())
    if report.warnings:
        log.warning(
            "import-json: %d integrity findings: %s",
            len(report.warnings),
            ", ".join(f"{w['code']}:{w['collection']}:{w['id']}" for w in report.warnings[:50]),
        )
    src_att, dst_att = old / "attachments", Path(getattr(repo, "data_dir", old)) / "attachments"
    if src_att.is_dir() and src_att.resolve() != dst_att.resolve():
        dst_att.mkdir(parents=True, exist_ok=True)
        for f in src_att.iterdir():
            if f.is_file() and not (dst_att / f.name).exists():
                shutil.copy2(f, dst_att / f.name)
                report.attachments += 1
    return report
