"""PDF text and positions, embedded e-invoice, page PNG, EXIF orientation, OCR and the worker's read()."""

import json
import shutil
import struct
import zlib
from decimal import Decimal as D

import pytest

from permita38.core.receipts import ocr, pagepng, pdfread, worker
from permita38.core.receipts.extraction import read_result_from_json, to_jsonable
from tests.backend.receipt_helpers import CII, UBL, pdf_with_attachment, receipt_pdf

PAGE_W, PAGE_H = 595.28, 841.89


def _read(tmp_path, data, name="f.bin", **kw):
    path = tmp_path / name
    path.write_bytes(data)
    kw.setdefault("tesseract", None)
    return worker.read(str(path), languages="deu+eng", timeout_s=60, **kw)


def _png_pixels(png: bytes):
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    pos, idat, header = 8, b"", None
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos : pos + 4])
        kind, body = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + n]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == struct.unpack(">I", png[pos + 8 + n : pos + 12 + n])[0]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    return header, zlib.decompress(idat)


# ---- text layer -----------------------------------------------------------------------------------------------


def test_pdf_text_words_sit_where_they_were_drawn(tmp_path):
    res = _read(tmp_path, receipt_pdf())
    assert (res.engine, res.pages_read, res.pages_total, res.error) == ("pdf_text", 1, 1, None)
    by_text = {w.text: w for w in res.words}
    # "Gesamt" was drawn at x=300pt with the baseline 700pt below the top, 12pt high
    g = by_text["Gesamt"]
    assert g.page == 0
    assert g.x == pytest.approx(300 / PAGE_W, abs=0.004)
    assert 690 / PAGE_H < g.y < 700 / PAGE_H and g.y + g.h > 700 / PAGE_H - 0.002
    assert g.w == pytest.approx(0.058, abs=0.012)
    assert by_text["160,00"].x > g.x and by_text["160,00"].y == pytest.approx(g.y, abs=0.004)
    # reading order: top line first
    assert [w.text for w in res.words][:3] == ["Musterhotel", "Beispielstadt", "GmbH"]
    assert all(0 <= w.x <= 1 and 0 <= w.y <= 1 and w.x + w.w <= 1.0001 for w in res.words)


def test_pages_beyond_the_limit_are_counted_as_unread(tmp_path):
    res = _read(tmp_path, receipt_pdf(pages=60))
    assert (res.pages_read, res.pages_total) == (50, 60)


def test_text_cap_drops_whole_pages_and_says_so():
    from permita38.core.receipts.extraction import ReadResult, Word

    words = [Word("x" * 100, p, 0, 0, 0.1, 0.1) for p in range(5) for _ in range(10)]
    res = worker.cap_text(ReadResult("pdf_text", words, None, 5, 5), max_chars=2500)
    assert res.pages_read == 2 and {w.page for w in res.words} == {0, 1}


def test_output_is_kept_under_the_cap():
    from permita38.core.receipts.extraction import ReadResult, Word

    words = [Word("abcde", p, 0.1, 0.2, 0.3, 0.4) for p in range(10) for _ in range(300)]
    text = worker.serialise(ReadResult("pdf_text", words, None, 10, 10), max_bytes=100_000)
    assert len(text) <= 100_000
    back = read_result_from_json(__import__("json").loads(text))
    assert back.pages_read < 10 and back.pages_total == 10 and max(w.page for w in back.words) < back.pages_read


def test_scanned_pdf_without_text_and_without_ocr(tmp_path):
    res = _read(tmp_path, receipt_pdf(lines=[("Hi", 60, 80, 12)]))
    assert (res.engine, res.error, res.pages_total, res.words) == ("none", "ocr_unavailable", 1, [])


# ---- embedded e-invoice ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, xml", [("factur-x.xml", CII), ("zugferd-invoice.xml", UBL)])
def test_embedded_xml_wins_over_text(tmp_path, name, xml):
    res = _read(tmp_path, pdf_with_attachment(name, xml.encode()))
    assert res.engine == "einvoice" and res.extraction is not None and res.pages_total == 1
    assert res.extraction.einvoice_sums["BT-112"] == (D("30.00") if xml is CII else D("160.00"))


def test_embedded_xml_with_doctype_is_ignored(tmp_path):
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Invoice/>'
    res = _read(tmp_path, pdf_with_attachment("factur-x.xml", bad))
    assert res.engine == "none"


