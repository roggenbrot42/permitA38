"""Hardening of the incoming offers (review findings): client fields only, flat accepts validated like capacity
accepts, a stale second accept, the order of the capacity checks, W13 boundary, W6 and W9 planner halves, and the
read-only token rule. Literal numbers from docs/contracts/subcontractors.md; today is frozen to 2026-03-02."""

from datetime import date

import pytest

import permita38.api.routers.incoming_offers as offers_router
import permita38.core.service.procurement as procurement_module
import permita38.core.service.plan_service as plan_module
from permita38.core.service.allocations import AllocationService

from .test_api_tokens import bearer, new_token

TODAY = date(2026, 3, 2)


class _Frozen(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    for mod in (offers_router, procurement_module, plan_module):
        monkeypatch.setattr(mod, "date", _Frozen)


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


def post(c, url, body=None, code=None):
    r = c.post(url, json=body) if body is not None else c.post(url)
    if code is not None:
        assert r.status_code == code, r.text
    return r


def capacity_supplier(c, name="Example Freelance Studio (Demo)"):
    return post(c, "/api/suppliers", {"name": name, "kind": "person-capacity", "workdays": [0, 1, 2, 3],
                                      "weekly_hours": 24, "rate": 80.0, "country": "DE"}, 201).json()


def project(c, name):
    return post(c, "/api/projects", {"name": name}, 200).json()


def w1_offer(sid, pid, **kw):
    body = {
        "supplier_id": sid, "reference": "ANG-2026-117", "received_on": "2026-02-20", "valid_until": "2026-03-31",
        "lines": [{"quantity": 80, "unit": "h", "unit_price": 80.0}],
        "plan": {"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20.0}, "project_id": pid,
    }
    body.update(kw)
    return body


# ----------------------------------------------------------------------------- C1: client fields only
def test_server_owned_fields_in_a_request_are_not_stored(boss_client, store ):
    s = capacity_supplier(boss_client)
    p = project(boss_client, "Demo project P")
    boss_id = next(u["id"] for u in store.list("users") if u["role"] == "boss")
    emp = post(boss_client, "/api/allocations", {"user_id": boss_id, "project_id": p["id"], "start": "2026-03-02",
                                                 "end": "2026-03-27", "hours_per_week": 8.0}, 201).json()
    body = w1_offer(s["id"], p["id"], allocation_id=emp["id"], status="accepted", decided_by="u1b2c3d4",
                    status_changed_at="2020-01-01", external_id="X1", import_batch="B1", hours=999.0,
                    net_total=1.0, customer_id="c0ffee01", supplier_name="Forged")
    offer = post(boss_client, "/api/incoming-offers", body, 201).json()
    assert offer["status"] == "received" and "allocation_id" not in offer
    assert (offer["hours"], offer["net_total"], offer.get("customer_id")) == (80.0, 6400.0, None)
    assert offer["supplier_name"] == s["name"]
    stored = store.get("incoming_offers", offer["id"])
    assert "external_id" not in stored and "import_batch" not in stored and stored.get("decided_by") is None


def test_unaccept_never_deletes_an_employee_allocation(boss_client, store):
    s = capacity_supplier(boss_client)
    p = project(boss_client, "Demo project P")
    boss_id = next(u["id"] for u in store.list("users") if u["role"] == "boss")
    emp = post(boss_client, "/api/allocations", {"user_id": boss_id, "project_id": p["id"], "start": "2026-03-09",
                                                 "end": "2026-03-27", "hours_per_week": 8.0}, 201).json()
    offer = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"], plan={
        "start": "2026-03-09", "end": "2026-03-27", "hours_per_week": 20.0}), 201).json()
    acc = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {}, 200).json()
    ext_id = acc["allocation"]["id"]
    # the offer is linked to the employee allocation by someone else's id: unaccept must not touch it
    store.save("incoming_offers", {**store.get("incoming_offers", offer["id"]), "allocation_id": emp["id"]})
    assert post(boss_client, f"/api/incoming-offers/{offer['id']}/unaccept", None, 200).json()["status"] == "under_review"
    assert boss_client.get(f"/api/allocations/{emp['id']}").status_code == 200
    assert store.get("allocations", ext_id) is not None  # not the offer's own allocation: left alone


