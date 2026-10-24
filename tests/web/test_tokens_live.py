"""API tokens against the real server (demo data only). Skipped until the backend routes of
docs/contracts/api-tokens.md exist."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    r = httpx.get(s.base + "/api/tokens", headers={"Authorization": "Bearer " + s.token})
    if r.status_code in (404, 405):
        s.stop()
        pytest.skip("backend has no /api/tokens route yet (feat/api-tokens-backend not merged)")
    yield s
    s.stop()


def open_as_boss(browser, srv):
    token = srv.token
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    return ctx, pg, problems


def test_create_use_activity_and_revoke(browser, srv):
    ctx, pg, problems = open_as_boss(browser, srv)
    pg.goto(srv.base + "/#/tokens")
    expect(pg.locator("#tokens-card")).to_contain_text("No API tokens yet")
    pg.click("[data-a=tokNew]")
    pg.fill("#tk_label", "Live test")
    pg.click("#tk_submit")
    secret = pg.locator("[data-testid=token-secret]").input_value()
    assert secret.startswith("pat_")
    pg.check("#tk_saved")
    pg.click("#tk_close")
    expect(pg.locator("tr", has_text="Live test")).to_contain_text("Read only")
    assert secret not in pg.content()
    # the token works for reading, not for writing
    h = {"Authorization": "Bearer " + secret}
    assert httpx.get(srv.base + "/api/customers", headers=h).status_code == 200
    assert httpx.post(srv.base + "/api/customers", headers=h, json={"name": "X"}).status_code == 403
    pg.click("[data-a=tokAudit]")
    expect(pg.locator("[data-testid=audit-table]")).to_contain_text("/api/customers")
    pg.click("#modal-root [data-a=tokCloseDialog]")
    pg.click("[data-a=tokRevoke]")
    expect(pg.locator("tr", has_text="Live test")).to_contain_text("Revoked")
    assert httpx.get(srv.base + "/api/customers", headers=h).status_code == 401
    assert problems == []
    ctx.close()


def test_employee_sees_only_own_tokens_page(browser, srv):
    h = {"Authorization": "Bearer " + srv.token}
    r = httpx.post(
        srv.base + "/api/users",
        headers=h,
        json={"username": "employee", "name": "Employee", "role": "employee", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    login = httpx.post(srv.base + "/api/auth/login", json={"username": "employee", "password": PASSWORD})
    token = login.json()["token"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{token}')")
    ctx.add_cookies([{"name": "authorization", "value": token, "url": srv.base}])
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/tokens")
    expect(pg.locator("#tokens-card")).to_contain_text("No API tokens yet")
    expect(pg.locator("[data-change=tokShowAll]")).to_have_count(0)
    ctx.close()
