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
