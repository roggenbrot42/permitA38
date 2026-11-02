"""Photos of one receipt: the generated PDF, overlap, and POST /api/receipts/{id}/parts. The reading is faked."""

import hashlib

import pytest

from permita38.core.receipts import photopdf, textrules
from permita38.core.receipts.extraction import ReadResult, Word
from permita38.core.service import receipts as svc_mod

from .receipt_helpers import bill, read
from .test_receipts_api import SINGLE, TODAY, c, reader, services  # noqa: F401  # noqa: F401  (fixtures)


def fake_jpeg(tag: int, w: int = 8, h: int = 12, comps: int = 3) -> bytes:
    """Enough of a JPEG for the upload check and the PDF writer: SOI, one frame header, a tag, EOI."""
    frame = bytes([8]) + h.to_bytes(2, "big") + w.to_bytes(2, "big") + bytes([comps]) + b"\x01\x11\x00" * comps
    return b"\xff\xd8\xff\xc0" + (len(frame) + 2).to_bytes(2, "big") + frame + bytes([tag]) * 10 + b"\xff\xd9"


class PhotoReader:
    """Photos read alone give a turn and half a bill; the generated PDF gives the whole bill."""

    def __init__(self):
        self.full = read(bill(SINGLE), "ocr")
        self.partial = read(bill(SINGLE[:3]), "ocr")
        self.turn = 90
        self.pdfs = []
        self.on_pdf = None  # called when the generated PDF is read, i.e. while the parts upload is under way

    def __call__(self, path, config):
        data = open(path, "rb").read()
        if data.startswith(b"%PDF"):
            self.pdfs.append(data)
            if self.on_pdf:
                self.on_pdf()
            return self.full
        return ReadResult(engine="ocr", words=self.partial.words, pages_read=1, pages_total=1, rotation=self.turn)


@pytest.fixture
def photos(monkeypatch):
    r = PhotoReader()
    monkeypatch.setattr(svc_mod.runner, "read_file", r)
    return r


def test_pdf_holds_the_jpegs_unchanged_with_their_turn_and_comes_back_out():
    a, b = fake_jpeg(1), fake_jpeg(2, w=10, h=6, comps=1)
    pdf = photopdf.build([(a, 90), (b, 0)])
    assert a in pdf and b in pdf and b"/Rotate 90" in pdf and b"/DCTDecode" in pdf
    assert photopdf.extract(pdf) == [(a, 90), (b, 0)]
    with pytest.raises(photopdf.PhotoError):
        photopdf.extract(b"%PDF-1.4\nsomething else")
    with pytest.raises(photopdf.PhotoError):
        photopdf.build([(a, 45)])
    with pytest.raises(photopdf.PhotoError):
        photopdf.build([(a, 0)] * 6)


def test_pdfium_opens_the_generated_pdf_with_one_page_per_photo():
    from permita38.core.receipts import pdfread

    pdf = photopdf.build([(fake_jpeg(1), 90), (fake_jpeg(2), 0)])
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "x.pdf"
        path.write_bytes(pdf)
        doc = pdfread.open_pdf(str(path))
        try:
            assert len(doc) == 2
        finally:
            doc.close()


def test_lines_repeated_at_the_start_of_the_next_photo_are_dropped():
    first = bill(["Cafe Beispiel GmbH", "Tee 3,00 € A", "Kuchen 4,00 € B", "Total 7,00 €"], page=0)
    second = bill(["Kuchen 4,00 € B", "Total 7,00 €", "EC-KARTE 7,00 €"], page=1)
    kept = textrules.drop_overlap(first + second)
    on_second = [w.text for w in kept if w.page == 1]
    assert on_second == ["EC-KARTE", "7,00", "€"]
    assert len([w for w in kept if w.page == 0]) == len(first)
    unrelated = bill(["Anderes 1,00 €"], page=1)
    assert textrules.drop_overlap(first + unrelated) == first + unrelated


