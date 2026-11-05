"""An invoice from tracked time goes to exactly one customer, and personal projects are never billed."""

from datetime import date

import pytest

TODAY = date.today().isoformat()


@pytest.fixture
def two_customers(boss_client):
    c = boss_client
    me = c.get("/api/auth/me").json()
    a = c.post("/api/customers", json={"name": "Alpha Demo", "rate": 80}).json()
    b = c.post("/api/customers", json={"name": "Beta Demo", "rate": 120}).json()
    pa = c.post("/api/projects", json={"name": "PA", "customer_id": a["id"]}).json()
    pb = c.post("/api/projects", json={"name": "PB", "customer_id": b["id"]}).json()
    mine = c.post("/api/projects", json={"name": "Own", "kind": "internal"}).json()
    for pid, mins in ((pa["id"], 60), (pb["id"], 90), (mine["id"], 30)):
        r = c.post("/api/entries", json={"user_id": me["id"], "date": TODAY, "minutes": mins, "project_id": pid})
        assert r.status_code == 200, r.text
    return c, a, b, pa, pb, mine


def _unbilled(c):
    return {e["project_id"] for e in c.get("/api/entries").json() if not e.get("invoice_id")}


def test_without_customer_or_project_is_refused_and_bills_nothing(two_customers):
    c, *_ = two_customers
    r = c.post("/api/invoices/from-time", json={})
    assert r.status_code == 422
    assert c.get("/api/invoices").json() == []
    assert len(_unbilled(c)) == 3


def test_customer_gets_only_its_own_time(two_customers):
    c, a, b, pa, pb, mine = two_customers
    inv = c.post("/api/invoices/from-time", json={"customer_id": a["id"]}).json()
    assert inv["customer_id"] == a["id"]
    assert [(ln["description"], ln["qty"], ln["price"]) for ln in inv["lines"]] == [("PA", 1.0, 80.0)]
    assert _unbilled(c) == {pb["id"], mine["id"]}  # B's and the personal time stay unbilled


def test_project_alone_bills_that_projects_customer(two_customers):
    c, a, b, pa, pb, mine = two_customers
    inv = c.post("/api/invoices/from-time", json={"project_id": pb["id"]}).json()
    assert inv["customer_id"] == b["id"] and inv["project_id"] == pb["id"]
    assert [(ln["description"], ln["qty"], ln["price"]) for ln in inv["lines"]] == [("PB", 1.5, 120.0)]


def test_personal_project_cannot_be_billed_from_time(two_customers):
    c, a, *_, mine = two_customers
    for body in ({"project_id": mine["id"]}, {"project_id": mine["id"], "customer_id": a["id"]}):
        r = c.post("/api/invoices/from-time", json=body)
        assert r.status_code == 422 and r.json()["detail"] == "Internal projects cannot be billed"
    assert mine["id"] in _unbilled(c)


def test_unknown_customer_or_project_is_404(two_customers):
    c, *_ = two_customers
    assert c.post("/api/invoices/from-time", json={"customer_id": "deadbeef0000"}).status_code == 404
    assert c.post("/api/invoices/from-time", json={"project_id": "deadbeef0000"}).status_code == 404


def test_project_of_another_customer_bills_nothing(two_customers):
    c, a, b, pa, pb, mine = two_customers
    r = c.post("/api/invoices/from-time", json={"customer_id": a["id"], "project_id": pb["id"]})
    assert r.status_code == 400
    assert len(_unbilled(c)) == 3


@pytest.mark.parametrize("coll", ["invoices", "offers"])
def test_document_with_personal_project_is_refused(two_customers, coll):
    c, a, b, pa, pb, mine = two_customers
    body = {"customer_id": a["id"], "project_id": mine["id"], "date": TODAY}
    r = c.post(f"/api/{coll}", json=body)
    assert r.status_code == 409 and r.json()["detail"] == "Internal projects cannot have offers, contracts or invoices"
    ok = c.post(f"/api/{coll}", json={**body, "project_id": pa["id"]})
    assert ok.status_code == 200, ok.text
    r = c.put(f"/api/{coll}/{ok.json()['id']}", json=body)
    assert r.status_code == 409 and r.json()["detail"] == "Internal projects cannot have offers, contracts or invoices"


def test_legacy_document_with_project_made_personal_stays_editable(two_customers, app):
    c, a, *_ = two_customers
    general = c.post("/api/projects", json={"name": "General", "customer_id": a["id"]}).json()
    body = {"customer_id": a["id"], "project_id": general["id"], "date": TODAY}
    inv = c.post("/api/invoices", json=body).json()
    offer = c.post("/api/offers", json=body).json()
    store = app.state.services.store  # a legacy row: made internal behind the API's back, no customer left
    store.save("projects", {"id": general["id"], "name": "General", "kind": "internal"})
    r = c.put(f"/api/invoices/{inv['id']}", json={**body, "title": "still editable"})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/offers/{offer['id']}/to-invoice")
    assert r.status_code == 422 and r.json()["detail"] == "Internal projects cannot be billed"
