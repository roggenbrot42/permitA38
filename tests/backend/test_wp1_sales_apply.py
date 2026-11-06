"""WP1: accepting an offer (hour fields, project kind) and plan apply clearing the other hour field."""

TODAY = "2099-01-05"
END = "2099-03-05"


def _offer(c, plan, lines=None, title="Website relaunch"):
    cid = c.post("/api/customers", json={"name": "Acme Demo"}).json()["id"]
    body = {
        "customer_id": cid,
        "title": title,
        "lines": lines or [{"description": "Work", "qty": 1, "unit": "pc", "price": 100}],
        "plan": plan,
    }
    r = c.post("/api/offers", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _accept(c, offer):
    return c.post(f"/api/offers/{offer['id']}/accept")


def test_accept_with_hours_per_week(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END, "hours_per_week": 12})
    r = _accept(boss_client, o)
    assert r.status_code == 200, r.text
    ct = r.json()["contract"]
    assert ct["hours_per_week"] == 12
    assert ct.get("total_hours") is None
    assert not ct.get("user_ids")
    assert r.json()["project"]["kind"] == "customer"


def test_accept_with_total_hours(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END, "total_hours": 80})
    ct = _accept(boss_client, o).json()["contract"]
    assert ct["total_hours"] == 80
    assert ct.get("hours_per_week") is None


def test_accept_sums_hour_lines(boss_client):
    lines = [
        {"description": "A", "qty": 10, "unit": "h", "price": 50},
        {"description": "B", "qty": 2.5, "unit": "h", "price": 50},
        {"description": "C", "qty": 7, "unit": "pc", "price": 5},
    ]
    o = _offer(boss_client, {"start": TODAY, "end": END}, lines)
    ct = _accept(boss_client, o).json()["contract"]
    assert ct["total_hours"] == 12.5
    assert ct.get("hours_per_week") is None


def test_accept_without_hours_is_refused_and_writes_nothing(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END})
    r = _accept(boss_client, o)
    assert r.status_code == 422
    assert "no hours to plan" in r.text
    assert boss_client.get("/api/contracts").json() == []
    assert [p for p in boss_client.get("/api/projects").json() if p["id"] != "internaladmin"] == []
    assert boss_client.get(f"/api/offers/{o['id']}").json()["status"] != "accepted"


def test_blank_title_falls_back(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END, "total_hours": 10}, title="   ")
    r = _accept(boss_client, o).json()
    assert r["project"]["name"].startswith("Offer ")
    assert r["contract"]["title"].strip()


def test_invoice_from_time_refused_for_internal_project(boss_client):
    pid = boss_client.post("/api/projects", json={"name": "Internal Tools", "kind": "internal"}).json()["id"]
    r = boss_client.post("/api/invoices/from-time", json={"project_id": pid})
    assert r.status_code == 422, r.text
    assert "Internal projects cannot be billed" in r.text


def test_apply_total_hours_clears_hours_per_week(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END, "hours_per_week": 12})
    ct = _accept(boss_client, o).json()["contract"]
    r = boss_client.post(
        "/api/plan/apply",
        json={"confirm": True, "changes": {"commitments": [{"kind": "contract", "id": ct["id"], "total_hours": 99}]}},
    )
    assert r.status_code == 200, r.text
    got = boss_client.get(f"/api/contracts/{ct['id']}").json()
    assert got["total_hours"] == 99
    assert got.get("hours_per_week") is None


def _contract_after(c, plan, lines=None):
    o = _offer(c, plan, lines)
    return o, _accept(c, o).json()["contract"]


def _apply(c, kind, rid, **fields):
    r = c.post(
        "/api/plan/apply",
        json={"confirm": True, "changes": {"commitments": [{"kind": kind, "id": rid, **fields}]}},
    )
    assert r.status_code == 200, r.text


def test_accept_on_internal_project_is_refused(boss_client, app):
    c = boss_client
    store = app.state.services.store
    o = _offer(c, {"start": TODAY, "end": END, "total_hours": 10})
    proj = store.save("projects", {"name": "Own", "kind": "internal", "status": "active"})
    store.save("offers", {**store.get("offers", o["id"]), "project_id": proj["id"]})
    r = _accept(c, o)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Internal projects cannot have offers, contracts or invoices"
    assert c.get("/api/contracts").json() == []
    assert c.get(f"/api/offers/{o['id']}").json()["status"] != "accepted"


def _h(qty):
    return [{"description": "Work", "qty": qty, "unit": "h", "price": 10}]


def test_hour_line_sum_bounds(boss_client):
    for qty in (0.0001, 100000.5):
        o = _offer(boss_client, {"start": TODAY, "end": END}, _h(qty))
        r = _accept(boss_client, o)
        assert r.status_code == 422, (qty, r.text)
        assert r.json()["detail"] == "The offer's hour lines must add up to more than 0 and at most 100000 h"
    assert boss_client.get("/api/contracts").json() == []


def test_legacy_zero_hours_per_week_falls_back_to_total_hours(boss_client, app):
    store = app.state.services.store
    o = _offer(boss_client, {"start": TODAY, "end": END, "total_hours": 50})
    legacy = store.get("offers", o["id"])
    legacy["plan"] = {**legacy["plan"], "hours_per_week": 0}
    store.save("offers", legacy)
    ct = _accept(boss_client, o).json()["contract"]
    assert ct["total_hours"] == 50 and ct.get("hours_per_week") is None


def test_draft_without_number_gets_a_fallback_name(boss_client):
    o = _offer(boss_client, {"start": TODAY, "end": END, "total_hours": 10}, title="  ")
    assert not o.get("number")
    assert _accept(boss_client, o).json()["project"]["name"] == "Offer draft"


def test_apply_both_directions_on_a_contract(boss_client):
    c = boss_client
    _, ct = _contract_after(c, {"start": TODAY, "end": END, "hours_per_week": 12})
    _apply(c, "contract", ct["id"], total_hours=99)
    got = c.get(f"/api/contracts/{ct['id']}").json()
    assert got["total_hours"] == 99 and got.get("hours_per_week") is None
    _apply(c, "contract", ct["id"], hours_per_week=7)
    got = c.get(f"/api/contracts/{ct['id']}").json()
    assert got["hours_per_week"] == 7 and got.get("total_hours") is None


def test_apply_both_directions_on_an_offer_plan(boss_client, app):
    c = boss_client
    o = _offer(c, {"start": TODAY, "end": END, "hours_per_week": 12})
    store = app.state.services.store
    store.save("offers", {**store.get("offers", o["id"]), "status": "sent"})
    _apply(c, "offer", o["id"], total_hours=99)
    plan = c.get(f"/api/offers/{o['id']}").json()["plan"]
    assert plan["total_hours"] == 99 and plan.get("hours_per_week") is None
    _apply(c, "offer", o["id"], hours_per_week=7)
    plan = c.get(f"/api/offers/{o['id']}").json()["plan"]
    assert plan["hours_per_week"] == 7 and plan.get("total_hours") is None
