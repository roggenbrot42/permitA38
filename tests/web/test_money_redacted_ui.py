"""Money & Taxes for a non-boss: business taxes only, no personal figures (live date, fake data)."""

from datetime import date

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402  (shared harness)

_FIXTURES = (chromium,)  # the pytest fixture is registered by importing it here; referenced so the import is used


def de_eur(n):
    """The page's de-DE currency format (Intl): 3570 -> '3.570,00 €' with a no-break space before the sign."""
    whole, cents = f"{abs(n):.2f}".split(".")
    groups = f"{int(whole):,}".replace(",", ".")
    return f"{'-' if n < 0 else ''}{groups},{cents}\u00a0€"


FORBIDDEN = ("NaN", "undefined", "Einkommensteuer", "Take-home", "Tax reserve", "What stays with you")


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    httpx.put(s.base + "/api/settings", headers=h, json={"company_type": "gewerbe", "state": "BY", "hebesatz": 400})
    r = httpx.put(
        s.base + "/api/settings/personal-tax",
        headers=h,
        json={
            "church_tax_percent": 9,
            "other_income_year": 31337,
            "employment": {"gross_year": 77123, "sv_liable": True, "steuerklasse": 1, "withheld_year": 4243.17},
        },
    )
    assert r.status_code == 200, r.text
    cust = httpx.post(s.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    inv = httpx.post(
        s.base + "/api/invoices",
        headers=h,
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "price": 50000}]},
    ).json()
    iso = date.today().isoformat()
    r = httpx.post(s.base + f"/api/invoices/{inv['id']}/finalize", headers=h, json={"date": iso})
    assert r.status_code == 200, r.text
    full = httpx.get(s.base + f"/api/invoices/{inv['id']}", headers=h).json()
    r = httpx.put(s.base + f"/api/invoices/{inv['id']}", headers=h, json={**full, "status": "paid", "paid_date": iso})
    assert r.status_code == 200, r.text
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
    # Gewerbesteuer on 50 000 profit, Hebesatz 400: (50 000 - 24 500) x 3.5 % x 400 % = 3 570.00 (actual, not projected)
    assert de_eur(3570) in text
    boss_tok = httpx.post(srv.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
    boss = httpx.get(srv.base + "/api/finance", headers={"Authorization": "Bearer " + boss_tok}).json()
    assert (
        "redacted" not in boss and boss["tax_actual"]["gewst"] == 3570.0 and boss["tax_actual"]["withheld"] == 4243.17
    )
    seeded = [31337, 77123, 4243.17] + [
        boss["tax_actual"][k] for k in ("zve", "est", "social", "tax_due", "emp_income")
    ]
    for v in seeded:
        assert v > 1000
        for shown in (de_eur(v), f"{v}"):
            assert shown not in text, shown
    assert not any(str(v) in text for v in ("31337", "77123", "4243.17"))
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
