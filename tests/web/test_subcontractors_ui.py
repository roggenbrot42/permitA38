"""Browser tests of suppliers, incoming offers, expense links, commitments and the assignments dialog.
The API is intercepted with the browser context's routes; the answers are the worked examples of
docs/contracts/subcontractors.md (extracted verbatim into tests/web/fixtures/subcontractors/), so these tests run
without the backend. The UI never computes capacity, gaps or money: every number asserted here comes from a fixture."""

import copy
import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api")
import pathlib  # noqa: E402

from playwright.sync_api import expect  # noqa: E402

from tests.web.test_planner_per_project_ui import (  # noqa: E402
    ORIGIN,
    PROJECTS,  # noqa: E402
    _json,
    _static,
)

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
SUP_RC = {
    **SUP_P,
    "id": "8b2c3d4e",
    "name": "Example Cloud Ltd (Demo)",
    "country": "IE",
    "vat_mode": "eu_reverse_charge",
    "supplier_no": "L-0005",
}


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

    save_warnings: list = []

    @staticmethod
    def server_figures(body):
        """Stand-in for the server's figures: 100 planned hours at the rate (the UI computes nothing itself)."""
        sup = bool(body.get("supplier_id"))
        out = {"kind": "external" if sup else "employee", "planned_hours": 100.0}
        if sup:
            out.update(supplier_name="Example Freelance Studio (Demo)", planned_net=100.0 * body["rate"])
        return out

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
                {
                    "me": me,
                    "settings": {},
                    "timer": None,
                    "states": {},
                    "conflicts": [],
                    "templates": [],
                    "data_dir": "",
                    "features": {},
                },
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
        if path.startswith("projects/") and path.endswith(("/planning", "/timesheet")):
            return _json(route, 404, {"detail": "Not found"})
        if path in ("customers", "absences", "offers", "tasks", "entries", "invoices", "trips"):
            return _json(route, 200, [])
        if path == "dashboard":  # the start page: an empty week (the subcontractor tests do not look at it)
            return _json(route, 200, {"days": [], "worked": 0, "target": 0, "week": [], "days_worked": 0})
        if path == "holidays":
            return _json(route, 200, {})
        if path == "plan/scenarios":
            return _json(route, 200, [])
        if path == "team/timeline":
            start = query["start"][0]
            ext = fixture(
                "team_timeline_external"
            )  # the contract's windows are in March 2026: moved into the shown window
            for x in ext["external"]:
                x["windows"] = [{"start": start, "end": (date.fromisoformat(start) + timedelta(days=4)).isoformat()}]
            return _json(
                route,
                200,
                {"level": "full", "start": start, "end": query["end"][0], "people": [], "holidays": {}, **ext},
            )
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
                rows = [
                    fixture("supplier_employee_view")
                    if s["id"] == "5d1c0aa2"
                    else {k: s[k] for k in ("id", "name", "kind", "tags", "active")}
                    for s in rows
                ]
            return _json(route, 200, copy.deepcopy(rows))
        if path == "suppliers" and m == "POST":
            if getattr(self, "fail_create", False):
                return _json(route, 422, {"detail": "Supplier name is not allowed (demo)"})
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
            row = {
                **fixture("offer_w1"),
                **body,
                "id": "9b3e5999",
                "status": "received",
                "effective_status": "received",
                "allocation_id": None,
            }
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
            new = {
                "review": "under_review",
                "reject": "rejected",
                "reopen": "under_review",
                "unaccept": "under_review",
            }[act]
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
        if path == "allocations" and m == "POST":
            row = {**body, "id": "a1100001", "status": "active", **self.server_figures(body)}
            self.allocation_list.append(row)
            return _json(route, 200, {**row, "planning_warnings": self.save_warnings})
        if path.startswith("allocations/") and m == "PUT":
            aid = path.split("/")[1]
            old = next(x for x in self.allocation_list if x["id"] == aid)
            for k in ("hours_per_week", "total_hours", "rate"):
                old.pop(k, None)
            old.update(body, **self.server_figures(body))
            return _json(route, 200, {**old, "planning_warnings": []})
        if path.startswith("allocations/") and m == "DELETE":
            self.allocation_list = [x for x in self.allocation_list if x["id"] != path.split("/")[1]]
            return _json(route, 200, {"ok": True})
        if path == "allocations" and m == "GET":
            rows = self.allocation_list
            if query.get("status"):
                rows = [a for a in rows if a["status"] == query["status"][0]]
            return _json(route, 200, copy.deepcopy(rows))
        return _json(route, 200, [])

    def finance(self, query):
        months = [
            {
                "revenue": 0,
                "expenses": 0,
                "profit": 0,
                "vat_due": 0,
                "tax_set_aside": 0,
                "vat_set_aside": 0,
                "social": 0,
                "take_home": 0,
            }
            for _ in range(12)
        ]
        z = {"est": 0, "soli": 0, "total": 0, "kst": 0, "kst_soli": 0, "church": 0, "gewst": 0}
        projected = {
            **z,
            "tax_due": 0,
            "zve": 0,
            "withheld": 0,
            "balance": None,
            "insurance": {"kv": 0, "pv": 0, "rv": 0, "alv": 0, "subsidy": 0},
            "vorsorge": {"dA": 0, "dBO": 0},
        }
        return {
            "months": months,
            "totals": {"revenue": 0, "expenses": 0, "profit": 0, "vat_due": 0},
            "tax_actual": z,
            "tax_projected": projected,
            "tax_owed": {"as_of": "2026-01-01", "profit_ytd": 0, "tax_due": 0, "withheld": 0, "owed": 0},
            "reserve": {
                "monthly": 0,
                "due_year": 0,
                "months_left": 0,
                "remaining": 0,
                "owed_now": 0,
                "withheld": 0,
                "balance": 0,
            },
            "extrapolated": True,
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
    expect(pg.locator("#sup-table tr[data-id]")).to_have_count(
        3
    )  # the reset list has rendered; typing before it would land in the replaced input
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
    assert (
        pg.locator("#toast").inner_text() == "A subcontractor needs the hours per week they can offer"
    )  # refused before any request
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
def test_expenses_page_has_no_vendor_banner_suppliers_page_offers_the_link(browser):
    api = FakeApi()  # 4 expenses with a vendor that is no supplier
    ctx, pg, problems = open_app(browser, api, page="expenses")
    expect(pg.locator("h1")).to_have_text("Expenses")
    assert pg.locator(".sup-vendor-banner").count() == 0
    assert "without a supplier" not in pg.locator("#app").inner_text()
    pg.goto(pg.url.split("#")[0] + "#/suppliers")
    expect(pg.get_by_role("button", name="Link vendors from expenses (4)")).to_be_visible()
    assert problems == []
    ctx.close()


def test_vendor_link_and_adoption_check_then_link(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="suppliers")
    pg.get_by_role("button", name="Link vendors from expenses (4)").click()
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
    ctx, pg, problems = open_app(browser, api, page="suppliers")
    pg.get_by_role("button", name="Link vendors from expenses (4)").click()
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
    assert calls[0][3]["items"][0] == {
        "vendors": ["Demo Print Shop GmbH", "demo print shop"],
        "create": {"name": "Demo Print Studio", "kind": "company"},
    }
    assert problems == []
    ctx.close()


def test_no_vendor_link_when_all_vendors_are_linked(browser):
    api = FakeApi()
    api.vendor_suggestions = {"groups": [], "unlinked_expenses": 0}
    ctx, pg, problems = open_app(browser, api, page="suppliers")
    expect(pg.get_by_role("button", name="+ New supplier")).to_be_visible()
    assert pg.get_by_role("button", name="Link vendors from expenses").count() == 0
    assert problems == []
    ctx.close()


def test_employee_gets_no_vendor_link_and_no_suggestions(browser):
    # the employee may read projects (the start page needs them); no suppliers write right: no link, no request
    api = FakeApi(
        me={**EMPLOYEE, "permissions": {**EMPLOYEE["permissions"], "projects": {"read": "all", "write": "none"}}}
    )
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.wait_for_timeout(300)
    assert pg.locator(".sup-vendor-banner").count() == 0
    assert api.calls("GET", "suppliers/vendor-suggestions") == []
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
    expect(dlg.locator("#f_supplier_id")).to_be_hidden()  # one Vendor field: a vendor named like a supplier links to it
    pg.fill("#f_vendor", "example freelance studio (demo)")
    expect(dlg.locator("#f_supplier_id")).to_have_value("5d1c0aa2")
    expect(dlg.locator("#e_vendor_sup")).to_contain_text("Example Freelance Studio (Demo)")
    pg.wait_for_selector("#f_allocation_id option[value=c04d77e1]", state="attached")
    expect(dlg.locator("#f_allocation_id")).to_be_visible()
    assert api.calls("GET", "finance/commitments")[-1][2]["supplier_id"] == ["5d1c0aa2"]
    pg.select_option("#f_allocation_id", "c04d77e1")
    pg.fill("#f_gross", "1904")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "expenses")[0][3]
    assert body["supplier_id"] == "5d1c0aa2" and body["allocation_id"] == "c04d77e1" and body["reverse_charge"] is False
    # the server writes the supplier's exact name into `vendor`, so the spelling typed here does not stay
    assert body["vendor"] == "example freelance studio (demo)"
    # reverse charge supplier: flag proposed, VAT typed as self-assessed amount
    pg.click("[data-a=editExpense]")
    pg.fill("#f_vendor", "Example Cloud Ltd (Demo)")
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


