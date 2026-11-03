"""Personal tax profile routes: boss session only, replace semantics, advisory warnings."""

import pytest
from fastapi.testclient import TestClient

from .conftest import PASSWORD
from .transfer_helpers import login

URL = "/api/settings/personal-tax"


def _role_client(app, boss_client, role):
    r = boss_client.post(
        "/api/users", json={"username": role[:3] + "x", "name": role, "role": role, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return login(app, role[:3] + "x")


def test_boss_gets_defaults_then_the_stored_profile(boss_client):
    r = boss_client.get(URL)
    assert r.status_code == 200
    assert r.json()["stored"] is False and r.json()["assessment"] == "single"
    put = boss_client.put(URL, json={"assessment": "joint", "church_tax_percent": 9, "other_income_year": 1200})
    assert put.status_code == 200, put.text
    assert put.json()["stored"] is True and put.json()["assessment"] == "joint"
    assert put.json()["warnings"] == []
    got = boss_client.get(URL).json()
    assert got["stored"] is True and got["assessment"] == "joint"
    assert got["church_tax_percent"] == 9 and got["other_income_year"] == 1200


def test_put_replaces_and_ignores_stored_and_warnings_in_the_body(boss_client, app):
    boss_client.put(URL, json={"assessment": "joint", "other_income_year": 500})
    r = boss_client.put(URL, json={"church_tax_percent": 8, "stored": False, "warnings": ["x"]})
    assert r.status_code == 200 and r.json()["stored"] is True and r.json()["warnings"] == []
    got = boss_client.get(URL).json()
    assert got["assessment"] == "single" and got["other_income_year"] == 0  # replaced, not merged
    raw = app.state.services.store.settings()["personal_tax"]
    assert "stored" not in raw and "warnings" not in raw


def test_put_returns_advisory_warnings_without_storing_them(boss_client, app):
    body = {"assessment": "single", "employment": {"gross_year": 30000, "steuerklasse": 3}}
    r = boss_client.put(URL, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == ["Steuerklasse III/IV/V means married; check the assessment."]
    assert "warnings" not in app.state.services.store.settings()["personal_tax"]
    assert boss_client.get(URL).json()["warnings"] is None


def test_put_validates_the_body(boss_client):
    assert boss_client.put(URL, json={"church_tax_percent": 16}).status_code == 422
    assert boss_client.put(URL, json={"assessment": "nope"}).status_code == 422
    assert boss_client.get(URL).json()["stored"] is False


@pytest.mark.parametrize("role", ["employee", "accountant"])
def test_other_roles_get_403_on_both_routes(role, app, boss_client):
    c = _role_client(app, boss_client, role)
    assert c.get(URL).status_code == 403
    assert c.put(URL, json={}).status_code == 403
    assert boss_client.get(URL).json()["stored"] is False


def test_unauthenticated_gets_401(client):
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={}).status_code == 401


def test_boss_api_token_gets_403_on_both_routes(app, boss_client):
    boss_client.put(URL, json={"assessment": "joint"})
    made = boss_client.post("/api/tokens", json={"label": "script", "read_only": False})
    assert made.status_code == 201, made.text
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {made.json()['token']}"
        for r in (c.get(URL), c.put(URL, json={"assessment": "single"})):
            assert r.status_code == 403 and r.json() == {"detail": "This route cannot be used with an API token"}
    assert boss_client.get(URL).json()["assessment"] == "joint"


def test_profile_survives_put_settings_and_is_never_shown_there(boss_client):
    boss_client.put(URL, json={"assessment": "joint"})
    assert boss_client.put("/api/settings", json={"company_name": "Demo"}).status_code == 200
    assert "personal_tax" not in boss_client.get("/api/settings").json()
    assert boss_client.get(URL).json()["assessment"] == "joint"
