"""Document scan against the real server and a real browser: no mocked route (counterpart of test_docscan_ui.py).
Generated PDFs with a text layer of invented invoices go through upload, the worker, the rules and the booking."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from permita38.core.pdf import Pdf  # noqa: E402
from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


def invoice_pdf(number: str, total: str) -> bytes:
    """An invoice from the owner of the invented company.

    Net 750,00 = 10 x 50,00 (500,00) + 4 x 62,50 (250,00); VAT 19 % of 750,00 = 142,50; gross 892,50.
    ``total`` is the printed grand total."""
    rows = [
        ("Max Muster IT-Beratung · Musterweg 1 · 12345 Musterstadt", 7),
        None,
        ("Beispiel Kunde GmbH", 10),  # the recipient block: lines 14 pt apart, no gap inside
        ("Kundenweg 5", 10),
        ("54321 Kundenstadt", 10),
        None,
        ("Rechnung", 16),
        (f"Rechnungsnummer: {number}", 10),
        ("Rechnungsdatum: 05.03.2024", 10),
        ("Leistungszeitraum: 01.02.2024 - 29.02.2024", 10),
        ("USt-IdNr. DE123456789", 10),
        None,
        ("Pos Beschreibung Menge Einzelpreis Gesamt", 10),
        ("1 Beratung 10 Std 50,00 500,00", 10),
        ("2 Entwicklung Modul A 4 Std 62,50 250,00", 10),
        ("Nettobetrag 750,00 EUR", 10),
        ("zzgl. 19% MwSt 142,50 EUR", 10),
        (f"Gesamtbetrag {total} EUR", 10),
        ("Zahlbar innerhalb von 14 Tagen ohne Abzug", 10),
    ]
    pdf = Pdf()
    y = 34.0
    for i, row in enumerate(rows):
        if row:
            pdf.text(42, y, row[0], size=row[1])
        y += 14 if 2 <= i <= 3 else 25
    return pdf.output()


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    store = s.app.state.services.store
    store.save_settings(
        {**store.settings(), "company_name": "Max Muster IT-Beratung", "owner_name": "Max Muster", "vat_id": "DE123456789"}
    )
    yield s
    s.stop()


def test_clean_flagged_confirm_and_duplicate(browser, srv):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    ctx.add_cookies([{"name": "authorization", "value": srv.token, "url": srv.base}])
    pg = ctx.new_page()
    pg.set_default_timeout(20000)
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("#ds_file", state="attached")

    def upload(name: str, data: bytes) -> None:
        pg.set_input_files("#ds_file", [{"name": name, "mimeType": "application/pdf", "buffer": data}])

    good = invoice_pdf("RE-2024-007", "892,50")
    upload("good.pdf", good)
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="added"]')
    assert "RE-2024-007 added to 2024" in pg.locator("#ds_panel .rcpt-row").first.inner_text()
    pg.wait_for_selector("text=RE-2024-007")

    # printed 900,00 against the computed 892,50: flagged
    upload("bad.pdf", invoice_pdf("RE-2024-008", "900,00"))
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="check"]')
    pg.wait_for_selector("#ds_flagged tr[data-id]")
    with pg.expect_response(lambda r: "/api/document-scans/" in r.url and "/pages/" in r.url) as resp:
        pg.click("#ds_flagged tr[data-id]")
    assert resp.value.status == 200 and resp.value.headers["content-type"] == "image/png"
    pg.wait_for_selector("#ds_add")
    assert pg.input_value("#ds_number") == "RE-2024-008"
    pg.check("#ds_ck_keep")  # keep the printed total of 900,00 (difference 892,50 - 900,00 = -7,50)
    pg.click("#ds_add")
    pg.wait_for_selector("#ds_add", state="detached")
    pg.wait_for_selector("#ds_flagged", state="detached")

    upload("again.pdf", good)
    pg.wait_for_selector('#ds_panel .rcpt-row[data-state="duplicate"]')

    h = {"Authorization": "Bearer " + srv.token}
    invoices = httpx.get(srv.base + "/api/invoices", headers=h).json()
    assert sorted(i["number"] for i in invoices) == ["RE-2024-007", "RE-2024-008"]
    assert httpx.get(srv.base + "/api/document-scans", headers=h).json() == {"items": []}
    assert problems == []
    ctx.close()
