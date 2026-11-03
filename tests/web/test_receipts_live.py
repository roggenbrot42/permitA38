"""Receipt scan against the real server and a real browser: no mocked route. Generated PDFs with a text layer go
through upload, the worker process, the rules and the booking (demo data only)."""

from datetime import date, timedelta

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from permita38.core.pdf import Pdf  # noqa: E402
from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


def bill_pdf(vendor: str, number: str, total: str) -> bytes:
    """A one-rate bill: net 10,00 + VAT 1,90 = 11,90, with ``total`` printed as the grand total."""
    day = (date.today() - timedelta(days=3)).strftime("%d.%m.%Y")
    pdf = Pdf()
    for text, x, y, size in [
        (vendor, 60, 80, 14),
        ("Musterstrasse 1, 12345 Beispielstadt", 60, 100, 10),
        (f"Rechnung Nr. {number}", 60, 130, 11),
        (f"Rechnungsdatum {day}", 60, 145, 11),
        ("Steuersatz Netto MwSt Brutto", 60, 585, 10),
        ("A 19,00% 10,00 1,90 11,90", 60, 600, 11),
        (f"Gesamt {total} EUR", 300, 700, 12),
    ]:
        pdf.text(x, y, text, size=size)
    return pdf.output()


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_upload_booked_flagged_edit_save_and_duplicate(browser, srv):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    ctx.add_cookies([{"name": "authorization", "value": srv.token, "url": srv.base}])
    pg = ctx.new_page()
    pg.set_default_timeout(20000)
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector("#rcpt_file", state="attached")

    def upload(name: str, data: bytes) -> None:
        pg.set_input_files("#rcpt_file", [{"name": name, "mimeType": "application/pdf", "buffer": data}])

    good = bill_pdf("Beispiel Werkstatt GmbH", "2026-77", "11,90")
    upload("good.pdf", good)
    pg.wait_for_selector('.rcpt-row[data-state="added"]')
    assert "Added: Beispiel Werkstatt GmbH" in pg.locator("#rcpt_panel .rcpt-row").first.inner_text()

    upload("bad.pdf", bill_pdf("Beispiel Papier GmbH", "2026-78", "12,90"))
    pg.wait_for_selector('.rcpt-row[data-state="check"]')
    pg.wait_for_selector("#rcpt_flagged tr[data-id]")
    pg.click("#rcpt_flagged tr[data-id]")
    pg.wait_for_selector("#m-save")
    assert pg.input_value("#f_vendor") == "Beispiel Papier GmbH"
    assert "do not add up" in pg.inner_text("#rcpt_flags")
    pg.fill("#f_vendor", "Beispiel Papier GmbH (geprüft)")
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    pg.wait_for_selector("#rcpt_flagged", state="detached")

    upload("again.pdf", good)
    pg.wait_for_selector('.rcpt-row[data-state="duplicate"]')
    assert "already" in pg.locator('#rcpt_panel .rcpt-row[data-state="duplicate"]').inner_text()

    h = {"Authorization": "Bearer " + srv.token}
    expenses = httpx.get(srv.base + "/api/expenses", headers=h).json()
    assert sorted(e["vendor"] for e in expenses) == ["Beispiel Papier GmbH (geprüft)", "Beispiel Werkstatt GmbH"]
    assert sorted((e["gross"] if "gross" in e else round(e["net"] + e["vat"], 2)) for e in expenses) == [11.9, 12.9]
    scans = httpx.get(srv.base + "/api/receipts?status=flagged", headers=h).json()["items"]
    assert scans == []
    assert problems == []
    ctx.close()
