"""Custom document templates can be edited in place and deleted from the settings page (real server, demo data)."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


def test_custom_template_edit_and_delete(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    r = httpx.post(srv.base + "/api/templates", headers=h, json={"name": "Demo template", "layout": "modern"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(srv.base + "/#/settings")
    btn = pg.locator(f'button[data-a="editTemplate"][data-id="{tid}"]')
    btn.wait_for()
    # a custom template is edited in place, not copied (the settings page used to never load the custom list)
    assert btn.inner_text() == "Edit"
    assert pg.locator('button[data-a="editTemplate"][data-id="classic"]').inner_text() == "Copy & customise"

    btn.click()
    pg.fill("#f_name", "Demo template renamed")
    pg.click("#m-save")
    pg.wait_for_selector("#m-save", state="detached")
    rows = httpx.get(srv.base + "/api/templates", headers=h).json()
    assert [(t["id"], t["name"]) for t in rows] == [(tid, "Demo template renamed")]

    pg.locator(f'button[data-a="editTemplate"][data-id="{tid}"]').click()
    pg.once("dialog", lambda d: d.accept())
    pg.click("#m-del")
    pg.wait_for_selector(f'button[data-a="editTemplate"][data-id="{tid}"]', state="detached")
    assert httpx.get(srv.base + "/api/templates", headers=h).json() == []
    assert errors == []
    ctx.close()
