"""Where backup archives are written. `BackupTarget` is the extension point for other storage."""

import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .archive import sha256_file

ARCHIVE_RE = re.compile(r"^permita38-backup-\d{8}T\d{6}Z-[a-z0-9-]+\.tar\.gz(\.enc)?$")


class TargetError(Exception):
    pass


@dataclass
class TargetFile:
    name: str
    path: str
    size: int
    mtime: float


@dataclass
class Written:
    path: str
    size: int
    sha256: str


class BackupTarget(ABC):
    kind = "abstract"

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def write(self, src: Path, filename: str) -> Written:
        """Copy `src` to the target atomically and verify it by reading it back."""

    @abstractmethod
    def list(self) -> "list[TargetFile]": ...

    @abstractmethod
    def delete(self, filename: str) -> None: ...

    @abstractmethod
    def local_path(self, filename: str) -> Path | None:
        """Filesystem path of an archive if the target is a local folder (None otherwise)."""

    @abstractmethod
    def check(self) -> str | None:
        """None if usable, otherwise a readable problem."""

    @abstractmethod
    def is_independent(self, data_dir: Path) -> bool:
        """False when the target lives inside the (synced) data folder: that is not a separate copy."""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind}


class LocalDirectoryTarget(BackupTarget):
    kind = "local"

    def __init__(self, name: str, path: Path):
        super().__init__(name)
        self.dir = Path(path).expanduser()

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind, "path": str(self.dir)}

    def check(self) -> str | None:
        d = self.dir
        if not d.is_absolute():
            return "use an absolute path"
        try:
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".p38-write-test-", dir=d)
            os.close(fd)
            os.unlink(tmp)
        except OSError as e:
            return f"not writable: {e.strerror or e}"
        return None

    def write(self, src: Path, filename: str) -> Written:
        if not ARCHIVE_RE.match(filename):
            raise TargetError(f"unexpected archive file name {filename!r}")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            final = self.dir / filename
            part = final.with_name(final.name + ".part")
            try:
                with open(src, "rb") as s, open(part, "wb") as d:
                    shutil.copyfileobj(s, d, 1 << 20)
                    d.flush()
                    os.fsync(d.fileno())
                os.replace(part, final)
            finally:
                part.unlink(missing_ok=True)
        except OSError as e:
            raise TargetError(f"cannot write to {self.dir}: {e.strerror or e}") from None
        want, size = sha256_file(src)
        got, got_size = sha256_file(final)
        if (want, size) != (got, got_size):
            final.unlink(missing_ok=True)
            raise TargetError(f"the copy in {self.dir} does not match the original (read-back check failed)")
        return Written(str(final), got_size, got)

    def list(self) -> list[TargetFile]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.iterdir()):
            if p.is_file() and ARCHIVE_RE.match(p.name):
                st = p.stat()
                out.append(TargetFile(p.name, str(p), st.st_size, st.st_mtime))
        return out

    def delete(self, filename: str) -> None:
        if not ARCHIVE_RE.match(filename):
            raise TargetError("not a backup archive")
        (self.dir / filename).unlink(missing_ok=True)

    def local_path(self, filename: str) -> Path | None:
        return self.dir / filename if ARCHIVE_RE.match(filename) else None

    def is_independent(self, data_dir: Path) -> bool:
        try:
            self.dir.resolve().relative_to(Path(data_dir).resolve())
        except ValueError:
            return True
        return False


class _UnavailableTarget(BackupTarget):
    """Documented extension points: implement `write/list/delete/check` and register in `create_target`."""

    hint = ""

    def _no(self):
        raise NotImplementedError(
            f"The {self.kind} backup target is not implemented yet. {self.hint} "
            "See docs/backups.md (extending targets) or use a local folder on a mounted share."
        )

    def write(self, src, filename):
        self._no()

    def list(self):
        self._no()

    def delete(self, filename):
        self._no()

    def local_path(self, filename):
        return None

    def check(self):
        return f"the {self.kind} target is not implemented yet"

    def is_independent(self, data_dir):
        return True


class S3Target(_UnavailableTarget):
    kind, hint = "s3", "Idea: boto3 or any S3-compatible client; encrypt archives before they leave the machine."


class SFTPTarget(_UnavailableTarget):
    kind, hint = "sftp", "Idea: paramiko; or mount the server with sshfs and use a local folder."


class WebDAVTarget(_UnavailableTarget):
    kind, hint = "webdav", "Idea: Nextcloud/WebDAV client; or mount it and use a local folder."


def create_target(cfg: Any) -> BackupTarget:
    """`cfg` has `type`, `name` and `path` (see BackupTargetConfig)."""
    name = cfg.name or (Path(cfg.path).name if cfg.path else cfg.type)
    if cfg.type == "local":
        if not cfg.path:
            raise TargetError("a local target needs a path")
        return LocalDirectoryTarget(name, Path(cfg.path))
    classes = {"s3": S3Target, "sftp": SFTPTarget, "webdav": WebDAVTarget}
    if cfg.type in classes:
        return classes[cfg.type](name)
    raise TargetError(f"unknown target type {cfg.type!r}")
