"""Browser tests of the planner Overview tab (GET /api/plan). The API is intercepted with the browser context's routes;
the answers are built from the hand-computed examples E1 and E7 of docs/specs/project-centric-planning.md, so these
tests run without the backend. The UI never computes capacity or shortfalls: every number asserted here comes from the
fake answer."""

import copy
import json
import mimetypes
import pathlib
import re
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"
AREAS = ["planning", "projects", "customers", "contracts", "absences", "offers", "team", "time", "invoices", "expenses"]
BOSS = {
    "id": "u1",
    "name": "Ada",
    "role": "boss",
    "permissions": {a: {"read": "all", "write": "all"} for a in AREAS},
}
USERS = [{"id": "u1", "username": "ada", "name": "Ada", "role": "boss", "weekly_hours": 40, "active": True}]
PROJECTS = [
    {"id": "p-orchard", "name": "Orchard", "status": "active", "customer_id": None},
    {"id": "p-admin", "name": "Administration", "status": "active", "customer_id": None},
]

K2_EXPLANATION = (
    "50 h promised − 0 h booked = 50 h remaining. Until 04.12.2026 Orchard has 24 h of assigned time left "
    "after its weekly contracts. Short 26 h."
)


def plan_body():
    """E1 (plus offer O1 of E7) as GET /api/plan answers it; the second week is E4's over-assigned week."""
    k1 = {
        "kind": "contract", "id": "k1", "title": "Support", "type": "weekly", "start": "2026-11-09",
        "end": "2026-12-18", "promised": 24.0, "booked": 0.0, "remaining": 24.0, "covered": 24.0, "shortfall": 0.0,
        "weekly_shortfalls": [], "rate": 120.0, "value": 2880.0, "invoiced": 0.0, "open": 2880.0,
        "win_probability": None, "explanation": "4 h a week for 6 weeks = 24 h, all covered.", "suggestions": [],
    }  # fmt: skip
    k2 = {
        "kind": "contract", "id": "k2", "title": "Data migration", "type": "total", "start": "2026-11-09",
        "end": "2026-12-04", "promised": 50.0, "booked": 0.0, "remaining": 50.0, "covered": 24.0, "shortfall": 26.0,
        "weekly_shortfalls": [], "rate": 120.0, "value": 6000.0, "invoiced": 0.0, "open": 6000.0,
        "win_probability": None, "explanation": K2_EXPLANATION,
        "suggestions": [{"user_id": "u1", "name": "Ada", "hours": 24.0, "from": "2026-11-09", "to": "2026-12-04"}],
    }  # fmt: skip
    o1 = {
        "kind": "offer", "id": "o1", "title": "Extra <i>rollout</i>", "type": "total", "start": "2026-11-16",
        "end": "2026-12-04", "promised": 10.0, "booked": 0.0, "remaining": 10.0, "covered": 0.0, "shortfall": 10.0,
        "weekly_shortfalls": [], "rate": 100.0, "value": 1000.0, "invoiced": 0.0, "open": 1000.0,
        "win_probability": 50, "explanation": "10 h offered, nothing left after the contracts. Short 10 h.",
        "suggestions": [],
    }  # fmt: skip
    return {
        "today": "2026-11-08",
        "weeks": [
            {
                "week": "2026-11-09", "week_end": "2026-11-15", "capacity": 40.0, "holiday_hours": 0.0,
                "vacation_hours": 0.0, "absence_hours": 0.0, "busy_hours": 0.0, "assigned_customer": 4.0,
                "assigned_internal": 30.0, "unassigned": 6.0, "over_assigned": 0.0, "billable_share": 10.0,
                "subcontractor_hours": 6.0,
            },
            {
                "week": "2026-11-16", "week_end": "2026-11-22", "capacity": 20.0, "holiday_hours": 0.0,
                "vacation_hours": 0.0, "absence_hours": 0.0, "busy_hours": 0.0, "assigned_customer": 10.0,
                "assigned_internal": 12.0, "unassigned": 0.0, "over_assigned": 2.0, "billable_share": 50.0,
                "subcontractor_hours": 0.0,
            },
        ],
        "people": [],
        "projects": [
            {
                "project_id": "p-admin", "name": "Administration", "kind": "internal", "status": "ok",
                "assigned": 180.0, "booked": 12.0, "budget_hours": 400.0, "items": [],
            },
            {
                "project_id": "p-orchard", "name": "Orchard", "kind": "customer",
                "supply": {"employees": 24.0, "subcontractors": 24.0, "total": 48.0},
                "remaining_contracts": 74.0, "shortfall_contracts": 26.0, "remaining_offers": 10.0,
                "shortfall_worst_case": 36.0, "shortfall_expected": 31.0, "status": "short",
                "planned_subcontractor_cost": 2160.0, "contract_value": 8880.0, "margin": 6720.0,
                "items": [k1, k2, o1],
            },
        ],
        "warnings": [
            {
                "code": "contract.shortfall", "severity": "warning", "field": "k2", "confirmable": False,
                "message": "Data migration: 50 h remaining, 24 h covered: short 26 h.", "details": {},
            }
        ],
        "skipped": [{"coll": "contracts", "id": "c0ffee00", "reason": "bad date"}],
    }  # fmt: skip


