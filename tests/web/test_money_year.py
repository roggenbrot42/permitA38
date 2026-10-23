"""The Money page clamps a typed or stale year to the 2000..2100 range the API accepts (no validation error page)."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server  # noqa: E402  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.mark.parametrize("route,shown", [("1999", "2000"), ("0", None), ("2101", "2100"), ("abc", None)])
def test_money_year_in_the_route_is_clamped(browser, srv, route, shown):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + f"/#/money/{route}")
    pg.wait_for_selector("h1:has-text('Money')")
    assert "Something went wrong" not in pg.inner_text("main")
    year = pg.inner_text(".head b").strip()
    assert year == shown if shown else 2000 <= int(year) <= 2100
    # the arrows stop at the ends of the range
    if shown == "2000":
        assert pg.locator("[data-a=goto][data-to='money/1999']").is_disabled()
    if shown == "2100":
        assert pg.locator("[data-a=goto][data-to='money/2101']").is_disabled()
    ctx.close()
