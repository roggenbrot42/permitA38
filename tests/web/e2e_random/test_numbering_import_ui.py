"""Browser tests for the numbering page and the import wizard (happy path and error rows).

They use the live server fixtures of this package; run with `make test-web` (needs Chromium, see tests/web/README).
"""

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


def test_numbering_preview_follows_the_start_number(make_page, live_server, api):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_next_invoice")
    year = date.today().year
    page.fill("#n_next_invoice", "1043")
    page.wait_for_timeout(700)
    assert f"RE-{year}-1043" in page.locator("#n_prev_invoice").inner_text()


def test_numbering_save_with_warning_asks_and_applies(make_page, live_server, api):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_next_invoice")
    page.fill("#n_next_invoice", "1000")  # a backwards step is only a warning when numbers exist; none yet
    page.wait_for_timeout(500)
    page.once("dialog", lambda d: d.accept())
    page.click('button[data-a=numSave][data-type=invoice]')
    page.wait_for_timeout(800)
    status = api.get("/numbering/status", type="invoice")
    assert status["next"][0].endswith("-1000")


def test_switch_wizard_suggests_pattern_and_next_number(make_page, live_server, api):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/numbering")
    page.wait_for_selector("#n_wizard_last")
    page.fill("#n_wizard_last", "RE-2026-1042")
    page.click("button[data-a=numWizard]")
    out = page.locator("#n_wizard_out").inner_text()
    assert "RE-{year}-{n:04d}" in out and "1043" in out
    page.click("button[data-a=numWizardApply][data-type=invoice]")
    page.wait_for_timeout(500)
    assert page.input_value("#n_next_invoice") == "1043"


def test_import_wizard_happy_path(make_page, live_server, api, tmp_path):
    page, _ = _login(make_page, live_server, api)
    page.goto(live_server.url + "/#/import")
    page.wait_for_selector("#i_file")
    f = tmp_path / "rechnungen.csv"
    f.write_text(CSV_OK, encoding="utf-8")
    page.set_input_files("#i_file", str(f))
    page.wait_for_timeout(300)
    page.click("button[data-a=impCheck]")
    page.wait_for_selector("[data-import-report]")
    assert "1 to import" in page.locator("[data-import-report]").inner_text()
    page.click("button[data-a=impRun]")
    page.wait_for_timeout(800)
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
