"""Browser tests of the receipt scan UI (docs/specs/receipt-scan.md: "Web UI", "Detected fields on the bill").

The receipt routes are mocked with ``page.route`` against the API contract, so these tests need no scanner. Only the
shell (login, bootstrap, the other collections) comes from the real test server; ``/api/receipts*``, ``GET
/api/expenses`` and the stored bills are answered by ``Mock``. Fake demo data only.

Selector contract: ``#rcpt_file`` (hidden multi-file input behind ``[data-a=rcptPick]`` "Upload receipts"),
``#rcpt_drop`` (drop overlay), ``#rcpt_panel .rcpt-row[data-state]`` (progress rows: scanning added check error
duplicate), ``#rcpt_flagged`` ("Needs your check (n)", rows ``[data-a=rcptCheck]``), pills ``.pill`` "scanned" and "paid
date assumed", in the dialog ``#rcpt_flags`` ``#rcpt_checks`` ``#rcpt_err`` ``#rcpt_ck_no_invoice_number``
``#rcpt_ck_total_differs`` ``#rcpt_discard``, preview ``#e_layer`` with ``.rcpt-box[data-field]`` (class ``hl`` when
highlighted), ``#rcpt_toggle`` (aria-pressed), ``#rcpt_pages`` page buttons ``[data-a=rcptPage]``, ``#rcpt_unmarked``.
"""

import json
import re
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_expense_lines_ui import PDF, make_png  # noqa: E402
from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)

