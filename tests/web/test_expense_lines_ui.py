"""Browser tests of the itemised expense dialog and the bill preview (docs/specs/itemised-expenses.md).

Selector contract the implementation must provide (all inside the "Add bill" / "Edit bill" dialog, ``#f_*`` are the
ids ``openForm`` already creates):

* ``#f_itemise``          checkbox "Itemise this bill"; ``#e_itemise_hint`` is a visible hint while it cannot be
                          switched off (more than one line); then ``#f_itemise`` has the ``disabled`` property.
* ``#f_gross`` / ``#f_vat_rate``  the simple form; hidden (not visible) while itemising.
* ``#e_lines``            the lines table, one ``tr[data-line]`` per line, containing
                          ``[data-f=description]``, ``[data-f=gross]``, ``[data-f=vat_rate]`` (a select with 19/7/0),
                          ``[data-f=deductible]`` and a ``[data-a=removeLine]`` button; ``[data-a=addLine]`` adds a line.
* totals                  ``#e_tot_gross``, ``#e_tot_net``, ``#e_tot_vat`` and one ``[data-tot-vat="<rate>"]`` per
                          rate present in the lines (text = formatted euro amount, any locale format).
* the expense list shows "<n> items" under the description of an itemised bill.
* preview pane            ``#e_preview`` (focusable, ``tabindex=0``) with ``data-kind`` = ``image`` | ``pdf`` |
                          ``other`` and, for images, ``data-zoom`` = zoom in percent (``"100"`` = natural size,
                          range 25..800); an ``<img>`` (alt = file name, ``src`` a ``blob:`` URL before saving),
                          for PDFs an ``<iframe>``; buttons ``[data-a=zoomIn]``, ``[data-a=zoomOut]``,
                          ``[data-a=zoomFit]``, ``[data-a=zoom100]``; link ``#e_preview_open`` ("Open in a new tab",
                          always present while a file is shown) and ``#e_preview_fallback`` (text node for browsers
                          without an inline PDF viewer; must be in the DOM for pdf, visibility is not asserted).
* Zoom steps: "+"/"-" multiply/divide by 1.25 and clamp to 25..800; wheel up (deltaY < 0) zooms in, wheel down out;
  keys ``+`` ``-`` zoom, ``0`` = Fit, arrow keys pan. "Fit" scales the whole image into the pane (a 1200x900 image
  gives a zoom below 100).
"""

import re
import struct
import zlib
from decimal import Decimal

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)

D = Decimal


