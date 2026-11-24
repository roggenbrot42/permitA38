"""Fiscal years: service rules and the three API routes (docs/specs/accounting.md 4, 8.1, 18)."""

import pytest
from fastapi.testclient import TestClient

from permita38.core import models
from permita38.core.errors import ServiceError
from permita38.core.service.fiscal_years import FiscalYearService

from .test_rbac import PASSWORD, _login

BODY = {"year": 2026, "chart": "skr03", "income_basis": "eur"}
URL = "/api/accounting/years"


@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
    return out


@pytest.fixture
def store(app):
    return app.state.services.store


@pytest.fixture
def svc(store):
    return FiscalYearService(store)


def add_entry(store, year: int) -> None:
    store.save(
        "journal_entries",
        {
            "fiscal_year": year, "entry_no": 1, "document_date": f"{year}-01-05", "booking_date": f"{year}-01-05",
            "event": "manual", "event_key": f"k{year}", "text": "t", "origin": "manual", "created_at": 1,
            "created_by": "u1x000", "prev_hash": "0", "hash": "1",
        },
    )  # fmt: skip


def test_create_defaults_and_get_list(boss_client):
    r = boss_client.post(URL, json=BODY)
    assert r.status_code == 201, r.text
    y = r.json()
    assert (y["year"], y["chart"], y["income_basis"]) == (2026, "skr03", "eur")
    assert (y["vat_basis"], y["vat_frequency"]) == ("soll", "month")
    assert y["dauerfrist"] is False and y["small_business"] is False and y["vat_regular_from"] is None
    assert y["closed_at"] is None and y["closed_by"] is None
    assert isinstance(y["opened_at"], int) and y["opened_by"] and y["rules_version"].startswith("skr03-")
    assert boss_client.get(f"{URL}/2026").json() == y
    assert boss_client.post(URL, json={**BODY, "year": 2025, "vat_frequency": "quarter"}).status_code == 201
    assert [r["year"] for r in boss_client.get(URL).json()] == [2025, 2026]


def test_duplicate_year_is_409_and_unknown_year_404(boss_client):
    assert boss_client.post(URL, json=BODY).status_code == 201
    r = boss_client.post(URL, json={**BODY, "chart": "skr04"})
    assert r.status_code == 409
    assert boss_client.get(f"{URL}/2026").json()["chart"] == "skr03"
    assert boss_client.get(f"{URL}/2030").status_code == 404


def test_rights_boss_accountant_allowed_employee_denied(clients):
    assert clients["accountant"].post(URL, json=BODY).status_code == 201
    for role in ("boss", "accountant"):
        assert clients[role].get(URL).status_code == 200
        assert clients[role].get(f"{URL}/2026").status_code == 200
    emp = clients["employee"]
    assert emp.get(URL).status_code == 403
    assert emp.get(f"{URL}/2026").status_code == 403
    assert emp.post(URL, json={**BODY, "year": 2027}).status_code == 403
    assert clients["boss"].get(f"{URL}/2027").status_code == 404  # the denied post created nothing


def test_gmbh_must_use_accrual(boss_client):
    s = boss_client.get("/api/settings").json()
    assert boss_client.put("/api/settings", json={**s, "company_type": "gmbh"}).status_code == 200
    r = boss_client.post(URL, json=BODY)
    assert r.status_code == 422 and "accrual" in r.json()["detail"]
    assert boss_client.get(URL).json() == []
    assert boss_client.post(URL, json={**BODY, "income_basis": "accrual"}).status_code == 201


@pytest.mark.parametrize(
    "patch",
    [
        {"year": 1999},
        {"year": 2101},
        {"year": "2026"},
        {"chart": "skr05"},
        {"income_basis": "cash"},
        {"vat_basis": "ist2"},
        {"vat_frequency": "year"},
        {"dauerfrist": "yes"},
        {"small_business": 1},
        {"vat_regular_from": "2026-07-01"},  # needs small_business
        {"small_business": True, "vat_regular_from": "2025-07-01"},  # outside the year
        {"small_business": True, "vat_regular_from": "2026-13-01"},
    ],
)
def test_validation_errors_are_422(boss_client, patch):
    assert boss_client.post(URL, json={**BODY, **patch}).status_code == 422
    assert boss_client.get(URL).json() == []


def test_missing_required_fields_are_422(boss_client):
    for key in ("year", "chart", "income_basis"):
        assert boss_client.post(URL, json={k: v for k, v in BODY.items() if k != key}).status_code == 422


def test_small_business_with_regular_from_roundtrip(boss_client):
    body = {
        **BODY,
        "small_business": True,
        "vat_regular_from": "2026-07-01",
        "vat_frequency": "none",
        "dauerfrist": True,
    }
    y = boss_client.post(URL, json=body).json()
    assert (y["small_business"], y["vat_regular_from"], y["vat_frequency"], y["dauerfrist"]) == (
        True, "2026-07-01", "none", True,
    )  # fmt: skip


def test_a_token_reads_but_cannot_create(app, boss_client):
    r = boss_client.post("/api/tokens", json={"label": "t", "read_only": False})
    assert r.status_code == 201
    tok = TestClient(app)
    tok.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert tok.post(URL, json=BODY).status_code == 403
    assert tok.get(URL).status_code == 200


