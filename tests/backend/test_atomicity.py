"""Multi-step business operations are all-or-nothing."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def billable(boss_client):
    me = boss_client.get("/api/auth/me").json()
    cust = boss_client.post("/api/customers", json={"name": "ACME", "rate": 100}).json()
    proj = boss_client.post("/api/projects", json={"name": "Site", "customer_id": cust["id"]}).json()
    ids = [
        boss_client.post(
            "/api/entries", json={"user_id": me["id"], "date": f"2026-01-0{i}", "minutes": 60, "project_id": proj["id"]}
        ).json()["id"]
        for i in (1, 2, 3)
    ]
    return cust, ids


def test_invoice_from_time_failing_midway_leaves_nothing_behind(app, boss_client, billable):
    cust, entry_ids = billable
    store = app.state.services.store
    real_save = store.save
    calls = {"entries": 0}

    def flaky(coll, obj, touch=True):
        if coll == "entries":
            calls["entries"] += 1
            if calls["entries"] == 2:
                raise RuntimeError("simulated crash while marking entries as billed")
        return real_save(coll, obj, touch)

    store.save = flaky
    with TestClient(app, raise_server_exceptions=False, headers=boss_client.headers) as c:
        r = c.post("/api/invoices/from-time", json={"customer_id": cust["id"]})
    assert r.status_code == 500
    store.save = real_save
    assert boss_client.get("/api/invoices").json() == []  # no orphan invoice
    entries = boss_client.get("/api/entries").json()
    assert len(entries) == 3 and all("invoice_id" not in e for e in entries)  # nothing marked as billed


def test_invoice_from_time_succeeds_normally_after_a_failed_attempt(boss_client, billable):
    cust, _ = billable
    inv = boss_client.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    assert inv["lines"][0]["amount"] == 300.0
    assert all(e["invoice_id"] == inv["id"] for e in boss_client.get("/api/entries").json())


def test_deleting_an_invoice_releases_its_entries_atomically(app, boss_client, billable):
    cust, _ = billable
    inv = boss_client.post("/api/invoices/from-time", json={"customer_id": cust["id"]}).json()
    store = app.state.services.store
    real_delete = store.delete

    def failing_delete(coll, rid):
        raise RuntimeError("disk error")

    store.delete = failing_delete
    with TestClient(app, raise_server_exceptions=False, headers=boss_client.headers) as c:
        assert c.delete(f"/api/invoices/{inv['id']}").status_code == 500
    store.delete = real_delete
    assert boss_client.get(f"/api/invoices/{inv['id']}").status_code == 200  # still there
    assert all(e.get("invoice_id") == inv["id"] for e in boss_client.get("/api/entries").json())  # still billed
    assert boss_client.delete(f"/api/invoices/{inv['id']}").status_code == 204
    assert all("invoice_id" not in e for e in boss_client.get("/api/entries").json())


def test_accepting_an_offer_creates_project_and_contract_together(app, boss_client):
    cust = boss_client.post("/api/customers", json={"name": "ACME"}).json()
    off = boss_client.post(
        "/api/offers",
        json={
            "customer_id": cust["id"],
            "lines": [{"description": "Dev", "qty": 10, "unit": "h", "price": 90}],
            "plan": {"start": "2026-02-02", "end": "2026-03-27", "hours_per_week": 10},
        },
    ).json()
    boss_client.post(f"/api/offers/{off['id']}/finalize")
    store = app.state.services.store
    real_save = store.save

    def flaky(coll, obj, touch=True):
        if coll == "contracts":
            raise RuntimeError("boom")
        return real_save(coll, obj, touch)

    store.save = flaky
    with TestClient(app, raise_server_exceptions=False, headers=boss_client.headers) as c:
        assert c.post(f"/api/offers/{off['id']}/accept").status_code == 500
    store.save = real_save
    assert boss_client.get("/api/projects").json() == []  # no project without its contract
    assert boss_client.get(f"/api/offers/{off['id']}").json()["status"] == "sent"  # offer not marked accepted
