"""Accounting page, part B (manual entries, reversal, period locks, fiscal years and closing, DATEV export) against the
real server. Contract: docs/specs/accounting.md 4, 9.8, 11, 13, 17, 18; datev-export.md "API and UI". Fake demo data only.

Scenario (see test_accounting_page.py): accrual year 2026 with three automatic entries (journal entries #1..#3).
Hand-computed: a private withdrawal of 250,00 books D private_withdrawal 9047 / C bank 9001 (bank 50,00 -> -200,00).
"""

import io
import zipfile
from datetime import date

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_accounting_page import (  # noqa: E402,F401  (shared scenario and fixtures)
    accrual,
    chart_dir,
    eur,
    open_page,
    rows,
)
from tests.web.test_impressum_prefill import PASSWORD  # noqa: E402


def api_call(srv, method, path, token=None, ok=(200, 201), **kw):
    r = httpx.request(
        method, srv.base + "/api" + path, headers={"Authorization": "Bearer " + (token or srv.token)}, **kw
    )
    assert r.status_code in ok, (path, r.status_code, r.text)
    return r


def journal(srv):
    return api_call(srv, "GET", "/accounting/journal").json()


def accountant_token(srv):
    api_call(
        srv, "POST", "/users", json={"username": "acct", "name": "Anna", "role": "accountant", "password": PASSWORD}
    )
    return httpx.post(srv.base + "/api/auth/login", json={"username": "acct", "password": PASSWORD}).json()["token"]


def pick_account(pg, i, number):
    pg.select_option(f"#acc_l{i}_acct", number)


def test_template_entry_free_entry_and_client_side_refusal(browser, accrual):
    ctx, pg, problems = open_page(browser, accrual, "entry")
    expect(pg.locator("#acc_tpl")).to_be_visible()
    assert "Private withdrawal" in pg.locator("#acc_tpl").inner_text()
    # template: private withdrawal prefills the roles, the typed amount lands on both sides
    pg.select_option("#acc_tpl", "private_withdrawal")
    assert pg.input_value("#acc_l0_acct") == "9047" and pg.input_value("#acc_l1_acct") == "9001"
    pg.fill("#acc_amount", "250,00")
    assert pg.input_value("#acc_l0_d") == "250.00" and pg.input_value("#acc_l1_c") == "250.00"
    assert pg.get_attribute("#acc-balance", "data-state") == "balanced"
    pg.fill("#acc_etext", "Private withdrawal demo")
    pg.fill("#acc_edate", "2026-03-05")
    pg.click("[data-testid=acc-entry-submit]")
    pg.wait_for_selector("#toast.on")
    entries = journal(accrual)
    assert len(entries) == 4
    e = next(x for x in entries if x["text"] == "Private withdrawal demo")
    assert e["origin"] == "manual"
    amounts = {(ln["account"], ln["amount"]) for ln in e["lines"]}
    assert amounts == {("9047", "250.00"), ("9001", "-250.00")}

    # free entry on two accounts
    pg.goto(accrual.base + "/#/accounting/entry")
    expect(pg.locator("#acc_tpl")).to_be_visible()
    pick_account(pg, 0, "9039")
    pick_account(pg, 1, "9001")
    pg.fill("#acc_l0_d", "12,50")
    pg.fill("#acc_l1_c", "12,50")
    pg.fill("#acc_etext", "Free entry demo")
    pg.fill("#acc_edate", "2026-03-06")
    pg.click("[data-testid=acc-entry-submit]")
    pg.wait_for_selector("#toast.on")
    assert len(journal(accrual)) == 5

    # unbalanced: refused in the browser, nothing is sent
    pg.goto(accrual.base + "/#/accounting/entry")
    expect(pg.locator("#acc_tpl")).to_be_visible()
    pick_account(pg, 0, "9039")
    pick_account(pg, 1, "9001")
    pg.fill("#acc_l0_d", "10,00")
    pg.fill("#acc_l1_c", "9,99")
    pg.fill("#acc_etext", "Unbalanced")
    assert pg.get_attribute("#acc-balance", "data-state") == "unbalanced"
    assert "difference 0,01" in pg.inner_text("#acc-balance").replace(" ", " ")
    pg.click("[data-testid=acc-entry-submit]")
    expect(pg.locator("#acc-entry-err")).to_contain_text("not balanced")
    assert len(journal(accrual)) == 5
    # a malformed amount is refused by Money.parseInput
    pg.fill("#acc_l1_c", "1.234,56")
    pg.click("[data-testid=acc-entry-submit]")
    expect(pg.locator("#acc-entry-err")).to_contain_text("decimal separator")
    assert len(journal(accrual)) == 5

    # the server refuses the same unbalanced body too (422), the page shows the server message inline
    status = pg.evaluate(
        """async () => { try { await api('POST', 'accounting/entries', {date: '2026-03-07', text: 'x',
        lines: [{account: '9039', amount: '10.00'}, {account: '9001', amount: '-9.99'}]}); return 0; }
        catch (e) { return e.status; } }"""
    )
    assert status == 422
    assert len(journal(accrual)) == 5
    assert problems == []
    ctx.close()


