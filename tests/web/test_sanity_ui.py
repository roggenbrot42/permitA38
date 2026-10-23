"""Browser test: advisory warnings appear as a banner after saving, and the record is saved anyway."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def ui(browser, srv):
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.on(
        "console", lambda m: m.type == "error" and "status of" not in m.text and problems.append("console: " + m.text)
    )
    yield pg
    assert problems == []
    ctx.close()


def banner_codes(pg):
    pg.wait_for_selector("#plnWarn li")
    return sorted(pg.locator("#plnWarn li").evaluate_all("els => els.map(e => e.dataset.code)"))


def test_invalid_vat_id_warns_in_a_banner_and_the_customer_is_saved(srv, ui):
    ui.goto(srv.base + "/#/customers")
    ui.click("[data-a=editCustomer]")
    ui.fill("#f_name", "Demo GmbH")
    ui.fill("#f_vat_id", "DE136695977")  # wrong check digit
    ui.click("#m-save")
    assert banner_codes(ui) == ["customer.vat_id_invalid"]
    assert "saved" in ui.inner_text("#plnWarn")
    customers = httpx.get(srv.base + "/api/customers", headers={"Authorization": "Bearer " + srv.token}).json()
    assert [c["name"] for c in customers] == ["Demo GmbH"]  # saved despite the warning
    ui.click("#plnWarn [data-a=plnDismiss]")
    assert ui.locator("#plnWarn").count() == 0


def test_long_day_without_break_warns_on_the_timesheet(srv, ui):
    ui.goto(srv.base + "/#/today")
    ui.click("[data-a=addTime]")
    ui.fill("#f_start", "06:00")
    ui.fill("#f_end", "17:00")
    ui.click("#m-save")
    assert banner_codes(ui) == ["time.break_missing", "time.day_over_10h"]
    # a clean entry on another day shows no warnings
    ui.click("#plnWarn [data-a=plnDismiss]")
    ui.click("[data-a=addTime]")
    ui.fill("#f_date", "2026-01-05")
    ui.fill("#f_start", "09:00")
    ui.fill("#f_end", "10:00")
    ui.click("#m-save")
    ui.wait_for_selector("text=Saved")
    assert ui.locator("#plnWarn").count() == 0
