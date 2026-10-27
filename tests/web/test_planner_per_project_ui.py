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


def forecast_b(after=False):
    w0 = fixture("example_b_week_no_allocation")
    for p in w0["projects"]:  # "other per-project keys as in example A"
        p.setdefault("binding_offers", 0.0)
        p.setdefault("expected_offers", 0.0)
        p.setdefault("free_after_confirmed", 0.0)
    f = {
        "weeks": [legacy_week(w0, i) for i in range(4)],
        "totals": {
            "gap": 120.0,
            "over_weeks": 4,
            "gap_by_project": [{"project_id": "a1b2c3d4", "name": "Demo project P", "gap": 120.0}],
            "contracted_revenue": 0.0,
            "assumed_revenue": 0.0,
            "revenue": 0.0,
            "free_worst_case": 8.0,
        },
        "warnings": [],
    }
    if after:  # derived from the contract's "after the bootstrap" sentence (P gap 0, team gap 0)
        for w in f["weeks"]:
            w["gap"] = w["gap_confirmed"] = w["gap_expected"] = 0.0
            w["status"] = "tight"
            for p in w["projects"]:
                p["gap"] = p["gap_confirmed"] = p["gap_expected"] = 0.0
        f["totals"].update(gap=0.0, over_weeks=0, gap_by_project=[])
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
        if path in ("forecast", "plan/simulate") and wanted not in (None, "unassigned"):
            if wanted not in [p["id"] for p in self.projects]:
                return _json(route, 404, {"detail": "Project not found"})
        if path == "forecast":
            return _json(route, 200, self.forecast)
        if path == "plan/simulate":
            return _json(route, 200, self.simulate_answer(body))
        if path == "plan/day-table":
            return _json(route, 200, fixture("day_table"))
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
        if path.startswith("allocations/") and path != "allocations/bootstrap":
            aid = path.split("/")[1]
            if m == "DELETE":
                self.allocs = [a for a in self.allocs if a["id"] != aid]
                return route.fulfill(status=204)
            if m == "PUT":
                for a in self.allocs:
                    if a["id"] == aid:
                        a.update(body)
                return _json(route, 200, {**next(a for a in self.allocs if a["id"] == aid), "planning_warnings": []})
        if path == "allocations/bootstrap":
            out = fixture("example_b_bootstrap")
            if body.get("confirm"):
                out.update(confirm=True, created=True)
                for a in out["allocations"]:
                    a.update(id="b0000001", kind="employee")
                self.allocs.append({**out["allocations"][0], "status": "active", "planned_hours": 100.0})
                self.forecast = forecast_b(after=True)
            return _json(route, 200, out)
        if path.startswith("projects/") and path.endswith("/planning"):
            return _json(route, 404, {"detail": "Not found"})
        return _json(route, 200, [])


def open_app(browser, api, page="plan/forecast", storage=None):
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


