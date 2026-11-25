"""Browser tests of the VAT page, ZM tab and the Today banners (docs/specs/zm.md, ustva.md "UI sketch" / "Deadlines").

Fake data only. The server clock (``zm_reports.today``) and the browser clock are both fixed to 2026-05-10, so Q1 2026
(due Mon 2026-04-27) is overdue and Q2 (due 2026-07-27) is not yet near.
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
NOW = date(2026, 5, 10)
HOSTILE = '<img src=x onerror="window.__pwned=1">'


@pytest.fixture
def srv(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)
    s = Server(tmp_path)
    s.app.state.services.zm_reports.today = lambda: NOW
    s.app.state.services.store.save_settings({"accounting.start": "2026-01-01"})
    s.h = {"Authorization": "Bearer " + s.token}
    s.api("POST", "accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": "accrual"})
    s.cust = s.api("POST", "customers", json={"name": "Demo AT GmbH", "country": "AT", "vat_id": "ATU12345678"}).json()
    s.api(
        "POST", "users", json={"username": "emp", "name": "Emp", "role": "employee", "password": PASSWORD}
    ).raise_for_status()
    yield s
    s.stop()


def api(self, method, path, **kw):
    r = httpx.request(method, self.base + "/api/" + path, headers=self.h, **kw)
    assert r.status_code < 300, (method, path, r.status_code, r.text)
    return r


Server.api = api


def eu_invoice(s, day, price, **extra):
    inv = s.api(
        "POST",
        "invoices",
        json={
            "customer_id": s.cust["id"],
            "date": day,
            "tax_treatment": "eu_b2b_service",
            "lines": [{"description": "Work", "qty": 1, "price": price, "vat": 0}],
            **extra,
        },
    ).json()
    s.api("POST", f"invoices/{inv['id']}/finalize")
    return inv


def open_as(browser, srv, token):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.clock.set_fixed_time("2026-05-10T10:00:00")
    return ctx, pg, problems


def test_zm_quarter_cycle_correction_banner_and_hostile_text(browser, srv):
    eu_invoice(srv, "2026-01-15", "1000.40")
    ctx, pg, problems = open_as(browser, srv, srv.token)
    # Today: the overdue Q1 report is announced
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector(".banner.bad:has-text('ZM Q1 2026')")
    assert "Overdue" in pg.inner_text(".banner.bad")
    # VAT page: navigation entry, tabs, the Returns placeholder
    pg.click("nav.side a[href='#/vat']")
    pg.wait_for_selector(".tabs a:has-text('ZM')")
    pg.click(".tabs a:has-text('Returns')")
    pg.wait_for_selector("text=coming")
    pg.click(".tabs a:has-text('ZM')")
    row = pg.locator("tr[data-href='#/vat/zm/2026/1']")
    row.wait_for()
    text = row.inner_text()
    assert "27.04.2026" in text and "Overdue" in text and "1.000,00" in text
    assert "Not required" in pg.locator("tr[data-href='#/vat/zm/2026/3']").inner_text()
    # open the quarter: live lines, exact sum, create draft, finalize
    row.click()
    pg.wait_for_selector("td:has-text('ATU12345678')")
    assert "1.000,00" in pg.inner_text("#zm-sum")  # 1000.40 truncated to whole euros
    pg.click("button[data-a=zmCreate]")
    pg.wait_for_selector("button[data-a=zmFinalize]")
    pg.click("button[data-a=zmFinalize]")
    pg.wait_for_selector("button[data-a=zmFiled]")
    assert "Final" in pg.inner_text("h2")
    # mark filed with hostile text: shown as text, never as markup
    pg.click("button[data-a=zmFiled]")
    pg.fill("#f_reference", HOSTILE)
    pg.fill("#f_note", HOSTILE)
    pg.click("#m-save")
    pg.wait_for_selector("text=Not a filing")
    pg.wait_for_selector("button[data-a=zmCorrect]")
    assert "Filed" in pg.inner_text("h2")
    assert HOSTILE in pg.inner_text("#main")
    assert pg.locator("#main img").count() == 0 and pg.evaluate("window.__pwned") is None
    # exports: links point at the three export routes and deliver the BZSt file
    hrefs = {i: srv.base + pg.get_attribute(f"#{i}", "href") for i in ("zm-csv", "zm-csv-readable", "zm-pdf")}
    assert hrefs["zm-csv"].endswith("/export.csv") and hrefs["zm-pdf"].endswith("/export.pdf")
    assert hrefs["zm-csv-readable"].endswith("/export.csv?readable=1")
    csv_ = httpx.get(hrefs["zm-csv"], headers=srv.h)
    assert csv_.content.startswith(b"#v1.0\r\n") and b"AT,U12345678,1000,S" in csv_.content
    assert httpx.get(hrefs["zm-pdf"], headers=srv.h).content.startswith(b"%PDF")
    # a credit note of May belongs to Q2 (negative line), the filed Q1 stays free of drift
    inv = srv.api("GET", "invoices").json()[0]
    srv.api(
        "POST",
        f"invoices/{inv['id']}/credit-notes",
        json={"credit_kind": "partial", "date": "2026-05-05", "reason": "Discount", "amounts": {"0": "100.00"}},
    )
    pg.goto(srv.base + "/#/vat/zm/2026/2")
    pg.wait_for_selector("h2:has-text('Q2')")
    pg.wait_for_selector("td:has-text('ATU12345678')")
    assert "-100,00" in pg.inner_text("#zm-sum").replace("−", "-")
    pg.goto(srv.base + "/#/vat/zm/2026/1")
    pg.wait_for_selector("h2:has-text('Q1')")
    pg.wait_for_selector("button[data-a=zmCorrect]")
    assert pg.locator("#main .banner:has-text('must be corrected')").count() == 0
    # an invoice written late for a service of Q1: drift banner, then the correction with the full list
    eu_invoice(srv, "2026-05-04", "500.00", service_to="2026-03-20")
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector(".banner:has-text('ZM of Q1 2026 must be corrected')")
    pg.goto(srv.base + "/#/vat/zm/2026")
    pg.wait_for_selector("tr[data-href='#/vat/zm/2026/1'] .pill:has-text('Must be corrected')")
    pg.click("tr[data-href='#/vat/zm/2026/1'] a:has-text('Open')")
    pg.wait_for_selector("#main .banner:has-text('must be corrected')")
    pg.click("button[data-a=zmCorrect]")
    pg.wait_for_selector("button[data-a=zmFinalize]")
    assert "Correction" in pg.inner_text("h2") and "1.500,00" in pg.inner_text("#zm-sum")
    pg.click("button[data-a=zmFinalize]")
    pg.wait_for_selector("button[data-a=zmFiled]")
    # reopen a final report and delete the resulting draft
    pg.click("button[data-a=zmReopen]")
    pg.wait_for_selector("button[data-a=zmDelete]")
    pg.click("button[data-a=zmDelete]")
    pg.wait_for_selector("tr[data-href='#/vat/zm/2026/1']")
    assert not problems, problems
    ctx.close()


def test_zm_api_error_is_shown_as_a_message(browser, srv):
    eu_invoice(srv, "2026-01-15", "1000.40")
    ctx, pg, problems = open_as(browser, srv, srv.token)
    pg.goto(srv.base + "/#/vat/zm/2026/1")
    pg.wait_for_selector("h2:has-text('Q1')")
    pg.wait_for_selector("button[data-a=zmCreate]")
    srv.api("POST", "zm", json={"year": 2026, "quarter": 1})  # another session created the draft meanwhile
    pg.click("button[data-a=zmCreate]")
    pg.wait_for_selector("#toast.on.err")
    assert "already exists" in pg.inner_text("#toast")
    assert not problems, problems
    ctx.close()


def test_employee_sees_no_vat_entry(browser, srv):
    eu_invoice(srv, "2026-01-15", "1000.40")
    token = httpx.post(srv.base + "/api/auth/login", json={"username": "emp", "password": PASSWORD}).json()["token"]
    ctx, pg, problems = open_as(browser, srv, token)
    pg.goto(srv.base + "/#/today")
    pg.wait_for_selector("nav.side a.nav")
    assert pg.locator("nav.side a[href='#/vat']").count() == 0
    assert pg.locator(".banner:has-text('ZM')").count() == 0
    pg.goto(srv.base + "/#/vat/zm")
    pg.wait_for_selector("#toast.on")
    assert "#/vat" not in pg.url
    assert not problems, problems
    ctx.close()
