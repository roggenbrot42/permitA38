"""A project picked next to a customer must belong to that customer (offers, invoices, contracts, trips).

Only a write that sets or changes the customer or the project is checked; a record that already has a mismatch
(a project moved to another customer afterwards) can still be edited. A project without a customer is general.
"""

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.project_customer import check_project_customer

from .test_trips_api import make_trip, trip_body

LINES = [{"description": "Dev", "qty": 2, "unit": "h", "price": 90}]
MISMATCH = "Project belongs to another customer"


def _customer(c, name):
    r = c.post("/api/customers", json={"name": name, "street": "Teststr. 1", "zip": "10115", "city": "Berlin"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _project(c, name, customer_id=None):
    body = {"name": name, "start": "2026-01-01", "end": "2026-12-31"}
    if customer_id:
        body["customer_id"] = customer_id
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _move_project(c, pid, customer_id):
    """Legacy data: the project moves to another customer after records already point at it."""
    body = {"name": "Moved", "start": "2026-01-01", "end": "2026-12-31", "customer_id": customer_id}
    r = c.put(f"/api/projects/{pid}", json=body)
    assert r.status_code == 200, r.text


@pytest.fixture
def world(boss_client):
    c = boss_client
    acme = _customer(c, "ACME GmbH")
    other = _customer(c, "Globex Ltd")
    p_acme = _project(c, "Website", acme)
    p_other = _project(c, "Shop", other)
    p_general = _project(c, "Internal")
    return {"c": c, "acme": acme, "other": other, "p_acme": p_acme, "p_other": p_other, "p_general": p_general}


def _offer(c, **kw):
    body = {"lines": LINES, **kw}
    return c.post("/api/offers", json=body)


def _invoice(c, **kw):
    body = {"lines": LINES, **kw}
    return c.post("/api/invoices", json=body)


# -- offers --------------------------------------------------------------------------------------------------------
def test_offer_with_matching_project_is_saved(world):
    c = world["c"]
    r = _offer(c, customer_id=world["acme"], project_id=world["p_acme"])
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == world["p_acme"]


def test_offer_with_project_of_another_customer_is_refused(world):
    c = world["c"]
    r = _offer(c, customer_id=world["acme"], project_id=world["p_other"])
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH
    assert c.get("/api/offers").json() == []


def test_offer_with_general_project_is_saved(world):
    c = world["c"]
    r = _offer(c, customer_id=world["acme"], project_id=world["p_general"])
    assert r.status_code == 200, r.text


def test_offer_without_customer_is_not_checked(world):
    c = world["c"]
    r = _offer(c, project_id=world["p_other"])
    assert r.status_code == 200, r.text


def test_offer_moved_to_other_customer_is_refused(world):
    c = world["c"]
    off = _offer(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    r = c.put(f"/api/offers/{off['id']}", json={**off, "project_id": world["p_other"]})
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH


def test_legacy_offer_mismatch_can_be_edited_unchanged(world):
    c = world["c"]
    off = _offer(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    _move_project(c, world["p_acme"], world["other"])  # now the stored offer mismatches its project
    r = c.put(f"/api/offers/{off['id']}", json={**off, "title": "Corrected title"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Corrected title"


def test_legacy_offer_mismatch_changed_project_is_checked_again(world):
    c = world["c"]
    off = _offer(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    _move_project(c, world["p_acme"], world["other"])  # legacy: the stored offer mismatches its project
    r = c.put(f"/api/offers/{off['id']}", json={**off, "project_id": world["p_other"]})
    assert r.status_code == 422  # the project changed, and it still belongs to another customer
    assert r.json()["detail"] == MISMATCH
    r = c.put(f"/api/offers/{off['id']}", json={**off, "customer_id": world["other"], "project_id": world["p_other"]})
    assert r.status_code == 200, r.text  # customer and project now match


# -- invoices ------------------------------------------------------------------------------------------------------
def test_invoice_matching_and_mismatching_project(world):
    c = world["c"]
    assert _invoice(c, customer_id=world["acme"], project_id=world["p_acme"]).status_code == 200
    r = _invoice(c, customer_id=world["acme"], project_id=world["p_other"])
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH


def test_invoice_update_with_other_customer_project_is_refused(world):
    c = world["c"]
    inv = _invoice(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    r = c.put(f"/api/invoices/{inv['id']}", json={**inv, "customer_id": world["other"]})
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH


def test_invoice_legacy_mismatch_unrelated_edit_allowed(world):
    c = world["c"]
    inv = _invoice(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    _move_project(c, world["p_acme"], world["other"])
    r = c.put(f"/api/invoices/{inv['id']}", json={**inv, "notes": "internal remark"})
    assert r.status_code == 200, r.text


# -- contracts -----------------------------------------------------------------------------------------------------
def test_contract_with_other_customer_project_is_refused(world):
    c = world["c"]
    body = {"title": "Retainer", "start": "2026-02-01", "end": "2026-06-30"}
    ok = c.post("/api/contracts", json={**body, "customer_id": world["acme"], "project_id": world["p_acme"]})
    assert ok.status_code == 200, ok.text
    bad = c.post("/api/contracts", json={**body, "customer_id": world["acme"], "project_id": world["p_other"]})
    assert bad.status_code == 422
    assert bad.json()["detail"] == MISMATCH


# -- trips ---------------------------------------------------------------------------------------------------------
def test_trip_with_matching_project_is_saved(world):
    t = make_trip(world["c"], customer_id=world["acme"], project_id=world["p_acme"])
    assert t["project_id"] == world["p_acme"]


def test_trip_with_other_customer_project_is_refused(world):
    c = world["c"]
    r = c.post("/api/trips", json=trip_body(customer_id=world["acme"], project_id=world["p_other"]))
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH


def test_trip_without_customer_or_general_project_is_saved(world):
    c = world["c"]
    assert make_trip(c, project_id=world["p_other"])["project_id"] == world["p_other"]
    assert make_trip(c, customer_id=world["acme"], project_id=world["p_general"])["id"]


def test_trip_update_to_other_customer_project_refused_and_legacy_edit_allowed(world):
    c = world["c"]
    t = make_trip(c, customer_id=world["acme"], project_id=world["p_acme"])
    r = c.put(f"/api/trips/{t['id']}", json=trip_body(customer_id=world["acme"], project_id=world["p_other"]))
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH
    _move_project(c, world["p_acme"], world["other"])
    r = c.put(
        f"/api/trips/{t['id']}", json=trip_body(customer_id=world["acme"], project_id=world["p_acme"], title="Edited")
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Edited"


# -- accept, conversions and PUT without a field --------------------------------------------------------------------
PLAN = {"start": "2026-03-02", "end": "2026-03-31", "hours_per_week": 10, "total_hours": 40, "rate": 90}


def test_accept_offer_with_other_customer_project_is_refused(world):
    c = world["c"]
    off = _offer(c, customer_id=world["acme"], project_id=world["p_acme"], plan=PLAN).json()
    _move_project(c, world["p_acme"], world["other"])  # legacy: the offer now mismatches its project
    before = c.get(f"/api/offers/{off['id']}").json()["status"]
    r = c.post(f"/api/offers/{off['id']}/accept")
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH
    assert c.get(f"/api/offers/{off['id']}").json()["status"] == before  # not accepted
    assert c.get("/api/contracts").json() == []


def test_offer_to_invoice_with_other_customer_project_is_refused(world):
    c = world["c"]
    off = _offer(c, customer_id=world["acme"], project_id=world["p_acme"]).json()
    _move_project(c, world["p_acme"], world["other"])  # legacy mismatch
    r = c.post(f"/api/offers/{off['id']}/to-invoice")
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH
    assert c.get("/api/invoices").json() == []


def test_legacy_contract_mismatch_edit_of_other_field_allowed_project_change_refused(world):
    c = world["c"]
    body = {"title": "Retainer", "start": "2026-02-01", "end": "2026-06-30"}
    ct = c.post("/api/contracts", json={**body, "customer_id": world["acme"], "project_id": world["p_acme"]}).json()
    _move_project(c, world["p_acme"], world["other"])  # legacy: the contract now mismatches its project
    r = c.put(f"/api/contracts/{ct['id']}", json={**ct, "title": "Corrected"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Corrected"
    r = c.put(f"/api/contracts/{ct['id']}", json={**ct, "project_id": world["p_other"]})
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH


def test_put_without_customer_is_checked_against_the_stored_customer(world):
    """A PUT replaces the record, but an omitted customer_id is checked against the stored one (acme here)."""
    c = world["c"]
    body = {"title": "Retainer", "start": "2026-02-01", "end": "2026-06-30"}
    ct = c.post("/api/contracts", json={**body, "customer_id": world["acme"], "project_id": world["p_acme"]}).json()
    r = c.put(f"/api/contracts/{ct['id']}", json={**body, "project_id": world["p_other"]})
    assert r.status_code == 422
    assert r.json()["detail"] == MISMATCH
    assert c.get(f"/api/contracts/{ct['id']}").json()["project_id"] == world["p_acme"]  # nothing was saved
    r = c.put(f"/api/contracts/{ct['id']}", json={**body, "project_id": world["p_general"]})
    assert r.status_code == 200, r.text  # a general project has no customer to check
    assert r.json().get("customer_id") is None  # the PUT replaced the record: the omitted customer is gone


# -- the shared helper ---------------------------------------------------------------------------------------------
class _Store:
    def __init__(self, projects):
        self.projects = projects

    def get(self, coll, rid):
        assert coll == "projects"
        return self.projects.get(rid)


def test_helper_rules_without_http():
    store = _Store({"p1": {"id": "p1", "customer_id": "c1"}, "p0": {"id": "p0"}})
    check_project_customer(store, {"customer_id": "c1", "project_id": "p1"}, None)  # match
    check_project_customer(store, {"customer_id": "c2", "project_id": "p0"}, None)  # no customer on project
    check_project_customer(store, {"project_id": "p1"}, None)  # no customer set: not checked
    with pytest.raises(ServiceError) as exc:
        check_project_customer(store, {"customer_id": "c2", "project_id": "p1"}, None)
    assert exc.value.status == 422
    # stored values are kept for fields the write leaves out
    check_project_customer(store, {"title": "x"}, {"customer_id": "c2", "project_id": "p1"})  # unchanged legacy
    with pytest.raises(ServiceError):
        check_project_customer(store, {"customer_id": "c2"}, {"customer_id": "c1", "project_id": "p1"})
