"""Personal tax profile page (#/me/tax) against the real server (demo data only).
Contract: docs/specs/tax-burden-and-settings.md, UI and Models."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)
from tests.web.test_settings_pages_ui import EMPLOYEE, open_app  # noqa: E402


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def open_tax(browser, srv):
    token = srv.token
    ctx = browser.new_context(viewport={"width": 1280, "height": 1400})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.goto(srv.base + "/#/me/tax")
    expect(pg.locator("#tax-profile")).to_be_visible()
    return ctx, pg, problems


def stored(srv):
    r = httpx.get(srv.base + "/api/settings/personal-tax", headers={"Authorization": "Bearer " + srv.token})
    assert r.status_code == 200
    return r.json()


def test_banner_then_fill_save_reload_round_trip(browser, srv):
    ctx, pg, problems = open_tax(browser, srv)
    expect(pg.locator("#tp-not-saved")).to_contain_text("Not saved yet")
    pg.select_option("#tp_assessment", "joint")
    pg.fill("#tp_church_tax_percent", "9")
    pg.fill("#tp_other_income_year", "1200.5")
    pg.fill("#tp_children_count", "2")
    pg.check("#tp_children_pv_parent")
    pg.fill("#tp_children_pv_under_25", "1")
    pg.select_option("#tp_health_mode", "pkv")
    pg.fill("#tp_health_pkv_monthly", "480")
    pg.fill("#tp_health_pkv_basis_monthly", "400")
    pg.select_option("#tp_pension_mode", "fixed")
    pg.fill("#tp_pension_fixed_monthly", "300")
    pg.select_option("#tp_unemployment_mode", "voluntary_half")
    pg.check("#tp_has_employment")
    pg.fill("#tp_employment_gross_year", "30000")
    pg.select_option("#tp_employment_steuerklasse", "3")
    pg.fill("#tp_employment_bav_employee_year", "1200")
    pg.fill("#tp_employment_vwl_employer_year", "480")
    pg.click("#tp-save")
    expect(pg.locator("#tp-not-saved")).to_have_count(0)
    got = stored(srv)
    assert got["stored"] is True
    assert got["assessment"] == "joint" and got["church_tax_percent"] == 9
    assert got["other_income_year"] == 1200.5
    assert got["children"] == {"count": 2, "full_allowance": False, "pv_parent": True, "pv_children_under_25": 1}
    assert got["health"]["mode"] == "pkv" and got["health"]["pkv_basis_monthly"] == 400
    assert got["pension"] == {"mode": "fixed", "fixed_monthly": 300}
    assert got["unemployment"]["mode"] == "voluntary_half"
    assert got["employment"]["steuerklasse"] == 3 and got["employment"]["bav_employee_year"] == 1200
    assert got["employment"]["vwl_employer_year"] == 480 and got["employment"]["withheld_year"] is None
    pg.reload()
    expect(pg.locator("#tp_assessment")).to_have_value("joint")
    expect(pg.locator("#tp_children_count")).to_have_value("2")
    expect(pg.locator("#tp_health_pkv_monthly")).to_have_value("480")
    expect(pg.locator("#tp_employment_steuerklasse")).to_have_value("3")
    expect(pg.locator("#tp_employment_gross_year")).to_have_value("30000")
    expect(pg.locator("#tp-not-saved")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_mode_dependent_fields(browser, srv):
    ctx, pg, _ = open_tax(browser, srv)
    gkv, pkv = pg.locator("[data-tp-mode=gkv]"), pg.locator("[data-tp-mode=pkv]")
    expect(gkv).to_be_visible()
    expect(pkv).to_be_hidden()
    expect(pg.locator("#tp_health_zusatzbeitrag_percent")).to_have_attribute("placeholder", "average")
    pg.select_option("#tp_health_mode", "pkv")
    expect(gkv).to_be_hidden()
    expect(pkv).to_be_visible()
    pg.select_option("#tp_health_mode", "none")
    expect(gkv).to_be_hidden()
    expect(pkv).to_be_hidden()
    expect(pg.locator("[data-tp-mode=pension_fixed]")).to_be_hidden()
    pg.select_option("#tp_pension_mode", "fixed")
    expect(pg.locator("[data-tp-mode=pension_fixed]")).to_be_visible()
    expect(pg.locator("[data-tp-mode=employment]")).to_be_hidden()
    pg.check("#tp_has_employment")
    expect(pg.locator("[data-tp-mode=employment]")).to_be_visible()
    pg.uncheck("#tp_has_employment")
    pg.click("#tp-save")
    expect(pg.locator("#tp-not-saved")).to_have_count(0)
    assert stored(srv)["employment"] is None
    ctx.close()


def test_warnings_shown_after_save_and_bounds_block_send(browser, srv):
    ctx, pg, _ = open_tax(browser, srv)
    pg.check("#tp_has_employment")
    pg.fill("#tp_employment_gross_year", "40000")
    pg.select_option("#tp_employment_steuerklasse", "4")  # single + Steuerklasse IV
    pg.click("#tp-save")
    expect(pg.locator("#tp-warnings")).to_contain_text("Steuerklasse III/IV/V means married")
    # PKV basis above premium is refused in the browser; nothing is sent, the stored value stays
    pg.select_option("#tp_health_mode", "pkv")
    pg.fill("#tp_health_pkv_monthly", "100")
    pg.fill("#tp_health_pkv_basis_monthly", "200")
    pg.click("#tp-save")
    expect(pg.locator("#toast")).to_contain_text("must not exceed")
    assert stored(srv)["health"]["mode"] == "gkv"
    # church tax above 15 is refused
    pg.select_option("#tp_health_mode", "gkv")
    pg.fill("#tp_church_tax_percent", "16")
    pg.click("#tp-save")
    expect(pg.locator("#toast")).to_contain_text("Church tax")
    assert stored(srv)["church_tax_percent"] == 0
    ctx.close()


def test_non_boss_sees_boss_only_message(browser):
    ctx, pg, problems, seen = open_app(browser, EMPLOYEE, page="me/tax")
    expect(pg.locator("#main")).to_contain_text("Only the boss")
    expect(pg.locator("#tax-profile")).to_have_count(0)
    assert ("GET", "settings/personal-tax") not in seen
    assert problems == []
    ctx.close()
