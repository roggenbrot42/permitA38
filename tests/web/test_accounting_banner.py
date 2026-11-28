"""Browser tests of the accounting set-up state and the adviser banner (accounting.md 16, 22 W9)."""

import shutil
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from permita38.core import charts  # noqa: E402
from permita38.core.service import accounting_status  # noqa: E402
from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)
from tests.web.test_roles_ui import open_as  # noqa: E402


CHART = Path(__file__).parents[1] / "backend" / "fixtures" / "chart_synthetic.json"


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    r = httpx.post(
        s.base + "/api/users",
        headers=h,
        json={"username": "accountant", "name": "Accountant", "role": "accountant", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    yield s
    s.stop()


def initialise(srv):
    store = srv.app.state.services.store
    settings = store.settings()
    settings["accounting.start"] = "2026-01-01"
    store.save_settings(settings)
    r = httpx.post(
        srv.base + "/api/accounting/years",
        headers={"Authorization": "Bearer " + srv.token},
        json={"year": 2026, "chart": "skr03", "income_basis": "eur"},
    )
    assert r.status_code == 201, r.text


def login_boss(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    return ctx, pg, problems


def test_setup_panel_before_initialisation_boss_and_accountant(browser, srv):
    ctx, pg, problems = login_boss(browser, srv)
    for page in ("vat", "accounting"):
        pg.goto(srv.base + f"/#/{page}")
        expect(pg.get_by_test_id("accounting-setup")).to_be_visible()
        expect(pg.get_by_test_id("accounting-setup-link")).to_have_attribute("href", "#/accounting/setup")
        expect(pg.get_by_test_id("adviser-banner")).to_have_count(0)
    pg.get_by_test_id("accounting-setup-link").click()
    expect(pg.get_by_test_id("wz")).to_be_visible()
    ctx.close()

    ctx, pg, problems2 = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/#/vat")
    expect(pg.get_by_test_id("accounting-setup-ask")).to_have_text("Ask the boss to set up accounting.")
    expect(pg.get_by_test_id("accounting-setup-link")).to_have_count(0)
    ctx.close()
    assert not problems and not problems2


def test_adviser_banner_after_initialisation_and_dismissal(browser, srv, chart_dir):
    initialise(srv)
    ctx, pg, problems = login_boss(browser, srv)
    for page in ("vat", "accounting"):
        pg.goto(srv.base + f"/#/{page}")
        expect(pg.get_by_test_id("accounting-setup")).to_have_count(0)
        expect(pg.get_by_test_id("adviser-banner")).to_contain_text("Tax rules not confirmed by a tax adviser")
    expect(pg.get_by_role("heading", name="Accounting", exact=True)).to_be_visible()  # the accounting page has replaced the VAT one
    pg.get_by_test_id("adviser-banner").get_by_role("button").click()
    expect(pg.get_by_test_id("adviser-banner")).to_have_count(0)
    pg.goto(srv.base + "/#/accounting")  # dismissed for the session
    pg.reload()
    expect(pg.get_by_role("heading", name="Accounting", exact=True)).to_be_visible()
    expect(pg.get_by_test_id("adviser-banner")).to_have_count(0)
    ctx.close()
    assert not problems


def test_no_banner_when_rules_are_confirmed(browser, srv, chart_dir, monkeypatch):
    initialise(srv)
    monkeypatch.setattr(accounting_status, "vat_rules_unconfirmed", lambda year: False)
    monkeypatch.setattr(accounting_status, "chart_unconfirmed", lambda chart, year: False)
    monkeypatch.setattr(accounting_status, "datev_unconfirmed", lambda year: False)
    ctx, pg, problems = login_boss(browser, srv)
    pg.goto(srv.base + "/#/vat")
    expect(pg.get_by_role("heading", name="VAT", exact=True)).to_be_visible()
    expect(pg.get_by_test_id("adviser-banner")).to_have_count(0)
    ctx.close()
    assert not problems