def test_server_refusal_is_shown_inline(browser, accrual):
    api_call(accrual, "POST", "/accounting/locks", json={"year": 2026, "month": 2})
    ctx, pg, problems = open_page(browser, accrual, "entry")
    expect(pg.locator("#acc_tpl")).to_be_visible()
    pick_account(pg, 0, "9039")
    pick_account(pg, 1, "9001")
    pg.fill("#acc_l0_d", "5")
    pg.fill("#acc_l1_c", "5")
    pg.select_option("#acc_l0_tk", "VST")
    pg.fill("#acc_l0_vdate", "2026-01-15")  # a locked month: manual entries never drift
    pg.fill("#acc_etext", "Late input VAT")
    pg.fill("#acc_edate", "2026-04-01")
    pg.click("[data-testid=acc-entry-submit]")
    expect(pg.locator("#acc-entry-err")).not_to_be_empty()
    assert len(journal(accrual)) == 3
    assert pg.locator("#acc-entry-err").inner_text().strip() != ""
    assert problems == []
    ctx.close()


def test_reverse_a_manual_entry(browser, accrual):
    api_call(
        accrual,
        "POST",
        "/accounting/entries",
        json={
            "date": "2026-03-01",
            "text": "Entry to reverse",
            "lines": [{"account": "9039", "amount": "10.00"}, {"account": "9001", "amount": "-10.00"}],
        },
    )
    ctx, pg, problems = open_page(browser, accrual, "journal")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(4)
    # only manual entries offer the action
    assert pg.locator("[data-testid=acc-reverse]").count() == 1
    entry = pg.locator("[data-testid=acc-entry]").last
    entry.locator("summary").click()
    entry.locator("[data-testid=acc-reverse]").click()
    expect(pg.locator("#modal-root")).to_contain_text("Reverse entry #4")
    pg.fill("#acc_rtext", "Reversal demo")
    pg.click("[data-testid=acc-reverse-confirm]")
    pg.wait_for_selector("#toast.on")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(5)
    rev = journal(accrual)[-1] if journal(accrual)[-1]["reverses_id"] else None
    entries = journal(accrual)
    rev = next(x for x in entries if x.get("reverses_id"))
    assert rev["text"] == "Reversal demo"
    assert {(ln["account"], ln["amount"]) for ln in rev["lines"]} == {("9039", "-10.00"), ("9001", "10.00")}
    assert "Reversal" in pg.locator("[data-testid=acc-entry]").last.inner_text()
    # the reversal itself offers no further reversal
    assert pg.locator("[data-testid=acc-reverse]").count() == 1
    assert problems == []
    ctx.close()