# ----------------------------------------------------------------------------- C2: flat accepts validate project and contract
def test_flat_accept_checks_project_contract_and_closed_project(boss_client, store):
    sup = post(boss_client, "/api/suppliers", {"name": "Demo Print Shop GmbH", "kind": "company"}, 201).json()
    p = project(boss_client, "Demo project P")
    q = project(boss_client, "Demo project Q")
    contract = post(boss_client, "/api/contracts", {"title": "C_P", "project_id": p["id"], "start": "2026-03-02",
                                                    "end": "2026-03-27", "hours_per_week": 10.0, "rate": 100.0,
                                                    "user_ids": []}, 200).json()
    offer = post(boss_client, "/api/incoming-offers", {
        "supplier_id": sup["id"], "received_on": "2026-02-20", "lines": [{"quantity": 2, "unit": "h", "unit_price": 50}],
        "project_id": p["id"], "contract_id": contract["id"]}, 201).json()

    bad = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {"project_id": "5d1c0aa2zz"})
    assert bad.status_code == 422 and bad.json() == {"detail": "Unknown project"}
    # project Q with the contract of P still set: the contract does not belong to Q
    wrong = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {"project_id": q["id"]})
    assert wrong.status_code == 422
    assert wrong.json() == {"detail": "The contract is not a contract of this project"}
    # closed project
    closed = store.get("projects", p["id"])
    store.save("projects", {**closed, "status": "done"})
    shut = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {})
    assert shut.status_code == 409 and shut.json() == {"detail": "The project is closed"}
    store.save("projects", {**closed, "status": "active"})

    # an explicit null clears the stale contract and moves the customer with the project
    ok = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept",
              {"project_id": q["id"], "contract_id": None}, 200).json()
    assert ok["offer"]["status"] == "accepted" and ok["offer"]["project_id"] == q["id"]
    assert "contract_id" not in ok["offer"]
    assert ok["commitment"] == {"kind": "flat", "planned_net": 100.0, "open_net": 100.0}


# ----------------------------------------------------------------------------- C3: the second accept sees the first
def test_second_accept_is_refused_and_creates_one_allocation(boss_client, store):
    s = capacity_supplier(boss_client)
    p = project(boss_client, "Demo project P")
    offer = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"]), 201).json()
    assert post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {}, 200)
    again = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {})
    assert again.status_code == 409 and again.json() == {"detail": "This action is not possible in status accepted"}
    created = [a for a in store.list("allocations") if a.get("incoming_offer_id") == offer["id"]]
    assert len(created) == 1


# ----------------------------------------------------------------------------- C7: hour line before closed project
def test_capacity_accept_without_hour_line_is_422_even_for_a_closed_project(boss_client, store):
    s = capacity_supplier(boss_client)
    p = project(boss_client, "Demo project P")
    offer = post(boss_client, "/api/incoming-offers", w1_offer(
        s["id"], p["id"], lines=[{"quantity": 2, "unit": "flat", "unit_price": 50.0}]), 201).json()
    store.save("projects", {**store.get("projects", p["id"]), "status": "archived"})
    r = post(boss_client, f"/api/incoming-offers/{offer['id']}/accept", {})
    assert r.status_code == 422 and r.json() == {"detail": "A capacity offer needs an hour line"}


# ----------------------------------------------------------------------------- C4: adoption keeps the vendor text
def test_adoption_writes_through_the_record_rules_and_keeps_the_vendor(boss_client):
    sup = post(boss_client, "/api/suppliers", {"name": "Demo Print Shop GmbH", "kind": "company"}, 201).json()
    e = post(boss_client, "/api/expenses", {"date": "2026-03-05", "net": 10, "vendor": "demo print shop"}, 200).json()
    body = {"dry_run": False, "items": [{"vendors": ["demo print shop"], "supplier_id": sup["id"]}]}
    r = post(boss_client, "/api/suppliers/adopt-vendors", body, 200).json()
    assert r["expenses_changed"] == 1
    row = next(x for x in boss_client.get("/api/expenses").json() if x["id"] == e["id"])
    assert row["supplier_id"] == sup["id"] and row["vendor"] == "demo print shop"


# ----------------------------------------------------------------------------- W13: customer dominance boundary
def _invoice(store, customer_id, net, paid="2026-02-15"):
    store.save("invoices", {"number": f"RE-{net}", "status": "paid", "date": paid, "paid_date": paid,
                            "customer_id": customer_id, "lines": [{"qty": 1, "price": net, "vat": 0}]})


def test_w13_hint_at_the_boundary(boss_client, store):
    a = store.save("customers", {"name": "Demo Customer A"})
    b = store.save("customers", {"name": "Demo Customer B"})
    _invoice(store, a["id"], 49800.0)
    _invoice(store, b["id"], 10200.0)
    hints = boss_client.get("/api/finance/commitments").json()["hints"]
    assert hints == [{
        "code": "business.customer_dominance", "severity": "info", "field": None, "confirmable": False,
        "message": hints[0]["message"],
        "details": {"customer_id": a["id"], "customer_name": "Demo Customer A", "share_percent": 83.0,
                    "revenue_customer": 49800.0, "revenue_total": 60000.0, "from": "2025-03-03",
                    "to": "2026-03-02"},
    }]


def test_w13_just_below_the_boundary_has_no_hint(boss_client, store):
    a = store.save("customers", {"name": "Demo Customer A"})
    b = store.save("customers", {"name": "Demo Customer B"})
    _invoice(store, a["id"], 49799.99)
    _invoice(store, b["id"], 10200.01)
    assert boss_client.get("/api/finance/commitments").json()["hints"] == []


