"""Browser tests of the document scan UI (docs/specs/document-scan.md: "API" and "Web UI").

``/api/document-scans*`` is mocked with ``page.route`` against the API contract of the spec, so these tests need no
backend. To run them against the real backend later: drop ``Mock.handle`` for the ``document-scans`` routes (keep the
``attachments`` stub or use real uploads), upload generated invented PDFs instead of fake bytes and read the expected
numbers from the real answers; the selectors below stay the same. Only the shell (login, bootstrap, customers,
invoices, offers) comes from the real test server. Fake demo data only.

Selector contract: ``[data-a=dsPick]`` "Upload old documents" with hidden ``#ds_file``, ``#ds_panel .rcpt-row[data-state]``
(scanning added check duplicate error), ``#ds_flagged`` "To check (n)" with rows ``[data-a=dsCheck]``, in the dialog
``#ds_kind #ds_number #ds_date #ds_cust #ds_vat #ds_lines #ds_totals #ds_diff #ds_ck_keep #ds_err #ds_add #ds_discard``,
preview ``#e_layer .rcpt-box[data-field]``.
"""

import json
import re
from urllib.parse import urlparse

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_expense_lines_ui import PDF, make_png  # noqa: E402
from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)


def scan(sid="d1", filename="old.pdf", flags=None, draft=None, doc=None):
    d = {"kind": "invoice", "number": "RE-2024-007", "date": "2024-03-05", "due_date": "2024-03-19", "valid_until": None, "service_from": None, "service_to": None, "customer": {"name": "Beispiel Kunde GmbH", "street": "Musterweg 1", "zip": "12345", "city": "Musterstadt", "country": "DE", "vat_id": None, "email": None}, "customer_id": None, "customer_match": {"action": "new", "ids": []}, "vat_rate": "19", "lines": [{"description": "Beratung", "quantity": "2", "unit": "h", "price": "50.00"}]}  # fmt: skip
    d.update(draft or {})
    dc = {"pages": 1, "checks": [], "printed_net": "100.00", "printed_vat": "19.00", "printed_total": "119.00", "boxes": [{"field": "number", "page": 0, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.04, "check": "none"}, {"field": "gross", "page": 0, "x": 0.6, "y": 0.8, "w": 0.2, "h": 0.04, "check": "ok"}]}  # fmt: skip
    dc.update(doc or {})
    return {"id": sid, "status": "flagged", "kind": d["kind"], "filename": filename, "engine": "pdf_text", "attachment": "a1.pdf", "flags": flags if flags is not None else [{"code": "not_my_document", "message": "server text"}], "notes": [{"code": "lines_summarised", "message": "Several lines were summarised."}], "draft": d, "doc": dc, "record_coll": None, "record_id": None, "created": 1793620000}  # fmt: skip


def added(number="RE-2024-001", year=2024, rid="inv1"):
    full = scan(sid="x", flags=[], draft={"number": number})
    full.update(status="added", record_coll="invoices", record_id=rid, notes=[{"code": "added_open", "message": "Added as open."}])
    return {"status": "added", "scan": full, "record": {"collection": "invoices", "id": rid, "number": number, "year": year}}  # fmt: skip


def dup(code, existing, message):
    return {"detail": message, "code": code, "existing": existing}


