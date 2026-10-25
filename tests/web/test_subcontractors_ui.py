"""Browser tests of suppliers, incoming offers, expense links, commitments and the planner's external numbers.
The API is intercepted with the browser context's routes; the answers are the worked examples of
docs/contracts/subcontractors.md (extracted verbatim into tests/web/fixtures/subcontractors/), so these tests run
without the backend. The UI never computes capacity, gaps or money: every number asserted here comes from a fixture."""

import copy
import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_planner_per_project_ui import (  # noqa: E402
    ORIGIN,
    _json,
    _static,
    legacy_week,
)
from tests.web.test_planner_per_project_ui import PROJECTS  # noqa: E402

import pathlib  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "subcontractors"

AREAS = ["planning", "projects", "customers", "contracts", "absences", "offers", "team", "time", "invoices", "expenses"]


def fixture(name):
    return json.loads((FIX / f"{name}.json").read_text())


def perms(**over):
    p = {a: {"read": "all", "write": "all"} for a in AREAS}
    p["finance"] = {"read": "all", "write": "all"}
    p["team_timeline"] = {"read": "all", "write": "none"}
    p["suppliers"] = {"read": "all", "write": "all"}
    p["procurement"] = {"read": "all", "write": "all"}
    p.update(over)
    return p


BOSS = {"id": "u1b2c3d4", "name": "Demo Boss", "role": "boss", "permissions": perms()}
ACCOUNTANT = {
    "id": "u9a8b7c6",
    "name": "Demo Accountant",
    "role": "accountant",
    "permissions": {
        "customers": {"read": "all", "write": "none"},
        "expenses": {"read": "all", "write": "all"},
        "finance": {"read": "all", "write": "none"},
        "suppliers": {"read": "all", "write": "all"},
        "procurement": {"read": "all", "write": "none"},
    },
}
EMPLOYEE = {
    "id": "u5e6f7a8",
    "name": "Demo Employee",
    "role": "employee",
    "permissions": {
        "time": {"read": "all", "write": "all"},
        "timer": {"read": "all", "write": "all"},
        "suppliers": {"read": "limited", "write": "none"},
        "procurement": {"read": "none", "write": "none"},
    },
}
USER = {"id": "u1b2c3d4", "username": "boss", "name": "Demo Boss", "role": "boss", "weekly_hours": 40, "active": True}

SUP_S = {
    "id": "5d1c0aa2",
    "name": "Example Freelance Studio (Demo)",
    "supplier_no": "L-0003",
    "kind": "person-capacity",
    "active": True,
    "country": "DE",
    "vat_mode": "domestic",
    "tags": ["frontend"],
    "weekly_hours": 24.0,
    "workdays": [0, 1, 2, 3],
    "rate": 80.0,
    "unavailable": [],
    "iban": "DE02120300000000202051",
    "email": "studio@example.invalid",
}
SUP_P = {
    "id": "7a1b2c3d",
    "name": "Demo Print Shop GmbH",
    "supplier_no": "L-0004",
    "kind": "company",
    "active": True,
    "country": "DE",
    "vat_mode": "domestic",
    "tags": [],
    "iban": "DE89370400440532013000",
}
SUP_RC = {**SUP_P, "id": "8b2c3d4e", "name": "Example Cloud Ltd (Demo)", "country": "IE", "vat_mode": "eu_reverse_charge", "supplier_no": "L-0005"}


def offer_w1():
    return fixture("offer_w1")


def offer_flat():
    o = fixture("offer_w7_domestic")
    o.update(
        id="9b3e51c8",
        supplier_id="7a1b2c3d",
        supplier_name="Demo Print Shop GmbH",
        reference="L-77",
        title="Print run",
        received_on="2026-02-21",
        valid_until="2099-12-31",
        lines=[{"description": "Printing", "quantity": 37.5, "unit": "h", "unit_price": 82.45}],
        plan=None,
        project_id=None,
        project_name=None,
        status="received",
        effective_status="received",
        allocation_id=None,
    )
    return o


def forecast():
    f = {"weeks": [], "totals": copy.deepcopy(fixture("forecast_totals_w4")), "warnings": []}
    f["weeks"] = [legacy_week(fixture("forecast_week_w4"), i) for i in range(4)]
    f["totals"].update(free_worst_case=40.0)
    return f


def forecast_without_external():
    f = forecast()
    for w in f["weeks"]:
        w.update(external_capacity=0.0, external_assigned=0.0, external_gap=0.0, external_cost=0.0, external_by_project=[])
    f["totals"].update(external_capacity=0.0, external_gap=0.0, external_cost=0.0, margin=20000.0)
    return f


