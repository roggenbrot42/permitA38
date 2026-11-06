"""PATCH /api/timer: switch project / task / note of the running timer (invented data)."""

import pytest


@pytest.fixture
def store(app, boss_client):
    return app.state.services.store


@pytest.fixture
def data(store):
    a = store.save("projects", {"name": "Alpha", "kind": "customer"})
    b = store.save("projects", {"name": "Beta", "kind": "internal"})
    ta = store.save("tasks", {"project_id": a["id"], "name": "Design"})
    tb = store.save("tasks", {"project_id": b["id"], "name": "Admin"})
    return a, b, ta, tb


def test_patch_changes_project_and_task_and_keeps_started(boss_client, data):
    a, b, ta, tb = data
    started = boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"]}).json()
    r = boss_client.patch("/api/timer", json={"project_id": b["id"], "task_id": tb["id"], "note": "switched"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["running"], t["project_id"], t["task_id"], t["note"]) == (True, b["id"], tb["id"], "switched")
    assert t["started"] == started["started"]
    assert boss_client.get("/api/timer").json() == t


def test_patch_only_given_fields_change(boss_client, data):
    a, _, ta, _ = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"], "note": "x"})
    t = boss_client.patch("/api/timer", json={"note": "y"}).json()
    assert (t["project_id"], t["task_id"], t["note"]) == (a["id"], ta["id"], "y")


def test_patch_project_without_task_clears_old_task(boss_client, data):
    a, b, ta, _ = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"]})
    t = boss_client.patch("/api/timer", json={"project_id": b["id"]}).json()
    assert (t["project_id"], t.get("task_id")) == (b["id"], None)


def test_patch_task_of_another_project_is_422(boss_client, data):
    a, _, _, tb = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"]})
    assert boss_client.patch("/api/timer", json={"task_id": tb["id"]}).status_code == 422
    assert boss_client.get("/api/timer").json().get("task_id") is None


def test_patch_unknown_and_unassigned_project_is_422(boss_client, data):
    boss_client.post("/api/timer/start", json={})
    assert boss_client.patch("/api/timer", json={"project_id": "nope"}).status_code == 422
    assert boss_client.patch("/api/timer", json={"project_id": "unassigned"}).status_code == 422
    assert boss_client.patch("/api/timer", json={"bogus": 1}).status_code == 422


def test_patch_without_running_timer_is_409(boss_client, data):
    assert boss_client.patch("/api/timer", json={"note": "x"}).status_code == 409


def test_stop_after_patch_saves_entry_on_new_project(boss_client, data):
    a, b, ta, tb = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"]})
    boss_client.patch("/api/timer", json={"project_id": b["id"], "task_id": tb["id"]})
    entry = boss_client.post("/api/timer/stop", json={}).json()["entry"]
    assert (entry["project_id"], entry["task_id"]) == (b["id"], tb["id"])


def test_member_patches_only_own_timer(boss_client, member_client, data):
    a, b, _, _ = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"]})
    assert member_client.patch("/api/timer", json={"project_id": b["id"]}).status_code == 409
    member_client.post("/api/timer/start", json={"project_id": a["id"]})
    assert member_client.patch("/api/timer", json={"project_id": b["id"]}).json()["project_id"] == b["id"]
    assert boss_client.get("/api/timer").json()["project_id"] == a["id"]
