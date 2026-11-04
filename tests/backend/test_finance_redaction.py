"""The /finance redaction (docs/specs/tax-burden-and-settings.md, "/finance redaction"): exact key sets at every
level for the boss and for everyone else, an allowlist (unknown keys never leak), and no personal value of a seeded
profile anywhere in a non-boss response. ``TODAY`` is pinned; every year is derived from it."""

import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from permita38.core import calc
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.personal_tax import NOT_SAVED_WARNING, PersonalTaxService

from .conftest import PASSWORD
from .transfer_helpers import login

TODAY = date(2027, 3, 15)
YEARS = (TODAY.year - 1, TODAY.year, TODAY.year + 1)

# fake personal figures that must never reach a non-boss (distinctive, so a text search is meaningful)
PROFILE = {
    "church_tax_percent": 9,
    "other_income_year": 31337,
    "other_deductions_year": 5151,
    "other_insurance_year": 6262,
    "children": {"count": 1, "pv_parent": True, "pv_children_under_25": 1},
    "health": {"mode": "gkv", "krankengeld": False},
    "pension": {"mode": "statutory_income"},
    "unemployment": {"mode": "voluntary"},
    "employment": {"gross_year": 77123, "sv_liable": True, "steuerklasse": 1, "withheld_year": 4243.17},
}
SENTINELS = ("31337", "5151", "6262", "77123", "4243.17")
# of these, the ones that surface verbatim in the boss JSON (the others only move derived values such as zve)
SURFACING = ("77123", "4243.17")  # tax_wage / sv_wage / emp_income base and withheld
DERIVED_KEYS = (
    "zve", "est", "soli", "church", "social", "tax_due", "total", "emp_income", "balance", "est_tarif",
    "income_tax_total", "withheld", "tax_wage", "sv_wage",
)  # fmt: skip


def _has(text, number):
    """The number as a whole token (not a part of a longer figure)."""
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", text) is not None


def personal_values(boss):
    """Strings of the boss-only derived figures (zve, income tax, social, ...) that no business figure shares."""
    pub = calc.redact_tax(boss)
    business = {json.dumps(v) for v in (pub["tax_actual"].values()) if isinstance(v, float)}
    out = set()
    for k in DERIVED_KEYS:
        v = boss["tax_actual"][k]
        if isinstance(v, float) and abs(v) >= 1000 and json.dumps(v) not in business:
            out.add(json.dumps(v))
    return sorted(out)


def assert_nothing_personal(boss, serialised):
    """Positive control on the boss JSON first, then none of the sentinels / derived values in the other one."""
    boss_text = json.dumps(boss)
    for v in SURFACING:
        assert _has(boss_text, v), v
    derived = personal_values(boss)
    assert len(derived) >= 6  # zve, est, social, ... really differ from the business figures
    for v in derived:
        assert _has(boss_text, v), v
    for v in (*SENTINELS, *derived):
        assert not _has(serialised, v), v


BOSS_TOP = {
    "year", "months", "totals", "tax_actual", "tax_projected", "tax_owed", "reserve", "projected_profit",
    "extrapolated", "effective_rate", "social_year", "take_home", "categories", "open_receivables", "warnings",
    "kleinunternehmer", "company_type",
}  # fmt: skip
BOSS_MONTH = {
    "month", "revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due", "tax_set_aside", "social",
    "vat_set_aside", "take_home",
}  # fmt: skip
BOSS_OWED = {"as_of", "profit_ytd", "est", "soli", "church", "gewst", "kst", "kst_soli", "tax_due", "withheld", "owed"}
BOSS_RESERVE = {"due_year", "withheld", "owed_now", "remaining", "months_left", "monthly", "balance"}
BOSS_TAX = {
    "profit", "social", "est", "soli", "church", "gewst", "kst", "gewst_credit", "income_tax_total", "total", "rate",
    "zve", "kst_soli", "tax_due", "withheld", "est_tarif", "kg_added", "kfb_used", "zuschlag_base", "soli_base",
    "vorsorge", "insurance", "emp_income", "tax_wage", "sv_wage", "balance", "warnings",
}  # fmt: skip

RED_TOP = {
    "year", "months", "totals", "categories", "open_receivables", "kleinunternehmer", "company_type",
    "projected_profit", "extrapolated", "warnings", "tax_actual", "tax_projected", "tax_owed", "reserve", "redacted",
}  # fmt: skip
RED_MONTH = {"month", "revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due", "vat_set_aside"}
RED_TOTALS = {"revenue", "vat_out", "expenses", "vat_in", "profit", "vat_due"}
RED_TAX = {"gewst", "kst", "kst_soli", "redacted"}
RED_OWED = {"as_of", "gewst", "kst", "kst_soli", "redacted"}


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "d")
    r.startup()
    r.save_settings({"company_type": "gewerbe", "state": "BY", "hebesatz": 450})
    PersonalTaxService(r).update(PROFILE)
    r.save(
        "invoices",
        {
            "status": "paid",
            "date": f"{TODAY.year - 1}-02-10",
            "paid_date": f"{TODAY.year - 1}-02-10",
            "lines": [{"qty": 1, "price": 80000}],
        },
    )
    yield r
    r.close()


def assert_boss_keys(f):
    assert set(f) == BOSS_TOP
    assert all(set(m) == BOSS_MONTH for m in f["months"])
    assert set(f["tax_actual"]) == set(f["tax_projected"]) == BOSS_TAX
    assert set(f["reserve"]) == BOSS_RESERVE
    if f["tax_owed"] is not None:
        assert set(f["tax_owed"]) == BOSS_OWED