class FakeApi:
    def __init__(self, me=None, suppliers=None, offers=None):
        self.me = me or BOSS
        self.suppliers = copy.deepcopy(suppliers if suppliers is not None else [SUP_S, SUP_P, SUP_RC])
        self.offers = copy.deepcopy(offers if offers is not None else [offer_w1(), offer_flat()])
        self.expenses = []
        self.accept_mode = "ok"  # ok | confirm | expired
        self.requests = []
        self.vendor_suggestions = fixture("vendor_suggestions")
        self.cancelled = None
        self.commitments = fixture("commitments")
        self.allocation_list = [
            {**fixture("accept_w1")["allocation"], "project_name": "Demo project P"},
            {
                "id": "e1111111",
                "kind": "employee",
                "user_id": "u1b2c3d4",
                "user_name": "Demo Boss",
                "project_id": "a1b2c3d4",
                "project_name": "Demo project P",
                "start": "2026-03-02",
                "end": "2026-03-27",
                "hours_per_week": 40.0,
                "planned_hours": 160.0,
                "status": "active",
            },
        ]

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        query = parse_qs(url.query)
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, query, body))
        m = req.method
        me = self.me
        if path == "bootstrap":
            return _json(
                route,
                200,
                {"me": me, "settings": {}, "timer": None, "states": {}, "conflicts": [], "templates": [], "data_dir": "", "features": {}},
            )
        if path == "projects":
            return _json(route, 200, copy.deepcopy(PROJECTS))
        if path == "users":
            return _json(route, 200, [USER])
        if path == "contracts":
            return _json(
                route,
                200,
                [
                    {
                        "id": "d4e5f6a7",
                        "title": "Contract C_P",
                        "project_id": "a1b2c3d4",
                        "status": "confirmed",
                        "start": "2026-03-02",
                        "end": "2026-03-27",
                        "hours_per_week": 50.0,
                        "rate": 100.0,
                        "user_ids": ["u1b2c3d4"],
                    }
                ],
            )
        if path == "expenses" and m == "GET":
            return _json(route, 200, self.expenses)
        if path == "expenses" and m == "POST":
            row = {**body, "id": "ee000001"}
            self.expenses.append(row)
            return _json(route, 200, row)
        if path.startswith("projects/") and path.endswith("/planning"):
            return _json(route, 404, {"detail": "Not found"})
        if path in ("customers", "absences", "offers", "tasks", "entries", "invoices", "trips"):
            return _json(route, 200, [])
        if path == "holidays":
            return _json(route, 200, {})
        if path == "plan/scenarios":
            return _json(route, 200, [])
        if path == "forecast":
            return _json(route, 200, forecast() if query.get("include_external") != ["0"] else forecast_without_external())
        if path == "plan/day-table":
            return _json(route, 200, {"days": [], "scope_names": {}})
        if path == "plan/simulate":
            return _json(route, 200, forecast() if (body or {}).get("include_external", True) else forecast_without_external())
        if path == "team/timeline":
            start = query["start"][0]
            ext = fixture("team_timeline_external")  # the contract's windows are in March 2026: moved into the shown window
            for x in ext["external"]:
                x["windows"] = [{"start": start, "end": (date.fromisoformat(start) + timedelta(days=4)).isoformat()}]
            return _json(route, 200, {"level": "full", "start": start, "end": query["end"][0], "people": [], "holidays": {}, **ext})
        if path == "finance":
            return _json(route, 200, self.finance(query))
        if path == "finance/commitments":
            c = copy.deepcopy(self.commitments)
            if query.get("supplier_id"):
                c["items"] = [i for i in c["items"] if i["supplier_id"] == query["supplier_id"][0]]
            return _json(route, 200, c)
        # ---- suppliers
        if path == "suppliers" and m == "GET":
            rows = self.suppliers
            if query.get("kind"):
                rows = [s for s in rows if s["kind"] == query["kind"][0]]
            if query.get("active") == ["true"]:
                rows = [s for s in rows if s.get("active", True)]
            if query.get("tag"):
                rows = [s for s in rows if query["tag"][0] in s.get("tags", [])]
            if query.get("q"):
                rows = [s for s in rows if query["q"][0].lower() in s["name"].lower()]
            if me["role"] == "employee":
                rows = [fixture("supplier_employee_view") if s["id"] == "5d1c0aa2" else {k: s[k] for k in ("id", "name", "kind", "tags", "active")} for s in rows]
            return _json(route, 200, copy.deepcopy(rows))
        if path == "suppliers" and m == "POST":
            row = {**body, "id": "5a5a5a5a"}
            self.suppliers.append(row)
            return _json(route, 201, {**row, "sanity_warnings": []})
        if path == "suppliers/vendor-suggestions":
            return _json(route, 200, self.vendor_suggestions)
        if path == "suppliers/adopt-vendors":
            out = fixture("adopt_dry_run")
            out["dry_run"] = body["dry_run"]
            if not body["dry_run"]:
                self.vendor_suggestions = {"groups": [], "unlinked_expenses": 0}
            return _json(route, 200, out)
        if path.startswith("suppliers/"):
            parts = path.split("/")
            s = next((x for x in self.suppliers if x["id"] == parts[1]), None)
            if s is None:
                return _json(route, 404, {"detail": "Not found"})
            if len(parts) == 3 and parts[2] == "dependency":
                return _json(route, 200, fixture("dependency"))
            if m == "GET":
                if me["role"] == "employee":
                    return _json(route, 200, fixture("supplier_employee_view"))
                return _json(route, 200, s)
            if m == "PUT":
                s.update(body)
                return _json(route, 200, {**s, "sanity_warnings": []})
            if m == "DELETE":
                return _json(route, 409, {"detail": fixture("errors")[4]["detail"]})
        # ---- incoming offers
        if path == "incoming-offers" and m == "GET":
            rows = self.offers
            if query.get("supplier_id"):
                rows = [o for o in rows if o["supplier_id"] == query["supplier_id"][0]]
            return _json(route, 200, rows)
        if path == "incoming-offers" and m == "POST":
            row = {**fixture("offer_w1"), **body, "id": "9b3e5999", "status": "received", "effective_status": "received", "allocation_id": None}
            self.offers.append(row)
            return _json(route, 201, {**row, "planning_warnings": []})
        if path.startswith("incoming-offers/"):
            parts = path.split("/")
            o = next((x for x in self.offers if x["id"] == parts[1]), None)
            if o is None:
                return _json(route, 404, {"detail": "Not found"})
            if len(parts) == 2:
                if m == "GET":
                    return _json(route, 200, o)
                if m == "PUT":
                    o.update(body)
                    return _json(route, 200, o)
                if m == "DELETE":
                    self.offers.remove(o)
                    return route.fulfill(status=204)
            act = parts[2]
            if act == "accept":
                return self.accept(route, o, body)
            new = {"review": "under_review", "reject": "rejected", "reopen": "under_review", "unaccept": "under_review"}[act]
            o.update(status=new, effective_status=new)
            if act == "reject":
                o["decision_note"] = body.get("reason")
            if act == "unaccept":
                o["allocation_id"] = None
            return _json(route, 200, o)
        if path.startswith("allocations/") and path.endswith("/cancel"):
            self.cancelled = body
            a = fixture("allocation_cancelled_w9")
            for x in self.allocation_list:
                if x["id"] == a["id"]:
                    x.update(a)
            return _json(route, 200, a)
        if path.startswith("allocations/") and m == "GET":
            aid = path.split("/")[1]
            a = next(x for x in self.allocation_list if x["id"] == aid)
            return _json(route, 200, {**a, **fixture("allocation_days_w1")})
        if path == "allocations" and m == "GET":
            return _json(route, 200, self.allocation_list)
        return _json(route, 200, [])

    def finance(self, query):
        months = [{"revenue": 0, "expenses": 0, "profit": 0, "vat_due": 0, "tax_set_aside": 0, "vat_set_aside": 0, "social": 0, "take_home": 0} for _ in range(12)]
        z = {"est": 0, "soli": 0, "total": 0, "kst": 0, "church": 0, "gewst": 0}
        return {
            "months": months,
            "totals": {"revenue": 0, "expenses": 0, "profit": 0, "vat_due": 0},
            "tax_actual": z,
            "tax_projected": z,
            "warnings": [],
            "company_type": "freiberufler",
            "kleinunternehmer": False,
            "open_receivables": 0,
            "categories": {},
            "social_year": 0,
            "take_home": 0,
            "projected_profit": 0,
            "effective_rate": 0,
        }

    def accept(self, route, o, body):
        errs = fixture("errors")
        if self.accept_mode == "project_422":
            return _json(route, 422, {"detail": errs[1]["detail"]})
        if self.accept_mode == "expired" and not body.get("confirm_expired"):
            return _json(route, 409, {"detail": errs[0]["detail"]})
        if self.accept_mode == "confirm" and "allocation.exceeds_weekly_hours" not in body.get("confirm_warnings", []):
            return _json(route, 409, fixture("accept_409_weekly_hours"))
        if o["supplier_id"] != "5d1c0aa2":
            o.update(status="accepted", effective_status="accepted")
            return _json(
                route,
                200,
                {
                    "offer": o,
                    "allocation": None,
                    "commitment": {"kind": "flat", "planned_net": 3091.88, "open_net": 3091.88},
                    "planning_warnings": [],
                },
            )
        if not (body.get("project_id") or o.get("project_id")):
            return _json(route, 422, {"detail": errs[1]["detail"]})
        r = copy.deepcopy(fixture("accept_w1"))
        if self.accept_mode == "confirm":
            r["planning_warnings"].append(fixture("warning_w11"))
        o.update(r["offer"])
        self.accepted_body = body
        return _json(route, 200, r)


