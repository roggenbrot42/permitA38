"""Browser tests of the business trip wizard (docs/specs/business-trips-wizard.md, section 6): new-trip dialog, the steps
of the trip page, itinerary rows, planned items with bills and totals. The API is intercepted in the browser; the
trip routes keep trips, items, itinerary and bills in memory (the totals are computed like the server does, with
decimals). Demo data only."""

import copy
import json
import re
from decimal import Decimal
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.web.test_trips_ui import (  # noqa: E402
    CONTRACT_TRIP,
    ME,
    ORIGIN,
    FakeTripApi,
    _json,
    open_app,
    open_trips_page,
)


class WizardApi(FakeTripApi):
    """The trip and expense routes of docs/contracts/trips.md (items, itinerary, bills, totals); everything else is the
    base fake. Every request is logged like the base fake's."""

    def __init__(self, me, trip=None):
        super().__init__(me, trip)
        self.bills: list[dict] = []
        self.seq = 0
        self.hold_next_put = False  # the next trip PUT is kept until release(): a write still running in the browser
        self.held: list = []

    def release(self):
        """Answers the held PUTs now (they are logged when answered)."""
        held, self.held = self.held, []
        for route in held:
            self.handle(route)

    def _id(self, prefix):
        self.seq += 1
        return f"{prefix}{self.seq:08d}"

    def _totals(self):
        """items_expected_total and items_actual_total as the server computes them (decimals, planning only)."""
        if not self.trip:
            return
        expected = sum(
            (Decimal(str(i["expected"])) for i in self.trip.get("items", []) if i.get("expected") is not None),
            Decimal(0),
        )
        own = [b for b in self.bills if b.get("trip_id") == self.trip["id"]]
        actual = sum((Decimal(str(b["net"])) + Decimal(str(b["vat"])) for b in own), Decimal(0))
        self.trip["items_expected_total"] = float(expected)
        self.trip["items_actual_total"] = float(actual)
        self.trip["bills"] = {
            "count": len(own),
            "net": float(sum((Decimal(str(b["net"])) for b in own), Decimal(0))),
            "vat": float(sum((Decimal(str(b["vat"])) for b in own), Decimal(0))),
        }

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path.split("/api/", 1)[1]
        if path != "expenses" and path != "trips" and not path.startswith("trips/"):
            return super().handle(route)
        if self.hold_next_put and req.method == "PUT":
            self.hold_next_put = False
            self.held.append(route)
            return None
        body = json.loads(req.post_data) if req.post_data else None
        self.requests.append((req.method, path, "", body))
        if path == "expenses":
            if req.method == "GET":
                return _json(route, 200, self.bills)
            if req.method == "POST":
                bill = {"id": self._id("b"), **body, "attachment": None}
                self.bills.append(bill)
                self._totals()
                return _json(route, 200, bill)
            return _json(route, 405, {"detail": "Method not allowed"})
        if path == "trips" and req.method == "GET":
            self._totals()
            return _json(route, 200, [self.trip] if self.trip else [])
        if path == "trips" and req.method == "POST":
            for it in body.get("items", []):
                it.setdefault("id", self._id("i"))
            self.trip = {
                "id": self._id("t"),
                "created": "2026-10-26T09:00:00Z",
                "updated": "2026-10-26T09:00:00Z",
                "user_id": self.me["id"],
                "allowance_total": 0,
                "mileage_km": 0,
                "mileage_total": 0,
                "rules_version": "2025-12-05",
                "days": [],
                "checklist": copy.deepcopy(CONTRACT_TRIP["checklist"]),
                **body,
                "status": "draft",
            }
            self._totals()
            return _json(route, 200, self.trip)
        if path == "trips/preview":
            return _json(route, 200, self._preview(body))
        m = re.fullmatch(r"trips/([0-9a-z]{6,32})(/reopen)?", path)
        if not m or not self.trip or m.group(1) != self.trip["id"]:
            return _json(route, 404, {"detail": "Not found"})
        if m.group(2):
            self.trip = {**self.trip, "status": "draft"}
            return _json(route, 200, self.trip)
        if req.method == "GET":
            self._totals()
            return _json(route, 200, self.trip)
        if req.method == "DELETE":
            self.trip = None
            return route.fulfill(status=204, body="")
        if req.method == "PUT":
            if self.trip["status"] == "final":
                return _json(route, 409, {"detail": "This trip is final; the boss can reopen it"})
            for leg in body.get("itinerary", []):
                if not (body["start"] <= leg["depart"] <= body["end"]):
                    return _json(route, 422, {"detail": f"itinerary leg departing {leg['depart']} is outside the trip"})
            for it in body.get("items", []):
                it.setdefault("id", self._id("i"))
            self.trip = {**self.trip, **body}
            self._totals()
            return _json(route, 200, self.trip)
        return _json(route, 405, {"detail": "Method not allowed"})

    @staticmethod
    def _preview(body):
        from datetime import date, timedelta

        start = date.fromisoformat(body["start"][:10])
        end = date.fromisoformat(body["end"][:10])
        days = []
        d = start
        while d <= end:
            days.append(
                {
                    "date": d.isoformat(),
                    "country": "DE",
                    "city": "",
                    "absence_hours": 24.0,
                    "base": 0.0,
                    "reductions": [],
                    "amount": 0.0,
                    "note": "",
                }
            )
            d += timedelta(days=1)
        km = sum(m["km"] for m in body.get("mileage", []))
        return {
            "days": days,
            "allowance_total": 0.0,
            "mileage_km": km,
            "mileage_rate": 0.3,
            "mileage_total": round(km * 0.3, 2),
            "rules_version": "2025-12-05",
        }


