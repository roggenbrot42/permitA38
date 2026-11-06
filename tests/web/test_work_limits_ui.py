"""Work limits card on My settings. The API is intercepted with the browser context's routes (demo data only).
Contract: docs/specs/project-centric-planning.md, rule S6 and WP4b/P8."""

import json
import mimetypes
import pathlib
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"
BOSS = {"id": "u1a2b3c4d5", "name": "Demo Boss", "role": "boss", "permissions": {}}
EMPLOYEE = {"id": "u2b3c4d5e6", "name": "Demo Employee", "role": "employee", "permissions": {}}
DEFAULTS = {"info_day": 10, "warn_day": 12, "max_day": 16, "info_week": 60, "info_average": 48}
BOOTSTRAP = {
    "settings": {},
    "timer": None,
    "states": {},
    "conflicts": [],
    "templates": [],
    "data_dir": "",
    "features": {},
}


def _json(route, status, payload):
    return route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _static(route):
    path = urlparse(route.request.url).path
    f = WEB / ("index.html" if path in ("", "/") else path.lstrip("/"))
    if not f.is_file():
        return route.fulfill(status=404, body="not found")
    return route.fulfill(
        status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes()
    )


class FakeApi:
    def __init__(self, me, limits=None):
        self.me = me
        self.limits = limits or dict(DEFAULTS)
        self.puts = []
        self.put_error = None

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path.split("/api/", 1)[1]
        if path == "bootstrap":
            return _json(route, 200, {"me": self.me, **BOOTSTRAP})
        if path == "settings/work-limits":
            if req.method == "PUT":
                body = json.loads(req.post_data)
                self.puts.append(body)
                if self.put_error:
                    return _json(route, 422, {"detail": self.put_error})
                self.limits = body
            return _json(route, 200, self.limits)
        if path == "auth/me":
            return _json(route, 200, self.me)
        if path == "settings/personal-tax":
            return _json(route, 200, {"stored": False, "health": {}, "children": {}})
        if path in ("settings", "auth/status"):
            return _json(route, 200, {})
        return _json(route, 200, [])


def open_app(browser, api):
    ctx = browser.new_context(viewport={"width": 1280, "height": 1400})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.goto(f"{ORIGIN}/#/me")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems


def test_boss_sees_defaults_as_placeholders_and_stored_values(browser):
    api = FakeApi(BOSS, {**DEFAULTS, "warn_day": 13, "info_average": 44})
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("#work-limits")).to_be_visible()
    expect(pg.locator("#wl_warn_day")).to_have_value("13")
    expect(pg.locator("#wl_info_average")).to_have_value("44")
    expect(pg.locator("#wl_info_day")).to_have_value("10")
    expect(pg.locator("#wl_max_day")).to_have_attribute("placeholder", "16")
    expect(pg.locator("#wl_info_week")).to_have_attribute("placeholder", "60")
    assert problems == []
    ctx.close()


def test_save_sends_body_and_reset_sends_defaults(browser):
    api = FakeApi(BOSS)
    ctx, pg, problems = open_app(browser, api)
    pg.fill("#wl_info_day", "9")
    pg.fill("#wl_warn_day", "11.5")
    pg.fill("#wl_max_day", "14")
    pg.fill("#wl_info_week", "55")
    pg.fill("#wl_info_average", "")
    pg.click("#wl-save")
    expect(pg.locator("#toast")).to_contain_text("Work limits saved")
    assert api.puts == [{"info_day": 9, "warn_day": 11.5, "max_day": 14, "info_week": 55, "info_average": 48}]
    expect(pg.locator("#wl_warn_day")).to_have_value("11.5")
    pg.click("#wl-reset")
    expect(pg.locator("#wl_warn_day")).to_have_value("12")
    assert api.puts[-1] == DEFAULTS
    assert problems == []
    ctx.close()


def test_422_message_is_shown_escaped(browser):
    api = FakeApi(BOSS)
    api.put_error = "must satisfy <b>info_day</b> <= warn_day <= max_day"
    ctx, pg, problems = open_app(browser, api)
    pg.fill("#wl_info_day", "15")
    pg.click("#wl-save")
    err = pg.locator("#wl-error")
    expect(err).to_contain_text("must satisfy <b>info_day</b> <= warn_day <= max_day")
    expect(err.locator("b")).to_have_count(0)
    expect(pg.locator("#wl_info_day")).to_have_value("15")
    assert problems == []
    ctx.close()


def test_employee_does_not_see_the_card(browser):
    api = FakeApi(EMPLOYEE)
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("#my-profile")).to_be_visible()
    expect(pg.locator("#work-limits")).to_have_count(0)
    assert api.puts == []
    assert problems == []
    ctx.close()