def assert_redacted_keys(f):
    assert set(f) == RED_TOP and f["redacted"] is True
    assert set(f["totals"]) == RED_TOTALS
    assert len(f["months"]) == 12 and all(set(m) == RED_MONTH for m in f["months"])
    assert set(f["tax_actual"]) == set(f["tax_projected"]) == RED_TAX
    assert f["tax_actual"]["redacted"] is True and f["tax_projected"]["redacted"] is True
    if f["tax_owed"] is not None:
        assert set(f["tax_owed"]) == RED_OWED and f["tax_owed"]["redacted"] is True
    assert f["reserve"] == {"redacted": True}
    assert NOT_SAVED_WARNING not in f["warnings"]


@pytest.mark.parametrize("year", YEARS)
def test_boss_view_key_sets(repo, year):
    f = calc.finance(repo, year, TODAY)
    assert_boss_keys(f)
    assert (f["tax_owed"] is None) == (year > TODAY.year)


@pytest.mark.parametrize("year", YEARS)
def test_redacted_view_key_sets_and_no_personal_value(repo, year):
    boss = calc.finance(repo, year, TODAY)
    f = calc.redact_tax(boss)
    assert_redacted_keys(f)
    assert (f["tax_owed"] is None) == (year > TODAY.year)
    assert_nothing_personal(boss, json.dumps(f))


def test_the_other_income_sentinel_moves_the_boss_zve(repo):
    """Control: 31337 / 5151 / 6262 do not appear verbatim but change the boss's zve, which is what must not leak."""
    year = TODAY.year - 1
    with_income = calc.finance(repo, year, TODAY)["tax_actual"]["zve"]
    PersonalTaxService(repo).update(
        {**PROFILE, "other_income_year": 0, "other_deductions_year": 0, "other_insurance_year": 0}
    )
    assert calc.finance(repo, year, TODAY)["tax_actual"]["zve"] != with_income


def test_redaction_keeps_the_rule_table_warnings(tmp_path):
    r = SQLiteRepository(tmp_path / "d2")
    r.startup()
    r.save_settings({"company_type": "freiberufler"})  # no profile saved: the boss gets NOT_SAVED_WARNING
    full = calc.finance(r, TODAY.year, TODAY)
    assert NOT_SAVED_WARNING in full["warnings"]
    red = calc.redact_tax(full)
    assert NOT_SAVED_WARNING not in red["warnings"]
    assert red["warnings"] == [w for w in full["warnings"] if w != NOT_SAVED_WARNING]
    assert any("not yet confirmed" in w or "No tax rules" in w for w in red["warnings"])
    r.close()


def test_unknown_keys_do_not_reach_the_redacted_output(repo):
    f = calc.finance(repo, TODAY.year - 1, TODAY)
    f["secret_top"] = 1
    f["totals"]["secret_totals"] = 1
    f["months"][0]["secret_month"] = 1
    f["tax_actual"]["secret_tax"] = 1
    f["tax_projected"]["secret_tax"] = 1
    f["tax_owed"]["secret_owed"] = 1
    f["reserve"]["secret_reserve"] = 1
    out = calc.redact_tax(f)
    assert_redacted_keys(out)
    assert "secret" not in json.dumps(out)


def test_f9_redacted_values(repo):
    f = calc.redact_tax(calc.finance(repo, TODAY.year - 1, TODAY))
    # F9 uses the O4 profile; the redacted business figures do not depend on the personal part
    assert f["tax_actual"] == f["tax_projected"] == {"gewst": 8741.25, "kst": 0, "kst_soli": 0, "redacted": True}
    assert f["tax_owed"] == {
        "as_of": f"{TODAY.year - 1}-12-31",
        "gewst": 8741.25,
        "kst": 0,
        "kst_soli": 0,
        "redacted": True,
    }
    assert f["projected_profit"] == 80000.0 and f["extrapolated"] is False


# -- API (the server picks the current year: no ``year`` parameter) ---------------------------------------------
def _accountant(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "acc", "name": "A", "role": "accountant", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return login(app, "acc")


def _token_client(app, boss_client, read_only):
    made = boss_client.post("/api/tokens", json={"label": "t", "read_only": read_only})
    assert made.status_code == 201, made.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return c


@pytest.fixture
def seeded(boss_client):
    r = boss_client.put("/api/settings/personal-tax", json=PROFILE)
    assert r.status_code == 200, r.text
    return boss_client


def test_boss_session_gets_the_full_key_sets(seeded):
    r = seeded.get("/api/finance")
    assert r.status_code == 200, r.text
    f = r.json()
    assert_boss_keys(f)
    assert "redacted" not in f and "redacted" not in f["tax_actual"]
    assert _has(r.text, "4243.17") and _has(r.text, "77123")


@pytest.mark.parametrize("who", ["accountant", "token_ro", "token_rw"])
def test_everyone_else_gets_the_redacted_key_sets(app, seeded, who):
    c = {
        "accountant": lambda: _accountant(app, seeded),
        "token_ro": lambda: _token_client(app, seeded, True),
        "token_rw": lambda: _token_client(app, seeded, False),
    }[who]()
    boss = seeded.get("/api/finance").json()
    r = c.get("/api/finance")
    assert r.status_code == 200, r.text
    f = r.json()
    assert_redacted_keys(f)
    assert_nothing_personal(boss, r.text)
