"""Money & Taxes for a non-boss: business taxes only, no personal figures (live date, fake data)."""

from datetime import date

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)

FORBIDDEN = ("NaN", "undefined", "Einkommensteuer", "Take-home", "Tax reserve", "What stays with you")


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    httpx.put(s.base + "/api/settings", headers=h, json={"company_type": "gewerbe", "state": "BY", "hebesatz": 400})
    r = httpx.put(
        s.base + "/api/settings/personal-tax",
        headers=h,
        json={"church_tax_percent": 9, "other_income_year": 31337, "employment": {"gross_year": 77123}},
    )
    assert r.status_code == 200, r.text
    cust = httpx.post(s.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    inv = httpx.post(
        s.base + "/api/invoices",
        headers=h,
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "price": 5000}]},
    ).json()
    iso = date.today().isoformat()
    httpx.post(s.base + f"/api/invoices/{inv['id']}/finalize", headers=h, json={"date": iso})
    httpx.post(s.base + f"/api/invoices/{inv['id']}/pay", headers=h, json={"paid_date": iso})
    r = httpx.post(
        s.base + "/api/users",
        headers=h,
        json={"username": "acc", "name": "Acc", "role": "accountant", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    yield s
    s.stop()


def open_money(browser, srv, username):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.goto(srv.base + "/#/money")
    pg.wait_for_selector("h1:has-text('Money')")
    pg.wait_for_selector(".card h2")
    return ctx, pg, problems


def test_accountant_sees_business_taxes_only(browser, srv):
    ctx, pg, problems = open_money(browser, srv, "acc")
    text = pg.text_content("#main")  # the DOM text: inner_text would show headers upper-cased by the stylesheet
    assert "Business taxes" in text and "Gewerbesteuer" in text and "Umsatzsteuer" in text
    for bad in FORBIDDEN:
        assert bad not in text, bad
    assert not [h for h in pg.locator("th").all_text_contents() if h in ("Tax reserve", "Take-home")]
    for sentinel in ("31337", "77123"):
        assert sentinel not in text
    assert not problems
    ctx.close()


def test_boss_still_sees_take_home(browser, srv):
    ctx, pg, problems = open_money(browser, srv, "boss")
    text = pg.text_content("#main")
    assert "Take-home" in text and "Tax reserve" in text
    assert "Business taxes" not in text
    assert "NaN" not in text and "undefined" not in text
    assert not problems
    ctx.close()
