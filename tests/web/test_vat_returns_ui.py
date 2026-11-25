"""Browser tests of the VAT page, Returns tab (docs/specs/ustva.md "UI sketch", "Status, finalise, filed, reopen, amend,
drift", "Sondervorauszahlung"; accounting.md 17).

Fake data only. The server clock (``vat_returns.today``) and the browser clock are fixed to 2026-04-20, so February
(due Tue 2026-03-10) is overdue once it is final and not filed.
"""

import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from permita38.core import charts  # noqa: E402
from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)

CHART = Path(__file__).parents[1] / "backend" / "fixtures" / "chart_synthetic.json"
NOW = date(2026, 4, 20)
HOSTILE = '<img src=x onerror="window.__pwned=1">'


def make_server(tmp_path, monkeypatch, **year):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)
    s = Server(tmp_path)
    s.app.state.services.vat_returns.today = lambda: NOW
    s.app.state.services.store.save_settings({"accounting.start": "2026-01-01"})
    s.h = {"Authorization": "Bearer " + s.token}
    s.api("POST", "accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": "accrual", **year})
    s.cust = s.api("POST", "customers", json={"name": "Demo Kunde GmbH", "country": "DE"}).json()
    return s


@pytest.fixture
def srv(tmp_path, monkeypatch):
    s = make_server(tmp_path, monkeypatch)
    yield s
    s.stop()


@pytest.fixture
def srv_df(tmp_path, monkeypatch):
    s = make_server(tmp_path, monkeypatch, dauerfrist=True)
    yield s
    s.stop()


def api(self, method, path, **kw):
    r = httpx.request(method, self.base + "/api/" + path, headers=self.h, **kw)
    assert r.status_code < 300, (method, path, r.status_code, r.text)
    return r


Server.api = api


def invoice(s, day, price, vat=19, **extra):
    inv = s.api(
        "POST",
        "invoices",
        json={
            "customer_id": s.cust["id"],
            "date": day,
            "lines": [{"description": "Work", "qty": 1, "price": price, "vat": vat}],
            **extra,
        },
    ).json()
    s.api("POST", f"invoices/{inv['id']}/finalize")
    return s.api("GET", f"invoices/{inv['id']}").json()


