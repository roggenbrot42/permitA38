"""Tesseract for the bundled Windows build: a pinned package, downloaded on request (docs/specs/receipt-scan.md).

The constants name the package published by ``.github/workflows/ocr-tools.yaml``. If they are ever ``None``,
``offered()`` is false and the feature stays off (``ocr_install`` is ``none``). The download is verified against the SHA-256 before anything is unpacked.

The install folder is per device (``%LOCALAPPDATA%\\permitA38\\ocr\\<version>\\``), never the data folder. All functions
take the folder as a parameter so tests run on any system.
"""

import os
import re
import shutil
import stat
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from permita38.core import netsafe
from permita38.core.errors import ServiceError

# ---- the pinned package (fill in from the workflow's summary) -------------------------------------------------
VERSION: str | None = "5.4.0.20240606-1"
URL: str | None = (
    "https://github.com/roggenbrot42/permitA38/releases/download/"
    "ocr-tools-win64-5.4.0.20240606-1/ocr-tools-win64-5.4.0.20240606-1.zip"
)
SIZE: int | None = 58705851  # bytes of the zip
SHA256: str | None = "78d4bd4fc3f72db5b67ec1fd4623b5116d9e00d72c328a28577a94e6f40726e9"

# ---- limits for unpacking ---------------------------------------------------------------------------------------
MAX_FILES = 400
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
EXE_NAME = "tesseract.exe"
_TMP_PREFIX = ".tmp-"


@dataclass(frozen=True)
class Package:
    version: str
    url: str
    size: int
    sha256: str

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""


def package() -> Package | None:
    """The pinned package, or ``None`` while the constants are not filled in."""
    if VERSION and URL and SIZE and SHA256:
        return Package(VERSION, URL, SIZE, SHA256)
    return None


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "permitA38" / "ocr"


def supported_build() -> bool:
    """The bundled Windows build only. Docker, Linux and source installs bring their own Tesseract."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def offered() -> bool:
    return supported_build() and package() is not None


# ---- finding the downloaded copy --------------------------------------------------------------------------------


def find_installed(root: Path | None = None) -> Path | None:
    """``tesseract.exe`` of the newest complete version folder (temporary folders do not count)."""
    root = root or default_root()
    try:
        folders = sorted(
            (d for d in root.iterdir() if d.is_dir() and not d.name.startswith(_TMP_PREFIX)),
            key=lambda d: ([int(n) for n in re.findall(r"\d+", d.name)], d.name),
            reverse=True,
        )
    except OSError:
        return None
    for folder in folders:
        exe = folder / EXE_NAME
        if exe.is_file():
            return exe
    return None


def tessdata_dir(exe: str | os.PathLike[str], root: Path | None = None) -> Path | None:
    """The ``tessdata`` folder next to ``exe`` when ``exe`` is the downloaded copy, else ``None``."""
    exe_path = Path(exe)
    root = root or default_root()
    try:
        inside = exe_path.resolve().parent.parent == root.resolve()
    except OSError:
        return None
    data = exe_path.parent / "tessdata"
    return data if inside and data.is_dir() else None


# ---- safe unpacking ---------------------------------------------------------------------------------------------


def _safe_member(name: str) -> Path:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name or ":" in name.split("/")[0]:
        raise ValueError("unsafe path in package")
    parts = [p for p in name.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise ValueError("unsafe path in package")
    return Path(*parts)


def unpack(zip_path: Path, target: Path) -> None:
    """Unpack into the empty folder ``target``. Refuses traversal, links, too many files and too much data.

    The real size is counted while writing, so a header that lies cannot get past the limit.
    """
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("too many files in package")
        if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise ValueError("package too large when unpacked")
        base = target.resolve()
        for info in infos:
            rel = _safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("link in package")
            out = (target / rel).resolve()
            if base != out and base not in out.parents:
                raise ValueError("unsafe path in package")
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "xb") as dst:
                while block := src.read(1 << 20):
                    total += len(block)
                    if total > MAX_UNPACKED_BYTES:
                        raise ValueError("package too large when unpacked")
                    dst.write(block)
    if not (target / EXE_NAME).is_file() or not (target / "tessdata").is_dir():
        raise ValueError("package has no tesseract.exe and tessdata")


def install(pkg: Package, root: Path | None = None, *, fetcher: netsafe.SafeFetcher | None = None) -> Path:
    """Download, verify, unpack and rename into ``<root>/<version>``. Returns the path of ``tesseract.exe``.

    Raises ``ServiceError(502)`` and leaves nothing behind when the download or the package is bad. Older version
    folders and leftovers of interrupted installs are removed after a success.
    """
    root = root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    for stale in root.glob(_TMP_PREFIX + "*"):  # an interrupted install of an earlier run
        shutil.rmtree(stale, ignore_errors=True)
    work = root / f"{_TMP_PREFIX}{uuid.uuid4().hex[:12]}"
    work.mkdir()
    try:
        archive = work / "package.zip"
        try:
            netsafe.download_pinned(pkg.url, pkg.sha256, pkg.size, archive, fetcher=fetcher)
        except netsafe.FetchError as e:
            raise ServiceError(502, f"The OCR package could not be downloaded or verified ({e.code}).") from None
        folder = work / "unpacked"
        folder.mkdir()
        try:
            unpack(archive, folder)
        except (ValueError, zipfile.BadZipFile, OSError):
            raise ServiceError(502, "The OCR package is damaged or unsafe.") from None
        archive.unlink()
        final = root / pkg.version
        try:
            if final.exists():
                shutil.rmtree(final)
            os.replace(folder, final)
        except OSError:
            raise ServiceError(
                409, "The OCR folder is in use or cannot be replaced; close other programs and retry."
            ) from None
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    shutil.rmtree(work, ignore_errors=True)
    for old in root.iterdir():
        if old.is_dir() and old.name != pkg.version:
            shutil.rmtree(old, ignore_errors=True)
    return final / EXE_NAME
