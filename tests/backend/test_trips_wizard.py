"""Business trip wizard, backend half (docs/specs/business-trips-wizard.md, section 5 and 6): the itinerary legs, the
planned items with their ids, ``trip_item_id`` on bills, the planning totals and the itinerary in the statement PDF.

Expected figures are hand-computed in the comments; nothing here calls the code under test to compute an expectation.
"""

from .test_trips_api import _pdf_text, make_trip, trip_body

LEG = {"depart": "2026-11-09T08:30", "from": "Berlin", "to": "Hamburg", "mode": "train"}


def item(**kw):
    body = {"date": "2026-11-09", "category": "train", "text": "ICE Berlin - Hamburg", "expected": 80}
    body.update(kw)
    return body


def put_trip(c, trip, **changes):
    """PUT the whole trip back, with the given changes (PUT replaces the trip)."""
    body = {
        "purpose": trip["purpose"],
        "start": trip["start"],
        "end": trip["end"],
        "places": trip.get("places") or [],
        "itinerary": trip.get("itinerary") or [],
        "items": trip.get("items") or [],
    }
    body.update(changes)
    return c.put(f"/api/trips/{trip['id']}", json=body)


# ------------------------------------------------------------------ itinerary
def test_itinerary_round_trips_with_from_and_to(boss_client):
    trip = make_trip(boss_client, itinerary=[LEG])
    assert trip["itinerary"] == [LEG]
    again = boss_client.get(f"/api/trips/{trip['id']}").json()
    assert again["itinerary"] == [LEG]


def test_itinerary_legs_are_sorted_by_departure(boss_client):
    late = {"depart": "2026-11-10T18:00", "from": "Wien", "to": "Berlin"}
    early = {"depart": "2026-11-09T09:00", "from": "Home", "to": "Berlin"}
    trip = make_trip(boss_client, itinerary=[late, early])
    assert [leg["depart"] for leg in trip["itinerary"]] == ["2026-11-09T09:00", "2026-11-10T18:00"]


def test_leg_outside_the_period_is_422(boss_client):
    early = {"depart": "2026-11-08T23:59", "from": "Home", "to": "Berlin"}
    late = {"depart": "2026-11-11T18:01", "from": "Berlin", "to": "Home"}
    assert boss_client.post("/api/trips", json=trip_body(itinerary=[early])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(itinerary=[late])).status_code == 422


def test_leg_on_the_boundaries_is_allowed(boss_client):
    first = {"depart": "2026-11-09T08:00", "from": "Home", "to": "Berlin"}
    last = {"depart": "2026-11-11T18:00", "from": "Berlin", "to": "Home"}
    trip = make_trip(boss_client, itinerary=[first, last])
    assert len(trip["itinerary"]) == 2


def test_itinerary_bounds_and_leg_fields_are_checked(boss_client):
    too_many = [{"depart": "2026-11-09T08:00", "from": "A", "to": "B"}] * 61
    assert boss_client.post("/api/trips", json=trip_body(itinerary=too_many)).status_code == 422
    bad_mode = [{**LEG, "mode": "rocket"}]
    assert boss_client.post("/api/trips", json=trip_body(itinerary=bad_mode)).status_code == 422
    bad_country = [{**LEG, "country": "germany"}]
    assert boss_client.post("/api/trips", json=trip_body(itinerary=bad_country)).status_code == 422
    missing_to = [{"depart": "2026-11-09T08:00", "from": "Berlin"}]
    assert boss_client.post("/api/trips", json=trip_body(itinerary=missing_to)).status_code == 422


def test_sixty_legs_are_the_limit(boss_client):
    legs = [{"depart": "2026-11-09T08:00", "from": "A", "to": "B"}] * 60
    assert boss_client.post("/api/trips", json=trip_body(itinerary=legs)).status_code == 200


# ------------------------------------------------------------------ items and their ids
def test_item_ids_are_generated_and_kept_on_put(boss_client):
    trip = make_trip(boss_client, items=[item(), item(text="Hotel", category="hotel", expected=120)])
    ids = [i["id"] for i in trip["items"]]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert all(len(i) >= 6 and i.isalnum() and i == i.lower() for i in ids)
    # PUT with the ids sent back: the same ids
    kept = put_trip(boss_client, trip, items=trip["items"])
    assert kept.status_code == 200, kept.text
    assert [i["id"] for i in kept.json()["items"]] == ids


