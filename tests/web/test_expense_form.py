"""Browser test of the expense dialog: the 'Business meals' category exists and proposes 70 % deductible."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_business_meals_category_is_selectable_and_proposes_70_percent(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/expenses")
    pg.wait_for_selector("[data-a=editExpense]")
    pg.click("[data-a=editExpense]")
    pg.wait_for_selector("#f_category")
    assert "Business meals" in pg.locator("#f_category option").all_inner_texts()
    assert pg.input_value("#f_deductible") == "100"
    pg.select_option("#f_category", "Business meals")
    assert pg.input_value("#f_deductible") == "70"
    pg.select_option("#f_category", "Travel")  # leaving the category restores the default
    assert pg.input_value("#f_deductible") == "100"
    pg.select_option("#f_category", "Business meals")
    pg.fill("#f_deductible", "50")  # a hand-typed share is never overwritten
    pg.select_option("#f_category", "Travel")
    assert pg.input_value("#f_deductible") == "50"
    # save a meal: category and 70 % are stored
    pg.select_option("#f_category", "Business meals")
    pg.fill("#f_deductible", "70")
    pg.fill("#f_vendor", "Restaurant Beispiel")
    pg.fill("#f_gross", "100")
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    import httpx

    got = httpx.get(srv.base + "/api/expenses", headers={"Authorization": "Bearer " + srv.token}).json()
    assert [(x["category"], x["deductible"], x["vat_deductible"]) for x in got] == [("Business meals", 70, 100)]
    ctx.close()
