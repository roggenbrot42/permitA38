"""API tokens against the real server (demo data only), contract: docs/contracts/api-tokens.md."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_impressum_prefill import PASSWORD, Server  # noqa: E402  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
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
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert not pg.evaluate(
        "s => [...document.querySelectorAll('input,textarea')].some(i => i.value === s)"
        " || [...document.querySelectorAll('*')].some(e => (e.textContent || '').includes(s))",
        secret,
    )
    expect(pg.locator("tr", has_text="Live test")).to_contain_text("Read only")
    # the token works for reading, not for writing
    h = {"Authorization": "Bearer " + secret}
    assert httpx.get(srv.base + "/api/customers", headers=h).status_code == 200
    assert httpx.post(srv.base + "/api/customers", headers=h, json={"name": "X"}).status_code == 403
    pg.click("[data-a=tokAudit]")
    expect(pg.locator("[data-testid=audit-table]")).to_contain_text("/api/customers")
    # the activity rows: the GET above (and the refused POST) are logged with the route template
    expect(pg.locator("[data-testid=audit-table]")).to_contain_text("403")
    pg.click("#modal-root [data-a=tokCloseDialog]")
    me = httpx.get(srv.base + "/api/auth/me", headers=h).json()
    assert me["auth"]["kind"] == "token" and me["auth"]["read_only"] is True
    refused = httpx.post(srv.base + "/api/tokens", headers=h, json={"label": "x"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "This route cannot be used with an API token"
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


def test_token_user_cannot_reach_export_routes(srv):
    """Export routes (if the server has any) must not give a token more than its owner's session gets."""
    sess = {"Authorization": "Bearer " + srv.token}
    spec = httpx.get(srv.base + "/api/openapi.json", headers=sess).json()
    paths = [p for p in spec["paths"] if "export" in p and "{" not in p]
    if not paths:
        pytest.skip("the server has no export routes")
    created = httpx.post(srv.base + "/api/tokens", headers=sess, json={"label": "export probe"}).json()
    h = {"Authorization": "Bearer " + created["token"]}
    for path in paths:
        via_session = httpx.get(srv.base + path, headers=sess).status_code
        via_token = httpx.get(srv.base + path, headers=h).status_code
        assert via_token in (401, 403, 404, 405, 422) or via_token == via_session, (path, via_token)
