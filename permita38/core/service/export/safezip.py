"""Defensive reading of archives that come from outside (zip bombs, path traversal, duplicate entries, ...).

Nothing is ever extracted to disk by name: callers read the members they know about, through `read`, which enforces
byte limits on the data actually decompressed (the sizes declared in the zip headers are not trusted).
"""

import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from permita38.core.config import ImportLimits
from permita38.core.errors import ServiceError

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
CHUNK = 64 * 1024


class ArchiveError(ServiceError):
    def __init__(self, message: str):
        super().__init__(400, message)


def safe_member_name(name: str) -> bool:
    if not name or len(name) > 255 or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    if _WINDOWS_DRIVE.match(name):
        return False
    parts = PurePosixPath(name).parts
    return bool(parts) and all(p not in ("", ".", "..") for p in parts)


class SafeArchive:
    def __init__(self, path: Path, limits: ImportLimits):
        self.limits = limits
        self.path = Path(path)
        try:
            size = self.path.stat().st_size
        except OSError:
            raise ArchiveError("File not found") from None
        if size > limits.max_archive_mb * 1024 * 1024:
            raise ArchiveError(f"Archive is larger than {limits.max_archive_mb} MB")
        try:
            self._zip = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("This is not a valid export archive (not a ZIP file)") from None
        self._read_total = 0
        self.infos: dict[str, zipfile.ZipInfo] = {}
        self._check_directory()

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "SafeArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check_directory(self) -> None:
        infos = self._zip.infolist()
        if len(infos) > self.limits.max_entries:
            raise ArchiveError("Archive contains too many files")
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            if name.endswith("/"):
                continue  # directory entry: ignored, never created
            if not safe_member_name(name):
                raise ArchiveError("Archive contains an unsafe file name")
            key = name.lower()
            if key in seen:
                raise ArchiveError("Archive contains duplicate entries")
            seen.add(key)
            if info.flag_bits & 0x1:
                raise ArchiveError("Encrypted archives are not supported")
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise ArchiveError("Archive contains a symbolic link")
            total += info.file_size
            if total > self.limits.max_uncompressed_mb * 1024 * 1024:
                raise ArchiveError("Archive would expand to more than the allowed size")
            if info.file_size > 1024 * 1024 and info.compress_size > 0:
                if info.file_size / info.compress_size > self.limits.max_ratio:
                    raise ArchiveError("Archive looks like a zip bomb (compression ratio too high)")
            self.infos[name] = info

    def names(self) -> list[str]:
        return sorted(self.infos)

    def has(self, name: str) -> bool:
        return name in self.infos

    def size(self, name: str) -> int:
        return self.infos[name].file_size

    def read(self, name: str, max_bytes: int) -> bytes:
        """Decompress one member, never producing more than `max_bytes` (and the archive-wide budget)."""
        if name not in self.infos:
            raise ArchiveError(f"{name} is missing from the archive")
        budget = self.limits.max_uncompressed_mb * 1024 * 1024
        out = bytearray()
        try:
            with self._zip.open(self.infos[name]) as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    out += chunk
                    self._read_total += len(chunk)
                    if len(out) > max_bytes:
                        raise ArchiveError(f"{name} is larger than the allowed size")
                    if self._read_total > budget:
                        raise ArchiveError("Archive expands to more than the allowed size")
        except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, NotImplementedError):
            raise ArchiveError(f"{name} is corrupt") from None
        return bytes(out)