HOSTILE = "<img src=x onerror=window.__pwn=1>Evil GmbH"
CAPS = {"enabled": True, "pdf": True, "einvoice": True, "ocr": True, "ocr_install": "none", "vision": "off", "accepted": [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xml"]}  # fmt: skip
PAGE_PNGS = [make_png(300, 400), make_png(500, 700), make_png(900, 1200)]


def expense(eid="e1", vendor="Old Vendor", external_id=None, paid_on="2026-10-07", **kw):
    e = {"id": eid, "date": "2026-10-07", "paid_on": paid_on, "vendor": vendor, "category": "Other", "net": 100.0, "vat": 19.0, "vat_rate": 19, "deductible": 100, "attachment": None, "external_id": external_id}  # fmt: skip
    e.update(kw)
    return e


def booked_hotel():
    exp = expense("e42", "Beispiel Hotel GmbH", "scan:s1", paid_on="2026-10-12", date="2026-10-12", net=180.74, vat=15.26, attachment="a1.pdf", lines=[{"description": "VAT 7 %", "gross": 160.0, "vat_rate": 7, "net": 149.53, "vat": 10.47}, {"description": "VAT 19 %", "gross": 30.0, "vat_rate": 19, "net": 25.21, "vat": 4.79}, {"description": "VAT 0 %", "gross": 6.0, "vat_rate": 0, "net": 6.0, "vat": 0}])  # fmt: skip
    return {"outcome": "booked", "scan": {"id": "s1", "status": "booked", "filename": "hotel.pdf", "engine": "pdf_text", "flags": [], "expense_id": "e42"}, "expense": exp}  # fmt: skip


def box(field, x, y, w=0.2, h=0.05, check="none", page=0, **kw):
    return {"field": field, "page": page, "x": x, "y": y, "w": w, "h": h, "check": check, **kw}


def flagged_scan(sid="s2", filename="IMG_0412.jpg", attachment="a2.jpg", draft=None, doc=None, flags=None):
    d = {"date": "2026-10-14", "paid_on": "2026-10-14", "invoice_no": "8841", "vendor": "Beispiel Tankstelle", "gross": 50.0, "vat_rate": 19, "category": None}  # fmt: skip
    d.update(draft or {})
    dc = {"payment": {"kind": "cash"}, "printed_total": 50.0, "pages": 1, "checks": [{"name": "items_total", "expected": "50.00", "found": "56.00", "ok": False}, {"name": "group_vat", "expected": "7.98", "found": "7.98", "ok": True}], "boxes": [box("vendor", 0.1, 0.05, check="ok"), box("date", 0.6, 0.05), box("invoice_no", 0.1, 0.12), box("total", 0.1, 0.8, check="failed"), box("vat_group", 0.5, 0.7, check="ok")]}  # fmt: skip
    dc.update(doc or {})
    return {"id": sid, "status": "flagged", "filename": filename, "engine": "ocr", "attachment": attachment, "created": 1793620000, "flags": flags if flags is not None else [{"code": "items_mismatch", "message": "The items do not add up."}], "draft": d, "doc": dc}  # fmt: skip


def flagged_body(scan):
    return {"outcome": "flagged", "expense": None, "scan": scan}


def duplicate_body(expense_id="e42", scan_id="s1"):
    return {"detail": "This receipt is already in the list: Beispiel Hotel GmbH, 12.10.2026, 196,00 €.", "code": "duplicate", "match": "file", "expense_id": expense_id, "scan_id": scan_id}  # fmt: skip


class Mock:
    """Answers the receipt routes of the contract and the expense list (so stored bills need no real backend)."""

    def __init__(self):
        self.caps = dict(CAPS)
        self.expenses = []
        self.scans = {}  # flagged scans by id
        self.booked_scans = {}  # GET /api/receipts/{id} of booked bills
        self.uploads = {}  # filename -> (status, body)
        self.confirm = None  # (status, body) override for confirm
        self.log = []  # (method, path, body)
        self.install_status = 200
        self.pages = {}  # scan id -> list of PNG bytes
        self.files = {"a1.pdf": (PDF, "application/pdf"), "a2.jpg": (make_png(1200, 900), "image/png")}

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        path = path[len("/api/") :] if path.startswith("/api/") else path
        m = req.method
        raw = req.post_data_buffer
        body = raw.decode() if raw and path != "receipts" else None
        if path == "expenses" and m == "GET":
            return route.fulfill(json=self.expenses)
        if path.startswith("expenses/") and m == "PUT":
            data = json.loads(body)
            self.log.append((m, path, data))
            for i, e in enumerate(self.expenses):
                if e["id"] == path.split("/")[1]:
                    self.expenses[i] = {**e, **data}
                    return route.fulfill(json=self.expenses[i])
            return route.fulfill(status=404, json={"detail": "no"})
        if path.startswith("attachments/") and m == "GET":
            name = path.split("/")[1]
            if name in self.files:
                data, ctype = self.files[name]
                return route.fulfill(body=data, content_type=ctype)
            return route.fulfill(status=404, json={"detail": "no"})
        if not path.startswith("receipts"):
            return route.fallback()
        if path == "receipts/capabilities":
            return route.fulfill(json=self.caps)
        if path == "receipts" and m == "GET":
            return route.fulfill(json={"items": list(self.scans.values())})
        if path == "receipts" and m == "POST":
            name = re.search(rb'filename="([^"]*)"', raw).group(1).decode()
            self.log.append((m, path, name))
            status, resp = self.uploads.get(name, (415, {"detail": "Unsupported file."}))
            if status == 201 and resp["outcome"] == "booked":
                self.expenses.insert(0, resp["expense"])
            elif status == 201:
                self.scans[resp["scan"]["id"]] = resp["scan"]
            return route.fulfill(status=status, json=resp)
        if path == "receipts/ocr/install" and m == "POST":
            self.log.append((m, path, None))
            if self.install_status != 200:
                return route.fulfill(
                    status=self.install_status, json={"detail": "The OCR package could not be verified."}
                )
            self.caps.update(ocr=True, ocr_install="none")
            return route.fulfill(json={"ocr": True, "version": "5.4.0-1"})
        parts = path.split("/")
        sid = parts[1]
        if len(parts) == 4 and parts[2] == "pages":
            self.log.append((m, path, None))
            pages = self.pages.get(sid, [])
            n = int(parts[3])
            if n < len(pages):
                return route.fulfill(body=pages[n], content_type="image/png")
            return route.fulfill(status=404, json={"detail": "no page"})
        if len(parts) == 3 and parts[2] == "confirm" and m == "POST":
            data = json.loads(body)
            self.log.append((m, path, data))
            if self.confirm:
                return route.fulfill(status=self.confirm[0], json=self.confirm[1])
            scan = self.scans.pop(sid)
            exp = expense("e" + sid, data.get("vendor"), "scan:" + sid, paid_on=data.get("paid_on"), date=data["date"], net=data.get("net"), vat=data.get("vat"))  # fmt: skip
            self.expenses.insert(0, exp)
            return route.fulfill(json={"expense": exp, "scan": {**scan, "status": "booked", "expense_id": exp["id"]}})
        if len(parts) == 2 and m == "DELETE":
            self.log.append((m, path, None))
            self.scans.pop(sid, None)
            return route.fulfill(status=204)
        if len(parts) == 2 and m == "GET":
            self.log.append((m, path, None))
            scan = self.scans.get(sid) or self.booked_scans.get(sid)
            return route.fulfill(json=scan) if scan else route.fulfill(status=404, json={"detail": "no scan"})
        return route.fulfill(status=404, json={"detail": "unmocked " + path})

    def calls(self, method, prefix):
        return [c for c in self.log if c[0] == method and c[1].startswith(prefix)]


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def make_ui(browser, srv):
    """Returns open(mock, bootstrap_patch=None) -> page showing the Expenses page with the mocked routes."""
    ctxs = []
    problems: list[str] = []
    dialogs: list[str] = []

    def open_page(mock, patch=None):
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctxs.append(ctx)
        ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
        ctx.add_init_script("window.__pwn = 0;")
        ctx.route(re.compile(r"/api/(receipts|expenses|attachments)"), mock.handle)
        if patch:

            def boot(route):
                data = route.fetch().json()
                patch(data)
                route.fulfill(json=data)

            ctx.route(re.compile(r"/api/bootstrap"), boot)
        pg = ctx.new_page()
        pg.set_default_timeout(5000)
        pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))

        def on_dialog(d):
            dialogs.append(d.message)
            d.accept()

        pg.on("dialog", on_dialog)
        pg.goto(srv.base + "/#/expenses")
        pg.wait_for_selector("h1:has-text('Expenses')")
        pg.dialogs = dialogs
        return pg

    yield open_page
    for c in ctxs:
        pages = c.pages
        for p in pages:
            assert p.evaluate("window.__pwn") == 0
    assert problems == []
    for c in ctxs:
        c.close()


