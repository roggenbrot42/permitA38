"""Accounting page (journal and reports) against the real server, on a small posted scenario created through the API.
Contract: docs/specs/accounting.md 14, 17, 18. Fake demo data only; the synthetic chart replaces the real one.

Accrual scenario 2026 (hand-computed):
  1  2026-01-10 invoice: receivables +119.00, revenue -100.00, VAT out -19.00
  2  2026-01-20 payment 50.00: bank +50.00, receivables -50.00
  3  2026-02-05 expense 30.00 + 5.70 VAT: expense +30.00, input VAT +5.70, payables -35.70
  trial balance: debit 204.70 = credit 204.70; open: receivable 69.00 (355 days to 31 Dec), payable 35.70;
  P&L: income 100.00, expenses 30.00, profit 70.00; balance sheet: debit 124.70, credit 54.70, result 70.00.
"""

import shutil
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from permita38.core import charts  # noqa: E402
from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402,F401  (shared harness)

CHART = Path(__file__).parents[1] / "backend" / "fixtures" / "chart_synthetic.json"


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)


def make_server(tmp_path, basis):
    s = Server(tmp_path)
    h = {"Authorization": "Bearer " + s.token}

    def call(method, path, **kw):
        r = httpx.request(method, s.base + "/api" + path, headers=h, **kw)
        assert r.status_code in (200, 201), (path, r.text)
        return r.json()

    call("PUT", "/settings", json={"accounting.start": "2026-01-01"})
    call("POST", "/accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": basis})
    cust = call("POST", "/customers", json={"name": "Demo GmbH"})
    inv = call(
        "POST",
        "/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-01-10",
            "lines": [{"description": "Work", "qty": 1, "price": 100}],
        },
    )
    call("POST", f"/invoices/{inv['id']}/finalize")
    amount = "50.00" if basis == "accrual" else "119.00"
    call("POST", f"/invoices/{inv['id']}/payments", json={"date": "2026-01-20", "amount": amount})
    call(
        "POST",
        "/expenses",
        json={
            "date": "2026-02-05",
            "net": 30,
            "vat": 5.7,
            "vendor": "Demo Supplier",
            "supplier_id": call("POST", "/suppliers", json={"name": "Demo Supplier"})["id"],
            "description": "Software",
        },
    )
    s.call = call
    return s


@pytest.fixture
def accrual(tmp_path, chart_dir):
    s = make_server(tmp_path, "accrual")
    yield s
    s.stop()


@pytest.fixture
def eur(tmp_path, chart_dir):
    s = make_server(tmp_path, "eur")
    yield s
    s.stop()


def open_page(browser, srv, hash_, token=None):
    ctx = browser.new_context(viewport={"width": 1280, "height": 1200})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token or srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.goto(srv.base + "/#/accounting/" + hash_)
    return ctx, pg, problems


def rows(pg, testid):
    """Normalised cell texts of every row of a table (non-breaking spaces become plain ones)."""
    out = pg.locator(f"[data-testid={testid}] tr").evaluate_all(
        "trs => trs.map(tr => [...tr.children].map(c => c.innerText.replace(/\\u00a0/g, ' ').trim()))"
    )
    return [r for r in out if r]


