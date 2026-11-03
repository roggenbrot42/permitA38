"""calc.finance tax integration: oracle cases F1 and F2 (docs/specs/tax-burden-and-settings.md), owed and reserve,
warnings and the /finance redaction. ``today`` is pinned; the expected numbers are the spec's literals."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from permita38.core import calc
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.personal_tax import NOT_SAVED_WARNING, PersonalTaxService

from .conftest import PASSWORD
from .transfer_helpers import login

TODAY = date(2026, 5, 26)
O5 = {
    "employment": {
        "gross_year": 50000,
        "sv_liable": True,
        "steuerklasse": 1,
        "bav_employee_year": 2400,
        "bav_employer_year": 360,
        "vwl_employer_year": 480,
        "withheld_year": 7000,
    }
}


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "d")
    r.startup()
    r.save_settings({"company_type": "freiberufler", "state": "BY", "hebesatz": 400})
    yield r
    r.close()


def book(repo, amount, day="2026-02-10"):
    repo.save("invoices", {"status": "paid", "date": day, "paid_date": day, "lines": [{"qty": 1, "price": amount}]})


def profile(repo, body):
    PersonalTaxService(repo).update(body)


def aside(f):
    return [m["tax_set_aside"] for m in f["months"]]


def test_f1_owed_and_reserve(repo):
    profile(repo, {})
    book(repo, 24000)
    f = calc.finance(repo, 2026, TODAY)
    assert f["projected_profit"] == 60000.0
    assert f["tax_projected"]["tax_due"] == 9634.0
    o = f["tax_owed"]
    assert o["owed"] == 1306.0 and o["tax_due"] == 1306.0 and o["withheld"] == 0.0
    assert o["as_of"] == "2026-05-26" and o["profit_ytd"] == 24000.0
    assert f["tax_actual"]["social"] == 5208.0
    r = f["reserve"]
    assert (r["due_year"], r["owed_now"], r["remaining"]) == (9634.0, 1306.0, 8328.0)
    assert (r["months_left"], r["monthly"]) == (8, 1041.0)
    assert aside(f) == [0.0] * 4 + [1041.0] * 8


def test_f2_owed_and_reserve_with_employment(repo):
    profile(repo, O5)
    book(repo, 8000)
    f = calc.finance(repo, 2026, TODAY)
    assert f["projected_profit"] == 20000.0
    assert f["tax_projected"]["tax_due"] == 13147.0
    assert f["tax_owed"]["owed"] == 1885.0 and f["tax_owed"]["withheld"] == 7000.0
    r = f["reserve"]
    assert (r["due_year"], r["withheld"], r["remaining"]) == (6147.0, 7000.0, 4262.0)
    assert (r["months_left"], r["monthly"]) == (8, 532.75)
    assert aside(f) == [0.0] * 4 + [532.75] * 8


def test_social_and_take_home_count_social_once(repo):
    profile(repo, {})
    book(repo, 24000)
    f = calc.finance(repo, 2026, TODAY)
    social_m = round(13020 / 12, 2)  # projected social 13 020 (O1)
    assert social_m == 1085.0
    assert all(m["social"] == 1085.0 for m in f["months"])
    assert f["social_year"] == 1085.0 * 5
    assert f["take_home"] == 24000.0 - 1306.0 - 1085.0 * 5
    feb = f["months"][1]
    assert feb["take_home"] == feb["profit"] - feb["tax_set_aside"] - feb["social"] == 24000.0 - 1085.0
    may = f["months"][4]
    assert may["take_home"] == 0.0 - 1041.0 - 1085.0


def test_past_year_has_no_reserve_and_owed_equals_actual(repo):
    profile(repo, {})
    book(repo, 24000, "2025-02-10")
    f = calc.finance(repo, 2025, TODAY)
    assert f["tax_owed"]["as_of"] == "2025-12-31"
    assert f["tax_owed"]["owed"] == f["tax_actual"]["tax_due"] > 0
    assert f["projected_profit"] == 24000.0
    r = f["reserve"]
    assert (r["months_left"], r["remaining"], r["monthly"]) == (0, 0.0, 0.0)
    assert aside(f) == [0.0] * 12
    assert f["social_year"] == round(f["tax_projected"]["social"] / 12, 2) * 12


def test_future_year_has_no_owed_and_reserves_over_twelve_months(repo):
    profile(repo, {})
    book(repo, 24000, "2027-02-10")
    f = calc.finance(repo, 2027, TODAY)
    assert f["tax_owed"] is None
    r = f["reserve"]
    assert (r["months_left"], r["owed_now"], r["remaining"], r["due_year"]) == (12, 0.0, r["due_year"], r["due_year"])
    assert r["monthly"] == round(r["due_year"] / 12, 2) and r["monthly"] > 0
    assert aside(f) == [r["monthly"]] * 12
    assert f["social_year"] == 0.0


def test_no_stored_profile_warns_and_a_stored_one_does_not(repo):
    assert NOT_SAVED_WARNING in calc.finance(repo, 2026, TODAY)["warnings"]
    profile(repo, {})
    w = calc.finance(repo, 2026, TODAY)["warnings"]
    assert NOT_SAVED_WARNING not in w
    assert w == ["Some rates for 2026 are not yet confirmed."]
    assert len(set(w)) == len(w)


def test_legacy_keys_are_ignored(repo):
    s = repo.settings()
    repo.save_settings({**s, "social_monthly": 999, "other_income": 99999, "church_tax": True, "splitting": True})
    profile(repo, {})
    book(repo, 24000)
    f = calc.finance(repo, 2026, TODAY)
    assert f["tax_owed"]["owed"] == 1306.0 and f["months"][0]["social"] == 1085.0


# -- /finance redaction ----------------------------------------------------------------------------------------
FULL = {"est", "soli", "church", "social", "zve", "vorsorge", "insurance"}


def _accountant(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "acc", "name": "A", "role": "accountant", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return login(app, "acc")


def _token_client(app, boss_client):
    made = boss_client.post("/api/tokens", json={"label": "t", "read_only": True})
    assert made.status_code == 201, made.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return c


def _assert_redacted(f):
    assert set(f["tax_actual"]) == set(f["tax_projected"]) == {"total", "rate", "gewst", "kst", "redacted"}
    assert f["tax_actual"]["redacted"] is True
    assert set(f["tax_owed"]) == {"as_of", "gewst", "kst", "redacted"} and f["tax_owed"]["redacted"] is True
    assert set(f["reserve"]) == {"monthly", "months_left", "redacted"} and f["reserve"]["redacted"] is True
    assert "social_year" in f and "take_home" in f and "social" in f["months"][0]


def test_boss_session_sees_the_full_breakdown(boss_client):
    boss_client.put("/api/settings/personal-tax", json={"church_tax_percent": 9})
    f = boss_client.get("/api/finance", params={"year": date.today().year}).json()
    assert FULL <= set(f["tax_actual"]) and FULL <= set(f["tax_projected"])
    assert "redacted" not in f["tax_actual"]
    assert {"owed", "est", "church", "withheld"} <= set(f["tax_owed"])
    assert {"due_year", "withheld", "owed_now", "remaining", "balance"} <= set(f["reserve"])


def test_accountant_and_tokens_see_only_the_redacted_keys(app, boss_client):
    boss_client.put("/api/settings/personal-tax", json={"church_tax_percent": 9, "employment": {"gross_year": 40000}})
    year = date.today().year
    acc = _accountant(app, boss_client)
    for c in (acc, _token_client(app, boss_client)):
        _assert_redacted(c.get("/api/finance", params={"year": year}).json())
    # a future year has tax_owed null; the redaction keeps it null
    f = acc.get("/api/finance", params={"year": year + 1}).json()
    assert f["tax_owed"] is None and set(f["reserve"]) == {"monthly", "months_left", "redacted"}


def test_boss_api_token_is_redacted_too(app, boss_client):
    made = boss_client.post("/api/tokens", json={"label": "script", "read_only": False})
    assert made.status_code == 201, made.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {made.json()['token']}"
    _assert_redacted(c.get("/api/finance", params={"year": date.today().year}).json())