def upload(pg, name, data=b"%PDF-1.4 fake", mime="application/pdf"):
    pg.set_input_files("#rcpt_file", [{"name": name, "mimeType": mime, "buffer": data}])


def rows(pg):
    return pg.locator("#rcpt_panel .rcpt-row")


def open_check(pg, sid="s2"):
    for _ in range(3):  # the list may be re-rendered under the click while a batch settles
        pg.click(f'#rcpt_flagged tr[data-id="{sid}"]')
        try:
            pg.wait_for_selector("#m-save", timeout=1500)
            return
        except Exception:  # noqa: BLE001
            continue
    pg.wait_for_selector("#m-save")


def with_flagged(mock, **kw):
    s = flagged_scan(**kw)
    mock.scans[s["id"]] = s
    mock.uploads[s["filename"]] = (201, flagged_body(s))
    return s


# ------------------------------------------------------------------ upload and panel
def test_upload_a_pdf_is_booked(make_ui):
    mock = Mock()
    mock.uploads["hotel.pdf"] = (201, booked_hotel())
    pg = make_ui(mock)
    assert pg.locator("[data-a=rcptPick]").is_visible()
    upload(pg, "hotel.pdf", PDF)
    pg.wait_for_selector('.rcpt-row[data-state="added"]')
    assert "Added: Beispiel Hotel GmbH, " in rows(pg).first.inner_text()
    assert "196" in rows(pg).first.inner_text()
    pg.wait_for_selector("tr[data-id=e42]")
    row = pg.locator("tr[data-id=e42]")
    assert "scanned" in row.inner_text()
    assert "paid date assumed" not in row.inner_text()
    assert mock.calls("POST", "receipts") == [("POST", "receipts", "hotel.pdf")]


def test_flagged_bill_edit_and_save(make_ui):
    mock = Mock()
    with_flagged(mock)
    pg = make_ui(mock)
    upload(pg, "IMG_0412.jpg", make_png(10, 10), "image/jpeg")
    pg.wait_for_selector('.rcpt-row[data-state="check"]')
    assert "The items do not add up." in rows(pg).first.inner_text()
    sec = pg.locator("#rcpt_flagged")
    assert "Needs your check (1)" in sec.inner_text()
    assert "not counted" in sec.inner_text()
    assert "Beispiel Tankstelle" in sec.inner_text()
    # the section sits outside the KPI cards
    assert pg.locator(".kpi #rcpt_flagged").count() == 0
    open_check(pg)
    assert pg.inner_text("#modal-root h2") == "Check receipt"
    assert pg.input_value("#f_vendor") == "Beispiel Tankstelle"
    assert pg.input_value("#f_gross") == "50"
    assert pg.input_value("#f_vat_rate") == "19"
    assert pg.input_value("#f_date") == "2026-10-14"
    assert pg.input_value("#f_invoice_no") == "8841"
    assert "The items do not add up." in pg.inner_text("#rcpt_flags")
    pg.fill("#f_vendor", "Tankstelle Beispiel")
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    (_, path, body) = mock.calls("POST", "receipts/s2/confirm")[0]
    assert body["vendor"] == "Tankstelle Beispiel" and body["accepted"] == []
    assert (body["net"], body["vat"], body["vat_rate"]) == (42.02, 7.98, 19)
    assert "attachment" not in body and "external_id" not in body
    pg.wait_for_selector("tr[data-id=es2]")
    assert pg.locator("#rcpt_flagged").count() == 0