def test_every_tab_shows_the_known_figures(browser, accrual):
    ctx, pg, problems = open_page(browser, accrual, "journal")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(3)
    tabs = [t.strip() for t in pg.locator(".tabs a").all_inner_texts()]
    assert tabs == ["Journal", "Ledger", "Trial balance", "Profit & loss", "Balance sheet", "Open items"]  # no EÜR tab
    # journal: expand the invoice entry to see its lines
    first = pg.locator("[data-testid=acc-entry]").first
    assert "Invoice RE-2026-0001" in first.inner_text()
    first.locator("summary").click()
    lines = first.locator("tbody tr").evaluate_all(
        "trs => trs.map(tr => [...tr.children].map(c => c.innerText.replace(/\\u00a0/g, ' ').trim()))"
    )
    assert [(r[0], r[2], r[3]) for r in lines] == [
        ("receivables", "119,00 €", ""),
        ("revenue_s19", "", "100,00 €"),
        ("vat_out_s19", "", "19,00 €"),
    ]
    assert "S19" in lines[2][4] and "100,00 €" in lines[2][4]
    assert lines[1][6] == "10.01.2026"  # income date
    # filters: a tax key narrows the list to the invoice, paging is off for three entries
    pg.fill("#acc_taxkey", "S19")
    pg.click("[data-a=accApply]")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(1)
    pg.fill("#acc_taxkey", "")
    pg.select_option("#acc_account", "9001")
    pg.click("[data-a=accApply]")
    expect(pg.locator("[data-testid=acc-entry]")).to_contain_text("Payment")  # only the payment touches the bank
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(1)
    pg.click("[data-a=accReset]")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(3)

    # ledger: pick the receivables account; running balance 119.00 then 69.00
    pg.click("[data-testid=acc-tab-ledger]")
    expect(pg.locator("#acc_ledger")).to_be_visible()
    pg.select_option("#acc_ledger", "9003")
    expect(pg.locator("[data-testid=acc-ledger]")).to_be_visible()
    led = rows(pg, "acc-ledger")
    assert led[1][0] == "Opening balance" and led[1][1] == "0,00 €"
    assert [(r[3], r[4], r[5]) for r in led[2:4]] == [("119,00 €", "", "119,00 €"), ("", "50,00 €", "69,00 €")]
    assert led[4][1] == "69,00 €"

    # trial balance
    pg.click("[data-testid=acc-tab-trial-balance]")
    expect(pg.locator("[data-testid=acc-trial]")).to_be_visible()
    tb = rows(pg, "acc-trial")
    by_account = {r[0]: r[2:] for r in tb if r[0].isdigit()}
    assert by_account["9001"] == ["50,00 €", "0,00 €", "50,00 €"]
    assert by_account["9003"] == ["119,00 €", "50,00 €", "69,00 €"]
    assert by_account["9004"] == ["0,00 €", "35,70 €", "-35,70 €"]
    assert by_account["9007"] == ["0,00 €", "100,00 €", "-100,00 €"]
    assert tb[-1][1:3] == ["204,70 €", "204,70 €"] and tb[-1][0] == "Total"
    assert "VAT rounding" in pg.inner_text("#acc-trial-note")

    # EÜR does not exist in an accrual year: the route falls back to the journal, no error toast
    pg.goto(accrual.base + "/#/accounting/eur")
    expect(pg.locator("[data-testid=acc-entry]").first).to_be_visible()

    # profit and loss
    pg.click("[data-testid=acc-tab-pnl]")
    expect(pg.locator("[data-testid=acc-pnl]")).to_be_visible()
    pnl = rows(pg, "acc-pnl")
    totals = {r[0]: r[-1] for r in pnl if len(r) >= 2}
    assert (
        totals["Total income"] == "100,00 €" and totals["Total expenses"] == "30,00 €" and totals["Profit"] == "70,00 €"
    )
    by_name = {r[1]: r[2] for r in pnl if len(r) == 3 and r[0].isdigit()}
    assert by_name["Synthetic expense_other"] == "30,00 €"  # an expense row is positive under "Expenses"
    assert by_name["Synthetic revenue_s19"] == "100,00 €"

    # balance sheet
    pg.click("[data-testid=acc-tab-balance-sheet]")
    expect(pg.locator("[data-testid=acc-balance]")).to_be_visible()
    text = pg.inner_text("#main").replace(" ", " ")
    assert "124,70 €" in text and "54,70 €" in text and "Result of 2026" in text and "70,00 €" in text
    assert "earlier years" not in text and "does not balance" not in text

    # open items with age: 10 Jan to 31 Dec 2026 is 355 days
    pg.click("[data-testid=acc-tab-open-items]")
    expect(pg.locator("[data-testid=acc-open-receivable]")).to_be_visible()
    rec = rows(pg, "acc-open-receivable")
    assert rec[1] == ["RE-2026-0001", "Demo GmbH", "10.01.2026", "355", "69,00 €"]
    assert rows(pg, "acc-open-payable")[-1][-1] == "35,70 €"
    assert rows(pg, "acc-open-payable")[1][1] == "Demo Supplier"
    assert rows(pg, "acc-open-payable")[1][2:3] == ["05.02.2026"]
    assert pg.locator("#toast.on.err").count() == 0
    assert problems == []
    ctx.close()