def test_forecast_shows_gap_per_project_and_server_warning(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    row = pg.locator("table tr.pp-over").first
    expect(row.locator(".pp-gap")).to_have_text("5 h")  # week gap 5.0 of example A
    expect(pg.locator(".pp-summary")).to_contain_text("Not covered: 20 h")
    expect(pg.locator(".pp-summary")).to_contain_text("Demo project P: 20 h")
    expect(pg.locator(".pp-warnings")).to_contain_text("project Demo project P: 30 h are planned but only 25 h")
    assert pg.locator(".pp-banner").count() == 0  # the employee allocation exists: no bootstrap banner
    row.locator(".pp-toggle").click()
    detail = pg.locator("tr.pp-detail").first
    expect(detail).to_contain_text("5 h not covered")  # P
    idle = detail.locator(".pp-idle")  # Q has 5 free hours that P cannot use
    expect(idle).to_contain_text("5 h free")
    assert "Demo project P" in idle.get_attribute("title")
    assert pg.locator("tr.pp-detail .pill.bad").count() == 1  # only P is over
    assert problems == []
    ctx.close()


def test_project_filter_sends_project_id_and_is_remembered(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    assert "project_id" not in api.calls("GET", "forecast")[0][2]
    pg.select_option("select[data-change=fcProject]", "unassigned")
    pg.wait_for_function("() => localStorage.fcProject === 'unassigned'")
    pg.wait_for_selector("select[data-change=fcProject]")
    assert api.calls("GET", "forecast")[-1][2]["project_id"] == ["unassigned"]
    expect(pg.locator("select[data-change=fcProject]")).to_have_value("unassigned")
    assert problems == []
    ctx.close()


def test_day_detail_uses_the_day_table(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    pg.locator(".pp-toggle").first.click()
    pg.locator("[data-a=fcDays]").first.click()
    expect(pg.locator(".pp-days")).to_contain_text("Demo project P: internal 6/5 gap 1")
    expect(pg.locator(".pp-days")).to_contain_text("Demo project Q: internal 2/3")
    q = api.calls("GET", "plan/day-table")[0][2]
    assert q["from"] == [monday().isoformat()] and q["to"] == [(monday() + timedelta(days=6)).isoformat()]
    assert problems == []
    ctx.close()


def test_banner_preview_confirm_and_second_run(browser):
    api = FakeApi(forecast=forecast_b(), allocations=[])
    ctx, pg, problems = open_app(browser, api)
    banner = pg.locator(".pp-banner")
    expect(banner).to_contain_text("120 h of planned work are not covered")
    banner.get_by_role("button", name="Allocate my capacity").click()
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator(".al-preview")).to_contain_text("Contract C_P")
    expect(dlg.locator(".al-preview")).to_contain_text("30")
    assert [r[3] for r in api.calls("POST", "allocations/bootstrap")] == [{"confirm": False}]  # preview writes nothing
    dlg.get_by_role("button", name="Create 1 allocation").click()
    expect(dlg.locator(".al-preview")).to_contain_text("Created 1 allocation")
    assert [r[3] for r in api.calls("POST", "allocations/bootstrap")] == [{"confirm": False}, {"confirm": True}]
    dlg.get_by_role("button", name="Close").click()
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    pg.wait_for_selector(".pp-summary", state="detached")  # the page was redrawn: gap and banner are gone
    assert pg.locator(".pp-banner").count() == 0
    assert problems == []
    ctx.close()


def test_person_without_allocation_gets_the_hint(browser):
    api = FakeApi(allocations=[])
    ctx, pg, problems = open_app(browser, api, storage={"fcUser": BOSS["id"]})
    expect(pg.locator(".pp-empty")).to_contain_text("All of your capacity is unassigned")
    assert pg.locator(".pp-banner").count() == 0
    assert problems == []
    ctx.close()


def open_dialog_from_project(browser, api):
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.locator("[data-a=allocations][data-project=a1b2c3d4]").click()
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("Allocations of Demo project P")
    return ctx, pg, dlg, problems


def test_dialog_lists_filters_and_creates_with_inline_warning(browser):
    api = FakeApi()
    api.post_warnings = [
        {
            "code": "allocation.employee_over_allocated",
            "severity": "warning",
            "field": "allocations",
            "confirmable": True,
            "message": "Week of 2026-03-02: 55 h are allocated to Demo Boss, 40 h are contracted.",
            "details": {},
        }
    ]
    ctx, pg, dlg, problems = open_dialog_from_project(browser, api)
    assert api.calls("GET", "allocations")[-1][2]["project_id"] == ["a1b2c3d4"]
    expect(dlg.locator("table")).to_contain_text("25 h/week")
    expect(dlg.locator("table")).to_contain_text("100 h")  # planned_hours from the server
    dlg.get_by_role("button", name="+ Allocation").click()
    dlg.locator("#al_hours").fill("30")
    dlg.locator("#al_start").fill("2026-03-02")
    dlg.locator("#al_end").fill("2026-03-27")
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator(".al-warn")).to_contain_text("55 h are allocated")  # saved, warning shown, not blocking
    body = api.calls("POST", "allocations")[0][3]
    assert body == {
        "user_id": "u1b2c3d4",
        "project_id": "a1b2c3d4",
        "start": "2026-03-02",
        "end": "2026-03-27",
        "hours_per_week": 30.0,
    }
    expect(dlg.locator("table")).to_contain_text("30 h/week")
    assert problems == []
    ctx.close()


def test_dialog_edit_delete_and_server_errors(browser):
    api = FakeApi()
    ctx, pg, dlg, problems = open_dialog_from_project(browser, api)
    api.post_error = (409, "The project is closed")
    dlg.get_by_role("button", name="+ Allocation").click()
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("[role=alert]")).to_have_text("The project is closed")
    assert dlg.locator(".al-warn").count() == 0
    dlg.get_by_role("button", name="Cancel").click()
    dlg.locator("tr[data-a=alEdit]").first.click()
    dlg.locator("#al_hours").fill("12.5")
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("table")).to_contain_text("12,5 h/week")
    assert api.calls("PUT", "allocations/9f3a1c20")[0][3]["hours_per_week"] == 12.5
    dlg.locator("tr[data-a=alEdit]").first.click()
    dlg.get_by_role("button", name="Delete").click()
    assert api.confirms == ["Delete this allocation?"]
    assert api.calls("DELETE", "allocations/9f3a1c20")
    expect(dlg).to_contain_text("No allocations yet")
    assert problems == []
    ctx.close()