def test_duplicate_error_stays_and_links_to_the_bill(make_ui):
    mock = Mock()
    mock.expenses = [booked_hotel()["expense"]]
    mock.uploads["hotel.pdf"] = (409, duplicate_body())
    pg = make_ui(mock)
    upload(pg, "hotel.pdf", PDF)
    pg.wait_for_selector('.rcpt-row[data-state="duplicate"]')
    r = rows(pg).first
    assert "already in the list" in r.inner_text()
    r.locator("a[data-a=editExpense]").click()
    pg.wait_for_selector("#m-save")
    assert pg.input_value("#f_vendor") == "Beispiel Hotel GmbH"
    pg.click("#m-cancel")
    assert rows(pg).count() == 1  # stays until dismissed
    r.locator("[data-a=rcptDismiss]").click()
    assert rows(pg).count() == 0


def test_batch_with_one_duplicate_carries_on(make_ui):
    mock = Mock()
    mock.expenses = [booked_hotel()["expense"]]
    h2 = booked_hotel()
    h2["expense"].update(id="e43", vendor="Zweites Hotel")
    h3 = booked_hotel()
    h3["expense"].update(id="e44", vendor="Drittes Hotel")
    mock.uploads.update({"one.pdf": (201, h2), "two.pdf": (409, duplicate_body()), "three.pdf": (201, h3)})
    pg = make_ui(mock)
    pg.set_input_files("#rcpt_file", [{"name": n, "mimeType": "application/pdf", "buffer": PDF} for n in ("one.pdf", "two.pdf", "three.pdf")])  # fmt: skip
    pg.wait_for_function("() => document.querySelectorAll('#rcpt_panel .rcpt-row[data-state=added]').length === 2")
    assert rows(pg).count() == 3
    assert [c[2] for c in mock.calls("POST", "receipts")] == ["one.pdf", "two.pdf", "three.pdf"]
    assert pg.locator('.rcpt-row[data-state="duplicate"]').count() == 1
    pg.wait_for_selector("tr[data-id=e44]")
    assert pg.locator("tr[data-id=e43]").count() == 1


def test_unaccepted_file_type_is_refused_without_a_request(make_ui):
    mock = Mock()
    pg = make_ui(mock)
    upload(pg, "notes.txt", b"hi", "text/plain")
    pg.wait_for_selector('.rcpt-row[data-state="error"]')
    assert "not accepted" in rows(pg).first.inner_text()
    assert mock.calls("POST", "receipts") == []


def test_drop_target_shows_overlay_and_uploads(make_ui):
    mock = Mock()
    mock.uploads["hotel.pdf"] = (201, booked_hotel())
    pg = make_ui(mock)
    dt = pg.evaluate_handle("() => { const dt = new DataTransfer(); dt.items.add(new File(['%PDF-1.4'], 'hotel.pdf', {type: 'application/pdf'})); return dt; }")  # fmt: skip
    pg.dispatch_event("#main", "dragenter", {"dataTransfer": dt})
    assert pg.locator("#rcpt_drop").is_visible()
    pg.dispatch_event("#main", "drop", {"dataTransfer": dt})
    assert not pg.locator("#rcpt_drop").is_visible()
    pg.wait_for_selector('.rcpt-row[data-state="added"]')


def test_hostile_text_is_escaped_everywhere(make_ui):
    mock = Mock()
    booked = booked_hotel()
    booked["expense"]["vendor"] = HOSTILE
    mock.uploads["hotel.pdf"] = (201, booked)
    s = with_flagged(mock, draft={"vendor": HOSTILE}, flags=[{"code": "x", "message": HOSTILE}])
    s["doc"]["checks"][0]["found"] = HOSTILE
    mock.uploads["IMG_0412.jpg"] = (201, flagged_body(s))
    pg = make_ui(mock)
    upload(pg, "hotel.pdf", PDF)
    pg.wait_for_selector('.rcpt-row[data-state="added"]')
    upload(pg, "IMG_0412.jpg", make_png(10, 10), "image/jpeg")
    pg.wait_for_selector("#rcpt_flagged")
    assert HOSTILE in pg.inner_text("#rcpt_flagged")
    open_check(pg)
    assert pg.input_value("#f_vendor") == HOSTILE
    assert HOSTILE in pg.inner_text("#rcpt_checks") and HOSTILE in pg.inner_text("#rcpt_flags")
    assert pg.locator("img[src=x], #modal-root img[onerror], #main img[onerror]").count() == 0
    assert pg.locator("#rcpt_checks a, #rcpt_flags a").count() == 0


# ------------------------------------------------------------------ acceptance
def scan_300():
    return flagged_scan(draft={"gross": 300.0, "invoice_no": None, "vat_rate": 19}, doc={"printed_total": 310.0})