def _json(route, status, payload):
    return route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _static(route):
    path = urlparse(route.request.url).path
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    f = WEB / name
    if not f.is_file():
        return route.fulfill(status=404, body="not found")
    return route.fulfill(
        status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes()
    )


class FakeApi:
    def __init__(self, body=None, me=None):
        self.body = body or plan_body()
        self.me = me or BOSS
        self.projects = copy.deepcopy(PROJECTS)
        self.requests = []

    def plan_queries(self):
        return [q for m, p, q in self.requests if m == "GET" and p == "plan"]

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        query = parse_qs(url.query)
        self.requests.append((req.method, path, query))
        if path == "bootstrap":
            return _json(
                route,
                200,
                {
                    "me": self.me, "settings": {}, "timer": None, "states": {}, "conflicts": [], "templates": [],
                    "data_dir": "", "features": {},
                },
            )  # fmt: skip
        if path == "projects":
            return _json(route, 200, self.projects)
        if path == "users":
            return _json(route, 200, USERS)
        if path == "plan":
            wanted = query.get("project_id", [None])[0]
            if wanted not in (None, "unassigned") and wanted not in [p["id"] for p in self.projects]:
                return _json(route, 404, {"detail": "Project not found"})
            return _json(route, 200, self.body)
        if path in ("holidays", "project-report"):
            return _json(route, 200, {})
        return _json(route, 200, [])


def open_app(browser, api, page="plan", storage=None):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    for k, v in (storage or {}).items():
        ctx.add_init_script(f"localStorage.setItem('{k}', '{v}')")
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("console", lambda m: m.type == "error" and "status of" not in m.text and problems.append(m.text))
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    pg.wait_for_selector(".ov-weeks")
    # the assignment dialog is extended by another package: record what the overview hands to it
    pg.evaluate("() => { window.__alloc = null; window.openAllocations = o => { window.__alloc = o; }; }")
    return ctx, pg, problems


def test_orchard_is_short_with_the_servers_explanation(browser):
    ctx, pg, problems = open_app(browser, FakeApi())
    row = pg.locator("tr.ov-project[data-project-row=p-orchard]")
    expect(row).to_contain_text("Orchard")
    expect(row.locator(".pill.bad")).to_have_text("short")
    expect(pg.locator("tr[data-item=k2] + tr.ov-expl .ov-explanation")).to_have_text(K2_EXPLANATION)
    k2 = pg.locator("tr[data-item=k2]")
    cells = [c.strip() for c in k2.locator("td.num").all_inner_texts()]
    assert cells == ["50 h", "0 h", "50 h", "24 h", "26 h"]  # promised, booked, remaining, covered, shortfall
    expect(k2).to_contain_text("value 6.000,00")
    # customer projects first, internal below with assigned / booked / budget
    names = pg.locator("tr.ov-project b").all_inner_texts()
    assert names == ["Orchard", "Administration"]
    admin = pg.locator("tr.ov-project[data-project-row=p-admin]")
    expect(admin).to_contain_text("assigned 180 h")
    expect(admin).to_contain_text("booked 12 h")
    expect(admin).to_contain_text("budget 400 h")
    # the offer shows net and win probability, its name is escaped
    o1 = pg.locator("tr[data-item=o1]")
    expect(o1).to_contain_text("Extra <i>rollout</i>")
    expect(o1).to_contain_text("net 1.000,00")
    expect(o1).to_contain_text("50 %")
    assert pg.locator("tr[data-item=o1] i").count() == 0
    expect(pg.locator(".pp-warnings")).to_contain_text("Data migration: 50 h remaining, 24 h covered: short 26 h.")
    assert problems == []
    ctx.close()


