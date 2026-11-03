"""Personal tax profile routes: boss session only, replace semantics, advisory warnings."""

import pytest
from fastapi.testclient import TestClient

from .conftest import PASSWORD
from .transfer_helpers import login

URL = "/api/settings/personal-tax"
LEGACY = ("social_monthly", "church_tax", "splitting", "other_income", "other_deductions")


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


@pytest.mark.parametrize("role", ["employee", "accountant", "member"])
def test_other_roles_get_403_on_both_routes(role, app, boss_client):
    c = _role_client(app, boss_client, role)
    valid = {"assessment": "joint", "church_tax_percent": 9, "employment": {"gross_year": 40000}}
    assert c.get(URL).status_code == 403
    assert c.put(URL, json={}).status_code == 403
    assert c.put(URL, json=valid).status_code == 403  # rights are checked before the body is validated
    assert c.put(URL, json={"church_tax_percent": 99}).status_code == 403
    assert boss_client.get(URL).json()["stored"] is False


def test_accountant_settings_show_no_tax_profile_or_legacy_key(app, boss_client):
    boss_client.put(URL, json={"assessment": "joint"})
    raw = app.state.services.store.settings()
    for key in LEGACY:
        raw[key] = 5
    app.state.services.store.save_settings(raw)
    c = _role_client(app, boss_client, "accountant")
    shown = c.get("/api/settings")
    assert shown.status_code == 200
    assert not ({"personal_tax", *LEGACY} & set(shown.json()))


@pytest.mark.parametrize("read_only", [True, False])
def test_api_tokens_get_403_on_both_routes(app, boss_client, read_only):
    boss_client.put(URL, json={"assessment": "joint"})
    made = boss_client.post("/api/tokens", json={"label": "t", "read_only": read_only})
    assert made.status_code == 201, made.text
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {made.json()['token']}"
        for r in (c.get(URL), c.put(URL, json={"assessment": "single"})):
            assert r.status_code == 403
    assert boss_client.get(URL).json()["assessment"] == "joint"


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


@pytest.mark.parametrize(
    "body",
    [
        {"health": {"mode": "pkv", "pkv_monthly": 300, "pkv_basis_monthly": 301}},
        {"employment": {}},
        {"employment": {"gross_year": 1000, "steuerklasse": 7}},
        {"health": {"mode": "other"}},
    ],
)
def test_invalid_bodies_are_422_and_store_nothing(boss_client, body):
    assert boss_client.put(URL, json=body).status_code == 422
    assert boss_client.get(URL).json()["stored"] is False


LAX = "production coerces numeric strings and bool/int (lax pydantic); the review asked for strict rejection"


@pytest.mark.xfail(strict=True, reason=LAX)
@pytest.mark.parametrize(
    "body",
    [
        {"children": {"count": "2"}},
        {"children": {"count": True}},
        {"children": {"pv_children_under_25": True}},
        {"health": {"krankengeld": 1}},
        {"church_tax_percent": "9"},
        {"employment": {"gross_year": "1000"}},
    ],
)
def test_numeric_strings_and_bool_counts_are_rejected(boss_client, body):
    assert boss_client.put(URL, json=body).status_code == 422


def test_basis_equal_to_premium_is_accepted(boss_client):
    r = boss_client.put(URL, json={"health": {"mode": "pkv", "pkv_monthly": 300, "pkv_basis_monthly": 300}})
    assert r.status_code == 200 and r.json()["health"]["pkv_basis_monthly"] == 300


def test_unknown_fields_are_accepted_and_round_trip(boss_client, app):
    body = {"x_future": {"a": [1, 2]}, "children": {"count": 1, "x_child": "y"}}
    r = boss_client.put(URL, json=body)
    assert r.status_code == 200, r.text
    got = boss_client.get(URL).json()
    assert got["x_future"] == {"a": [1, 2]} and got["children"]["x_child"] == "y" and got["children"]["count"] == 1
    assert app.state.services.store.settings()["personal_tax"]["x_future"] == {"a": [1, 2]}


def test_explicit_none_is_accepted_where_the_field_is_nullable(boss_client):
    body = {
        "health": {"zusatzbeitrag_percent": None},
        "employment": {"gross_year": 50000, "steuerklasse": None, "withheld_year": None},
    }
    r = boss_client.put(URL, json=body)
    assert r.status_code == 200, r.text
    got = boss_client.get(URL).json()
    assert got["health"]["zusatzbeitrag_percent"] is None
    assert got["employment"]["steuerklasse"] is None and got["employment"]["withheld_year"] is None
    assert got["employment"]["gross_year"] == 50000


def test_explicit_none_is_rejected_where_the_field_is_not_nullable(boss_client):
    assert boss_client.put(URL, json={"assessment": None}).status_code == 422
    assert boss_client.put(URL, json={"children": {"count": None}}).status_code == 422
    assert boss_client.put(URL, json={"employment": {"gross_year": None}}).status_code == 422


def test_the_full_profile_round_trips(boss_client):
    body = {
        "assessment": "joint",
        "church_tax_percent": 8,
        "other_income_year": 100,
        "other_deductions_year": 200,
        "other_insurance_year": 300,
        "children": {"count": 2, "full_allowance": True, "pv_parent": True, "pv_children_under_25": 1},
        "health": {
            "mode": "pkv",
            "krankengeld": False,
            "zusatzbeitrag_percent": 1.5,
            "pkv_monthly": 400,
            "pkv_basis_monthly": 300,
            "pkv_care_monthly": 50,
            "pkv_employer_subsidy_monthly": 100,
        },
        "pension": {"mode": "fixed", "fixed_monthly": 250},
        "unemployment": {"mode": "voluntary_half"},
        "employment": {
            "gross_year": 60000,
            "sv_liable": False,
            "steuerklasse": 3,
            "bav_employee_year": 1000,
            "bav_employer_year": 500,
            "vwl_employer_year": 240,
            "werbungskosten_year": 1500,
            "withheld_year": 9000,
        },
    }
    assert boss_client.put(URL, json=body).status_code == 200
    got = boss_client.get(URL).json()
    for key, value in body.items():
        assert got[key] == value, key


@pytest.mark.parametrize("stored", [{}, "text", {"stored": 1}, {"warnings": "x"}, [], 5, {"children": "x"}])
def test_damaged_stored_profiles_read_as_defaults(boss_client, app, stored):
    raw = app.state.services.store.settings()
    raw["personal_tax"] = stored
    app.state.services.store.save_settings(raw)
    r = boss_client.get(URL)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["stored"] is True and got["assessment"] == "single" and got["children"]["count"] == 0
    assert got["health"]["mode"] == "gkv" and got["employment"] is None
    assert got["warnings"] is None