def test_acceptance_checkboxes_block_save(make_ui):
    mock = Mock()
    s = scan_300()
    mock.scans["s2"] = s
    pg = make_ui(mock)
    open_check(pg)
    inv, differs = pg.locator("#rcpt_ck_no_invoice_number"), pg.locator("#rcpt_ck_total_differs")
    assert inv.is_visible() and differs.is_visible()
    assert "printed total of" in pg.inner_text("#rcpt_acc_total_differs") and "310" in pg.inner_text("#rcpt_acc_total_differs")  # fmt: skip
    pg.click("#m-save")
    pg.wait_for_selector("#rcpt_err:not([hidden])")
    assert mock.calls("POST", "receipts/s2/confirm") == []
    inv.check()
    pg.click("#m-save")
    assert mock.calls("POST", "receipts/s2/confirm") == []  # the second box is still open
    differs.check()
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    body = mock.calls("POST", "receipts/s2/confirm")[0][2]
    assert body["accepted"] == ["no_invoice_number", "total_differs"]


def test_typing_an_invoice_number_and_the_printed_total_hide_the_boxes(make_ui):
    mock = Mock()
    mock.scans["s2"] = scan_300()
    pg = make_ui(mock)
    open_check(pg)
    pg.fill("#f_invoice_no", "R-1")
    assert not pg.locator("#rcpt_ck_no_invoice_number").is_visible()
    pg.fill("#f_gross", "310")
    assert not pg.locator("#rcpt_ck_total_differs").is_visible()
    pg.fill("#f_gross", "249")  # below 250 EUR no number is needed either
    pg.fill("#f_invoice_no", "")
    assert not pg.locator("#rcpt_ck_no_invoice_number").is_visible()
    assert pg.locator("#rcpt_ck_total_differs").is_visible()


def test_server_needs_acceptance_is_shown_in_the_dialog(make_ui):
    mock = Mock()
    mock.scans["s2"] = flagged_scan(draft={"invoice_no": None})  # 50 EUR: the client sees no reason
    mock.confirm = (409, {"detail": "Please confirm that this bill has no invoice number.", "code": "needs_acceptance", "missing": ["no_invoice_number"]})  # fmt: skip
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#m-save")
    pg.wait_for_selector("#rcpt_err:not([hidden])")
    assert "no invoice number" in pg.inner_text("#rcpt_err")
    assert pg.locator("#rcpt_ck_no_invoice_number").is_visible()
    assert pg.locator("#m-save").is_visible()  # the dialog stays open


def test_server_duplicate_is_shown_in_the_dialog(make_ui):
    mock = Mock()
    mock.scans["s2"] = flagged_scan()
    mock.confirm = (409, duplicate_body())
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#m-save")
    pg.wait_for_selector("#rcpt_err:not([hidden])")
    assert "already in the list" in pg.inner_text("#rcpt_err")


def test_discard_receipt_deletes_it(make_ui):
    mock = Mock()
    mock.scans["s2"] = flagged_scan()
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#rcpt_discard")
    pg.wait_for_selector("#m-save", state="detached")
    assert mock.calls("DELETE", "receipts/s2")
    pg.wait_for_selector("#rcpt_flagged", state="detached")


# ------------------------------------------------------------------ pills, rights, notes
def test_paid_date_assumed_pill_goes_away_after_a_date_is_entered(make_ui):
    mock = Mock()
    mock.expenses = [expense("e7", "Scan Shop", "scan:s7", paid_on=None), expense("e8", "Manual Shop")]
    pg = make_ui(mock)
    r7, r8 = pg.locator("tr[data-id=e7]"), pg.locator("tr[data-id=e8]")
    assert "scanned" in r7.inner_text() and "paid date assumed" in r7.inner_text()
    assert "scanned" not in r8.inner_text()
    r7.click()
    pg.wait_for_selector("#f_paid_on")  # GET /receipts/s7 is a 404 here: it behaves like a normal bill
    pg.fill("#f_paid_on", "2026-10-13")
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    assert mock.calls("PUT", "expenses/e7")[0][2]["paid_on"] == "2026-10-13"
    pg.wait_for_function("() => !document.querySelector('tr[data-id=e7]').innerText.includes('paid date assumed')")
    assert "scanned" in pg.locator("tr[data-id=e7]").inner_text()


