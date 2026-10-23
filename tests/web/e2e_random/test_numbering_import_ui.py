"""Browser tests for the numbering page and the import wizard (happy path and error rows).

They use the live server fixtures of this package; run with `make test-web` (needs Chromium, see tests/web/README).
"""

import random
from datetime import date

import pytest

pytestmark = pytest.mark.e2e_random

CSV_OK = (
    "Rechnungsnummer;Rechnungsdatum;Kunde;Netto;USt;Brutto;Status\n"
    "WEB-2025-0001;03.04.2025;Web Kunde GmbH;100,00;19,00;119,00;bezahlt\n"
)
CSV_BAD = CSV_OK.replace("119,00", "119,01") + "WEB-2025-0002;05.04.2025;Web Kunde GmbH;10;1,90;11,90;kaputt\n"


def _login(make_page, live_server, api):
    api.setup_boss()
    return make_page(token=api.http.headers["Authorization"].split()[1])


def _import_via_api(api, csv_text: str):
    r = api.http.post("/import/run", data={"type": "invoices"}, files={"file": ("seed.csv", csv_text.encode())})
    assert r.status_code == 200, r.text
    return r.json()


def test_numbering_preview_follows_the_start_number(make_page, live_server, api):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_next_invoice")
    year = date.today().year
    page.fill("#n_next_invoice", "1043")
    page.wait_for_function(
        "t => document.getElementById('n_prev_invoice').innerText.includes(t)", arg=f"RE-{year}-1043"
    )


def test_numbering_save_with_a_real_warning_asks_first(make_page, live_server, api):
    year = date.today().year
    today = date.today().strftime("%d.%m.%Y")
    page, watch = _login(make_page, live_server, api)
    _import_via_api(api, f"Nummer;Datum;Kunde;Netto\nRE-{year}-0005;{today};Warn Kunde;10\n")
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_next_invoice")
    page.fill("#n_next_invoice", "2")  # backwards: 0005 is already used in this period
    page.wait_for_selector('#n_prev_invoice [data-warn="backwards"]')
    messages: list[str] = []
    page.on("dialog", lambda d: messages.append(d.message))  # the fixture's handler answers; this one only records
    watch.rng = random.Random(1)
    watch.accept_confirm = 0.0  # cancel in the first dialog: nothing may change
    page.click("button[data-a=numSave][data-type=invoice]")
    page.wait_for_load_state("networkidle")
    assert len(messages) == 1 and "already used" in messages[0] and "Save anyway?" in messages[0]
    assert api.get("/numbering/log") == []
    watch.accept_confirm = 1.0  # accept in the second one: applied and logged
    page.click("button[data-a=numSave][data-type=invoice]")
    page.wait_for_selector("#toast.on")
    log = api.get("/numbering/log")
    assert len(log) == 1 and "backwards" in log[0]["warnings"]
    nxt = api.get("/numbering/status", type="invoice")["next"]
    assert nxt == [f"RE-{year}-{n:04d}" for n in (2, 3, 4, 6, 7)]  # 0005 stays taken and is skipped


def test_switch_wizard_suggests_pattern_and_next_number(make_page, live_server, api):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_wizard_last")
    page.fill("#n_wizard_last", "RE-2026-1042")
    page.click("button[data-a=numWizard]")
    out = page.locator("#n_wizard_out").inner_text()
    assert "RE-{year}-{n:04d}" in out and "1043" in out
    page.click("button[data-a=numWizardApply][data-type=invoice]")
    page.wait_for_function("() => document.getElementById('n_next_invoice').value === '1043'")


def test_import_wizard_happy_path(make_page, live_server, api, tmp_path):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/import")
    page.wait_for_selector("#i_file")
    f = tmp_path / "rechnungen.csv"
    f.write_text(CSV_OK, encoding="utf-8")
    page.set_input_files("#i_file", str(f))
    page.wait_for_selector("text=rechnungen.csv")
    page.click("button[data-a=impCheck]")
    page.wait_for_selector("[data-import-report]")
    assert "1 to import" in page.locator("[data-import-report]").inner_text()
    page.click("button[data-a=impRun]")
    page.wait_for_selector("button[data-a=impUndo]")  # the batch shows up under "Earlier imports"
    assert len(api.get("/import/batches")) == 1
    numbers = [i["number"] for i in api.get("/invoices")]
    assert numbers == ["WEB-2025-0001"]  # kept as in the file


def test_import_wizard_error_rows_block_the_import(make_page, live_server, api, tmp_path):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/import")
    page.wait_for_selector("#i_file")
    f = tmp_path / "fehler.csv"
    f.write_text(CSV_BAD, encoding="utf-8")
    page.set_input_files("#i_file", str(f))
    page.click("button[data-a=impCheck]")
    page.wait_for_selector("[data-import-report]")
    report = page.locator("[data-import-report]")
    assert "totals do not match" in report.inner_text()
    assert page.locator("button[data-a=impRun]").is_disabled()
    assert api.get("/invoices") == []


def test_import_undo_from_the_ui(make_page, live_server, api, tmp_path):
    page, _ = _login(make_page, live_server, api)
    _import_via_api(api, CSV_OK)
    assert len(api.get("/invoices")) == 1
    page.goto(live_server.url + "/#/import")
    page.wait_for_selector("button[data-a=impUndo]")
    page.once("dialog", lambda d: d.accept())
    page.click("button[data-a=impUndo]")
    page.wait_for_function("() => !document.querySelector('button[data-a=impUndo]')")
    assert api.get("/invoices") == [] and api.get("/customers") == []
    assert [b["status"] for b in api.get("/import/batches")] == ["undone"]


def test_hostile_file_content_is_escaped_in_the_import_report(make_page, live_server, api, tmp_path):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/import")
    page.wait_for_selector("#i_file")
    evil_name = '<img src=x onerror="window.__xss=1">'
    evil_header = "<script>window.__xss=2</script>"
    f = tmp_path / "evil.csv"
    f.write_text(
        f"Nummer;Datum;Kunde;Netto;{evil_header}\nEV-1;01.01.2026;{evil_name};10;<b>x</b>\n", encoding="utf-8"
    )
    page.set_input_files("#i_file", str(f))
    page.wait_for_selector("text=evil.csv")
    page.click("button[data-a=impCheck]")
    page.wait_for_selector("[data-import-report]")
    report = page.locator("[data-import-report]")
    assert evil_name in report.inner_text() and evil_header in report.inner_text()  # shown as text
    assert report.locator("img, script").count() == 0
    assert page.evaluate("window.__xss") is None
    page.click("button[data-a=impRun]")
    page.wait_for_selector("button[data-a=impUndo]")
    page.goto(live_server.url + "/#/customers")
    page.wait_for_selector("text=onerror")
    assert page.locator("main img, #app img").count() == 0 and page.evaluate("window.__xss") is None