def open_app(browser, api, page="suppliers", storage=None):
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
    pg.confirms = []

    def on_dialog(d):
        pg.confirms.append(d.message)
        d.accept()

    pg.on("dialog", on_dialog)
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems


# ---------------------------------------------------------------- suppliers
def test_supplier_list_shows_commitments_and_filters(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    rows = pg.locator("#sup-table tr[data-id]")
    expect(rows).to_have_count(3)
    row = pg.locator('#sup-table tr[data-id="5d1c0aa2"]')
    expect(row).to_contain_text("Example Freelance Studio (Demo)")
    expect(row).to_contain_text("Subcontractor")
    expect(row).to_contain_text("frontend")
    expect(row).to_contain_text("6.400,00")  # by_supplier open_net of the commitments fixture
    pg.select_option("#sup_kind", "person-capacity")
    pg.wait_for_function("() => localStorage.supKind === 'person-capacity'")
    expect(pg.locator("#sup-table tr[data-id]")).to_have_count(1)
    q = api.calls("GET", "suppliers")[-1][2]
    assert q["kind"] == ["person-capacity"] and q["active"] == ["true"]
    pg.select_option("#sup_kind", "")
    pg.fill("#sup_q", "print")
    pg.press("#sup_q", "Enter")
    expect(pg.locator("#sup-table tr[data-id]")).to_have_count(1)
    assert api.calls("GET", "suppliers")[-1][2]["q"] == ["print"]
    assert problems == []
    ctx.close()


def test_supplier_detail_shows_dependency_hint_and_commitments(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="suppliers/5d1c0aa2")
    expect(pg.locator("h1")).to_contain_text("Example Freelance Studio (Demo)")
    expect(pg.locator(".sup-dependency")).to_contain_text("30 h per week on average for 12 weeks")
    assert pg.locator(".sup-dependency").get_attribute("data-raised") == "1"
    expect(pg.locator("main")).to_contain_text("DE02120300000000202051")
    expect(pg.locator("main")).to_contain_text("Mon, Tue, Wed, Thu")
    expect(pg.locator("main")).to_contain_text("ANG-2026-117")
    expect(pg.locator("main")).to_contain_text("6.400,00")
    assert problems == []
    ctx.close()


def test_new_subcontractor_form_sends_capacity_fields(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=supNew]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#sup_cap")).to_be_hidden()
    pg.fill("#f_name", "Demo Dev Collective")
    pg.select_option("#f_kind", "person-capacity")
    expect(dlg.locator("#sup_cap")).to_be_visible()
    pg.fill("#f_tags", "backend, Demo")
    dlg.get_by_role("button", name="Save").click()
    assert pg.locator("#toast").inner_text() == "A subcontractor needs the hours per week they can offer"  # refused before any request
    assert not api.calls("POST", "suppliers")
    pg.fill("#sup_weekly", "24")
    pg.fill("#sup_rate", "80")
    pg.uncheck("#sup_wd4")
    pg.click("[data-a=supWinAdd]")
    pg.fill("#sup_uf0", "2026-03-09")
    pg.fill("#sup_ut0", "2026-03-11")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "suppliers")[0][3]
    assert body["kind"] == "person-capacity" and body["weekly_hours"] == 24 and body["rate"] == 80
    assert body["workdays"] == [0, 1, 2, 3]
    assert body["unavailable"] == [{"from": "2026-03-09", "to": "2026-03-11", "note": None}]
    assert body["tags"] == ["backend", "Demo"] and body["vat_mode"] is None and body["country"] == "DE"
    assert problems == []
    ctx.close()


