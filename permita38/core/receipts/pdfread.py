"""PDF reader on pypdfium2: embedded e-invoice XML, text layer as positioned words, page rendering.

Runs only inside the worker process (docs/specs/receipt-scan.md, "Worker process"). Positions are fractions of the page,
from the top left, so they do not depend on zoom or resolution.
"""

from collections.abc import Iterator
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from permita38.core.receipts.einvoice import EInvoiceDoc, EInvoiceError, read_einvoice
from permita38.core.receipts.extraction import Extraction, Word

MAX_TEXT_PAGES = 50
MIN_TEXT_CHARS = 50
MAX_TEXT_CHARS = 200_000
MAX_OCR_PAGES = 10
OCR_DPI = 300
PAGE_PNG_DPI = 150
MAX_RENDER_PIXELS = 40_000_000  # a page larger than this at the wanted dpi is rendered at a lower scale


class PdfUnreadable(Exception):
    pass


def open_pdf(path: str) -> Any:
    try:
        return pdfium.PdfDocument(path)
    except Exception:  # pdfium raises PdfiumError for corrupt, encrypted or truncated files
        raise PdfUnreadable from None


def embedded_einvoice(pdf: Any) -> Extraction | None:
    """The first attached XML that is a supported e-invoice (ZUGFeRD / Factur-X), else ``None``."""
    doc = embedded_einvoice_doc(pdf)
    return doc.extraction if doc is not None else None


def embedded_einvoice_doc(pdf: Any) -> EInvoiceDoc | None:
    """Like ``embedded_einvoice`` with the buyer and the type code (``einvoice.read_einvoice``)."""
    try:
        count = pdf.count_attachments()
    except Exception:
        return None
    for i in range(min(count, 20)):
        try:
            att = pdf.get_attachment(i)
            name = (att.get_name() or "").lower()
            if not name.endswith(".xml"):
                continue
            return read_einvoice(_attachment_bytes(att))
        except Exception:  # not an invoice, refused, or unreadable: try the next attachment
            continue
    return None


def _attachment_bytes(att: Any) -> bytes:
    import ctypes

    n = ctypes.c_ulong(0)
    pdfium_c.FPDFAttachment_GetFile(att.raw, None, 0, n)
    if n.value == 0 or n.value > 20_000_000:
        raise EInvoiceError("too_large")
    buf = (ctypes.c_ubyte * n.value)()
    out = ctypes.c_ulong(0)
    pdfium_c.FPDFAttachment_GetFile(att.raw, buf, n.value, out)
    return bytes(buf)[: out.value]


_BREAKS = frozenset(" \t\r\n\x00\xa0﻿\x0b\x0c")


class PageTooLarge(Exception):
    """A page holds more characters than the text cap allows; reading it character by character would be slow."""


def page_words(page: Any, page_index: int, max_chars: int = MAX_TEXT_CHARS) -> Iterator[Word]:
    """Words of one page with their boxes. A word ends at whitespace or when the next character starts a new line."""
    tp = page.get_textpage()
    try:
        left, bottom, right, top = page.get_bbox()
        pw, ph = right - left, top - bottom
        if pw <= 0 or ph <= 0:
            return
        n = tp.count_chars()
        if n > max_chars:
            raise PageTooLarge
        chars: list[str] = []
        box: list[float] | None = None  # l, b, r, t of the current word

        def flush() -> Word | None:
            nonlocal chars, box
            word = None
            if chars and box is not None:
                x0 = min(max((box[0] - left) / pw, 0.0), 1.0)
                x1 = min(max((box[2] - left) / pw, 0.0), 1.0)
                y0 = min(max(1.0 - (box[3] - bottom) / ph, 0.0), 1.0)
                y1 = min(max(1.0 - (box[1] - bottom) / ph, 0.0), 1.0)
                word = Word("".join(chars), page_index, x0, y0, x1 - x0, y1 - y0)
            chars, box = [], None
            return word

        # get_text_range may drop or add characters (hyphens, line breaks); the char index is what the boxes use,
        # so the text is read per character from the same index.
        for i in range(n):
            ch = tp.get_text_range(i, 1)
            if not ch or ch in _BREAKS or ch.isspace():
                w = flush()
                if w:
                    yield w
                continue
            cl, b, r, t = tp.get_charbox(i)
            if box is not None:
                if b > box[3] or t < box[1]:  # no vertical overlap: next line
                    w = flush()
                    if w:
                        yield w
            if box is None:
                box = [cl, b, r, t]
            else:
                box = [min(box[0], cl), min(box[1], b), max(box[2], r), max(box[3], t)]
            chars.append(ch)
        w = flush()
        if w:
            yield w
    finally:
        tp.close()