def test_assign_button_opens_the_dialog_prefilled(browser):
    ctx, pg, problems = open_app(browser, FakeApi())
    button = pg.get_by_role("button", name="Assign Ada (24 h)")
    expect(button).to_have_count(1)
    button.click()
    assert pg.evaluate("() => window.__alloc") == {
        "projectId": "p-orchard",
        "userId": "u1",
        "start": "2026-11-09",
        "end": "2026-12-04",
        "total_hours": 24,
    }
    assert problems == []
    ctx.close()


def test_week_row_shows_the_servers_numbers(browser):
    ctx, pg, problems = open_app(browser, FakeApi())
    row = pg.locator("tr[data-week='2026-11-09']")
    expect(row).to_contain_text("09.11.2026")
    for col, value in (("capacity", "40"), ("customer", "4"), ("internal", "30"), ("unassigned", "6")):
        expect(row.locator(f"td[data-col={col}]")).to_have_text(value)
    expect(row.locator("td[data-col=share]")).to_have_text("10 %")
    expect(row.locator("td[data-col=subs]")).to_have_text("6")
    assert "pp-over" not in (row.get_attribute("class") or "")
    assert problems == []
    ctx.close()


def test_over_assigned_week_is_red(browser):
    ctx, pg, problems = open_app(browser, FakeApi())
    row = pg.locator("tr[data-week='2026-11-16']")
    expect(row).to_have_class(re.compile(r"\bpp-over\b"))
    expect(row.locator("td[data-col=over]")).to_have_text("2")
    expect(row.locator("td[data-col=over]")).to_have_class(re.compile(r"\bpp-gap\b"))
    assert problems == []
    ctx.close()


def test_filters_reload_with_the_right_query(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    assert api.plan_queries()[-1] == {"weeks": ["12"]}
    pg.select_option("select[data-change=fcWeeks]", "26")
    pg.wait_for_function("() => localStorage.fcWeeks === '26'")
    pg.wait_for_selector(".ov-weeks")
    pg.select_option("select[data-change=fcUser]", "u1")
    pg.wait_for_function("() => localStorage.fcUser === 'u1'")
    pg.wait_for_selector(".ov-weeks")
    pg.select_option("select[data-change=fcProject]", "p-orchard")
    pg.wait_for_function("() => localStorage.fcProject === 'p-orchard'")
    pg.wait_for_selector(".ov-weeks")
    for _ in range(30):
        if api.plan_queries()[-1].get("project_id"):
            break
        pg.wait_for_timeout(100)
    assert api.plan_queries()[-1] == {"weeks": ["26"], "user_id": ["u1"], "project_id": ["p-orchard"]}
    pg.select_option("select[data-change=fcProject]", "unassigned")
    pg.wait_for_function("() => localStorage.fcProject === 'unassigned'")
    for _ in range(30):
        if api.plan_queries()[-1].get("project_id") == ["unassigned"]:
            break
        pg.wait_for_timeout(100)
    assert api.plan_queries()[-1]["project_id"] == ["unassigned"]
    assert problems == []
    ctx.close()


def test_stale_remembered_project_falls_back_to_all(browser):
    ctx, pg, problems = open_app(browser, FakeApi(), storage={"fcProject": "gone0001"})
    expect(pg.locator("select[data-change=fcProject]")).to_have_value("")
    assert pg.evaluate("() => localStorage.fcProject") == ""
    ctx.close()


def test_skipped_line_only_for_the_boss(browser):
    ctx, pg, problems = open_app(browser, FakeApi())
    expect(pg.locator(".ov-skipped summary")).to_have_text("1 damaged record was skipped")
    ctx.close()
    employee = {**BOSS, "role": "employee"}
    ctx, pg, problems = open_app(browser, FakeApi(me=employee))
    assert pg.locator(".ov-skipped").count() == 0
    assert "damaged" not in pg.locator("main, #app, body").first.inner_text()
    ctx.close()
