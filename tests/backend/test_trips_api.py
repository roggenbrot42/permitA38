"""Business trips over the API (docs/specs/business-trips.md): bounds, figures, trip_id, rights, final lock, statement
PDF and the finance effect. Expected figures are hand-computed in the comments (2026 domestic rates 14 / 28 EUR,
mileage 0,30 EUR/km); nothing here calls the code under test to compute an expectation.
"""

import re
import zlib
from decimal import Decimal

import pytest

from permita38.core.trip_rates import RateTableMissing, load_checklist, load_rates

from .conftest import PASSWORD, _login

HAMBURG = [{"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"}]


def trip_body(**kw):
    """Mon 9 Nov 08:00 to Wed 11 Nov 18:00, domestic: 14 + 28 + 14 = 56,00 EUR allowance."""
    body = {
        "purpose": "Customer visit Hamburg",
        "start": "2026-11-09T08:00",
        "end": "2026-11-11T18:00",
        "places": HAMBURG,
    }
    body.update(kw)
    return body


#: what a final trip needs: a cost centre and every checklist line either added or marked not needed
FINAL_OK = {"cost_centre": "general", "not_needed": ["accommodation", "transportation", "food", "material"]}


def make_trip(c, **kw):
    r = c.post("/api/trips", json=trip_body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def accountant(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "acc1", "name": "Ada Accountant", "role": "accountant", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return _login(app, "acc1")


# ------------------------------------------------------------------ the rate tables themselves
def test_rate_table_values_are_exact_decimals_and_documented():
    t = load_rates(2026)
    assert t["domestic"] == {"from8h": Decimal("14.00"), "h24": Decimal("28.00")}
    assert t["mileage_per_km"] == Decimal("0.30")
    assert t["rules_version"] == "2025-12-05"
    assert t["source"] == "BMF 2025-12-05 GZ IV C 5 - S 2353/00094/007/012"
    assert t["valid_from"] == "2026-01-01"
    assert t["fallback_country"] == "LU"
    for code in ("DE", "AT", "FR", "NL", "CH", "LU", "IT", "ES", "GB", "US"):
        assert code in t["countries"], code
        assert isinstance(t["countries"][code]["h24"], Decimal)
    assert t["countries"]["DE"]["placeholder"] is False
    assert t["countries"]["AT"]["placeholder"] is True  # every row but DE is a marked placeholder


def test_missing_year_has_no_table():
    with pytest.raises(RateTableMissing):
        load_rates(1999)


def test_checklist_has_the_six_items_in_order():
    assert [i["id"] for i in load_checklist()] == [
        "allowance",
        "accommodation",
        "transportation",
        "food",
        "material",
        "cost_centre",
    ]


# ------------------------------------------------------------------ bounds (strict model)
@pytest.mark.parametrize(
    "patch",
    [
        {"end": "2026-11-09T08:00"},  # end == start
        {"end": "2026-11-08T18:00"},  # end before start
        {"start": "2026-02-30T08:00"},  # not a real date
        {"start": "2026-11-09 08:00"},  # wrong format
        {"purpose": "   "},  # blank purpose
        {"start": "2026-01-01T00:00", "end": "2026-04-02T00:00"},  # 91 calendar days (31 Mar 24:00 is the end)
        {"places": [{"from_date": "2026-11-09", "country": "de"}]},  # country must be two capitals
        {"places": [{"from_date": "2026-11-09", "country": "DE"}] * 21},  # more than 20 places
        {"mileage": [{"date": "2026-11-09", "km": 0.05}]},  # below 0.1 km
        {"mileage": [{"date": "2026-11-09", "km": 5000.1}]},  # above 5000 km
        {"mileage": [{"date": "2026-11-09", "km": 12.25}]},  # more than one decimal
        {"mileage": [{"date": "2026-11-12", "km": 10}]},  # outside the trip
        {"meals_provided": [{"date": "2026-10-31", "breakfast": True}]},  # outside the trip
        {"status": "closed"},
        {"not_needed": ["visa", "x" * 300]},
    ],
)
def test_strict_bounds_give_422(boss_client, patch):
    r = boss_client.post("/api/trips", json=trip_body(**patch))
    assert r.status_code == 422, r.text


def test_mileage_limit_is_100_rows(boss_client):
    rows = [{"date": "2026-11-09", "km": 1}] * 101
    assert boss_client.post("/api/trips", json=trip_body(mileage=rows)).status_code == 422


def test_midnight_end_counts_the_day_before(boss_client):
    # 1 Jan 00:00 to 1 Apr 00:00 covers 1 Jan .. 31 Mar: exactly 90 calendar days
    ok = trip_body(start="2026-01-01T00:00", end="2026-04-01T00:00")
    assert boss_client.post("/api/trips/preview", json=ok).status_code == 200


def test_ninety_calendar_days_is_the_limit(boss_client):
    ok = trip_body(start="2026-01-01T08:00", end="2026-03-31T18:00")  # exactly 90 calendar days
    assert boss_client.post("/api/trips", json=ok).status_code == 200


def test_unknown_fields_round_trip_through_extra(boss_client):
    made = make_trip(boss_client, client_ref={"nested": [1, 2]}, note_for_later="keep me")
    got = boss_client.get(f"/api/trips/{made['id']}").json()
    assert got["client_ref"] == {"nested": [1, 2]}
    assert got["note_for_later"] == "keep me"


# ------------------------------------------------------------------ figures: preview and saved trip agree
def test_saved_trip_has_the_hand_computed_figures(boss_client):
    made = make_trip(
        boss_client,
        meals_provided=[{"date": "2026-11-10", "breakfast": True}],
        mileage=[{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}],
    )
    assert made["allowance_total"] == 50.40  # 14 + (28 - 5,60) + 14
    assert made["mileage_total"] == 71.25  # 237,5 x 0,30
    assert made["rules_version"] == "2025-12-05"
    assert [d["amount"] for d in made["days"]] == [14.00, 22.40, 14.00]
    assert made["status"] == "draft"


def test_preview_equals_the_saved_figures_and_saves_nothing(boss_client):
    body = trip_body(
        meals_provided=[{"date": "2026-11-10", "breakfast": True, "lunch": True}],
        mileage=[{"date": "2026-11-10", "from": "A", "to": "B", "km": 100.4}],
    )
    before = len(boss_client.get("/api/trips").json())
    pv = boss_client.post("/api/trips/preview", json=body)
    assert pv.status_code == 200, pv.text
    assert len(boss_client.get("/api/trips").json()) == before  # nothing saved
    saved = boss_client.post("/api/trips", json=body).json()
    assert pv.json()["days"] == saved["days"]
    assert pv.json()["allowance_total"] == saved["allowance_total"]
    assert pv.json()["mileage_total"] == saved["mileage_total"] == 30.12  # 100,4 x 0,30
    # 14 + (28 - 5,60 - 11,20) + 14 = 39,20
    assert saved["allowance_total"] == 39.20


def test_preview_validates_like_save(boss_client):
    assert boss_client.post("/api/trips/preview", json=trip_body(end="2026-11-09T08:00")).status_code == 422


def test_unknown_customer_project_or_checklist_item_is_422(boss_client):
    assert boss_client.post("/api/trips", json=trip_body(customer_id="abc123def456")).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(project_id="abc123def456")).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(not_needed=["visa"])).status_code == 422
    assert boss_client.post("/api/trips", json=trip_body(not_needed=["material"])).status_code == 200


