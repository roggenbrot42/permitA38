"""Regression tests for real bugs found by the randomized tests.

Each test states the *correct* behaviour. Fixed bugs are plain tests; a bug that is still open is marked
`xfail(strict=True)`: it documents the bug today and turns red (XPASS) the moment it is fixed, which is the
signal to delete the marker.

Fixed in 0.4.0: input validation (dates, numbers, ranges, lone surrogates) and VAT rounding.
"""

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.service import auth as auth_module

PASSWORD = "correct horse battery"


@pytest.fixture
def api(tmp_path):
    app = get_app(Config(data_dir=tmp_path / "data", secret_key="known-bugs-secret", web_dir=None))
    with TestClient(app, raise_server_exceptions=False) as client:
        client.app_ref = app  # type: ignore[attr-defined]
        yield client


def _boss(api):
    r = api.post("/api/auth/setup", json={"username": "boss", "name": "Bea", "password": PASSWORD})
    assert r.status_code == 200
    token = api.post("/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
    api.cookies.clear()
    api.headers["Authorization"] = "Bearer " + token
    return r.json()


def _member(api):
    r = api.post("/api/users", json={"username": "mia", "name": "Mia", "role": "member", "password": PASSWORD})
    assert r.status_code == 200
    member = TestClient(api.app_ref, raise_server_exceptions=False)
    token = member.post("/api/auth/login", json={"username": "mia", "password": PASSWORD}).json()["token"]
    member.cookies.clear()
    member.headers["Authorization"] = "Bearer " + token
    member.user_id = r.json()["id"]  # type: ignore[attr-defined]
    return member


# ------------------------------------------------------------------ input validation: dates and ranges
@pytest.mark.parametrize("year", [10000, 99999, -1])
def test_holidays_year_out_of_range_is_a_client_error(api, year):
    _boss(api)
    r = api.get("/api/holidays", params={"year": year})
    assert r.status_code in (400, 422), r.status_code


def test_one_invalid_absence_must_not_break_everybody_elses_dashboard(api):
    boss = _boss(api)
    member = _member(api)
    bad = member.post("/api/absences", json={"start": "garbage"})  # a member can do this to the whole team
    assert bad.status_code in (400, 422), "invalid date must be rejected"
    assert api.get("/api/dashboard", params={"user_id": boss["id"]}).status_code == 200
    assert api.get("/api/forecast").status_code == 200


def test_absence_without_user_id_must_not_break_the_dashboard(api):
    _boss(api)
    r = api.post("/api/absences", json={"start": "2026-10-19", "end": "2026-10-22"})
    assert r.status_code in (400, 422) or api.get("/api/dashboard").status_code == 200


def test_invalid_expense_date_must_not_break_finance(api):
    _boss(api)
    r = api.post("/api/expenses", json={"date": "2026-13-45", "net": 1})
    assert r.status_code in (400, 422), "invalid date must be rejected"
    assert api.get("/api/finance").status_code == 200


def test_invalid_contract_date_must_not_break_forecast(api):
    _boss(api)
    r = api.post("/api/contracts", json={"title": "x", "start": "nope", "end": "2026-12-31", "hours_per_week": 5})
    assert r.status_code in (400, 422), "invalid date must be rejected"
    assert api.get("/api/forecast").status_code == 200


def test_absurd_minutes_value_is_a_client_error(api):
    _boss(api)
    r = api.post("/api/entries", content=b'{"date":"2026-10-22","minutes":' + b"1" + b"0" * 400 + b"}",
                 headers={"Content-Type": "application/json"})  # fmt: skip
    assert r.status_code in (400, 422), r.status_code


# ------------------------------------------------------------------ unicode
def test_lone_surrogate_is_rejected_cleanly_and_stores_nothing(api):
    _boss(api)
    r = api.post("/api/customers", content=b'{"name": "\\ud83d"}', headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422), r.status_code
    assert api.get("/api/customers").json() == [], "a rejected request must not leave a half-written record"


def test_lone_surrogate_in_invalid_field_gives_422(api):
    _boss(api)
    r = api.post("/api/entries", content=b'{"date":"2026-01-01","minutes":"\\ud83d"}',
                 headers={"Content-Type": "application/json"})  # fmt: skip
    assert r.status_code == 422, r.status_code


# ------------------------------------------------------------------ documents
# Fixed by the repository layer (get(None) returns None instead of raising); kept as a regression test.
@pytest.mark.parametrize("coll", ["invoices", "offers"])
def test_pdf_of_draft_without_customer_works(api, coll):
    _boss(api)
    doc = api.post(f"/api/{coll}", json={"lines": [{"description": "x", "qty": 1, "price": 10}]}).json()
    r = api.get(f"/api/{coll}/{doc['id']}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ------------------------------------------------------------------ authentication
@pytest.mark.xfail(
    strict=True,
    reason="Tokens use whole-second `iat`; a token issued in the same second as a password "
    "change is still accepted (core/service/auth.py: `claims['iat'] < pw_changed`)",
)
def test_password_change_invalidates_tokens_issued_in_the_same_second(api, monkeypatch):
    class Frozen:
        @staticmethod
        def time():
            return 1_800_000_000.0

    monkeypatch.setattr(auth_module, "time", Frozen)
    _boss(api)
    old = api.headers["Authorization"]
    r = api.post("/api/auth/change-password", json={"old_password": PASSWORD, "new_password": "another long password"})
    assert r.status_code == 200
    api.cookies.clear()
    assert api.get("/api/auth/me", headers={"Authorization": old}).status_code == 401


# ------------------------------------------------------------------ member visibility
@pytest.mark.xfail(
    strict=True,
    reason="GET /projects/{id} returns the full record (rate, notes, budget) to a member, "
    "even for projects they are not assigned to; GET /projects hides both (core/service/records.py get)",
)
def test_member_cannot_read_unassigned_project_details(api):
    _boss(api)
    member = _member(api)
    p = api.post(
        "/api/projects", json={"name": "Secret", "rate": 175, "notes": "margin 40%", "member_ids": ["nobody0000x"]}
    ).json()
    assert p["id"] not in {x["id"] for x in member.get("/api/projects").json()}  # hidden in the list...
    r = member.get(f"/api/projects/{p['id']}")
    assert r.status_code in (403, 404), "...so it must be hidden by id as well"


@pytest.mark.xfail(
    strict=True,
    reason="GET /tasks returns every task incl. `rate` and tasks of projects the member is "
    "not assigned to; project rates are hidden from members, task rates are not",
)
def test_member_task_list_hides_rates_and_unassigned_projects(api):
    _boss(api)
    member = _member(api)
    p = api.post("/api/projects", json={"name": "Secret", "member_ids": ["nobody0000x"]}).json()
    api.post("/api/tasks", json={"name": "Consulting", "project_id": p["id"], "rate": 175})
    rows = member.get("/api/tasks").json()
    assert not [t for t in rows if t["project_id"] == p["id"]]
    assert not [t for t in rows if "rate" in t]


# ------------------------------------------------------------------ mass assignment
@pytest.mark.xfail(
    strict=True,
    reason="A member can set `rate` and `invoice_id` on their own time entries (mass "
    "assignment through the Entry model). `rate` overrides every other rate when the boss bills the "
    "time; `invoice_id` hides hours from invoicing. Both should be boss/system-only (core/service/records.py _write)",
)
def test_member_cannot_set_billing_fields_on_own_entries(api):
    _boss(api)
    member = _member(api)
    r = member.post(
        "/api/entries", json={"date": "2026-10-22", "minutes": 60, "rate": 10000, "invoice_id": "abcdef123456"}
    )
    assert r.status_code in (400, 403, 422) or ("rate" not in r.json() and "invoice_id" not in r.json())