# ------------------------------------------------------------------ service: update
def make(svc, **kw):
    return svc.create({"id": "u1x000"}, models.FiscalYearIn(**{**BODY, **kw}))


def test_update_before_the_first_entry_changes_every_basis(svc):
    row = make(svc)
    new = svc.update(
        2026,
        {
            "chart": "skr04",
            "income_basis": "accrual",
            "vat_basis": "ist",
            "vat_frequency": "quarter",
            "dauerfrist": True,
        },
    )
    assert new["id"] == row["id"] and new["opened_at"] == row["opened_at"]
    assert (new["chart"], new["income_basis"], new["vat_basis"], new["vat_frequency"], new["dauerfrist"]) == (
        "skr04", "accrual", "ist", "quarter", True,
    )  # fmt: skip
    assert new["rules_version"] == "skr04-none/vat-2026"  # no chart file on this branch yet: "none"
    assert svc.get(2026) == new


def test_update_after_an_entry_freezes_the_bases_only(svc, store):
    make(svc)
    add_entry(store, 2026)
    for patch in ({"chart": "skr04"}, {"income_basis": "accrual"}, {"vat_basis": "ist"}, {"small_business": True}):
        with pytest.raises(ServiceError) as e:
            svc.update(2026, patch)
        assert e.value.status == 409, patch
    assert svc.get(2026)["chart"] == "skr03"
    assert svc.update(2026, {"chart": "skr03"})["chart"] == "skr03"  # the same value is no change
    new = svc.update(2026, {"vat_frequency": "quarter", "dauerfrist": True})
    assert (new["vat_frequency"], new["dauerfrist"]) == ("quarter", True)


def test_vat_frequency_fields_freeze_with_the_first_vat_return(svc, store):
    make(svc)
    patches = ({"vat_frequency": "quarter"}, {"dauerfrist": True}, {"vat_regular_from": "2026-07-01"})
    assert svc.update(2026, {"vat_frequency": "quarter"})["vat_frequency"] == "quarter"  # allowed before
    svc.update(2026, {"vat_frequency": "month"})
    store.save(
        "vat_returns",
        {
            "year": 2026, "period_kind": "month", "period_no": 1, "from_date": "2026-01-01", "to_date": "2026-01-31",
            "due_date": "2026-02-10", "status": "draft", "amends_key": "",
        },
    )  # fmt: skip
    for patch in patches:
        with pytest.raises(ServiceError) as e:
            svc.update(2026, patch)
        assert e.value.status == 409, patch
    assert svc.get(2026)["vat_frequency"] == "month"
    assert svc.update(2026, {"vat_frequency": "month"})["vat_frequency"] == "month"  # the same value is no change
    assert svc.update(2026, {"chart": "skr04"})["chart"] == "skr04"  # bases follow the entry rule only


def test_a_return_in_another_year_does_not_freeze(svc, store):
    make(svc)
    store.save(
        "vat_returns",
        {
            "year": 2025, "period_kind": "month", "period_no": 1, "from_date": "2025-01-01", "to_date": "2025-01-31",
            "due_date": "2025-02-10", "status": "draft", "amends_key": "",
        },
    )  # fmt: skip
    assert svc.update(2026, {"dauerfrist": True})["dauerfrist"] is True


def test_an_entry_in_another_year_does_not_freeze(svc, store):
    make(svc)
    add_entry(store, 2025)
    assert svc.update(2026, {"chart": "skr04"})["chart"] == "skr04"


def test_a_closed_year_accepts_no_change(svc, store):
    row = make(svc)
    store.save("fiscal_years", {**row, "closed_at": 5, "closed_by": "u1x000"})
    for patch in ({"dauerfrist": True}, {"chart": "skr04"}, {}):
        with pytest.raises(ServiceError) as e:
            svc.update(2026, patch)
        assert e.value.status == 409


def test_update_refuses_the_year_unknown_fields_and_invalid_results(svc):
    make(svc)
    for patch in ({"year": 2027}, {"closed_at": 1}, {"opened_by": "x"}, {"nonsense": 1}):
        with pytest.raises(ServiceError) as e:
            svc.update(2026, patch)
        assert e.value.status == 422, patch
    for patch in ({"chart": "skr05"}, {"vat_regular_from": "2026-07-01"}, {"dauerfrist": "yes"}):
        with pytest.raises(ServiceError) as e:
            svc.update(2026, patch)
        assert e.value.status == 422, patch
    with pytest.raises(ServiceError) as e:
        svc.update(2030, {"dauerfrist": True})
    assert e.value.status == 404
    assert svc.get(2026)["chart"] == "skr03" and svc.get(2026)["dauerfrist"] is False


def test_update_to_cash_basis_refused_for_a_gmbh(svc, store):
    make(svc, income_basis="accrual")
    store.save_settings({**store.settings(), "company_type": "gmbh"})
    with pytest.raises(ServiceError) as e:
        svc.update(2026, {"income_basis": "eur"})
    assert e.value.status == 422
    assert svc.get(2026)["income_basis"] == "accrual"