def test_controls_are_hidden_without_rights_or_capability(make_ui):
    mock = Mock()
    mock.scans["s2"] = flagged_scan()
    pg = make_ui(mock)
    assert pg.locator("[data-a=rcptPick]").count() == 1
    # no write right on attachments
    mock2 = Mock()
    mock2.scans["s2"] = flagged_scan()

    def patch(data):
        data["me"]["permissions"]["attachments"] = {"read": "all", "write": "none"}

    pg2 = make_ui(mock2, patch)
    assert pg2.locator("[data-a=rcptPick], #rcpt_file, #rcpt_flagged, #rcpt_panel").count() == 0
    assert pg2.locator("[data-a=editExpense]").count() >= 1
    # scanning switched off
    mock3 = Mock()
    mock3.caps["enabled"] = False
    pg3 = make_ui(mock3)
    assert pg3.locator("[data-a=rcptPick], #rcpt_file, #rcpt_panel").count() == 0
    # drop does nothing and shows no overlay
    dt = pg3.evaluate_handle(
        "() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'a.pdf')); return dt; }"
    )
    pg3.dispatch_event("#main", "dragenter", {"dataTransfer": dt})
    assert pg3.locator("#rcpt_drop").count() == 0


def test_ocr_missing_note(make_ui):
    mock = Mock()
    mock.caps["ocr"] = False
    pg = make_ui(mock)
    assert "OCR" in pg.inner_text("#rcpt_ocr_note")
    assert pg.locator("button:has-text('Download OCR')").count() == 0
    mock2 = Mock()
    pg2 = make_ui(mock2)
    assert pg2.locator("#rcpt_ocr_note").count() == 0


def test_ocr_download_button_shows_size_and_host_first(make_ui):
    mock = Mock()
    mock.caps.update(ocr=False, ocr_install="available", ocr_download_size=52428800, ocr_download_host="github.com")
    pg = make_ui(mock)
    pg.click("#rcpt_ocr_install")
    pg.wait_for_selector("#rcpt_ocr_note", state="detached")
    assert "50 MB" in pg.dialogs[-1] and "github.com" in pg.dialogs[-1]
    assert mock.calls("POST", "receipts/ocr/install")
    # a failure keeps the button
    failing = Mock()
    failing.caps.update(ocr=False, ocr_install="available", ocr_download_size=1048576, ocr_download_host="github.com")
    failing.install_status = 502
    pg2 = make_ui(failing)
    pg2.click("#rcpt_ocr_install")
    pg2.wait_for_selector("#toast.err")
    assert pg2.locator("#rcpt_ocr_install").is_enabled()


# ------------------------------------------------------------------ overlay
def frac(pg, field):
    """The box of a field as fractions of the image: (x, y, w, h) measured in the browser."""
    return pg.evaluate(
        """f => { const i = document.querySelector('#e_img').getBoundingClientRect(), b = document.querySelector('.rcpt-box[data-field=' + f + ']').getBoundingClientRect();
        return [(b.left - i.left) / i.width, (b.top - i.top) / i.height, b.width / i.width, b.height / i.height]; }""",
        field,
    )


def open_overlay(make_ui, **kw):
    mock = Mock()
    s = with_flagged(mock, **kw)
    pg = make_ui(mock)
    open_check(pg, s["id"])
    pg.wait_for_selector(".rcpt-box")
    pg.wait_for_function("() => document.querySelector('#e_img').naturalWidth > 0")
    return mock, pg


def test_overlay_follows_zoom(make_ui):
    _, pg = open_overlay(make_ui)
    pg.click("[data-a=zoom100]")
    pg.wait_for_function("() => document.querySelector('#e_img').getBoundingClientRect().width === 1200")
    before = frac(pg, "total")
    assert [round(v, 3) for v in before] == [0.1, 0.8, 0.2, 0.05]
    w1 = pg.evaluate("document.querySelector('#e_img').getBoundingClientRect().width")
    pg.click("[data-a=zoomOut]")
    pg.wait_for_function("w => document.querySelector('#e_img').getBoundingClientRect().width < w", arg=w1)
    after = frac(pg, "total")
    assert [round(v, 3) for v in after] == [0.1, 0.8, 0.2, 0.05]
    # the layer is exactly the picture
    assert pg.evaluate("""() => { const i = document.querySelector('#e_img').getBoundingClientRect(), l = document.querySelector('#e_layer').getBoundingClientRect();
        return Math.abs(i.width - l.width) < 1 && Math.abs(i.height - l.height) < 1; }""")  # fmt: skip