def test_missing_rate_table_for_the_year_is_422(boss_client):
    r = boss_client.post("/api/trips", json=trip_body(start="2025-11-09T08:00", end="2025-11-09T18:00"))
    assert r.status_code == 422, r.text


def test_unknown_country_is_saved_with_the_luxembourg_note(boss_client):
    made = make_trip(boss_client, places=[{"from_date": "2026-11-09", "country": "XX"}])
    assert "Country not in the rate table" in made["days"][0]["note"]
    assert made["days"][0]["country"] == "LU"  # the row that was used


# ------------------------------------------------------------------ expenses link to trips
def test_expense_with_known_trip_id_round_trips(boss_client):
    trip = make_trip(boss_client)
    r = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 100, "vat": 19, "vat_rate": 19, "category": "Travel", "trip_id": trip["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["trip_id"] == trip["id"]
    listed = boss_client.get("/api/expenses").json()
    assert any(e.get("trip_id") == trip["id"] for e in listed)


def test_expense_with_unknown_trip_id_is_422(boss_client):
    r = boss_client.post(
        "/api/expenses",
        json={"date": "2026-11-09", "net": 100, "vat": 0, "trip_id": "abc123def456", "category": "Travel"},
    )
    assert r.status_code == 422
    assert "trip" in r.text.lower()


def test_deleting_a_trip_keeps_its_bills_unlinked(boss_client):
    trip = make_trip(boss_client)
    exp = boss_client.post(
        "/api/expenses", json={"date": "2026-11-09", "net": 40, "vat": 0, "trip_id": trip["id"]}
    ).json()
    assert boss_client.delete(f"/api/trips/{trip['id']}").status_code == 204
    kept = boss_client.get(f"/api/expenses/{exp['id']}").json()
    assert kept["id"] == exp["id"] and kept["net"] == 40
    assert kept.get("trip_id") is None


# ------------------------------------------------------------------ HTTP status codes
def test_anonymous_gets_401(client):
    assert client.get("/api/trips").status_code == 401
    assert client.post("/api/trips", json=trip_body()).status_code == 401
    assert client.post("/api/trips/preview", json=trip_body()).status_code == 401


def test_unknown_or_malformed_ids_are_404(boss_client):
    assert boss_client.get("/api/trips/abc123def456").status_code == 404
    assert boss_client.get("/api/trips/not-an-id!").status_code == 404
    assert boss_client.put("/api/trips/abc123def456", json=trip_body()).status_code == 404
    assert boss_client.delete("/api/trips/abc123def456").status_code == 404


# ------------------------------------------------------------------ rights
def test_employee_sees_and_changes_only_own_trips(boss_client, member_client):
    boss_trip = make_trip(boss_client, title="boss trip")
    mine = make_trip(member_client, title="my trip")
    ids = {t["id"] for t in member_client.get("/api/trips").json()}
    assert ids == {mine["id"]}
    assert member_client.get(f"/api/trips/{boss_trip['id']}").status_code == 404  # not revealed
    assert member_client.put(f"/api/trips/{boss_trip['id']}", json=trip_body()).status_code == 404
    assert member_client.delete(f"/api/trips/{boss_trip['id']}").status_code == 404
    assert member_client.get(f"/api/trips/{mine['id']}").status_code == 200
    assert member_client.get(f"/api/trips/{mine['id']}/statement.pdf").status_code == 200
    assert boss_client.get("/api/trips").status_code == 200  # the boss sees both


def test_accountant_reads_every_trip_and_changes_none(boss_client, accountant, member_client):
    trip = make_trip(member_client)
    assert accountant.get("/api/trips").status_code == 200
    assert accountant.get(f"/api/trips/{trip['id']}").status_code == 200
    assert accountant.get(f"/api/trips/{trip['id']}/statement.pdf").status_code == 200
    assert accountant.post("/api/trips", json=trip_body()).status_code == 403
    assert accountant.post("/api/trips/preview", json=trip_body()).status_code == 403  # preview needs write (contract)
    assert accountant.put(f"/api/trips/{trip['id']}", json=trip_body()).status_code == 403
    assert accountant.delete(f"/api/trips/{trip['id']}").status_code == 403


def test_employee_cannot_reopen_but_the_boss_can(boss_client, member_client):
    mine = make_trip(member_client)
    assert member_client.put(f"/api/trips/{mine['id']}", json=trip_body(**FINAL_OK, status="final")).status_code == 200
    assert member_client.post(f"/api/trips/{mine['id']}/reopen").status_code == 403
    assert boss_client.post(f"/api/trips/{mine['id']}/reopen").status_code == 200


# ------------------------------------------------------------------ final lock
def test_final_trip_is_locked_until_the_boss_reopens_it(boss_client):
    trip = make_trip(boss_client)
    fin = boss_client.put(f"/api/trips/{trip['id']}", json=trip_body(**FINAL_OK, status="final", title="done"))
    assert fin.status_code == 200, fin.text
    assert fin.json()["status"] == "final"
    assert boss_client.put(f"/api/trips/{trip['id']}", json=trip_body(title="again")).status_code == 409
    assert boss_client.delete(f"/api/trips/{trip['id']}").status_code == 409
    assert boss_client.post(f"/api/trips/{trip['id']}/reopen").json()["status"] == "draft"
    assert boss_client.post(f"/api/trips/{trip['id']}/reopen").status_code == 409  # not final any more
    assert boss_client.put(f"/api/trips/{trip['id']}", json=trip_body(title="again")).status_code == 200


def test_final_trip_keeps_its_stored_figures(boss_client):
    trip = make_trip(boss_client)
    fin = boss_client.put(f"/api/trips/{trip['id']}", json=trip_body(**FINAL_OK, status="final")).json()
    assert fin["allowance_total"] == trip["allowance_total"] == 56.00


# ------------------------------------------------------------------ statement PDF
def _pdf_text(data: bytes) -> list[str]:
    """Every text string written by the PDF content streams (unescaped)."""
    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        try:
            content = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for s in re.finditer(rb"\(((?:\\.|[^\\)])*)\) Tj", content):
            out.append(re.sub(rb"\\(.)", rb"\1", s.group(1)).decode("cp1252"))
    return out


def test_statement_pdf_contains_the_day_lines_and_totals(boss_client):
    trip = make_trip(boss_client, mileage=[{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}])
    r = boss_client.get(f"/api/trips/{trip['id']}/statement.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    text = _pdf_text(r.content)
    assert "09.11.2026" in text
    assert "11.11.2026" in text
    assert "14,00" in text and "28,00" in text
    assert "56,00" in text  # allowance total
    assert "71,25" in text  # mileage total
    assert any("2025-12-05" in t for t in text)  # rules version


def test_statement_escapes_user_text(boss_client):
    hostile = 'Visit (Mueller) \\ "x"'
    trip = make_trip(boss_client, purpose=hostile)
    data = boss_client.get(f"/api/trips/{trip['id']}/statement.pdf").content
    assert any(hostile in t for t in _pdf_text(data))  # read back as the same text
    raw = b"".join(zlib.decompress(m.group(1)) for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S))
    assert b"Visit \\(Mueller\\) \\\\" in raw  # written with PDF escapes, never as bare parentheses


# ------------------------------------------------------------------ finance
def test_a_draft_trip_changes_no_finance_figure(boss_client):
    before = boss_client.get("/api/finance", params={"year": 2026}).json()
    make_trip(boss_client, mileage=[{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}])
    after = boss_client.get("/api/finance", params={"year": 2026}).json()
    assert after["months"] == before["months"]
    assert "Mileage" not in after["categories"]


def test_finalising_a_trip_adds_exactly_the_expected_cents(boss_client):
    before = boss_client.get("/api/finance", params={"year": 2026}).json()
    nov_before = before["months"][10]
    trip = make_trip(boss_client, mileage=[{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}])
    assert trip["allowance_total"] == 56.00 and trip["mileage_total"] == 71.25
    fin = boss_client.put(
        f"/api/trips/{trip['id']}",
        json=trip_body(
            **FINAL_OK, status="final", mileage=[{"date": "2026-11-09", "from": "Kiel", "to": "Hamburg", "km": 237.5}]
        ),
    )
    assert fin.status_code == 200, fin.text
    after = boss_client.get("/api/finance", params={"year": 2026}).json()
    nov = after["months"][10]
    # allowance 56,00 + mileage 71,25: deductible cost, no input VAT
    assert round(nov["expenses"] - nov_before["expenses"], 2) == 127.25
    assert round(nov["vat_in"] - nov_before["vat_in"], 2) == 0.00
    assert round(after["totals"]["expenses"] - before["totals"]["expenses"], 2) == 127.25
    assert after["categories"]["Meal allowance"] == 56.00
    assert after["categories"]["Mileage"] == 71.25
    assert round(after["totals"]["profit"] - before["totals"]["profit"], 2) == -127.25


def test_a_december_trip_changes_only_december(boss_client):
    before = boss_client.get("/api/finance", params={"year": 2026}).json()
    made = make_trip(boss_client, start="2026-12-30T08:00", end="2026-12-30T18:00", places=[])
    boss_client.put(
        f"/api/trips/{made['id']}",
        json=trip_body(**FINAL_OK, status="final", start="2026-12-30T08:00", end="2026-12-30T18:00", places=[]),
    )
    assert made["allowance_total"] == 14.00  # one day of 10 hours, domestic: 14,00
    after = boss_client.get("/api/finance", params={"year": 2026}).json()
    assert round(after["months"][11]["expenses"] - before["months"][11]["expenses"], 2) == 14.00
    assert round(after["months"][10]["expenses"] - before["months"][10]["expenses"], 2) == 0.00


# ------------------------------------------------------------------ checklist, bills, finalising (contract)
def test_final_is_refused_while_checklist_lines_are_open_unless_forced(boss_client):
    trip = make_trip(boss_client)  # no cost centre, nothing marked not needed
    r = boss_client.put(f"/api/trips/{trip['id']}", json=trip_body(status="final"))
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Checklist has open items: Hotel / accommodation, Transportation, Material, Cost centre / project"
    )
    forced = boss_client.put(f"/api/trips/{trip['id']}?force=true", json=trip_body(status="final"))
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "final"


