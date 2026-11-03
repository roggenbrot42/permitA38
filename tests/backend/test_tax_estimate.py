"""Tax engine oracle cases O1-O7 of docs/specs/tax-burden-and-settings.md (2026 table), plus properties."""

from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core import tax

SET = {"company_type": "freiberufler", "state": "BY", "hebesatz": 400}
KG_FALSE = {"health": {"mode": "gkv", "krankengeld": False}}


def est(profit, profile, year=2026, **sett):
    return tax.estimate(D(profit), year, {**SET, **sett}, profile)


O2_PROFILE = {
    "assessment": "joint",
    "church_tax_percent": 9,
    "children": {"count": 2, "pv_children_under_25": 2},
    "health": {"mode": "pkv", "pkv_monthly": 600, "pkv_basis_monthly": 480, "pkv_care_monthly": 60},
    "pension": {"mode": "fixed", "fixed_monthly": 500},
}


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


def _o2_common(r):
    assert r["social"] == D(13920)
    v = r["vorsorge"]
    assert (v["A"], v["H"], v["dA"]) == (D(6000), D(61652), D(6000))
    assert (v["B"], v["O"], v["C"], v["dBO"]) == (D(6480), D(1440), D(2800), D(6480))


def test_o2_kindergeld_wins():
    r = est(90000, O2_PROFILE)
    _o2_common(r)
    assert r["zve"] == D(77448)
    assert tax.est(77448, 2026, True) - tax.est(77448 - 9756, 2026, True) == 2962
    assert r["kfb_used"] == 0 and r["kg_added"] == 0
    assert r["est_tarif"] == 13616 and r["est"] == D(13616)
    assert r["zuschlag_base"] == 7856 and r["soli"] == 0
    assert r["church"] == D("707.04")
    assert r["income_tax_total"] == D("14323.04")
    assert r["total"] == D("28243.04")


def test_o3_freibetraege_win_soli_milderungszone():
    r = est(200000, O2_PROFILE)
    _o2_common(r)
    assert r["zve"] == D(187448)
    assert r["kfb_used"] == 2 and r["kg_added"] == D(6216)
    assert r["est_tarif"] == 48260 and r["est"] == D(54476)
    assert r["zuschlag_base"] == 48260 and r["soli_base"] == 48260
    assert r["soli"] == D("899.64")
    assert r["church"] == D("4343.40")
    assert r["income_tax_total"] == D("59719.04")
    assert r["total"] == D("73639.04")


O4_PROFILE = {
    "church_tax_percent": 8,
    "children": {"count": 1, "pv_parent": True, "pv_children_under_25": 1},
    "health": {"mode": "gkv", "krankengeld": False},
    "pension": {"mode": "statutory_income"},
    "unemployment": {"mode": "voluntary"},
}


def test_o4_gewerbe_with_child_and_section_35():
    r = est(80000, O4_PROFILE, company_type="gewerbe", hebesatz=450)
    i = r["insurance"]
    assert i["kv"] == D("11787.72") and i["pv"] == D("2511.00")
    assert i["rv"] == D("14880.00") and i["alv"] == D("1233.96")
    assert r["social"] == D("30412.68")
    v = r["vorsorge"]
    assert v["A"] == D(14880) and v["dA"] == D(14880)
    assert v["B"] == D("14298.72") and v["O"] == D("1233.96") and v["dBO"] == D("14298.72")
    assert r["zve"] == D("50785.28")
    assert tax.est(50785.28, 2026) - tax.est(50785.28 - 4878, 2026) == 1686
    assert r["kfb_used"] == 1 and r["kg_added"] == D(1554)
    assert r["est_tarif"] == 9139
    assert r["gewst"] == D("8741.25") and r["gewst_credit"] == D("7770.00")
    assert r["est"] == D("2923.00")
    assert r["zuschlag_base"] == 9139 and r["soli_base"] == 1369 and r["soli"] == 0
    assert r["church"] == D("731.12")
    assert r["income_tax_total"] == D("3654.12")
    assert r["tax_due"] == D("12395.37")
    assert r["total"] == D("42808.05")


def test_o6_gmbh():
    profile = {
        "employment": {"gross_year": 60000, "sv_liable": False},
        "health": {"mode": "pkv", "pkv_monthly": 700, "pkv_basis_monthly": 560, "pkv_care_monthly": 70},
        "pension": {"mode": "fixed", "fixed_monthly": 400},
    }
    r = est(100000, profile, company_type="gmbh", hebesatz=400)
    assert r["kst"] == D("15000.00") and r["kst_soli"] == D("825.00") and r["gewst"] == D("14000.00")
    assert r["gewst_credit"] == 0
    assert r["emp_income"] == D(58770) and r["social"] == D(14040)
    v = r["vorsorge"]
    assert (v["A"], v["dA"], v["B"], v["O"], v["dBO"]) == (D(4800), D(4800), D(7560), D(1680), D(7560))
    assert r["zve"] == D(46374)
    assert r["est"] == D(9297) and r["income_tax_total"] == D("9297.00")
    assert r["tax_due"] == D("39122.00")
    assert r["total"] == D("53162.00")


@settings(max_examples=60, deadline=None)
@given(p=st.integers(0, 400_000), n=st.integers(0, 4), joint=st.booleans(), full=st.booleans())
def test_guenstigerpruefung_never_worse_than_kindergeld(p, n, joint, full):
    profile = {
        "assessment": "joint" if joint else "single",
        "children": {"count": n, "full_allowance": full},
    }
    r = est(p, profile)
    # Kindergeld only: tariff on zvE, nothing added
    assert r["est"] <= D(tax.est(r["zve"], 2026, joint))
    assert r["kfb_used"] <= n


@settings(max_examples=60, deadline=None)
@given(p=st.integers(-50_000, 300_000), other=st.integers(0, 50_000), hs=st.integers(0, 600))
def test_credit_zero_without_positive_income_and_bounded(p, other, hs):
    r = est(p, {"other_income_year": other}, company_type="gewerbe", hebesatz=hs)
    assert r["gewst_credit"] <= r["gewst"]
    if p <= 0:  # business share 0: no section 35 credit
        assert r["gewst_credit"] == 0
    r0 = est(p, {}, company_type="gewerbe", hebesatz=hs)
    if p <= 0:
        assert r0["gewst"] == 0 and r0["gewst_credit"] == 0
