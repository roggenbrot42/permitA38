"""Logo embedding: JPEG marker parsing in Pdf.jpeg and PNG decoding in Pdf.png (hand-built files, stdlib only)."""

import re
import struct
import time
import zlib

import pytest

from permita38.core import pdf as pdfmod
from permita38.core.pdf import Pdf


def seg(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def sof(nf: int, w: int = 40, h: int = 20, marker: int = 0xC0) -> bytes:
    comps = b"".join(bytes([i + 1, 0x11, 0]) for i in range(nf))
    return seg(marker, struct.pack(">BHHB", 8, h, w, nf) + comps)


SOI, EOI = b"\xff\xd8", b"\xff\xd9"
SOS = seg(0xDA, b"\x00" * 4)
APP0 = seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
APP14 = seg(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x02")


def jpeg(*parts: bytes) -> bytes:
    return SOI + b"".join(parts) + SOS + b"\x00\x01" + EOI


def place(data: bytes) -> tuple[bool, Pdf]:
    pdf = Pdf()
    return pdf.jpeg(data, 10, 10, 100, 50), pdf


@pytest.mark.parametrize(
    ("nf", "space"), [(1, b"/ColorSpace /DeviceGray"), (3, b"/ColorSpace /DeviceRGB"), (4, b"/ColorSpace /DeviceCMYK")]
)
def test_colour_space_follows_component_count(nf, space):
    ok, pdf = place(jpeg(APP0, sof(nf)))
    out = pdf.output()
    assert ok and space in out and b"/Subtype /Image" in out and b"/Width 40 /Height 20" in out
    assert b"/Decode" not in out


def test_adobe_cmyk_is_decoded_inverted_plain_cmyk_is_not():
    _, adobe = place(jpeg(APP14, sof(4)))
    assert b"/ColorSpace /DeviceCMYK" in adobe.output() and b"/Decode [1 0 1 0 1 0 1 0]" in adobe.output()
    _, plain = place(jpeg(APP0, sof(4)))
    assert b"/Decode" not in plain.output()


@pytest.mark.parametrize("marker", [0xC1, 0xC2])
def test_extended_and_progressive_frames_are_accepted(marker):
    ok, pdf = place(jpeg(sof(3, marker=marker)))
    assert ok and len(pdf.images) == 1


def test_fill_bytes_before_a_marker_are_skipped():
    data = SOI + APP0 + b"\xff\xff\xff" + sof(3)[1:] + SOS + EOI
    ok, pdf = place(data)
    assert ok and b"/DeviceRGB" in pdf.output()


def test_dht_marker_is_not_taken_for_a_frame():
    ok, pdf = place(jpeg(seg(0xC4, b"\x00" * 17), sof(1, 7, 9)))
    assert ok and b"/Width 7 /Height 9" in pdf.output()


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 30,
        b"",
        SOI,
        SOI + APP0,  # no frame at all
        (SOI + sof(3))[:-4],  # truncated frame header
        SOI + SOS + sof(3) + EOI,  # SOS before any SOF
        SOI + EOI,
        jpeg(sof(2)),
        jpeg(sof(3, w=0)),
        jpeg(sof(3, h=0)),
        jpeg(sof(0)),
        jpeg(seg(0xC0, struct.pack(">BHHBBBB", 12, 20, 40, 1, 1, 0x11, 0))),  # precision 12
        jpeg(sof(3, marker=0xC3)),  # lossless
        jpeg(sof(3, marker=0xC9)),  # arithmetic
        SOI + seg(0xE0, b"JFIF" + b"\x00" * 4)[:-3],  # APP segment runs past the end
    ],
)
def test_unusable_data_returns_false_and_adds_no_image(data):
    ok, pdf = place(data)
    assert ok is False and pdf.images == [] and b"/Subtype /Image" not in pdf.output()


@pytest.mark.parametrize("marker", [0xC8, 0xCC])
def test_c8_and_cc_are_not_taken_for_a_frame(marker):
    ok, pdf = place(jpeg(seg(marker, b"\x00" * 6), sof(1, 7, 9)))
    assert ok and b"/Width 7 /Height 9" in pdf.output()


def test_rst_and_tem_standalone_markers_before_the_frame_are_accepted():
    data = SOI + b"\xff\xd0\xff\xd7\xff\x01" + sof(3) + SOS + EOI
    ok, pdf = place(data)
    assert ok and b"/DeviceRGB" in pdf.output()