def test_toggle_is_on_in_check_receipt_and_remembered(make_ui):
    _, pg = open_overlay(make_ui)
    t = pg.locator("#rcpt_toggle")
    assert t.inner_text() == "Show detected fields"
    assert t.get_attribute("aria-pressed") == "true" and pg.locator("#e_layer").is_visible()
    t.click()
    assert t.get_attribute("aria-pressed") == "false" and not pg.locator("#e_layer").is_visible()
    assert pg.evaluate("localStorage.getItem('permita38.receipts.boxes')") == "0"
    pg.click("#m-cancel")
    open_check(pg)
    pg.wait_for_selector("#rcpt_toggle")
    assert pg.locator("#rcpt_toggle").get_attribute("aria-pressed") == "false"  # remembered
    pg.click("#rcpt_toggle")
    assert pg.locator("#e_layer").is_visible()
    assert pg.evaluate("localStorage.getItem('permita38.receipts.boxes')") == "1"


def test_focusing_a_form_field_highlights_and_scrolls_to_its_box(make_ui):
    _, pg = open_overlay(make_ui)
    pg.set_viewport_size(
        {"width": 1400, "height": 700}
    )  # the landscape pane must be shorter than the 900 px photo to need scrolling
    pg.click("[data-a=zoom100]")
    pg.wait_for_function("() => document.querySelector('#e_img').getBoundingClientRect().width === 1200")
    pg.evaluate("() => { document.querySelector('#e_stage').scrollTop = 0; }")
    pg.focus("#f_gross")
    assert "hl" in pg.get_attribute(".rcpt-box[data-field=total]", "class").split()
    assert "hl" not in pg.get_attribute(".rcpt-box[data-field=vendor]", "class").split()
    assert pg.evaluate("document.querySelector('#e_stage').scrollTop") > 100
    pg.focus("#f_vendor")
    assert "hl" in pg.get_attribute(".rcpt-box[data-field=vendor]", "class").split()
    assert "hl" not in pg.get_attribute(".rcpt-box[data-field=total]", "class").split()


def test_boxes_have_labels_values_and_a_failed_check_in_text(make_ui):
    _, pg = open_overlay(make_ui)
    total, vendor = pg.locator(".rcpt-box[data-field=total]"), pg.locator(".rcpt-box[data-field=vendor]")
    assert "Total" in total.inner_text() and "check failed" in total.inner_text()
    assert "check failed" not in vendor.inner_text()
    assert total.get_attribute("aria-label").startswith("Total – check failed")
    assert not vendor.locator(".rcpt-tip").is_visible()  # the value shows on hover or focus only
    vendor.hover()
    assert "Beispiel Tankstelle" in vendor.locator(".rcpt-tip").inner_text()
    pg.locator(".rcpt-box[data-field=date]").focus()  # reachable by keyboard
    assert "2026-10-14" in pg.locator(".rcpt-box[data-field=date] .rcpt-tip").inner_text()
    # the failed row of the checks table says so in words, and expected is the computed figure
    failed = pg.locator("#rcpt_checks tr.rcpt-failed")
    assert failed.count() == 1 and "Does not match" in failed.inner_text()
    cells = failed.locator("td").all_inner_texts()
    assert cells[1:3] == ["50.00", "56.00"]
    assert pg.get_attribute("#rcpt_checks th:nth-child(2)", "scope") == "col" and "Calculated" in pg.text_content("#rcpt_checks thead")  # fmt: skip


def test_box_label_is_not_html(make_ui):
    s = flagged_scan(draft={"vendor": HOSTILE})
    mock = Mock()
    mock.scans["s2"] = s
    pg = make_ui(mock)
    open_check(pg)
    pg.wait_for_selector(".rcpt-box")
    pg.locator(".rcpt-box[data-field=vendor]").hover()
    assert HOSTILE in pg.locator(".rcpt-box[data-field=vendor] .rcpt-tip").inner_text()
    assert pg.locator("#e_layer img").count() == 0


def test_values_without_a_box_are_listed_under_the_preview(make_ui):
    doc = {"prepaid": 20.0, "amount_due": 30.0, "boxes": [box("vendor", 0.1, 0.05), box("date", 0.6, 0.05)]}
    _, pg = open_overlay(make_ui, doc=doc)
    text = pg.inner_text("#rcpt_unmarked")
    assert "Not marked on the bill" in text
    assert "Prepaid" in text and "Amount due" in text and "Total" in text and "Invoice no.: 8841" in text
    assert "Vendor" not in text and "Date" not in text


