"""Boss work limits (S6, settings key work_limits.<user_id>) and the employee weekly-hours warning."""

import pytest

from .conftest import PASSWORD

URL = "/api/settings/work-limits"
DEFAULTS = dict(info_day=10, warn_day=12, max_day=16, info_week=60, info_average=48)


def test_defaults_when_not_stored(boss_client):
    r = boss_client.get(URL)
    assert r.status_code == 200
    assert r.json() == {**DEFAULTS, "stored": False}


def test_round_trip_and_hidden_from_settings(boss_client):
    body = dict(info_day=9, warn_day=11, max_day=14, info_week=55, info_average=45)
    r = boss_client.put(URL, json=body)
    assert r.status_code == 200 and r.json() == {**body, "stored": True}
    assert boss_client.get(URL).json() == {**body, "stored": True}
    assert not any(k.startswith("work_limits") for k in boss_client.get("/api/settings").json())
    assert boss_client.put("/api/settings", json={"company_name": "Demo"}).status_code == 200
    assert boss_client.get(URL).json()["info_day"] == 9


def test_partial_body_uses_defaults(boss_client):
    r = boss_client.put(URL, json={"info_day": 8})
    assert r.status_code == 200 and r.json()["info_day"] == 8 and r.json()["warn_day"] == 12


@pytest.mark.parametrize(
    "body",
    [
        {"info_day": 0},
        {"max_day": 25},
        {"info_week": 169},
        {"info_average": 0},
        {"info_day": 13},  # above warn_day 12
        {"warn_day": 17},  # above max_day 16
        {"max_day": 11},  # below warn_day 12
        {"bogus": 1},
    ],
)
def test_invalid_is_422(boss_client, body):
    assert boss_client.put(URL, json=body).status_code == 422


def test_employee_forbidden(member_client):
    assert member_client.get(URL).status_code == 403
    assert member_client.put(URL, json=DEFAULTS).status_code == 403


def _user(boss_client, username, role, hours):
    return boss_client.post(
        "/api/users",
        json={"username": username, "name": "Eva Example", "role": role, "password": PASSWORD, "weekly_hours": hours},
    )


def _codes(r):
    assert r.status_code == 200, r.text
    return [w["code"] for w in r.json()["planning_warnings"]]


def test_user_warning_employee_above_48(boss_client):
    r = _user(boss_client, "eva", "employee", 48.5)
    assert _codes(r) == ["user.weekly_hours_above_law"]
    w = r.json()["planning_warnings"][0]
    assert w["severity"] == "warning"
    assert (
        w["message"] == "Eva Example is set to 48.5 h a week; the Working Time Act allows 48 h on average (§3 ArbZG)."
    )
    uid = r.json()["id"]
    assert all("planning_warnings" not in u for u in boss_client.get("/api/users").json())  # never stored
    upd = boss_client.put(
        f"/api/users/{uid}",
        json={"username": "eva", "name": "Eva Example", "role": "employee", "weekly_hours": 48},
    )
    assert _codes(upd) == []


def test_user_no_warning_at_48_or_for_boss(boss_client):
    assert _codes(_user(boss_client, "ed", "employee", 48)) == []
    assert _codes(_user(boss_client, "bo", "boss", 60)) == []
