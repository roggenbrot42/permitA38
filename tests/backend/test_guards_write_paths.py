"""Step 0 of docs/specs/db-constraints.md as a test: every write path stores the valid edge values of the guarded
columns (minutes 1, rate 0, start == end, net 0) without a refusal by the database guards: entries, timer, invoices,
offers, projects, contracts, absences, allocations, trips (shortest period), expenses, scans of receipts and old
documents, incoming offers and the CSV import. Plus one negative control per guard kind through the storage layer,
which bypasses the request validation. All data is invented.
"""

import pytest

from permita38.core.repository import ConstraintViolation

DAY = "2026-03-02"
CSV = (
    "Nummer;Datum;Kunde;Netto;USt;Brutto\n"
    "RE-2026-0001;02.01.2026;Acme;10,00;1,90;11,90\n"
    "RE-2026-0002;03.01.2026;Acme;10,00;1,90;11,90\n"
)


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


@pytest.fixture
def me(boss_client):
    return boss_client.get("/api/auth/me").json()


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _customer(c, **kw):
    return ok(c.post("/api/customers", json={"name": "ACME GmbH", **kw}))


def _draft(c, customer_id, **kw):
    body = {"customer_id": customer_id, "lines": [{"description": "Work", "qty": 1, "unit": "h", "price": 10}], **kw}
    return ok(c.post("/api/invoices", json=body))


# ------------------------------------------------------------------ time
def test_entry_with_one_minute_and_rate_zero(boss_client, me):
    r = ok(boss_client.post("/api/entries", json={"user_id": me["id"], "date": DAY, "minutes": 1, "rate": 0}))
    assert r["minutes"] == 1 and r["rate"] == 0


def test_timer_start_stop_makes_an_entry(boss_client):
    ok(boss_client.post("/api/timer/start", json={"note": "inv001"}))
    r = ok(boss_client.post("/api/timer/stop", json={}))
    assert r["entry"]["minutes"] >= 1


def test_invoice_from_time_on_a_single_day(boss_client, me):
    cust = _customer(boss_client, rate=0)
    proj = ok(boss_client.post("/api/projects", json={"name": "Website", "customer_id": cust["id"]}))
    body = {"user_id": me["id"], "date": DAY, "minutes": 60, "project_id": proj["id"]}
    ok(boss_client.post("/api/entries", json=body))
    inv = ok(boss_client.post("/api/invoices/from-time", json={"customer_id": cust["id"]}))
    assert inv["service_from"] == DAY and inv["service_to"] == DAY


# ------------------------------------------------------------------ documents
def test_finalize_invoices_and_offers_allocates_numbers(boss_client):
    cust = _customer(boss_client)
    a = ok(boss_client.post(f"/api/invoices/{_draft(boss_client, cust['id'])['id']}/finalize"))
    b = ok(boss_client.post(f"/api/invoices/{_draft(boss_client, cust['id'])['id']}/finalize"))
    assert a["number"].endswith("-0001") and b["number"].endswith("-0002")
    offer = ok(
        boss_client.post(
            "/api/offers",
            json={"customer_id": cust["id"], "lines": [{"description": "Dev", "qty": 1, "unit": "h", "price": 10}]},
        )
    )
    assert ok(boss_client.post(f"/api/offers/{offer['id']}/finalize"))["number"]


def test_invoice_with_equal_service_period(boss_client):
    cust = _customer(boss_client)
    inv = _draft(boss_client, cust["id"], service_from=DAY, service_to=DAY)
    assert inv["service_from"] == DAY and inv["service_to"] == DAY
    ok(boss_client.post(f"/api/invoices/{inv['id']}/finalize"))


def test_offer_accept_with_a_one_day_plan_and_to_invoice(boss_client):
    cust = _customer(boss_client, rate=0)
    offer = ok(
        boss_client.post(
            "/api/offers",
            json={
                "customer_id": cust["id"],
                "lines": [{"description": "Dev", "qty": 8, "unit": "h", "price": 0}],
                "plan": {"start": DAY, "end": DAY, "hours_per_week": 8},
            },
        )
    )
    ok(boss_client.post(f"/api/offers/{offer['id']}/finalize"))
    acc = ok(boss_client.post(f"/api/offers/{offer['id']}/accept"))
    assert acc["project"]["id"] and acc["contract"]["id"]
    assert ok(boss_client.post(f"/api/offers/{offer['id']}/to-invoice"))["status"] == "draft"


# ------------------------------------------------------------------ periods and rates
def test_project_contract_absence_with_start_equal_end(boss_client, me):
    p = ok(boss_client.post("/api/projects", json={"name": "P", "start": DAY, "end": DAY, "rate": 0}))
    assert p["start"] == p["end"] == DAY
    # zero hours and "both hour fields" are refused by the API before the database guards
    assert boss_client.post("/api/projects", json={"name": "P0", "budget_hours": 0}).status_code == 422
    body = {"title": "C", "project_id": p["id"], "start": DAY, "end": DAY, "rate": 0}
    assert boss_client.post("/api/contracts", json={**body, "hours_per_week": 0}).status_code == 422
    assert boss_client.post("/api/contracts", json={**body, "total_hours": 0}).status_code == 422
    assert boss_client.post("/api/contracts", json={**body, "hours_per_week": 1, "total_hours": 1}).status_code == 422
    k = ok(boss_client.post("/api/contracts", json={**body, "total_hours": 0.0001}))
    assert k["start"] == k["end"] == DAY
    a = ok(boss_client.post("/api/absences", json={"user_id": me["id"], "start": DAY, "end": DAY}))
    assert a["start"] == a["end"] == DAY


