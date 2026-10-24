"""Browser tests of the API tokens card. The API is intercepted with the browser context's routes; the token fixtures
are the examples of docs/contracts/api-tokens.md (fake secrets only), so these tests run without the backend."""

import copy
import json
import mimetypes
import pathlib
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"

SECRET = "pat_3q7mXc0Qd1pZk9Yw2eLr8VbT5uHnA4sJf6GoKiDxE1c"
ITEM = {
    "id": "c0ffee01",
    "user_id": "u1a2b3c4d5",
    "label": "Backup script",
    "hint": "pat_3q7m",
    "read_only": True,
    "created": 1766000000,
    "expires_at": 1768592000,
    "last_used_at": 1766400000,
    "revoked_at": None,
    "status": "active",
}
AUDIT_ROW = {
    "id": "a1b2c3d4",
    "at": 1766400000,
    "token_id": "c0ffee01",
    "user_id": "u1a2b3c4d5",
    "method": "GET",
    "route": "/api/customers",
    "status": 200,
}
BOSS = {"id": "u1a2b3c4d5", "name": "Demo Boss", "role": "boss", "permissions": {}}
EMPLOYEE = {"id": "u2b3c4d5e6", "name": "Demo Employee", "role": "employee", "permissions": {}}


def _json(route, status, payload, headers=None):
    return route.fulfill(status=status, content_type="application/json", body=json.dumps(payload), headers=headers)


def _static(route):
    path = urlparse(route.request.url).path
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    f = WEB / name
    if not f.is_file():
        return route.fulfill(status=404, body="not found")
    return route.fulfill(
        status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes()
    )


class FakeTokenApi:
    """The token routes of the contract, in memory, with a log of every request."""

    def __init__(self, me, items=None):
        self.me = me
        self.items = items if items is not None else []
        self.requests = []
        self.create_error = None  # (status, payload, headers) answered by POST /tokens
        self.audit_pages = [{"items": [AUDIT_ROW], "next": None}]
        self.confirms = []

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        query = parse_qs(url.query)
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, query, body))
        if path == "bootstrap":
            return _json(
                route,
                200,
                {
                    "me": self.me,
                    "settings": {},
                    "timer": None,
                    "states": {},
                    "conflicts": [],
                    "templates": [],
                    "data_dir": "",
                    "features": {},
                },
            )
        if path == "tokens" and req.method == "GET":
            return _json(route, 200, copy.deepcopy(self.items))
        if path == "tokens" and req.method == "POST":
            if self.create_error:
                status, payload, headers = self.create_error
                return _json(route, status, payload, headers)
            item = {**ITEM, "label": body["label"], "read_only": body["read_only"], "id": "c0ffee02"}
            self.items.insert(0, item)
            return _json(route, 201, {"token": SECRET, "item": item})
        if path == "tokens/revoke-all":
            n = 0
            for t in self.items:
                if t["status"] == "active":
                    t["status"], n = "revoked", n + 1
            return _json(route, 200, {"revoked": n})
        if path.startswith("tokens/") and path.endswith("/revoke"):
            for t in self.items:
                if t["id"] == path.split("/")[1]:
                    t["status"] = "revoked"
                    return _json(route, 200, t)
            return _json(route, 404, {"detail": "Token not found"})
        if path.startswith("tokens/") and path.endswith("/audit"):
            page = 1 if "before" in query else 0
            return _json(route, 200, self.audit_pages[page])
        if path in ("settings", "auth/status"):
            return _json(route, 200, {})
        return _json(route, 200, [])


def open_app(browser, api, page="tokens"):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    # the fake origin is not a secure context: record what the page copies instead of using the real clipboard
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async t => { window.__copied = t; }}})"
    )
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))

    def on_dialog(d):
        api.confirms.append(d.message)
        d.accept()

    pg.on("dialog", on_dialog)
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems


def test_empty_state_and_nav_for_employee(browser):
    api = FakeTokenApi(EMPLOYEE)
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("nav.side")).to_contain_text("API tokens")
    expect(pg.locator("#tokens-card")).to_contain_text("No API tokens yet")
    expect(pg.locator("[data-change=tokShowAll]")).to_have_count(0)  # boss only
    assert problems == []
    ctx.close()


