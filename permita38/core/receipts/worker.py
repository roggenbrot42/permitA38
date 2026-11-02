"""The scan worker: runs in its own process, reads one file and prints ``to_jsonable(ReadResult)`` to stdout.

Started by ``runner.read_file`` through the hidden CLI subcommand ``permita38 scan-worker <path>``. Memory, CPU and
open files are limited on POSIX; the parent enforces the timeout on every platform. Nothing read from the file is
written to stderr or any log (docs/specs/receipt-scan.md, "Worker process").
"""

import json
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from permita38.core.receipts import ocr, qrcodes, textrules
from permita38.core.receipts.einvoice import EInvoiceError, parse_einvoice
from permita38.core.receipts.extraction import Code, ReadResult, Word, cap_words, to_jsonable

MAX_FILE_BYTES = 50_000_000
MAX_TEXT_CHARS = 200_000
MAX_OUTPUT_BYTES = 900_000  # the parent refuses more than 1 MB
MEMORY_LIMIT = 1 << 30
DEFAULT_TIMEOUT_S = 120
OCR_MODES = (6, 3, 11)  # Tesseract page segmentation modes: block of text, automatic, sparse text


def apply_limits(timeout_s: int) -> None:
    """RLIMIT_AS 1 GiB, RLIMIT_CPU and RLIMIT_NOFILE on POSIX; nothing on Windows."""
    try:
        import resource
    except ImportError:  # Windows
        return
    for name, value in (
        ("RLIMIT_AS", MEMORY_LIMIT),
        ("RLIMIT_CPU", max(timeout_s, 5)),
        ("RLIMIT_NOFILE", 64),
    ):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            _soft, hard = resource.getrlimit(limit)
            new = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(limit, (new, hard))
        except (ValueError, OSError):
            pass


def _kind(head: bytes) -> str:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<") or head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:2] in (b"<\x00", b"\x00<"):
        return "xml"
    return "unknown"


def _fail(code: str, total: int = 0) -> ReadResult:
    return ReadResult(engine="none", pages_total=total, error=code)


def read(path: str, *, tesseract: str | None, languages: str, timeout_s: int) -> ReadResult:
    started = time.monotonic()

    def left() -> float:
        return max(timeout_s - 5 - (time.monotonic() - started), 1.0)

    try:
        size = Path(path).stat().st_size
        if size == 0:
            return _fail("empty")
        if size > MAX_FILE_BYTES:
            return _fail("too_large")
        data = Path(path).read_bytes()
    except OSError:
        return _fail("unreadable")
    kind = _kind(data[:16])
    if kind == "xml":
        try:
            return ReadResult(engine="einvoice", extraction=parse_einvoice(data), pages_read=1, pages_total=1)
        except EInvoiceError:
            return _fail("unreadable")
    if kind == "pdf":
        return _read_pdf(path, tesseract, languages, left)
    if kind in ("png", "jpeg", "webp"):
        return _read_image(data, kind, tesseract, languages, left)
    return _fail("unreadable")


def _read_pdf(path: str, tesseract: str | None, languages: str, left) -> ReadResult:  # type: ignore[no-untyped-def]
    from permita38.core.receipts import pdfread

    try:
        pdf = pdfread.open_pdf(path)
    except pdfread.PdfUnreadable:
        return _fail("unreadable")
    try:
        total = len(pdf)
        if total == 0:
            return _fail("unreadable")
        words, read_pages = pdfread.read_text(pdf)
        invoice = pdfread.embedded_einvoice(pdf)
        if invoice is not None:
            return ReadResult("einvoice", words, invoice, read_pages, total)
        if pdfread.usable_chars(words) >= pdfread.MIN_TEXT_CHARS:
            result = ReadResult("pdf_text", words, None, read_pages, total)
            result.codes = _pdf_codes(pdf, min(total, pdfread.MAX_OCR_PAGES))
            return result
        if not tesseract:
            return ReadResult("none", [], None, 0, total, "ocr_unavailable")
        count = min(total, pdfread.MAX_OCR_PAGES)

        def run_mode(psm: int) -> list[Word]:
            words: list[Word] = []
            for index in range(count):
                page = pdf[index]
                try:
                    w, h, ch, pixels = pdfread.render_raster(page, pdfread.OCR_DPI, gray=True)
                finally:
                    page.close()
                words.extend(ocr.ocr_raster(tesseract, languages, w, h, ch, pixels, index, left(), psm=psm))
            return words

        try:
            first, others = _ocr_modes(run_mode, left)
        except subprocess.TimeoutExpired:
            return ReadResult("none", [], None, 0, total, "timeout")
        except (RuntimeError, OSError):
            return ReadResult("none", [], None, 0, total, "ocr_failed")
        if pdfread.usable_chars(first) == 0:
            return ReadResult("none", [], None, count, total, "unreadable")
        result = ReadResult("ocr", first, None, count, total, alternatives=others)
        result.codes = _pdf_codes(pdf, count)
        return result
    finally:
        pdf.close()


