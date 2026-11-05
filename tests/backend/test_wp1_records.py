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


def test_administration_project_cannot_be_deleted(boss_client, app):
    store = app.state.services.store
    store.save("projects", {"id": "internaladmin", "name": "Administration", "kind": "internal"})
    r = boss_client.delete("/api/projects/internaladmin")
    assert r.status_code == 409
    assert r.json()["detail"] == "The Administration project cannot be deleted; archive it instead"
    assert store.get("projects", "internaladmin")


def test_project_kind_fallback_for_rows_without_kind():
    assert project_kind({"customer_id": "abc"}) == "customer"
    assert project_kind({}) == "internal"
    assert project_kind({"kind": "internal", "customer_id": "abc"}) == "internal"
    assert project_kind({"kind": "customer"}) == "customer"