def test_eur_year_shows_eur_and_hides_pnl(browser, eur):
    ctx, pg, problems = open_page(browser, eur, "eur")
    expect(pg.locator("[data-testid=acc-eur]")).to_be_visible()
    tabs = [t.strip() for t in pg.locator(".tabs a").all_inner_texts()]
    assert tabs == ["Journal", "Ledger", "Trial balance", "EÜR", "Open items"]  # no P&L / balance sheet
    expect(pg.locator("[data-testid=acc-eur]")).to_be_visible()
    cells = {r[0]: r[1] for r in rows(pg, "acc-eur") if len(r) == 2}
    assert cells["VAT received"] == "19,00 €" and cells["Input VAT paid"] == "5,70 €"
    # gross form: income 100.00 + 19.00 VAT = 119.00, expenses 30.00 + 5.70 = 35.70, profit 83.30
    assert cells["Total income"] == "119,00 €" and cells["Total expenses"] == "35,70 €" and cells["Profit"] == "83,30 €"
    assert cells["Revenue 19 %"] == "100,00 €" and cells["Other expenses"] == "30,00 €"
    # a P&L route of an EÜR year (API answers 422) falls back to the journal without an error
    pg.goto(eur.base + "/#/accounting/pnl")
    expect(pg.locator("[data-testid=acc-entry]").first).to_be_visible()
    assert pg.locator("#toast.on.err").count() == 0
    assert problems == []
    ctx.close()


def test_employee_has_no_accounting(browser, accrual):
    h = {"Authorization": "Bearer " + accrual.token}
    r = httpx.post(
        accrual.base + "/api/users",
        headers=h,
        json={"username": "emp", "name": "Emma", "role": "employee", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    token = httpx.post(accrual.base + "/api/auth/login", json={"username": "emp", "password": PASSWORD}).json()["token"]
    ctx, pg, problems = open_page(browser, accrual, "journal", token=token)
    pg.wait_for_selector("nav.side a.nav")
    labels = [t.strip() for t in pg.locator("nav.side a[href^='#/']").all_inner_texts()]
    assert not any("Accounting" in x for x in labels), labels
    assert "#/accounting" not in pg.url  # the page itself is refused
    assert pg.locator("[data-testid=acc-entry]").count() == 0
    ctx.close()
    # the boss does see the entry
    ctx, pg, _ = open_page(browser, accrual, "journal")
    pg.wait_for_selector("nav.side a.nav")
    assert any("Accounting" in x for x in pg.locator("nav.side a[href^='#/']").all_inner_texts())
    ctx.close()


def test_hostile_entry_text_is_rendered_as_text(browser, accrual):
    evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'
    accrual.call(
        "POST",
        "/accounting/entries",
        json={
            "date": "2026-03-01",
            "text": evil,
            "lines": [{"account": "9039", "amount": "10.00"}, {"account": "9001", "amount": "-10.00"}],
        },
    )
    ctx, pg, problems = open_page(browser, accrual, "journal")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(4)
    entry = pg.locator("[data-testid=acc-entry]").last
    assert evil in entry.locator("summary").inner_text()  # shown literally
    assert entry.locator("img, script").count() == 0
    assert "Manual" in entry.inner_text()
    assert pg.evaluate("window.__pwned") is None
    assert problems == []
    ctx.close()