def create_trip(pg, purpose, start, end, origin="Berlin", destination="Hamburg"):
    open_trips_page(pg)
    pg.click("[data-a=newTrip]")
    pg.wait_for_selector("#f_purpose")
    pg.fill("#f_purpose", purpose)
    pg.fill("#f_start", start)
    pg.fill("#f_end", end)
    pg.fill("#f_origin", origin)
    pg.fill("#f_destination", destination)
    pg.click("#m-save")
    pg.wait_for_selector("#t_step tr[data-row]")


def open_trip(pg, trip_id, step):
    pg.goto(f"{ORIGIN}/#/trips/{trip_id}/{step}")
    pg.wait_for_selector("#t_step")


def puts(api):
    return [r for r in api.requests if r[0] == "PUT"]


def test_new_trip_dialog_creates_the_trip_and_lands_on_the_itinerary(browser):
    api = WizardApi(ME, None)
    ctx, pg, problems = open_app(browser, api)
    create_trip(pg, "Customer visit", "2026-11-09T08:00", "2026-11-11T18:00")
    post = [r for r in api.requests if r[0] == "POST" and r[1] == "trips"]
    assert len(post) == 1
    body = post[0][3]
    assert body["purpose"] == "Customer visit" and body["status"] == "draft"
    assert body["origin"] == "Berlin" and body["destination"] == "Hamburg" and body["destination_country"] == "DE"
    assert pg.evaluate("location.hash") == f"#/trips/{api.trip['id']}/itinerary"
    expect(pg.locator("nav[aria-label='Trip steps'] [aria-current=step]")).to_contain_text("Itinerary")
    assert pg.locator("#t_step tr[data-row]").count() == 3
    assert pg.locator('#t_step tr[data-row="dep"] [data-f=place]').input_value() == "Berlin"
    assert pg.locator('#t_step tr[data-row="stay"] [data-f=place]').input_value() == "Hamburg"
    assert pg.locator('#t_step tr[data-row="ret"] [data-f=place]').input_value() == "Berlin"
    assert puts(api) == []
    assert problems == []
    ctx.close()


