"""Entries on internal projects are never billable (invented data)."""

import pytest


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


@pytest.fixture
def projs(store):
    a = store.save("projects", {"name": "Alpha", "kind": "customer"})
    b = store.save("projects", {"name": "Beta", "kind": "internal"})
    return a, b


def _entry(client, project_id, **extra):
    body = {"date": "2026-03-02", "minutes": 60, "project_id": project_id, **extra}
    r = client.post("/api/entries", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_create_internal_billable_true_is_overridden(boss_client, projs):
    assert _entry(boss_client, projs[1]["id"], billable=True)["billable"] is False


def test_update_onto_internal_project_sets_false(boss_client, projs):
    a, b = projs
    e = _entry(boss_client, a["id"])
    assert e["billable"] is True
    r = boss_client.put(f"/api/entries/{e['id']}", json={**e, "project_id": b["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["billable"] is False


def test_customer_project_keeps_client_value_and_default(boss_client, projs):
    a, _ = projs
    assert _entry(boss_client, a["id"], billable=False)["billable"] is False
    assert _entry(boss_client, a["id"])["billable"] is True


def test_timer_internal_stop_not_billable(boss_client, projs):
    boss_client.post("/api/timer/start", json={"project_id": projs[1]["id"]})
    e = boss_client.post("/api/timer/stop", json={}).json()["entry"]
    assert e["billable"] is False


def test_timer_customer_stop_billable(boss_client, projs):
    boss_client.post("/api/timer/start", json={"project_id": projs[0]["id"]})
    e = boss_client.post("/api/timer/stop", json={}).json()["entry"]
    assert e["billable"] is True


@pytest.fixture(params=["boss", "member"])
def who(request, boss_client, member_client):
    return boss_client if request.param == "boss" else member_client


def _put(client, e, **over):
    body = {"note": "x", "billable": True, "minutes": 13, "end": "10:00", "date": e["date"], **over}
    r = client.put(f"/api/entries/{e['id']}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_internal_to_no_project_keeps_billable_true(who, projs):
    e = _entry(who, projs[1]["id"])
    assert e["billable"] is False
    out = _put(who, e, project_id=None, task_id=None, rate=None)
    assert out.get("project_id") is None
    assert out["billable"] is True


def test_internal_to_customer_project_keeps_billable_true(who, projs):
    e = _entry(who, projs[1]["id"])
    out = _put(who, e, project_id=projs[0]["id"])
    assert out["billable"] is True


def test_customer_to_internal_project_sets_false(who, projs):
    e = _entry(who, projs[0]["id"])
    out = _put(who, e, project_id=projs[1]["id"])
    assert out["billable"] is False


def _day(client, minutes):
    return client.post("/api/entries", json={"date": "2026-01-01", "minutes": minutes})


def test_day_total_over_1440_is_rejected(who):
    for m in (300, 599, 352):
        assert _day(who, m).status_code in (200, 201)
    r = _day(who, 193)  # 1444 > 1440
    assert r.status_code == 422 and "a day has only 24" in r.text


def test_day_total_of_exactly_1440_is_accepted(who):
    for m in (300, 599, 352, 189):
        assert _day(who, m).status_code in (200, 201)


def test_day_total_update_replaces_old_minutes(who):
    ids = [_day(who, m).json()["id"] for m in (300, 599, 352)]
    r = who.put(f"/api/entries/{ids[2]}", json={"date": "2026-01-01", "minutes": 541})  # 300 + 599 + 541 = 1440
    assert r.status_code == 200, r.text
    r = who.put(f"/api/entries/{ids[2]}", json={"date": "2026-01-01", "minutes": 542})
    assert r.status_code == 422


def test_invoiced_entry_403_before_day_total(member_client, store):
    ids = [_day(member_client, m).json()["id"] for m in (300, 599, 352)]
    e = store.get("entries", ids[2])
    store.save("entries", {**e, "invoice_id": "inv999"})
    r = member_client.put(f"/api/entries/{ids[2]}", json={"date": "2026-01-01", "minutes": 900})
    assert r.status_code == 403, r.text