def test_create_as_final_is_checked_the_same_way(boss_client):
    assert boss_client.post("/api/trips", json=trip_body(status="final")).status_code == 409
    assert boss_client.post("/api/trips?force=true", json=trip_body(status="final")).status_code == 200


def test_checklist_states_and_bills_follow_the_linked_bills(boss_client):
    trip = make_trip(
        boss_client,
        cost_centre="general",
        not_needed=["material"],
        mileage=[{"date": "2026-11-09", "from": "A", "to": "B", "km": 100}],
    )
    for net, cat in ((100.0, "Accommodation"), (50.0, "Accommodation")):
        boss_client.post(
            "/api/expenses", json={"date": "2026-11-09", "net": net, "vat": 0, "category": cat, "trip_id": trip["id"]}
        )
    got = boss_client.get(f"/api/trips/{trip['id']}").json()
    assert got["bills"] == {"count": 2, "net": 150.0, "vat": 0.0}
    rows = {r["id"]: r for r in got["checklist"]}
    assert [r["id"] for r in got["checklist"]] == [
        "allowance",
        "accommodation",
        "transportation",
        "food",
        "material",
        "cost_centre",
    ]
    assert rows["allowance"]["state"] == "added" and rows["allowance"]["count"] == 3
    assert rows["allowance"]["sum"] == 56.0
    assert rows["accommodation"] == {**rows["accommodation"], "state": "added", "count": 2, "sum": 150.0}
    assert rows["transportation"] == {
        **rows["transportation"],
        "state": "added",
        "count": 1,
        "sum": 30.0,
    }  # 100 km x 0,30
    assert rows["material"]["state"] == "not_needed"
    assert rows["food"]["state"] == "added"
    assert rows["cost_centre"]["state"] == "added"