def test_item_without_id_gets_a_new_one_and_duplicates_are_refused(boss_client):
    trip = make_trip(boss_client, items=[item(id="")])
    assert trip["items"][0]["id"]
    dup = [item(id="abc123def4"), item(id="abc123def4", text="again")]
    assert boss_client.post("/api/trips", json=trip_body(items=dup)).status_code == 422


def test_item_bounds_and_money_rule(boss_client):
    assert boss_client.post("/api/trips", json=trip_body(items=[item(expected=-1)])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(items=[item(expected=10.005)])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(items=[item(date="2026-13-01")])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(items=[item()] * 101)).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(items=[item()] * 100)).status_code == 200


def test_item_without_expected_is_allowed(boss_client):
    trip = make_trip(boss_client, items=[{"date": "2026-11-09", "category": "other", "text": "Lunch"}])
    assert "expected" not in trip["items"][0]


def test_item_category_is_one_of_the_five_types(boss_client):
    assert boss_client.post("/api/trips", json=trip_body(items=[item(category="food")])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(items=[item(category="car")])).status_code == 422
    for category in ("hotel", "train", "flight", "taxi", "other"):
        assert boss_client.post("/api/trips", json=trip_body(items=[item(category=category)])).status_code == 200


def test_item_from_to_and_date_to_round_trip(boss_client):
    hotel = {
        "date": "2026-11-09",
        "date_to": "2026-11-11",
        "category": "hotel",
        "text": "Hotel Hamburg",
        "expected": 120,
    }
    train = item(**{"from": "Berlin", "to": "Hamburg"})
    trip = make_trip(boss_client, items=[hotel, train])
    got = {i["category"]: i for i in trip["items"]}
    assert got["hotel"]["date_to"] == "2026-11-11"
    assert got["train"]["from"] == "Berlin" and got["train"]["to"] == "Hamburg"


def test_item_date_to_before_date_is_422(boss_client):
    body = item(date_to="2026-11-08")
    assert boss_client.post("/api/trips", json=trip_body(items=[body])).status_code == 422


def test_origin_destination_and_country_round_trip(boss_client):
    trip = make_trip(boss_client, origin="Berlin", destination="Hamburg", destination_country="DE")
    assert (trip["origin"], trip["destination"], trip["destination_country"]) == ("Berlin", "Hamburg", "DE")
    again = boss_client.get(f"/api/trips/{trip['id']}").json()
    assert again["destination_country"] == "DE"


def test_destination_country_must_be_alpha_2_upper_case(boss_client):
    assert boss_client.post("/api/trips", json=trip_body(destination_country="de")).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(destination_country="DEU")).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(destination_country="AT")).status_code == 200


# ------------------------------------------------------------------ trip_item_id on bills
def test_bill_with_item_of_its_trip_is_accepted(boss_client):
    trip = make_trip(boss_client, items=[item()])
    r = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 80, "vat": 0, "trip_id": trip["id"], "trip_item_id": trip["items"][0]["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["trip_item_id"] == trip["items"][0]["id"]


def test_trip_item_of_a_foreign_trip_is_422(boss_client):
    first = make_trip(boss_client, items=[item()])
    second = make_trip(boss_client, items=[item(text="other")])
    r = boss_client.post(
        "/api/expenses",
        json={
            "date": "2026-11-09",
            "net": 80,
            "vat": 0,
            "trip_id": second["id"],
            "trip_item_id": first["items"][0]["id"],
        },
    )
    assert r.status_code == 422
    assert "Unknown trip item" in r.text


def test_trip_item_without_trip_id_is_422(boss_client):
    trip = make_trip(boss_client, items=[item()])
    r = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 80, "vat": 0, "trip_item_id": trip["items"][0]["id"]},
    )
    assert r.status_code == 422
    assert "Unknown trip item" in r.text