class Mock:
    def __init__(self):
        self.scans = {}
        self.uploads = {}  # filename -> (status, body)
        self.confirm = None
        self.log = []

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path[len("/api/") :]
        m = req.method
        if path.startswith("attachments/") and m == "GET":
            return route.fulfill(body=PDF, content_type="application/pdf")
        if not path.startswith("document-scans"):
            return route.fallback()
        if path == "document-scans" and m == "GET":
            return route.fulfill(json={"items": list(self.scans.values())})
        if path == "document-scans" and m == "POST":
            name = re.search(rb'filename="([^"]*)"', req.post_data_buffer).group(1).decode()
            self.log.append((m, path, name))
            status, resp = self.uploads.get(name, (415, {"detail": "Unsupported file."}))
            if status == 201 and resp["status"] == "flagged":
                self.scans[resp["scan"]["id"]] = resp["scan"]
            return route.fulfill(status=status, json=resp)
        parts = path.split("/")
        sid = parts[1]
        if len(parts) == 4 and parts[2] == "pages":
            return route.fulfill(body=make_png(600, 849), content_type="image/png")
        if len(parts) == 3 and parts[2] == "confirm" and m == "POST":
            data = json.loads(req.post_data)
            self.log.append((m, path, data))
            if self.confirm:
                return route.fulfill(status=self.confirm[0], json=self.confirm[1])
            self.scans.pop(sid, None)
            return route.fulfill(json={"record": {"collection": "invoices", "id": "inv9", "number": data["number"], "year": int(data["date"][:4])}, "scan": {"id": sid, "status": "added"}})  # fmt: skip
        if len(parts) == 2 and m == "DELETE":
            self.log.append((m, path, None))
            self.scans.pop(sid, None)
            return route.fulfill(status=204)
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
    ctxs, problems = [], []

    def open_page(mock):
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctxs.append(ctx)
        ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
        ctx.add_init_script("window.__pwn = 0;")
        ctx.route(re.compile(r"/api/(document-scans|attachments)"), mock.handle)
        pg = ctx.new_page()
        pg.set_default_timeout(5000)
        pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(srv.base + "/#/sales/invoices")
        pg.wait_for_selector("h1:has-text('Offers & Invoices')")
        return pg

    yield open_page
    for c in ctxs:
        for p in c.pages:
            assert p.evaluate("window.__pwn") == 0
    assert problems == []
    for c in ctxs:
        c.close()


def upload(pg, name):
    pg.set_input_files("#ds_file", [{"name": name, "mimeType": "application/pdf", "buffer": PDF}])


def flagged(mock, **kw):
    s = scan(**kw)
    mock.scans[s["id"]] = s
    mock.uploads[s["filename"]] = (201, {"status": "flagged", "scan": s})
    return s


def open_check(pg, sid="d1"):
    row = f'#ds_flagged tr[data-id="{sid}"]'
    pg.wait_for_selector(row)
    # a running batch re-renders the page when it settles: wait until no file is scanning, then click once
    pg.wait_for_function("() => !document.querySelector('#ds_panel .rcpt-row[data-state=scanning]')")
    pg.click(row)
    pg.wait_for_selector("#ds_add")


