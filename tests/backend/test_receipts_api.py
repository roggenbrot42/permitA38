"""Receipt scan service and API (docs/specs/receipt-scan.md, WP E). Fake demo data only; the reading is faked by file
content, except where a real PDF goes through the worker. No test needs tesseract."""

import threading
import time
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from permita38.core.receipts import runner
from permita38.core.receipts.extraction import Extraction, ReadResult, VatGroup
from permita38.core.service import receipts as svc_mod

from .conftest import PASSWORD, _login
from .receipt_helpers import RECEIPT_LINES, bill, read, receipt_pdf
from .test_api_tokens import bearer, new_token

TODAY = date(2026, 11, 2)

HOTEL = [
    "Musterhotel Beispielstadt GmbH",
    "Rechnung Nr. RE-2026-0042",
    "Rechnungsdatum 07.10.2026",
    "Uebernachtung 160,00 EUR",
    "Fruehstueck 30,00 EUR",
    "Parkgebuehr 6,00 EUR",
    "A 7,00% 149,53 10,47 160,00",
    "B 19,00% 25,21 4,79 30,00",
    "C 0,00% 6,00 0,00 6,00",
    "Gesamt 196,00 EUR",
]
SINGLE = [
    "Beispiel Handel GmbH",
    "Rechnung Nr. R-100",
    "Rechnungsdatum 12.10.2026",
    "A 19,00% 10,00 1,90 11,90",
    "Gesamt 11,90 EUR",
]
RESTAURANT = [
    "Restaurant Beispielgarten",
    "Beleg-Nr 4711",
    "Datum 12.10.2026",
    "Speisen und Getraenke 100,00",
    "A 19,00% 84,03 15,97 100,00",
    "Gesamt 100,00 EUR",
    "Kartenzahlung 100,00 EUR",
]
# items add up to 56,00 but the bill says 50,00
TANK = [
    "Beispiel Tankstelle",
    "Beleg-Nr 8841",
    "Datum 14.10.2026",
    "Super 56,00 A",
    "A 19,00% 42,02 7,98 50,00",
    "Gesamt 50,00 EUR",
]


def _bill_250(gross: str, net: str, vat: str, number: str | None = None) -> list[str]:
    lines = [
        "Beispiel Werkstatt GmbH",
        "Rechnungsdatum 12.10.2026",
        f"A 19,00% {net} {vat} {gross}",
        f"Gesamt {gross} EUR",
    ]
    if number:
        lines.insert(1, f"Rechnung Nr. {number}")
    return lines


class Reader:
    """Stands in for the worker: the content of a stored file decides what was 'read' from it."""

    def __init__(self):
        self.by_content: dict[bytes, ReadResult] = {}
        self.delay = 0.0
        self.calls = 0

    def add(self, lines: list[str], tag: str = "", engine: str = "pdf_text", result: ReadResult | None = None) -> bytes:
        content = b"%PDF-1.4 fake " + (tag or "|".join(lines)).encode()
        self.by_content[content] = result or read(bill(lines), engine)
        return content

    def __call__(self, path, config):
        self.calls += 1
        with open(path, "rb") as f:
            data = f.read()
        if self.delay:
            time.sleep(self.delay)
        if data in self.by_content:
            return self.by_content[data]
        return real_read_file(path, config)


real_read_file = runner.read_file


@pytest.fixture
def reader(monkeypatch):
    r = Reader()
    monkeypatch.setattr(svc_mod.runner, "read_file", r)
    return r


@pytest.fixture
def services(app):
    s = app.state.services
    s.receipts._today = lambda: TODAY
    s.receipts._ocr_cache = (time.monotonic(), False)
    return s


@pytest.fixture
def c(boss_client, services, reader):
    return boss_client


def up(client, content: bytes, name: str = "bill.pdf"):
    return client.post("/api/receipts", files={"file": (name, content)})


def totals(client):
    return client.get("/api/finance", params={"year": 2026}).json()["totals"]


def expenses(client):
    return client.get("/api/expenses").json()


def scans(services):
    return services.store.list("receipt_scans")


def codes(scan):
    return [f["code"] for f in scan["flags"]]


# ------------------------------------------------------------------ outcomes
def test_hotel_is_booked_with_the_oracle_figures(c, reader):
    r = up(c, reader.add(HOTEL))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["outcome"] == "booked"
    e, scan = body["expense"], body["scan"]
    assert (e["net"], e["vat"]) == (180.74, 15.26)
    assert [(ln["vat_rate"], ln["gross"], ln["net"], ln["vat"]) for ln in e["lines"]] == [
        (7, 160.0, 149.53, 10.47),
        (19, 30.0, 25.21, 4.79),
        (0, 6.0, 6.0, 0.0),
    ]
    assert (e["date"], e["vendor"], e["invoice_no"], e["category"]) == (
        "2026-10-07",
        "Musterhotel Beispielstadt GmbH",
        "RE-2026-0042",
        "Accommodation",
    )
    assert "paid_on" not in e  # unknown payment: assumed to be the bill date
    assert e["external_id"] == "scan:" + scan["id"] and e["attachment"] == scan["attachment"]
    assert (scan["status"], scan["engine"], scan["flags"], scan["expense_id"]) == ("booked", "pdf_text", [], e["id"])
    assert "sha256" not in scan and "invoice_key" not in scan
    assert c.get(f"/api/expenses/{e['id']}").json()["lines"][0]["gross"] == 160.0


def test_single_rate_bill_is_a_classic_expense(c, reader):
    e = up(c, reader.add(SINGLE)).json()["expense"]
    assert (e["net"], e["vat"], e["vat_rate"]) == (10.0, 1.9, 19)
    assert "lines" not in e


def test_restaurant_bill_is_a_business_meal_and_feeds_the_finance_figures(c, reader):
    r = up(c, reader.add(RESTAURANT))
    e = r.json()["expense"]
    assert (e["category"], e["deductible"], e["vat_deductible"]) == ("Business meals", 70, 100)
    assert (e["net"], e["vat"], e["paid_on"]) == (84.03, 15.97, "2026-10-12")
    t = totals(c)
    assert (t["expenses"], t["vat_in"]) == (58.82, 15.97)