def test_a_vendor_that_is_no_supplier_stays_free_text_and_unlinks(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.click("[data-a=editExpense]")
    dlg = pg.locator("#modal-root .modal")
    pg.fill("#f_vendor", "Demo Print Shop GmbH")
    expect(dlg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    pg.fill("#f_vendor", "Trattoria Beispiel")
    expect(dlg.locator("#f_supplier_id")).to_have_value("")
    expect(dlg.locator("#e_vendor_sup")).to_be_hidden()
    pg.fill("#f_gross", "23.80")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "expenses")[0][3]
    assert body["vendor"] == "Trattoria Beispiel" and body["supplier_id"] is None
    assert problems == []
    ctx.close()


def test_expense_dialog_keeps_an_inactive_supplier_selectable(browser):
    inactive = {**SUP_P, "active": False}
    api = FakeApi(suppliers=[SUP_S, inactive])
    api.expenses = [
        {
            "id": "ee000009",
            "date": "2026-03-01",
            "vendor": "Demo Print Shop GmbH",
            "net": 10.0,
            "vat": 1.9,
            "vat_rate": 19,
            "deductible": 100,
            "supplier_id": "7a1b2c3d",
            "category": "Software",
        }
    ]
    ctx, pg, problems = open_app(browser, api, page="expenses")
    expect(pg.locator("tr[data-id=ee000009]")).to_contain_text("supplier")
    pg.click("tr[data-id=ee000009] td >> nth=0")
    expect(pg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    assert pg.locator("#f_supplier_id option[value='7a1b2c3d']").inner_text().endswith("(inactive)")
    assert problems == []
    ctx.close()


# ---------------------------------------------------------------- vendor combobox
def open_bill(browser, api=None):
    api = api or FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.click("[data-a=editExpense]")
    return api, ctx, pg, problems, pg.locator("#modal-root .modal")


def options(pg):
    return pg.locator("#modal-root .vc-list li[role=option]")


def test_vendor_list_opens_on_focus_and_click_with_names_and_numbers(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    expect(dlg.locator(".vc-arrow")).to_be_visible()
    expect(dlg.locator("#f_vendor")).to_have_attribute("role", "combobox")
    expect(dlg.locator(".vc-list")).to_be_hidden()
    pg.focus("#f_vendor")
    expect(dlg.locator(".vc-list")).to_be_visible()
    expect(options(pg)).to_have_count(3)  # empty field: every active supplier, no add row
    expect(options(pg).first).to_contain_text("Example Freelance Studio (Demo)")
    expect(options(pg).first).to_contain_text("L-0003")
    pg.keyboard.press("Escape")
    expect(dlg).to_be_visible()
    expect(dlg.locator(".vc-list")).to_be_hidden()
    dlg.locator(".vc-arrow").click()  # the arrow opens it, a second click closes it
    expect(dlg.locator(".vc-list")).to_be_visible()
    dlg.locator(".vc-arrow").click()
    expect(dlg.locator(".vc-list")).to_be_hidden()
    pg.click("#f_vendor")
    expect(dlg.locator(".vc-list")).to_be_visible()
    # the list stays inside the dialog and the window
    lb, db = dlg.locator(".vc-list").bounding_box(), dlg.bounding_box()
    assert lb["x"] >= db["x"] and lb["x"] + lb["width"] <= db["x"] + db["width"] and lb["y"] + lb["height"] <= 900
    assert problems == []
    ctx.close()


def test_vendor_list_filters_anywhere_in_the_name_ignoring_case(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    pg.click("#f_vendor")
    pg.keyboard.type("PRINT")
    expect(options(pg)).to_have_count(2)  # the supplier and the add row
    expect(options(pg).first).to_contain_text("Demo Print Shop GmbH")
    expect(dlg.locator("#f_supplier_id")).to_have_value("")  # typing alone does not link
    pg.keyboard.type("x")
    expect(options(pg)).to_have_count(1)
    expect(options(pg).first).to_have_text("+ Add as new supplier")
    assert problems == []
    ctx.close()


def test_vendor_list_keyboard_pick_links_and_enter_does_not_save(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    pg.fill("#f_gross", "11.90")
    pg.click("#f_vendor")
    pg.keyboard.type("cloud")
    pg.keyboard.press("ArrowDown")
    expect(options(pg).first).to_have_attribute("aria-selected", "true")
    pg.keyboard.press("Enter")
    expect(dlg).to_be_visible()
    assert api.calls("POST", "expenses") == []  # Enter in the list picks, it does not save
    expect(dlg.locator("#f_vendor")).to_have_value("Example Cloud Ltd (Demo)")
    expect(dlg.locator("#f_supplier_id")).to_have_value("8b2c3d4e")
    expect(dlg.locator("#e_vendor_sup")).to_contain_text("Linked to supplier L-0005 Example Cloud Ltd (Demo)")
    expect(pg.locator("#f_reverse_charge")).to_be_checked()  # as before: a reverse-charge supplier proposes the flag
    expect(dlg.locator(".vc-list")).to_be_hidden()
    pg.keyboard.press("Enter")  # the list is closed: now Enter saves, as everywhere in the dialog
    pg.wait_for_selector("#modal-root .modal", state="detached")
    assert api.calls("POST", "expenses")[0][3]["supplier_id"] == "8b2c3d4e"
    assert problems == []
    ctx.close()


def test_vendor_list_mouse_pick_and_arrow_wraps(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    pg.click("#f_vendor")
    pg.keyboard.press("ArrowUp")  # from nothing selected to the last row
    expect(options(pg).nth(2)).to_have_attribute("aria-selected", "true")
    options(pg).nth(1).click()
    expect(dlg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    expect(dlg.locator("#f_vendor")).to_have_value("Demo Print Shop GmbH")
    pg.fill("#f_vendor", "Trattoria Beispiel")  # anything else is free text again
    expect(dlg.locator("#f_supplier_id")).to_have_value("")
    expect(dlg.locator("#e_vendor_sup")).to_be_hidden()
    assert problems == []
    ctx.close()


def test_add_as_new_supplier_creates_links_and_keeps_the_dialog(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    pg.click("#f_vendor")
    pg.keyboard.type("Trattoria Beispiel")
    expect(options(pg)).to_have_count(1)
    options(pg).first.click()
    expect(dlg.locator("#f_supplier_id")).to_have_value("5a5a5a5a")
    expect(dlg.locator("#e_vendor_sup")).to_contain_text("Linked to supplier Trattoria Beispiel")
    assert api.calls("POST", "suppliers")[0][3] == {"name": "Trattoria Beispiel", "kind": "company"}
    pg.click("#f_vendor")  # the new supplier is in the list now
    expect(options(pg)).to_have_count(4)
    assert problems == []
    ctx.close()


def test_a_typed_name_alone_never_creates_a_supplier(browser):
    api, ctx, pg, problems, dlg = open_bill(browser)
    pg.fill("#f_gross", "23.80")
    pg.fill("#f_vendor", "Trattoria Beispiel")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    assert api.calls("POST", "suppliers") == []
    assert api.calls("POST", "expenses")[0][3]["supplier_id"] is None
    ctx.close()


def test_a_failed_create_shows_the_error_and_leaves_the_text(browser):
    api = FakeApi()
    api.fail_create = True
    api, ctx, pg, problems, dlg = open_bill(browser, api)
    pg.click("#f_vendor")
    pg.keyboard.type("Trattoria Beispiel")
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("Enter")
    expect(pg.locator("#toast, .toast").first).to_contain_text("not allowed")
    expect(dlg.locator("#f_vendor")).to_have_value("Trattoria Beispiel")
    expect(dlg.locator("#f_supplier_id")).to_have_value("")
    expect(dlg).to_be_visible()
    ctx.close()


def test_no_add_row_without_the_suppliers_write_right(browser):
    me = {**BOSS, "permissions": perms(suppliers={"read": "all", "write": "none"})}
    api, ctx, pg, problems, dlg = open_bill(browser, FakeApi(me=me))
    pg.click("#f_vendor")
    pg.keyboard.type("Trattoria Beispiel")
    expect(dlg.locator(".vc-list")).to_contain_text("No supplier matches")
    assert pg.locator(".vc-add").count() == 0
    ctx.close()


def test_without_the_suppliers_right_the_vendor_is_a_plain_field(browser):
    me = {
        **BOSS,
        "permissions": perms(
            suppliers={"read": "none", "write": "none"}, procurement={"read": "none", "write": "none"}
        ),
    }
    api, ctx, pg, problems, dlg = open_bill(browser, FakeApi(me=me))
    assert pg.locator(".vc").count() == 0 and pg.locator("#f_vendor").get_attribute("role") is None
    ctx.close()


def test_a_hostile_supplier_name_is_shown_as_text(browser):
    evil = {**SUP_P, "id": "6e6e6e6e", "name": "<img src=x onerror=window.__pwn=1>Evil", "supplier_no": "<b>9</b>"}
    api, ctx, pg, problems, dlg = open_bill(browser, FakeApi(suppliers=[SUP_S, evil]))
    pg.click("#f_vendor")
    expect(options(pg).nth(1)).to_contain_text("<img src=x onerror=window.__pwn=1>Evil")
    assert pg.locator(".vc-list img, .vc-list b").count() == 0
    options(pg).nth(1).click()
    expect(dlg.locator("#e_vendor_sup")).to_contain_text("<b>9</b>")
    assert pg.evaluate("window.__pwn === undefined")
    ctx.close()


def test_a_linked_inactive_supplier_stays_linked_and_listed(browser):
    inactive = {**SUP_P, "active": False}
    api = FakeApi(suppliers=[SUP_S, inactive])
    api.expenses = [
        {
            "id": "ee000009",
            "date": "2026-03-01",
            "vendor": "Demo Print Shop GmbH",
            "net": 10.0,
            "vat": 1.9,
            "vat_rate": 19,
            "deductible": 100,
            "supplier_id": "7a1b2c3d",
            "category": "Software",
        }
    ]
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.click("tr[data-id=ee000009] td >> nth=0")
    expect(pg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    expect(pg.locator("#e_vendor_sup")).to_contain_text("Demo Print Shop GmbH")
    pg.click("#f_vendor")
    expect(options(pg)).to_have_count(2)  # the linked name shows every supplier
    expect(options(pg).nth(1)).to_contain_text("(inactive)")
    ctx.close()


def test_a_draft_vendor_that_matches_a_supplier_links_when_the_dialog_opens(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="expenses")
    pg.evaluate(
        "expenseDialog({date: '2026-03-01', vendor: 'demo print shop gmbh', description: '', category: 'Other', deductible: 100, vat_rate: 19, gross: 11.9})"
    )
    pg.wait_for_selector("#f_vendor")
    expect(pg.locator("#f_supplier_id")).to_have_value("7a1b2c3d")
    expect(pg.locator("#e_vendor_sup")).to_contain_text("Linked to supplier L-0004 Demo Print Shop GmbH")
    pg.evaluate("closeModal()")
    pg.evaluate(
        "expenseDialog({date: '2026-03-01', vendor: 'Unknown Kiosk', category: 'Other', deductible: 100, vat_rate: 19, gross: 5})"
    )
    pg.wait_for_selector("#f_vendor")
    expect(pg.locator("#f_supplier_id")).to_have_value("")
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
    expect(dlg.locator("#ac_result")).to_contain_text("Demo project P")
    expect(dlg.locator("#ac_result")).to_contain_text("02.03.2026 – 27.03.2026")
    expect(dlg.locator(".proc-warnings li[data-code='allocation.no_demand']")).to_contain_text(
        "nothing is planned there"
    )
    assert pg.locator("#plnWarn").count() == 0  # the dialog shows the warnings: no second banner behind it
    assert api.calls("POST", "incoming-offers/9b3e51c7/accept")[0][3] == {
        "confirm_warnings": [],
        "project_id": "a1b2c3d4",
    }
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
    assert reqs[0][3]["confirm_warnings"] == [] and reqs[1][3]["confirm_warnings"] == [
        "allocation.exceeds_weekly_hours"
    ]
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
    dlg = pg.locator("#modal-root .modal")
    expect(dlg).to_be_visible()  # the dialog opens after two fetches: Escape before its key handler exists does nothing
    expect(pg.locator("#ac_project")).to_be_focused()  # focus is inside the dialog when Escape is pressed
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
    expect(dlg.locator("#f_supplier_id option")).to_have_count(3)
    pg.select_option("#f_supplier_id", "5d1c0aa2")
    pg.fill("#f_reference", "ANG-NEW")
    pg.fill("#f_received_on", "2026-02-20")
    pg.fill("#off_lines tr[data-line='0'] [data-f=description]", "Frontend development")
    pg.fill("#off_lines tr[data-line='0'] [data-f=quantity]", "80")
    pg.fill("#off_lines tr[data-line='0'] [data-f=unit_price]", "80")
    pg.check("#off_plan_on")
    pg.fill("#off_ps", "2026-03-02")
    pg.fill("#off_pe", "2026-03-27")
    assert pg.locator("#off_ph, #off_pm").count() == 0  # the hour lines are booked: the plan asks for the period only
    pg.select_option("#f_project_id", "a1b2c3d4")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("POST", "incoming-offers")[0][3]
    assert body["lines"] == [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80}]
    assert body["plan"] == {"start": "2026-03-02", "end": "2026-03-27"}
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


OFFER_IN_FIELDS = {
    "supplier_id",
    "reference",
    "title",
    "received_on",
    "valid_until",
    "lines",
    "vat_rate",
    "vat_mode",
    "plan",
    "project_id",
    "contract_id",
    "attachment",
    "decision_note",
    "notes",
}
SUPPLIER_IN_FIELDS = {
    "name",
    "supplier_no",
    "kind",
    "active",
    "street",
    "zip",
    "city",
    "country",
    "vat_id",
    "tax_number",
    "vat_mode",
    "contact",
    "email",
    "phone",
    "notes",
    "iban",
    "bic",
    "bank_name",
    "payment_terms_days",
    "payment_terms_note",
    "tags",
    "weekly_hours",
    "workdays",
    "rate",
    "unavailable",
}


def test_supplier_edit_sends_only_supplier_in_fields(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="suppliers/5d1c0aa2")
    pg.click("[data-a=supEdit]")
    dlg = pg.locator("#modal-root .modal")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("PUT", "suppliers/5d1c0aa2")[0][3]
    assert set(body) <= SUPPLIER_IN_FIELDS  # no id, sanity_warnings, created, updated ...
    assert body["name"] == "Example Freelance Studio (Demo)" and body["weekly_hours"] == 24
    assert problems == []
    ctx.close()


def test_offer_edit_pins_no_vat_unless_it_differs_from_the_supplier(browser):
    api = FakeApi()  # the offer says domestic, the supplier is domestic: the form keeps the supplier's setting
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offEdit]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#f_vat_mode")).to_have_value("")
    expect(dlg.locator("#f_vat_rate")).to_have_value("")
    dlg.get_by_role("button", name="Save").click()
    pg.wait_for_selector("#modal-root .modal", state="detached")
    body = api.calls("PUT", "incoming-offers/9b3e51c7")[0][3]
    assert body["vat_mode"] is None and body["vat_rate"] is None
    assert set(body) <= OFFER_IN_FIELDS  # only the fields of IncomingOfferIn: no hours, net_total, status, id ...
    assert problems == []
    ctx.close()


def test_offer_edit_shows_a_vat_treatment_that_differs_from_the_supplier(browser):
    api = FakeApi()
    api.offers[0].update(vat_mode="eu_reverse_charge", vat_rate=0)  # the supplier is domestic: this offer differs
    ctx, pg, problems = open_app(browser, api, page="incoming/9b3e51c7")
    pg.click("[data-a=offEdit]")
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#f_vat_mode")).to_have_value("eu_reverse_charge")
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
    assert (
        pg.locator(
            "[data-a=offAccept], [data-a=offReject], [data-a=offEdit], [data-a=offDelete], [data-a=offReview]"
        ).count()
        == 0
    )
    pg.goto(f"{ORIGIN}/#/incoming")
    expect(pg.locator("h1")).to_contain_text("Incoming offers")
    assert pg.locator("[data-a=offNew]").count() == 0  # the accountant does not record offers
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
    expect(ext).to_contain_text("from offer ANG-2026-117")
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


def open_dialog(browser, api, **kw):
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.locator("[data-a=allocations][data-project=a1b2c3d4]").click()
    return ctx, pg, problems, pg.locator("#modal-root .modal")


def test_allocations_dialog_creates_a_subcontractor_row_with_total_hours_and_rate(browser):
    api = FakeApi()
    ctx, pg, problems, dlg = open_dialog(browser, api)
    dlg.locator("[data-a=alNew]").click()
    expect(dlg.locator("#al_owner optgroup")).to_have_count(2)  # people, subcontractors
    expect(dlg.locator("#al_ratebox")).to_be_hidden()  # an employee has no rate
    pg.select_option("#al_owner", "s:5d1c0aa2")
    expect(dlg.locator("#al_ratebox")).to_be_visible()
    expect(dlg.locator("#al_rate")).to_have_value("80")  # the supplier's rate is the default
    pg.fill("#al_start", "2026-03-02")
    pg.fill("#al_end", "2026-03-27")
    pg.select_option("#al_mode", "total")
    pg.fill("#al_hours", "100")
    pg.fill("#al_rate", "")
    dlg.locator("[data-a=alSave]").click()
    expect(dlg.locator("[role=alert]")).to_contain_text("rate")
    assert api.calls("POST", "allocations") == []
    pg.fill("#al_rate", "75.5")
    dlg.locator("[data-a=alSave]").click()
    body = api.calls("POST", "allocations")[0][3]
    assert body == {
        "project_id": "a1b2c3d4",
        "start": "2026-03-02",
        "end": "2026-03-27",
        "supplier_id": "5d1c0aa2",
        "total_hours": 100.0,
        "rate": 75.5,
    }
    row = dlg.locator("tr", has_text="100 h in total")
    expect(row).to_contain_text("7.550,00")  # planned net from the server
    expect(row).to_contain_text("100 h")  # planned hours from the server
    assert problems == []
    ctx.close()


def test_allocations_dialog_edits_a_subcontractor_row_and_shows_save_warnings(browser):
    api = FakeApi()
    ctx, pg, problems, dlg = open_dialog(browser, api)
    dlg.locator("tr", has_text="Example Freelance Studio").locator("button[data-a=alEdit]").click()
    expect(dlg.locator("#al_owner")).to_have_value("s:5d1c0aa2")
    expect(dlg.locator("#al_mode")).to_have_value("week")
    expect(dlg.locator("#al_hours")).to_have_value("20")
    expect(dlg.locator("#al_rate")).to_have_value("80")
    pg.select_option("#al_mode", "total")
    pg.fill("#al_hours", "60")
    pg.fill("#al_rate", "90")
    dlg.locator("[data-a=alSave]").click()
    call = api.calls("PUT", "allocations/c04d77e1")[0][3]
    assert call["total_hours"] == 60.0 and "hours_per_week" not in call and call["rate"] == 90.0
    assert call["supplier_id"] == "5d1c0aa2" and "user_id" not in call
    expect(dlg.locator("tr", has_text="Example Freelance Studio")).to_contain_text("60 h in total")
    assert problems == []
    ctx.close()


def test_allocations_dialog_shows_the_planning_warnings_after_a_save(browser):
    api = FakeApi()
    api.save_warnings = [fixture("warning_w11")]
    ctx, pg, problems, dlg = open_dialog(browser, api)
    dlg.locator("[data-a=alNew]").click()
    dlg.locator("[data-a=alSave]").click()
    expect(dlg.locator(".al-warn li[data-code='%s']" % fixture("warning_w11")["code"])).to_have_count(1)
    body = api.calls("POST", "allocations")[0][3]
    assert body["user_id"] == "u1b2c3d4" and body["hours_per_week"] == 10.0 and "rate" not in body
    assert "total_hours" not in body and "supplier_id" not in body
    assert problems == []
    ctx.close()


def test_allocations_dialog_has_no_delete_for_an_offer_linked_row(browser):
    api = FakeApi()
    api.allocation_list.append(
        {**api.allocation_list[0], "id": "c04d77e2", "incoming_offer_id": None, "offer_reference": None}
    )
    ctx, pg, problems, dlg = open_dialog(browser, api)
    dlg.locator("tr", has_text="from offer ANG-2026-117").locator("button[data-a=alEdit]").click()
    expect(dlg.locator(".al-form .al-offer")).to_contain_text("ANG-2026-117")
    assert dlg.locator("[data-a=alDelete]").count() == 0
    dlg.locator("[data-a=alCancelEdit]").click()
    dlg.locator("tr:not(:has(.al-offer))", has_text="Example Freelance Studio").locator("button[data-a=alEdit]").click()
    assert dlg.locator("[data-a=alDelete]").count() == 1
    dlg.locator("[data-a=alDelete]").click()
    assert pg.confirms == ["Delete this assignment?"]
    assert api.calls("DELETE", "allocations/c04d77e2")
    assert problems == []
    ctx.close()


def test_allocations_dialog_prefill_opens_the_form_with_the_given_values(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="project/a1b2c3d4")
    pg.evaluate(
        "() => openAllocations({projectId: 'a1b2c3d4', userId: 'u1b2c3d4', start: '2026-04-06', end: '2026-04-24', total_hours: 26})"
    )
    dlg = pg.locator("#modal-root .modal")
    expect(dlg.locator("#al_owner")).to_have_value("u:u1b2c3d4")
    expect(dlg.locator("#al_project")).to_have_value("a1b2c3d4")
    expect(dlg.locator("#al_start")).to_have_value("2026-04-06")
    expect(dlg.locator("#al_end")).to_have_value("2026-04-24")
    expect(dlg.locator("#al_mode")).to_have_value("total")
    expect(dlg.locator("#al_hours")).to_have_value("26")
    dlg.locator("[data-a=alSave]").click()
    body = api.calls("POST", "allocations")[0][3]
    assert body == {
        "project_id": "a1b2c3d4",
        "start": "2026-04-06",
        "end": "2026-04-24",
        "user_id": "u1b2c3d4",
        "total_hours": 26.0,
    }
    assert dlg.locator("[data-a=alBootstrap]").count() == 0
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
            "details": {
                "customer_id": "c0ffee01",
                "customer_name": "Demo Customer A",
                "share_percent": 83.0,
                "to": "2026-03-02",
            },
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


def test_team_timeline_has_an_external_group(browser):
    api = FakeApi()
    ctx, pg, problems = open_app(browser, api, page="timeline")
    row = pg.locator(".tl-extrow")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Example Freelance Studio (Demo)")
    bar = row.locator(".tl-bar.external")
    expect(bar).to_have_count(1)
    assert "left:0px" in bar.get_attribute("style")  # the window starts on the first day shown
    assert "subcontractor," in bar.get_attribute("aria-label")
    assert problems == []
    ctx.close()