def test_clean_upload_is_added_with_year_and_link(make_ui):
    mock = Mock()
    mock.uploads["old.pdf"] = (201, added())
    pg = make_ui(mock)
    upload(pg, "old.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="added"]')
    row = pg.locator("#ds_panel .rcpt-row")
    assert "RE-2024-001 added to 2024 as open (not paid)" in row.inner_text()
    assert row.locator("a").get_attribute("href") == "#/doc/invoices/inv1"
    assert mock.calls("POST", "document-scans") == [("POST", "document-scans", "old.pdf")]


def test_flagged_upload_panel_and_confirm_new_customer(make_ui):
    mock = Mock()
    flagged(mock)
    pg = make_ui(mock)
    upload(pg, "old.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="check"]')
    assert "Flagged:" in pg.inner_text("#ds_panel")
    assert "To check (1)" in pg.inner_text("#ds_flagged")
    open_check(pg)
    # fields are filled from the read draft; the flag is a plain sentence, not the server's text
    assert pg.input_value("#ds_number") == "RE-2024-007"
    assert pg.input_value("#ds_date") == "2024-03-05"
    assert pg.input_value("#ds_cust") == "Beispiel Kunde GmbH"
    assert "does not look like a document from you" in pg.inner_text("#ds_flags")
    assert "Several lines were summarised." in pg.inner_text("#ds_notes")
    assert pg.locator("#ds_newcust").is_visible()
    assert pg.input_value("#ds_c_city") == "Musterstadt"
    # oracle: 2 x 50.00 = 100.00 net, 19 % = 19.00, gross 119.00 = printed, so no "keep" box
    assert "119,00" in pg.inner_text("#ds_c_gross")
    assert pg.locator("#ds_keep").is_hidden()
    pg.click("#ds_add")
    pg.wait_for_selector("#ds_add", state="detached")
    ((_, path, body),) = mock.calls("POST", "document-scans/d1/confirm")
    assert body == {"kind": "invoice", "number": "RE-2024-007", "date": "2024-03-05", "due_date": "2024-03-19", "valid_until": None, "service_from": None, "service_to": None, "vat_rate": 19, "lines": [{"description": "Beratung", "quantity": 2, "unit": "h", "price": 50}], "customer": {"name": "Beispiel Kunde GmbH", "street": "Musterweg 1", "zip": "12345", "city": "Musterstadt", "country": "DE"}, "accept_totals_mismatch": False}  # fmt: skip
    pg.wait_for_selector("#ds_flagged", state="detached")


def test_existing_customer_is_linked_by_name(make_ui, srv):
    httpx.post(srv.base + "/api/customers", headers={"Authorization": "Bearer " + srv.token}, json={"name": "Alt Kunde AG"}).raise_for_status()  # fmt: skip
    mock = Mock()
    flagged(mock, draft={"customer": {"name": "alt kunde ag"}})
    pg = make_ui(mock)
    open_check(pg)
    assert pg.locator("#ds_newcust").is_hidden()
    pg.click("#ds_add")
    pg.wait_for_selector("#ds_add", state="detached")
    body = mock.calls("POST", "document-scans/d1/confirm")[0][2]
    assert "customer" not in body and body["customer_id"]


def test_totals_difference_needs_keep_printed(make_ui):
    mock = Mock()
    # printed 150,00 against the lines' 119,00: difference computed - printed = -31,00
    flagged(mock, doc={"printed_net": "126.05", "printed_vat": "23.95", "printed_total": "150.00"})
    pg = make_ui(mock)
    open_check(pg)
    assert pg.locator("#ds_keep").is_visible()
    assert "31,00" in pg.inner_text("#ds_diff")
    pg.click("#ds_add")
    assert "Keep the printed totals" in pg.inner_text("#ds_err")
    assert mock.calls("POST", "document-scans/d1/confirm") == []
    pg.check("#ds_ck_keep")
    pg.click("#ds_add")
    pg.wait_for_selector("#ds_add", state="detached")
    assert mock.calls("POST", "document-scans/d1/confirm")[0][2]["accept_totals_mismatch"] is True


def test_edit_lines_recomputes_totals(make_ui):
    mock = Mock()
    flagged(mock)
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#ds_addline")
    pg.fill('#ds_lines tr[data-line="1"] [data-f=description]', "Reise")
    pg.fill('#ds_lines tr[data-line="1"] [data-f=price]', "10.50")
    # 100.00 + 10.50 = 110.50 net; VAT 19 % = 20.995 -> 21.00 (half-up); gross 131.50
    assert "131,50" in pg.inner_text("#ds_c_gross")
    assert pg.locator("#ds_keep").is_visible()
    pg.click('[data-a=dsRemoveLine][data-i="1"]')
    assert "119,00" in pg.inner_text("#ds_c_gross")
    pg.select_option("#ds_vat", "7")
    # 100.00 net, 7 % = 7.00
    assert "107,00" in pg.inner_text("#ds_c_gross")


def test_duplicate_number_message_with_link(make_ui):
    mock = Mock()
    msg = "A document with the number RE-2024-007 already exists."
    mock.uploads["old.pdf"] = (409, dup("duplicate_number", {"collection": "invoices", "id": "inv5", "number": "RE-2024-007"}, msg))  # fmt: skip
    pg = make_ui(mock)
    upload(pg, "old.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="duplicate"]')
    row = pg.locator("#ds_panel .rcpt-row")
    assert msg in row.inner_text()
    assert row.locator("a").get_attribute("href") == "#/doc/invoices/inv5"


def test_confirm_409_shows_message_and_keeps_dialog(make_ui):
    mock = Mock()
    flagged(mock)
    mock.confirm = (409, dup("duplicate_number", {"collection": "invoices", "id": "inv5", "number": "RE-2024-007"}, "That number exists."))  # fmt: skip
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#ds_add")
    pg.wait_for_function("() => !document.querySelector('#ds_err').hidden")
    assert "That number exists." in pg.inner_text("#ds_err")


def test_discard(make_ui):
    mock = Mock()
    flagged(mock)
    pg = make_ui(mock)
    open_check(pg)
    pg.click("#ds_discard")
    pg.wait_for_selector("#ds_flagged", state="detached")
    assert mock.calls("DELETE", "document-scans/d1")


def test_preview_layer_and_hostile_text(make_ui):
    evil = "<img src=x onerror=window.__pwn=1>"
    mock = Mock()
    flagged(
        mock,
        filename="<svg onload=window.__pwn=1>.pdf",
        flags=[{"code": "future_code", "message": evil + "flag"}],
        draft={"customer": {"name": evil + "Evil"}, "number": "<b>1</b>"},
    )
    mock.scans["d1"]["notes"] = [{"code": "future_note", "message": evil + "note"}]
    pg = make_ui(mock)
    upload(pg, "<svg onload=window.__pwn=1>.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="check"]')
    panel = pg.inner_text("#ds_panel")
    assert "<svg onload=window.__pwn=1>.pdf" in panel and evil + "flag" in panel
    pg.wait_for_selector("#ds_flagged")
    table = pg.inner_text("#ds_flagged")
    assert "<svg onload=window.__pwn=1>.pdf" in table and "<b>1</b>" in table and evil + "Evil" in table
    open_check(pg)
    assert evil + "flag" in pg.inner_text("#ds_flags")
    assert evil + "note" in pg.inner_text("#ds_notes")
    assert pg.input_value("#ds_number") == "<b>1</b>"
    assert pg.input_value("#ds_cust") == evil + "Evil"
    assert pg.locator("#ds_flags img, #ds_notes img, #ds_flagged img, #ds_panel img, #ds_flagged b").count() == 0
    pg.wait_for_selector("#e_layer .rcpt-box[data-field=number]")
    pg.focus("#ds_number")
    pg.wait_for_selector("#e_layer .rcpt-box.hl[data-field=number]")
    pg.wait_for_timeout(300)  # a hostile onerror would fire after the image failed to load
    assert pg.evaluate("window.__pwn") == 0


def test_check_link_appears_without_page_render_while_dialog_open(make_ui):
    mock = Mock()
    flagged(mock)
    mock.scans.clear()
    pg = make_ui(mock)
    # an open dialog suppresses the page re-render after the upload
    pg.evaluate("document.querySelector('#modal-root').innerHTML = '<div id=\"fake_dlg\">x</div>'")
    upload(pg, "old.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="check"] a[data-a=dsCheck]')
    assert "Show it under" in pg.inner_text("#ds_panel")


def test_unflagged_409_has_no_check_link_and_unit_is_limited(make_ui):
    mock = Mock()
    flagged(mock)
    mock.uploads["old.pdf"] = (409, {"detail": "This file was already added.", "code": "duplicate_file", "existing": {"scan_id": "gone"}})  # fmt: skip
    pg = make_ui(mock)
    upload(pg, "old.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="duplicate"]')
    assert pg.locator("#ds_panel .rcpt-row a").count() == 0
    open_check(pg)
    assert pg.get_attribute('#ds_lines [data-f=unit]', "maxlength") == "30"
    assert pg.get_attribute("#ds_c_zip", "maxlength") == "20"
    assert pg.get_attribute("#ds_c_country", "maxlength") == "100"
    assert pg.get_attribute("#ds_c_vat", "maxlength") == "30"


def test_png_refused_before_upload(make_ui):
    mock = Mock()
    pg = make_ui(mock)
    upload(pg, "photo.png")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="error"]')
    assert "not accepted" in pg.inner_text("#ds_panel .rcpt-row")
    assert mock.calls("POST", "document-scans") == []


def test_415_shows_server_message(make_ui):
    mock = Mock()
    mock.uploads["odd.pdf"] = (415, {"detail": "Only PDF and JPEG files are accepted."})
    pg = make_ui(mock)
    upload(pg, "odd.pdf")
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="error"]')
    assert "Only PDF and JPEG files are accepted." in pg.inner_text("#ds_panel .rcpt-row")


def test_confirm_needs_acceptance_requires_keep(make_ui):
    mock = Mock()
    flagged(mock)
    mock.confirm = (
        409,
        {
            "detail": "The lines differ from the printed totals.",
            "code": "needs_acceptance",
            "missing": ["totals_mismatch"],
        },
    )
    pg = make_ui(mock)
    open_check(pg)
    assert not pg.locator("#ds_keep").is_visible()
    pg.click("#ds_add")
    pg.wait_for_function("() => !document.querySelector('#ds_err').hidden")
    assert "differ from the printed totals" in pg.inner_text("#ds_err")
    assert pg.locator("#ds_keep").is_visible()
    assert pg.eval_on_selector("#ds_ck_keep", "e => e.required")