def test_flagged_bill_is_not_an_expense_and_changes_no_figure(c, reader):
    before = (totals(c), c.get("/api/finance/project-report").json())
    r = up(c, reader.add(TANK))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["outcome"] == "flagged" and body["expense"] is None
    scan = body["scan"]
    assert (scan["status"], codes(scan)) == ("flagged", ["items_mismatch"])
    assert scan["flags"][0]["message"] == "The items do not add up."
    assert scan["draft"]["gross"] == 50.0 and scan["draft"]["vat_rate"] == 19
    failed = [x for x in scan["doc"]["checks"] if not x["ok"]]
    assert [(x["name"], x["expected"], x["found"]) for x in failed] == [
        ("items_group", "56.00", "50.00"),  # computed from the items vs printed
        ("items_total", "56.00", "50.00"),
    ]
    assert scan["doc"]["pages"] == 1
    assert expenses(c) == []
    assert (totals(c), c.get("/api/finance/project-report").json()) == before
    assert c.get("/api/receipts?status=flagged").json()["items"][0]["id"] == scan["id"]


def test_unreadable_file_is_kept_and_flagged(c, reader):
    content = reader.add([], tag="x", result=ReadResult(engine="none", error="crash"))
    scan = up(c, content).json()["scan"]
    assert codes(scan) == ["unreadable"] and scan["engine"] == "none"
    assert c.get(f"/api/attachments/{scan['attachment']}").status_code == 200
    assert expenses(c) == []


def test_a_total_nothing_confirms_is_flagged_not_verifiable(c, reader):
    lines = [
        "Beispiel Handel GmbH",
        "Rechnung Nr. R-1",
        "Rechnungsdatum 12.10.2026",
        "19 % MwSt enthalten",
        "Gesamt 23,80 EUR",
    ]
    scan = up(c, reader.add(lines)).json()["scan"]
    assert "not_verifiable" in codes(scan) and scan["status"] == "flagged"


def test_invoice_number_is_needed_only_above_250_euro(c, reader):
    ok = up(c, reader.add(_bill_250("250,00", "210,08", "39,92")))
    assert ok.json()["outcome"] == "booked"
    over = up(c, reader.add(_bill_250("250,01", "210,09", "39,92")))
    assert over.json()["outcome"] == "flagged"
    assert codes(over.json()["scan"]) == ["no_invoice_number", "no_supplier_tax_id"]
    with_id = up(c, reader.add([*_bill_250("251,00", "210,92", "40,08"), "USt-IdNr. DE123456789"], tag="vat id"))
    assert codes(with_id.json()["scan"]) == ["no_invoice_number"]


def test_a_draft_that_fails_expense_validation_is_flagged_invalid_data(c, reader):
    x = Extraction(
        vendor="Beispiel Handel GmbH",
        invoice_no="R" * 100,  # the field takes 60 characters
        bill_date="2026-10-12",
        total=D("11.90"),
        vat_groups=[VatGroup(rate=D("19"), net=D("10.00"), vat=D("1.90"), gross=D("11.90"))],
    )
    scan = up(c, reader.add([], tag="long number", result=ReadResult("einvoice", [], x, 1, 1))).json()["scan"]
    assert scan["status"] == "flagged" and codes(scan) == ["invalid_data"]
    assert expenses(c) == []


def test_an_implausible_or_unusual_bill_is_never_booked(c, reader):
    lines = ["Beispiel Handel GmbH", "Rechnung Nr. R-9", "Rechnungsdatum 12.10.2027", *SINGLE[3:]]
    assert codes(up(c, reader.add(lines)).json()["scan"]) == ["date_implausible"]


def test_real_pdf_with_a_full_vat_table_goes_through_pdfium_and_books_clean(c):
    from permita38.core.pdf import Pdf

    pdf = Pdf()
    for text, x, y, size in [
        ("Musterhotel Beispielstadt GmbH", 60, 80, 14),
        ("Musterstrasse 1, 12345 Beispielstadt", 60, 100, 10),
        ("Rechnung Nr. RE-2026-0042", 60, 130, 11),
        ("Rechnungsdatum 07.10.2026", 60, 145, 11),
        ("Uebernachtung 2 x 80,00 EUR", 60, 400, 11),
        ("Fruehstueck 30,00 EUR", 60, 415, 11),
        ("Parkgebuehr 6,00 EUR", 60, 430, 11),
        ("Steuersatz Netto MwSt Brutto", 60, 585, 10),
        ("A 7,00% 149,53 10,47 160,00", 60, 600, 11),
        ("B 19,00% 25,21 4,79 30,00", 60, 615, 11),
        ("C 0,00% 6,00 0,00 6,00", 60, 630, 11),
        ("Gesamt 196,00 EUR", 300, 700, 12),
    ]:
        pdf.text(x, y, text, size=size)
    body = up(c, pdf.output()).json()
    assert body["outcome"] == "booked", body["scan"].get("flags")
    e = body["expense"]
    assert (e["net"], e["vat"]) == (180.74, 15.26)
    assert body["scan"]["engine"] == "pdf_text"
    assert {b["field"] for b in body["scan"]["doc"]["boxes"]} >= {"vendor", "date", "invoice_no", "total", "vat_group"}


def test_real_pdf_with_quantity_and_unit_price_only_is_not_an_items_mismatch(c):
    r = up(c, receipt_pdf(lines=RECEIPT_LINES)).json()
    assert "items_mismatch" not in codes(r["scan"])
    assert r["scan"]["doc"]["items"][0]["amount"] == "160.00"


def test_box_of_an_einvoice_pdf_only_where_the_value_is_found_once(c, reader):
    x = Extraction(
        vendor="Musterhotel GmbH",
        invoice_no="RE-2026-0042",
        bill_date="2026-10-07",
        total=D("160.00"),
        vat_groups=[VatGroup(rate=D("7"), net=D("149.53"), vat=D("10.47"), gross=D("160.00"))],
        payment_signals=["prepaid"],
    )
    words = bill(["Musterhotel GmbH", "Rechnung RE-2026-0042", "Datum 07.10.2026", "Summe 160,00", "Zahlung 160,00"])
    content = reader.add([], tag="e1", result=ReadResult("einvoice", words, x, 1, 1))
    fields = {b["field"]: b for b in up(c, content).json()["scan"]["doc"]["boxes"]}
    assert {"vendor", "invoice_no", "date"} <= set(fields)
    assert "total" not in fields  # 160,00 is printed twice: no box rather than a guess
    inv = next(w for w in words if w.text == "RE-2026-0042")
    assert (fields["invoice_no"]["x"], fields["invoice_no"]["y"]) == (round(inv.x, 4), round(inv.y, 4))


