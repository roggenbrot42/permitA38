"""Browser tests of the contracts card on the project page (PAGES.project): which contracts are listed, the hint line
for the customer's contracts without a project, the "+ New contract" prefill, and the rights (read-only role, no
contracts right). The API is answered by a small in-test fake; all names and numbers are demo data."""

import json
import mimetypes
import pathlib
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"
AREAS = ["planning", "projects", "customers", "contracts", "absences", "offers", "team", "time", "invoices", "expenses"]
BOSS = {
    "id": "u1b2c3d4",
    "name": "Demo Boss",
    "role": "boss",
    "permissions": {a: {"read": "all", "write": "all"} for a in AREAS},
}
# the project page reads entries and expenses too (its own loadAll), so those rights are granted to the test roles
PROJECT_READ = {
    "projects": {"read": "all", "write": "all"},
    "customers": {"read": "all", "write": "none"},
    "time": {"read": "all", "write": "none"},
    "expenses": {"read": "all", "write": "none"},
}
CUST = "cu000001"
OTHER_CUST = "cu000002"
PROJECTS = [
    {"id": "pr000001", "name": "Demo project P", "status": "active", "customer_id": CUST, "personal": False},
    {"id": "pr000002", "name": "Demo project Q", "status": "active", "customer_id": CUST, "personal": False},
]
CUSTOMERS = [{"id": CUST, "name": "Demo GmbH"}, {"id": OTHER_CUST, "name": "Other AG"}]
CONTRACTS = [
    {
        "id": "ct000001",
        "title": "Demo contract Alpha",
        "customer_id": CUST,
        "project_id": "pr000001",
        "start": "2026-03-01",
        "end": "2026-06-30",
        "hours_per_week": 8,
        "rate": 90,
        "status": "confirmed",
        "user_ids": [BOSS["id"]],
    },
    {
        "id": "ct000002",
        "title": "Demo contract Early",
        "customer_id": CUST,
        "project_id": "pr000001",
        "start": "2026-01-15",
        "end": "2026-02-28",
        "total_hours": 40,
        "rate": None,
        "status": "done",
        "user_ids": [BOSS["id"]],
    },
    {
        "id": "ct000003",
        "title": "Demo contract Beta",
        "customer_id": CUST,
        "project_id": "pr000002",
        "start": "2026-01-01",
        "end": "2026-12-31",
        "total_hours": 100,
        "rate": 80,
        "status": "confirmed",
        "user_ids": [BOSS["id"]],
    },
    {
        "id": "ct000004",
        "title": "Demo contract Gamma",
        "customer_id": CUST,
        "project_id": None,
        "start": "2026-04-01",
        "end": "2026-05-31",
        "hours_per_week": 4,
        "rate": 70,
        "status": "confirmed",
        "user_ids": [BOSS["id"]],
    },
    {
        "id": "ct000005",
        "title": "Demo contract Delta",
        "customer_id": OTHER_CUST,
        "project_id": None,
        "start": "2026-04-01",
        "end": "2026-05-31",
        "hours_per_week": 4,
        "rate": 70,
        "status": "confirmed",
        "user_ids": [BOSS["id"]],
    },
]


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
    def __init__(self, me=BOSS):
        self.me = me
        self.contracts = json.loads(json.dumps(CONTRACTS))
        self.requests = []

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path.split("/api/", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, body))

        def ok(payload, status=200):
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

        if path == "bootstrap":
            return ok(
                {
                    "me": self.me,
                    "settings": {},
                    "timer": None,
                    "states": {},
                    "conflicts": [],
                    "templates": [],
                    "data_dir": "",
                    "features": {},
                }
            )
        if path == "users":
            return ok(
                [
                    {
                        "id": BOSS["id"],
                        "username": "boss",
                        "name": "Demo Boss",
                        "role": "boss",
                        "weekly_hours": 40,
                        "active": True,
                    }
                ]
            )
        if path == "projects":
            return ok(PROJECTS)
        if path == "customers":
            return ok(CUSTOMERS)
        if path == "contracts" and req.method == "POST":
            row = {**body, "id": "ct%06d" % (len(self.contracts) + 1)}
            self.contracts.append(row)
            return ok(row, 201)
        if path == "contracts":
            return ok(self.contracts)
        if path == "project-report":
            return ok({})
        if path.startswith("projects/") and path.endswith(
            "/planning"
        ):  # planner.js appends its box; no planning data here
            return route.fulfill(status=404, content_type="application/json", body='{"detail": "Not found"}')
        if path in ("tasks", "entries", "expenses", "absences", "offers", "invoices"):
            return ok([])
        return ok([])


