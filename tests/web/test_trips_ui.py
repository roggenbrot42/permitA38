"""Browser tests of the business trips page. The API is intercepted with the browser context's routes: the trip,
preview and checklist fixtures are the examples of docs/contracts/trips.md, so these tests run without the backend.
Demo data only."""

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

# the example Trip of the contract (verbatim where the contract gives values; days are the three days it implies)
DAY_09 = {
    "date": "2026-11-09",
    "country": "DE",
    "city": "Hamburg",
    "absence_hours": 16.0,
    "base": 14.0,
    "reductions": [],
    "amount": 14.0,
    "note": "",
}
DAY_10 = {
    "date": "2026-11-10",
    "country": "DE",
    "city": "Hamburg",
    "absence_hours": 24.0,
    "base": 28.0,
    "reductions": [{"meal": "breakfast", "amount": 5.6}],
    "amount": 22.4,
    "note": "",
}
DAY_11 = {
    "date": "2026-11-11",
    "country": "AT",
    "city": "Wien",
    "absence_hours": 18.0,
    "base": 14.0,
    "reductions": [],
    "amount": 14.0,
    "note": "",
}
CHECKLIST = [
    {
        "id": "allowance",
        "label": "Daily allowance",
        "hint": "Computed from the trip itself; no receipts needed.",
        "state": "added",
        "count": 3,
        "sum": 56.0,
    },
    {
        "id": "accommodation",
        "label": "Hotel / accommodation",
        "hint": "Itemise the breakfast and the city tax.",
        "state": "open",
        "count": 0,
        "sum": 0,
    },
    {
        "id": "transportation",
        "label": "Transportation",
        "hint": "Train, flight, taxi, parking, and the mileage table.",
        "state": "added",
        "count": 2,
        "sum": 86.55,
    },
    {
        "id": "food",
        "label": "Food",
        "hint": "Meals while travelling are covered by the allowance.",
        "state": "not_needed",
        "count": 0,
        "sum": 0,
    },
    {
        "id": "material",
        "label": "Material",
        "hint": "Equipment, printing, samples bought for the trip.",
        "state": "not_needed",
        "count": 0,
        "sum": 0,
    },
    {
        "id": "cost_centre",
        "label": "Cost centre / project",
        "hint": "Choose a customer or project, or general.",
        "state": "added",
        "count": 1,
        "sum": 0,
    },
]
CONTRACT_TRIP = {
    "id": "t7f3k2m9qa",
    "created": "2026-10-23T09:12:44Z",
    "updated": "2026-10-23T09:12:44Z",
    "user_id": "u1a2b3c4d5",
    "title": "Customer visit Hamburg",
    "purpose": "Workshop at the customer's site",
    "customer_id": "k3j9x0ab12",
    "project_id": "p8d2m4qz7w",
    "start": "2026-11-09T08:00",
    "end": "2026-11-11T18:00",
    "places": [
        {"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-11-11", "country": "AT", "city": "Wien"},
    ],
    "meals_provided": [{"date": "2026-11-10", "breakfast": True, "lunch": False, "dinner": False}],
    "mileage": [{"date": "2026-11-09", "from": "Berlin", "to": "Hamburg", "km": 288.5, "purpose": "Outbound"}],
    "cost_centre": "project",
    "not_needed": ["material", "food"],
    "status": "draft",
    "allowance_total": 56.0,
    "mileage_km": 288.5,
    "mileage_total": 86.55,
    "rules_version": "2025-12-05",
    "days": [DAY_09, DAY_10, DAY_11],
    "bills": {"count": 2, "net": 180.0, "vat": 12.6},
    "checklist": CHECKLIST,
}
ME = {
    "id": "u1a2b3c4d5",
    "name": "Demo Boss",
    "role": "boss",
    "permissions": {
        "trips": {"read": "all", "write": "all"},
        "expenses": {"read": "all", "write": "all"},
        "customers": {"read": "all", "write": "all"},
        "projects": {"read": "all", "write": "all"},
    },
}
ACCOUNTANT = {
    "id": "u9z8y7x6w5",
    "name": "Demo Accountant",
    "role": "accountant",
    "permissions": {
        "trips": {"read": "all", "write": "none"},
        "customers": {"read": "all", "write": "none"},
        "projects": {"read": "all", "write": "none"},
    },
}
CUSTOMERS = [{"id": "k3j9x0ab12", "name": "Demo GmbH"}]
PROJECTS = [{"id": "p8d2m4qz7w", "name": "Demo project", "status": "active"}]


def preview_response(body):
    """What POST /trips/preview returns for the three-day example trip (Mon-Wed), computed from the body's meals."""
    if (body["start"], body["end"]) != ("2026-11-09T08:00", "2026-11-11T18:00"):
        return 422, {"detail": "fixture covers the three-day example only"}
    meals = {m["date"]: m for m in body.get("meals_provided", [])}
    day10 = copy.deepcopy(DAY_10)
    if meals.get("2026-11-10", {}).get("breakfast"):
        pass  # keep the contract's day 10: breakfast provided, 5,60 reduction, 22,40 left
    else:
        day10.update(reductions=[], amount=28.0)
    days = [copy.deepcopy(DAY_09), day10, copy.deepcopy(DAY_11)]
    km = sum(m["km"] for m in body.get("mileage", []))
    allowance = round(sum(d["amount"] for d in days), 2)
    return 200, {
        "days": days,
        "allowance_total": allowance,
        "mileage_km": km,
        "mileage_rate": 0.3,
        "mileage_total": round(km * 0.3 + 1e-9, 2),
        "rules_version": "2025-12-05",
    }