def _pdf_codes(pdf, count: int) -> list[Code]:  # type: ignore[no-untyped-def]
    """QR codes on the pages of a PDF (rendered gray at 200 dpi). A page that cannot be rendered has none."""
    from permita38.core.receipts import pdfread

    codes: list[Code] = []
    for index in range(count):
        try:
            page = pdf[index]
            try:
                w, h, _ch, pixels = pdfread.render_raster(page, 200, gray=True)
            finally:
                page.close()
            codes.extend(qrcodes.find_codes(w, h, pixels, index))
        except Exception:
            continue
    return codes[: qrcodes.MAX_CODES]


_TOTAL_WORD = re.compile(r"total|summe|gesamt|zu zahlen|betrag", re.IGNORECASE)


def _plausible(words: list[Word]) -> bool:
    """The words read like a receipt: a total label, or at least 5 amounts."""
    text = " ".join(w.text for w in words)
    if _TOTAL_WORD.search(text):
        return True
    return sum(1 for w in words if textrules.parse_amount(w.text) is not None) >= 5


def _read_image(data: bytes, kind: str, tesseract: str | None, languages: str, left) -> ReadResult:  # type: ignore[no-untyped-def]
    if not tesseract:
        return ReadResult("none", [], None, 0, 1, "ocr_unavailable")
    try:
        if kind == "jpeg":
            return _read_jpeg(data, tesseract, languages, left)

        def run_mode(psm: int) -> list[Word]:
            suffix = {"png": ".png", "webp": ".webp"}[kind]
            return ocr.ocr_image_file(tesseract, languages, data, suffix, left(), psm=psm)

        words, others = _ocr_modes(run_mode, left)
    except subprocess.TimeoutExpired:
        return ReadResult("none", [], None, 0, 1, "timeout")
    except Exception:  # undecodable picture, tesseract could not read it
        return ReadResult("none", [], None, 0, 1, "unreadable")
    if sum(len(w.text) for w in words) == 0:
        return ReadResult("none", [], None, 1, 1, "unreadable")
    return ReadResult("ocr", words, None, 1, 1, alternatives=others)


def _read_jpeg(data: bytes, tesseract: str, languages: str, left) -> ReadResult:  # type: ignore[no-untyped-def]
    """A photo is tried at the angles that suit its shape; the first angle that reads like a receipt wins.

    EXIF is applied first (browsers show the photo turned). A landscape raster then tries 90 and 270 degrees more,
    a portrait one 0 and 180 (phones do not always record the rotation). Boxes are fractions of the turned
    photo, which is also what the page image route serves, so the overlay needs no conversion."""
    from permita38.core.receipts import pdfread

    base = ocr.rotation_degrees(ocr.exif_orientation(data))
    rasters: dict[int, tuple[int, int, int, bytes]] = {}

    def raster(degrees: int) -> tuple[int, int, int, bytes]:
        if degrees not in rasters:
            rasters[degrees] = pdfread.render_jpeg(data, degrees)
        return rasters[degrees]

    def read_at(degrees: int, psm: int) -> list[Word]:
        w, h, ch, pixels = raster(degrees)
        return ocr.ocr_raster(tesseract, languages, w, h, ch, pixels, 0, left(), dpi=300, psm=psm)

    w0, h0, _c, _p = raster(base)
    extras = (90, 270) if w0 > h0 else (0, 180)
    chosen, first, best_count = base, None, -1
    started = time.monotonic()
    for extra in extras:
        degrees = (base + extra) % 360
        words = read_at(degrees, OCR_MODES[0])
        count = sum(1 for w in words if textrules.parse_amount(w.text) is not None)
        if first is None or count > best_count:
            chosen, first, best_count = degrees, words, count
        if _plausible(words):
            chosen, first = degrees, words
            break
        if left() < 5:
            break
    took = max(time.monotonic() - started, 0.5)
    assert first is not None
    others: list[list[Word]] = []
    for psm in OCR_MODES[1:]:
        if left() <= 2 * took / len(extras) + 1:
            break
        try:
            more = read_at(chosen, psm)
        except Exception:
            break
        if sum(len(w.text) for w in more) > 0:
            others.append(more)
    if sum(len(w.text) for w in first) == 0:
        return ReadResult("none", [], None, 1, 1, "unreadable")
    result = ReadResult("ocr", first, None, 1, 1, alternatives=others)
    result.rotation = chosen
    w, h, _ch, pixels = raster(chosen)
    result.codes = qrcodes.find_codes(w, h, pixels)
    if not result.codes:
        for extra in (90, 180, 270):  # a code is found at any angle; the box is then given at the chosen one only
            if extra == 0:
                continue
            w, h, _ch, pixels = raster((chosen + extra) % 360)
            result.codes = qrcodes.find_codes(w, h, pixels)
            if result.codes:
                result.codes = [replace(c, x=0, y=0, w=0, h=0) for c in result.codes]  # no box: another angle
                break
    return result