def open_app(browser, api, page="project/pr000001"):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("console", lambda m: m.type == "error" and "status of" not in m.text and problems.append(m.text))
    pg.on("dialog", lambda d: (problems.append("dialog: " + d.message), d.dismiss()))
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("h1")
    return ctx, pg, problems


def contracts_card(pg):
    return (
        pg.locator("div.card")
        .filter(has=pg.locator("h2", has_text="Contracts"))
        .filter(has_not=pg.locator("h2", has_text="Tasks"))
    )


def test_project_lists_its_contracts_and_counts_the_unassigned_ones(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    card = contracts_card(pg)
    rows = card.locator("tr[data-id]")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator("td").nth(0)).to_have_text("Demo contract Early")  # sorted by start
    expect(rows.nth(1).locator("td").nth(0)).to_have_text("Demo contract Alpha")
    expect(card).to_contain_text("15.01.2026 – 28.02.2026")
    expect(card).to_contain_text("8 h/week")
    expect(card).to_contain_text("40 h total")
    expect(card).not_to_contain_text("Demo contract Beta")  # belongs to project Q
    expect(card).to_contain_text("1 more contract of Demo GmbH without a project")  # Gamma; Delta is another customer
    expect(card).not_to_contain_text("Demo contract Gamma")
    expect(card.locator("a[href='#/plan/contracts']")).to_have_count(1)
    assert problems == []
    ctx.close()


def test_new_contract_is_prefilled_with_project_and_customer_and_shows_up(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    contracts_card(pg).locator("button[data-a=editContract][data-proj=pr000001]").click()
    pg.wait_for_selector("#f_title")
    expect(pg.locator("#f_project_id")).to_have_value("pr000001")
    expect(pg.locator("#f_customer_id")).to_have_value(CUST)
    pg.fill("#f_title", "Demo contract New")
    pg.fill("#f_start", "2026-05-01")
    pg.fill("#f_end", "2026-07-31")
    pg.fill("#f_hours_per_week", "5")
    pg.click("#m-save")
    expect(contracts_card(pg).locator("tr[data-id]")).to_have_count(3)
    expect(contracts_card(pg)).to_contain_text("Demo contract New")
    posted = api.calls("POST", "contracts")
    assert len(posted) == 1
    assert posted[0][2]["project_id"] == "pr000001"
    assert posted[0][2]["customer_id"] == CUST
    assert problems == []
    ctx.close()


def test_read_only_role_sees_table_but_no_write_controls(browser):
    me = {**BOSS, "role": "employee", "permissions": {**PROJECT_READ, "contracts": {"read": "all", "write": "none"}}}
    api = FakeApi(me=me)
    ctx, pg, problems = open_app(browser, api)
    card = contracts_card(pg)
    expect(card.locator("td").first).to_be_visible()
    expect(card.locator("tr[data-id]")).to_have_count(2)
    assert card.locator("button[data-a=editContract]").count() == 0
    assert card.locator("tr[data-a=editContract]").count() == 0
    assert card.locator("tr.click").count() == 0
    assert problems == []
    ctx.close()


def test_role_without_contracts_right_has_no_card_and_page_loads(browser):
    me = {**BOSS, "role": "employee", "permissions": PROJECT_READ}
    api = FakeApi(me=me)
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("h1")).to_contain_text("Demo project P")
    expect(pg.locator("h2", has_text="Tasks & task rates")).to_be_visible()
    assert pg.locator("h2", has_text="Contracts").count() == 0
    assert api.calls("GET", "contracts") == []
    assert problems == []
    ctx.close()
