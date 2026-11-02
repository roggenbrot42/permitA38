"""The two tiles on the Expenses page must equal /api/finance for every accounting mode (demo data only)."""

import re
from datetime import date

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)

YEAR = date.today().year


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def _number(text: str) -> float:
    digits = re.sub(r"[^\d,\-–]", "", text).replace(",", ".")
    return float(digits) if digits not in ("", "–") else 0.0


@pytest.mark.parametrize(
    "accounting,klein", [("eur", False), ("accrual", False), ("eur", True), ("accrual", True)]
)
def test_the_tiles_agree_with_the_finance_figures(browser, srv, accounting, klein):
    head = {"Authorization": "Bearer " + srv.token}
    put = httpx.put(srv.base + "/api/settings", headers=head, json={"accounting": accounting, "kleinunternehmer": klein})
    assert put.status_code == 200, put.text
    bills = [  # bill date, paid on, net, vat, deductible, vat_deductible
        (f"{YEAR - 1}-12-20", f"{YEAR}-01-05", 100.0, 19.0, 100, None),  # billed last year, paid this year
        (f"{YEAR}-03-02", None, 50.0, 9.5, 70, 100),  # a business meal
        (f"{YEAR}-04-10", f"{YEAR}-04-12", 33.33, 6.33, 100, None),
        (f"{YEAR}-12-28", f"{YEAR + 1}-01-03", 20.0, 3.8, 100, None),  # paid next year
    ]
    for day, paid, net, vat, ded, vded in bills:
        body = {"date": day, "paid_on": paid, "net": net, "vat": vat, "vendor": "Beispiel Handel", "deductible": ded}
        if vded is not None:
            body["vat_deductible"] = vded
        r = httpx.post(srv.base + "/api/expenses", headers=head, json=body)
        assert r.status_code in (200, 201), r.text
    fin = httpx.get(srv.base + f"/api/finance?year={YEAR}", headers=head).json()["totals"]

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector(".kpi")
    tiles = pg.locator(".kpi .v").all_inner_texts()
    assert _number(tiles[0]) == pytest.approx(fin["expenses"], abs=0.005)
    assert _number(tiles[1]) == pytest.approx(0.0 if klein else fin["vat_in"], abs=0.005)
    ctx.close()


def test_damaged_paid_on_counts_as_the_bill_date_and_empty_lines_are_skipped(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector(".kpi")
    result = pg.evaluate(
        """(y) => {
          S.settings.accounting = 'eur'; S.settings.kleinunternehmer = false;
          const bills = [
            {date: y + '-03-02', paid_on: 'soon', net: 10, vat: 1.9},        // damaged: the bill date
            {date: y + '-03-03', paid_on: (y + 1) + '-01-02', net: 100, vat: 19},  // paid next year: no cost, VAT counts
            {date: y + '-03-04', paid_on: y + '-02-30', net: 5, vat: 0.95},  // 30 February is no date
            {date: y + '-03-05', lines: [null, {net: 20, vat: 3.8}]},
          ];
          return expenseTiles(bills, y);
        }""",
        YEAR,
    )
    assert result == {"cost": 35.0, "vat": 25.65}
    ctx.close()