def _ocr_modes(run_mode, left) -> tuple[list[Word], list[list[Word]]]:  # type: ignore[no-untyped-def]
    """Run the page modes of ``OCR_MODES`` (6, 3, 11): the first result, then the others while there is time.

    An error in the first mode is raised; an error or a timeout in a later one only ends the loop. A later mode starts
    only if the time left is more than twice what the first one took."""
    started = time.monotonic()
    first = run_mode(OCR_MODES[0])
    took = max(time.monotonic() - started, 0.5)
    others: list[list[Word]] = []
    for psm in OCR_MODES[1:]:
        if left() <= 2 * took + 1:
            break
        try:
            words = run_mode(psm)
        except Exception:
            break
        if sum(len(w.text) for w in words) > 0:
            others.append(words)
    return first, others


def _round_words(result: ReadResult) -> ReadResult:
    def rounded(words: list[Word]) -> list[Word]:
        return [replace(w, x=round(w.x, 4), y=round(w.y, 4), w=round(w.w, 4), h=round(w.h, 4)) for w in words]

    result.words = rounded(result.words)
    result.alternatives = [rounded(a) for a in result.alternatives]
    return result


def cap_text(result: ReadResult, max_chars: int = MAX_TEXT_CHARS) -> ReadResult:
    """At most ``max_chars`` characters of words: whole trailing pages are dropped and ``pages_read`` says so."""
    while sum(len(w.text) for w in result.words) > max_chars and result.words:
        last = result.words[-1].page
        result.words = [w for w in result.words if w.page != last]
        result.pages_read = min(result.pages_read, last)
    return result


def serialise(result: ReadResult, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    """JSON for stdout under ``max_bytes``; if the words do not fit, trailing pages are dropped and counted unread."""
    result = cap_text(cap_words(_round_words(result)))
    result.alternatives = [a for a in result.alternatives if sum(len(w.text) for w in a) <= MAX_TEXT_CHARS]
    while True:
        text = json.dumps(to_jsonable(result), ensure_ascii=True, separators=(",", ":"))
        if len(text) > max_bytes and result.alternatives:
            result.alternatives.pop()  # the other page modes go first, the main reading stays whole
            continue
        if len(text) <= max_bytes or not result.words:
            return text
        last = result.words[-1].page
        result.words = [w for w in result.words if w.page != last]
        result.pages_read = min(result.pages_read, last)
        if result.extraction is None and not result.words:
            result.engine, result.error = "none", "too_large"


def run(args) -> int:  # type: ignore[no-untyped-def]
    """Entry of ``permita38 scan-worker``. ``args``: path, page, tesseract, languages, timeout."""
    apply_limits(args.timeout)
    out = sys.stdout.buffer
    try:
        if args.page is not None:
            from permita38.core.receipts import pagepng, pdfread

            try:
                out.write(pagepng.page_png(args.path, args.page, getattr(args, "rotation", None)))
            except (IndexError, pdfread.PdfUnreadable):
                return 3
            out.flush()
            return 0
        tess = ocr.available(args.tesseract, args.languages)
        result = read(args.path, tesseract=tess, languages=args.languages, timeout_s=args.timeout)
        text = serialise(result)
    except MemoryError:
        return 4
    out.write(text.encode("ascii"))
    out.flush()
    return 0