class FakeTripApi:
    """One trip (or none) in memory, the routes of docs/contracts/trips.md, and a log of every request."""

    def __init__(self, me, trip=None):
        self.me = me
        self.trip = trip
        self.requests: list[tuple[str, str, str, dict | None]] = []
        self.confirm_answer = True
        self.dialogs: list[str] = []

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        force = parse_qs(url.query).get("force", [""])[0]
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, force, body))
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
                    "templates": {},
                    "data_dir": "",
                    "features": {},
                },
            )
        if path == "customers":
            return _json(route, 200, CUSTOMERS)
        if path == "projects":
            return _json(route, 200, PROJECTS)
        if path == "expenses":
            return _json(route, 200, [])
        if path == "trips" and req.method == "GET":
            return _json(route, 200, [self.trip] if self.trip else [])
        if path == "trips/preview":
            if self.me["role"] == "accountant":
                return _json(route, 403, {"detail": "Forbidden"})
            status, payload = preview_response(body)
            return _json(route, status, payload)
        if path == "tasks" and req.method == "GET":
            return _json(route, 200, [])
        if path == "trips" and req.method == "POST":
            self.trip = copy.deepcopy(CONTRACT_TRIP)
            return _json(route, 200, self.trip)
        m = re.fullmatch(r"trips/([0-9a-z]{6,32})(/reopen)?", path)
        if not m or not self.trip or m.group(1) != self.trip["id"]:
            return _json(route, 404, {"detail": "Not found"})
        if m.group(2):  # reopen
            self.trip = {**self.trip, "status": "draft"}
            return _json(route, 200, self.trip)
        if req.method == "GET":
            return _json(route, 200, self.trip)
        if req.method == "DELETE":
            self.trip = None
            return route.fulfill(status=204, body="")
        if req.method == "PUT":
            if self.trip["status"] == "final":
                return _json(route, 409, {"detail": "This trip is final; the boss can reopen it"})
            open_labels = [i["label"] for i in self.trip["checklist"] if i["state"] == "open"]
            if body["status"] == "final" and open_labels and force != "true":
                return _json(route, 409, {"detail": "Checklist has open items: " + ", ".join(open_labels)})
            self.trip = {**self.trip, "status": body["status"], "purpose": body["purpose"]}
            return _json(route, 200, self.trip)
        return _json(route, 405, {"detail": "Method not allowed"})


def _json(route, status, payload):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _static(route):
    path = urlparse(route.request.url).path
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    f = WEB / name
    if not f.is_file():
        return route.fulfill(status=404, body="not found")
    route.fulfill(
        status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes()
    )


def open_app(browser, api):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    ctx.route("**/*", _static)  # registered first: the API route below takes precedence for /api/
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))

    def on_dialog(d):
        api.dialogs.append(d.message)
        d.accept() if api.confirm_answer else d.dismiss()

    pg.on("dialog", on_dialog)
    return ctx, pg, problems


def open_trips_page(pg):
    pg.goto(ORIGIN + "/#/trips")
    pg.wait_for_selector("nav.side a.nav")


def test_list_shows_period_destination_purpose_and_figures(browser):
    api = FakeTripApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    expect(pg.locator("nav.side")).to_contain_text("Business trips")
    row = pg.locator("table.trip-list tr", has_text="Workshop at the customer's site")
    expect(row).to_contain_text("09.11.2026 – 11.11.2026")
    expect(row).to_contain_text("Hamburg (Germany)")
    expect(row).to_contain_text("56,00")
    expect(row).to_contain_text("288,5 km")
    expect(row).to_contain_text("180,00")
    expect(row).to_contain_text("Draft")
    expect(pg.locator("[data-a=newTrip]")).to_have_count(1)
    assert problems == []
    ctx.close()