def test_logo_reaches_the_invoice_pdf(boss_client):
    c = boss_client
    up = c.post("/api/attachments", files={"file": ("logo.jpg", jpeg(APP0, sof(3, 80, 40)), "image/jpeg")})
    assert up.status_code == 200, up.text
    assert c.put("/api/settings", json={"logo": up.json()["name"]}).status_code == 200
    cust = c.post("/api/customers", json={"name": "ACME GmbH", "rate": 100}).json()
    inv = c.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "lines": [{"description": "Dev", "qty": 1, "unit": "h", "price": 100}]},
    ).json()
    pdf = c.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200
    assert b"/Subtype /Image" in pdf.content and b"/ColorSpace /DeviceRGB" in pdf.content


# ---------------------------------------------------------------- PNG

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(
    ctype, depth, w, h, raw, plte=None, trns=None, interlace=0, idat=None, iend=True, ihdr_first=True, ihdr_len_ok=True
) -> bytes:
    """A PNG from already filtered raw scanlines (each row starts with its filter byte)."""
    ihdr = struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace)
    if not ihdr_len_ok:
        ihdr += b"\x00"
    parts = [chunk(b"IHDR", ihdr)] if ihdr_first else [chunk(b"tEXt", b"k\x00v"), chunk(b"IHDR", ihdr)]
    if plte is not None:
        parts.append(chunk(b"PLTE", plte))
    if trns is not None:
        parts.append(chunk(b"tRNS", trns))
    parts.append(chunk(b"IDAT", zlib.compress(raw) if idat is None else idat))
    if iend:
        parts.append(chunk(b"IEND", b""))
    return PNG_SIG + b"".join(parts)


def put(data: bytes) -> tuple[bool, Pdf]:
    pdf = Pdf()
    return pdf.png(data, 10, 10, 100, 50), pdf


def streams(pdf: Pdf) -> tuple[bytes, bytes | None]:
    """Decompressed colour stream and SMask stream (None) of the only image."""
    (rec,) = pdf.images
    return zlib.decompress(rec["data"]), (zlib.decompress(rec["smask"]) if rec["smask"] else None)


def test_each_supported_type_embeds_with_the_right_colour_space():
    cases = [
        (png(0, 8, 2, 1, b"\x00\x01\x02"), b"/ColorSpace /DeviceGray"),
        (png(2, 8, 2, 1, b"\x00" + bytes(6)), b"/ColorSpace /DeviceRGB"),
        (png(4, 8, 2, 1, b"\x00\x00\xff\x00\xff"), b"/ColorSpace /DeviceGray"),
        (png(6, 8, 2, 1, b"\x00" + bytes([0, 0, 0, 255] * 2)), b"/ColorSpace /DeviceRGB"),
        (png(3, 8, 2, 1, b"\x00\x00\x01", plte=bytes(6)), b"/ColorSpace [/Indexed /DeviceRGB 1 <000000000000>]"),
    ]
    for data, space in cases:
        ok, pdf = put(data)
        out = pdf.output()
        assert ok and space in out and b"/Filter /FlateDecode" in out and b"/Width 2 /Height 1" in out
        assert b"/SMask" not in out  # fully opaque


def test_alpha_sources_produce_a_soft_mask():
    rgba = png(6, 8, 2, 1, b"\x00" + bytes([1, 2, 3, 255, 4, 5, 6, 0]))
    gray_alpha = png(4, 8, 2, 1, b"\x00" + bytes([9, 255, 8, 7]))
    pal = png(3, 8, 3, 1, b"\x00\x00\x01\x02", plte=bytes(9), trns=bytes([255, 0]))
    rgb_key = png(2, 8, 2, 1, b"\x00" + bytes([1, 2, 3, 4, 5, 6]), trns=struct.pack(">HHH", 4, 5, 6))
    gray_key = png(0, 8, 3, 1, b"\x00\x07\x08\x07", trns=struct.pack(">H", 7))
    for data in (rgba, gray_alpha, pal, rgb_key, gray_key):
        ok, pdf = put(data)
        assert ok and b"/SMask" in pdf.output()
    assert streams(put(rgba)[1])[1] == bytes([255, 0])
    assert streams(put(gray_alpha)[1])[1] == bytes([255, 7])
    assert streams(put(pal)[1])[1] == bytes([255, 0, 255])  # index 2 has no tRNS entry: opaque
    assert streams(put(rgb_key)[1])[1] == bytes([255, 0])
    assert streams(put(gray_key)[1])[1] == bytes([0, 255, 0])


def test_fully_opaque_rgba_has_no_soft_mask():
    ok, pdf = put(png(6, 8, 2, 1, b"\x00" + bytes([1, 2, 3, 255, 4, 5, 6, 255])))
    assert ok and b"/SMask" not in pdf.output()


