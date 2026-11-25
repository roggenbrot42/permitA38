"""Browser tests of the accounting set-up wizard (accounting.md 16-18, work package W8).

One folder holds one review row of every kind (the same cases as tests/backend/test_init_review.py). The clock of
the initialisation service is fixed to 2026-06-10, so January..May 2026 are the ended periods.
"""

import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from permita38.core import charts  # noqa: E402
from permita38.core.service import accounting_init  # noqa: E402
from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402,F401  (shared harness)
from tests.web.test_roles_ui import open_as  # noqa: E402

CHART = Path(__file__).parents[1] / "backend" / "fixtures" / "chart_synthetic.json"
TODAY = date(2026, 6, 10)
HOSTILE = '<img src=x onerror="window.__pwned=1">'
KEY = {
    "vat": "invoices:solldec1:vat_before_start",
    "cancelled": "invoices:cxlpaid1:legacy_cancelled_paid",
    "nodate": "invoices:paidnodt1:legacy_paid_no_date",
    "before": "invoices:paidbefr2:legacy_paid_before_invoice",
    "nosupplier": "expenses:rcnosup1:rc_without_supplier",
    "typed": "expenses:rctyped1:rc_stored_vat",
    "prepaid": "expenses:prepaid1:expense_prepaid",
}


class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


def fill_folder(store):
    cu = store.save("customers", {"name": "Demo Client GmbH", "country": "DE"})
    base = {"customer_id": cu["id"], "lines": [{"description": "Work", "qty": 1, "price": "100", "vat": "19"}]}
    store.save(
        "invoices",
        {
            "id": "solldec1",
            "date": "2026-01-05",
            "number": HOSTILE,
            "status": "sent",
            "service_to": "2025-12-20",
            **base,
        },
    )
    store.save(
        "invoices",
        {
            "id": "cxlpaid1",
            "date": "2026-02-02",
            "number": "R-2",
            "status": "cancelled",
            "paid_date": "2026-02-10",
            **base,
        },
    )
    store.save("invoices", {"id": "paidnodt1", "date": "2026-03-01", "number": "R-5", "status": "paid", **base})
    store.save(
        "invoices",
        {"id": "paidbefr2", "date": "2026-03-12", "number": "R-8", "status": "paid", "paid_date": "2026-03-11", **base},
    )
    sup = store.save("suppliers", {"name": "Demo Supplier", "kind": "goods", "vat_mode": "eu_reverse_charge"})
    rc = {"date": "2026-03-01", "net": 200.0, "vat": 0.0, "category": "software", "reverse_charge": True}
    store.save("expenses", {"id": "rcnosup1", **rc})
    store.save("expenses", {"id": "rctyped1", **rc, "vat": 5.0, "supplier_id": sup["id"]})
    store.save(
        "expenses",
        {"id": "prepaid1", "date": "2026-02-01", "net": 10.0, "vat": 1.9, "vat_rate": 19.0, "category": "software",
         "paid_on": "2025-12-30"},
    )  # fmt: skip