def test_a_trip_shows_exactly_three_itinerary_rows_and_add_stay_adds_one(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")

    def kinds():
        return pg.locator("#t_step tr[data-row]").evaluate_all("rows => rows.map(r => r.dataset.row)")

    assert kinds() == ["dep", "stay", "ret"]  # a plain trip: exactly three rows
    pg.click("[data-a=tripStayAdd]")
    assert kinds() == ["dep", "stay", "stay", "ret"]
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    saved = puts(api)
    assert len(saved) == 1
    legs = saved[0][3]["itinerary"]
    assert len(legs) == 3 and all(
        leg["depart"] >= "2026-11-09T08:00" and leg["depart"] <= "2026-11-11T18:00" for leg in legs
    )
    # the places of the allowance: the leg to Hamburg leaves on the first day, the way back on the last
    assert saved[0][3]["places"] == [
        {"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-11-11", "country": "DE", "city": "Berlin"},
    ]
    assert problems == []
    ctx.close()


def test_a_28_day_single_city_trip_also_shows_three_rows(browser):
    api = WizardApi(ME, None)
    ctx, pg, problems = open_app(browser, api)
    create_trip(pg, "Long assignment", "2026-11-01T08:00", "2026-11-29T18:00")
    assert pg.locator("#t_step tr[data-row]").count() == 3  # not one row per day
    assert problems == []
    ctx.close()


def test_back_and_next_keep_the_entered_values_and_a_step_shows_only_its_fields(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_title", "Renamed workshop")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    assert pg.locator("#t_purpose").count() == 0 and pg.locator("#t_title").count() == 0
    assert pg.locator("#t_days").count() == 0 and pg.locator("#t_breakdown").count() == 0
    expect(pg.locator("#t_step")).not_to_contain_text("Cost centre")
    expect(pg.locator("main")).to_contain_text("Workshop at the customer's site")  # the summary line above steps 2-4
    pg.click("[data-a=tripMove][data-step=plan]")
    pg.wait_for_selector("#t_title")
    assert pg.input_value("#t_title") == "Renamed workshop"
    assert [p[3]["title"] for p in puts(api)] == ["Renamed workshop", "Renamed workshop"]
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    assert pg.locator("#t_step [data-f=breakfast]").count() == 0 and pg.locator("#t_step [data-f=mode]").count() == 0
    assert problems == []
    ctx.close()


def test_a_refused_save_stays_on_the_step_with_the_message(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_purpose", "")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    expect(pg.locator("#t_error")).to_contain_text("Enter the reason for the trip")
    assert pg.evaluate("location.hash") == "#/trips/t7f3k2m9qa/plan"
    assert puts(api) == []
    assert problems == []
    ctx.close()


def test_expenses_add_items_set_expected_add_bill_from_the_row_and_totals(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["checklist"][2] = {**trip["checklist"][2], "state": "open", "count": 0, "sum": 0}
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    expect(pg.locator("#t_step")).to_contain_text("Planned costs are planning figures only")

    # hotel, through the drop-down and the small dialog of the type
    pg.select_option("#t_add_item", "hotel")
    pg.wait_for_selector("#f_text")
    assert pg.input_value("#f_text") == "Hamburg"  # prefilled with the destination
    pg.fill("#f_text", "Hotel Hamburg")
    pg.fill("#f_date", "2026-11-09")
    pg.fill("#f_date_to", "2026-11-10")
    pg.fill("#f_expected", "120")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="Hotel Hamburg")).to_have_count(1)
    hotel = api.trip["items"][0]
    assert hotel["category"] == "hotel" and hotel["date_to"] == "2026-11-10" and hotel["expected"] == 120

    # train, with from and to prefilled from the trip
    pg.select_option("#t_add_item", "train")
    pg.wait_for_selector("#f_from")
    assert pg.input_value("#f_from") == "Berlin" and pg.input_value("#f_to") == "Hamburg"
    pg.fill("#f_date", "2026-11-09")
    pg.fill("#f_text", "ICE")
    pg.fill("#f_expected", "80")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="ICE")).to_have_count(1)
    expect(pg.locator(".trip-totals")).to_contain_text("200,00")  # expected total: 120 + 80

    # expected changed inline
    pg.fill(f'input[data-item-exp="{hotel["id"]}"]', "100")
    pg.locator(f'input[data-item-exp="{hotel["id"]}"]').press("Tab")
    pg.wait_for_function("() => document.querySelector('.trip-totals').innerText.includes('180,00')")
    assert api.trip["items"][0]["expected"] == 100

    # a bill from the hotel row: the actual appears and the receipt is missing
    pg.locator("tr[data-item]", has_text="Hotel Hamburg").locator("button", has_text="Add bill").click()
    pg.wait_for_selector("#f_trip_item_id")
    assert pg.input_value("#f_trip_item_id") == hotel["id"]
    assert pg.input_value("#f_trip_id") == "t7f3k2m9qa"
    pg.fill("#f_vendor", "Demo Hotel")
    pg.fill("#f_gross", "95.20")
    pg.click("#m-save")
    row = pg.locator("tr[data-item]", has_text="Hotel Hamburg")
    expect(row).to_contain_text("95,20")
    expect(row.locator(".pill")).to_contain_text("missing")

    # an unplanned bill from the checklist (no item): a row of its own with the actual only
    pg.click("[data-a=tripMove][data-step=review]")
    pg.wait_for_selector("#t_checklist .trip-check")
    pg.locator("#t_checklist li.check-item", has_text="Transportation").locator("button", has_text="Add bill").click()
    pg.wait_for_selector("#f_trip_item_id")
    assert pg.input_value("#f_trip_item_id") == ""
    pg.fill("#f_vendor", "Demo Taxi")
    pg.fill("#f_gross", "11.90")
    pg.click("#m-save")
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    expect(pg.locator("tr[data-bill]", has_text="Demo Taxi")).to_contain_text("not planned")
    expect(pg.locator("tr[data-bill]", has_text="Demo Taxi")).to_contain_text("11,90")
    expect(pg.locator(".trip-totals")).to_contain_text("107,10")  # actual: 95,20 + 11,90
    assert problems == []
    ctx.close()