def open_page(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    dialogs: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    pg.clock.set_fixed_time(f"{NOW.isoformat()}T10:00:00")
    return ctx, pg, problems, dialogs


def test_february_cycle_gap_drill_down_payment_filed_drift_and_amendment(browser, srv):
    jan = invoice(srv, "2026-01-15", "2000.00")
    feb = invoice(srv, "2026-02-10", "1000.00")
    ctx, pg, problems, dialogs = open_page(browser, srv)
    pg.goto(srv.base + "/#/vat")
    pg.wait_for_selector("#vat-list")
    assert "not confirmed by a tax adviser" in pg.inner_text("#vat-adviser")
    assert pg.locator("#vat-list tbody tr").count() == 12 and pg.locator("#vat-special").count() == 0
    jan_row = pg.locator("tr[data-href='#/vat/returns/2026/month/1']").inner_text()
    assert "10.02.2026" in jan_row and "Overdue" in jan_row
    # February: preview with the form figures, create the draft
    pg.click("tr[data-href='#/vat/returns/2026/month/2']")
    pg.wait_for_selector("#vat-boxes")
    row81 = pg.locator("#vat-boxes tr[data-kz='81']").inner_text()
    assert "1.000,00" in row81 and "190,00" in row81
    assert "190,00" in pg.locator("#vat-boxes tr[data-kz='83']").inner_text()
    assert pg.locator("button[data-a=vatBox]").count() == 0  # a preview has no source lines yet
    pg.click("button[data-a=vatCreate]")
    pg.wait_for_selector("button[data-a=vatFinalize]")
    # drill down to the source lines of Kz 81
    pg.click("tr[data-kz='81'] button[data-a=vatBox]")
    pg.wait_for_selector("#vat-box-table")
    assert feb["number"] in pg.inner_text("#vat-box-table") and "190,00" in pg.inner_text("#vat-box-table")
    assert jan["number"] not in pg.inner_text("#vat-box-table")
    # finalize: January is not locked yet, the dialog lists it, the retry confirms it
    pg.click("button[data-a=vatFinalize]")
    pg.wait_for_selector("button[data-a=vatFiled]")
    assert len(dialogs) == 1 and "2026-01" in dialogs[0] and "2026-02" not in dialogs[0]
    assert "Final · not filed" in pg.inner_text("h2") and "Overdue" in pg.inner_text("h2")
    assert "190,00" in pg.locator("#vat-boxes tr[data-kz='83']").inner_text()
    # payments: a partial payment leaves an open rest, the second settles it
    pg.click("button[data-a=vatPay]")
    pg.fill("#f_amount", "100,00")
    pg.click("#m-save")
    pg.wait_for_selector("#vat-pay-table")
    assert pg.inner_text("#vat-pay-state") == "Open" and "90,00" in pg.inner_text("#vat-open")
    pg.click("button[data-a=vatPay]")
    assert pg.input_value("#f_amount") == "90.00"  # proposes the open rest
    pg.click("#m-save")
    pg.wait_for_selector("#vat-pay-state:has-text('Paid')")
    assert "0,00" in pg.inner_text("#vat-open") and pg.locator("#vat-pay-table tbody tr").count() == 2
    # exports: delivered with the session, the lines CSV keeps its own name
    with pg.expect_download() as dl:
        pg.click("#vat-csv-lines")
    assert dl.value.suggested_filename == "export-lines.csv"
    with pg.expect_download() as dl:
        pg.click("#vat-pdf")
    assert Path(dl.value.path()).read_bytes().startswith(b"%PDF")
    assert pg.get_attribute("#vat-json", "href").endswith("/export.json")
    assert pg.get_attribute("#vat-csv", "href").endswith("/export.csv")
    # mark filed with hostile text: shown as text, never as markup
    pg.click("button[data-a=vatFiled]")
    pg.fill("#f_reference", HOSTILE)
    pg.fill("#f_note", HOSTILE)
    pg.click("#m-save")
    pg.wait_for_selector("button[data-a=vatAmend]")
    assert "Filed" in pg.inner_text("h2") and pg.locator("button[data-a=vatReopen]").count() == 0
    assert HOSTILE in pg.inner_text("#main") and pg.locator("#main img").count() == 0
    assert pg.evaluate("window.__pwned") is None and pg.locator("#vat-drift").count() == 0
    # a late invoice (issued in April for a service of February) is drift of the filed return
    late = invoice(srv, "2026-04-15", "500.00", service_to="2026-02-20")
    pg.reload()
    pg.wait_for_selector("#vat-drift")
    text = pg.inner_text("#vat-drift")
    assert "1 entry" in text and "95,00" in text and late["number"] in text
    pg.click("button[data-a=vatAmend]")
    pg.wait_for_selector("button[data-a=vatFinalize]")
    assert "Amended return" in pg.inner_text("h2")
    assert "1" in pg.locator("#vat-boxes tr[data-kz='10']").inner_text()
    assert "285,00" in pg.locator("#vat-boxes tr[data-kz='83']").inner_text()
    assert "1.500,00" in pg.locator("#vat-boxes tr[data-kz='81']").inner_text()
    pg.click("button[data-a=vatFinalize]")
    pg.wait_for_selector("button[data-a=vatFiled]")
    assert len(dialogs) == 1  # no gap this time
    # reopen the amended return, delete the draft: the original is current again
    pg.click("button[data-a=vatReopen]")
    pg.wait_for_selector("button[data-a=vatDelete]")
    pg.click("button[data-a=vatDelete]")
    pg.wait_for_selector("#vat-list")
    assert "Filed" in pg.locator("tr[data-href='#/vat/returns/2026/month/2']").inner_text()
    assert not problems, problems
    ctx.close()


def test_api_error_is_shown_as_a_message(browser, srv):
    invoice(srv, "2026-01-15", "100.00")
    ctx, pg, problems, _ = open_page(browser, srv)
    pg.goto(srv.base + "/#/vat/returns/2026/month/1")
    pg.wait_for_selector("button[data-a=vatCreate]")
    srv.api("POST", "vat-returns", json={"year": 2026, "kind": "month", "no": 1})  # another session was faster
    pg.click("button[data-a=vatCreate]")
    pg.wait_for_selector("#toast.on.err")
    assert "already exists" in pg.inner_text("#toast")
    assert not problems, problems
    ctx.close()


def test_special_prepayment_proposal_amount_and_booked_payment(browser, srv_df):
    s = srv_df
    store = s.app.state.services.store
    for no in range(1, 13):  # final monthly returns of 2025: Kz 83 adds up to 6,600.00 -> 600.00
        store.save(
            "vat_returns",
            {
                "year": 2025, "period_kind": "month", "period_no": no, "from_date": f"2025-{no:02d}-01",
                "to_date": f"2025-{no:02d}-28", "due_date": f"2025-{no:02d}-28", "status": "final", "amends_key": "",
                "finalised_at": 1, "finalised_by": "u1x000", "boxes": {}, "source_lines": [], "rounding_difference_cents": 0,
                "result_cents": 660000 if no == 12 else 0, "special_prepayment_credit_cents": 0,
            },
        )  # fmt: skip
    ctx, pg, problems, _ = open_page(browser, s)
    pg.goto(s.base + "/#/vat/returns/2026")
    pg.wait_for_selector("#vat-special")
    assert "600,00" in pg.inner_text("#sp-proposal")
    assert "6.600,00" in pg.inner_text("#vat-special") and "12 months" in pg.inner_text("#vat-special")
    assert "No payment booked" in pg.inner_text("#sp-payment") and pg.inner_text("#sp-amount") == "–"
    # the proposal is pre-filled; save the amount and book the payment
    pg.click("button[data-a=vatSpecial]")
    assert pg.input_value("#f_amount") == "600.00"
    pg.fill("#f_paid_on", "2026-02-10")
    pg.click("#m-save")
    pg.wait_for_selector("#sp-payment:has-text('Paid')")
    assert "600,00" in pg.inner_text("#sp-amount") and "10.02.2026" in pg.inner_text("#sp-payment")
    # changing the amount while the payment is booked is a 409 shown as a message
    pg.click("button[data-a=vatSpecial]")
    pg.fill("#f_amount", "500,00")
    pg.fill("#f_paid_on", "2026-02-10")
    pg.click("#m-save")
    pg.wait_for_selector("#toast.on.err")
    assert "reverse" in pg.inner_text("#toast")
    assert not problems, problems
    ctx.close()


def test_no_special_prepayment_panel_without_dauerfrist(browser, srv):
    ctx, pg, problems, _ = open_page(browser, srv)
    pg.goto(srv.base + "/#/vat/returns/2026")
    pg.wait_for_selector("#vat-list")
    assert pg.locator("#vat-special").count() == 0
    assert not problems, problems
    ctx.close()
