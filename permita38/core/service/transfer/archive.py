"""Safe reading of an export archive. Nothing is extracted by path; every read is bounded.

Sizes in the ZIP directory are not trusted: entries are read in chunks with a running counter that aborts at the
limit. Every entry name must have exactly one of the allowed shapes, otherwise the whole archive is refused."""

import hashlib
import json
import math
import re
import stat
import zipfile
from pathlib import Path
from typing import Any

from . import limits, manifest

ATTACHMENT_NAME = r"[0-9a-z]{6,32}\.[a-z0-9]{1,5}"
_ATTACH_RE = re.compile(r"attachments/(" + ATTACHMENT_NAME + ")")
_DATA_RE = re.compile(r"data/([a-z_]{1,40})\.json")
_CSV_RE = re.compile(r"csv/([a-z_]{1,40})\.csv")


class ArchiveError(Exception):
    def __init__(self, code: str, message: str, file: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.file = file


def classify(name: str) -> tuple[str, str] | None:
    """('manifest'|'readme'|'data'|'settings'|'csv'|'attachment', argument) or None for a name that is not allowed."""
    if name == "manifest.json":
        return "manifest", ""
    if name == "README.txt":
        return "readme", ""
    if name == manifest.SETTINGS_FILE:
        return "settings", ""
    if m := _DATA_RE.fullmatch(name):
        return ("data", m.group(1)) if m.group(1) in manifest.exported_collections() else None
    if m := _CSV_RE.fullmatch(name):
        return ("csv", m.group(1)) if m.group(1) in manifest.csv_collections() else None
    if m := _ATTACH_RE.fullmatch(name):
        return "attachment", m.group(1)
    return None


def size_limit(kind: str) -> int:
    return {
        "manifest": limits.MAX_MANIFEST,
        "readme": limits.MAX_MANIFEST,
        "attachment": limits.MAX_ATTACHMENT,
    }.get(kind, limits.MAX_DATA_FILE)


def _show(name: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in name)[:200]


class ArchiveReader:
    def __init__(self, path: Path):
        try:
            self.zf = zipfile.ZipFile(path)
            infos = self.zf.infolist()
        except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError) as e:
            raise ArchiveError("not_a_zip", "The file is not a ZIP archive or it is damaged.") from e
        if len(infos) > limits.MAX_ENTRIES:
            raise ArchiveError("limit_exceeded", f"The archive has more than {limits.MAX_ENTRIES} entries.")
        self.entries: dict[str, zipfile.ZipInfo] = {}
        self.kinds: dict[str, tuple[str, str]] = {}
        folded: set[str] = set()
        declared_total = 0
        for info in infos:
            name = info.filename
            kind = classify(name)
            if kind is None:
                raise ArchiveError("unsafe_entry", f"Entry name not allowed: {_show(name)!r}.")
            if name in self.entries or name.casefold() in folded:
                raise ArchiveError("unsafe_entry", f"Duplicate entry: {_show(name)!r}.", name)
            folded.add(name.casefold())
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ArchiveError("unsafe_entry", f"Not a regular file: {_show(name)!r}.", name)
            if info.flag_bits & 0x1:
                raise ArchiveError("unsafe_entry", f"Encrypted entry: {_show(name)!r}.", name)
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ArchiveError("unsafe_entry", f"Compression method not allowed: {_show(name)!r}.", name)
            if info.file_size > size_limit(kind[0]):
                raise ArchiveError("limit_exceeded", f"{_show(name)!r} is larger than its size limit.", name)
            if info.file_size > limits.RATIO_EXEMPT and info.file_size > limits.MAX_RATIO * max(info.compress_size, 1):
                raise ArchiveError("limit_exceeded", f"{_show(name)!r} has a suspicious compression ratio.", name)
            declared_total += info.file_size
            self.entries[name] = info
            self.kinds[name] = kind
        if declared_total > limits.MAX_TOTAL:
            raise ArchiveError("limit_exceeded", "The archive is larger than 2 GiB uncompressed.")
        self.total_read = 0

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "ArchiveReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _chunks(self, name: str):
        info = self.entries[name]
        cap = size_limit(self.kinds[name][0])
        n = 0
        try:
            with self.zf.open(info) as f:
                while chunk := f.read(limits.CHUNK):
                    n += len(chunk)
                    self.total_read += len(chunk)
                    if n > cap:
                        raise ArchiveError("limit_exceeded", f"{_show(name)!r} is larger than its size limit.", name)
                    if self.total_read > limits.MAX_TOTAL:
                        raise ArchiveError("limit_exceeded", "The archive is larger than 2 GiB uncompressed.", name)
                    if n > limits.RATIO_EXEMPT and n > limits.MAX_RATIO * max(info.compress_size, 1):
                        raise ArchiveError(
                            "limit_exceeded", f"{_show(name)!r} has a suspicious compression ratio.", name
                        )
                    yield chunk
        except (zipfile.BadZipFile, EOFError, OSError, ValueError, RuntimeError) as e:
            raise ArchiveError("not_a_zip", f"{_show(name)!r} is damaged or truncated.", name) from e

    def read(self, name: str) -> bytes:
        return b"".join(self._chunks(name))

    def digest(self, name: str) -> tuple[int, str, bytes]:
        """(bytes, sha256, first 16 bytes) of an entry without keeping it in memory."""
        sha, n, head = hashlib.sha256(), 0, b""
        for c in self._chunks(name):
            sha.update(c)
            n += len(c)
            if len(head) < 16:
                head += c[: 16 - len(head)]
        return n, sha.hexdigest(), head

    def stream(self, name: str):
        """The entry as bounded chunks (for copying an attachment to disk without holding it in memory)."""
        return self._chunks(name)


def depth_ok(value: Any, max_depth: int = limits.MAX_JSON_DEPTH) -> bool:
    stack = [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, (dict, list)):
            if d > max_depth:
                return False
            stack.extend((x, d + 1) for x in (v.values() if isinstance(v, dict) else v))
    return True


def _reject_constant(text: str) -> Any:
    raise ValueError(f"{text} is not allowed")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def parse_json(blob: bytes, name: str) -> Any:
    """Strict JSON: no NaN/Infinity (also not as 1e999)."""
    try:
        value = json.loads(blob.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float)
    except RecursionError as e:
        raise ArchiveError("limit_exceeded", f"{_show(name)!r} is nested too deeply.", name) from e
    except (ValueError, UnicodeDecodeError) as e:
        raise ArchiveError("json_invalid", f"{_show(name)!r} is not valid JSON.", name) from e
    if not depth_ok(value):
        raise ArchiveError("limit_exceeded", f"{_show(name)!r} is nested too deeply.", name)
    return value