# ------------------------------------------------------------------ duplicates
def test_same_file_twice_is_refused_and_stores_nothing(c, reader, services):
    content = reader.add(HOTEL)
    first = up(c, content).json()
    files = services.store.list_attachments()
    r = up(c, content)
    assert r.status_code == 409
    assert r.json() == {
        "detail": "This receipt is already in the list: Musterhotel Beispielstadt GmbH, 07.10.2026, 196,00 €.",
        "code": "duplicate",
        "match": "file",
        "expense_id": first["expense"]["id"],
        "scan_id": first["scan"]["id"],
    }
    assert services.store.list_attachments() == files and len(scans(services)) == 1 and len(expenses(c)) == 1


def test_same_invoice_number_in_another_file_is_refused_and_the_new_file_removed(c, reader, services):
    first = up(c, reader.add(HOTEL)).json()
    files = services.store.list_attachments()
    other = reader.add(HOTEL, tag="a photo of the same bill")
    r = up(c, other)
    assert r.status_code == 409 and r.json()["match"] == "invoice"
    assert r.json()["expense_id"] == first["expense"]["id"]
    assert services.store.list_attachments() == files and len(expenses(c)) == 1


def test_duplicate_of_a_flagged_bill_has_no_expense_id(c, reader):
    first = up(c, reader.add(TANK)).json()["scan"]
    r = up(c, reader.add(TANK, tag="photo of the tank bill"))
    assert r.status_code == 409
    assert r.json()["expense_id"] is None and r.json()["scan_id"] == first["id"] and r.json()["match"] == "invoice"
    assert "Beispiel Tankstelle, 14.10.2026, 50,00 €" in r.json()["detail"]


def test_a_bill_attached_by_hand_is_detected_as_a_duplicate(c, reader, services):
    content = reader.add(SINGLE)
    name = c.post("/api/attachments", files={"file": ("old.pdf", content)}).json()["name"]
    exp = c.post(
        "/api/expenses", json={"date": "2026-10-12", "net": 10, "vat": 1.9, "attachment": name, "vendor": "X"}
    ).json()
    r = up(c, content)
    assert r.status_code == 409 and r.json()["match"] == "file" and r.json()["expense_id"] == exp["id"]
    assert [s["status"] for s in scans(services)] == ["known"]
    assert up(c, content).status_code == 409 and len(scans(services)) == 1  # hashed once, not again
    # deleting the expense frees the file
    assert c.delete(f"/api/expenses/{exp['id']}").status_code == 204
    assert up(c, content).json()["outcome"] == "booked"


def test_a_receipt_can_be_uploaded_again_after_its_expense_was_deleted(c, reader, services):
    content = reader.add(HOTEL)
    first = up(c, content).json()
    assert c.delete(f"/api/expenses/{first['expense']['id']}").status_code == 204
    assert scans(services) == []
    again = up(c, content)
    assert again.status_code == 201 and again.json()["outcome"] == "booked"
    assert len(expenses(c)) == 1


def test_same_vendor_date_and_amount_without_invoice_numbers_is_a_possible_duplicate(c, reader):
    c.post("/api/expenses", json={"date": "2026-10-12", "net": 10, "vat": 1.9, "vendor": "Beispiel Handel GmbH"})
    scan = up(c, reader.add(SINGLE)).json()["scan"]
    assert codes(scan) == ["possible_duplicate"]


def test_same_vendor_date_and_amount_with_different_invoice_numbers_is_no_duplicate(c, reader):
    c.post("/api/expenses", json={"date": "2026-10-12", "net": 10, "vat": 1.9, "vendor": "Beispiel Handel GmbH",
                                  "invoice_no": "R-99"})  # fmt: skip
    assert up(c, reader.add(SINGLE)).json()["outcome"] == "booked"


