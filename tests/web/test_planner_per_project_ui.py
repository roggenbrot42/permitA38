"""Browser tests of the planner per project (forecast tab, Plan & vacation, allocations dialog, bootstrap banner).
The API is intercepted with the browser context's routes; the answers are the worked examples of
docs/contracts/planner-per-project.md (extracted verbatim into tests/web/fixtures/planner_per_project/), so these
tests run without the backend. The UI never computes capacity or gaps: every number asserted here comes from a fixture."""

import copy
import json
import mimetypes
import pathlib
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "planner_per_project"
ORIGIN = "http://permita38.test"

AREAS = ["planning", "projects", "customers", "contracts", "absences", "offers", "team", "time", "invoices", "expenses"]
BOSS = {
    "id": "u1b2c3d4",
    "name": "Demo Boss",
    "role": "boss",
    "permissions": {a: {"read": "all", "write": "all"} for a in AREAS},
}
USER = {"id": "u1b2c3d4", "username": "boss", "name": "Demo Boss", "role": "boss", "weekly_hours": 40, "active": True}
PROJECTS = [
    {"id": "a1b2c3d4", "name": "Demo project P", "status": "active", "customer_id": None},
    {"id": "e5f6a7b8", "name": "Demo project Q", "status": "active", "customer_id": None},
]


def fixture(name):
    return json.loads((FIX / f"{name}.json").read_text())


def monday():
    d = date.today()
    return d - timedelta(days=d.weekday())


def legacy_week(w, i):
    """The contract leaves the pre-existing fields out of its examples ("keep their existing values"): fill them."""
    w = copy.deepcopy(w)
    w["week"] = (monday() + timedelta(days=7 * i)).isoformat()
    for k, v in dict(tentative=0.0, free=0.0, revenue=0.0, tentative_revenue=0.0, basis="contract", events=[]).items():
        w.setdefault(k, v)
    return w


def forecast_a():
    f = fixture("forecast_example_a")
    f["weeks"] = [legacy_week(f["weeks"][0], i) for i in range(4)]
    f["totals"].update(contracted_revenue=0.0, assumed_revenue=0.0, revenue=0.0, free_worst_case=0.0)
    return f


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
    def __init__(self, forecast=None, allocations=None, projects=None):
        self.forecast = forecast or forecast_a()
        self.allocs = copy.deepcopy(allocations if allocations is not None else fixture("allocations_list"))
        self.projects = projects or copy.deepcopy(PROJECTS)
        self.me = BOSS
        self.users = [USER]
        self.requests = []
        self.confirms = []
        self.post_warnings = []  # planning_warnings answered by POST/PUT /allocations
        self.post_error = None  # (status, detail)
        self.apply_warnings = []

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def simulate_answer(self, body):
        f = copy.deepcopy(self.forecast)
        if body.get("changes", {}).get("allocations"):
            d = fixture("simulate_delta")["delta"]
            f["delta"] = d
            for w in f["weeks"]:
                w["delta"] = 25.0 / len(f["weeks"])
                for p in w.get("projects", []):
                    p["delta"] = 25.0 / len(f["weeks"]) if p["project_id"] == "a1b2c3d4" else 0.0
        return f

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        query = parse_qs(url.query)
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, query, body))
        m = req.method
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
        if path == "projects" and m == "GET":
            return _json(route, 200, self.projects)
        if path == "users" and m == "GET":
            return _json(route, 200, self.users)
        if path in ("customers", "contracts", "absences", "offers", "tasks", "entries", "invoices", "expenses"):
            return _json(route, 200, [])
        if path == "project-report":
            return _json(route, 200, {})
        if path == "holidays":
            return _json(route, 200, {})
        if path == "plan/scenarios":
            return _json(route, 200, [])
        wanted = query.get("project_id", [None])[0] or (body or {}).get("project_id")
        if path in ("plan", "plan/simulate") and wanted not in (None, "unassigned"):
            if wanted not in [p["id"] for p in self.projects]:
                return _json(route, 404, {"detail": "Project not found"})
        if path == "plan":
            return _json(route, 200, self.forecast)
        if path == "plan/simulate":
            return _json(route, 200, self.simulate_answer(body))
        if path == "plan/apply":
            return _json(route, 200, {"applied": 1, "allocation_warnings": self.apply_warnings})
        if path == "allocations" and m == "GET":
            return _json(route, 200, self.allocs)
        if path == "allocations" and m == "POST":
            if self.post_error:
                return _json(route, self.post_error[0], {"detail": self.post_error[1]})  # detail: text or a 422 list
            row = {"id": "n%07d" % len(self.allocs), "kind": "employee", "user_name": "Demo Boss", "status": "active"}
            row.update(body)
            row["project_name"] = next(p["name"] for p in self.projects if p["id"] == body["project_id"])
            self.allocs.append(row)
            return _json(route, 201, {**row, "planning_warnings": self.post_warnings})
        if path.startswith("allocations/"):
            aid = path.split("/")[1]
            if m == "DELETE":
                self.allocs = [a for a in self.allocs if a["id"] != aid]
                return route.fulfill(status=204)
            if m == "PUT":
                for a in self.allocs:
                    if a["id"] == aid:
                        a.update(body)
                return _json(route, 200, {**next(a for a in self.allocs if a["id"] == aid), "planning_warnings": []})
        if path.startswith("projects/") and path.endswith(("/planning", "/timesheet")):
            return _json(route, 404, {"detail": "Not found"})
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

    def on_dialog(d):
        api.confirms.append(d.message)
        d.accept()

    pg.on("dialog", on_dialog)
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems


def open_planner(browser, api):
    ctx, pg, problems = open_app(browser, api, page="plan/vacation", storage={"plnUser": BOSS["id"]})
    pg.wait_for_selector("#plnWrap .pln-bar, #plnWrap .pln-row")
    return ctx, pg, problems


def test_planner_existing_allocation_bar_can_be_moved_with_keyboard(browser):
    api = FakeApi()
    ctx, pg, problems = open_planner(browser, api)
    api.allocs[0]["start"] = monday().isoformat()
    api.allocs[0]["end"] = (monday() + timedelta(days=27)).isoformat()
    pg.reload()
    pg.wait_for_selector(".pln-bar.alloc")
    bar = pg.locator(".pln-bar.alloc").first
    bar.focus()
    pg.keyboard.press("ArrowRight")
    pg.wait_for_selector(".pln-bar.alloc.draft")
    sent = api.calls("POST", "plan/simulate")[-2][3]["changes"]["allocations"][0]
    assert sent["id"] == "9f3a1c20" and sent["start"] == (monday() + timedelta(days=1)).isoformat()
    assert problems == []
    ctx.close()


def test_stale_remembered_project_falls_back_to_all(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(
        browser, api, page="plan/vacation", storage={"plnProject": "gone0001", "plnUser": BOSS["id"]}
    )
    pg.wait_for_selector("#plnProj")
    expect(pg.locator("#plnProj")).to_have_value("")
    assert pg.evaluate("() => localStorage.plnProject") == ""
    ctx.close()


def test_buttons_are_hidden_without_the_planning_right(browser):
    api = FakeApi()
    api.me = {**BOSS, "permissions": {**BOSS["permissions"], "planning": {"read": "none", "write": "none"}}}
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.wait_for_selector("[data-a=editProject]")
    assert pg.locator("[data-a=allocations]").count() == 0
    pg.goto(f"{ORIGIN}/#/team")
    pg.wait_for_selector("[data-a=editUser]")
    assert pg.locator("[data-a=allocations]").count() == 0
    ctx.close()