def test_xml_file_is_an_einvoice(tmp_path):
    res = _read(tmp_path, UBL.encode())
    assert res.engine == "einvoice" and res.extraction.invoice_no == "RE-2026-0042"
    assert res.words == []


def test_json_round_trip_keeps_the_exact_decimals(tmp_path):
    res = _read(tmp_path, UBL.encode())
    back = read_result_from_json(json.loads(json.dumps(to_jsonable(res))))
    assert back.extraction.einvoice_sums == res.extraction.einvoice_sums


# ---- bad input ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data, error",
    [
        (b"", "empty"),
        (b"%PDF-1.7\nthis is not a pdf at all", "unreadable"),
        (receipt_pdf()[:300], "unreadable"),
        (b"\x00\x01\x02 random bytes", "unreadable"),
        (b'<?xml version="1.0"?><!DOCTYPE a><a/>', "unreadable"),
    ],
    ids=["empty", "corrupt", "truncated", "unknown", "doctype"],
)
def test_unreadable_files(tmp_path, data, error):
    res = _read(tmp_path, data)
    assert (res.engine, res.error, res.words, res.extraction) == ("none", error, [], None)


# ---- page PNG -------------------------------------------------------------------------------------------------


def test_page_png_decodes_and_has_the_right_size(tmp_path):
    path = tmp_path / "r.pdf"
    path.write_bytes(receipt_pdf())
    (w, h, depth, color, _c, _f, interlace), raw = _png_pixels(pagepng.page_png(str(path), 0))
    assert (w, h) == (1241, 1754)  # A4 at 150 dpi: 595.28 x 841.89 pt
    assert (depth, color, interlace) == (8, 2, 0)
    assert len(raw) == h * (1 + 3 * w)
    assert raw[0] == 0  # filter type of the first row
    assert raw[1:4] == b"\xff\xff\xff"  # the page corner is white


def test_page_png_missing_page(tmp_path):
    path = tmp_path / "r.pdf"
    path.write_bytes(receipt_pdf())
    with pytest.raises(IndexError):
        pagepng.page_png(str(path), 1)


def test_encode_png_round_trip():
    pixels = bytes([10, 20, 30, 40, 50, 60])  # 2x1 RGB
    (w, h, *_), raw = _png_pixels(pagepng.encode_png(2, 1, 3, pixels))
    assert (w, h, raw) == (2, 1, b"\x00" + pixels)
    with pytest.raises(ValueError):
        pagepng.encode_png(2, 2, 3, pixels)


# ---- EXIF orientation -----------------------------------------------------------------------------------------


def _jpeg_with_orientation(value: int, endian: str = "<") -> bytes:
    tiff = (b"II*\x00" if endian == "<" else b"MM\x00*") + struct.pack(endian + "I", 8)
    tiff += struct.pack(endian + "H", 1) + struct.pack(endian + "HHIHH", 0x0112, 3, 1, value, 0) + b"\x00\x00\x00\x00"
    seg = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg + b"\xff\xd9"


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("value, degrees", [(1, 0), (3, 180), (6, 90), (8, 270)])
def test_exif_orientation_is_read(value, degrees, endian):
    assert ocr.exif_orientation(_jpeg_with_orientation(value, endian)) == value
    assert ocr.rotation_degrees(value) == degrees


def test_exif_absent_or_broken_means_upright():
    assert ocr.exif_orientation(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20) == 1
    assert ocr.exif_orientation(b"\xff\xd8\xff\xe1\x00\x05Exi") == 1
    assert ocr.exif_orientation(b"not a jpeg") == 1
    assert ocr.exif_orientation(_jpeg_with_orientation(9)) == 1


def test_orientation_6_box_transform():
    # a word at the top left of the stored picture appears at the top right once the picture is turned 90 clockwise
    assert ocr.rotate_box(0.1, 0.1, 0.2, 0.05, 90) == pytest.approx((0.85, 0.1, 0.05, 0.2))
    # a word along the bottom edge of the stored picture ends up on the left edge
    assert ocr.rotate_box(0.2, 0.9, 0.5, 0.05, 90) == pytest.approx((0.05, 0.2, 0.05, 0.5))
    assert ocr.rotate_box(0.1, 0.2, 0.3, 0.4, 180) == pytest.approx((0.6, 0.4, 0.3, 0.4))
    assert ocr.rotate_box(0.1, 0.2, 0.3, 0.4, 270) == pytest.approx((0.2, 0.6, 0.4, 0.3))
    box = (0.1, 0.2, 0.3, 0.1)
    for _ in range(4):  # four quarter turns bring a box home
        box = ocr.rotate_box(*box, 90)
    assert box == pytest.approx((0.1, 0.2, 0.3, 0.1))