def test_company_form_sends_no_capacity_and_escape_closes(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api)
    pg.click("[data-a=supNew]")
    pg.fill("#f_name", "<b>Odd & Co</b>")
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    pg.click("[data-a=supNew]")
    pg.fill("#f_name", "<b>Odd & Co</b>")
    pg.fill("#f_iban", "DE89 3704 0044 0532 0130 00")
    pg.locator("#modal-root").get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "suppliers")[0][3]
    assert "weekly_hours" not in body and "workdays" not in body and body["name"] == "<b>Odd & Co</b>"
    expect(pg.locator("h1")).to_have_text("<b>Odd & Co</b>")  # shown as text
    assert pg.locator("h1 b").count() == 0
    assert problems == []
    ctx.close()


def test_employee_sees_the_limited_view_only(browser):
    api = FakeApi(me=EMPLOYEE)
    ctx, pg, problems = open_app(browser, api, page="suppliers")
    expect(pg.locator("nav.side")).to_contain_text("Suppliers")
    expect(pg.locator("nav.side")).not_to_contain_text("Incoming offers")
    expect(pg.locator("#sup-table")).to_contain_text("Example Freelance Studio (Demo)")
    expect(pg.locator("main")).not_to_contain_text("Open commitments")
    assert pg.locator("[data-a=supNew], [data-a=supAdopt]").count() == 0
    assert not api.calls("GET", "finance/commitments")
    pg.click('#sup-table tr[data-id="5d1c0aa2"] a')
    expect(pg.locator("h1")).to_contain_text("Example Freelance Studio (Demo)")
    expect(pg.locator("main")).to_contain_text("limited view")
    assert pg.locator("[data-a=supEdit], [data-a=supDelete], .sup-facts").count() == 0
    assert "DE02" not in pg.inner_text("main")
    assert not api.calls("GET", "suppliers/5d1c0aa2/dependency")
    assert problems == []
    ctx.close()


