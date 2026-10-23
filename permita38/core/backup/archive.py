"""Build, verify and restore backup archives.

An archive is one ``.tar.gz`` file (optionally wrapped by passphrase encryption, ``.tar.gz.enc``)::

    manifest.json                 what is inside, SHA-256 of every file, retention class per file, hash chain
    README-RESTORE.txt            how to restore by hand
    db/permita38.sqlite3          consistent database snapshot
    attachments/...               receipts and the logo
    issued/<year>/<number>.pdf    the PDFs exactly as they were issued
    exports/*.csv                 human-readable exports (customers, offers, invoices, expenses, journal)

The user table (password hashes, 2FA secrets) is never exported to CSV; it is only inside the database snapshot, which
is the reason to encrypt archives that leave the machine.
"""

import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import socket
import tarfile
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from permita38 import __version__
from permita38.core.database import CURRENT_REVISION
from permita38.core.repository import TABLES, Repository

from . import crypto, rules

MANIFEST_NAME = "manifest.json"
README_NAME = "README-RESTORE.txt"
FORMAT = 1
MAX_MEMBERS = 500_000
EXPORTS = {  # collection -> retention class of its CSV export
    "customers": "letters",
    "offers": "letters",
    "invoices": "vouchers",
    "expenses": "vouchers",
    "accounts": "books",
    "journal_entries": "books",
    "journal_lines": "books",
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

RESTORE_TEXT = """permitA38 backup - how to restore
=================================

1. Install permitA38 (same or newer version than the one that wrote this backup: see manifest.json).
2. Restore into a NEW, empty folder (the command never overwrites existing data):

       permita38 backup restore <this-archive> --to <new-data-folder>

   Encrypted archives (.enc) ask for the passphrase (or use the PERMITA38_BACKUP_PASSPHRASE variable).
3. Start permitA38 with `--data <new-data-folder>`.

By hand: unpack the archive (tar -xzf), copy db/<database file> into a new data folder, and copy the
attachments/ and issued/ folders next to it. manifest.json lists SHA-256 checksums of every file.
The exports/ folder contains CSV files you can open in a spreadsheet without permitA38.
"""


class ArchiveError(Exception):
    pass


@dataclass
class Built:
    path: Path
    sha256: str
    size: int
    manifest: dict[str, Any]
    manifest_sha256: str
    encrypted: bool


@dataclass
class Report:
    ok: bool = False
    problems: list[str] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    manifest_sha256: str | None = None
    sha256: str | None = None
    size: int = 0
    encrypted: bool = False
    needs_passphrase: bool = False


@dataclass
class _Item:
    arcname: str
    path: Path
    cls: str
    ref: date


def host_id() -> str:
    """Stable, anonymous id of this machine (so a hash chain can tell devices apart); no hostname is stored."""
    return hashlib.sha256(("permita38:" + socket.gethostname()).encode()).hexdigest()[:12]


def sha256_file(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


def archive_name(now: datetime, kind: str, encrypted: bool) -> str:
    slug = kind.replace("+", "-")
    return f"permita38-backup-{now:%Y%m%dT%H%M%SZ}-{slug}.tar.gz" + (".enc" if encrypted else "")


# ------------------------------------------------------------------------------------------------ building
def _csv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    s = str(v)
    # spreadsheet formula injection: a text value that starts with = + - @ must stay text
    return "'" + s if isinstance(v, str) and s.startswith(_FORMULA_PREFIXES) else s


def _write_csv(repo: Repository, coll: str, dest: Path) -> None:
    table = TABLES[coll]
    cols = [c for c in table.column_names]
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([*cols, "extra"])
        for rec in repo.list(coll):
            extra = {k: v for k, v in rec.items() if k not in cols}
            w.writerow([*(_csv_cell(rec.get(c)) for c in cols), json.dumps(extra, ensure_ascii=False, sort_keys=True)])


def _safe_member_name(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name


def _collect(repo: Repository, staging: Path, now: datetime, reference_date: date | None = None) -> tuple[list[_Item], dict[str, int]]:
    if repo.snapshot_name is None:
        raise ArchiveError(f"The {repo.backend} backend has no file snapshot; use its native backup tooling")
    root = repo.file_root()
    snap = staging / "db" / repo.snapshot_name
    repo.backup(snap)
    counts = repo.snapshot_counts(snap)
    today = reference_date or now.date()  # a closing archive counts from the end of the closed year
    items = [_Item(f"db/{repo.snapshot_name}", snap, "books", today)]

    if root is not None:
        expense_dates = {
            e["attachment"]: date.fromisoformat(e["date"]) for e in repo.list("expenses") if e.get("attachment")
        } if counts.get("expenses") else {}  # fmt: skip
        att = root / "attachments"
        if att.is_dir():
            for p in sorted(x for x in att.iterdir() if x.is_file() and not x.name.endswith(".part")):
                ref = expense_dates.get(p.name)
                items.append(_Item(f"attachments/{p.name}", p, "vouchers" if ref else "operational", ref or today))
        issued = {r["path"]: r for r in repo.list("issued_documents")} if counts.get("issued_documents") else {}
        iss = root / "issued"
        if iss.is_dir():
            for p in sorted(iss.rglob("*.pdf")):
                rel = p.relative_to(root).as_posix()
                row = issued.get(rel)
                cls = (row or {}).get("retention_class") or ("vouchers" if "RE" in p.stem.upper() else "letters")
                ref = date.fromisoformat(row["issued_on"]) if row and row.get("issued_on") else today
                items.append(_Item(rel, p, cls, ref))

    (staging / "exports").mkdir(exist_ok=True)
    for coll, cls in EXPORTS.items():
        if coll in TABLES and counts.get(coll):
            dest = staging / "exports" / f"{coll}.csv"
            _write_csv(repo, coll, dest)
            items.append(_Item(f"exports/{coll}.csv", dest, cls, today))
    readme = staging / README_NAME
    readme.write_text(RESTORE_TEXT, encoding="utf-8")
    items.append(_Item(README_NAME, readme, "operational", today))
    return items, counts


def build_archive(
    repo: Repository,
    out_dir: Path,
    *,
    kind: str,
    now: datetime,
    prev_manifest_sha256: str | None = None,
    passphrase: str | None = None,
    closing_year: int | None = None,
    reference_date: date | None = None,
    scrypt_n_log2: int = crypto.DEFAULT_N_LOG2,
) -> Built:
    """Create one archive in `out_dir` (atomically: temp file, fsync, rename)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if passphrase is not None and not crypto.available():
        raise crypto.CryptoUnavailable(
            "Encrypted backups need the optional 'cryptography' package: pip install 'permita38[backup-encryption]'"
        )
    staging = Path(tempfile.mkdtemp(prefix=".p38-staging-", dir=out_dir))
    try:
        items, counts = _collect(repo, staging, now, reference_date)
        entries, ends = [], []
        for it in items:
            digest, size = sha256_file(it.path)
            end = rules.retention_end(it.cls, it.ref)
            if end:
                ends.append(end)
            entries.append(
                {
                    "path": it.arcname,
                    "sha256": digest,
                    "size": size,
                    "class": it.cls,
                    "retention_until": end.isoformat() if end else None,
                }  # fmt: skip
            )
        entries.sort(key=lambda e: e["path"])
        review = rules.review_status(now.date())
        manifest = {
            "format": FORMAT,
            "app": "permitA38",
            "app_version": __version__,
            "schema_revision": CURRENT_REVISION,
            "backend": repo.backend,
            "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": kind,
            "closing_year": closing_year,
            "host_id": host_id(),
            "prev_manifest_sha256": prev_manifest_sha256,
            "db_file": f"db/{repo.snapshot_name}",
            "row_counts": counts,
            "legal": kind == "yearly",
            "retention_until": max(ends).isoformat() if ends else None,
            "retention_rules": {
                "reviewed_on": review["reviewed_on"],
                "verified_against_official_source": review["verified_against_official_source"],
            },
            "files": entries,
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8")
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

        encrypted = passphrase is not None
        final = out_dir / archive_name(now, kind, encrypted)
        plain = staging / "archive.tar.gz"
        mtime = int(now.timestamp())
        with open(plain, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    _add_bytes(tar, MANIFEST_NAME, manifest_bytes, mtime)
                    for it in items:
                        ti = tarfile.TarInfo(it.arcname)
                        ti.size, ti.mtime, ti.mode = it.path.stat().st_size, mtime, 0o644
                        with open(it.path, "rb") as f:
                            tar.addfile(ti, f)
        part = final.with_name(final.name + ".part")
        try:
            if encrypted:
                with open(plain, "rb") as src, open(part, "wb") as dst:
                    crypto.encrypt_stream(src, dst, passphrase or "", n_log2=scrypt_n_log2)
                    dst.flush()
                    os.fsync(dst.fileno())
            else:
                shutil.copyfile(plain, part)
                with open(part, "rb") as f:
                    os.fsync(f.fileno())
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        digest, size = sha256_file(final)
        return Built(final, digest, size, manifest, manifest_sha, encrypted)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: int) -> None:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime, ti.mode = len(data), mtime, 0o644
    tar.addfile(ti, io.BytesIO(data))


# ------------------------------------------------------------------------------------------------ opening
class _Opened:
    """A readable tar.gz, decrypting into a temp file first if needed."""

    def __init__(self, path: Path, passphrase: str | None):
        self.path, self.passphrase = Path(path), passphrase
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.tar: tarfile.TarFile | None = None

    def __enter__(self) -> "_Opened":
        src = self.path
        if crypto.is_encrypted_file(src):
            if not self.passphrase:
                raise crypto.BackupCryptoError("This backup is encrypted: a passphrase is required")
            self._tmp = tempfile.TemporaryDirectory(prefix="p38-decrypt-")
            plain = Path(self._tmp.name) / "archive.tar.gz"
            with open(src, "rb") as s, open(plain, "wb") as d:
                crypto.decrypt_stream(s, d, self.passphrase)
            src = plain
        try:
            self.tar = tarfile.open(src, mode="r:gz")
        except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
            self.close()
            raise ArchiveError(f"The archive is damaged or not a permitA38 backup: {e}") from None
        return self

    def close(self) -> None:
        if self.tar is not None:
            self.tar.close()
        if self._tmp is not None:
            self._tmp.cleanup()

    def __exit__(self, *exc) -> None:
        self.close()


def _stream_members(tar: tarfile.TarFile):
    """Yield (member, file object) for regular files; reject anything unsafe."""
    count = 0
    for m in tar:
        count += 1
        if count > MAX_MEMBERS:
            raise ArchiveError("The archive contains an unreasonable number of files")
        if not _safe_member_name(m.name):
            raise ArchiveError(f"The archive contains an unsafe path: {m.name!r}")
        if not m.isfile():
            raise ArchiveError(f"The archive contains something that is not a plain file: {m.name!r}")
        f = tar.extractfile(m)
        if f is None:
            raise ArchiveError(f"Cannot read {m.name!r} from the archive")
        yield m, f


def _hash_stream(f: BinaryIO, out: BinaryIO | None = None) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
        n += len(block)
        if out is not None:
            out.write(block)
    return h.hexdigest(), n


def read_manifest(path: Path, passphrase: str | None = None) -> tuple[dict[str, Any], str]:
    """Manifest and its SHA-256 (cheap: only the first member is read)."""
    with _Opened(path, passphrase) as op:
        assert op.tar is not None
        first = op.tar.next()
        if first is None or first.name != MANIFEST_NAME or not first.isfile():
            raise ArchiveError("manifest.json is missing (not a permitA38 backup)")
        f = op.tar.extractfile(first)
        assert f is not None
        data = f.read(8 * 1024 * 1024)
        try:
            return json.loads(data), hashlib.sha256(data).hexdigest()
        except ValueError:
            raise ArchiveError("manifest.json is not valid JSON") from None


# ------------------------------------------------------------------------------------------------ verifying
def verify_archive(
    path: Path, repo: Repository, passphrase: str | None = None, *, deep: bool = True, keep_snapshot: Path | None = None
) -> Report:
    """Check an archive end to end: container, manifest, every checksum and (deep) the database snapshot."""
    rep = Report()
    path = Path(path)
    try:
        rep.sha256, rep.size = sha256_file(path)
    except OSError as e:
        rep.problems.append(f"cannot read the file: {e.strerror or e}")
        return rep
    rep.encrypted = crypto.is_encrypted_file(path)
    try:
        with _Opened(path, passphrase) as op, tempfile.TemporaryDirectory(prefix="p38-verify-") as tmp:
            assert op.tar is not None
            actual: dict[str, tuple[str, int]] = {}
            manifest_bytes = None
            snap_path: Path | None = None
            for idx, (m, f) in enumerate(_stream_members(op.tar)):
                if m.name == MANIFEST_NAME:
                    if idx != 0:
                        rep.problems.append("manifest.json is not the first entry")
                    manifest_bytes = f.read(8 * 1024 * 1024)
                    continue
                if m.name in actual:
                    rep.problems.append(f"duplicate entry {m.name}")
                out = None
                if deep and m.name.startswith("db/") and snap_path is None:
                    snap_path = Path(tmp) / Path(m.name).name
                    out = open(snap_path, "wb")
                try:
                    actual[m.name] = _hash_stream(f, out)
                finally:
                    if out:
                        out.close()
            if manifest_bytes is None:
                rep.problems.append("manifest.json is missing")
                return rep
            try:
                manifest = json.loads(manifest_bytes)
            except ValueError:
                rep.problems.append("manifest.json is not valid JSON")
                return rep
            rep.manifest, rep.manifest_sha256 = manifest, hashlib.sha256(manifest_bytes).hexdigest()
            _check_manifest(manifest, actual, rep)
            if deep and snap_path is not None and not rep.problems:
                rep.problems += [f"database snapshot: {p}" for p in repo.verify_snapshot(snap_path)]
                counts = repo.snapshot_counts(snap_path)
                if counts != manifest.get("row_counts"):
                    rep.problems.append("row counts of the database snapshot do not match the manifest")
                rep.problems += [f"invariant: {p}" for p in repo.snapshot_invariants(snap_path)]
                if keep_snapshot is not None:
                    shutil.copyfile(snap_path, keep_snapshot)
    except crypto.BackupCryptoError as e:
        rep.needs_passphrase = "passphrase is required" in str(e)
        rep.problems.append(str(e))
    except crypto.CryptoUnavailable as e:
        rep.problems.append(str(e))
    except ArchiveError as e:
        rep.problems.append(str(e))
    except (OSError, EOFError, tarfile.TarError, zlib.error) as e:
        rep.problems.append(f"The archive is damaged or truncated: {e}")
    rep.ok = not rep.problems
    return rep


def _check_manifest(manifest: dict, actual: dict[str, tuple[str, int]], rep: Report) -> None:
    if manifest.get("format") != FORMAT or manifest.get("app") != "permitA38":
        rep.problems.append("unsupported or foreign backup format")
        return
    if int(manifest.get("schema_revision", 0)) > CURRENT_REVISION:
        rep.problems.append(
            f"the backup has schema revision {manifest['schema_revision']}, newer than this version ({CURRENT_REVISION})"
        )
    listed = {e["path"]: e for e in manifest.get("files", [])}
    for name in sorted(set(listed) - set(actual)):
        rep.problems.append(f"missing from the archive: {name}")
    for name in sorted(set(actual) - set(listed)):
        rep.problems.append(f"not listed in the manifest (added later?): {name}")
    for name, (digest, size) in actual.items():
        e = listed.get(name)
        if e and (e["sha256"] != digest or e["size"] != size):
            rep.problems.append(f"checksum mismatch: {name}")
    if manifest.get("db_file") not in listed:
        rep.problems.append("the database snapshot is not part of the archive")


def verify_chain(items: list[tuple[str, dict[str, Any], str]]) -> list[str]:
    """Hash chain across archives: [(label, manifest, manifest_sha256)] -> problems.

    Each manifest records the hash of the previous backup's manifest. A changed manifest no longer matches what its
    successor recorded; a removed archive shows up as a predecessor that is not present (reported as a note)."""
    problems: list[str] = []
    ordered = sorted(items, key=lambda x: (x[1].get("created", ""), x[0]))
    by_hash = {sha: label for label, _, sha in ordered}
    for i, (label, man, _sha) in enumerate(ordered):
        prev = man.get("prev_manifest_sha256")
        if not prev:
            continue
        if prev in by_hash:
            expected = ordered[i - 1][0] if i else None
            if by_hash[prev] != expected and expected is not None:
                problems.append(f"{label}: chain order inconsistent (points to {by_hash[prev]})")
        elif i and ordered[i - 1][1].get("host_id") == man.get("host_id"):
            # the predecessor on the same machine is present but its manifest hash differs from the recorded one
            problems.append(f"{label}: the manifest of {ordered[i - 1][0]} was changed after this backup was made")
    return problems


# ------------------------------------------------------------------------------------------------ restoring
def restore_archive(path: Path, to_dir: Path, repo: Repository, passphrase: str | None = None) -> dict[str, Any]:
    """Restore into a NEW folder (never over existing data). The archive is verified first."""
    to_dir = Path(to_dir).resolve()
    live = repo.file_root()
    if live is not None and to_dir == live.resolve():
        raise ArchiveError("Refusing to restore over the live data folder; choose a new folder")
    if to_dir.exists() and (not to_dir.is_dir() or any(to_dir.iterdir())):
        raise ArchiveError(f"{to_dir} already exists and is not empty; restore never overwrites existing data")
    rep = verify_archive(path, repo, passphrase, deep=True)
    if not rep.ok:
        raise ArchiveError("The backup did not verify: " + "; ".join(rep.problems[:3]))
    manifest = rep.manifest or {}
    partial = to_dir.with_name(to_dir.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    db_file = manifest["db_file"]
    restored = {"files": 0}
    try:
        with _Opened(path, passphrase) as op:
            assert op.tar is not None
            for m, f in _stream_members(op.tar):
                if m.name == MANIFEST_NAME or m.name.startswith("exports/") or m.name == README_NAME:
                    continue
                dest = partial / (PurePosixPath(m.name).name if m.name == db_file else m.name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as out:
                    _hash_stream(f, out)
                restored["files"] += 1
        (partial / "RESTORED-FROM.txt").write_text(
            f"Restored from {Path(path).name}\nbackup created {manifest.get('created')} by permitA38 "
            f"{manifest.get('app_version')} (schema revision {manifest.get('schema_revision')})\n",
            encoding="utf-8",
        )
        if to_dir.exists():
            to_dir.rmdir()
        os.replace(partial, to_dir)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return {"to": str(to_dir), "files": restored["files"], "manifest": manifest}
