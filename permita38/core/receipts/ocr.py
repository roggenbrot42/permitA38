"""Tesseract as a local program: availability, EXIF orientation, TSV output into ``Word``s.

No shell, an argument list, a temporary directory. Pages are handed over as PNM files, which needs no image library.
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from permita38.core.receipts import ocr_tools
from permita38.core.receipts.extraction import Word

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_tesseract(configured: str | None = None) -> str | None:
    """The one place that locates the program: ``scan.tesseract_path``, then PATH, then the downloaded copy."""
    if configured:
        p = Path(configured)
        if p.is_dir():
            p = p / ("tesseract.exe" if os.name == "nt" else "tesseract")
        return str(p) if p.is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    downloaded = ocr_tools.find_installed()
    return str(downloaded) if downloaded else None


def _tessdata_args(exe: str) -> list[str]:
    """``--tessdata-dir`` for the downloaded copy, whose language files are not where the program expects them."""
    data = ocr_tools.tessdata_dir(exe)
    return ["--tessdata-dir", str(data)] if data else []


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"  # one thread: predictable memory under the worker's address-space limit
    return env


def installed_languages(exe: str, timeout: float = 10) -> set[str]:
    try:
        out = subprocess.run(
            [exe, "--list-langs", *_tessdata_args(exe)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=_NO_WINDOW,
            env=_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    text = out.stdout + "\n" + out.stderr  # older versions print the list on stderr
    return {ln.strip() for ln in text.splitlines()[1:] if ln.strip() and " " not in ln.strip()}


def available(configured: str | None, languages: str) -> str | None:
    """Path of tesseract if it is found and every wanted language is installed, else ``None``."""
    exe = find_tesseract(configured)
    if not exe:
        return None
    have = installed_languages(exe)
    return exe if all(lang in have for lang in languages.split("+")) else None


# ---- EXIF orientation ------------------------------------------------------------------------------------------


def exif_orientation(data: bytes) -> int:
    """The EXIF orientation tag (1..8) of a JPEG, ``1`` when absent or unreadable. Standard library only."""
    if not data.startswith(b"\xff\xd8"):
        return 1
    pos, n = 2, len(data)
    while pos + 4 <= n and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xDA, 0xD9):  # image data starts
            break
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        seg = data[pos + 4 : pos + 2 + length]
        if marker == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            return _tiff_orientation(seg[6:])
        pos += 2 + length
    return 1


def _tiff_orientation(tiff: bytes) -> int:
    try:
        endian = {b"II": "<", b"MM": ">"}[tiff[:2]]
        (ifd,) = struct.unpack(endian + "I", tiff[4:8])
        (count,) = struct.unpack(endian + "H", tiff[ifd : ifd + 2])
        for i in range(min(count, 64)):
            entry = tiff[ifd + 2 + 12 * i : ifd + 14 + 12 * i]
            tag, kind, num = struct.unpack(endian + "HHI", entry[:8])
            if tag == 0x0112 and kind == 3 and num == 1:
                (value,) = struct.unpack(endian + "H", entry[8:10])
                return value if 1 <= value <= 8 else 1
    except (KeyError, struct.error):
        pass  # a damaged or unknown EXIF block means: no rotation
    return 1


# Clockwise turn that makes the stored pixels look like the browser shows them (mirrored values are not handled).
_ROTATION = {3: 180, 6: 90, 8: 270}


def rotation_degrees(orientation: int) -> int:
    return _ROTATION.get(orientation, 0)


def rotate_box(x: float, y: float, w: float, h: float, degrees: int) -> tuple[float, float, float, float]:
    """A box in fractions of the page (top left origin) after the page is turned ``degrees`` clockwise."""
    if degrees == 0:
        return x, y, w, h
    if degrees == 90:
        return 1 - y - h, x, h, w
    if degrees == 180:
        return 1 - x - w, 1 - y - h, w, h
    if degrees == 270:
        return y, 1 - x - w, h, w
    raise ValueError(degrees)


# ---- running tesseract -----------------------------------------------------------------------------------------


def pnm_bytes(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    magic = b"P5" if channels == 1 else b"P6"
    return magic + f"\n{width} {height}\n255\n".encode("ascii") + pixels


def tsv_to_words(tsv: str, page: int) -> list[Word]:
    """Level-5 rows of Tesseract's TSV as words in reading order; the page row (level 1) gives the image size."""
    width = height = 0
    words: list[Word] = []
    for line in tsv.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        try:
            level = int(cols[0])
            left, top, w, h = (int(c) for c in cols[6:10])
            conf = float(cols[10])
        except ValueError:
            continue
        if level == 1:
            width, height = w, h
        elif level == 5 and width > 0 and height > 0 and conf >= 0:
            text = cols[11].strip()
            if text:
                x0, y0 = min(max(left / width, 0.0), 1.0), min(max(top / height, 0.0), 1.0)
                x1, y1 = min(max((left + w) / width, 0.0), 1.0), min(max((top + h) / height, 0.0), 1.0)
                words.append(Word(text, page, x0, y0, x1 - x0, y1 - y0))
    return words


def run_tesseract(
    exe: str, languages: str, image: Path, page: int, timeout: float, *, dpi: int | None = None, psm: int | None = None
) -> list[Word]:
    """OCR one image file. Raises ``subprocess.TimeoutExpired`` or ``RuntimeError`` (no output content)."""
    with tempfile.TemporaryDirectory(prefix="p38ocr-") as tmp:
        out = os.path.join(tmp, "out")
        cmd = [exe, str(image), out, "-l", languages, *_tessdata_args(exe)]
        if dpi:
            cmd += ["--dpi", str(dpi)]
        if psm:
            cmd += ["--psm", str(int(psm))]
        cmd.append("tsv")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            cwd=tmp,
            creationflags=_NO_WINDOW,
            env=_env(),
        )
        tsv_path = Path(out + ".tsv")
        if proc.returncode != 0 or not tsv_path.is_file():
            raise RuntimeError("tesseract_failed")
        return tsv_to_words(tsv_path.read_text(encoding="utf-8", errors="replace"), page)


def ocr_raster(
    exe: str,
    languages: str,
    width: int,
    height: int,
    channels: int,
    pixels: bytes,
    page: int,
    timeout: float,
    dpi: int = 300,
    psm: int | None = None,
) -> list[Word]:
    with tempfile.TemporaryDirectory(prefix="p38pnm-") as tmp:
        path = Path(tmp) / "page.pnm"
        path.write_bytes(pnm_bytes(width, height, channels, pixels))
        return run_tesseract(exe, languages, path, page, timeout, dpi=dpi, psm=psm)


def ocr_image_file(
    exe: str, languages: str, data: bytes, suffix: str, timeout: float, psm: int | None = None
) -> list[Word]:
    """OCR an image as it is (PNG, WebP, an upright JPEG). Page 0."""
    with tempfile.TemporaryDirectory(prefix="p38img-") as tmp:
        path = Path(tmp) / ("image" + suffix)
        path.write_bytes(data)
        return run_tesseract(exe, languages, path, 0, timeout, psm=psm)