def test_delete_of_a_used_supplier_shows_the_server_message(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="suppliers/5d1c0aa2")
    pg.click("[data-a=supDelete]")
    expect(pg.locator("#toast")).to_have_text(fixture("errors")[4]["detail"])
    assert pg.confirms and "archive" in pg.confirms[0]
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- vendor adoption
def test_vendor_banner_and_adoption_check_then_link(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    banner = pg.locator(".sup-vendor-banner")
    expect(banner).to_contain_text("4 expenses have a vendor without a supplier")
    banner.get_by_role("button", name="Review and link").click()
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator(".sup-adopt tr")).to_have_count(3)  # header + 2 groups
    expect(dlg.locator("#ad_sup0")).to_have_value("7a1b2c3d")  # best existing supplier of the group
    expect(dlg.locator("#ad_sup1")).to_have_value("__new__")
    expect(dlg.locator("#ad_go")).to_be_disabled()
    dlg.locator("#ad_check").click()
    expect(dlg.locator("#ad_result")).to_contain_text("4 expenses would be linked, 1 supplier created")
    req = api.calls("POST", "suppliers/adopt-vendors")[0][3]
    assert req["dry_run"] is True
    assert req["items"][0] == {"vendors": ["Demo Print Shop GmbH", "demo print shop"], "supplier_id": "7a1b2c3d"}
    assert req["items"][1] == {"vendors": ["Example Hosting"], "create": {"name": "Example Hosting", "kind": "company"}}
    dlg.locator("#ad_use1").uncheck()  # any change invalidates the check
    expect(dlg.locator("#ad_go")).to_be_disabled()
    dlg.locator("#ad_check").click()
    expect(dlg.locator("#ad_go")).to_be_enabled()
    dlg.locator("#ad_go").click()
    expect(dlg.locator("#ad_result")).to_contain_text("Linked 4 expenses")
    assert [r[3]["dry_run"] for r in api.calls("POST", "suppliers/adopt-vendors")] == [True, True, False]
    assert len(api.calls("POST", "suppliers/adopt-vendors")[-1][3]["items"]) == 1
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_adoption_check_keeps_the_choices_that_link_then_sends(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.locator(".sup-vendor-banner").get_by_role("button", name="Review and link").click()
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#ad_sup0")).to_have_value("7a1b2c3d")
    dlg.locator("#ad_sup0").select_option("__new__")
    dlg.locator("#ad_name0").fill("Demo Print Studio")
    dlg.locator("#ad_check").click()
    expect(dlg.locator("#ad_result")).to_contain_text("created")
    expect(dlg.locator("#ad_sup0")).to_have_value("__new__")  # the repaint after the check keeps the choice
    expect(dlg.locator("#ad_name0")).to_have_value("Demo Print Studio")
    dlg.locator("#ad_go").click()
    expect(dlg.locator("#ad_result")).to_contain_text("Linked")
    calls = api.calls("POST", "suppliers/adopt-vendors")
    assert [c[3]["dry_run"] for c in calls] == [True, False]
    assert calls[0][3]["items"] == calls[1][3]["items"]  # the link sends what the check showed
    assert calls[0][3]["items"][0] == {"vendors": ["Demo Print Shop GmbH", "demo print shop"], "create": {"name": "Demo Print Studio", "kind": "company"}}
    assert problems == []
    ctx.close()


def test_no_vendor_banner_for_the_employee_and_when_all_linked(browser):
    api = FakeApi()
    api.vendor_suggestions = {"groups": [], "unlinked_expenses": 0}
    ctx, pg, problems = open_app(browser, api, page="expenses")
    assert pg.locator(".sup-vendor-banner").count() == 0
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- expense links
def test_expense_dialog_links_supplier_allocation_and_reverse_charge(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.click("[data-a=editExpense]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#f_allocation_id")).to_be_hidden()
    expect(dlg.locator("#f_reverse_charge")).to_be_hidden()
    pg.select_option("#f_supplier_id", "5d1c0aa2")
    expect(dlg.locator("#f_vendor")).to_have_value("Example Freelance Studio (Demo)")
    pg.wait_for_selector("#f_allocation_id option[value=c04d77e1]", state="attached")
    expect(dlg.locator("#f_allocation_id")).to_be_visible()
    assert api.calls("GET", "finance/commitments")[-1][2]["supplier_id"] == ["5d1c0aa2"]
    pg.select_option("#f_allocation_id", "c04d77e1")
    pg.fill("#f_gross", "1904")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "expenses")[0][3]
    assert body["supplier_id"] == "5d1c0aa2" and body["allocation_id"] == "c04d77e1" and body["reverse_charge"] is False
    assert body["vendor"] == "Example Freelance Studio (Demo)"
    # reverse charge supplier: flag proposed, VAT typed as self-assessed amount
    pg.click("[data-a=editExpense]")
    pg.select_option("#f_supplier_id", "8b2c3d4e")
    expect(pg.locator("#f_reverse_charge")).to_be_checked()
    expect(pg.locator("#f_allocation_id")).to_be_hidden()  # not a subcontractor
    pg.fill("#f_gross", "3091.88")
    pg.select_option("#f_vat_rate", "19")
    pg.click("[data-a=supRcPropose]")
    expect(pg.locator("#f_rc_vat")).to_have_value("587.46")
    pg.locator("#modal-root").get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "expenses")[1][3]
    assert body["reverse_charge"] is True and body["allocation_id"] is None
    assert body["net"] == 3091.88 and body["vat"] == 587.46 and body["vat_rate"] == 0
    assert problems == []
    ctx.close()