def test_allocation_with_start_equal_end(boss_client, me):
    p = ok(boss_client.post("/api/projects", json={"name": "Demo project P"}))
    body = {"user_id": me["id"], "project_id": p["id"], "start": DAY, "end": DAY, "hours_per_week": 5.0}
    ok(boss_client.post("/api/allocations", json=body), 201)


def test_rate_zero_on_customer_task_supplier(boss_client):
    assert _customer(boss_client, rate=0)["rate"] == 0
    p = ok(boss_client.post("/api/projects", json={"name": "P"}))
    assert ok(boss_client.post("/api/tasks", json={"project_id": p["id"], "name": "T", "rate": 0}))["rate"] == 0
    ok(
        boss_client.post(
            "/api/suppliers",
            json={"name": "Demo Studio", "kind": "person-capacity", "workdays": [0, 1], "weekly_hours": 10, "rate": 0},
        ),
        201,
    )


# ------------------------------------------------------------------ expenses, procurement
def test_expenses_with_zero_amounts_and_itemised(boss_client):
    z = ok(boss_client.post("/api/expenses", json={"date": DAY, "net": 0, "vat": 0, "paid_on": DAY}))
    assert z["net"] == 0 and z["vat"] == 0
    lines = [{"description": "inv001", "gross": 10.0, "vat_rate": 0}]
    item = ok(boss_client.post("/api/expenses", json={"date": DAY, "lines": lines}))
    assert item["net"] == 10.0 and item["vat"] == 0


def test_incoming_offer_with_dates(boss_client):
    s = ok(boss_client.post("/api/suppliers", json={"name": "Demo Print Shop GmbH", "kind": "company"}), 201)
    body = {
        "supplier_id": s["id"],
        "reference": "ANG-2026-117",
        "title": "inv001",
        "received_on": "2026-02-20",
        "valid_until": "2026-03-31",
        "lines": [{"description": "Print", "quantity": 1, "unit": "h", "unit_price": 10.0}],
    }
    ok(boss_client.post("/api/incoming-offers", json=body), 201)


# ------------------------------------------------------------------ trips and scans
def test_trip_with_the_shortest_period_and_equal_ends(boss_client, store):
    from .test_trips_api import trip_body

    ok(boss_client.post("/api/trips", json=trip_body(start="2026-03-10T08:00", end="2026-03-10T08:01")))
    n = len(store.list("trips"))
    r = boss_client.post("/api/trips", json=trip_body(start="2026-03-10T08:00", end="2026-03-10T08:00"))
    assert r.status_code == 422, r.text
    assert len(store.list("trips")) == n == 1


def test_receipt_scan_and_document_scan_are_booked_and_added(boss_client, store, scan_stubs):
    from .test_document_scans_service import INVOICE as SCAN_INVOICE
    from .test_receipts_api import SINGLE

    receipt_reader, doc_reader = scan_stubs
    st = store.settings()
    company = {"company_name": "Max Muster IT-Beratung", "owner_name": "Max Muster", "vat_id": "DE123456789"}
    store.save_settings({**st, **company, "payment_days": 30})
    booked = boss_client.post("/api/receipts", files={"file": ("bill.pdf", receipt_reader.add(SINGLE))})
    assert booked.status_code == 201 and booked.json()["outcome"] == "booked", booked.text
    scanned = boss_client.post("/api/document-scans", files={"file": ("old.pdf", doc_reader.add(SCAN_INVOICE))})
    assert scanned.status_code == 201 and scanned.json()["status"] == "added", scanned.text


# ------------------------------------------------------------------ document importer
def test_csv_import_creates_invoices_with_distinct_numbers(boss_client, store):
    files = {"file": ("x.csv", CSV.encode(), "text/csv")}
    ok(boss_client.post("/api/import/run", files=files, data={"type": "invoices"}))
    assert sorted(i["number"] for i in store.list("invoices")) == ["RE-2026-0001", "RE-2026-0002"]


# ------------------------------------------------------------------ negative controls (bypass the validation)
def test_store_refuses_a_duplicate_number(store):
    store.save("invoices", {"number": "RE-2026-0001", "title": "inv001"})
    with pytest.raises(ConstraintViolation) as e:
        store.save("invoices", {"number": "RE-2026-0001", "title": "inv002"})
    assert e.value.code == "numbering.duplicate"


def test_store_refuses_a_bad_date_format(store):
    with pytest.raises(ConstraintViolation) as e:
        store.save("entries", {"date": "2026-3-2", "minutes": 10})
    assert e.value.code == "format.date"


def test_store_refuses_a_negative_value(store):
    with pytest.raises(ConstraintViolation) as e:
        store.save("expenses", {"date": DAY, "net": -10.0, "vat": 0})
    assert e.value.code == "range.negative"


def test_store_refuses_a_reversed_period(store):
    with pytest.raises(ConstraintViolation) as e:
        store.save("projects", {"name": "P", "start": "2026-03-03", "end": DAY})
    assert e.value.code == "state.period_reversed"
