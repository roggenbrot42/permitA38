"""Turning photos: the choice of angle (without Tesseract) and the data it leaves behind."""

import pytest

from permita38.core.receipts import pagepng, pdfread, worker
from permita38.core.receipts.extraction import ReadResult, Word, read_result_from_json, to_jsonable


def words(*texts):
    return [Word(t, 0, 0.1, 0.1 * i, 0.1, 0.02) for i, t in enumerate(texts)]


def test_a_total_label_or_five_amounts_make_a_reading_plausible():
    assert worker._plausible(words("Total", "26,00"))
    assert worker._plausible(words("a", "1,00", "2,00", "3,00", "4,00", "5,00"))
    assert not worker._plausible(words("xq", "j", "1,00", "2,00"))  # noise at the wrong angle


def test_rotation_survives_the_json_round_trip_and_bad_values_are_zero():
    r = ReadResult(engine="ocr", words=words("Total"), rotation=270)
    assert read_result_from_json(to_jsonable(r)).rotation == 270
    assert read_result_from_json({**to_jsonable(r), "rotation": 45}).rotation == 0
    assert read_result_from_json({"engine": "ocr"}).rotation == 0


def test_photo_page_png_refuses_bad_angles_and_non_photos(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(IndexError):
        pagepng.page_png(str(pdf), 0, 45)
    with pytest.raises(IndexError):
        pagepng.page_png(str(pdf), 1, 90)
    with pytest.raises(pdfread.PdfUnreadable):
        pagepng.page_png(str(pdf), 0, 90)  # a rotation is only for a JPEG


# ------------------------------------------------------------------ the choice of angle, without Tesseract
def _fake_photo(monkeypatch, size, reads):
    """``reads``: degrees -> words the (fake) OCR finds at that angle. Returns the list of angles that were read."""
    from permita38.core.receipts import ocr, qrcodes

    seen: list[int] = []

    def render(data, degrees):
        w, h = size if degrees in (0, 180) else size[::-1]
        return w, h, 1, bytes([degrees // 90])  # the "pixels" tell the fake OCR which angle this is

    def ocr_raster(exe, languages, w, h, ch, pixels, page, left, dpi=300, psm=6):
        degrees = pixels[0] * 90
        seen.append(degrees)
        return reads.get(degrees, words("xq"))

    monkeypatch.setattr(pdfread, "render_jpeg", render)
    monkeypatch.setattr(ocr, "ocr_raster", ocr_raster)
    monkeypatch.setattr(ocr, "exif_orientation", lambda data: 1)
    monkeypatch.setattr(qrcodes, "find_codes", lambda w, h, pixels: [])
    return seen


RECEIPT_WORDS = words("Total", "26,00")


def test_a_landscape_photo_is_tried_at_90_then_270_and_the_first_plausible_angle_wins(monkeypatch):
    seen = _fake_photo(monkeypatch, (400, 300), {270: RECEIPT_WORDS})
    r = worker._read_jpeg(b"jpeg", "tesseract", "deu", lambda: 100)
    assert r.rotation == 270 and r.engine == "ocr" and seen[:2] == [90, 270]


def test_a_landscape_photo_that_reads_at_90_stops_there(monkeypatch):
    seen = _fake_photo(monkeypatch, (400, 300), {90: RECEIPT_WORDS})
    r = worker._read_jpeg(b"jpeg", "tesseract", "deu", lambda: 100)
    assert r.rotation == 90 and 270 not in seen[:2]


def test_a_portrait_photo_is_tried_at_0_then_180(monkeypatch):
    seen = _fake_photo(monkeypatch, (300, 400), {180: RECEIPT_WORDS})
    r = worker._read_jpeg(b"jpeg", "tesseract", "deu", lambda: 100)
    assert r.rotation == 180 and seen[:2] == [0, 180]
    seen = _fake_photo(monkeypatch, (300, 400), {0: RECEIPT_WORDS})
    assert worker._read_jpeg(b"jpeg", "tesseract", "deu", lambda: 100).rotation == 0
    assert set(seen) == {0}  # the first angle was plausible: no other angle is tried


def test_with_no_plausible_angle_the_one_with_most_amounts_wins(monkeypatch):
    few, more = words("xq", "1,00"), words("xq", "1,00", "2,00", "3,00")
    _fake_photo(monkeypatch, (300, 400), {0: few, 180: more})
    assert worker._read_jpeg(b"jpeg", "tesseract", "deu", lambda: 100).rotation == 180