def test_list_columns_and_statuses(browser):
    items = [
        ITEM,
        {**ITEM, "id": "c0ffee03", "label": "Old", "status": "expired", "last_used_at": None, "read_only": False},
        {**ITEM, "id": "c0ffee04", "label": "Gone", "status": "revoked", "revoked_at": 1766500000},
    ]
    api = FakeTokenApi(BOSS, items)
    ctx, pg, problems = open_app(browser, api)
    table = pg.locator("[data-testid=tokens-table]")
    row = table.locator("tr", has_text="Backup script")
    expect(row).to_contain_text("pat_3q7m")
    expect(row).to_contain_text("Read only")
    expect(row).to_contain_text("Active")
    expect(row.locator("[data-a=tokRevoke]")).to_have_count(1)
    old = table.locator("tr", has_text="Old")
    expect(old).to_contain_text("Read and write")
    expect(old).to_contain_text("never")
    expect(old).to_contain_text("Expired")
    expect(old.locator("[data-a=tokRevoke]")).to_have_count(0)
    gone = table.locator("tr", has_text="Gone")
    expect(gone).to_contain_text("Revoked")
    expect(gone.locator("[data-a=tokRevoke]")).to_have_count(0)
    assert SECRET not in pg.content()
    assert problems == []
    ctx.close()


def test_create_shows_secret_once_and_removes_it_on_close(browser):
    api = FakeTokenApi(BOSS)
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=tokNew]")
    expect(pg.locator("#tk_days")).to_have_value("90")
    expect(pg.locator("#tk_ro")).to_be_checked()
    expect(pg.locator("#tk_warn")).to_have_text("")
    pg.uncheck("#tk_ro")
    expect(pg.locator("#tk_warn")).to_have_text("This token can change data.")
    pg.check("#tk_ro")
    pg.fill("#tk_label", "Backup script")
    pg.select_option("#tk_days", "30")
    pg.click("#tk_submit")
    secret = pg.locator("[data-testid=token-secret]")
    expect(secret).to_have_value(SECRET)
    expect(pg.locator("#modal-root")).to_contain_text("This is the only time the token is shown")
    post = api.calls("POST", "tokens")[0][3]
    assert post == {"label": "Backup script", "expires_in_days": 30, "read_only": True}
    # the list behind the dialog shows the hint only
    expect(pg.locator("#tokens-card")).to_contain_text("pat_3q7m")
    assert SECRET not in pg.locator("#tokens-card").inner_html()
    # Close needs the acknowledgement
    expect(pg.locator("#tk_close")).to_be_disabled()
    pg.click("[data-a=tokCopy]")
    expect(pg.locator("#tk_copied")).to_have_text("Copied")
    assert pg.evaluate("window.__copied") == SECRET
    pg.check("#tk_saved")
    pg.click("#tk_close")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert SECRET not in pg.content()
    assert SECRET not in pg.url
    stored = pg.evaluate("JSON.stringify([localStorage, sessionStorage])")
    assert SECRET not in stored
    assert problems == []
    ctx.close()


def test_new_token_disabled_at_twenty_active(browser):
    items = [{**ITEM, "id": f"c0ffee{i:02d}", "user_id": BOSS["id"]} for i in range(20)]
    api = FakeTokenApi(BOSS, items)
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("[data-a=tokNew]")).to_be_disabled()
    expect(pg.locator("#tok_full")).to_contain_text("You already have 20 active tokens")
    assert problems == []
    ctx.close()


