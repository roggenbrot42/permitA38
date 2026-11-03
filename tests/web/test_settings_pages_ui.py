"""Browser tests of the Business settings and My settings pages (WP-A4). The API is intercepted with the browser
context's routes (fake demo data only), so these tests run without the backend."""

import json
import mimetypes
import pathlib
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"

BOSS_PERMS = {a: {"read": "all", "write": "all"} for a in ("settings", "planning", "team", "timer", "finance")}
EMP_PERMS = {"timer": {"read": "all", "write": "all"}}
BOSS = {
    "id": "u1",
    "name": "Demo Boss",
    "username": "demoboss",
    "role": "boss",
    "weekly_hours": 38.5,
    "default_rate": 90,
    "permissions": BOSS_PERMS,
}
EMPLOYEE = {
    "id": "u2",
    "name": "Demo Employee",
    "username": "demoemp",
    "role": "employee",
    "weekly_hours": 30,
    "default_rate": 55,
    "permissions": EMP_PERMS,
}
SETTINGS = {"company_name": "Demo GmbH", "company_type": "freiberufler", "hebesatz": 400, "default_template": "t1"}


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


def open_app(browser, me, stored=True, page="settings"):
    seen: list[tuple[str, str]] = []

    def api(route):
        req = route.request
        path = urlparse(req.url).path.split("/api/", 1)[1]
        seen.append((req.method, path))
        if path == "bootstrap":
            return _json(
                route,
                200,
                {
                    "me": me,
                    "settings": SETTINGS,
                    "timer": None,
                    "states": {"SN": "Sachsen"},
                    "conflicts": [],
                    "templates": [
                        {"id": "t1", "name": "Classic", "layout": "classic", "language": "de", "accent": "#123456"}
                    ],
                    "data_dir": "/demo",
                    "features": {},
                },
            )
        if path == "auth/me":
            return _json(route, 200, me)
        if path == "settings":
            return _json(route, 200, SETTINGS)
        if path == "settings/personal-tax":
            if me["role"] != "boss":
                return _json(route, 403, {"detail": "Not allowed"})
            return _json(route, 200, {"stored": stored, "health": {"mode": "pkv"}, "children": {"count": 2}})
        if path == "tokens":
            return _json(route, 200, [])
        if path in ("calendars", "calendars/suggestions"):
            return _json(route, 200, [])
        return _json(route, 200, {} if path in ("auth/status",) else [])

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems, seen


def test_business_settings_cards_as_grouped(browser):
    ctx, pg, problems, _ = open_app(browser, BOSS)
    expect(pg.locator("h1")).to_have_text("Business settings")
    expect(pg.locator("nav.side")).to_contain_text("Business settings")
    for title in (
        "Business identity",
        "Bank and logo",
        "Business tax set-up",
        "Documents and defaults",
        "Data & sync",
    ):
        expect(pg.locator("#main h2", has_text=title)).to_have_count(1)
    expect(pg.locator("#card-identity #f_company_name")).to_have_count(1)
    expect(pg.locator("#card-identity #f_vat_id")).to_have_count(1)
    expect(pg.locator("#card-bank #f_iban")).to_have_count(1)
    expect(pg.locator("#card-bank #logo_file")).to_have_count(1)
    expect(pg.locator("#card-tax #f_vat_rate")).to_have_count(1)
    expect(pg.locator("#card-defaults #f_default_rate")).to_have_count(1)
    expect(pg.locator("#card-defaults a[href='#/numbering']")).to_have_count(1)
    expect(pg.locator("#card-defaults [data-a=editTemplate]").first).to_be_visible()
    # moved to My settings or removed
    expect(pg.locator("#tokens-card")).to_have_count(0)
    expect(pg.locator("#calendars")).to_have_count(0)
    for legacy in ("social_monthly", "church_tax", "splitting", "other_income", "other_deductions"):
        expect(pg.locator(f"#f_{legacy}")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_hebesatz_only_for_trade_companies(browser):
    ctx, pg, problems, _ = open_app(browser, BOSS)
    expect(pg.locator("#hebesatz_wrap")).to_be_hidden()
    pg.select_option("#f_company_type", "gewerbe")
    expect(pg.locator("#hebesatz_wrap")).to_be_visible()
    pg.select_option("#f_company_type", "gmbh")
    expect(pg.locator("#hebesatz_wrap")).to_be_visible()
    pg.select_option("#f_company_type", "freiberufler")
    expect(pg.locator("#hebesatz_wrap")).to_be_hidden()
    assert problems == []
    ctx.close()


def test_boss_my_settings_has_tax_summary_and_link(browser):
    ctx, pg, problems, _ = open_app(browser, BOSS, page="me")
    expect(pg.locator("h1")).to_have_text("My settings")
    expect(pg.locator("nav.side a[href='#/me']")).to_have_count(1)
    profile = pg.locator("#my-profile")
    expect(profile.locator("[data-f=name]")).to_have_text("Demo Boss")
    expect(profile.locator("[data-f=username]")).to_have_text("demoboss")
    expect(profile.locator("[data-f=role]")).to_have_text("Boss")
    expect(profile.locator("[data-f=weekly_hours]")).to_have_text("38.5")
    expect(profile.locator("[data-f=default_rate]")).to_have_text("90")
    expect(profile.locator("[data-a=changePassword]")).to_have_count(1)
    expect(profile.locator("[data-a=twoFactor]")).to_have_count(1)
    tax = pg.locator("#my-tax")
    expect(tax.locator("[data-f=tax_health]")).to_have_text("private health insurance")
    expect(tax.locator("[data-f=tax_children]")).to_have_text("2")
    expect(tax).not_to_contain_text("Not saved yet")
    expect(pg.locator("#tokens-card")).to_have_count(1)
    expect(pg.locator("#calendars")).to_have_count(1)
    tax.locator("a[href='#/me/tax']").click()
    expect(pg.locator("#tax-profile")).to_be_visible()
    assert problems == []
    ctx.close()


def test_boss_tax_summary_says_not_saved_yet(browser):
    ctx, pg, problems, _ = open_app(browser, BOSS, stored=False, page="me")
    expect(pg.locator("#my-tax")).to_contain_text("Not saved yet")
    assert problems == []
    ctx.close()


def test_employee_sees_profile_and_tokens_but_no_tax(browser):
    ctx, pg, problems, seen = open_app(browser, EMPLOYEE, page="me")
    expect(pg.locator("h1")).to_have_text("My settings")
    expect(pg.locator("#my-profile [data-f=name]")).to_have_text("Demo Employee")
    expect(pg.locator("#my-profile [data-f=weekly_hours]")).to_have_text("30")
    expect(pg.locator("#tokens-card")).to_have_count(1)
    expect(pg.locator("#my-tax")).to_have_count(0)
    expect(pg.locator("a[href='#/me/tax']")).to_have_count(0)
    expect(pg.locator("#calendars")).to_have_count(0)  # needs planning write with scope all
    expect(pg.locator("nav.side a[href='#/settings']")).to_have_count(0)
    assert ("GET", "settings/personal-tax") not in seen
    # the tax route itself shows the boss-only message
    pg.goto(f"{ORIGIN}/#/me/tax")
    expect(pg.locator("#main")).to_contain_text("Only the boss")
    assert problems == []
    ctx.close()
