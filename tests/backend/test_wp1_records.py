"""WP1 records: project kind, contracts without user_ids, internal projects carry no documents."""

from datetime import date

from permita38.core.service.project_customer import project_kind

TODAY = date.today().isoformat()


def _customer(c):
    return c.post("/api/customers", json={"name": "Alpha Demo", "rate": 80}).json()


def _contract(cust, proj):
    return {
        "title": "Demo contract",
        "customer_id": cust["id"],
        "project_id": proj["id"],
        "start": "2026-01-05",
        "end": "2026-03-27",
        "hours_per_week": 10,
    }


def test_kind_is_stored_and_returned(boss_client):
    c = boss_client
    cust = _customer(c)
    a = c.post("/api/projects", json={"name": "Own", "kind": "internal"}).json()
    b = c.post("/api/projects", json={"name": "Job", "customer_id": cust["id"]}).json()
    assert a["kind"] == "internal" and b["kind"] == "customer"
    listed = {p["id"]: p for p in c.get("/api/projects").json()}
    assert listed[a["id"]]["kind"] == "internal" and listed[b["id"]]["kind"] == "customer"
    assert "personal" not in listed[a["id"]]


def test_personal_is_refused(boss_client):
    r = boss_client.post("/api/projects", json={"name": "Own", "personal": True})
    assert r.status_code == 422


def test_member_view_has_kind(boss_client, member_client):
    p = boss_client.post("/api/projects", json={"name": "Own", "kind": "internal"}).json()
    boss_client.post(
        "/api/allocations",
        json={
            "user_id": member_client.user_id,
            "project_id": p["id"],
            "start": "2026-03-02",
            "end": "2026-03-31",
            "hours_per_week": 8,
        },
    )
    view = {x["id"]: x for x in member_client.get("/api/projects").json()}
    assert view[p["id"]]["kind"] == "internal" and "personal" not in view[p["id"]]


def test_contract_drops_user_ids(boss_client, app):
    c = boss_client
    cust = _customer(c)
    me = c.get("/api/auth/me").json()
    proj = c.post("/api/projects", json={"name": "Job", "customer_id": cust["id"]}).json()
    r = c.post("/api/contracts", json={**_contract(cust, proj), "user_ids": [me["id"]]})
    assert r.status_code == 200, r.text
    assert not r.json().get("user_ids")  # the response model shows the empty default
    assert "user_ids" not in app.state.services.store.get("contracts", r.json()["id"])


def test_documents_on_internal_project_are_refused(boss_client):
    c = boss_client
    cust = _customer(c)
    p = c.post("/api/projects", json={"name": "Own", "kind": "internal"}).json()
    bodies = {
        "offers": {"customer_id": cust["id"], "project_id": p["id"], "date": TODAY},
        "contracts": _contract(cust, p),
        "invoices": {"customer_id": cust["id"], "project_id": p["id"], "date": TODAY},
    }
    for coll, body in bodies.items():
        r = c.post(f"/api/{coll}", json=body)
        assert r.status_code == 409, (coll, r.text)
        assert r.json()["detail"] == "Internal projects cannot have offers, contracts or invoices"


def test_project_kind_fallback_for_rows_without_kind():
    assert project_kind({"customer_id": "abc"}) == "customer"
    assert project_kind({}) == "internal"
    assert project_kind({"kind": "internal", "customer_id": "abc"}) == "internal"
    assert project_kind({"kind": "customer"}) == "customer"


def _contract_on(store, proj):
    return store.save(
        "contracts",
        {
            "title": "Demo contract",
            "project_id": proj["id"],
            "start": "2026-01-05",
            "end": "2026-03-27",
            "hours_per_week": 10,
            "status": "confirmed",
        },
    )


def test_update_to_internal_with_a_contract_is_refused(boss_client, app):
    c = boss_client
    cust = _customer(c)
    p = c.post("/api/projects", json={"name": "Job", "customer_id": cust["id"]}).json()
    _contract_on(app.state.services.store, p)
    r = c.put(f"/api/projects/{p['id']}", json={"name": "Job", "kind": "internal"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "This project has offers, contracts or invoices; it stays a customer project."
    got = c.get(f"/api/projects/{p['id']}").json()
    assert got["kind"] == "customer" and got["customer_id"] == cust["id"]


def test_update_without_kind_keeps_the_stored_kind(boss_client, app):
    c = boss_client
    store = app.state.services.store
    p = store.save("projects", {"name": "Legacy", "kind": "customer", "status": "active"})
    _contract_on(store, p)
    r = c.put(f"/api/projects/{p['id']}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "customer" and r.json()["name"] == "Renamed"
    q = store.save("projects", {"name": "Own", "kind": "internal", "status": "active"})
    r = c.put(f"/api/projects/{q['id']}", json={"name": "Own 2"})
    assert r.status_code == 200 and r.json()["kind"] == "internal"


def test_create_derives_kind_from_customer(boss_client):
    c = boss_client
    cust = _customer(c)
    assert c.post("/api/projects", json={"name": "A", "customer_id": cust["id"]}).json()["kind"] == "customer"
    assert c.post("/api/projects", json={"name": "B"}).json()["kind"] == "internal"


def test_kind_customer_combinations_on_update(boss_client):
    c = boss_client
    cust = _customer(c)
    p = c.post("/api/projects", json={"name": "Job", "customer_id": cust["id"]}).json()
    # omitted kind keeps customer, but the customer was dropped
    assert c.put(f"/api/projects/{p['id']}", json={"name": "Job"}).status_code == 422
    assert c.put(f"/api/projects/{p['id']}", json={"name": "Job", "kind": "customer"}).status_code == 422
    q = c.post("/api/projects", json={"name": "Own", "kind": "internal"}).json()
    r = c.put(f"/api/projects/{q['id']}", json={"name": "Own", "customer_id": cust["id"]})
    assert r.status_code == 422, r.text
    r = c.put(f"/api/projects/{q['id']}", json={"name": "Own", "kind": "customer", "customer_id": cust["id"]})
    assert r.status_code == 200 and r.json()["kind"] == "customer"
    r = c.put(f"/api/projects/{q['id']}", json={"name": "Own", "kind": "internal"})
    assert r.status_code == 200 and r.json()["kind"] == "internal"
