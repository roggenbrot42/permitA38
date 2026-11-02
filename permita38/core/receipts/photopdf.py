"""One PDF from several JPEG photos (docs/specs/receipt-scan.md, "WP I"). Standard library only.

The JPEG bytes are embedded unchanged (``DCTDecode``); the turn found by the reader is the page's ``/Rotate``, so
the pages look like the photos did on screen. The file starts with a marker comment: only a PDF written here is
taken apart again by ``extract``.
"""

import re
import struct

MARKER = b"%permitA38-photos\n"
MAX_PHOTOS = 5
_COLOR = {1: b"/DeviceGray", 3: b"/DeviceRGB"}


class PhotoError(ValueError):
    pass


def jpeg_size(data: bytes) -> tuple[int, int, int]:
    """(width, height, components) from the first start-of-frame marker of a JPEG."""
    if not data.startswith(b"\xff\xd8"):
        raise PhotoError("not a JPEG")
    pos, n = 2, len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            raise PhotoError("bad JPEG")
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        try:
            (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
            if marker in (0xC0, 0xC1, 0xC2):  # baseline, extended and progressive frames
                precision, height, width, comps = struct.unpack(">BHHB", data[pos + 4 : pos + 10])
        except struct.error:
            raise PhotoError("truncated JPEG") from None
        if marker in (0xC0, 0xC1, 0xC2):
            if precision != 8 or comps not in _COLOR or not width or not height:
                raise PhotoError("unsupported JPEG")
            return width, height, comps
        pos += 2 + length
    raise PhotoError("bad JPEG")


def build(photos: list[tuple[bytes, int]]) -> bytes:
    """A PDF with one page per ``(jpeg bytes, degrees clockwise)``."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise PhotoError("number of photos")
    objects: list[bytes] = []  # object n is objects[n - 1]
    kids = []
    for i, (data, rotation) in enumerate(photos):
        width, height, comps = jpeg_size(data)
        if rotation not in (0, 90, 180, 270):
            raise PhotoError("rotation")
        page, content, image = 3 + 3 * i, 4 + 3 * i, 5 + 3 * i
        kids.append(b"%d 0 R" % page)
        stream = b"q %d 0 0 %d 0 0 cm /Im0 Do Q" % (width, height)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Rotate %d /Contents %d 0 R "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> >>" % (width, height, rotation, content, image)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 "
            b"/Filter /DCTDecode /Length %d >>\nstream\n"
            % (width, height, _COLOR[comps], len(data))
            + data
            + b"\nendstream"
        )
    objects.insert(0, b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(photos))
    objects.insert(0, b"<< /Type /Catalog /Pages 2 0 R >>")
    out = bytearray(b"%PDF-1.4\n" + MARKER)
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def extract(pdf: bytes) -> list[tuple[bytes, int]]:
    """The photos and turns of a PDF written by ``build``; ``PhotoError`` for any other file."""
    if not pdf.startswith(b"%PDF-1.4\n" + MARKER):
        raise PhotoError("not a photo PDF")
    rotations = [int(r) for r in re.findall(rb"/Rotate (\d+) /Contents", pdf)]
    photos = []
    for m in re.finditer(rb"/Filter /DCTDecode /Length (\d+) >>\nstream\n", pdf):
        size = int(m.group(1))
        data = pdf[m.end() : m.end() + size]
        if len(data) != size or not data.startswith(b"\xff\xd8"):
            raise PhotoError("damaged")
        photos.append(data)
    if not photos or len(photos) != len(rotations) or len(photos) > MAX_PHOTOS:
        raise PhotoError("damaged")
    return list(zip(photos, rotations))