def test_dialog_escapes_names_and_hides_external_rows_from_editing(browser):
    api = FakeApi()
    api.projects[0]["name"] = "<img src=x onerror=alert(1)>"
    api.allocs[0]["project_name"] = "<img src=x onerror=alert(1)>"
    api.allocs.append(
        {**api.allocs[0], "id": "ext00001", "kind": "external", "user_id": None, "supplier_name": "Sub <b>X</b>"}
    )
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.locator("[data-a=allocations][data-project=a1b2c3d4]").click()
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("<img src=x onerror=alert(1)>")
    assert dlg.locator("img").count() == 0 and dlg.locator("b b").count() == 0
    expect(dlg).to_contain_text("Sub <b>X</b>")
    assert dlg.locator("tr[data-a=alEdit]").count() == 1  # the subcontractor row is read-only
    assert problems == []
    ctx.close()


def test_team_page_opens_the_dialog_for_a_person(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="team")
    pg.locator("[data-a=allocations][data-user=u1b2c3d4]").click()
    expect(pg.locator("#modal-root .modal")).to_contain_text("Allocations of Demo Boss")
    assert api.calls("GET", "allocations")[-1][2]["user_id"] == ["u1b2c3d4"]
    assert problems == []
    ctx.close()


def open_planner(browser, api):
    ctx, pg, problems = open_app(browser, api, page="plan/vacation", storage={"plnUser": BOSS["id"]})
    pg.wait_for_selector("#plnWrap .pln-bar, #plnWrap .pln-row")
    return ctx, pg, problems


def test_planner_project_selector_and_stacked_bars(browser):
    api = FakeApi()
    ctx, pg, problems = open_planner(browser, api)
    assert "project_id" not in api.calls("POST", "plan/simulate")[-1][3]  # old call shape when no project is chosen
    # the per-project row of stacked bars was removed from the timeline; the week table keeps the Gap column
    assert pg.locator(".pln-pbar").count() == 0
    assert pg.locator("#pln .pln-label", has_text="Per project").count() == 0
    expect(pg.locator("#pln table th", has_text="Gap")).not_to_have_count(0)
    pg.select_option("#plnProj", "a1b2c3d4")
    pg.wait_for_function("() => localStorage.plnProject === 'a1b2c3d4'")
    pg.wait_for_function(
        "() => document.querySelector('#plnProj') && document.querySelector('#plnProj').value === 'a1b2c3d4'"
    )
    for _ in range(50):  # the simulation runs after a short debounce
        if api.calls("POST", "plan/simulate")[-1][3].get("project_id") == "a1b2c3d4":
            break
        pg.wait_for_timeout(100)
    assert api.calls("POST", "plan/simulate")[-1][3]["project_id"] == "a1b2c3d4"
    expect(pg.locator("#pln")).to_contain_text("Not covered: 20 h")
    assert problems == []
    ctx.close()


def test_planner_allocation_draft_delta_discard_and_apply(browser):
    api = FakeApi()
    ctx, pg, problems = open_planner(browser, api)
    start = monday().isoformat()
    pg.locator("#plnAHrs").fill("10")
    pg.locator("#plnAFrom").fill(start)
    pg.locator("#plnATo").fill((monday() + timedelta(days=27)).isoformat())
    pg.get_by_role("button", name="Add allocation").click()
    expect(pg.locator(".pln-delta")).to_contain_text("Demo project P 20 h → 45 h (+25 h)")
    sent = api.calls("POST", "plan/simulate")[-2][3]["changes"]["allocations"]  # [-1] is the base call
    assert sent == [
        {
            "user_id": "u1b2c3d4",
            "project_id": "a1b2c3d4",
            "start": start,
            "end": (monday() + timedelta(days=27)).isoformat(),
            "hours_per_week": 10.0,
        }
    ]
    assert pg.locator(".pln-bar.alloc.draft").count() == 1
    assert api.calls("POST", "plan/apply") == []  # a draft writes nothing
    pg.locator(".pln-draftlist").get_by_role("button", name="Discard").click()
    pg.wait_for_selector(".pln-bar.alloc.draft", state="detached")
    assert "allocations" not in api.calls("POST", "plan/simulate")[-2][3]["changes"]
    # apply with a warning from the server
    api.apply_warnings = [
        {
            "code": "allocation.employee_over_allocated",
            "severity": "warning",
            "field": "allocations",
            "confirmable": True,
            "message": "Week of 2026-03-02: 50 h allocated.",
            "details": {},
        }
    ]
    pg.get_by_role("button", name="Add allocation").click()
    pg.locator("[data-a=plnApply]").click()
    pg.wait_for_selector("#plnWarn")
    expect(pg.locator("#plnWarn")).to_contain_text("50 h allocated")
    assert api.confirms and api.confirms[-1].startswith("Write 1 change")
    assert api.calls("POST", "plan/apply")[0][3]["changes"]["allocations"][0]["project_id"] == "a1b2c3d4"
    assert problems == []
    ctx.close()


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


