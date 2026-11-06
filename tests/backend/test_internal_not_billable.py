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
