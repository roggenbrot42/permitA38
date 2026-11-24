"""Browser tests of the expense dialog: tax treatment, service date, server-computed § 13b VAT, accounting warnings and
the locked-period 409 (docs/specs/credit-notes-payments.md section 5, accounting.md 9.4 and 10)."""

import shutil
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from permita38.core import charts  # noqa: E402
from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)

FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "fixtures"


@pytest.fixture
def srv(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    s = Server(tmp_path)
    yield s
    s.stop()


def api(srv, method, path, **kw):
    return httpx.request(method, srv.base + "/api/" + path, headers={"Authorization": "Bearer " + srv.token}, **kw)


@pytest.fixture
def ui(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    yield pg
    ctx.close()


def open_new(ui, srv):
    ui.goto(srv.base + "/#/expenses")
    ui.wait_for_selector("[data-a=editExpense]")
    ui.click("[data-a=editExpense]")
    ui.wait_for_selector("#f_tax_treatment")


def start_accounting(srv):
    assert (
        api(srv, "POST", "accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": "eur"}).status_code
        == 201
    )
    assert api(srv, "PUT", "settings", json={"accounting.start": "2026-01-01"}).status_code == 200


def expenses(srv):
    return api(srv, "GET", "expenses").json()


def test_form_offers_the_four_treatments_and_a_service_date_with_its_bound(ui, srv):
    open_new(ui, srv)
    opts = ui.locator("#f_tax_treatment option")
    assert opts.evaluate_all("els => els.map(e => e.value)") == [
        "",
        "standard",
        "no_input_vat",
        "reverse_charge_eu_service",
        "reverse_charge_third_country",
    ]
    assert "Automatic" in opts.first.inner_text()
    assert ui.input_value("#f_tax_treatment") == ""
    assert "Automatic" in ui.inner_text("#e_tt_help")
    ui.fill("#f_date", "2026-01-15")
    assert "366 days" in ui.inner_text("#e_sd_help") and "2027-01-16" in ui.inner_text("#e_sd_help")
    assert ui.is_hidden("#e_rc_note")
    ui.select_option("#f_tax_treatment", "reverse_charge_eu_service")
    assert ui.is_visible("#e_rc_note") and "calculated by the server" in ui.inner_text("#e_rc_note")
    assert "§ 13b" in ui.inner_text("#e_tt_help")
    ui.select_option("#f_tax_treatment", "no_input_vat")
    assert ui.is_hidden("#e_rc_note")


def test_reverse_charge_eu_expense_shows_the_server_computed_vat_and_a_tag(ui, srv):
    open_new(ui, srv)
    ui.fill("#f_date", "2026-02-10")
    ui.fill("#f_vendor", "Demo Cloud Ltd")
    ui.fill("#f_gross", "1000")
    ui.select_option("#f_tax_treatment", "reverse_charge_eu_service")
    ui.fill("#f_service_date", "2026-02-01")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    got = expenses(srv)
    assert len(got) == 1
    e = got[0]
    assert (e["tax_treatment"], e["net"], e["vat"], e["service_date"]) == (
        "reverse_charge_eu_service",
        "1000.00",
        "190.00",
        "2026-02-01",
    )
    ui.wait_for_selector("tr[data-id] .pill[data-treatment=reverse_charge_eu_service]")
    assert "§ 13b EU" in ui.inner_text("tr[data-id]")
    assert "190,00" in ui.inner_text("tr[data-id]")  # the list shows the server's VAT


def test_typed_vat_on_a_reverse_charge_bill_is_ignored_and_the_warning_is_shown(ui, srv):
    api(srv, "POST", "suppliers", json={"name": "Demo Cloud Ltd", "country": "IE", "vat_mode": "eu_reverse_charge"})
    ui.goto(srv.base + "/#/expenses")
    ui.wait_for_selector("[data-a=editExpense]")
    ui.click("[data-a=editExpense]")
    ui.wait_for_selector("#f_vendor")
    ui.fill("#f_date", "2026-02-10")
    ui.fill("#f_vendor", "Demo Cloud Ltd")
    ui.fill("#f_gross", "1000")
    ui.wait_for_selector("#f_rc_vat", state="visible")
    ui.fill("#f_rc_vat", "50")  # the old field: the typed VAT is not the server's
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    toast = ui.inner_text("#toast")
    assert "computed by the server" in toast and "ignored" in toast
    e = expenses(srv)[0]
    assert e["vat"] != "50.00" and e["reverse_charge"] is True  # the server's figure, never the typed 50


def test_service_date_beyond_the_bound_is_refused_and_the_dialog_keeps_the_input(ui, srv):
    open_new(ui, srv)
    ui.fill("#f_date", "2026-02-10")
    ui.fill("#f_vendor", "Demo Cloud Ltd")
    ui.fill("#f_gross", "1000")
    ui.select_option("#f_tax_treatment", "reverse_charge_eu_service")
    ui.fill("#f_service_date", "2028-01-01")
    ui.click("#m-save")
    ui.wait_for_selector("#e_save_err")
    assert "366 days" in ui.inner_text("#e_save_err")
    assert ui.is_visible("#m-save")  # still open
    assert ui.input_value("#f_vendor") == "Demo Cloud Ltd" and ui.input_value("#f_gross") == "1000"
    assert expenses(srv) == []


def test_locked_period_edit_shows_the_409_message_and_keeps_the_form(ui, srv):
    start_accounting(srv)
    r = api(
        srv,
        "POST",
        "expenses",
        json={
            "date": "2026-01-15",
            "vendor": "Demo Shop",
            "category": "Software",
            "net": "100.00",
            "vat": "19.00",
            "vat_rate": 19,
        },
    )
    assert r.status_code == 200, r.text
    assert api(srv, "POST", "accounting/locks", json={"year": 2026, "month": 1}).status_code == 201
    ui.goto(srv.base + "/#/expenses")
    ui.wait_for_selector("tr[data-id]")
    ui.click("tr[data-id]")
    ui.wait_for_selector("#f_vendor")
    ui.fill("#f_gross", "238")  # a changed amount must re-post: refused in a locked month
    ui.click("#m-save")
    ui.wait_for_selector("#e_save_err")
    assert "period is locked" in ui.inner_text("#e_save_err").lower() and "book a correction" in ui.inner_text(
        "#e_save_err"
    )
    assert ui.is_visible("#m-save") and ui.input_value("#f_gross") == "238"
    assert expenses(srv)[0]["net"] == "100.00"


def test_description_is_rendered_as_text(ui, srv):
    ui.on("dialog", lambda d: d.dismiss())
    open_new(ui, srv)
    ui.fill("#f_vendor", "Demo Shop")
    ui.fill("#f_description", '<img src=x onerror="window.__xss=1"><b>bold</b>')
    ui.fill("#f_gross", "10")
    ui.select_option("#f_tax_treatment", "no_input_vat")
    ui.click("#m-save")
    ui.wait_for_selector("#m-save", state="detached")
    ui.wait_for_selector("tr[data-id]")
    assert "<b>bold</b>" in ui.inner_text("tr[data-id]")
    assert ui.locator("tr[data-id] b", has_text="bold").count() == 0
    assert ui.evaluate("window.__xss") is None
    assert "no input VAT" in ui.inner_text("tr[data-id]")