def test_inactive_user_row_keeps_its_person_on_save(browser):
    api = FakeApi()
    api.allocs[0]["user_id"] = "uinact01"
    api.allocs[0]["user_name"] = "Former Employee"
    api.users = [{**USER}, {**USER, "id": "uinact01", "name": "Former Employee", "active": False}]
    ctx, pg, dlg, problems = open_dialog_from_project(browser, api)
    dlg.get_by_role("button", name="Edit").first.click()
    expect(dlg.locator("#al_user option:checked")).to_have_text("Former Employee (inactive)")
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("form.al-form")).to_have_count(0)
    assert api.calls("PUT", "allocations/9f3a1c20")[0][3]["user_id"] == "uinact01"
    assert problems == []
    ctx.close()


def test_enter_on_cancel_or_delete_never_saves(browser):
    api = FakeApi()
    ctx, pg, dlg, problems = open_dialog_from_project(browser, api)
    dlg.get_by_role("button", name="+ Allocation").click()
    dlg.locator("form.al-form").get_by_role("button", name="Cancel").focus()
    pg.keyboard.press("Enter")
    expect(dlg.locator("form.al-form")).to_have_count(0)
    assert api.calls("POST", "allocations") == []
    dlg.get_by_role("button", name="Edit").first.click()
    dlg.get_by_role("button", name="Delete").focus()
    pg.keyboard.press("Enter")  # a button press is its own action (confirmed delete), not a save
    expect(dlg).to_contain_text("No allocations yet")
    assert api.calls("POST", "allocations") == [] and api.calls("PUT", "allocations/9f3a1c20") == []
    assert api.confirms == ["Delete this allocation?"]
    assert problems == []
    ctx.close()


def test_escape_closes_the_dialog_and_redraws_the_page(browser):
    api = FakeApi(forecast=forecast_b(), allocations=[])
    ctx, pg, problems = open_app(browser, api)
    pg.get_by_role("button", name="Allocate my capacity").click()
    dlg = pg.locator("#modal-root .modal")
    dlg.get_by_role("button", name="Create 1 allocation").click()
    expect(dlg.locator(".al-preview")).to_contain_text("Created 1 allocation")
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    pg.wait_for_selector(".pp-banner", state="detached")
    assert problems == []
    ctx.close()


def test_stale_remembered_project_falls_back_to_all(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, storage={"fcProject": "gone0001"})
    expect(pg.locator("select[data-change=fcProject]")).to_have_value("")
    assert pg.evaluate("() => localStorage.fcProject") == ""
    ctx.close()
    api = FakeApi()
    ctx, pg, problems = open_app(
        browser, api, page="plan/vacation", storage={"plnProject": "gone0001", "plnUser": BOSS["id"]}
    )
    pg.wait_for_selector("#plnProj")
    expect(pg.locator("#plnProj")).to_have_value("")
    assert pg.evaluate("() => localStorage.plnProject") == ""
    ctx.close()


@pytest.mark.parametrize(
    "error,shown",
    [
        ((403, "Forbidden"), "Forbidden"),
        ((422, [{"loc": ["body", "hours_per_week"], "msg": "too large", "type": "x"}]), "hours_per_week: too large"),
    ],
)
def test_dialog_shows_403_and_422(browser, error, shown):
    api = FakeApi()
    api.post_error = error
    ctx, pg, dlg, problems = open_dialog_from_project(browser, api)
    dlg.get_by_role("button", name="+ Allocation").click()
    dlg.locator("form.al-form").get_by_role("button", name="Save").click()
    expect(dlg.locator("[role=alert]")).to_have_text(shown)
    assert problems == []
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