# ----------------------------------------------------------------------------- W6: planner equality with states without effect
def test_w6_offers_in_open_or_rejected_states_leave_forecast_and_commitments_unchanged(boss_client):
    s = capacity_supplier(boss_client)
    p = project(boss_client, "Demo project P")
    base = boss_client.get("/api/forecast?weeks=4").json()
    base_com = boss_client.get("/api/finance/commitments").json()
    received = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"]), 201).json()
    under = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"], reference="B"), 201).json()
    post(boss_client, f"/api/incoming-offers/{under['id']}/review", None, 200)
    rejected = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"], reference="C"), 201).json()
    post(boss_client, f"/api/incoming-offers/{rejected['id']}/reject", {}, 200)
    expired = post(boss_client, "/api/incoming-offers", w1_offer(s["id"], p["id"], reference="D",
                                                                 valid_until="2026-03-01"), 201).json()
    assert expired["effective_status"] == "expired" and received["effective_status"] == "received"
    assert boss_client.get("/api/forecast?weeks=4").json() == base
    com = boss_client.get("/api/finance/commitments").json()
    assert com["items"] == base_com["items"] and com["totals"] == base_com["totals"]


# ----------------------------------------------------------------------------- T7: read-only tokens
ROUTE_CALLS = [
    ("post", "/api/suppliers", {"name": "X", "kind": "company"}),
    ("put", "/api/suppliers/5d1c0aa2", {"name": "X", "kind": "company"}),
    ("delete", "/api/suppliers/5d1c0aa2", None),
    ("post", "/api/suppliers/adopt-vendors", {"dry_run": True, "items": [{"vendors": ["x"], "supplier_id": "5d1c0aa2"}]}),
    ("post", "/api/incoming-offers", {"supplier_id": "5d1c0aa2", "received_on": "2026-02-20",
                                      "lines": [{"quantity": 1, "unit": "h", "unit_price": 1}]}),
    ("put", "/api/incoming-offers/5d1c0aa2", {"supplier_id": "5d1c0aa2", "received_on": "2026-02-20",
                                              "lines": [{"quantity": 1, "unit": "h", "unit_price": 1}]}),
    ("delete", "/api/incoming-offers/5d1c0aa2", None),
    ("post", "/api/incoming-offers/5d1c0aa2/review", None),
    ("post", "/api/incoming-offers/5d1c0aa2/accept", {}),
    ("post", "/api/incoming-offers/5d1c0aa2/reject", {}),
    ("post", "/api/incoming-offers/5d1c0aa2/reopen", None),
    ("post", "/api/incoming-offers/5d1c0aa2/unaccept", None),
    ("post", "/api/allocations/5d1c0aa2/cancel", {"effective_from": "2026-03-13"}),
]
READS = [
    "/api/suppliers", "/api/suppliers/vendor-suggestions", "/api/suppliers/5d1c0aa2",
    "/api/suppliers/5d1c0aa2/dependency", "/api/incoming-offers", "/api/incoming-offers/5d1c0aa2",
    "/api/allocations/5d1c0aa2", "/api/finance/commitments",
]


@pytest.mark.parametrize("method,url,body", ROUTE_CALLS)
def test_read_only_token_is_refused_on_every_write_route(app, boss_client, method, url, body):
    token = new_token(boss_client, read_only=True)["token"]
    c = bearer(app, token)
    r = getattr(c, method)(url, json=body) if body is not None else getattr(c, method)(url)
    assert r.status_code == 403, (method, url, r.status_code, r.text)


@pytest.mark.parametrize("url", READS)
def test_read_only_token_reads_the_new_routes(app, boss_client, url):
    token = new_token(boss_client, read_only=True)["token"]
    r = bearer(app, token).get(url)
    assert r.status_code != 403, (url, r.text)  # 404/409 are fine: the ids above do not exist


# ----------------------------------------------------------------------------- T8: contract assignees that are suppliers
def test_contract_user_ids_accept_active_subcontractors_only(boss_client, store):
    p = project(boss_client, "Demo project P")
    boss_id = next(u["id"] for u in store.list("users") if u["role"] == "boss")
    company = post(boss_client, "/api/suppliers", {"name": "Demo Print Shop GmbH", "kind": "company"}, 201).json()
    active = capacity_supplier(boss_client)
    inactive = capacity_supplier(boss_client, name="Archived Studio")
    store.save("suppliers", {**store.get("suppliers", inactive["id"]), "active": False})
    contract = {"title": "C_P", "project_id": p["id"], "start": "2026-03-02", "end": "2026-03-27",
                "hours_per_week": 10.0, "rate": 100.0}
    refused = post(boss_client, "/api/contracts", {**contract, "user_ids": [company["id"]]})
    assert refused.status_code == 422, refused.text
    assert post(boss_client, "/api/contracts", {**contract, "user_ids": [inactive["id"]]}).status_code == 422
    kept = post(boss_client, "/api/contracts", {**contract, "user_ids": [boss_id, active["id"]]}, 200).json()
    assert kept["user_ids"] == [boss_id, active["id"]]
    unknown = post(boss_client, "/api/contracts", {**contract, "user_ids": ["5d1c0aa2zz"]}, 200)
    assert unknown.status_code == 200  # unknown ids keep today's behaviour
