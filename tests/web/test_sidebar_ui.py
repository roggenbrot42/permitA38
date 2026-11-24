"""The side menu must stay reachable on a short viewport (issue #102: Sign out lay outside the viewport)."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def open_boss(browser, srv, height):
    ctx = browser.new_context(viewport={"width": 1280, "height": height})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/")
    pg.wait_for_selector("nav.side [data-a=logout]", state="attached")
    return ctx, pg


def test_side_menu_fits_short_viewport(browser, srv):
    ctx, pg = open_boss(browser, srv, 600)
    bottom = pg.evaluate("document.querySelector('nav.side').getBoundingClientRect().bottom")
    assert bottom <= 600 + 1, bottom
    ctx.close()


def test_sign_out_clickable_on_short_viewport(browser, srv):
    ctx, pg = open_boss(browser, srv, 600)
    pg.click("[data-a=logout]", timeout=3000)
    pg.wait_for_selector("#login-form", timeout=5000)
    ctx.close()
