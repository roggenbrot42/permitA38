"""Boundaries of the hard limits: the limit itself is accepted, one more is a 422 (or a business answer that
shows validation passed, such as 401 for an unknown user with a long password)."""

import json

import pytest

from .conftest import PASSWORD


def send(client, method, url, body):
    return client.request(method, url, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("length,status", [(1000, 401), (1001, 422)])
def test_login_password_limit(client, length, status):
    r = send(client, "POST", "/api/auth/login", {"username": "nobody", "password": "p" * length})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


@pytest.mark.parametrize("length,status", [(1000, 200), (1001, 422)])
def test_change_password_limit(boss_client, length, status):
    r = send(boss_client, "POST", "/api/auth/change-password", {"old_password": PASSWORD, "new_password": "p" * length})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


@pytest.mark.parametrize("length,status", [(64, 401), (65, 422)])
def test_login_username_limit(client, length, status):
    r = send(client, "POST", "/api/auth/login", {"username": "u" * length, "password": PASSWORD})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


@pytest.mark.parametrize("length,status", [(4096, 401), (4097, 422)])
def test_mfa_token_limit(client, length, status):
    r = send(client, "POST", "/api/auth/login/totp", {"mfa_token": "t" * length, "code": "123456"})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


@pytest.mark.parametrize("length,status", [(64, 401), (65, 422)])
def test_recovery_code_limit(client, length, status):
    r = send(client, "POST", "/api/auth/login/totp", {"mfa_token": "t", "recovery_code": "r" * length})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


@pytest.mark.parametrize("hours,status", [(0, 200), (168, 200), (168.1, 422), (-1, 422)])
def test_setup_weekly_hours_range(client, hours, status):
    body = {"username": "boss", "name": "Boss", "password": PASSWORD, "weekly_hours": hours}
    r = send(client, "POST", "/api/auth/setup", body)
    assert r.status_code == status, (hours, r.status_code, r.text[:200])


@pytest.mark.parametrize("name,status", [("a" * 200, 200), ("a" * 201, 422), ("", 422), ("   ", 422)])
def test_setup_name_is_required_and_bounded(client, name, status):
    body = {"username": "boss", "name": name, "password": PASSWORD}
    r = send(client, "POST", "/api/auth/setup", body)
    assert r.status_code == status, (name[:10], r.status_code, r.text[:200])
    if status == 422:
        assert client.get("/api/auth/status").json()["setup_needed"] is True  # nothing was created


@pytest.mark.parametrize("length,status", [(5000, 200), (5001, 422)])
def test_timer_note_limit(boss_client, length, status):
    r = send(boss_client, "POST", "/api/timer/start", {"note": "n" * length})
    assert r.status_code == status, (length, r.status_code, r.text[:200])


def test_blank_ids_are_stored_as_none(boss_client):
    """The web forms send '' for "no project"; the timer keeps None, not an empty string."""
    r = send(boss_client, "POST", "/api/timer/start", {"project_id": "", "task_id": "", "note": ""})
    assert r.status_code == 200
    assert r.json()["project_id"] is None and r.json()["task_id"] is None
    stored = boss_client.get("/api/timer").json()  # the store leaves out empty values, so a missing key is None
    assert stored.get("project_id") is None and stored.get("task_id") is None
