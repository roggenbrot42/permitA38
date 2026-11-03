"""calc.finance tax integration: oracle cases F1 and F2 (docs/specs/tax-burden-and-settings.md), owed and reserve,
warnings and the /finance redaction. ``today`` is pinned; the expected numbers are the spec's literals."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from permita38.core import calc
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.personal_tax import NOT_SAVED_WARNING, PersonalTaxService

from .conftest import PASSWORD
from .transfer_helpers import login

TODAY = date(2026, 5, 26)
O1 = {}
O4 = {
    "church_tax_percent": 8,
    "children": {"count": 1, "pv_parent": True, "pv_children_under_25": 1},
    "health": {"mode": "gkv", "krankengeld": False},
    "pension": {"mode": "statutory_income"},
    "unemployment": {"mode": "voluntary"},
}
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


def social(f):
    return [m["social"] for m in f["months"]]


def th(f):
    return [m["take_home"] for m in f["months"]]


def dsum(values):
    return sum((Decimal(str(v)) for v in values if v is not None), Decimal(0))


def assert_sums(f, begun):
    """take_home and social_year are the sums of the months; the set-asides add up to owed_now + months_left x monthly."""
    r = f["reserve"]
    assert dsum(th(f)) == Decimal(str(f["take_home"]))
    assert dsum(social(f)) == Decimal(str(f["social_year"]))
    assert all(m["take_home"] is None for m in f["months"][begun:])
    assert all(m["take_home"] is not None for m in f["months"][:begun])
    assert dsum(aside(f)) == Decimal(str(r["owed_now"])) + r["months_left"] * Decimal(str(r["monthly"]))


def test_f1_owed_and_reserve(repo):
    profile(repo, O1)
    book(repo, 24000)
    f = calc.finance(repo, 2026, TODAY)
    assert f["projected_profit"] == 60000.0 and f["extrapolated"] is True
    assert f["tax_projected"]["tax_due"] == 9634.0
    o = f["tax_owed"]
    assert o["owed"] == 1306.0 and o["tax_due"] == 1306.0 and o["withheld"] == 0.0
    assert o["as_of"] == "2026-05-26" and o["profit_ytd"] == 24000.0
    assert f["tax_actual"]["social"] == 5208.0
    r = f["reserve"]
    assert (r["due_year"], r["owed_now"], r["remaining"]) == (9634.0, 1306.0, 8328.0)
    assert (r["months_left"], r["monthly"]) == (8, 1041.0)
    assert aside(f) == [0.0] * 4 + [2347.0] + [1041.0] * 7
    assert dsum(aside(f)) == Decimal("9634.00")
    assert social(f) == [1085.0] * 5 + [0.0] * 7 and f["social_year"] == 5425.0
    assert th(f) == [-1085.0, 22915.0, -1085.0, -1085.0, -3432.0] + [None] * 7
    assert f["take_home"] == 16228.0
    assert_sums(f, 5)


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
    assert aside(f) == [0.0] * 4 + [2417.75] + [532.75] * 7
    assert social(f) == [0.0] * 12 and f["social_year"] == 0.0
    assert th(f) == [0.0, 8000.0, 0.0, 0.0, -2417.75] + [None] * 7
    assert f["take_home"] == 5582.25


def test_f3_early_lump_sum_january_boundary(repo):
    profile(repo, O1)
    book(repo, 60000, "2026-01-10")
    f = calc.finance(repo, 2026, date(2026, 1, 10))
    assert f["projected_profit"] == 60000.0 and f["extrapolated"] is False
    assert f["tax_projected"]["tax_due"] == f["tax_actual"]["tax_due"] == 9634.0
    assert f["tax_owed"]["owed"] == 9634.0
    r = f["reserve"]
    assert (r["due_year"], r["owed_now"], r["remaining"]) == (9634.0, 9634.0, 0.0)
    assert (r["months_left"], r["monthly"]) == (12, 0.0)
    assert aside(f) == [9634.0] + [0.0] * 11
    assert social(f) == [1085.0] + [0.0] * 11
    assert th(f) == [49281.0] + [None] * 11
    assert f["take_home"] == 49281.0 and f["social_year"] == 1085.0
    assert_sums(f, 1)


def test_f4_day_59_has_no_projection(repo):
    profile(repo, O1)
    book(repo, 24000)
    f = calc.finance(repo, 2026, date(2026, 2, 28))
    assert f["projected_profit"] == 24000.0 and f["extrapolated"] is False
    assert f["tax_projected"]["social"] == f["tax_actual"]["social"] == 5208.0
    assert f["tax_projected"]["tax_due"] == f["tax_actual"]["tax_due"] == 1306.0
    assert f["tax_owed"]["owed"] == 1306.0
    r = f["reserve"]
    assert (r["due_year"], r["owed_now"], r["remaining"]) == (1306.0, 1306.0, 0.0)
    assert (r["months_left"], r["monthly"]) == (11, 0.0)
    assert aside(f) == [0.0, 1306.0] + [0.0] * 10
    assert social(f) == [434.0, 434.0] + [0.0] * 10
    assert th(f) == [-434.0, 22260.0] + [None] * 10
    assert f["take_home"] == 21826.0 and f["social_year"] == 868.0
    assert_sums(f, 2)


def test_f5_day_60_is_projected_linearly(repo):
    profile(repo, O1)
    book(repo, 24000)
    f = calc.finance(repo, 2026, date(2026, 3, 1))
    assert f["projected_profit"] == 146000.0 and f["extrapolated"] is True
    p = f["tax_projected"]
    assert p["social"] == 15135.84 and p["zve"] == 131316.41
    assert p["soli"] == 2420.93 and p["tax_due"] == 46437.93
    assert f["tax_owed"]["owed"] == 1306.0
    r = f["reserve"]
    assert (r["months_left"], r["remaining"], r["monthly"]) == (10, 45131.93, 4513.19)
    assert aside(f) == [0.0, 0.0, 5819.19] + [4513.19] * 9
    assert social(f) == [1261.32] * 3 + [0.0] * 9
    assert th(f) == [-1261.32, 22738.68, -7080.51] + [None] * 9
    assert f["take_home"] == 14396.85 and f["social_year"] == 3783.96
    assert_sums(f, 3)


def test_f6_december_boundary(repo):
    profile(repo, O1)
    book(repo, 24000)
    f = calc.finance(repo, 2026, date(2026, 12, 15))
    assert f["projected_profit"] == 25100.29 and f["extrapolated"] is True
    p = f["tax_projected"]
    assert p["social"] == 5446.8 and p["zve"] == 19793.19 and p["tax_due"] == 1519.0
    assert f["tax_owed"]["owed"] == 1306.0
    r = f["reserve"]
    assert (r["months_left"], r["remaining"], r["monthly"]) == (1, 213.0, 213.0)
    assert aside(f) == [0.0] * 11 + [1519.0]
    assert social(f) == [453.9] * 12
    assert th(f) == [-453.9, 23546.1] + [-453.9] * 9 + [-1972.9]
    assert f["take_home"] == 17034.2 and f["social_year"] == 5446.8
    assert_sums(f, 12)


def test_f7_refund_is_never_added_to_take_home(repo):
    profile(repo, {"employment": {**O5["employment"], "withheld_year": 20000}})
    book(repo, 8000)
    f = calc.finance(repo, 2026, TODAY)
    assert f["tax_projected"]["tax_due"] == 13147.0 and f["tax_projected"]["balance"] == -6853.0
    assert f["tax_owed"]["owed"] == -11115.0
    r = f["reserve"]
    assert (r["owed_now"], r["due_year"], r["remaining"], r["monthly"]) == (0.0, 0.0, 0.0, 0.0)
    assert aside(f) == [0.0] * 12 and social(f) == [0.0] * 12
    assert th(f) == [0.0, 8000.0, 0.0, 0.0, 0.0] + [None] * 7
    assert f["take_home"] == 8000.0


def test_f8a_nothing_booked(repo):
    profile(repo, O1)
    f = calc.finance(repo, 2026, TODAY)
    assert f["projected_profit"] == 0.0
    assert f["tax_projected"]["tax_due"] == f["tax_actual"]["tax_due"] == 0.0
    assert f["tax_actual"]["social"] == 3432.96
    assert f["tax_owed"]["owed"] == 0.0
    r = f["reserve"]
    assert (r["due_year"], r["remaining"], r["monthly"]) == (0.0, 0.0, 0.0)
    assert aside(f) == [0.0] * 12
    assert th(f) == [-286.08] * 5 + [None] * 7
    assert f["take_home"] == -1430.4 and f["social_year"] == 1430.4


def test_f8b_negative_profit(repo):
    profile(repo, O1)
    repo.save("expenses", {"date": "2026-02-10", "net": 5000, "vat": 0})
    f = calc.finance(repo, 2026, TODAY)
    assert f["totals"]["profit"] == -5000.0 and f["projected_profit"] == 0.0
    assert f["tax_actual"]["tax_due"] == 0.0 and f["tax_actual"]["social"] == 3432.96
    assert f["tax_owed"]["owed"] == 0.0
    assert th(f)[1] == -5286.08
    assert f["take_home"] == -6430.4


def test_f9_gewerbe_past_year_literal(repo):
    repo.save_settings({"company_type": "gewerbe", "state": "BY", "hebesatz": 450})
    profile(repo, O4)
    book(repo, 80000)
    f = calc.finance(repo, 2026, date(2027, 3, 15))
    assert f["projected_profit"] == 80000.0 and f["extrapolated"] is False
    for t in (f["tax_projected"], f["tax_actual"]):
        assert t["gewst"] == 8741.25 and t["gewst_credit"] == 7770.0
        assert t["tax_due"] == 12395.37 and t["social"] == 30412.68
    o = f["tax_owed"]
    assert o["as_of"] == "2026-12-31" and o["owed"] == 12395.37 and o["gewst"] == 8741.25
    r = f["reserve"]
    assert (r["months_left"], r["remaining"], r["monthly"], r["owed_now"]) == (0, 0.0, 0.0, 12395.37)
    assert aside(f) == [0.0] * 11 + [12395.37]
    assert social(f) == [2534.39] * 12
    assert th(f) == [-2534.39, 77465.61] + [-2534.39] * 9 + [-14929.76]
    assert f["take_home"] == 37191.95 and f["social_year"] == 30412.68
    assert_sums(f, 12)


def test_f10_future_year_literal(repo):
    profile(repo, O1)
    book(repo, 24000, "2027-02-10")
    f = calc.finance(repo, 2027, TODAY)
    assert f["projected_profit"] == 24000.0 and f["extrapolated"] is False
    assert f["tax_projected"]["tax_due"] == 1306.0
    assert "No tax rules for 2027; using 2026." in f["warnings"]
    assert f["tax_owed"] is None
    r = f["reserve"]
    assert (r["due_year"], r["owed_now"], r["months_left"]) == (1306.0, 0.0, 12)
    assert (r["remaining"], r["monthly"]) == (1306.0, 108.83)
    assert aside(f) == [108.83] * 12
    assert social(f) == [0.0] * 12 and th(f) == [None] * 12
    assert f["take_home"] == 0.0 and f["social_year"] == 0.0
    assert_sums(f, 0)


@pytest.mark.parametrize(
    ("today", "projected", "extrapolated"),
    [
        (date(2028, 2, 28), 24000.0, False),
        (date(2028, 2, 29), 146000.0, True),
        (date(2028, 12, 31), 24000.0, True),
    ],
)
def test_f11_leap_year(repo, today, projected, extrapolated):
    profile(repo, O1)
    book(repo, 24000, "2028-01-15")
    f = calc.finance(repo, 2028, today)
    assert f["projected_profit"] == projected and f["extrapolated"] is extrapolated


def test_f11_day_365_in_a_non_leap_year(repo):
    profile(repo, O1)
    book(repo, 24000)
    f = calc.finance(repo, 2026, date(2026, 12, 31))
    assert f["projected_profit"] == 24000.0 and f["extrapolated"] is True


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