def read_text(pdf: Any, max_pages: int = MAX_TEXT_PAGES, max_chars: int = MAX_TEXT_CHARS) -> tuple[list[Word], int]:
    """Words of up to ``max_pages`` pages, capped at ``max_chars`` characters; returns (words, pages fully read)."""
    words: list[Word] = []
    chars = 0
    read = 0
    for index in range(min(len(pdf), max_pages)):
        page = pdf[index]
        try:
            page_list = list(page_words(page, index, max_chars))
        except PageTooLarge:
            break
        finally:
            page.close()
        size = sum(len(w.text) for w in page_list)
        if chars + size > max_chars:
            break
        words.extend(page_list)
        chars += size
        read += 1
    return words, read


def usable_chars(words: list[Word]) -> int:
    return sum(len(w.text) for w in words)


def render_raster(page: Any, dpi: int, *, gray: bool) -> tuple[int, int, int, bytes]:
    """(width, height, channels, packed rows) of a page at ``dpi``; large pages get a smaller scale."""
    scale = dpi / 72
    width, height = page.get_size()
    if width * height * scale * scale > MAX_RENDER_PIXELS:
        scale = (MAX_RENDER_PIXELS / (width * height)) ** 0.5
    if gray:
        bitmap = page.render(scale=scale, force_bitmap_format=pdfium_c.FPDFBitmap_Gray, may_draw_forms=False)
        step = 1
    else:
        bitmap = page.render(scale=scale, rev_byteorder=True, may_draw_forms=False)
        step = 3 if bitmap.format == pdfium_c.FPDFBitmap_BGR else 4
    w, h, stride = bitmap.width, bitmap.height, bitmap.stride
    raw = bytes(bitmap.buffer)
    bitmap.close()
    rows = []
    for y in range(h):
        row = raw[y * stride : y * stride + w * step]
        if step == 4:  # drop the fourth byte (alpha or padding)
            packed = bytearray(w * 3)
            packed[0::3], packed[1::3], packed[2::3] = row[0::4], row[1::4], row[2::4]
            row = bytes(packed)
        rows.append(row)
    return w, h, (1 if gray else 3), b"".join(rows)


def render_jpeg(data: bytes, degrees: int, gray: bool = True) -> tuple[int, int, int, bytes]:
    """A JPEG turned ``degrees`` clockwise as a gray raster, for OCR. pdfium decodes and rotates; no image library."""
    import io

    doc = pdfium.PdfDocument.new()
    try:
        image = pdfium.PdfImage.new(doc)
        image.load_jpeg(io.BytesIO(data))
        w, h = image.get_px_size()
        if w < 1 or h < 1 or w * h > 200_000_000:
            raise PdfUnreadable
        image.set_matrix(pdfium.PdfMatrix().scale(w, h))
        page = doc.new_page(w, h)
        page.insert_obj(image)
        page.gen_content()
        page.set_rotation(degrees)
        # OCR reads the full size; the preview is cut to 1800 px on the long side so that its PNG stays small
        dpi = 72 if gray else max(1, int(72 * min(1.0, 1800 / max(w, h))))
        return render_raster(page, dpi, gray=gray)
    except PdfUnreadable:
        raise
    except Exception:
        raise PdfUnreadable from None
    finally:
        doc.close()