# 3x2 RGBA, hand-computed. Pixels (R,G,B,A):
#   row0: (10,20,30,255) (12,24,36,128) (15,30,45,0)
#   row1: (11,22,33,255) (14,26,40,128) (16,31,47,64)
RGB_EXPECTED = bytes([10, 20, 30, 12, 24, 36, 15, 30, 45, 11, 22, 33, 14, 26, 40, 16, 31, 47])
ALPHA_EXPECTED = bytes([255, 128, 0, 255, 128, 64])
ROW0 = [10, 20, 30, 255, 12, 24, 36, 128, 15, 30, 45, 0]
ROW1 = [11, 22, 33, 255, 14, 26, 40, 128, 16, 31, 47, 64]
FILTERED = {
    0: (ROW0, ROW1),
    1: ([10, 20, 30, 255, 2, 4, 6, 129, 3, 6, 9, 128], [11, 22, 33, 255, 3, 4, 7, 129, 2, 5, 7, 192]),
    2: (ROW0, [1, 2, 3, 0, 2, 2, 4, 0, 1, 1, 2, 64]),
    3: ([10, 20, 30, 255, 7, 14, 21, 1, 9, 18, 27, 192], [6, 12, 18, 128, 3, 3, 6, 193, 2, 3, 5, 0]),
    4: ([10, 20, 30, 255, 2, 4, 6, 129, 3, 6, 9, 128], [1, 2, 3, 0, 2, 2, 4, 0, 1, 1, 2, 64]),
}


@pytest.mark.parametrize("ft", [0, 1, 2, 3, 4])
def test_unfilter_oracle_rgba(ft):
    r0, r1 = FILTERED[ft]
    raw = bytes([ft] + r0 + [ft] + r1)
    ok, pdf = put(png(6, 8, 3, 2, raw))
    colour, alpha = streams(pdf)
    assert ok and colour == RGB_EXPECTED and alpha == ALPHA_EXPECTED


def test_unfilter_oracle_paeth_picks_a_b_and_c():
    # gray 4x2; row0 stored unfiltered, row1 Paeth. Predictors chosen by hand: b, a, b, c.
    raw = bytes([0, 100, 100, 20, 30, 4, 5, 0, 246, 30])
    ok, pdf = put(png(0, 8, 4, 2, raw))
    assert ok and streams(pdf)[0] == bytes([100, 100, 20, 30, 105, 105, 10, 50])


def test_palette_two_bit_unpacks_to_literal_indices():
    plte = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])
    raw = bytes([0, 0x1B, 0x40, 0, 0xF2, 0x80])  # rows: 0,1,2,3,1 / 3,3,0,2,2
    ok, pdf = put(png(3, 2, 5, 2, raw, plte=plte))
    assert ok and streams(pdf)[0] == bytes([0, 1, 2, 3, 1, 3, 3, 0, 2, 2])
    assert b"/ColorSpace [/Indexed /DeviceRGB 3 <ff0000" + b"00ff000000ff090909>]" in pdf.output()


def test_palette_index_beyond_palette_is_refused():
    ok, pdf = put(png(3, 8, 2, 1, b"\x00\x00\x05", plte=bytes(6)))
    assert ok is False and pdf.images == []


def good_rgba() -> bytes:
    return png(6, 8, 2, 1, b"\x00" + bytes(8))


@pytest.mark.parametrize(
    "data",
    [
        png(6, 16, 2, 1, b"\x00" + bytes(16)),  # 16-bit
        png(6, 8, 2, 1, b"\x00" + bytes(8), interlace=1),  # Adam7
        png(0, 4, 2, 1, b"\x00\x00"),  # 4-bit gray
        png(6, 8, 2, 1, b"\x05" + bytes(8)),  # unknown filter byte
        png(6, 8, 2, 1, b"\x00" + bytes(8), ihdr_first=False),
        png(6, 8, 2, 1, b"\x00" + bytes(8), ihdr_len_ok=False),
        good_rgba()[:-12],  # IEND missing
        good_rgba()[:40],  # truncated inside a chunk
        png(6, 8, 2, 1, b"\x00" + bytes(8), iend=False),
        png(6, 8, 0, 1, b"\x00"),  # width 0
        png(6, 8, 4001, 1, b"\x00" + bytes(4001 * 4)),  # width 4001
        png(6, 8, 2, 4001, b"\x00"),  # height 4001
        png(0, 8, 3000, 3000, b"\x00"),  # too many pixels (also too little data)
        png(6, 8, 10, 10, b"", idat=zlib.compress(bytes(10 * 41 + 1))),  # inflates past expected
        png(6, 8, 10, 10, b"", idat=zlib.compress(bytes(10 * 41 - 1))),  # too few bytes
        png(3, 8, 2, 1, b"\x00\x00\x01"),  # palette without PLTE
        png(6, 8, 2, 1, b"", idat=b"not zlib data"),
        PNG_SIG,
        b"",
        jpeg(APP0, sof(3)),  # JPEG bytes to png()
    ],
)
def test_unsupported_png_returns_false_and_adds_no_image(data):
    ok, pdf = put(data)
    assert ok is False and pdf.images == [] and b"/Subtype /Image" not in pdf.output()