def test_a_final_trip_is_read_only_on_every_step(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip.update(
        {
            "status": "final",
            "items": [
                {
                    "id": "k3j9x0ab12",
                    "date": "2026-11-09",
                    "category": "hotel",
                    "text": "Hotel Hamburg",
                    "expected": 120,
                }
            ],
        }
    )
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    expect(pg.locator("main")).to_contain_text("This trip is final. Reopen it to change it.")
    assert pg.locator("#t_add_item").count() == 0
    assert pg.locator("input[data-item-exp]").count() == 0  # the expected cost is a plain figure on a final trip
    assert pg.locator("[data-a=tripItemEdit], [data-a=tripItemDel], [data-a=editExpense]").count() == 0
    open_trip(pg, "t7f3k2m9qa", "plan")
    assert pg.locator("#t_purpose").is_disabled()
    open_trip(pg, "t7f3k2m9qa", "review")
    assert pg.locator("[data-a=tripFinalize]").count() == 0
    expect(pg.locator("[data-a=tripReopen]")).to_have_count(1)  # the boss may reopen it
    assert pg.locator("#t_days input:not([disabled])").count() == 0
    assert [
        r for r in api.requests if r[0] in ("PUT", "POST") and r[1] != "trips/preview"
    ] == []  # the rate read is no write
    assert problems == []
    ctx.close()


def test_stepper_is_keyboard_operable_with_aria_current(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    links = pg.locator("nav[aria-label='Trip steps'] a")
    assert links.count() == 4
    for i in range(4):
        assert links.nth(i).get_attribute("href").startswith("#/trips/t7f3k2m9qa/")
    expect(pg.locator("nav[aria-label='Trip steps'] [aria-current=step]")).to_have_count(1)
    expect(pg.locator("nav[aria-label='Trip steps'] [aria-current=step]")).to_contain_text("Plan")
    links.nth(3).focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#t_step #t_breakdown")
    assert pg.evaluate("location.hash") == "#/trips/t7f3k2m9qa/review"
    expect(pg.locator("nav[aria-label='Trip steps'] [aria-current=step]")).to_contain_text("Review")
    assert problems == []
    ctx.close()


THREE_LEGS = [
    {"depart": "2026-11-09T08:00", "from": "Berlin", "to": "Hamburg", "country": "DE"},
    {"depart": "2026-11-10T09:00", "from": "Hamburg", "to": "Bremen", "country": "DE"},
    {"depart": "2026-11-11T18:00", "from": "Bremen", "to": "Berlin", "country": "DE"},
]


def row_kinds(pg):
    return pg.locator("#t_step tr[data-row]").evaluate_all("rows => rows.map(r => r.dataset.row)")


def active_label(pg):
    return pg.evaluate("document.activeElement.getAttribute('aria-label') || document.activeElement.id")


def test_a_single_stay_has_no_remove_button_and_removing_one_of_three_keeps_two_legs(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")
    assert pg.locator("[data-a=tripStayDel]").count() == 0  # the one stay of a plain trip cannot be removed
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    assert len(puts(api)[0][3]["itinerary"]) == 2
    assert problems == []
    ctx.close()

    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = copy.deepcopy(THREE_LEGS)
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")
    assert row_kinds(pg) == ["dep", "stay", "stay", "ret"]
    expect(pg.locator("[data-a=tripStayDel]")).to_have_count(2)
    pg.click("[data-a=tripStayDel][data-i='1']")
    assert row_kinds(pg) == ["dep", "stay", "ret"]
    expect(pg.locator("[data-a=tripStayDel]")).to_have_count(0)  # back to one stay: no more Remove
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    legs = puts(api)[0][3]["itinerary"]
    assert [leg["to"] for leg in legs] == ["Bremen", "Berlin"]
    assert problems == []
    ctx.close()


def test_a_plan_change_of_destination_rebuilds_the_places_when_the_itinerary_is_empty(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_destination", "Bremen")
    pg.select_option("#t_dest_country", "AT")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    saved = puts(api)
    assert len(saved) == 1
    assert saved[0][3]["places"] == [
        {"from_date": "2026-11-09", "country": "AT", "city": "Bremen"},
        {"from_date": "2026-11-11", "country": "DE", "city": "Berlin"},
    ]
    assert api.dialogs == []  # no legs to ask about
    assert problems == []
    ctx.close()


def test_a_declined_update_keeps_the_legs_and_derives_the_places_from_them(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = [
        {"depart": "2026-11-09T08:00", "from": "Berlin", "to": "Hamburg", "country": "DE"},
        {"depart": "2026-11-11T18:00", "from": "Hamburg", "to": "Berlin", "country": "DE"},
    ]
    api = WizardApi(ME, trip)
    api.confirm_answer = False
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_destination", "Bremen")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    assert len(api.dialogs) == 1 and "destination" in api.dialogs[0]
    saved = puts(api)[0][3]
    assert saved["itinerary"] == trip["itinerary"]  # the rows stay as they were
    assert saved["places"] == [
        {"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"},
        {"from_date": "2026-11-11", "country": "DE", "city": "Berlin"},
    ]
    assert problems == []
    ctx.close()


def test_shortening_the_period_drops_the_meals_outside_and_says_how_many(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))  # a breakfast on 2026-11-10
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_end", "2026-11-09T18:00")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    saved = puts(api)
    assert len(saved) == 1 and saved[0][3]["meals_provided"] == []
    expect(pg.locator("#toast")).to_contain_text("1 meal entry")
    assert problems == []
    ctx.close()


def test_mileage_outside_the_shorter_period_is_asked_for_and_a_refusal_stays_on_the_step(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["mileage"].append({"date": "2026-11-11", "from": "Hamburg", "to": "Berlin", "km": 290, "purpose": "Return"})
    api = WizardApi(ME, trip)
    api.confirm_answer = False
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_end", "2026-11-10T18:00")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    expect(pg.locator("#t_error")).to_contain_text("Mileage outside the new period")
    assert active_label(pg) == "t_error"  # the message takes the focus
    assert pg.evaluate("location.hash") == "#/trips/t7f3k2m9qa/plan"
    assert puts(api) == [] and len(api.dialogs) == 1
    api.confirm_answer = True
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    saved = puts(api)
    assert len(saved) == 1
    assert [m["date"] for m in saved[0][3]["mileage"]] == ["2026-11-09"]  # removed only after the yes
    assert problems == []
    ctx.close()


def test_itinerary_times_must_not_go_backwards_and_a_departure_outside_the_trip_is_named(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = copy.deepcopy(THREE_LEGS)
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")
    pg.fill("input[aria-label='Stay 2: until']", "2026-11-10T08:00")  # before the row above departs (09:00)
    pg.click("[data-a=tripMove][data-step=expenses]")
    expect(pg.locator("#t_error")).to_contain_text("must not go backwards")
    assert puts(api) == []
    pg.fill("input[aria-label='Stay 2: until']", "2026-11-12T08:00")  # after the trip ends
    pg.click("[data-a=tripMove][data-step=expenses]")
    expect(pg.locator("#t_error")).to_contain_text("is outside the trip")
    assert puts(api) == []
    assert problems == []
    ctx.close()


def test_adding_a_stay_keeps_mode_and_note_on_one_leg_and_focuses_the_new_stay(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = [
        {"depart": "2026-11-09T08:00", "from": "Berlin", "to": "Hamburg", "country": "DE"},
        {
            "depart": "2026-11-11T18:00",
            "from": "Hamburg",
            "to": "Berlin",
            "country": "DE",
            "mode": "train",
            "note": "ICE 123",
        },
    ]
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")
    pg.click("[data-a=tripStayAdd]")
    assert row_kinds(pg) == ["dep", "stay", "stay", "ret"]
    assert active_label(pg) == "Stay 2: place"
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_add_item")
    legs = puts(api)[0][3]["itinerary"]
    assert len(legs) == 3
    assert sum(1 for leg in legs if leg.get("note") == "ICE 123") == 1
    assert sum(1 for leg in legs if leg.get("mode") == "train") == 1
    assert problems == []
    ctx.close()


def test_a_removed_stay_moves_the_focus_to_the_stay_that_takes_its_place(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = copy.deepcopy(THREE_LEGS)
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "itinerary")
    pg.click("[data-a=tripStayDel][data-i='1']")
    assert active_label(pg) == "Stay 1: place"
    assert pg.input_value("input[aria-label='Stay 1: place']") == "Bremen"
    assert problems == []
    ctx.close()


def test_the_footer_names_the_expected_total_without_mileage_when_car_rows_are_listed(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))  # one car row
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    expect(pg.locator(".trip-totals")).to_contain_text("Expected total (without mileage)")
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["mileage"] = []
    api2 = WizardApi(ME, trip)
    ctx2, pg2, problems2 = open_app(browser, api2)
    open_trip(pg2, "t7f3k2m9qa", "expenses")
    expect(pg2.locator(".trip-totals")).to_contain_text("Expected total")
    expect(pg2.locator(".trip-totals")).not_to_contain_text("without mileage")
    assert problems == [] and problems2 == []
    ctx.close()
    ctx2.close()


def test_an_inline_expected_edit_does_not_swallow_the_next_click(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {"id": "i0000000001", "date": "2026-11-09", "category": "hotel", "text": "Hotel Hamburg", "expected": 120}
    ]
    api = WizardApi(ME, trip)
    api.hold_next_put = True  # the inline save stays in flight while the user clicks Next
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.fill('input[data-item-exp="i0000000001"]', "100")
    pg.locator('input[data-item-exp="i0000000001"]').press("Tab")
    pg.click("[data-a=tripMove][data-step=review]")
    pg.wait_for_timeout(300)
    assert pg.evaluate("location.hash").endswith("/expenses")
    api.release()  # the inline save completes now; the queued Next then runs
    pg.wait_for_selector("#t_breakdown")
    assert pg.evaluate("location.hash").endswith("/review")
    assert api.trip["items"][0]["expected"] == 100
    assert len(puts(api)) == 2 and api.held == []
    assert problems == []
    ctx.close()


def test_xss_item_text_and_place_name_are_shown_as_text(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {
            "id": "i0000000002",
            "date": "2026-11-09",
            "category": "other",
            "text": '<img src=x onerror="window.__xss=1">',
            "expected": 5,
        }
    ]
    trip["places"] = [{"from_date": "2026-11-09", "country": "DE", "city": "<script>window.__xss=2</script>Evil"}]
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    expect(pg.locator("tr[data-item]")).to_contain_text('<img src=x onerror="window.__xss=1">')
    assert pg.locator("#t_step img").count() == 0
    open_trip(pg, "t7f3k2m9qa", "review")
    assert pg.input_value('tr[data-day="2026-11-09"] [data-f=city]') == "<script>window.__xss=2</script>Evil"
    assert pg.locator("#t_step script").count() == 0
    assert pg.evaluate("window.__xss === undefined")
    assert problems == []
    ctx.close()


def test_an_item_outside_the_period_shows_the_pill(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {"id": "i0000000003", "date": "2026-11-20", "category": "other", "text": "Late workshop", "expected": 1},
        {"id": "i0000000004", "date": "2026-11-09", "category": "other", "text": "Inside", "expected": 1},
    ]
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    expect(pg.locator("tr[data-item]", has_text="Late workshop").locator(".pill")).to_contain_text(
        "Outside the trip period"
    )
    expect(pg.locator("tr[data-item]", has_text="Inside").locator(".pill")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_flight_taxi_and_other_dialogs_store_their_fields(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")

    pg.select_option("#t_add_item", "flight")
    pg.wait_for_selector("#f_from")
    assert pg.input_value("#f_from") == "Berlin" and pg.input_value("#f_to") == "Hamburg"
    pg.fill("#f_date", "2026-11-09")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="Berlin – Hamburg")).to_have_count(1)  # text defaults to the route
    assert api.trip["items"][0]["category"] == "flight" and api.trip["items"][0]["from"] == "Berlin"

    pg.select_option("#t_add_item", "taxi")
    pg.wait_for_selector("#f_from")
    pg.fill("#f_from", "Airport")
    pg.fill("#f_to", "Hotel")
    pg.fill("#f_date", "2026-11-09")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="Airport – Hotel")).to_have_count(1)
    assert api.trip["items"][1]["category"] == "taxi" and api.trip["items"][1]["to"] == "Hotel"

    pg.select_option("#t_add_item", "other")
    pg.wait_for_selector("#f_text")
    pg.fill("#f_text", "Demo parking")
    pg.fill("#f_date", "2026-11-10")
    pg.fill("#f_expected", "12.5")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="Demo parking")).to_have_count(1)
    other = api.trip["items"][2]
    assert other["category"] == "other" and other["expected"] == 12.5 and "from" not in other
    assert problems == []
    ctx.close()


def test_a_car_mileage_row_is_listed_and_saved_with_the_trip(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.select_option("#t_add_item", "car")
    pg.wait_for_selector("#f_km")
    pg.fill("#f_km", "42.5")
    pg.fill("#f_purpose", "Site visit")
    pg.click("#m-save")
    expect(pg.locator("#t_step")).to_contain_text("42,5 km")
    expect(pg.locator("#t_step tr[data-mileage]", has_text="Site visit")).to_have_count(
        2
    )  # items table and mileage table
    assert api.trip["mileage"][-1]["km"] == 42.5
    assert problems == []
    ctx.close()


def test_remove_item_deletes_the_planned_item_after_a_confirm(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {"id": "i0000000005", "date": "2026-11-09", "category": "hotel", "text": "Hotel Hamburg", "expected": 120}
    ]
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.locator("tr[data-item]", has_text="Hotel Hamburg").locator("button", has_text="Remove").click()
    expect(pg.locator("tr[data-item]")).to_have_count(0)
    assert api.trip["items"] == [] and len(api.dialogs) == 1
    assert problems == []
    ctx.close()


def test_edit_item_opens_the_dialog_and_keeps_the_fields_it_does_not_show(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {
            "id": "i0000000006",
            "date": "2026-11-09",
            "category": "hotel",
            "text": "Hotel Hamburg",
            "expected": 120,
            "note_internal": "kept",
        }
    ]
    trip["x_extra"] = "kept too"
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.locator("tr[data-item]", has_text="Hotel Hamburg").locator("button", has_text="Edit").click()
    pg.wait_for_selector("#f_text")
    assert pg.input_value("#f_text") == "Hotel Hamburg" and pg.input_value("#f_expected") == "120"
    pg.fill("#f_text", "Hotel Harbour")
    pg.click("#m-save")
    expect(pg.locator("tr[data-item]", has_text="Hotel Harbour")).to_have_count(1)
    item = api.trip["items"][0]
    assert item["text"] == "Hotel Harbour" and item["note_internal"] == "kept"
    assert puts(api)[-1][3]["x_extra"] == "kept too"  # the unknown top-level field is sent back with the PUT
    assert problems == []
    ctx.close()


def wait_until(pg, check, tries=60):
    """Polls the page (a short wait each time) until check() is true; the caller asserts what it expects."""
    for _ in range(tries):
        if check():
            return
        pg.wait_for_timeout(50)


LEGS_11_09_11_11 = [
    {"depart": "2026-11-09T08:00", "from": "Berlin", "to": "Hamburg", "country": "DE"},
    {"depart": "2026-11-11T18:00", "from": "Hamburg", "to": "Berlin", "country": "DE"},
]


def test_a_trip_moved_to_a_later_week_shifts_its_itinerary_and_the_itinerary_step_saves(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = copy.deepcopy(LEGS_11_09_11_11)
    trip["mileage"] = []  # no mileage row lies in the old week, so the mileage question does not come up here
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_start", "2026-11-16T08:00")
    pg.fill("#t_end", "2026-11-18T18:00")
    pg.click("[data-a=tripMove][data-step=itinerary]")
    pg.wait_for_selector("#t_step tr[data-row]")
    saved = puts(api)
    assert len(saved) == 1, "the Plan step saves once and is not refused"
    assert saved[0][3]["itinerary"] == [
        {"depart": "2026-11-16T08:00", "from": "Berlin", "to": "Hamburg", "country": "DE"},
        {"depart": "2026-11-18T18:00", "from": "Hamburg", "to": "Berlin", "country": "DE"},
    ]  # 7 days later, same times of day
    expect(pg.locator("#toast")).to_contain_text("Itinerary moved by 7 days")
    expect(pg.locator("nav[aria-label='Trip steps'] [aria-current=step]")).to_contain_text("Itinerary")
    assert pg.locator('#t_step tr[data-row="dep"] [data-f=time]').input_value() == "2026-11-16T08:00"
    # the Itinerary step now saves against the moved period (the fake answers 422 for a row outside it)
    pg.click("[data-a=tripMove][data-step=expenses]")
    pg.wait_for_selector("#t_step input[data-item-exp], #t_step .trip-totals, #t_step table")
    assert pg.evaluate("location.hash").endswith("/expenses")
    assert len(puts(api)) == 2
    assert [leg["depart"] for leg in api.trip["itinerary"]] == ["2026-11-16T08:00", "2026-11-18T18:00"]
    assert problems == []
    ctx.close()


def test_a_period_that_cuts_off_a_moved_leg_is_still_refused_with_the_itinerary_message(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["itinerary"] = copy.deepcopy(LEGS_11_09_11_11)
    trip["mileage"] = []
    api = WizardApi(ME, trip)
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "plan")
    pg.fill("#t_start", "2026-11-09T08:00")
    pg.fill("#t_end", "2026-11-10T18:00")  # the return leg departs 2026-11-11: no shift can keep it inside
    pg.click("[data-a=tripMove][data-step=itinerary]")
    expect(pg.locator("#t_error")).to_contain_text("correct the times on the Itinerary step first")
    assert puts(api) == []
    assert problems == []
    ctx.close()


def test_two_quick_expected_edits_on_different_rows_are_both_saved(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {"id": "i0000000001", "date": "2026-11-09", "category": "hotel", "text": "Hotel Hamburg", "expected": 120},
        {"id": "i0000000002", "date": "2026-11-10", "category": "train", "text": "ICE", "expected": 80},
    ]
    api = WizardApi(ME, trip)
    api.hold_next_put = True  # the first inline save stays in flight while the second row is edited
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.fill('input[data-item-exp="i0000000001"]', "100")
    pg.locator('input[data-item-exp="i0000000001"]').press("Tab")
    pg.wait_for_timeout(200)
    pg.fill('input[data-item-exp="i0000000002"]', "90")
    pg.locator('input[data-item-exp="i0000000002"]').press("Tab")
    pg.wait_for_timeout(200)
    api.release()
    wait_until(pg, lambda: len(puts(api)) == 2)
    saved = puts(api)
    assert len(saved) == 2, "the second edit is sent after the first answer, not dropped"
    assert {i["id"]: i.get("expected") for i in saved[-1][3]["items"]} == {"i0000000001": 100, "i0000000002": 90}
    assert {i["id"]: i.get("expected") for i in api.trip["items"]} == {"i0000000001": 100, "i0000000002": 90}
    assert problems == []
    ctx.close()


def test_an_item_dialog_save_waits_for_the_inline_write_and_keeps_both_figures(browser):
    trip = copy.deepcopy(CONTRACT_TRIP)
    trip["items"] = [
        {"id": "i0000000001", "date": "2026-11-09", "category": "hotel", "text": "Hotel Hamburg", "expected": 120},
        {"id": "i0000000002", "date": "2026-11-10", "category": "train", "text": "ICE", "expected": 80},
    ]
    api = WizardApi(ME, trip)
    api.hold_next_put = True  # the inline save of row 1 is in flight while row 2 is edited in its dialog
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.fill('input[data-item-exp="i0000000001"]', "100")
    pg.locator('input[data-item-exp="i0000000001"]').press("Tab")
    pg.wait_for_timeout(200)
    pg.click('[data-a=tripItemEdit][data-id="i0000000002"]')
    pg.fill("#f_expected", "95")
    pg.click("#m-save")
    pg.wait_for_timeout(200)
    api.release()
    wait_until(pg, lambda: len(puts(api)) == 2)
    assert {i["id"]: i.get("expected") for i in api.trip["items"]} == {"i0000000001": 100, "i0000000002": 95}
    assert problems == []
    ctx.close()


def test_next_from_the_expenses_step_sends_no_put_when_nothing_changed(browser):
    api = WizardApi(ME, copy.deepcopy(CONTRACT_TRIP))
    ctx, pg, problems = open_app(browser, api)
    open_trip(pg, "t7f3k2m9qa", "expenses")
    pg.click("[data-a=tripMove][data-step=review]")
    pg.wait_for_selector("#t_breakdown")
    assert pg.evaluate("location.hash").endswith("/review")
    assert puts(api) == []
    assert problems == []
    ctx.close()