def test_scanned_pdf_shows_page_images_with_page_buttons(make_ui):
    mock = Mock()
    doc = {"pages": 3, "boxes": [box("total", 0.1, 0.5, check="ok", page=0), box("vendor", 0.2, 0.1, page=1), box("date", 0.3, 0.2, page=1)]}  # fmt: skip
    with_flagged(mock, filename="scan.pdf", attachment="a1.pdf", doc=doc)
    mock.pages["s2"] = PAGE_PNGS
    pg = make_ui(mock)
    open_check(pg)
    pg.wait_for_selector("#e_img")
    assert pg.locator("#e_pdf").count() == 0  # not the browser's PDF viewer
    pg.wait_for_function("() => document.querySelector('#e_img').naturalWidth === 300")
    assert pg.locator("#rcpt_pages [data-a=rcptPage]").count() == 3
    assert [b.get_attribute("data-field") for b in pg.locator(".rcpt-box").all()] == ["total"]
    pg.click('[data-a=rcptPage][data-n="1"]')
    pg.wait_for_function("() => document.querySelector('#e_img').naturalWidth === 500")
    pg.wait_for_selector(".rcpt-box[data-field=vendor]")
    assert [b.get_attribute("data-field") for b in pg.locator(".rcpt-box").all()] == ["vendor", "date"]
    assert pg.get_attribute('[data-a=rcptPage][data-n="1"]', "aria-pressed") == "true"
    assert [c[1] for c in mock.calls("GET", "receipts/s2/pages")] == ["receipts/s2/pages/0", "receipts/s2/pages/1"]
    assert "original" in pg.inner_text("#e_preview").lower() or pg.locator("#e_preview_open").is_visible()


def test_xml_bill_shows_the_checks_but_no_layer(make_ui):
    mock = Mock()
    mock.files["a3.xml"] = (b"<Invoice/>", "application/xml")
    doc = {"pages": 0, "boxes": []}
    with_flagged(mock, filename="rechnung.xml", attachment="a3.xml", doc=doc)
    pg = make_ui(mock)
    open_check(pg)
    pg.wait_for_selector("#e_preview[data-kind=other]")
    assert pg.locator("#e_layer, #rcpt_toggle, #e_img, #rcpt_unmarked").count() == 0
    assert pg.locator("#rcpt_checks").is_visible()


def test_booked_scanned_bill_loads_the_overlay_off_by_default(make_ui):
    mock = Mock()
    s = flagged_scan()
    s["status"] = "booked"
    mock.booked_scans["s9"] = s
    mock.expenses = [expense("e9", "Beispiel Tankstelle", "scan:s9", attachment="a2.jpg")]
    pg = make_ui(mock)
    pg.click("tr[data-id=e9]")
    pg.wait_for_selector("#rcpt_toggle")
    assert pg.inner_text("#modal-root h2") == "Edit bill"
    assert pg.locator("#rcpt_toggle").get_attribute("aria-pressed") == "false"
    assert not pg.locator("#e_layer").is_visible()
    assert pg.locator("#rcpt_checks").count() == 0  # the check table belongs to the open receipt
    pg.click("#rcpt_toggle")
    assert pg.locator(".rcpt-box[data-field=total]").is_visible()
    assert pg.locator("#rcpt_discard").count() == 0
    assert mock.calls("GET", "receipts/s9")


def test_booked_scanned_bill_without_a_scan_is_a_normal_bill(make_ui):
    mock = Mock()
    mock.expenses = [expense("e9", "Gone Shop", "scan:s404", attachment="a2.jpg")]
    pg = make_ui(mock)
    pg.click("tr[data-id=e9]")
    pg.wait_for_selector("#e_img")
    assert pg.locator("#rcpt_toggle, #e_layer .rcpt-box").count() == 0


@pytest.mark.parametrize(
    "w,h", [(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (1920, 950), (2560, 1440), (3440, 1440), (1536, 738)]
)
def test_check_receipt_dialog_panel_stays_inside_the_dialog(make_ui, w, h):
    mock = Mock()
    with_flagged(
        mock,
        flags=[
            {"code": "items_mismatch", "message": "The items do not add up."},
            {"code": "x", "message": "The total differs from the printed total."},
        ],
    )
    pg = make_ui(mock)
    pg.set_viewport_size({"width": w, "height": h})
    upload(pg, "IMG_0412.jpg", make_png(10, 10), "image/jpeg")
    pg.wait_for_selector('.rcpt-row[data-state="check"]')
    open_check(pg)
    pg.wait_for_selector("#e_preview img")
    assert pg.locator("#rcpt_checks").is_visible() and pg.locator("#rcpt_flags").is_visible()
    dlg = pg.locator("#modal-root .modal").bounding_box()
    pv = pg.locator("#e_preview").bounding_box()
    assert dlg["y"] >= -0.5 and dlg["y"] + dlg["height"] <= h + 0.5
    assert pv["y"] + pv["height"] <= dlg["y"] + dlg["height"] - 22 + 1
    save = pg.locator("#m-save").bounding_box()
    assert 0 <= save["y"] and save["y"] + save["height"] <= dlg["y"] + dlg["height"]