def test_deleting_an_item_that_has_bills_is_422_on_put(boss_client):
    trip = make_trip(boss_client, items=[item(), item(text="Hotel", category="hotel")])
    first, second = trip["items"]
    bill = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 80, "vat": 0, "trip_id": trip["id"], "trip_item_id": first["id"]},
    ).json()
    r = put_trip(boss_client, trip, items=[second])
    assert r.status_code == 422, r.text
    assert "Item has bills" in r.text
    # the item is still there; deleting an item without bills works
    assert boss_client.get(f"/api/trips/{trip['id']}").json()["items"] == trip["items"]
    ok = put_trip(boss_client, trip, items=[first])
    assert ok.status_code == 200, ok.text
    assert [i["id"] for i in ok.json()["items"]] == [first["id"]]
    assert boss_client.get(f"/api/expenses/{bill['id']}").json()["trip_item_id"] == first["id"]


def test_deleting_the_trip_unlinks_bills_from_item_too(boss_client):
    trip = make_trip(boss_client, items=[item()])
    bill = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 40, "vat": 0, "trip_id": trip["id"], "trip_item_id": trip["items"][0]["id"]},
    ).json()
    assert boss_client.delete(f"/api/trips/{trip['id']}").status_code == 204
    kept = boss_client.get(f"/api/expenses/{bill['id']}").json()
    assert kept.get("trip_id") is None and kept.get("trip_item_id") is None


# ------------------------------------------------------------------ planning totals
def test_totals_oracle_expected_and_actual(boss_client):
    """Items 80,00 + 120,00 expected = 200,00. Bill on the first item: gross 80,00 + 15,20 = 95,20. Unplanned bill:
    gross 11,90. Actual = 95,20 + 11,90 = 107,10."""
    trip = make_trip(boss_client, items=[item(expected=80), item(text="Hotel", category="hotel", expected=120)])
    first = trip["items"][0]
    assert trip["items_expected_total"] == 200.0
    assert trip["items_actual_total"] == 0.0
    boss_client.post(
        "/api/expenses",
        json={
            "date": "2026-11-09",
            "net": 80,
            "vat": 15.2,
            "vat_rate": 19,
            "trip_id": trip["id"],
            "trip_item_id": first["id"],
        },
    )
    boss_client.post("/api/expenses", json={"date": "2026-11-09", "net": 11.9, "vat": 0, "trip_id": trip["id"]})
    got = boss_client.get(f"/api/trips/{trip['id']}").json()
    assert got["items_expected_total"] == 200.0
    assert got["items_actual_total"] == 107.1
    listed = [t for t in boss_client.get("/api/trips").json() if t["id"] == trip["id"]][0]
    assert listed["items_actual_total"] == 107.1


def test_planned_costs_do_not_change_allowance_mileage_or_statement(boss_client):
    """Expected costs are planning figures only: allowance 56,00 (9-11 Nov, see trip_body) stays the same."""
    plain = make_trip(boss_client)
    planned = make_trip(boss_client, items=[item(expected=500)])
    assert planned["allowance_total"] == plain["allowance_total"] == 56.0
    assert planned["mileage_total"] == 0
    assert planned["bills"] == {"count": 0, "net": 0, "vat": 0}
    text = _pdf_text(boss_client.get(f"/api/trips/{planned['id']}/statement.pdf").content)
    assert "500,00" not in text
    assert not any("Itinerary" in t for t in text)


def test_items_totals_are_zero_for_a_trip_without_items(boss_client):
    trip = make_trip(boss_client)
    assert trip["items_expected_total"] == 0 and trip["items_actual_total"] == 0


def test_client_cannot_set_the_totals(boss_client):
    trip = make_trip(boss_client, items_expected_total=1, items_actual_total=2)
    assert trip["items_expected_total"] == 0 and trip["items_actual_total"] == 0


# ------------------------------------------------------------------ rights
def test_employee_plans_items_on_own_trip_only(boss_client, member_client):
    own = make_trip(member_client, items=[item()])
    assert own["items"][0]["id"]
    foreign = make_trip(boss_client, items=[item(text="boss")])
    # another person's trip is not visible to a limited role: 404, as everywhere in the trips area
    r = put_trip(member_client, foreign, items=foreign["items"] + [item(text="sneaky")])
    assert r.status_code == 404, r.text
    assert boss_client.get(f"/api/trips/{foreign['id']}").json()["items"] == foreign["items"]


