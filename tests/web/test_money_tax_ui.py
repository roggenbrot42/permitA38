"""Money & Taxes for the boss: the rendered cards show exactly what GET /api/finance returns (live date, fake data).

The live server uses today's date (no clock pinning in tests/web), so nothing is asserted against literals: the
expected strings are built from the response. Contract: docs/specs/tax-burden-and-settings.md, UI (Money & Taxes)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)


def fmt(v) -> str:
    """The de-DE currency format of the page (`1.234,56 €`) for a response value, spaces normalised."""
    d = Decimal(str(v)).quantize(Decimal("0.01"))
    s = f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    return ("-" if d < 0 else "") + s


def norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace(" ", " ").split())


def make_server(tmp_path, employment):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}
    httpx.put(s.base + "/api/settings", headers=h, json={"company_type": "gewerbe", "state": "BY", "hebesatz": 400})
    if employment is not None:
        r = httpx.put(
            s.base + "/api/settings/personal-tax", headers=h, json={"church_tax_percent": 9, "employment": employment}
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
    return s


@pytest.fixture
def srv(tmp_path):
    s = make_server(tmp_path, {"gross_year": 30000})
    yield s
    s.stop()


@pytest.fixture
def srv_refund(tmp_path):
    s = make_server(tmp_path, {"gross_year": 60000, "withheld_year": 50000})
    yield s
    s.stop()


@pytest.fixture
def srv_unsaved(tmp_path):
    s = make_server(tmp_path, None)
    yield s
    s.stop()


def open_boss_money(browser, srv):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
    f = httpx.get(srv.base + "/api/finance", headers={"Authorization": "Bearer " + token}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 1400})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.goto(srv.base + "/#/money")
    pg.wait_for_selector("h1:has-text('Money')")
    pg.wait_for_selector("#card-tax-burden")
    return ctx, pg, f, problems


def cell(pg, card, key) -> str:
    return norm(pg.locator(f"#{card} [data-k='{key}']").first.text_content())


def test_cards_show_the_response_values(browser, srv):
    ctx, pg, f, problems = open_boss_money(browser, srv)
    assert not f.get("redacted")
    tp, o, r, ins = f["tax_projected"], f["tax_owed"], f["reserve"], f["tax_projected"]["insurance"]
    burden = norm(pg.locator("#card-tax-burden").text_content())
    assert cell(pg, "card-tax-burden", "tax_due") == fmt(tp["tax_due"])
    assert cell(pg, "card-tax-burden", "take_home") == fmt(f["take_home"])
    for k in ("kv", "pv", "rv", "alv"):
        assert fmt(ins[k]) in burden, k
    assert "Social insurance" in burden and "How this was computed" in burden
    assert fmt(tp["zve"]) in burden
    # currently owed
    owed = norm(pg.locator("#card-tax-owed").text_content())
    assert "Owed on the profit booked so far" in owed and "Vorauszahlungen" in owed
    assert fmt(o["profit_ytd"]) in owed and fmt(o["tax_due"]) in owed
    assert cell(pg, "card-tax-owed", "owed") == fmt(abs(o["owed"]))
    assert ("Refund so far" in owed) == (o["owed"] < 0)
    # monthly reserve line
    line = norm(pg.locator("#reserve-line").text_content())
    if r["monthly"]:
        for v in (fmt(r["monthly"]), fmt(r["remaining"]), fmt(r["due_year"]), f"remaining {r['months_left']} months"):
            assert v in line, (v, line)
        assert (fmt(r["owed_now"]) in line) == (r["owed_now"] > 0)
    else:
        assert line.startswith("Set aside") or line == "No further reserve needed"
    # extrapolation note
    assert (pg.locator("#not-extrapolated").count() == 1) == (not f["extrapolated"])
    if not f["extrapolated"]:
        banner = norm(pg.locator("#not-extrapolated").text_content())
        assert "Not yet extrapolated" in banner
        assert (
            "The full-year estimate starts after 60 days of the year; until then the figures cover only the profit "
            "booked so far." in banner
        )
    # the current month's cumulative set-aside is labelled (footnote + tooltip); the data has this month's profit
    assert norm(pg.locator("#month-footnote").text_content()) == (
        "Current month includes the catch-up of tax on the profit so far."
    )
    assert pg.locator("#main table td[title^='Current month includes']").count() == 1
    # month columns: set-aside and take-home as returned, "–" for a month that has not begun
    rows = pg.locator("#main table:has(th:text-is('Take-home')) tr").all()[1:]
    assert len(rows) == 12
    for m, row in zip(f["months"], rows):
        tds = [norm(t) for t in row.locator("td").all_text_contents()]
        assert tds[5] == fmt(m["tax_set_aside"])
        assert tds[7] == ("–" if m["take_home"] is None else fmt(m["take_home"]))
    # warnings are shown
    banners = [norm(t) for t in pg.locator(".banner").all_text_contents()]
    for w in f["warnings"]:
        assert any(w in b for b in banners), w
    for bad in ("NaN", "undefined"):
        assert bad not in pg.text_content("#main")
    assert not problems
    ctx.close()


def test_refund_wording_when_owed_is_negative(browser, srv_refund):
    ctx, pg, f, problems = open_boss_money(browser, srv_refund)
    o = f["tax_owed"]
    assert o["owed"] < 0, o
    owed = norm(pg.locator("#card-tax-owed").text_content())
    assert "Refund so far" in owed and cell(pg, "card-tax-owed", "owed") == fmt(-o["owed"])
    assert fmt(o["withheld"]) in owed
    assert not problems
    ctx.close()


def test_no_profile_saved_warning_is_shown(browser, srv_unsaved):
    ctx, pg, f, problems = open_boss_money(browser, srv_unsaved)
    assert f["warnings"], "the boss gets the 'no profile saved' warning"
    banners = [norm(t) for t in pg.locator(".banner").all_text_contents()]
    for w in f["warnings"]:
        assert any(w in b for b in banners), w
    assert not problems
    ctx.close()