def make_png(w: int, h: int) -> bytes:
    """A w x h RGB PNG with a vertical gradient (no Pillow needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes([40 + (y * 200) // h, 100, 180]) * w for y in range(h))  # one colour per row
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")  # fmt: skip


def make_pdf() -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


PNG = make_png(1200, 900)
PDF = make_pdf()


def amount(text: str) -> Decimal:
    """'196,00 €' / '€196.00' / '1.196,00' -> Decimal (the UI is de-DE, so the last separator is the decimal one)."""
    t = re.sub(r"[^\d,.\-]", "", text)
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return D(t)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def ui(browser, srv):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    # spy on the blob URL lifecycle
    ctx.add_init_script(
        "window.__revoked = []; const _r = URL.revokeObjectURL.bind(URL);"
        "URL.revokeObjectURL = u => { window.__revoked.push(u); return _r(u); };"
        "window.__pwn = 0;"
    )
    pg = ctx.new_page()
    pg.set_default_timeout(5000)
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector("[data-a=editExpense]")
    yield pg
    assert problems == []
    assert pg.evaluate("window.__pwn") == 0
    ctx.close()


def api(srv, path):
    return httpx.get(srv.base + "/api/" + path, headers={"Authorization": "Bearer " + srv.token}).json()


def open_new(pg):
    pg.click("button[data-a=editExpense]")
    pg.wait_for_selector("#f_itemise")


def fill_line(pg, n: int, desc: str, gross: str, rate: str, ded: str | None = None):
    row = pg.locator("#e_lines tr[data-line]").nth(n)
    row.locator("[data-f=description]").fill(desc)
    row.locator("[data-f=gross]").fill(gross)
    row.locator("[data-f=vat_rate]").select_option(rate)
    if ded is not None:
        row.locator("[data-f=deductible]").fill(ded)


def itemise_hotel(pg):
    open_new(pg)
    pg.fill("#f_vendor", "Hotel Beispiel")
    pg.check("#f_itemise")
    pg.wait_for_selector("#e_lines tr[data-line]")
    fill_line(pg, 0, "Room 2 nights", "160", "7")
    for n, (d, g, r) in enumerate([("Breakfast", "30", "19"), ("City tax", "6", "0")], 1):
        pg.click("[data-a=addLine]")
        pg.wait_for_function("n => document.querySelectorAll('#e_lines tr[data-line]').length === n", arg=n + 1)
        fill_line(pg, n, d, g, r)


# ------------------------------------------------------------------ itemising
def test_checkbox_turns_the_single_amount_into_line_one(ui):
    open_new(ui)
    ui.fill("#f_gross", "119")
    ui.select_option("#f_vat_rate", "19")
    assert ui.locator("#e_lines").count() == 0 or not ui.locator("#e_lines").is_visible()
    ui.check("#f_itemise")
    ui.wait_for_selector("#e_lines tr[data-line]")
    assert ui.locator("#e_lines tr[data-line]").count() == 1
    row = ui.locator("#e_lines tr[data-line]").first
    assert amount(row.locator("[data-f=gross]").input_value()) == D("119")
    assert row.locator("[data-f=vat_rate]").input_value() == "19"
    assert not ui.locator("#f_gross").is_visible()
    assert amount(ui.inner_text("#e_tot_gross")) == D("119")
    assert amount(ui.inner_text("#e_tot_net")) == D("100")
    assert amount(ui.inner_text("#e_tot_vat")) == D("19")


def test_live_totals_match_the_worked_example(ui):
    itemise_hotel(ui)
    assert amount(ui.inner_text("#e_tot_gross")) == D("196.00")
    assert amount(ui.inner_text("#e_tot_net")) == D("180.74")
    assert amount(ui.inner_text("#e_tot_vat")) == D("15.26")
    assert amount(ui.inner_text('[data-tot-vat="7"]')) == D("10.47")
    assert amount(ui.inner_text('[data-tot-vat="19"]')) == D("4.79")
    assert amount(ui.inner_text('[data-tot-vat="0"]')) == D("0")
    # changing a line updates the totals at once
    fill_line(ui, 1, "Breakfast", "0", "19")
    assert amount(ui.inner_text("#e_tot_gross")) == D("166.00")
    assert amount(ui.inner_text('[data-tot-vat="19"]')) == D("0")


def test_group_rounding_in_the_display(ui):
    open_new(ui)
    ui.check("#f_itemise")
    ui.wait_for_selector("#e_lines tr[data-line]")
    fill_line(ui, 0, "a", "0.50", "7")
    ui.click("[data-a=addLine]")
    fill_line(ui, 1, "b", "0.50", "7")
    assert amount(ui.inner_text('[data-tot-vat="7"]')) == D("0.07")  # not 0,06


def test_remove_line_and_switch_off_only_with_one_line_left(ui):
    itemise_hotel(ui)
    assert ui.locator("#f_itemise").is_disabled()
    assert ui.locator("#e_itemise_hint").is_visible()
    ui.locator("#e_lines tr[data-line]").nth(2).locator("[data-a=removeLine]").click()
    ui.locator("#e_lines tr[data-line]").nth(1).locator("[data-a=removeLine]").click()
    assert ui.locator("#e_lines tr[data-line]").count() == 1
    assert amount(ui.inner_text("#e_tot_gross")) == D("160")
    assert ui.locator("#f_itemise").is_enabled()
    ui.uncheck("#f_itemise")
    ui.wait_for_selector("#f_gross", state="visible")
    assert amount(ui.input_value("#f_gross")) == D("160") and ui.input_value("#f_vat_rate") == "7"


def test_save_hotel_bill_then_reopen_and_edit(srv, ui):
    itemise_hotel(ui)
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    (e,) = api(srv, "expenses")
    assert [(x["description"], x["gross"], x["vat_rate"]) for x in e["lines"]] == [
        ("Room 2 nights", 160, 7),
        ("Breakfast", 30, 19),
        ("City tax", 6, 0),
    ]
    assert (e["net"], e["vat"], e["vat_rate"]) == (180.74, 15.26, None)
    # the list says "3 items"
    ui.wait_for_selector("tr[data-a=editExpense]")
    assert "3 items" in ui.inner_text("tr[data-a=editExpense]")
    # reopen: checkbox on, three lines, same totals
    ui.click("tr[data-a=editExpense]")
    ui.wait_for_selector("#e_lines tr[data-line]")
    assert ui.is_checked("#f_itemise") and ui.locator("#e_lines tr[data-line]").count() == 3
    assert ui.locator("#e_lines tr[data-line]").nth(1).locator("[data-f=description]").input_value() == "Breakfast"
    assert amount(ui.inner_text("#e_tot_vat")) == D("15.26")
    # edit a line and save
    fill_line(ui, 1, "Breakfast", "40", "19")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    (e,) = api(srv, "expenses")
    assert e["lines"][1]["gross"] == 40 and e["net"] + e["vat"] == pytest.approx(206.0)


def test_existing_single_amount_bill_opens_as_before(srv, ui):
    httpx.post(
        srv.base + "/api/expenses",
        headers={"Authorization": "Bearer " + srv.token},
        json={"date": "2026-03-10", "net": 100, "vat": 19, "vat_rate": 19, "vendor": "Old"},
    )
    ui.reload()
    ui.wait_for_selector("tr[data-a=editExpense]")
    assert "items" not in ui.inner_text("tr[data-a=editExpense]")
    ui.click("tr[data-a=editExpense]")
    ui.wait_for_selector("#f_itemise")
    assert not ui.is_checked("#f_itemise")
    assert amount(ui.input_value("#f_gross")) == D("119")


def test_hostile_line_description_is_escaped(srv, ui):
    hostile = '<img src=x onerror="window.__pwn=1">"\'&'
    open_new(ui)
    ui.fill("#f_vendor", "x")
    ui.check("#f_itemise")
    ui.wait_for_selector("#e_lines tr[data-line]")
    fill_line(ui, 0, hostile, "10", "19")
    ui.click("[data-a=addLine]")
    fill_line(ui, 1, "ok", "10", "19")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    ui.click("tr[data-a=editExpense]")
    ui.wait_for_selector("#e_lines tr[data-line]")
    assert ui.locator("#e_lines tr[data-line]").first.locator("[data-f=description]").input_value() == hostile
    assert ui.locator("#e_lines img").count() == 0 and ui.locator("#e_lines tr[data-line]").count() == 2
    assert api(srv, "expenses")[0]["lines"][0]["description"] == hostile


# ------------------------------------------------------------------ bill preview
def choose(pg, name: str, blob: bytes, mime: str):
    pg.set_input_files("#e_file", files=[{"name": name, "mimeType": mime, "buffer": blob}])


def zoom(pg) -> float:
    return float(pg.get_attribute("#e_preview", "data-zoom"))


def img_width(pg) -> float:
    return pg.locator("#e_preview img").bounding_box()["width"]


def test_png_preview_is_shown_before_saving_and_zoom_buttons_work(ui):
    open_new(ui)
    choose(ui, "bill.png", PNG, "image/png")
    ui.wait_for_selector("#e_preview img")
    assert ui.get_attribute("#e_preview", "data-kind") == "image"
    assert ui.get_attribute("#e_preview img", "src").startswith("blob:")
    assert ui.get_attribute("#e_preview img", "alt") == "bill.png"
    ui.click("#e_preview [data-a=zoom100]")
    assert zoom(ui) == 100
    assert img_width(ui) == pytest.approx(1200, abs=2)  # natural size
    ui.click("#e_preview [data-a=zoomIn]")
    assert zoom(ui) == pytest.approx(125) and img_width(ui) == pytest.approx(1500, abs=3)
    ui.click("#e_preview [data-a=zoom100]")
    ui.click("#e_preview [data-a=zoomOut]")
    assert zoom(ui) == pytest.approx(80) and img_width(ui) == pytest.approx(960, abs=3)
    ui.click("#e_preview [data-a=zoomFit]")
    assert zoom(ui) < 100  # the 1200 px wide image is scaled down to the pane
    assert img_width(ui) <= ui.locator("#e_preview").bounding_box()["width"] + 2
    assert ui.get_attribute("#e_preview_open", "href").startswith("blob:")


def test_zoom_is_clamped_to_25_and_800_percent(ui):
    open_new(ui)
    choose(ui, "bill.png", PNG, "image/png")
    ui.wait_for_selector("#e_preview img")
    for _ in range(30):
        ui.click("#e_preview [data-a=zoomIn]")
    assert zoom(ui) == 800
    for _ in range(40):
        ui.click("#e_preview [data-a=zoomOut]")
    assert zoom(ui) == 25


def test_wheel_keyboard_and_drag(ui):
    open_new(ui)
    choose(ui, "bill.png", PNG, "image/png")
    ui.wait_for_selector("#e_preview img")
    ui.click("#e_preview [data-a=zoom100]")
    box = ui.locator("#e_preview").bounding_box()
    ui.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    ui.mouse.wheel(0, -200)
    ui.wait_for_function("() => +document.querySelector('#e_preview').dataset.zoom > 100")
    ui.mouse.wheel(0, 600)
    ui.wait_for_function("() => +document.querySelector('#e_preview').dataset.zoom < 100")
    ui.focus("#e_preview")
    ui.click("#e_preview [data-a=zoom100]")
    ui.focus("#e_preview")
    ui.keyboard.press("+")
    assert zoom(ui) == pytest.approx(125)
    ui.keyboard.press("-")
    ui.keyboard.press("-")
    assert zoom(ui) == pytest.approx(80)
    ui.keyboard.press("0")
    assert zoom(ui) < 100  # 0 = Fit
    # drag pans the image at 100 %
    ui.click("#e_preview [data-a=zoom100]")
    before = ui.locator("#e_preview img").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui.mouse.move(cx, cy)
    ui.mouse.down()
    ui.mouse.move(cx - 80, cy - 40, steps=5)
    ui.mouse.up()
    after = ui.locator("#e_preview img").bounding_box()
    assert after["x"] == pytest.approx(before["x"] - 80, abs=3) and after["y"] == pytest.approx(before["y"] - 40, abs=3)


def test_pdf_preview_uses_an_iframe_and_always_offers_a_new_tab(ui):
    open_new(ui)
    choose(ui, "bill.pdf", PDF, "application/pdf")
    ui.wait_for_selector("#e_preview iframe")
    assert ui.get_attribute("#e_preview", "data-kind") == "pdf"
    assert ui.get_attribute("#e_preview iframe", "src").startswith("blob:")
    assert ui.locator("#e_preview img").count() == 0
    assert ui.locator("#e_preview_fallback").count() == 1
    assert "new tab" in ui.inner_text("#e_preview_open").lower()
    assert ui.get_attribute("#e_preview_open", "target") == "_blank"


def test_other_file_types_get_no_preview(ui):
    open_new(ui)
    choose(ui, "notes.txt", b"hello", "text/plain")
    ui.wait_for_function("() => !document.querySelector('#e_preview img, #e_preview iframe')")
    assert ui.locator("#e_preview img, #e_preview iframe").count() == 0


def test_blob_url_is_revoked_when_the_file_changes_and_when_the_dialog_closes(ui):
    open_new(ui)
    choose(ui, "one.png", PNG, "image/png")
    ui.wait_for_selector("#e_preview img")
    first = ui.get_attribute("#e_preview img", "src")
    choose(ui, "two.png", make_png(20, 20), "image/png")
    ui.wait_for_function("u => document.querySelector('#e_preview img').src !== u", arg=first)
    assert first in ui.evaluate("window.__revoked")
    second = ui.get_attribute("#e_preview img", "src")
    ui.click("#m-cancel")
    ui.wait_for_selector("#f_itemise", state="detached")
    assert second in ui.evaluate("window.__revoked")


def test_hostile_file_name_is_escaped_in_the_preview(ui):
    name = 'a"onerror="window.__pwn=1" x<b>.png'
    open_new(ui)
    choose(ui, name, make_png(20, 20), "image/png")
    ui.wait_for_selector("#e_preview img")
    assert ui.get_attribute("#e_preview img", "alt") == name
    assert ui.locator("#e_preview b").count() == 0


def test_stored_attachment_is_previewed_when_editing_and_stays_served_safely(srv, ui):
    h = {"Authorization": "Bearer " + srv.token}
    up = httpx.post(srv.base + "/api/attachments", headers=h, files={"file": ("b.png", PNG, "image/png")}).json()
    httpx.post(
        srv.base + "/api/expenses",
        headers=h,
        json={"date": "2026-03-10", "net": 10, "vat": 1.9, "vat_rate": 19, "attachment": up["name"]},
    )
    ui.reload()
    ui.wait_for_selector("tr[data-a=editExpense]")
    ui.click("tr[data-a=editExpense]")
    ui.wait_for_selector("#e_preview img")
    assert ui.get_attribute("#e_preview", "data-kind") == "image"
    ui.wait_for_function("() => document.querySelector('#e_preview img').naturalWidth > 0")  # loads under the CSP
    assert "attachments/" + up["name"] in ui.get_attribute("#e_preview_open", "href")


# ------------------------------------------------------------------ review fixes
def test_a_file_is_previewed_with_the_type_of_its_extension_not_the_browser_guess(ui):
    open_new(ui)
    # record the Blob behind each object URL (the page CSP has no blob: connect-src, so fetch(blob:) cannot be used)
    ui.evaluate(
        "() => { const c = URL.createObjectURL.bind(URL); window.__made = [];"
        " URL.createObjectURL = b => { window.__made.push(b); return c(b); }; }"
    )
    choose(ui, "bill.pdf", b"<!doctype html><p>not a pdf</p>", "text/html")
    ui.wait_for_selector("#e_preview iframe")
    assert ui.get_attribute("#e_preview iframe", "src").startswith("blob:")
    assert ui.evaluate("() => window.__made.at(-1).type") == "application/pdf"


def test_a_stored_rate_outside_19_7_0_is_offered_and_agrees_with_the_totals(srv, ui):
    r = httpx.post(
        srv.base + "/api/expenses",
        headers={"Authorization": "Bearer " + srv.token},
        json={
            "date": "2026-03-10",
            "net": 0,
            "lines": [
                {"description": "Odd rate", "gross": 10.5, "vat_rate": 16},  # the API allows 0, 5, 7, 16, 19
                {"description": "Std", "gross": 1, "vat_rate": 19},
            ],
        },
    )
    assert r.status_code == 200, r.text
    ui.reload()
    ui.wait_for_selector("tr[data-a=editExpense]")
    ui.click("tr[data-a=editExpense]")
    ui.wait_for_selector("#e_lines tr[data-line]")
    row = ui.locator("#e_lines tr[data-line]").first
    assert row.locator("[data-f=vat_rate]").input_value() == "16"
    assert amount(ui.inner_text('[data-tot-vat="16"]')) == D("1.45")  # 10.50 * 16 / 116


def test_enter_pressed_twice_in_a_row_saves_exactly_one_bill(srv, ui):
    open_new(ui)
    ui.fill("#f_vendor", "Double")
    ui.fill("#f_gross", "10")
    ui.select_option("#f_vat_rate", "19")
    # two Enter key events in the same task: the second must find the save still running
    ui.evaluate(
        "() => { const el = document.querySelector('#f_vendor');"
        " for (let i = 0; i < 2; i++) el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); }"
    )
    ui.wait_for_selector("#m-save", state="detached")
    assert [x["vendor"] for x in api(srv, "expenses")] == ["Double"]


def test_business_meals_line_defaults_to_70_percent_and_saves_full_input_vat(srv, ui):
    open_new(ui)
    ui.fill("#f_vendor", "Restaurant")
    ui.select_option("#f_category", "Business meals")
    assert ui.input_value("#f_deductible") == "70"
    ui.check("#f_itemise")
    ui.wait_for_selector("#e_lines tr[data-line]")
    assert ui.locator("#e_lines tr[data-line]").first.locator("[data-f=deductible]").input_value() == "70"
    fill_line(ui, 0, "Dinner", "119", "19")
    ui.click("[data-a=addLine]")
    ui.wait_for_function("() => document.querySelectorAll('#e_lines tr[data-line]').length === 2")
    assert ui.locator("#e_lines tr[data-line]").nth(1).locator("[data-f=deductible]").input_value() == "70"
    ui.locator("#e_lines tr[data-line]").nth(1).locator("[data-f=gross]").fill("0")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    (e,) = api(srv, "expenses")
    assert e["lines"][0]["deductible"] == 70 and e["lines"][0]["vat_deductible"] == 100
    assert e["category"] == "Business meals"


@pytest.mark.parametrize("amount_text,message", [("", "enter the amount"), ("-5", "must not be negative")])
def test_empty_or_negative_line_amount_shows_the_message_and_saves_nothing(srv, ui, amount_text, message):
    itemise_hotel(ui)
    fill_line(ui, 1, "Breakfast", amount_text, "19")
    ui.click("#m-save")
    ui.wait_for_selector("#toast.err")
    assert message in ui.inner_text("#toast")
    assert ui.locator("#m-save").count() == 1  # the dialog stays open
    assert api(srv, "expenses") == []


def test_remove_button_is_disabled_on_the_last_line(ui):
    open_new(ui)
    ui.check("#f_itemise")
    ui.wait_for_selector("#e_lines tr[data-line]")
    assert ui.locator("#e_lines [data-a=removeLine]").first.is_disabled()
    ui.click("[data-a=addLine]")
    ui.wait_for_function("() => document.querySelectorAll('#e_lines tr[data-line]').length === 2")
    assert ui.locator("#e_lines [data-a=removeLine]").first.is_enabled()


def test_switching_itemising_off_after_removing_lines_saves_a_classic_bill(srv, ui):
    itemise_hotel(ui)
    ui.locator("#e_lines tr[data-line]").nth(2).locator("[data-a=removeLine]").click()
    ui.locator("#e_lines tr[data-line]").nth(1).locator("[data-a=removeLine]").click()
    ui.uncheck("#f_itemise")
    ui.wait_for_selector("#f_gross", state="visible")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    (e,) = api(srv, "expenses")
    assert "lines" not in e and e["vendor"] == "Hotel Beispiel"
    assert e["net"] + e["vat"] == pytest.approx(160)