# ------------------------------------------------------------------ statement
def test_statement_prints_the_itinerary_table(boss_client):
    trip = make_trip(
        boss_client, itinerary=[LEG, {"depart": "2026-11-11T16:45", "from": "Hamburg", "to": "Berlin", "mode": "train"}]
    )
    text = _pdf_text(boss_client.get(f"/api/trips/{trip['id']}/statement.pdf").content)
    assert "Itinerary" in text
    assert "09.11.2026 08:30" in text and "11.11.2026 16:45" in text
    assert "Berlin" in text and "Hamburg" in text and "train" in text


# ------------------------------------------------------------------ review additions
def test_bill_moved_to_another_trip_with_its_item_is_422_and_clearing_the_item_is_200(boss_client):
    first = make_trip(boss_client, items=[item()])
    second = make_trip(boss_client, items=[item(text="other")])
    bill = boss_client.post(
        "/api/expenses",
        json={
            "date": "2026-11-09",
            "net": 80,
            "vat": 0,
            "trip_id": first["id"],
            "trip_item_id": first["items"][0]["id"],
        },
    ).json()
    moved = boss_client.put(
        f"/api/expenses/{bill['id']}",
        json={
            "date": "2026-11-09",
            "net": 80,
            "vat": 0,
            "trip_id": second["id"],
            "trip_item_id": first["items"][0]["id"],
        },
    )
    assert moved.status_code == 422, moved.text
    assert "Unknown trip item" in moved.text
    cleared = boss_client.put(
        f"/api/expenses/{bill['id']}",
        json={"date": "2026-11-09", "net": 80, "vat": 0, "trip_id": second["id"]},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["trip_id"] == second["id"]
    assert cleared.json().get("trip_item_id") is None


def test_items_actual_total_excludes_bills_of_other_trips(boss_client):
    """Trip A has a bill of 50,00 net and 9,50 VAT (gross 59,50). Trip B has a bill of 1000,00 net, no VAT.
    The actual total of A is 59,50, not 1059,50."""
    trip_a = make_trip(boss_client, items=[item()])
    trip_b = make_trip(boss_client, items=[item(text="other")])
    boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 50, "vat": 9.5, "vat_rate": 19, "trip_id": trip_a["id"]},
    )
    boss_client.post("/api/expenses", json={"date": "2026-11-09", "net": 1000, "vat": 0, "trip_id": trip_b["id"]})
    assert boss_client.get(f"/api/trips/{trip_a['id']}").json()["items_actual_total"] == 59.5
    assert boss_client.get(f"/api/trips/{trip_b['id']}").json()["items_actual_total"] == 1000.0


def test_legs_with_equal_departure_keep_their_order(boss_client):
    first = {"depart": "2026-11-09T09:00", "from": "First", "to": "Hamburg"}
    second = {"depart": "2026-11-09T09:00", "from": "Second", "to": "Berlin"}
    early = {"depart": "2026-11-09T08:30", "from": "Early", "to": "Berlin"}
    trip = make_trip(boss_client, itinerary=[first, second, early])
    assert [leg["from"] for leg in trip["itinerary"]] == ["Early", "First", "Second"]


def test_old_stored_trip_without_itinerary_items_or_origin_loads(app, boss_client):
    """A trip saved before the wizard has no itinerary, items, origin or destination: it still reads and lists."""
    owner = app.state.services.store.list("users")[0]["id"]
    app.state.services.store.save(
        "trips",
        {
            "id": "oldtrip0001",
            "user_id": owner,
            "purpose": "Old customer visit",
            "start": "2026-11-09T08:00",
            "end": "2026-11-11T18:00",
            "places": [{"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"}],
            "status": "draft",
            "created": "2026-10-01T09:00:00Z",
            "updated": "2026-10-01T09:00:00Z",
        },
    )
    got = boss_client.get("/api/trips/oldtrip0001")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["purpose"] == "Old customer visit"
    assert body.get("itinerary", []) == [] and body.get("items", []) == []
    assert body.get("origin") is None and body.get("destination") is None
    assert body["items_actual_total"] == 0 and body["items_expected_total"] == 0
    assert [t["id"] for t in boss_client.get("/api/trips").json()].count("oldtrip0001") == 1


def test_hundred_items_get_a_hundred_unique_ids(boss_client):
    trip = make_trip(boss_client, items=[item(text=f"Item {n}") for n in range(100)])
    ids = [i["id"] for i in trip["items"]]
    assert len(ids) == 100
    assert len(set(ids)) == 100