def test_adding_a_photo_books_the_whole_bill_keeps_one_pdf_and_remembers_each_photo(c, photos, services):  # noqa: F811
    one, two = fake_jpeg(1), fake_jpeg(2)
    scan = c.post("/api/receipts", files={"file": ("a.jpg", one)}).json()["scan"]
    assert scan["status"] == "flagged" and scan["doc"]["rotation"] == 90
    reply = c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("b.jpg", two)})
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert body["outcome"] == "booked" and body["expense"]["attachment"].endswith(".pdf")
    assert body["scan"]["doc"]["part_hashes"] == [hashlib.sha256(one).hexdigest(), hashlib.sha256(two).hexdigest()]
    pdf = open(services.store.attachment_path(body["expense"]["attachment"]), "rb").read()
    assert photopdf.extract(pdf) == [(one, 90), (two, 90)]
    assert services.store.attachment_path(scan["attachment"]) is None  # the original photo is gone
    for photo in (one, two):  # each photo alone is a duplicate now
        assert c.post("/api/receipts", files={"file": ("x.jpg", photo)}).status_code == 409


def test_a_booked_or_unknown_scan_a_pdf_and_a_sixth_photo_are_refused(c, photos, services):  # noqa: F811
    scan = c.post("/api/receipts", files={"file": ("a.jpg", fake_jpeg(1))}).json()["scan"]
    photos.partial = photos.full  # the next photo is not needed: the bill is complete after it
    assert c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("b.jpg", fake_jpeg(2))}).status_code == 201
    again = c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("c.jpg", fake_jpeg(3))})
    assert again.status_code == 409 and again.json()["code"] == "not_flagged"
    assert c.post("/api/receipts/doesnotexist/parts", files={"file": ("c.jpg", fake_jpeg(3))}).status_code == 404
    assert c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("c.pdf", b"%PDF-1.4 x")}).status_code in (
        400,
        415,
        422,
    )


def test_five_photos_at_most(c, photos, services):  # noqa: F811
    photos.full = photos.partial  # never complete: stays flagged
    scan = c.post("/api/receipts", files={"file": ("a.jpg", fake_jpeg(1))}).json()["scan"]
    for tag in (2, 3, 4, 5):
        assert c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("p.jpg", fake_jpeg(tag))}).status_code == 201
    sixth = c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("p.jpg", fake_jpeg(6))})
    assert sixth.status_code == 409 and sixth.json()["code"] == "too_many_parts"
    assert Word  # imported for the type of the fake words


def test_a_truncated_jpeg_header_is_a_photo_error_not_a_crash():
    for data in (b"\xff\xd8\xff\xc0\x00\x11", b"\xff\xd8\xff\xc0\x00\x11\x08\x00"):
        with pytest.raises(photopdf.PhotoError):
            photopdf.jpeg_size(data)


def test_a_part_that_arrives_while_the_scan_changed_is_refused_and_leaves_no_file(c, photos, services):  # noqa: F811
    one, two = fake_jpeg(1), fake_jpeg(2)
    photos.full = photos.partial  # stays flagged
    scan = c.post("/api/receipts", files={"file": ("a.jpg", one)}).json()["scan"]
    store = services.store
    before = set(store.list_attachments()) if hasattr(store, "list_attachments") else None

    def other_upload_won():  # a parallel photo upload changed the row between the snapshot and the write
        row = store.get("receipt_scans", scan["id"])
        row["doc"] = {**row["doc"], "part_hashes": [row["sha256"], "f" * 64]}
        store.save("receipt_scans", row)

    photos.on_pdf = other_upload_won
    reply = c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("b.jpg", two)})
    assert reply.status_code == 409 and reply.json()["code"] == "changed"
    assert store.attachment_path(scan["attachment"]) is not None  # the original is untouched
    if before is not None:
        assert set(store.list_attachments()) == before


def test_the_slow_evaluation_runs_outside_the_transaction(c, photos, services):  # noqa: F811
    store, receipts = services.store, services.receipts
    depth = {"now": 0}
    real_tx = store.transaction

    class Counting:
        def __enter__(self):
            self.inner = real_tx()
            self.inner.__enter__()
            depth["now"] += 1

        def __exit__(self, *exc):
            depth["now"] -= 1
            return self.inner.__exit__(*exc)

    store.transaction = Counting
    seen = []
    real_eval = receipts._evaluate

    def watching(*a, **k):
        seen.append(depth["now"])
        return real_eval(*a, **k)

    receipts._evaluate = watching
    try:
        scan = c.post("/api/receipts", files={"file": ("a.jpg", fake_jpeg(1))}).json()["scan"]
        c.post(f"/api/receipts/{scan['id']}/parts", files={"file": ("b.jpg", fake_jpeg(2))})
    finally:
        del store.transaction, receipts._evaluate
    assert seen and set(seen) == {0}
