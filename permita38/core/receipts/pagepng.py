"""PNG writer on the standard library only (no Pillow), and the page image of a PDF at 150 dpi."""

import struct
import zlib

from permita38.core.receipts import pdfread

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPE = {1: 0, 3: 2}  # grayscale, truecolour


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """``pixels`` is ``height`` packed rows of ``width * channels`` bytes (1 = gray, 3 = RGB)."""
    if channels not in _COLOR_TYPE or width < 1 or height < 1 or len(pixels) != width * height * channels:
        raise ValueError("bad raster")
    stride = width * channels
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))  # filter type 0 per row
    header = struct.pack(">IIBBBBB", width, height, 8, _COLOR_TYPE[channels], 0, 0, 0)
    return _SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def page_png(pdf_path: str, page_index: int, rotation: int | None = None, dpi: int = pdfread.PAGE_PNG_DPI) -> bytes:
    """The page as a PNG. Raises ``IndexError`` for a page that does not exist, ``PdfUnreadable`` for a bad file.

    A JPEG photo is turned ``rotation`` degrees clockwise (colour, its own pixel size)."""
    if rotation is not None:
        if page_index != 0 or rotation not in (0, 90, 180, 270):
            raise IndexError(page_index)
        with open(pdf_path, "rb") as f:
            head = f.read(3)
            f.seek(0)
            data = f.read()
        if not head.startswith(b"\xff\xd8\xff"):
            raise pdfread.PdfUnreadable
        w, h, channels, pixels = pdfread.render_jpeg(data, rotation, gray=False)
        return encode_png(w, h, channels, pixels)
    pdf = pdfread.open_pdf(pdf_path)
    try:
        if not 0 <= page_index < len(pdf):
            raise IndexError(page_index)
        page = pdf[page_index]
        try:
            w, h, channels, pixels = pdfread.render_raster(page, dpi, gray=False)
        finally:
            page.close()
        return encode_png(w, h, channels, pixels)
    finally:
        pdf.close()