@pytest.fixture
def srv(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)
    monkeypatch.setattr(accounting_init, "date", FixedDate)
    s = Server(tmp_path)
    fill_folder(s.app.state.services.store)
    r = httpx.post(
        s.base + "/api/users",
        headers={"Authorization": "Bearer " + s.token},
        json={"username": "accountant", "name": "Accountant", "role": "accountant", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    yield s
    s.stop()


def status(srv):
    return httpx.get(srv.base + "/api/accounting/status", headers={"Authorization": "Bearer " + srv.token}).json()


def boss_page(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    return ctx, pg, problems


def row(pg, key):
    return pg.locator(f'[data-testid="wz-review-row"][data-key="{key}"]')


def answer(pg, key, action, **extra):
    r = row(pg, key)
    r.locator("select[id$='_act']").select_option(action)
    if "treatment" in extra:
        r.get_by_label("Tax treatment").select_option(extra["treatment"])
    if "date" in extra:
        r.locator("input[type=date]").fill(extra["date"])


def test_wizard_walks_all_steps_409_then_success(browser, srv):
    ctx, pg, problems = boss_page(browser, srv)
    pg.goto(srv.base + "/#/accounting/setup")
    expect(pg.get_by_test_id("wz")).to_have_attribute("data-step", "1")
    # step 1: bases; the start must be the first of a month
    pg.get_by_label("Income basis").select_option("accrual")
    expect(pg.get_by_text("Ist-Versteuerung needs the permission")).to_be_visible()
    pg.get_by_label("Accounting start").fill("2026-01-15")
    pg.get_by_test_id("wz-next").click()
    expect(pg.get_by_test_id("wz-err")).to_contain_text("first day of a month")
    pg.get_by_label("Accounting start").fill("2026-01-01")
    pg.get_by_test_id("wz-next").click()
    # step 2: opening balances with a live balance
    expect(pg.get_by_test_id("wz")).to_have_attribute("data-step", "2")
    pg.get_by_label("Role of line 1").select_option("bank")
    pg.get_by_label("Debit of line 1").fill("1000,50")
    pg.get_by_label("Role of line 2").select_option("equity")
    pg.get_by_label("Credit of line 2").fill("900")
    expect(pg.get_by_test_id("wz-balance")).to_have_attribute("data-state", "unbalanced")
    expect(pg.get_by_test_id("wz-balance")).to_contain_text("difference")
    pg.get_by_test_id("wz-next").click()
    expect(pg.get_by_test_id("wz-err")).to_contain_text("must balance")
    pg.get_by_label("Credit of line 2").fill("1000,50")
    expect(pg.get_by_test_id("wz-balance")).to_have_attribute("data-state", "balanced")
    pg.get_by_test_id("wz-next").click()
    # step 3: one row of each kind; hostile text is shown as text
    expect(pg.get_by_test_id("wz")).to_have_attribute("data-step", "3")
    expect(pg.get_by_test_id("wz-review-row")).to_have_count(7)
    expect(row(pg, KEY["vat"])).to_contain_text(HOSTILE)
    assert pg.evaluate("window.__pwned") is None
    assert pg.locator("#app img[src='x']").count() == 0
    expect(pg.get_by_test_id("wz-open")).to_contain_text("7 of 7")
    # a row only offers its allowed resolutions
    assert row(pg, KEY["cancelled"]).locator("select[id$='_act'] option").all_inner_texts() == [
        "– choose –", "Create the credit note and refund", "Keep as paid",
    ]  # fmt: skip
    # step 4: every ended period needs an answer
    pg.get_by_test_id("wz-next").click()
    expect(pg.get_by_test_id("wz")).to_have_attribute("data-step", "4")
    expect(pg.get_by_test_id("wz-period")).to_have_count(5)
    pg.get_by_test_id("wz-next").click()
    expect(pg.get_by_test_id("wz-err")).to_contain_text("answer")
    pg.get_by_label("Answer for 2026-01 (01.01.2026 – 31.01.2026)").select_option("filed")
    pg.get_by_label("Kz 83 of 2026-01 (01.01.2026 – 31.01.2026)").fill("123,45")
    pg.get_by_label("Filing date of 2026-01 (01.01.2026 – 31.01.2026)").fill("2026-02-10")
    for n in range(2, 6):
        pg.locator(f"[data-period='2026-month-{n}'] select").select_option("none")
    pg.get_by_test_id("wz-next").click()
    # step 5: summary; the unanswered rows are warned about, the server refuses with 409 and the wizard jumps back
    expect(pg.get_by_test_id("wz-summary")).to_contain_text("1 filed, 4 none filed")
    expect(pg.get_by_test_id("wz-open-warning")).to_contain_text("7 review row(s)")
    pg.get_by_test_id("wz-submit").click()
    expect(pg.get_by_test_id("wz")).to_have_attribute("data-step", "3")
    expect(pg.get_by_test_id("wz-unresolved")).to_contain_text(KEY["nosupplier"])
    expect(pg.get_by_test_id("wz-unresolved").locator("li")).to_have_count(7)
    expect(pg.locator('[data-testid="wz-review-row"][data-unresolved="1"]')).to_have_count(7)
    assert status(srv)["initialised"] is False
    # answer every row; one control per allowed resolution
    answer(pg, KEY["vat"], "confirm")
    answer(pg, KEY["cancelled"], "credit_note")
    answer(pg, KEY["nodate"], "date", date="2026-03-05")
    answer(pg, KEY["before"], "confirm")
    answer(pg, KEY["nosupplier"], "treatment", treatment="reverse_charge_third_country")
    answer(pg, KEY["typed"], "confirm")
    answer(pg, KEY["prepaid"], "confirm")
    expect(pg.get_by_test_id("wz-open")).to_contain_text("Every row has an answer")
    pg.get_by_test_id("wz-next").click()
    pg.get_by_test_id("wz-next").click()
    expect(pg.get_by_test_id("wz-open-warning")).to_have_count(0)
    pg.get_by_test_id("wz-submit").click()
    # success: the result and the links; the VAT and Accounting pages leave the set-up state
    expect(pg.get_by_test_id("wz-done")).to_contain_text("Accounting is set up")
    assert status(srv)["initialised"] is True
    pg.get_by_test_id("wz-go-vat").click()
    expect(pg.get_by_role("heading", name="VAT", exact=True)).to_be_visible()
    expect(pg.get_by_test_id("accounting-setup")).to_have_count(0)
    pg.goto(srv.base + "/#/accounting")
    expect(pg.get_by_role("heading", name="Accounting", exact=True)).to_be_visible()
    expect(pg.get_by_test_id("accounting-setup")).to_have_count(0)
    ctx.close()
    assert not problems, problems


def test_accountant_cannot_open_the_wizard(browser, srv):
    ctx, pg, problems = open_as(browser, srv, "accountant")
    pg.goto(srv.base + "/#/accounting/setup")
    expect(pg.get_by_test_id("accounting-setup-ask")).to_have_text("Ask the boss to set up accounting.")
    expect(pg.get_by_test_id("wz")).to_have_count(0)
    ctx.close()
    assert not problems, problems