def test_decompression_bomb_is_capped():
    bomb = zlib.compress(bytes(50_000_000), 9)  # ~50 KB that inflates to 50 MB
    start = time.perf_counter()
    ok, pdf = put(png(6, 8, 10, 10, b"", idat=bomb))
    assert ok is False and pdf.images == [] and time.perf_counter() - start < 1


def test_bad_crc_and_extra_chunks_are_ignored():
    data = good_rgba()
    bad = bytearray(data)
    bad[29] ^= 0xFF  # IHDR CRC starts at 8 signature + 8 header + 13 body = 29
    with_extra = PNG_SIG + data[8:29] + data[29:33] + chunk(b"gAMA", bytes(4)) + data[33:]
    assert put(bytes(bad))[0] and put(with_extra)[0]


def test_multiple_idat_chunks_are_concatenated():
    z = zlib.compress(b"\x00" + bytes([1, 2, 3, 4, 5, 6, 7, 8]))
    data = (
        PNG_SIG
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", z[:5])
        + chunk(b"IDAT", z[5:])
        + chunk(b"IEND", b"")
    )
    ok, pdf = put(data)
    assert ok and streams(pdf)[0] == bytes([1, 2, 3, 5, 6, 7])


def test_image_dispatches_on_magic_bytes():
    pdf = Pdf()
    assert pdf.image(good_rgba(), 0, 0, 10, 10)
    assert pdf.image(jpeg(APP0, sof(3)), 0, 0, 10, 10)
    assert pdf.image(b"GIF89a" + bytes(40), 0, 0, 10, 10) is False
    assert pdf.image(b"", 0, 0, 10, 10) is False
    assert len(pdf.images) == 2
    out = pdf.output()
    assert b"/Filter /FlateDecode" in out and b"/Filter /DCTDecode" in out


def test_soft_mask_object_precedes_and_is_referenced_by_its_image():
    ok, pdf = put(png(6, 8, 2, 1, b"\x00" + bytes([1, 2, 3, 0, 4, 5, 6, 255])))
    out = pdf.output()
    m = re.search(rb"/SMask (\d+) 0 R", out)
    assert ok and m
    sm_pos = out.index(b"\n%s 0 obj\n" % m.group(1))
    assert sm_pos < out.index(b"/SMask") and b"/ColorSpace /DeviceGray" in out[sm_pos : out.index(b"endobj", sm_pos)]


def test_png_cache_is_bounded_and_reused():
    pdfmod._PNG_CACHE.clear()
    first = png(0, 8, 1, 1, b"\x00\x00")
    put(first)
    put(first)
    assert len(pdfmod._PNG_CACHE) == 1
    for v in range(12):
        put(png(0, 8, 1, 1, bytes([0, v + 1])))
    assert len(pdfmod._PNG_CACHE) == 8


def test_large_paeth_image_decodes_quickly():
    w, h = 900, 360
    row = bytes([4]) + bytes((i * 7) & 255 for i in range(w * 4))
    data = png(6, 8, w, h, row * h)
    start = time.perf_counter()
    ok, _ = put(data)
    assert ok and time.perf_counter() - start < 1.5


def test_png_logo_reaches_the_invoice_pdf(boss_client):
    c = boss_client
    data = png(6, 8, 2, 1, b"\x00" + bytes([200, 10, 10, 255, 10, 10, 200, 0]))
    up = c.post("/api/attachments", files={"file": ("logo.png", data, "image/png")})
    assert up.status_code == 200, up.text
    assert c.put("/api/settings", json={"logo": up.json()["name"]}).status_code == 200
    cust = c.post("/api/customers", json={"name": "ACME GmbH", "rate": 100}).json()
    inv = c.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "lines": [{"description": "Dev", "qty": 1, "unit": "h", "price": 100}]},
    ).json()
    pdf = c.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200
    assert b"/SMask" in pdf.content and b"/ColorSpace /DeviceRGB" in pdf.content