def test_two_parallel_uploads_of_one_file_give_one_row_and_one_409(app, boss_client, services, reader):
    content = reader.add(HOTEL)
    reader.delay = 0.3
    clients = [_login(app, "boss"), _login(app, "boss")]
    barrier = threading.Barrier(2)
    out: list[int] = []

    def go(cl):
        barrier.wait()
        out.append(up(cl, content).status_code)

    threads = [threading.Thread(target=go, args=(cl,)) for cl in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(out) == [201, 409]
    assert len(scans(services)) == 1 and len(expenses(boss_client)) == 1
    assert len(services.store.list_attachments()) == 1


def test_a_failed_booking_leaves_no_expense_scan_or_file(c, reader, services, monkeypatch):
    real_save = services.store.save

    def failing(coll, obj, *a, **kw):
        if coll == "receipt_scans":
            raise RuntimeError("disk full")
        return real_save(coll, obj, *a, **kw)

    monkeypatch.setattr(services.store, "save", failing)
    with pytest.raises(RuntimeError):
        up(c, reader.add(HOTEL))
    monkeypatch.undo()
    assert expenses(c) == [] and scans(services) == [] and services.store.list_attachments() == []


# ------------------------------------------------------------------ errors
def test_upload_errors(c, reader, services):
    assert up(c, b"x" * 20, "notes.txt").status_code == 415
    r = up(c, b"\x89PNG\r\n\x1a\n....", "scan.pdf")
    assert r.status_code == 415 and "does not match" in r.json()["detail"]
    assert up(c, b"%PDF-1.4 " + b"0" * (15 * 1024 * 1024), "big.pdf").status_code == 413
    assert up(c, b"<?xml?><a/>", "bill.xml").status_code != 415  # xml is accepted
    assert c.post("/api/receipts").status_code == 422
    assert services.store.list_attachments() == [] or all(a.endswith(".xml") for a in services.store.list_attachments())


def test_scanning_switched_off_is_503(c, reader, services):
    services.config.scan.enabled = False
    assert up(c, reader.add(HOTEL)).status_code == 503
    assert c.get("/api/receipts/capabilities").json()["enabled"] is False
    assert services.store.list_attachments() == []


def test_capabilities(c, services, monkeypatch):
    got = c.get("/api/receipts/capabilities").json()
    assert got == {
        "enabled": True,
        "pdf": True,
        "einvoice": True,
        "ocr": False,
        "ocr_install": "none",
        "vision": "off",
        "accepted": [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xml"],
    }
    services.receipts._ocr_cache = None
    monkeypatch.setattr(svc_mod.ocr, "available", lambda path, languages: "/usr/bin/tesseract")
    assert c.get("/api/receipts/capabilities").json()["ocr"] is True


# ------------------------------------------------------------------ list, get, pages, discard
def test_list_shows_only_flagged_newest_first_and_get_returns_any_status(c, reader, services):
    booked = up(c, reader.add(HOTEL)).json()["scan"]
    old = up(c, reader.add(TANK)).json()["scan"]
    new = up(
        c, reader.add(TANK, tag="second tank bill", result=read(bill(TANK[:1] + ["Beleg-Nr 9"] + TANK[2:])))
    ).json()["scan"]
    services.store.save(
        "receipt_scans", {**services.store.get("receipt_scans", old["id"]), "created": 100}, touch=False
    )
    services.store.save(
        "receipt_scans", {**services.store.get("receipt_scans", new["id"]), "created": 200}, touch=False
    )
    items = c.get("/api/receipts?status=flagged").json()["items"]
    assert [i["id"] for i in items] == [new["id"], old["id"]]
    assert c.get("/api/receipts").json() == c.get("/api/receipts?status=flagged").json()
    assert c.get("/api/receipts?status=booked").status_code == 422
    assert c.get(f"/api/receipts/{booked['id']}").json()["status"] == "booked"
    assert c.get("/api/receipts/nope").status_code == 404 and c.get("/api/receipts/abcdefabcdef").status_code == 404


def test_page_image_of_a_pdf_and_404_otherwise(c, reader):
    scan = up(c, receipt_pdf(pages=2, lines=RECEIPT_LINES)).json()["scan"]
    r = c.get(f"/api/receipts/{scan['id']}/pages/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert c.get(f"/api/receipts/{scan['id']}/pages/2").status_code == 404
    assert c.get(f"/api/receipts/{scan['id']}/pages/-1").status_code == 404
    png = reader.add([], tag="img", result=ReadResult(engine="none", error="x"))
    image = up(c, b"\x89PNG\r\n\x1a\n" + b"fake image", "photo.png").json()["scan"]
    assert c.get(f"/api/receipts/{image['id']}/pages/0").status_code == 404
    assert c.get("/api/receipts/nope/pages/0").status_code == 404
    del png


def test_discard_removes_a_flagged_scan_and_its_file_only(c, reader, services):
    flagged = up(c, reader.add(TANK)).json()["scan"]
    booked = up(c, reader.add(HOTEL)).json()["scan"]
    assert c.delete(f"/api/receipts/{booked['id']}").status_code == 409
    assert c.delete(f"/api/receipts/{flagged['id']}").status_code == 204
    assert c.get(f"/api/receipts/{flagged['id']}").status_code == 404
    assert services.store.attachment_path(flagged["attachment"]) is None
    assert services.store.attachment_path(booked["attachment"]) is not None
    assert c.delete(f"/api/receipts/{flagged['id']}").status_code == 404


# ------------------------------------------------------------------ confirm
TANK_BODY = {"date": "2026-10-14", "vendor": "Beispiel Tankstelle", "net": 42.02, "vat": 7.98, "vat_rate": 19,
             "invoice_no": "8841", "category": "Transportation"}  # fmt: skip


def test_confirm_books_the_corrected_bill_once_and_the_figures_match(c, reader, services):
    scan = up(c, reader.add(TANK)).json()["scan"]
    r = c.post(
        f"/api/receipts/{scan['id']}/confirm",
        json={**TANK_BODY, "attachment": "abcdef123456.pdf", "external_id": "scan:x"},
    )
    assert r.status_code == 200, r.text
    e = r.json()["expense"]
    assert (e["net"], e["vat"]) == (42.02, 7.98)
    assert e["external_id"] == "scan:" + scan["id"] and e["attachment"] == scan["attachment"]
    assert "sanity_warnings" in e
    assert r.json()["scan"]["status"] == "booked" and r.json()["scan"]["expense_id"] == e["id"]
    t = totals(c)
    assert (t["expenses"], t["vat_in"]) == (42.02, 7.98)
    again = c.post(f"/api/receipts/{scan['id']}/confirm", json=TANK_BODY)
    assert again.status_code == 409
    assert len(expenses(c)) == 1 and c.get("/api/receipts").json() == {"items": []}
    assert c.get("/api/receipts/" + scan["id"]).json()["status"] == "booked"


def test_confirm_needs_acceptance_above_250_without_an_invoice_number(c, reader):
    scan = up(c, reader.add(_bill_250("250,01", "210,09", "39,92"))).json()["scan"]
    body = {"date": "2026-10-12", "vendor": "Beispiel Werkstatt GmbH", "net": 210.09, "vat": 39.92, "vat_rate": 19}
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "needs_acceptance" and r.json()["missing"] == ["no_invoice_number"]
    assert expenses(c) == []
    ok = c.post(f"/api/receipts/{scan['id']}/confirm", json={**body, "accepted": ["no_invoice_number"]})
    assert ok.status_code == 200 and len(expenses(c)) == 1


def test_confirm_with_another_gross_needs_total_differs(c, reader):
    scan = up(c, reader.add(TANK)).json()["scan"]
    body = {**TANK_BODY, "net": 47.06, "vat": 8.94}  # 56,00 gross, the bill says 50,00
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=body)
    assert r.status_code == 409 and r.json()["missing"] == ["total_differs"]
    assert expenses(c) == []
    ok = c.post(f"/api/receipts/{scan['id']}/confirm", json={**body, "accepted": ["total_differs"]})
    assert ok.status_code == 200 and ok.json()["expense"]["net"] == 47.06


def test_confirm_with_a_typed_invoice_number_checks_for_duplicates_again(c, reader):
    c.post(
        "/api/expenses",
        json={"date": "2026-10-14", "net": 5, "vat": 0, "vendor": "Beispiel Tankstelle", "invoice_no": "77"},
    )
    scan = up(c, reader.add(TANK)).json()["scan"]
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json={**TANK_BODY, "invoice_no": "77"})
    assert r.status_code == 409 and r.json()["code"] == "duplicate" and r.json()["match"] == "invoice"
    assert len(expenses(c)) == 1
    assert c.post(f"/api/receipts/{scan['id']}/confirm", json={**TANK_BODY, "invoice_no": "78"}).status_code == 200


def test_confirm_errors(c, reader):
    scan = up(c, reader.add(TANK)).json()["scan"]
    assert c.post(f"/api/receipts/{scan['id']}/confirm", json={**TANK_BODY, "net": -1}).status_code == 422
    assert c.post(f"/api/receipts/{scan['id']}/confirm", json={**TANK_BODY, "accepted": ["x"]}).status_code == 422
    assert c.post("/api/receipts/nope/confirm", json=TANK_BODY).status_code == 404
    assert c.post(f"/api/receipts/{scan['id']}/confirm", json={**TANK_BODY, "vat_rate": 13}).status_code == 422
    assert expenses(c) == [] and c.get(f"/api/receipts/{scan['id']}").json()["status"] == "flagged"


def test_a_corrected_category_is_used_for_the_vendors_next_bill(c, reader):
    first = up(c, reader.add(SINGLE)).json()["expense"]
    assert first["category"] == "Other"
    c.put(f"/api/expenses/{first['id']}", json={"date": "2026-10-12", "net": 10, "vat": 1.9, "vat_rate": 19,
                                               "vendor": "Beispiel Handel GmbH", "category": "Office supplies", "invoice_no": "R-100"})  # fmt: skip
    nxt = ["BEISPIEL HANDEL GMBH ", "Rechnung Nr. R-101", *SINGLE[2:]]
    e = up(c, reader.add(nxt)).json()["expense"]
    assert e["category"] == "Office supplies"
    assert e["external_id"].startswith("scan:")


# ------------------------------------------------------------------ rights
@pytest.fixture
def people(app, boss_client):
    out = {}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role, "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role)
    return out


_TINY_JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x0c\x00\x08\x03\x01\x11\x00\x01\x11\x00\x01\x11\x00\x01\xff\xd9"


def _everything(client, content):
    return [
        client.post("/api/receipts", files={"file": ("a.pdf", content)}).status_code,
        client.get("/api/receipts").status_code,
        client.get("/api/receipts/capabilities").status_code,
        client.get("/api/receipts/nope").status_code,
        client.get("/api/receipts/nope/pages/0").status_code,
        client.post("/api/receipts/nope/confirm", json=TANK_BODY).status_code,
        client.delete("/api/receipts/nope").status_code,
        client.post("/api/receipts/nope/parts", files={"file": ("p.jpg", _TINY_JPEG)}).status_code,
    ]


def test_rights_boss_and_accountant_may_use_everything_and_the_employee_nothing(c, reader, people):
    assert _everything(c, reader.add(SINGLE)) == [201, 200, 200, 404, 404, 404, 404, 404]
    assert _everything(people["accountant"], reader.add(RESTAURANT)) == [201, 200, 200, 404, 404, 404, 404, 404]
    assert _everything(people["employee"], reader.add(HOTEL)) == [403] * 8
    assert _everything(c.__class__(c.app), b"%PDF-") == [401] * 8


def test_rights_tokens(app, c, reader):
    ro = bearer(app, new_token(c)["token"])  # read-only by default
    rw = bearer(app, new_token(c, read_only=False)["token"])
    assert ro.get("/api/receipts").status_code == 200 and ro.get("/api/receipts/capabilities").status_code == 200
    assert up(ro, reader.add(HOTEL)).status_code == 403
    assert ro.delete("/api/receipts/nope").status_code == 403
    assert ro.post("/api/receipts/nope/parts", files={"file": ("p.jpg", _TINY_JPEG)}).status_code == 403
    assert ro.post("/api/receipts/nope/confirm", json=TANK_BODY).status_code == 403
    assert up(rw, reader.add(HOTEL)).status_code == 201


# ------------------------------------------------------------------ lessons from the first real receipts
TIP_BILL = [
    "Beispiel Bistro",
    "Musterstr. 8 / 80000 Beispielstadt",
    "Ihre Rechnung Nr: 41234 14. 10. 2026",
    "1 x Testgericht à 11, 20 € 11, 20 € B",
    "1 x Beilage à 3,10 € 3,10 € B",
    "1 x Testgetränk à 5,20 € 5,20 € A",
    "1 x Saft div. à 3,90 € 3,90 € A",
    "Zusatz 0,40 € A",
    "Summe Verkäufe 23,80 €",
    "Total 26,00 €",
    "Nettoumsatz 21,34 €",
    "Ums. inkl. 19% MwSt 9,50 € A",
    "enth. MwSt 19% 1,52 € A",
    "Ums. inkl. 7% MwSt 14,30 € B",
    "enth. MwSt 7% 0,94 € B",
    "Steuersumme 2,46 €",
    "Trinkgelder etc. 2,20 €",
    "EC-KARTE 26, 00 €",
]


def test_bill_with_a_tip_is_booked_with_the_oracle_figures(c, reader):
    r = up(c, reader.add(TIP_BILL))
    assert r.json()["outcome"] == "booked", r.text
    e = r.json()["expense"]
    assert [(ln["gross"], ln["vat_rate"]) for ln in e["lines"]] == [(9.5, 19), (14.3, 7), (2.2, 0)]
    assert (e["net"], e["vat"], e["category"], e["deductible"], e["vat_deductible"]) == (
        23.54,
        2.46,
        "Business meals",
        70,
        100,
    )
    t = totals(c)
    assert (t["expenses"], t["vat_in"]) == (16.48, 2.46)


def _alt_result(first: list[str], *others: list[str]) -> ReadResult:
    res = read(bill(first), "ocr")
    res.alternatives = [bill(o) for o in others]
    return res


def test_the_first_clean_page_mode_is_kept(c, reader, services):
    bad = [ln.replace("Total 26,00", "Total 27,00") for ln in TIP_BILL]
    r = up(c, reader.add([], tag="m1", result=_alt_result(bad, TIP_BILL, bad)))
    assert r.json()["outcome"] == "booked"
    assert r.json()["scan"]["engine"] == "ocr"


def test_without_a_clean_mode_the_one_with_the_fewest_flags_is_kept(c, reader):
    one = [ln.replace("Total 26,00", "Total 27,00") for ln in TIP_BILL]
    many = [ln for ln in one if "Nettoumsatz" not in ln and "Rechnung" not in ln][:-6]
    r = up(c, reader.add([], tag="m2", result=_alt_result(many, one, many)))
    body = r.json()
    assert body["outcome"] == "flagged"
    assert body["scan"]["doc"]["printed_total"] == "27.00"
    assert "no_invoice_number" not in codes(body["scan"])


def test_an_unreadable_mode_never_beats_a_reading(c, reader):
    one = [ln.replace("Total 26,00", "Total 27,00") for ln in TIP_BILL]
    res = _alt_result(one, ["Bildrauschen"])
    r = up(c, reader.add([], tag="m3", result=res))
    assert r.json()["scan"]["doc"]["printed_total"] == "27.00"


# ------------------------------------------------------------------ the same bill read in two ways
FAKE_TSE = "V0;ZZ99YY88XX77WW66;Kassenbeleg-V1;Beleg^0.00_20.00_0.00_0.00_0.00^20.00:Bar;9001;5;2026-10-17T10:07:12.000Z;2026-10-17T10:08:00.000Z;ecdsa;UTC;c2ln;a2V5"  # invented
OTHER_TSE = FAKE_TSE.replace("9001", "9002")
CAFE = [
    "{vendor}",
    "Rechnung Nr: {number}",
    "17.10.2026",
    "Tee 12,00 € B",
    "Kuchen 8,00 € B",
    "Total 20,00 €",
    "Umsatz 7% inkl. 20,00 € B",
    "BAR 20,00 €",
]


def _cafe(vendor="Cafe Beispiel GmbH", number="38", vat_id=None):
    lines = [ln.format(vendor=vendor, number=number) for ln in CAFE]
    return lines + ([f"USt-IdNr. {vat_id}"] if vat_id else [])


def _with_code(lines, tse=FAKE_TSE, engine="pdf_text"):
    from permita38.core.receipts.extraction import Code

    return replace(read(bill(lines), engine), codes=[Code(tse, 0, 0.1, 0.5, 0.2, 0.1)])


def test_the_same_tse_id_is_a_duplicate_whatever_vendor_number_and_vat_id_were_read(c, reader, services):
    first = up(c, reader.add([], tag="scan", result=_with_code(_cafe(vat_id="DE123456789"), engine="ocr")))
    assert first.status_code == 201, first.text
    assert first.json()["scan"]["doc"]["tse_id"] == "zz99yy88xx77ww66-9001-20261017100712000"
    files = services.store.list_attachments()
    photo = reader.add(
        [], tag="photo", result=_with_code(_cafe(vendor="cafe  beispiel gmbh.", number="39"), engine="ocr")
    )
    r = up(c, photo)
    assert r.status_code == 409 and r.json()["match"] == "tse"
    assert r.json()["expense_id"] == first.json()["expense"]["id"]
    assert services.store.list_attachments() == files and len(expenses(c)) == 1


def test_another_tse_id_is_another_bill_even_with_the_same_vendor_date_and_gross(c, reader):
    up(c, reader.add([], tag="one", result=_with_code(_cafe(number="38"))))
    r = up(c, reader.add([], tag="two", result=_with_code(_cafe(number="39"), OTHER_TSE)))
    assert r.status_code == 201 and r.json()["outcome"] == "booked"


def test_a_flagged_bill_with_a_tse_id_blocks_a_second_reading(c, reader):
    first = up(c, reader.add([], tag="one", result=_with_code([*_cafe(), "Gutschrift"])))
    assert first.status_code == 201 and first.json()["outcome"] == "flagged"
    r = up(c, reader.add([], tag="two", result=_with_code(_cafe(number="40"), engine="ocr")))
    assert r.status_code == 409 and r.json()["match"] == "tse" and r.json()["expense_id"] is None
    assert r.json()["scan_id"] == first.json()["scan"]["id"]


def test_the_same_number_counts_whether_the_vat_id_was_read_on_one_side_or_the_other(c, reader):
    first = up(c, reader.add(_bill_250("300,00", "252,10", "47,90", "RE-5") + ["USt-IdNr. DE123456789"]))
    assert first.json()["outcome"] == "booked"
    r = up(c, reader.add(_bill_250("300,00", "252,10", "47,90", "RE-5"), tag="without the VAT id"))
    assert r.status_code == 409 and r.json()["match"] == "invoice"
    # and the other way round, with the vendor spelled differently
    other = [*_bill_250("310,00", "260,50", "49,50", "RE-6"), "Steuernummer 12/345/67890"]
    assert up(c, reader.add(other)).json()["outcome"] == "booked"
    spelled = [ln.replace("Beispiel Werkstatt GmbH", "BEISPIEL  WERKSTATT GmbH.") for ln in other] + [
        "USt-IdNr. DE123456789"
    ]
    assert up(c, reader.add(spelled, tag="with a VAT id")).status_code == 409


def test_same_vendor_date_and_gross_with_ocr_numbers_that_differ_is_a_possible_duplicate(c, reader):
    a = up(c, reader.add([], tag="a", result=read(bill(_cafe(number="R-100")), "ocr")))
    assert a.json()["outcome"] == "booked"
    b = up(c, reader.add([], tag="b", result=read(bill(_cafe(vendor="Cafe Beispiel GmbH.", number="R-108")), "ocr")))
    assert b.status_code == 201 and codes(b.json()["scan"]) == ["possible_duplicate"]


def test_two_real_bills_with_text_layer_numbers_that_differ_stay_two_bills(c, reader):
    assert up(c, reader.add(_cafe(number="R-100"))).json()["outcome"] == "booked"
    assert up(c, reader.add(_cafe(number="R-108"))).json()["outcome"] == "booked"
    assert len(expenses(c)) == 2


def test_two_identical_coffees_without_numbers_are_flagged_not_refused(c, reader):
    lines = [ln for ln in _cafe() if "Rechnung" not in ln]
    assert up(c, reader.add(lines, tag="1")).json()["outcome"] == "booked"
    r = up(c, reader.add(lines, tag="2"))
    assert r.status_code == 201 and codes(r.json()["scan"]) == ["possible_duplicate"]


def test_a_tip_does_not_hide_a_possible_duplicate(c, reader):
    c.post("/api/expenses", json={"date": "2026-10-12", "net": 10, "vat": 1.9, "vendor": "Beispiel Handel GmbH"})
    scan = up(c, reader.add(SINGLE)).json()["scan"]
    assert codes(scan) == ["possible_duplicate"]  # compared on the gross, 11,90


def test_confirm_refuses_a_bill_whose_tse_id_is_booked_meanwhile(c, reader, services):
    flagged = up(c, reader.add(TANK)).json()["scan"]
    assert flagged["status"] == "flagged"
    booked = up(c, reader.add(SINGLE)).json()["scan"]
    for sid in (flagged["id"], booked["id"]):  # the same till receipt, found twice by hand edits of the store
        row = services.store.get("receipt_scans", sid)
        services.store.save("receipt_scans", {**row, "doc": {**row["doc"], "tse_id": "aa11-1"}})
    r = c.post(f"/api/receipts/{flagged['id']}/confirm", json=TANK_BODY)
    assert r.status_code == 409 and r.json()["match"] == "tse"
    assert len(expenses(c)) == 1


# ------------------------------------------------------------------ review round: duplicates by number
def _cafe_variant(date="17.10.2026", total="20,00", vendor="Cafe Beispiel GmbH", number=None):
    lines = [ln.replace("17.10.2026", date).replace("20,00", total) for ln in _cafe(vendor=vendor)]
    lines = [
        ln.replace("Kuchen 8,00", f"Kuchen {_cents(total) - 12:.2f}".replace(".", ",")) if "Kuchen" in ln else ln
        for ln in lines
    ]
    return [ln for ln in lines if "Rechnung" not in ln] if number is None else lines


def _cents(text: str) -> float:
    return float(text.replace(",", "."))


def test_a_bill_that_differs_in_date_gross_or_vendor_is_not_a_possible_duplicate(c, reader):
    first = up(c, reader.add(_cafe_variant(), tag="first"))
    assert first.json()["outcome"] == "booked"
    for tag, lines in (
        ("other date", _cafe_variant(date="18.10.2026")),
        ("other gross", _cafe_variant(total="21,00")),
        ("other vendor", _cafe_variant(vendor="Cafe Anders GmbH")),
    ):
        r = up(c, reader.add(lines, tag=tag))
        assert r.status_code == 201 and r.json()["outcome"] == "booked", (tag, r.text)
    assert len(expenses(c)) == 4


def test_a_waiting_scan_is_the_other_side_of_a_possible_duplicate(c, reader):
    first = up(c, reader.add([*_cafe_variant(), "Gutschrift"], tag="waiting"))
    assert first.json()["outcome"] == "flagged"
    second = up(c, reader.add(_cafe_variant(), tag="again"))
    assert second.status_code == 201 and codes(second.json()["scan"]) == ["possible_duplicate"]


def test_an_expense_with_the_same_vendor_and_number_but_no_scan_row_refuses_on_the_same_day_only(c, reader):
    body = {"date": "2026-10-17", "net": 18.69, "vat": 1.31, "vendor": "Cafe Beispiel GmbH", "invoice_no": "38"}
    assert c.post("/api/expenses", json=body).status_code in (200, 201)
    assert scans_of(c) == []
    r = up(c, reader.add(_cafe(number="38"), tag="same day"))
    assert r.status_code == 409 and r.json()["match"] == "invoice"
    other_day = [ln.replace("17.10.2026", "18.10.2026") for ln in _cafe(number="38")]
    r = up(c, reader.add(other_day, tag="next day"))
    assert r.status_code == 201 and codes(r.json()["scan"]) == ["possible_duplicate"]


def scans_of(c):
    return c.get("/api/receipts?status=flagged").json()["items"]


def test_a_till_number_that_restarts_every_day_never_refuses_the_next_days_bill(c, reader):
    one = up(c, reader.add([], tag="mon", result=read(bill(_cafe(number="5")), "ocr")))
    assert one.json()["outcome"] == "booked"
    two = [ln.replace("17.10.2026", "18.10.2026") for ln in _cafe(number="5")]
    r = up(c, reader.add([], tag="tue", result=read(bill(two), "ocr")))
    assert r.status_code == 201 and codes(r.json()["scan"]) == ["possible_duplicate"]


def _day(lines, day):
    return [ln.replace("17.10.2026", day) for ln in lines]


def test_a_receipt_label_number_with_the_same_vat_id_on_another_day_is_flagged_not_refused(c, reader):
    one = [ln.replace("Rechnung Nr:", "Bon-Nr") for ln in _cafe(number="17", vat_id="DE123456789")]
    assert up(c, reader.add(one, tag="day one")).json()["outcome"] == "booked"
    r = up(c, reader.add(_day(one, "18.10.2026"), tag="day two"))
    assert r.status_code == 201 and codes(r.json()["scan"]) == ["possible_duplicate"]
    # the same receipt number on the same day is still refused
    again = up(c, reader.add(one, tag="day one again"))
    assert again.status_code == 409 and again.json()["match"] == "invoice"


def test_a_real_invoice_number_with_the_same_vat_id_on_another_day_is_still_refused(c, reader):
    one = _cafe(number="RE-17", vat_id="DE123456789")
    assert up(c, reader.add(one, tag="day one")).json()["outcome"] == "booked"
    r = up(c, reader.add(_day(one, "18.10.2026"), tag="day two"))
    assert r.status_code == 409 and r.json()["match"] == "invoice"


def test_a_parallel_upload_of_the_same_bill_in_other_bytes_is_flagged_inside_the_transaction(c, reader, services):
    first = reader.add([], tag="first", result=read(bill(_cafe(number="R-100")), "ocr"))
    second = reader.add([], tag="second", result=read(bill(_cafe(number="R-108")), "ocr"))
    svc = services.receipts
    real = svc._evaluate
    state = {"done": False}

    def evaluate_then_lose_the_race(*args, **kwargs):
        ev = real(*args, **kwargs)  # evaluated before the other upload exists: clean
        if not state["done"]:
            state["done"] = True
            assert not ev.flags
            assert up(c, first, "a.pdf").json()["outcome"] == "booked"  # the parallel upload lands meanwhile
        return ev

    svc._evaluate = evaluate_then_lose_the_race
    try:
        r = up(c, second, "b.pdf")
    finally:
        del svc._evaluate
    assert r.status_code == 201 and r.json()["outcome"] == "flagged"
    assert codes(r.json()["scan"]) == ["possible_duplicate"]
    assert len(expenses(c)) == 1


def test_two_different_tse_ids_are_never_refused_by_the_invoice_number(c, reader):
    up(c, reader.add([], tag="one", result=_with_code(_cafe(number="38"))))
    r = up(c, reader.add([], tag="two", result=_with_code(_cafe(number="38"), OTHER_TSE)))
    assert r.status_code == 201 and r.json()["scan"]["doc"]["tse_id"].startswith("zz99yy88xx77ww66-9002-")


def test_the_start_time_is_part_of_the_tse_id():
    from permita38.core.receipts.qrcodes import parse_tse

    later = FAKE_TSE.replace("2026-10-17T10:07:12", "2026-10-17T11:30:00")
    one, two = parse_tse(FAKE_TSE), parse_tse(later)
    assert one is not None and two is not None and one.tse_id != two.tse_id
    assert one.tse_id == "zz99yy88xx77ww66-9001-20261017100712000"


def test_a_credit_note_is_flagged_and_never_refused_as_a_duplicate_of_its_invoice(c, reader):
    inv = _bill_250("300,00", "252,10", "47,90", "RE-5") + ["USt-IdNr. DE123456789"]
    assert up(c, reader.add(inv, tag="invoice")).json()["outcome"] == "booked"
    r = up(c, reader.add([*inv, "Gutschrift"], tag="credit"))
    assert r.status_code == 201 and "credit_note" in codes(r.json()["scan"])


def test_the_keys_of_a_booked_scan_follow_the_expense(c, reader):
    inv = _bill_250("300,00", "252,10", "47,90", "RE-5") + ["USt-IdNr. DE123456789"]
    first = up(c, reader.add(inv, tag="one")).json()["expense"]
    assert up(c, reader.add(inv, tag="two")).status_code == 409
    # the number is corrected by hand: the old number is free again, the new one is taken
    assert c.put(f"/api/expenses/{first['id']}", json={**first, "invoice_no": "RE-9"}).status_code == 200
    r = up(c, reader.add(inv, tag="three"))
    assert r.status_code == 201 and "invoice" not in str(r.json().get("match"))
    r = up(c, reader.add([ln.replace("RE-5", "RE-9") for ln in inv], tag="four"))
    assert r.status_code == 409 and r.json()["match"] == "invoice"
    # and a deleted expense takes its keys with it
    assert c.delete(f"/api/expenses/{first['id']}").status_code == 204
    r = up(c, reader.add([ln.replace("RE-5", "RE-9") for ln in inv], tag="five"))
    assert r.status_code == 201


# ------------------------------------------------------------------ review round: confirm
def _credit_bill(gross, net, vat, number=None):
    return [*_bill_250(gross, net, vat, number), "Gutschrift"]  # any flag keeps the bill waiting


def _confirm_body(net, vat, **over):
    return {"date": "2026-10-12", "vendor": "Beispiel Werkstatt GmbH", "net": net, "vat": vat, "vat_rate": 19, **over}


def test_confirm_of_exactly_250_euro_without_a_number_needs_no_acceptance(c, reader):
    scan = up(c, reader.add(_credit_bill("250,00", "210,08", "39,92"))).json()["scan"]
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=_confirm_body(210.08, 39.92))
    assert r.status_code == 200, r.text