def test_tsv_to_words():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t100\t200\t300\t40\t95.5\tGesamt\n"
        "5\t1\t1\t1\t1\t2\t500\t200\t100\t40\t-1\tjunk\n"
        "5\t1\t1\t1\t1\t3\t650\t200\t100\t40\t90\t \n"
    )
    [w] = ocr.tsv_to_words(tsv, 3)
    assert (w.text, w.page) == ("Gesamt", 3)
    assert (w.x, w.y, w.w, w.h) == pytest.approx((0.1, 0.1, 0.3, 0.02))


def test_lookup_prefers_the_configured_path(tmp_path):
    fake = tmp_path / "tesseract"
    fake.write_text("")
    assert ocr.find_tesseract(str(fake)) == str(fake)
    assert ocr.find_tesseract(str(tmp_path / "missing")) is None
    assert ocr.available(str(tmp_path / "missing"), "eng") is None


# ---- OCR ------------------------------------------------------------------------------------------------------


def _exe_or_skip():
    if not shutil.which("tesseract"):
        pytest.skip("tesseract is not installed")
    exe = ocr.available(None, "eng")
    if not exe:
        pytest.skip("tesseract without the eng language")
    return exe


def test_ocr_reads_a_fake_png_with_positions(tmp_path):
    exe = _exe_or_skip()
    path = tmp_path / "r.pdf"
    path.write_bytes(receipt_pdf(lines=[("Cafe Total 12,50", 60, 120, 24), ("Invoice 2026 Thanks", 60, 300, 24)]))
    pdf = pdfread.open_pdf(str(path))
    page = pdf[0]
    w, h, ch, pixels = pdfread.render_raster(page, 300, gray=False)
    res = _read(tmp_path, pagepng.encode_png(w, h, ch, pixels), tesseract=exe)
    assert res.engine == "ocr" and (res.pages_read, res.pages_total) == (1, 1)
    invoice = next(x for x in res.words if x.text == "Invoice")
    assert invoice.x == pytest.approx(60 / PAGE_W, abs=0.02) and invoice.y == pytest.approx(280 / PAGE_H, abs=0.02)
    total = next(x for x in res.words if x.text == "Total")
    assert total.y == pytest.approx(100 / PAGE_H, abs=0.02) and total.x > invoice.x


def test_ocr_of_a_pdf_without_text_layer(tmp_path):
    exe = _exe_or_skip()
    res = _read(tmp_path, receipt_pdf(lines=[("Total 12,50", 60, 120, 24)]), tesseract=exe)
    assert (res.engine, res.pages_read, res.pages_total) == ("ocr", 1, 1)
    total = next(x for x in res.words if x.text == "Total")
    assert total.page == 0 and total.x == pytest.approx(60 / PAGE_W, abs=0.02)
    assert total.y == pytest.approx(100 / PAGE_H, abs=0.02)


def test_word_cap_marks_the_bill_as_partly_read_and_cuts_long_words():
    from permita38.core.receipts import extraction as ex

    words = [ex.Word("a" * 500, 0, 0.1, 0.1, 0.1, 0.01)] + [ex.Word("b", 1, 0.1, 0.1, 0.1, 0.01)] * (ex.MAX_WORDS + 5)
    res = ex.cap_words(ex.ReadResult(engine="pdf_text", words=words, pages_read=2, pages_total=2))
    assert len(res.words) == ex.MAX_WORDS and len(res.words[0].text) == ex.MAX_WORD_CHARS
    assert res.pages_read < res.pages_total  # rules.evaluate then raises pages_skipped


def test_an_einvoice_keeps_pages_read_when_the_words_are_capped():
    from permita38.core.receipts import extraction as ex

    words = [ex.Word("b", 1, 0.1, 0.1, 0.1, 0.01)] * (ex.MAX_WORDS + 5)
    res = ex.ReadResult(
        engine="einvoice", words=words, extraction=ex.Extraction(vendor="X"), pages_read=2, pages_total=2
    )
    assert ex.cap_words(res).pages_read == 2  # its values do not come from the words