def test_lock_up_to_a_month_with_gap_confirmation_and_no_unlock(browser, accrual):
    api_call(accrual, "POST", "/accounting/locks", json={"year": 2026, "month": 1})
    ctx, pg, problems = open_page(browser, accrual, "periods")
    expect(pg.locator("[data-testid=acc-locks]")).to_be_visible()
    assert [r[0] for r in rows(pg, "acc-locks")[1:]] == ["2026-01"]
    assert "Locked manually" in pg.locator("[data-testid=acc-locks]").inner_text()
    assert pg.locator("button:has-text('Unlock')").count() == 0
    # lock up to April: Feb, Mar, Apr are locked together and the gap is shown before confirming
    pg.fill("#acc_lock_month", "2026-04")
    pg.click("[data-testid=acc-lock-go]")
    expect(pg.locator("[data-testid=acc-lock-gap]")).to_contain_text("3 months")
    expect(pg.locator("[data-testid=acc-lock-gap]")).to_contain_text("2026-02, 2026-03, 2026-04")
    assert len(api_call(accrual, "GET", "/accounting/locks").json()) == 1  # nothing before the confirmation
    pg.click("[data-testid=acc-lock-confirm]")
    pg.wait_for_selector("#toast.on")
    assert [r[0] for r in rows(pg, "acc-locks")[1:]] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert len(api_call(accrual, "GET", "/accounting/locks").json()) == 4
    # a month that is already locked is refused in the page
    pg.fill("#acc_lock_month", "2026-03")
    pg.click("[data-testid=acc-lock-go]")
    expect(pg.locator("#toast.on.err")).to_contain_text("already locked")
    assert problems == []
    ctx.close()


def test_accountant_cannot_close_a_year_boss_can(browser, accrual):
    token = accountant_token(accrual)
    ctx, pg, problems = open_page(browser, accrual, "years", token=token)
    expect(pg.locator("[data-testid=acc-years]")).to_be_visible()
    assert rows(pg, "acc-years")[1][:3] == ["2026", "skr03", "Accrual basis"]
    assert pg.locator("[data-testid=acc-close-2026]").count() == 0
    assert "Only the boss" in pg.inner_text("#main")
    # but the accountant does see the other tabs and may post entries
    assert pg.locator("[data-testid=acc-tab-entry]").count() == 1
    ctx.close()
    ctx, pg, _ = open_page(browser, accrual, "years")
    expect(pg.locator("[data-testid=acc-close-2026]")).to_be_visible()
    ctx.close()


def test_boss_ends_an_eur_year_with_locked_months(browser, eur):
    eur.app.state.services.locks.today = lambda: date(2027, 1, 15)  # 2026 is over (the service clock is injectable)
    # blockers first: the months before December are not locked
    ctx, pg, problems = open_page(browser, eur, "years")
    expect(pg.locator("[data-testid=acc-close-2026]")).to_have_text("End year")
    pg.click("[data-testid=acc-close-2026]")
    expect(pg.locator("#modal-root")).to_contain_text("This is final")
    pg.click("[data-testid=acc-close-confirm]")
    expect(pg.locator("[data-testid=acc-close-blockers]")).not_to_be_empty()  # the server's blockers, inline
    blockers = pg.locator("[data-testid=acc-close-blockers]").inner_text()
    assert "lock" in blockers.lower() or "month" in blockers.lower(), blockers
    assert api_call(eur, "GET", "/accounting/years/2026").json()["closed_at"] is None
    pg.click("#modal-root [data-a=closeModal]")
    # lock January to November on the periods tab, then end the year
    pg.click("[data-testid=acc-tab-periods]")
    pg.fill("#acc_lock_month", "2026-11")
    pg.click("[data-testid=acc-lock-go]")
    expect(pg.locator("[data-testid=acc-lock-gap]")).to_contain_text("11 months")
    pg.click("[data-testid=acc-lock-confirm]")
    pg.wait_for_selector("#toast.on")
    pg.click("[data-testid=acc-tab-years]")
    pg.click("[data-testid=acc-close-2026]")
    pg.click("[data-testid=acc-close-confirm]")
    pg.wait_for_selector("#toast.on")
    assert api_call(eur, "GET", "/accounting/years/2026").json()["closed_at"] is not None
    expect(pg.locator("[data-testid=acc-years]")).to_contain_text("Ended")
    assert pg.locator("[data-testid=acc-close-2026]").count() == 0
    assert problems == []
    ctx.close()