def test_cost_centre_project_needs_a_customer_or_project(boss_client):
    r = boss_client.post("/api/trips", json=trip_body(cost_centre="project"))
    assert r.status_code == 422
    assert "Choose a customer or project, or general" in r.text


def test_list_is_sorted_by_start_descending(boss_client):
    early = make_trip(boss_client, start="2026-03-02T08:00", end="2026-03-02T18:00", places=[])
    late = make_trip(boss_client, start="2026-09-02T08:00", end="2026-09-02T18:00", places=[])
    ids = [t["id"] for t in boss_client.get("/api/trips").json()]
    assert ids.index(late["id"]) < ids.index(early["id"])


def test_statement_says_draft_until_final(boss_client):
    trip = make_trip(boss_client)
    text = _pdf_text(boss_client.get(f"/api/trips/{trip['id']}/statement.pdf").content)
    assert "Status: Draft" in text


# ------------------------------------------------------------------ review round 2: rights, lock, owner, preview
def test_employee_gets_404_for_another_persons_statement(boss_client, member_client):
    boss_trip = make_trip(boss_client)
    assert member_client.get(f"/api/trips/{boss_trip['id']}/statement.pdf").status_code == 404


def test_accountant_cannot_reopen_and_unknown_ids_are_404(boss_client, accountant):
    final = boss_client.put(f"/api/trips/{make_trip(boss_client)['id']}", json=trip_body(**FINAL_OK, status="final"))
    assert final.status_code == 200
    assert accountant.post(f"/api/trips/{final.json()['id']}/reopen").status_code == 403
    assert accountant.get("/api/trips/abc123def456").status_code == 404


