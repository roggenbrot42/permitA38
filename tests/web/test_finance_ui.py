"""The Money page says its figures are approximate until accounting is set up (accounting.md 14, last paragraph)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import PASSWORD, Server, chromium  # noqa: E402  (shared harness)

_FIXTURES = (chromium,)  # the pytest fixture is registered by importing it here; referenced so the import is used


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_money_page_notes_approximate_figures(browser, srv):
    token = httpx.post(srv.base + "/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
    f = httpx.get(srv.base + "/api/finance", headers={"Authorization": "Bearer " + token}).json()
    assert f["approximate"] is True  # accounting is not set up on a fresh server
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/money")
    pg.wait_for_selector("h1:has-text('Money')")
    assert "approximate until accounting is set up" in pg.inner_text("#approximate-note")
    ctx.close()
