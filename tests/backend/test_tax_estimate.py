"""Tax engine oracle cases O1, O5, O7 of docs/specs/tax-burden-and-settings.md (2026 table), plus properties."""

from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core import tax

SET = {"company_type": "freiberufler", "state": "BY", "hebesatz": 400}
KG_FALSE = {"health": {"mode": "gkv", "krankengeld": False}}


def est(profit, profile, year=2026):
    return tax.estimate(D(profit), year, SET, profile)


def test_o1_freiberufler_gkv_krankengeld():
    r = est(60000, {})
    assert r["insurance"]["kv"] == D("10500.00")
    assert r["insurance"]["pv"] == D("2520.00")
    assert r["social"] == D("13020.00")
    v = r["vorsorge"]
    assert (v["B"], v["O"], v["C"], v["dBO"]) == (D("12600.00"), D("420.00"), D(2800), D("12600.00"))
    assert v["A"] == 0 and v["dA"] == 0
    assert r["zve"] == D("47364.00")
    assert r["est_tarif"] == 9634 and r["est"] == D("9634")
    assert r["soli_base"] == 9634 and r["soli"] == 0 and r["church"] == 0
    assert r["income_tax_total"] == D("9634.00")
    assert r["tax_due"] == D("9634.00")
    assert r["total"] == D("22654.00")
    assert r["rate"] == D(9634) / D(60000)
    assert r["balance"] is None and r["warnings"] == []


def test_o5_employment_plus_side_income():
    profile = {
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
    r = est(20000, profile)
    assert r["tax_wage"] == D(48080) and r["sv_wage"] == D(48080)
    assert r["emp_income"] == D(46850)
    i = r["insurance"]
    assert i["ee_kv"] == D("4206.96")
    assert i["ee_pv"] == D("1153.92")
    assert i["ee_rv"] == D("4471.44")
    assert i["ee_alv"] == D("625.08")
    assert i["kv"] == 0 and i["pv"] == 0 and i["rv"] == 0 and i["alv"] == 0
    assert r["social"] == D("10457.40")
    v = r["vorsorge"]
    assert v["A"] == D("8942.88") and v["dA"] == D("4471.44")
    assert v["B"] == D("5192.60") and v["O"] == D("793.36")
    assert v["C"] == D(1900) and v["dBO"] == D("5192.60")
    assert r["zve"] == D("57149.96")
    assert r["est"] == D(13147) and r["soli"] == 0
    assert r["income_tax_total"] == D("13147.00")
    assert r["total"] == D("23604.40")
    assert r["withheld"] == D(7000) and r["balance"] == D("6147.00")


def test_o7_low_profit_minimum_base():
    r = est(12000, KG_FALSE)
    assert r["insurance"]["kv"] == D("2673.60")
    assert r["insurance"]["pv"] == D("664.44")
    assert r["social"] == D("3338.04")
    assert r["vorsorge"]["B"] == D("3338.04") and r["vorsorge"]["dBO"] == D("3338.04")
    assert r["zve"] == D("8625.96")
    assert r["est"] == 0
    assert r["total"] == D("3338.04")


def test_fallback_warning_for_unknown_year():
    assert est(60000, {}, year=2030)["warnings"] == ["No tax rules for 2030; using 2026."]


def test_joint_and_church():
    r = tax.estimate(D(100000), 2026, SET, {"assessment": "joint", "church_tax_percent": 9})
    assert r["est_tarif"] == tax.est(r["zve"], 2026, True)
    assert r["church"] == (D(r["est_tarif"]) * 9 / 100).quantize(D("0.01"), rounding="ROUND_DOWN")


profits = st.integers(min_value=-50_000, max_value=400_000)
profiles = st.sampled_from([{}, KG_FALSE, {"assessment": "joint"}, {"health": {"mode": "none"}}])


@settings(max_examples=60, deadline=None)
@given(p1=profits, p2=profits, profile=profiles)
def test_total_monotone_in_profit(p1, p2, profile):
    lo, hi = sorted((p1, p2))
    assert est(hi, profile)["total"] >= est(lo, profile)["total"]


@settings(max_examples=60, deadline=None)
@given(p=profits, profile=profiles, income=st.integers(0, 150_000))
def test_vorsorge_and_soli_invariants(p, profile, income):
    profile = {**profile, "other_income_year": income, "pension": {"mode": "fixed", "fixed_monthly": 9000}}
    r = est(p, profile)
    v = r["vorsorge"]
    assert v["dBO"] >= v["B"]
    assert v["dA"] <= v["H"]
    assert r["soli"] <= r["soli_base"] * D("0.055")