def test_employee_cannot_change_a_final_trip_and_force_does_not_skip_the_lock(boss_client, member_client):
    mine = make_trip(member_client)
    assert member_client.put(f"/api/trips/{mine['id']}", json=trip_body(**FINAL_OK, status="final")).status_code == 200
    assert member_client.put(f"/api/trips/{mine['id']}", json=trip_body()).status_code == 409
    assert member_client.delete(f"/api/trips/{mine['id']}").status_code == 409
    forced = member_client.put(f"/api/trips/{mine['id']}?force=true", json=trip_body(**FINAL_OK, status="final"))
    assert forced.status_code == 409  # force skips only the checklist check, never the final lock


def test_boss_edit_keeps_the_owner_and_client_user_id_is_ignored(boss_client, member_client):
    mine = make_trip(member_client, user_id="abc123def456")
    assert mine["user_id"] == member_client.user_id
    edited = boss_client.put(f"/api/trips/{mine['id']}", json=trip_body(title="edited by boss"))
    assert edited.status_code == 200
    assert edited.json()["user_id"] == member_client.user_id


def test_preview_ignores_response_only_fields_in_the_body(boss_client):
    plain = boss_client.post("/api/trips/preview", json=trip_body()).json()
    noisy = boss_client.post(
        "/api/trips/preview",
        json=trip_body(
            bills={"count": 9, "net": 9, "vat": 9},
            checklist=[],
            days=[],
            allowance_total=999,
            mileage_total=999,
            rules_version="x",
            status="final",
        ),
    ).json()
    assert noisy["days"] == plain["days"]
    assert noisy["allowance_total"] == plain["allowance_total"] == 56.00


