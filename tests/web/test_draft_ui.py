"""Browser test: a draft offer/invoice is clearly marked as a draft until it is finalized."""

import time

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
    # the header is re-rendered after the save: wait for the saved state, not for the old "not saved yet" pill
    pg.wait_for_function("() => document.querySelector('h1 .pill.warn')?.textContent.trim() === 'draft'")
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
    pg.wait_for_timeout(300)  # render() is async; give an (unwanted) reprice time to happen
    assert price() == 99
    # a hand-typed price equal to the old suggestion is not overwritten either
    pg.fill('[data-l="0"][data-k="price"]', str(int(default)))
    pg.select_option("#d_cust", plain["id"])
    pg.select_option("#d_cust", with_rate["id"])
    pg.wait_for_timeout(300)
    assert price() == default
    ctx.close()


def test_typing_while_customer_reprices_is_kept(browser, srv):
    """Regression (journey seed 1337): picking a customer with its own rate reprices the line; that must not re-render
    the form asynchronously and drop what the user typed meanwhile (here the quantity)."""
    h = {"Authorization": "Bearer " + srv.token}
    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Rate GmbH", "rate": 111}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/doc/offers/new")
    pg.wait_for_selector("#d_totals")

    def slow(route):  # make any re-render's data load slow, so typing lands inside that window
        pg.wait_for_timeout(600)
        route.continue_()

    pg.route("**/api/offers", slow)
    pg.select_option("#d_cust", cust["id"])
    pg.fill('[data-l="0"][data-k="qty"]', "8")
    pg.wait_for_function('() => +document.querySelector(\'[data-l="0"][data-k="price"]\').value === 111')
    pg.wait_for_timeout(1500)
    assert pg.input_value('[data-l="0"][data-k="qty"]') == "8"
    assert "Net 888,00" in " ".join(pg.inner_text("#d_totals").split()).replace("\xa0", " ")
    ctx.close()


def test_line_vat_survives_the_first_save_with_slow_api(browser, srv):
    """After the first save the lines show the stored VAT rates, also while the API answers slowly."""
    h = {"Authorization": "Bearer " + srv.token}
    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    problems: list[str] = []

    def slow(route):
        if route.request.method == "GET" and "/api/" in route.request.url and "bootstrap" not in route.request.url:
            time.sleep(0.3)
        route.continue_()

    ctx.route("**/api/**", slow)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.goto(srv.base + "/#/doc/offers/new-" + cust["id"])
    pg.wait_for_selector("#d_totals")
    pg.fill('[data-l="0"][data-k="description"]', "Work")
    pg.fill('[data-l="0"][data-k="vat"]', "19")
    pg.click("[data-a=addLine]")
    pg.fill('[data-l="1"][data-k="description"]', "Book")
    pg.fill('[data-l="1"][data-k="vat"]', "7")
    pg.click("[data-a=docSave]")
    pg.wait_for_function("() => !location.hash.includes('/new')")
    seen: set[str] = set()
    for _ in range(12):  # sample the field while the page settles
        seen.add(pg.input_value('[data-l="1"][data-k="vat"]'))
        pg.wait_for_timeout(80)
    assert seen == {"7"}
    assert pg.input_value('[data-l="0"][data-k="vat"]') == "19"
    assert "VAT 7%" in pg.inner_text("#d_totals")
    assert problems == []
    ctx.close()


def test_draft_can_be_deleted_from_the_editor(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    cust = httpx.post(srv.base + "/api/customers", headers=h, json={"name": "Demo GmbH"}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    pg.goto(srv.base + "/#/doc/offers/new-" + cust["id"])
    pg.wait_for_selector("#d_totals")
    assert pg.locator("[data-a=docDelete]").count() == 0  # nothing to delete before the first save
    pg.fill('[data-l="0"][data-k="description"]', "Work")
    pg.click("[data-a=docSave]")
    pg.wait_for_selector("[data-a=docDelete]")
    assert len(httpx.get(srv.base + "/api/offers", headers=h).json()) == 1
    pg.click("[data-a=docDelete]")
    pg.wait_for_function("() => location.hash === '#/sales/offers'")
    assert httpx.get(srv.base + "/api/offers", headers=h).json() == []