def test_create_dialog_breakdown_follows_meal_checkbox_and_saves(browser):
    api = FakeTripApi(ME, None)
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    pg.click("[data-a=newTrip]")
    pg.wait_for_selector("#t_purpose")
    pg.fill("#t_start", "2026-11-09T08:00")
    pg.fill("#t_end", "2026-11-11T18:00")
    pg.fill("#t_purpose", "Workshop at the customer's site")
    expect(pg.locator("#t_breakdown")).to_contain_text("56,00")
    expect(pg.locator("#t_breakdown")).to_contain_text(
        "Mon 09.11."
    )  # every figure explained in words, not in a tooltip
    expect(pg.locator('tr[data-day="2026-11-10"] [data-f=country]')).to_have_value("Germany (DE)")
    before = len(api.calls("POST", "trips/preview"))
    pg.check('tr[data-day="2026-11-10"] [data-f=breakfast]')
    expect(pg.locator("#t_breakdown")).to_contain_text("50,40")
    expect(pg.locator("#t_breakdown")).to_contain_text("breakfast provided 5,60")
    last = api.calls("POST", "trips/preview")[-1]
    assert len(api.calls("POST", "trips/preview")) > before
    assert last[3]["meals_provided"] == [{"date": "2026-11-10", "breakfast": True, "lunch": False, "dinner": False}]
    assert last[3]["places"] == [{"from_date": "2026-11-09", "country": "DE"}]
    pg.click("[data-a=tripSave]")
    expect(pg.locator("#t_checklist")).to_contain_text("Hotel / accommodation")
    post = api.calls("POST", "trips")[0][3]
    assert post["purpose"] == "Workshop at the customer's site" and post["status"] == "draft"
    assert "allowance_total" not in post and "checklist" not in post  # response-only fields are not sent
    assert problems == []
    ctx.close()


def test_checklist_open_line_add_bill_preselects_trip(browser):
    api = FakeTripApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    pg.click("button[data-a=editTrip]")
    pg.wait_for_selector("#t_checklist .trip-check")
    line = pg.locator("#t_checklist li.check-item", has_text="Hotel / accommodation")
    expect(line).to_contain_text("Open")
    line.locator("button", has_text="Add bill").click()
    pg.wait_for_selector("#f_category")
    assert pg.input_value("#f_category") == "Accommodation"
    assert pg.input_value("#f_trip_id") == "t7f3k2m9qa"
    assert pg.input_value("#f_project_id") == "p8d2m4qz7w"
    assert "Transportation" in pg.locator("#f_category option").all_inner_texts()
    assert problems == []
    ctx.close()


def open_trip_with_open_lines(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["checklist"][2] = {**trip["checklist"][2], "state": "open", "count": 0, "sum": 0}
    api = FakeTripApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    pg.click("button[data-a=editTrip]")
    pg.wait_for_selector("[data-a=tripFinalize]")
    return api, ctx, pg, problems


def test_finalize_409_asks_then_retries_with_force(browser):
    api, ctx, pg, problems = open_trip_with_open_lines(browser)
    pg.click("[data-a=tripFinalize]")
    expect(pg.locator("[data-a=tripFinalize]")).to_have_count(0)  # the stored trip is final now
    puts = [(r[2], r[3]["status"]) for r in api.requests if r[0] == "PUT"]
    assert puts == [("", "final"), ("true", "final")]
    assert api.dialogs == [
        "Checklist has open items: Hotel / accommodation, Transportation\n\nFinalize anyway? "
        "The open lines stay open in the statement."
    ]
    expect(pg.locator("#modal-root .banner")).to_contain_text("final")
    assert problems == []
    ctx.close()


def test_finalize_declined_sends_no_force(browser):
    api, ctx, pg, problems = open_trip_with_open_lines(browser)
    api.confirm_answer = False
    pg.click("[data-a=tripFinalize]")
    expect(pg.locator("[data-a=tripFinalize]")).to_have_count(1)
    assert [(r[2]) for r in api.requests if r[0] == "PUT"] == [""]
    assert api.trip["status"] == "draft"
    ctx.close()


def test_accountant_reads_trips_read_only(browser):
    api = FakeTripApi(ACCOUNTANT, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    expect(pg.locator("nav.side")).to_contain_text("Business trips")
    expect(pg.locator("[data-a=newTrip]")).to_have_count(0)
    expect(pg.locator("main")).to_contain_text("Read-only for your role.")
    pg.click("button[data-a=editTrip]")
    pg.wait_for_selector("#t_purpose")
    assert pg.locator("#t_purpose").is_disabled()
    assert pg.locator("#t_breakdown").inner_text().find("56,00") >= 0
    for action in ("tripSave", "tripFinalize", "tripDelete", "tripReopen"):
        assert pg.locator(f"#modal-root [data-a={action}]").count() == 0, action
    assert pg.locator("#modal-root [data-a=editExpense]").count() == 0
    assert pg.locator("#modal-root a", has_text="Statement (PDF)").count() == 1
    assert api.calls("POST", "trips/preview") == []  # no write right: no preview either
    assert [r for r in api.requests if r[0] in ("PUT", "POST", "DELETE")] == []
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_employee_without_expense_right_gets_no_add_bill_button(browser):
    employee = {
        "id": "u5e5e5e5e5",
        "name": "Demo Employee",
        "role": "employee",
        "permissions": {
            "trips": {"read": "limited", "write": "limited"},
            "projects": {"read": "limited", "write": "none"},
        },
    }
    api = FakeTripApi(employee, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trips_page(pg)
    pg.click("button[data-a=editTrip]")
    pg.wait_for_selector("#t_checklist .trip-check")
    expect(pg.locator("[data-a=tripSave]")).to_have_count(1)  # own trip: still editable
    assert pg.locator("#modal-root [data-a=editExpense]").count() == 0
    assert problems == []
    ctx.close()
