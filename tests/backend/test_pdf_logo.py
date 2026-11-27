"""Logo embedding: JPEG marker parsing in Pdf.jpeg (hand-built JPEG headers, stdlib only)."""

import struct

import pytest

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
