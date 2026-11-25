"""The Money page says its figures are approximate until accounting is set up (accounting.md 14, last paragraph)."""

import shutil
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from permita38.core import charts  # noqa: E402
from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402  (shared harness)

_FIXTURES = (chromium,)  # the pytest fixture is registered by importing it here; referenced so the import is used


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_money_page_notes_approximate_figures(browser, srv):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
    f = httpx.get(srv.base + "/api/finance", headers={"Authorization": "Bearer " + token}).json()
    assert f["approximate"] is True  # accounting is not set up on a fresh server
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/money")
    pg.wait_for_selector("h1:has-text('Money')")
    assert "approximate until accounting is set up" in pg.inner_text("#approximate-note")
    ctx.close()


def _small_business_money_page(browser, tmp_path, monkeypatch, treatment):
    """A small-business year with accounting set up and one bill; returns the text of the VAT payable row."""
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    fixture = Path(__file__).resolve().parents[1] / "backend" / "fixtures" / "chart_synthetic.json"
    shutil.copy(fixture, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    s = Server(tmp_path)
    try:
        h = {"Authorization": "Bearer " + s.token}

        def call(method, path, **kw):
            r = httpx.request(method, s.base + "/api" + path, headers=h, **kw)
            assert r.status_code in (200, 201), (path, r.text)
            return r.json()

        call("PUT", "/settings", json={"accounting.start": "2026-01-01", "kleinunternehmer": True})
        call(
            "POST",
            "/accounting/years",
            json={"year": 2026, "chart": "skr03", "income_basis": "accrual", "small_business": True},
        )
        bill = {"date": "2026-05-20", "vendor": "Demo Cloud Ltd", "category": "software", "net": 1000, "vat": 0}
        if treatment:
            bill.update(service_date="2026-05-20", vat_rate=19, tax_treatment=treatment)
        call("POST", "/expenses", json=bill)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(f"localStorage.setItem('permita38.token', '{s.token}')")
        pg = ctx.new_page()
        pg.goto(s.base + "/#/money/2026")
        pg.wait_for_selector("h1:has-text('Money')")
        row = pg.locator("tr:has-text('Umsatzsteuer (VAT) payable')").first
        row.wait_for()
        text = row.inner_text()
        ctx.close()
        return text
    finally:
        s.stop()


def test_small_business_shows_the_13b_vat_it_owes(browser, tmp_path, monkeypatch):
    text = _small_business_money_page(browser, tmp_path, monkeypatch, "reverse_charge_eu_service")
    assert "190,00" in text


def test_small_business_without_13b_shows_a_dash(browser, tmp_path, monkeypatch):
    text = _small_business_money_page(browser, tmp_path, monkeypatch, None)
    assert "–" in text and "190" not in text