def test_confirm_with_both_acceptances_missing_names_both_and_one_given_leaves_the_other(c, reader):
    scan = up(c, reader.add(_credit_bill("300,00", "252,10", "47,90"))).json()["scan"]
    body = _confirm_body(260.50, 49.50)  # 310,00 against a printed 300,00, no number
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=body)
    assert r.status_code == 409 and r.json()["missing"] == ["no_invoice_number", "total_differs"]
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json={**body, "accepted": ["total_differs"]})
    assert r.status_code == 409 and r.json()["missing"] == ["no_invoice_number"]
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json={**body, "accepted": ["no_invoice_number", "total_differs"]})
    assert r.status_code == 200 and expenses(c)[0]["net"] + expenses(c)[0]["vat"] == 310.0


def test_confirm_one_cent_off_the_printed_total_raises_total_differs(c, reader):
    scan = up(c, reader.add(_credit_bill("300,00", "252,10", "47,90", "RE-1"))).json()["scan"]
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=_confirm_body(252.11, 47.90, invoice_no="RE-1"))
    assert r.status_code == 409 and r.json()["missing"] == ["total_differs"]
    ok = c.post(f"/api/receipts/{scan['id']}/confirm", json=_confirm_body(252.10, 47.90, invoice_no="RE-1"))
    assert ok.status_code == 200