def test_expense_dialog_keeps_an_inactive_supplier_selectable(browser):
    inactive = {**SUP_P, "active": False}
    api = FakeApi(suppliers=[SUP_S, inactive])
    api.expenses = [{"id": "ee000009", "date": "2026-03-01", "vendor": "Demo Print Shop GmbH", "net": 10.0, "vat": 1.9, "vat_rate": 19, "deductible": 100, "supplier_id": "7a1b2c3d", "category": "Software"}]
    ctx, pg, problems = open_app(browser, api, page="expenses")
    expect(pg.locator("tr[data-id=ee000009]")).to_contain_text("supplier")
    pg.click("tr[data-id=ee000009] td >> nth=0")
    expect(pg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    assert pg.locator("#f_supplier_id option[value='7a1b2c3d']").inner_text().endswith("(inactive)")
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- incoming offers
def test_offer_list_tabs_and_detail_use_server_numbers(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming")
    expect(pg.locator("#off-table")).to_contain_text("ANG-2026-117")
    expect(pg.locator("#off-table")).to_contain_text("6.400,00")
    pg.click("[data-a=offTab][data-tab=accepted]")
    expect(pg.locator("main")).to_contain_text("No offers here")
    pg.click("[data-a=offTab][data-tab=open]")
    pg.click('#off-table tr[data-id="9b3e51c7"] a')
    amounts = pg.locator("#off-amounts")
    expect(amounts).to_contain_text("6.400,00")
    expect(amounts).to_contain_text("1.216,00")
    expect(amounts).to_contain_text("7.616,00")
    expect(pg.locator("main")).to_contain_text("Frontend development")
    expect(pg.locator("main")).to_contain_text("5c0ffee1.pdf")
    assert problems == []
    ctx.close()


def test_review_reject_and_reopen(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offReview]")
    expect(pg.locator("h1 .pill")).to_have_text("under review")
    pg.click("[data-a=offReject]")
    pg.fill("#f_reason", "too expensive")
    pg.locator("#modal-root").get_by_role("button", name="Reject").click()
    expect(pg.locator("h1 .pill")).to_have_text("rejected")
    assert api.calls("POST", "incoming-offers/9b3e51c7/reject")[0][3] == {"reason": "too expensive"}
    assert pg.locator("[data-a=offAccept]").count() == 0
    pg.click("[data-a=offReopen]")
    expect(pg.locator("h1 .pill")).to_have_text("under review")
    assert problems == []
    ctx.close()


def test_accept_capacity_offer_shows_server_result_and_warnings_once(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("80")
    expect(dlg.locator("#ac_project")).to_have_value("a1b2c3d4")
    assert dlg.locator("#ac_project option[value='']").count() == 0  # a capacity offer needs a project
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_result")).to_contain_text("80")
    expect(dlg.locator("#ac_result")).to_contain_text("6.400,00")
    expect(dlg.locator(".proc-warnings li[data-code='allocation.no_demand']")).to_contain_text("nothing is planned there")
    assert pg.locator("#plnWarn").count() == 0  # the dialog shows the warnings: no second banner behind it
    assert api.calls("POST", "incoming-offers/9b3e51c7/accept")[0][3] == {"confirm_warnings": [], "project_id": "a1b2c3d4"}
    dlg.locator("#ac_close").click()
    expect(pg.locator("h1 .pill")).to_have_text("accepted")
    expect(pg.locator(".proc-alloc")).to_contain_text("6.400,00")
    expect(pg.locator(".proc-alloc")).to_contain_text("16 days")
    assert pg.locator("[data-a=offDelete]").count() == 0
    assert problems == []
    ctx.close()


def test_accept_needs_confirmation_of_the_weekly_hours_warning(browser):
    api = FakeApi()
    api.accept_mode = "confirm"
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_error")).to_contain_text("Confirm these warnings first: allocation.exceeds_weekly_hours")
    expect(dlg.locator("#ac_error")).to_be_focused()  # the error is announced and focus stays inside the dialog
    box = dlg.locator("#ac_code0")
    expect(box).to_be_visible()
    assert box.get_attribute("data-code") == "allocation.exceeds_weekly_hours"
    box.check()
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_result")).to_be_visible()
    reqs = api.calls("POST", "incoming-offers/9b3e51c7/accept")
    assert reqs[0][3]["confirm_warnings"] == [] and reqs[1][3]["confirm_warnings"] == ["allocation.exceeds_weekly_hours"]
    expect(dlg.locator(".proc-warnings li[data-code='allocation.exceeds_weekly_hours']")).to_have_count(1)
    assert problems == []
    ctx.close()


def test_expired_offer_needs_the_confirm_checkbox(browser):
    api = FakeApi()
    api.accept_mode = "expired"
    api.offers[0].update(valid_until="2026-03-01", effective_status="expired")
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    expect(pg.locator("main")).to_contain_text("expired on 01.03.2026")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    dlg.locator("#ac_expired").check()
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_result")).to_be_visible()
    assert api.calls("POST", "incoming-offers/9b3e51c7/accept")[0][3]["confirm_expired"] is True
    assert problems == []
    ctx.close()


def test_accept_without_project_shows_the_server_error_and_keeps_the_dialog(browser):
    api = FakeApi()
    api.accept_mode = "project_422"  # the server refuses the accept with a 422
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    dlg.locator("#ac_submit").click()  # the first open project is preselected for a capacity offer
    expect(dlg.locator("#ac_error")).to_contain_text("A capacity offer needs a project")
    expect(dlg.locator("#ac_submit")).to_be_enabled()  # the dialog stays usable
    assert api.calls("POST", "incoming-offers/9b3e51c7/accept")[0][3]["project_id"] == "a1b2c3d4"
    assert pg.locator("#modal-root .modal").count() == 1
    assert problems == []
    ctx.close()


def test_escape_after_an_accept_error_closes_the_dialog(browser):
    api = FakeApi()
    api.accept_mode = "confirm"
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_error")).to_be_focused()
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_accept_plain_supplier_offer_is_a_flat_commitment(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c8")
    pg.click("[data-a=offAccept]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("flat commitment")
    dlg.locator("#ac_submit").click()
    expect(dlg.locator("#ac_result")).to_contain_text("Flat commitment of 3.091,88")
    assert "project_id" not in api.calls("POST", "incoming-offers/9b3e51c8/accept")[0][3]
    assert problems == []
    ctx.close()


def test_accept_dialog_escape_and_cancel_button(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offAccept]")
    pg.keyboard.press("Escape")
    expect(pg.locator("#modal-root .modal")).to_have_count(0)
    assert not api.calls("POST", "incoming-offers/9b3e51c7/accept")
    assert problems == []
    ctx.close()


def test_record_offer_posts_lines_and_plan(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="incoming")
    pg.click("[data-a=offNew]")
    dlg = pg.locator("#modal-root .modal")
    assert dlg.locator("#f_supplier_id option").count() == 3
    pg.select_option("#f_supplier_id", "5d1c0aa2")
    pg.fill("#f_reference", "ANG-NEW")
    pg.fill("#f_received_on", "2026-02-20")
    pg.fill("#off_lines tr[data-line='0'] [data-f=description]", "Frontend development")
    pg.fill("#off_lines tr[data-line='0'] [data-f=quantity]", "80")
    pg.fill("#off_lines tr[data-line='0'] [data-f=unit_price]", "80")
    pg.check("#off_plan_on")
    pg.fill("#off_ps", "2026-03-02")
    pg.fill("#off_pe", "2026-03-27")
    pg.fill("#off_ph", "20")
    pg.select_option("#f_project_id", "a1b2c3d4")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "incoming-offers")[0][3]
    assert body["lines"] == [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80}]
    assert body["plan"] == {"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20}
    assert body["project_id"] == "a1b2c3d4" and body["vat_mode"] is None and body["vat_rate"] is None
    assert not any(k in body for k in ("net_total", "vat", "gross_total", "status"))
    assert problems == []
    ctx.close()


def test_accepted_offer_edit_sends_only_the_allowed_changes(browser):
    api = FakeApi()
    api.offers[0].update(status="accepted", effective_status="accepted", allocation_id="c04d77e1")
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offEdit]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_contain_text("can only change its notes")
    assert dlg.locator("#off_lines").count() == 0
    pg.fill("#f_notes", "paid by transfer")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("PUT", "incoming-offers/9b3e51c7")[0][3]
    assert body["notes"] == "paid by transfer" and body["lines"] == api.offers[0]["lines"]
    assert not any(k in body for k in ("status", "net_total", "allocation_id", "id"))
    assert problems == []
    ctx.close()


def test_unaccept_asks_first(browser):
    api = FakeApi()
    api.offers[0].update(status="accepted", effective_status="accepted", allocation_id="c04d77e1")
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offUnaccept]")
    expect(pg.locator("h1 .pill")).to_have_text("under review")
    assert "Unaccept" in pg.confirms[0]
    assert problems == []
    ctx.close()


def test_accountant_reads_offers_without_buttons(browser):
    api = FakeApi(me=ACCOUNTANT)
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    expect(pg.locator("h1")).to_contain_text("ANG-2026-117")
    assert pg.locator("[data-a=offAccept], [data-a=offReject], [data-a=offEdit], [data-a=offDelete], [data-a=offReview]").count() == 0
    pg.goto(f"{ORIGIN}/#/suppliers")
    expect(pg.locator("[data-a=supNew]")).to_be_visible()  # the accountant writes suppliers
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- allocations dialog
def test_allocations_dialog_cancels_an_external_allocation_with_a_date(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.locator("[data-a=allocations][data-project=a1b2c3d4]").click()
    dlg = pg.locator("#modal-root .modal")
    ext = dlg.locator("tr", has_text="Example Freelance Studio")
    expect(ext).to_contain_text("6.400,00")
    assert ext.locator("[data-a=alEdit]").count() == 0  # read-only
    ext.locator("[data-a=alCancelAlloc]").click()
    pg.fill("#al_cfrom", "2026-03-13")
    pg.fill("#al_creason", "project paused")
    pg.press("#al_creason", "Enter")
    expect(dlg.locator("tr", has_text="Example Freelance Studio")).to_contain_text("cancelled from 13.03.2026")
    assert api.cancelled == {"effective_from": "2026-03-13", "reason": "project paused"}
    expect(dlg.locator("tr", has_text="Example Freelance Studio")).to_contain_text("3.200,00")
    assert dlg.locator("tr", has_text="Demo Boss").locator("[data-a=alCancelAlloc]").count() == 0
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- finance, planner, timeline
def test_money_page_has_the_commitments_block(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="money")
    block = pg.locator("#proc-commitments")
    expect(block).to_contain_text("Open supplier commitments")
    expect(block).to_contain_text("9.491,88")  # totals.open_net
    expect(block.locator("tr[data-kind=flat]")).to_contain_text("Demo Print Shop GmbH")
    expect(block.locator("tr[data-kind=flat]")).to_contain_text("–")  # no invoiced figure for a flat item
    assert problems == []
    ctx.close()


def test_customer_dominance_hint_is_dismissible(browser):
    api = FakeApi()
    api.commitments["hints"] = [
        {
            "code": "business.customer_dominance",
            "severity": "info",
            "field": None,
            "confirmable": False,
            "message": "Demo Customer A made 83 % of the revenue of the last 12 months.",
            "details": {"customer_id": "c0ffee01", "customer_name": "Demo Customer A", "share_percent": 83.0, "to": "2026-03-02"},
        }
    ]
    ctx, pg, problems = open_app(browser, api, page="money")
    expect(pg.locator(".proc-hint")).to_contain_text("83 %")
    pg.click(".proc-hint [data-a=hintDismiss]")
    expect(pg.locator(".proc-hint")).to_have_count(0)
    pg.reload()
    pg.wait_for_selector("#proc-commitments")
    assert pg.locator(".proc-hint").count() == 0
    assert problems == []
    ctx.close()


def test_forecast_draws_server_external_numbers_and_toggle(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="plan/forecast")
    ext = pg.locator("#pp-ext")
    expect(ext).to_contain_text("80 h")  # totals.external_capacity
    expect(ext).to_contain_text("20 h")  # external gap
    expect(ext).to_contain_text("6.400,00")  # external cost
    expect(ext).to_contain_text("13.600,00")  # margin
    first = pg.locator("table tr.pp-over").first
    expect(first.locator(".pp-extcell")).to_contain_text("20 h")
    expect(first.locator(".pp-extcell")).to_contain_text("gap 5 h")
    expect(first.locator(".pp-extcell")).to_contain_text("1.600,00")
    assert pg.locator("svg.chart rect.fc-ext").count() == 4
    first.locator(".pp-toggle").click()
    expect(pg.locator("tr.pp-detail .pp-extrow")).to_contain_text("external 25 h of 20 h")
    assert "include_external" not in api.calls("GET", "forecast")[0][2]
    pg.uncheck("#fc_ext")
    pg.wait_for_function("() => localStorage.fcExternal === '0'")
    pg.wait_for_selector("#pp-ext", state="detached")
    assert api.calls("GET", "forecast")[-1][2]["include_external"] == ["0"]
    assert pg.locator(".pp-extcell").count() == 0
    assert problems == []
    ctx.close()


def test_plan_and_vacation_sends_include_external_false_when_off(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="plan/vacation", storage={"plnUser": ""})
    pg.wait_for_selector("#plnExt")
    assert "include_external" not in api.calls("POST", "plan/simulate")[0][3]
    expect(pg.locator(".pln-extkpi").first).to_contain_text("External cost")
    pg.uncheck("#plnExt")
    pg.wait_for_function("() => localStorage.plnExternal === '0'")
    pg.wait_for_function("() => !document.querySelector('.pln-extkpi')")
    last = api.calls("POST", "plan/simulate")[-2:]
    assert all(r[3]["include_external"] is False for r in last)
    assert problems == []
    ctx.close()


def test_planner_shows_external_allocations_read_only(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="plan/vacation", storage={"plnUser": ""})
    row = pg.locator(".pln-ext-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Example Freelance Studio")
    assert row.locator("[data-kind]").count() == 0  # no drag handles
    assert problems == []
    ctx.close()


def test_team_timeline_has_an_external_group(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="timeline")
    row = pg.locator(".tl-extrow")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Example Freelance Studio (Demo)")
    assert problems == []
    ctx.close()
