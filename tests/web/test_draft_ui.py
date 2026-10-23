"""Browser test: a draft offer/invoice is clearly marked as a draft until it is finalized."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_draft_is_marked_everywhere_until_finalized(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/doc/invoices/new-" + cust["id"])
    pg.wait_for_selector("#d_totals")
    # unsaved: marked as draft, with a "Save draft" button and the explanation
    assert "draft" in pg.inner_text("main h1").lower()
    assert pg.inner_text("[data-a=docSave]") == "Save draft"
    assert "no number yet" in pg.inner_text(".banner.info").lower()
    pg.fill('[data-l="0"][data-k="description"]', "Work")
    pg.click("[data-a=docSave]")
    pg.wait_for_function("() => !location.hash.includes('/new')")
    pg.wait_for_selector("h1 .pill.warn")
    assert pg.inner_text("h1 .pill.warn").strip() == "draft"
    # the list shows it as a draft without a number
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click .pill.warn")
    assert "No number yet" in pg.inner_text("main")
    # finalizing removes the draft marking
    pg.click("tr.click")
    pg.wait_for_selector("[data-a=docFinalize]")
    pg.click("[data-a=docFinalize]")
    pg.wait_for_selector("[data-change=docStatus]")
    assert pg.locator(".banner.info").count() == 0
    assert pg.inner_text("[data-a=docSave]") == "Save"
    ctx.close()


def test_new_document_uses_project_then_customer_then_default_rate(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    post = lambda path, body: httpx.post(srv.base + "/api/" + path, headers=h, json=body).json()  # noqa: E731
    with_rate = post("customers", {"name": "Rate GmbH", "rate": 111})
    plain = post("customers", {"name": "Plain AG"})
    proj = post(
        "projects",
        {"name": "Special", "customer_id": with_rate["id"], "rate": 142.5, "start": "2026-01-01", "end": "2026-12-31"},
    )
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/doc/offers/new")
    pg.wait_for_selector("#d_totals")
    price = lambda: float(pg.input_value('[data-l="0"][data-k="price"]'))  # noqa: E731
    default = price()
    pg.select_option("#d_cust", with_rate["id"])
    pg.wait_for_function('(v) => +document.querySelector(\'[data-l="0"][data-k="price"]\').value === v', arg=111)
    pg.select_option("#d_proj", proj["id"])
    pg.wait_for_function('(v) => +document.querySelector(\'[data-l="0"][data-k="price"]\').value === v', arg=142.5)
    pg.select_option("#d_proj", "")
    pg.wait_for_function('(v) => +document.querySelector(\'[data-l="0"][data-k="price"]\').value === v', arg=111)
    pg.select_option("#d_cust", plain["id"])
    pg.wait_for_function('(v) => +document.querySelector(\'[data-l="0"][data-k="price"]\').value === v', arg=default)
    # a price typed by hand is never overwritten
    pg.fill('[data-l="0"][data-k="price"]', "99")
    pg.select_option("#d_cust", with_rate["id"])
    pg.wait_for_function("() => document.querySelector('#d_cust').value")
    pg.wait_for_timeout(300)   # render() is async; give an (unwanted) reprice time to happen
    assert price() == 99
    # a hand-typed price equal to the old suggestion is not overwritten either
    pg.fill('[data-l="0"][data-k="price"]', str(int(default)))
    pg.select_option("#d_cust", plain["id"])
    pg.select_option("#d_cust", with_rate["id"])
    pg.wait_for_timeout(300)
    assert price() == default
    ctx.close()
