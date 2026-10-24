"""Build the export archive in a 0600 temporary file (never in memory)."""

import hashlib
import json
import os
import stat
import tempfile
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from permita38 import __version__
from permita38.core.database import head_id
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

from . import csvout, limits, manifest

README = """permitA38 data export
=====================

This ZIP archive is a copy of the data of a permitA38 instance, made for moving it to another instance and for
your own safety. Restore it on a fresh instance: Settings > Data export and import, or `permita38 import FILE`.

Contents
  manifest.json          format version, schema version, counts and a SHA-256 hash of every other file
  data/<collection>.json every record, lossless (JSON is the source of truth)
  data/settings.json     settings without secrets
  csv/<collection>.csv   the same records for people (spreadsheets); never read by the import
  attachments/           receipts and invoice PDFs, unchanged

NOT in the archive: the secret key, two-factor secrets and recovery codes, API tokens, calendar link URLs, the
calendar event cache and (unless requested) password hashes. Users without a password must get one with
`permita38 set-password`; two-factor must be set up again.

This is NOT a legally compliant archive (GoBD): it is not unalterable and has no signature or time stamp. The hashes
only detect corruption. Keep the original invoices and receipts as the law requires. The archive contains personal
data: store it encrypted.
"""


@dataclass
class ExportOptions:
    scope: str = "full"
    include_accounts: bool = False
    include_attachments: bool = True


@dataclass
class ExportResult:
    path: Path
    filename: str
    size: int
    sha256: str
    counts: dict[str, int]
    manifest: dict = field(default_factory=dict)


def filename_for(now: float | None = None) -> str:
    return "permita38-export-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + ".zip"


class _Sink:
    """One archive entry being written: counts bytes and hashes them."""

    def __init__(self, zf: zipfile.ZipFile, name: str, stamp: tuple, stored: bool):
        info = zipfile.ZipInfo(name, date_time=stamp)
        info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        self.f = zf.open(info, "w", force_zip64=True)
        self.sha = hashlib.sha256()
        self.bytes = 0

    def write(self, chunk: bytes) -> None:
        self.f.write(chunk)
        self.sha.update(chunk)
        self.bytes += len(chunk)

    def close(self) -> dict:
        self.f.close()
        return {"bytes": self.bytes, "sha256": self.sha.hexdigest()}


def _json_chunks(coll: str, records: list[dict]) -> Iterator[bytes]:
    yield b'{"collection": ' + json.dumps(coll).encode() + b', "count": ' + str(len(records)).encode() + b', "records": ['
    for i, r in enumerate(records):
        text = json.dumps(r, ensure_ascii=False, sort_keys=True)
        yield (b",\n" if i else b"\n") + text.encode("utf-8")
    yield b"\n]}\n" if records else b"]}\n"


def estimate(store: Repository, counts: dict[str, int], attachment_bytes: int) -> int:
    return sum(counts.values()) * 2048 * 2 + attachment_bytes


def attachment_stats(store: Repository) -> tuple[int, int]:
    total = 0
    names = store.list_attachments()
    for n in names:
        p = store.attachment_path(n)
        if p:
            total += os.path.getsize(p)
    return len(names), total


def numbering_state(sequences: list[dict]) -> dict:
    out: dict = {}
    for row in sorted(sequences, key=lambda r: str(r.get("period"))):
        out[str(row.get("doc_type"))] = {"period": row.get("period"), "last_value": row.get("last_value")}
    return out


def build(store: Repository, opts: ExportOptions, tmp_dir: Path) -> ExportResult:
    master = opts.scope == "master_data"
    include_accounts = opts.include_accounts and not master
    include_attachments = opts.include_attachments and not master
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="permita38-export-", suffix=".zip", dir=tmp_dir)  # mode 0600
    os.close(fd)
    path = Path(tmp_name)
    now = int(time.time())
    stamp = time.gmtime(now)[:6]
    files: dict[str, dict] = {}
    counts: dict[str, int] = {}
    total = 0
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:

            def add(name: str, chunks: Iterator[bytes], stored: bool = False) -> None:
                nonlocal total
                sink = _Sink(zf, name, stamp, stored)
                try:
                    for c in chunks:
                        sink.write(c)
                        total += len(c)
                        if total > limits.MAX_TOTAL:
                            raise ServiceError(413, "The export would be larger than the limit (2 GiB uncompressed).")
                finally:
                    files[name] = sink.close()

            collections = manifest.master_data_collections() if master else manifest.exported_collections()
            settings: dict = {}
            sequences: list[dict] = []
            with store.transaction():  # one consistent snapshot
                for coll in collections:
                    records = [manifest.scrub_record(coll, r, include_accounts) for r in store.list(coll)]
                    counts[coll] = len(records)
                    add(f"data/{coll}.json", _json_chunks(coll, records))
                    if coll in manifest.csv_collections():
                        add(f"csv/{coll}.csv", csvout.csv_chunks(manifest.columns_for(coll, include_accounts), records))
                    if coll == "number_sequences":
                        sequences = records
                if not master:
                    settings = manifest.exportable_settings(store.settings())
                    payload = json.dumps({"settings": settings}, ensure_ascii=False, sort_keys=True) + "\n"
                    add(manifest.SETTINGS_FILE, iter([payload.encode("utf-8")]))
            if include_attachments:
                for name in store.list_attachments():
                    p = store.attachment_path(name)
                    if not p:
                        continue
                    if os.path.getsize(p) > limits.MAX_ATTACHMENT:
                        raise ServiceError(413, "An attachment is larger than the limit (25 MiB).")

                    def blob(p: str = p) -> Iterator[bytes]:
                        with open(p, "rb") as f:
                            while chunk := f.read(limits.CHUNK):
                                yield chunk

                    add(f"attachments/{name}", blob(), stored=True)
            add("README.txt", iter([README.encode("utf-8")]))
            excluded = list(manifest.EXCLUDED_NOTES)
            if not include_accounts:
                excluded.append("password hashes (not requested)")
            man = {
                "format": manifest.FORMAT,
                "format_version": manifest.FORMAT_VERSION,
                "scope": opts.scope,
                "app_version": __version__,
                "schema_head": head_id(),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "created_epoch": now,
                "include_accounts": include_accounts,
                "include_attachments": include_attachments,
                "counts": counts,
                "numbering": numbering_state(sequences),
                "excluded": excluded,
                "files": dict(sorted(files.items())),
            }
            sink = _Sink(zf, "manifest.json", stamp, False)
            sink.write((json.dumps(man, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))
            sink.close()
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(limits.CHUNK):
                sha.update(chunk)
        return ExportResult(path, filename_for(now), path.stat().st_size, sha.hexdigest(), counts, man)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
