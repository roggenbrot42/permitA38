"""Timer request bodies (POST /api/timer/start, /api/timer/stop) use the same hard limits as every other body."""

import json

import pytest

LONG = "x" * 5001


def send(client, method, url, body):
    """JSON with \\uXXXX escapes (the client library cannot encode a lone surrogate itself)."""
    return client.request(method, url, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def _422(r):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    for e in r.json()["detail"]:
        assert set(e) == {"loc", "msg", "type"}
    return r.json()["detail"]


@pytest.mark.parametrize(
    "bad",
    [
        {"note": "\ud83d"},  # lone surrogate: used to be a 500
        {"note": "a\x00b"},  # NUL character
        {"note": LONG},  # over 5000 characters
        {"note": 5},  # wrong type
        {"note": ["a"]},
        {"project_id": "NOT A VALID ID!"},  # ids: 6-32 characters 0-9a-z
        {"project_id": 12345678},
        {"task_id": "\ud83d"},
        {"task_id": "ab"},  # too short
        {"project_id": "a" * 33},  # too long
        {"unknown_ok": 1, "note": "\ud83d"},  # values of unknown fields are checked too
    ],
)
def test_timer_start_rejects_bad_bodies(boss_client, bad):
    _422(send(boss_client, "POST", "/api/timer/start", bad))


@pytest.mark.parametrize("bad", [{"note": "\ud83d"}, {"note": "a\x00b"}, {"note": LONG}, {"note": 5}, {"note": ["a"]}])
def test_timer_stop_rejects_bad_bodies(boss_client, bad):
    send(boss_client, "POST", "/api/timer/start", {"note": "start"})
    _422(send(boss_client, "POST", "/api/timer/stop", bad))
    assert boss_client.get("/api/timer").json().get("running") is True  # nothing was saved or stopped


def test_timer_start_accepts_the_bodies_the_web_ui_sends(boss_client):
    c = boss_client
    assert c.post("/api/timer/start", json={"project_id": None, "task_id": None, "note": ""}).status_code == 200
    assert c.post("/api/timer/start", json={"project_id": "", "task_id": "", "note": ""}).status_code == 200
    assert (
        c.post("/api/timer/start", json={"project_id": "abc123", "task_id": "def456", "note": "Ü ok"}).status_code
        == 200
    )
    assert c.post("/api/timer/start", json={"note": "x" * 5000}).status_code == 200  # the limit itself
    assert c.post("/api/timer/start", json={}).status_code == 200  # all fields have defaults


def test_timer_stop_accepts_the_bodies_the_web_ui_sends(boss_client):
    c = boss_client
    c.post("/api/timer/start", json={"project_id": None, "task_id": None, "note": ""})
    assert c.post("/api/timer/stop", json={"note": None}).status_code == 200
    c.post("/api/timer/start", json={"note": "first"})
    assert c.post("/api/timer/stop", json={"note": "Ü ok"}).status_code == 200
    c.post("/api/timer/start", json={"note": "first"})
    assert c.post("/api/timer/stop", json={}).status_code == 200