def test_accrual_close_dialog_says_there_is_no_preview(browser, accrual):
    ctx, pg, problems = open_page(browser, accrual, "years")
    pg.click("[data-testid=acc-close-2026]")
    expect(pg.locator("[data-testid=acc-close-note]")).to_contain_text("no preview")
    pg.fill("#acc_prov_trade", "1.234,56")
    pg.click("[data-testid=acc-close-confirm]")
    expect(pg.locator("[data-testid=acc-close-blockers]")).to_contain_text("decimal separator")
    pg.fill("#acc_prov_trade", "")
    pg.click("[data-testid=acc-close-confirm]")
    expect(pg.locator("[data-testid=acc-close-blockers]")).not_to_be_empty()  # months not locked: the server's blockers
    assert api_call(accrual, "GET", "/accounting/years/2026").json()["closed_at"] is None
    assert problems == []
    ctx.close()


def test_datev_export_needs_settings_then_downloads(browser, accrual):
    ctx, pg, problems = open_page(browser, accrual, "datev")
    expect(pg.locator("#acc_dx_from")).to_have_value("2026-01-01")
    # outside one fiscal year: refused in the page
    pg.fill("#acc_dx_to", "2027-01-31")
    pg.click("[data-testid=acc-datev-go]")
    expect(pg.locator("[data-testid=acc-datev-msg]")).to_contain_text("same year")
    pg.fill("#acc_dx_to", "2026-03-31")
    pg.click("[data-testid=acc-datev-go]")
    msg = pg.locator("[data-testid=acc-datev-msg]")
    expect(msg).to_contain_text("consultant and client number")
    expect(msg).to_contain_text("datev_consultant_no")
    link = pg.locator("[data-testid=acc-datev-settings-link]")
    assert link.get_attribute("href") == "#/settings"
    # set the numbers, then the export downloads a zip with the report
    api_call(accrual, "PUT", "/settings", json={"datev_consultant_no": 1001, "datev_client_no": 12345})
    with pg.expect_download() as dl:
        pg.click("[data-testid=acc-datev-go]")
    download = dl.value
    assert download.suggested_filename == "datev-export_2026-01-01_2026-03-31.zip"
    path = download.path()
    with zipfile.ZipFile(io.BytesIO(open(path, "rb").read())) as z:
        names = z.namelist()
    assert any(n.endswith(".csv") for n in names), names
    expect(msg).to_contain_text("Downloaded")
    assert problems == []
    ctx.close()


def test_hostile_entry_text_is_rendered_as_text_in_the_form_flow(browser, accrual):
    evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'
    ctx, pg, problems = open_page(browser, accrual, "entry")
    expect(pg.locator("#acc_tpl")).to_be_visible()
    pick_account(pg, 0, "9039")
    pick_account(pg, 1, "9001")
    pg.fill("#acc_l0_d", "1")
    pg.fill("#acc_l1_c", "1")
    pg.fill("#acc_etext", evil)
    pg.fill("#acc_edate", "2026-03-02")
    pg.click("[data-testid=acc-entry-submit]")
    pg.wait_for_selector("#toast.on")
    pg.goto(accrual.base + "/#/accounting/journal")
    expect(pg.locator("[data-testid=acc-entry]")).to_have_count(4)
    entry = pg.locator("[data-testid=acc-entry]").last
    assert evil in entry.locator("summary").inner_text()
    assert entry.locator("img, script").count() == 0
    # the reverse dialog and the entry form never interpret the text either
    entry.locator("summary").click()
    entry.locator("[data-testid=acc-reverse]").click()
    assert pg.locator("#modal-root img, #modal-root script").count() == 0
    assert pg.evaluate("window.__pwned") is None
    assert problems == []
    ctx.close()


def test_employee_has_no_part_b_tabs(browser, accrual):
    api_call(
        accrual, "POST", "/users", json={"username": "emp", "name": "Emma", "role": "employee", "password": PASSWORD}
    )
    token = httpx.post(accrual.base + "/api/auth/login", json={"username": "emp", "password": PASSWORD}).json()["token"]
    ctx, pg, problems = open_page(browser, accrual, "entry", token=token)
    pg.wait_for_selector("nav.side a.nav")
    assert pg.locator("#acc_tpl").count() == 0
    ctx.close()