def test_create_errors_409_422_and_429(browser):
    api = FakeTokenApi(BOSS)
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=tokNew]")
    pg.fill("#tk_label", "x")
    api.create_error = (409, {"detail": "Too many active tokens (maximum 20)"}, None)
    pg.click("#tk_submit")
    expect(pg.locator("#tk_err")).to_have_text("Too many active tokens (maximum 20)")
    api.create_error = (
        422,
        {"detail": [{"loc": ["body", "label"], "msg": "String should have at most 80 characters", "type": "x"}]},
        None,
    )
    pg.click("#tk_submit")
    expect(pg.locator("#tk_err")).to_contain_text("label: String should have at most 80 characters")
    api.create_error = (
        429,
        {"detail": "Too many failed attempts. Please wait a few minutes and try again."},
        {"Retry-After": "120"},
    )
    pg.click("#tk_submit")
    expect(pg.locator("#tk_err")).to_contain_text("Too many failed attempts")
    expect(pg.locator("#tk_err")).to_contain_text("120 seconds")
    api.create_error = (403, {"detail": "This route cannot be used with an API token"}, None)
    pg.click("#tk_submit")
    expect(pg.locator("#tk_err")).to_have_text("This route cannot be used with an API token")
    expect(pg.locator("[data-testid=token-secret]")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_label_is_required_before_any_request(browser):
    api = FakeTokenApi(BOSS)
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=tokNew]")
    pg.click("#tk_submit")
    expect(pg.locator("#tk_err")).to_have_text("Enter a label")
    assert api.calls("POST", "tokens") == []
    ctx.close()


def test_revoke_and_revoke_all_ask_first(browser):
    api = FakeTokenApi(BOSS, [copy.deepcopy(ITEM), {**ITEM, "id": "c0ffee05", "label": "Second"}])
    ctx, pg, problems = open_app(browser, api)
    pg.locator("tr", has_text="Backup script").locator("[data-a=tokRevoke]").click()
    expect(pg.locator("tr", has_text="Backup script")).to_contain_text("Revoked")
    assert "Backup script" in api.confirms[0]
    assert len(api.calls("POST", "tokens/c0ffee01/revoke")) == 1
    pg.click("[data-a=tokRevokeAll]")
    expect(pg.locator("tr", has_text="Second")).to_contain_text("Revoked")
    assert len(api.confirms) == 2
    assert problems == []
    ctx.close()


def test_activity_dialog_pages_with_next(browser):
    api = FakeTokenApi(BOSS, [copy.deepcopy(ITEM)])
    api.audit_pages = [
        {"items": [AUDIT_ROW], "next": 1766399990},
        {"items": [{**AUDIT_ROW, "id": "a1b2c3d5", "at": 1766399990, "method": "POST", "status": 403}], "next": None},
    ]
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=tokAudit]")
    table = pg.locator("[data-testid=audit-table]")
    expect(table.locator("tbody tr")).to_have_count(1)
    expect(table).to_contain_text("/api/customers")
    pg.click("[data-a=tokAuditMore]")
    expect(table.locator("tbody tr")).to_have_count(2)
    expect(table).to_contain_text("403")
    expect(pg.locator("[data-a=tokAuditMore]")).to_have_count(0)
    assert api.calls("GET", "tokens/c0ffee01/audit")[1][2]["before"] == ["1766399990"]
    assert problems == []
    ctx.close()


def test_boss_show_all_adds_owner_column(browser):
    api = FakeTokenApi(BOSS, [{**ITEM, "owner_name": "Demo Employee", "user_id": EMPLOYEE["id"]}])
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("[data-testid=tokens-table]")).not_to_contain_text("Demo Employee")
    pg.check("[data-change=tokShowAll]")
    expect(pg.locator("[data-testid=tokens-table]")).to_contain_text("Demo Employee")
    assert api.calls("GET", "tokens")[-1][2] == {"all": ["true"]}
    assert problems == []
    ctx.close()


def test_forbidden_list_shows_the_server_message(browser):
    api = FakeTokenApi(BOSS)
    api.handle_original = api.handle

    def deny(route):
        if route.request.url.endswith("/api/tokens") and route.request.method == "GET":
            return _json(route, 403, {"detail": "This route cannot be used with an API token"})
        return api.handle_original(route)

    api.handle = deny
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("#tokens-card [role=alert]")).to_contain_text("cannot be used with an API token")
    ctx.close()


def test_settings_page_has_the_card_for_the_boss(browser):
    api = FakeTokenApi(
        {**BOSS, "permissions": {"settings": {"read": "all", "write": "all"}}},
        [copy.deepcopy(ITEM)],
    )
    ctx, pg, problems = open_app(browser, api, page="settings")
    expect(pg.locator("#tokens-card")).to_contain_text("Backup script")
    ctx.close()