def test_year_without_table_is_422_with_the_contract_message(boss_client):
    r = boss_client.post("/api/trips/preview", json=trip_body(start="2031-05-04T08:00", end="2031-05-04T18:00"))
    assert r.status_code == 422
    assert r.json()["detail"] == "No rate table for year 2031"


def test_expense_trip_id_rights_follow_the_trips_area(boss_client, accountant, app, monkeypatch):
    from permita38.core import rights
    from permita38.core.rights import Scope

    boss_trip = make_trip(boss_client)
    body = {"date": "2026-11-09", "net": 10, "vat": 0, "trip_id": boss_trip["id"]}
    assert accountant.post("/api/expenses", json=body).status_code == 200  # trips read: all
    monkeypatch.setitem(rights.MATRIX["accountant"], "trips", (Scope.NONE, Scope.NONE))
    assert accountant.post("/api/expenses", json=body).status_code == 422  # trips none -> unknown trip
    monkeypatch.setitem(rights.MATRIX["accountant"], "trips", (Scope.LIMITED, Scope.NONE))
    assert accountant.post("/api/expenses", json=body).status_code == 422  # limited: not their trip
    monkeypatch.setitem(rights.MATRIX["accountant"], "trips", (Scope.ALL, Scope.NONE))
    assert accountant.post("/api/expenses", json=body).status_code == 200


