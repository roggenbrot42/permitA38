"""PATCH /api/timer: switch project / task / note of the running timer (invented data)."""

from datetime import datetime, timedelta

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


def test_stop_with_discard_writes_no_entry(boss_client, store, data):
    a, _, ta, _ = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"]})
    before = len(store.list("entries"))
    r = boss_client.post("/api/timer/stop", json={"discard": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"running": False}
    assert len(store.list("entries")) == before
    assert boss_client.get("/api/timer").json()["running"] is False


def test_stop_without_discard_still_books(boss_client, store, data):
    a, _, ta, _ = data
    boss_client.post("/api/timer/start", json={"project_id": a["id"], "task_id": ta["id"]})
    before = len(store.list("entries"))
    r = boss_client.post("/api/timer/stop", json={"discard": False})
    assert r.json()["running"] is False and "entry" in r.json()
    assert len(store.list("entries")) == before + 1


def test_discard_without_timer(boss_client):
    r = boss_client.post("/api/timer/stop", json={"discard": True})
    assert r.status_code == 200 and r.json() == {"running": False}


def test_stop_unknown_field_is_422(boss_client):
    assert boss_client.post("/api/timer/stop", json={"discard": True, "bogus": 1}).status_code == 422


def _stop_past_midnight(boss_client, monkeypatch, a, started: datetime, stopped: datetime):
    from permita38.core.service import time as time_module

    clock = {"now": started.timestamp()}
    monkeypatch.setattr(time_module._time, "time", lambda: clock["now"])
    boss_client.post("/api/timer/start", json={"project_id": a["id"]})
    clock["now"] = stopped.timestamp()
    return boss_client.post("/api/timer/stop", json={}).json()["entry"]


def test_timer_past_midnight_ends_at_2359_of_its_start_day(boss_client, data, monkeypatch):
    a = data[0]
    entry = _stop_past_midnight(
        boss_client, monkeypatch, a, datetime(2026, 11, 3, 22, 30), datetime(2026, 11, 4, 1, 10)
    )
    assert (entry["date"], entry["start"], entry["end"], entry["minutes"]) == ("2026-11-03", "22:30", "23:59", 89)


def test_timer_three_days_old_still_ends_on_its_day(boss_client, data, monkeypatch):
    a = data[0]
    entry = _stop_past_midnight(boss_client, monkeypatch, a, datetime(2026, 11, 3, 9, 0), datetime(2026, 11, 6, 9, 0))
    assert (entry["date"], entry["start"], entry["end"], entry["minutes"]) == ("2026-11-03", "09:00", "23:59", 899)
    assert entry["minutes"] <= 24 * 60


def test_timer_started_at_2359_books_one_minute(boss_client, data, monkeypatch):
    a = data[0]
    start = datetime(2026, 11, 3, 23, 59)
    entry = _stop_past_midnight(boss_client, monkeypatch, a, start, start + timedelta(minutes=5))
    assert (entry["date"], entry["end"], entry["minutes"]) == ("2026-11-03", "23:59", 1)