def test_confirm_with_lines_uses_the_sum_of_the_line_grosses(c, reader):
    scan = up(c, reader.add(TANK)).json()["scan"]
    lines = [
        {"description": "Super", "gross": 30.0, "vat_rate": 19},
        {"description": "Waesche", "gross": 20.0, "vat_rate": 19},
    ]
    body = {
        "date": "2026-10-14",
        "vendor": "Beispiel Tankstelle",
        "invoice_no": "8841",
        "category": "Transportation",
        "lines": lines,
    }
    r = c.post(f"/api/receipts/{scan['id']}/confirm", json=body)
    assert r.status_code == 200, r.text  # 30,00 + 20,00 = the printed 50,00
    scan2 = up(c, reader.add([ln.replace("8841", "8842") for ln in TANK], tag="other")).json()["scan"]
    wrong = {**body, "invoice_no": "8843", "lines": [{**lines[0], "gross": 31.0}, lines[1]]}
    r = c.post(f"/api/receipts/{scan2['id']}/confirm", json=wrong)
    assert r.status_code == 409 and r.json()["missing"] == ["total_differs"]


# ------------------------------------------------------------------ an XML invoice through the API, no fake reader
def test_an_ubl_file_is_read_by_the_real_reader_and_a_broken_sum_is_flagged(c, services):
    from .receipt_helpers import UBL

    clean = UBL.replace(
        '<cbc:PrepaidAmount currencyID="EUR">50.00', '<cbc:PrepaidAmount currencyID="EUR">0.00'
    ).replace('PayableAmount currencyID="EUR">110.00', 'PayableAmount currencyID="EUR">160.00')
    r = up(c, clean.encode(), "bill.xml")
    assert r.status_code == 201 and r.json()["outcome"] == "booked", r.text
    e = r.json()["expense"]
    assert (e["net"], e["vat"], e["invoice_no"], e["date"]) == (149.53, 10.47, "RE-2026-0042", "2026-10-07")
    broken = clean.replace("RE-2026-0042", "RE-2026-0043").replace(
        'TaxInclusiveAmount currencyID="EUR">160.00', 'TaxInclusiveAmount currencyID="EUR">161.00'
    )
    r = up(c, broken.encode(), "broken.xml")
    assert r.status_code == 201 and r.json()["outcome"] == "flagged" and "total_mismatch" in codes(r.json()["scan"])