def test_finance_across_a_year_boundary_books_allowance_per_day_and_mileage_at_start(boss_client, monkeypatch):
    from permita38.core.trip_rates import load_rates

    synthetic_2027 = {
        "year": 2027,
        "rules_version": "2027-SYNTH",
        "mileage_per_km": Decimal("0.30"),
        "fallback_country": "LU",
        "domestic": {"from8h": Decimal("15.00"), "h24": Decimal("30.00")},
        "countries": {
            "DE": {"name": "Germany", "from8h": Decimal("15.00"), "h24": Decimal("30.00"), "placeholder": False}
        },
    }

    def rates(year):
        return load_rates(2026) if year == 2026 else synthetic_2027

    monkeypatch.setattr("permita38.core.service.trips.load_rates", rates)  # no 2027 table exists in the repository
    before26 = boss_client.get("/api/finance", params={"year": 2026}).json()
    before27 = boss_client.get("/api/finance", params={"year": 2027}).json()
    body = trip_body(
        start="2026-12-30T08:00",
        end="2027-01-02T18:00",
        places=[],
        mileage=[{"date": "2026-12-30", "from": "A", "to": "B", "km": 100}],
        **FINAL_OK,
        status="final",
    )
    made = boss_client.post("/api/trips", json=body)
    assert made.status_code == 200, made.text
    assert [d["amount"] for d in made.json()["days"]] == [14.00, 28.00, 30.00, 15.00]
    after26 = boss_client.get("/api/finance", params={"year": 2026}).json()
    after27 = boss_client.get("/api/finance", params={"year": 2027}).json()
    # 2026: allowance 14 + 28 and mileage 100 km x 0,30 (start year) = 72,00
    assert round(after26["totals"]["expenses"] - before26["totals"]["expenses"], 2) == 72.00
    # 2027: allowance 30 + 15 = 45,00 and no mileage
    assert round(after27["totals"]["expenses"] - before27["totals"]["expenses"], 2) == 45.00


def test_statement_cells_are_cut_to_their_column_and_every_text_is_escaped(boss_client):
    city = "Ort (Nord) \\ " + "y" * 90  # long and hostile
    mile = {
        "date": "2026-11-09",
        "from": "Von (x) \\ " + "z" * 80,
        "to": "Nach (y)",
        "km": 10,
        "purpose": "Zweck \\ (1)",
    }
    trip = make_trip(
        boss_client,
        title="Titel (A) \\ B",
        places=[{"from_date": "2026-11-09", "country": "AT", "city": city}],
        mileage=[mile],
    )
    data = boss_client.get(f"/api/trips/{trip['id']}/statement.pdf").content
    texts = _pdf_text(data)
    raw = b"".join(zlib.decompress(m.group(1)) for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S))
    assert any("Title: Titel (A) \\ B" in t for t in texts)  # the header is wrapped, never cut
    assert any(t.startswith("Ort (Nord) \\ y") and t.endswith("...") for t in texts)  # city cell cut
    assert any(t.startswith("Von (x) \\ z") and t.endswith("...") for t in texts)  # mileage 'from' cut
    assert any(t == "Zweck \\ (1)" for t in texts)  # short text stays whole
    assert any(t == "Nach (y)" for t in texts)
    assert b"Ort \\(Nord\\) \\\\ y" in raw  # written with PDF escapes


def test_statement_prints_the_placeholder_note_in_full(boss_client):
    trip = make_trip(boss_client, places=[{"from_date": "2026-11-09", "country": "AT"}])
    texts = _pdf_text(boss_client.get(f"/api/trips/{trip['id']}/statement.pdf").content)
    assert any("Rate for AT is a placeholder: verify" in t for t in texts)
