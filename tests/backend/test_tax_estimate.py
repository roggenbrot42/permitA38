"""Tax engine oracle cases O1-O7 of docs/specs/tax-burden-and-settings.md (2026 table), plus properties."""

from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core import tax
from permita38.core.tax_rules import rules_for

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
    # intermediates: base_m 5000.00 (kv 17.5 % -> 875.00 / month), sa 36, no Gewerbesteuer
    assert r["insurance"]["kv"] / 12 == D("875.00") and r["insurance"]["pv"] / 12 == D("210.00")
    assert D(60000) - D("12600.00") - 36 == r["zve"]
    assert r["gewst"] == 0 and r["gewst_credit"] == 0 and r["kst"] == 0 and r["kg_added"] == 0


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
    # base_m 1318.33 (minimum), per month kv 222.80 and pv 55.37
    assert r["insurance"]["kv"] / 12 == D("222.80") and r["insurance"]["pv"] / 12 == D("55.37")


def test_fallback_warning_for_unknown_year():
    assert est(60000, {}, year=2030)["warnings"] == ["No tax rules for 2030; using 2026."]


def test_joint_and_church():
    r = tax.estimate(D(100000), 2026, SET, {"assessment": "joint", "church_tax_percent": 9})
    # base_m capped at 5812.50: kv 12 x 1017.19, pv 12 x 244.13, Krankengeld cut 488.25, sa 72 (joint)
    assert r["insurance"]["kv"] == D("12206.28") and r["insurance"]["pv"] == D("2929.56")
    assert r["vorsorge"]["B"] == D("14647.59") and r["vorsorge"]["O"] == D("488.25")
    assert r["zve"] == D("85280.41")
    # splitting: 2 x T(42 640) = 2 x 8 057
    assert r["est_tarif"] == 16114 and r["est"] == D(16114)
    assert r["soli_base"] == 16114 and r["soli"] == 0
    assert r["church"] == D("1450.26")
    assert r["income_tax_total"] == D("17564.26")


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
    assert D(90000) - 6000 - 6480 - 72 == r["zve"]  # sa 72 (joint)
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
    assert D(200000) - 6000 - 6480 - 72 == r["zve"]  # sa 72 (joint)
    assert r["zve"] - 2 * 9756 == D(167936)  # z after both children took the Freibetrag (kfb 9756 each)
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
    assert D(80000) - 14880 - D("14298.72") - 36 == r["zve"]  # dA, dBO, sa 36
    # kv base 5812.50 (BBG cap): 12 x 982.31; rv base 6666.67: 12 x 1240.00
    assert i["kv"] / 12 == D("982.31") and i["rv"] / 12 == D("1240.00")
    assert r["est_tarif"] == 9139
    # Gewerbesteuer: ertrag 55 500, Messbetrag 1 942.50, gewst = 1 942.50 x 4.5, share 1 so Hoechstbetrag = est_tarif
    # 9 139.00; the limb 4 x Messbetrag = 7 770.00 binds
    assert r["gewst"] == D("8741.25") and r["gewst_credit"] == D("7770.00")
    assert r["gewst_credit"] < D("8741.25") and r["gewst_credit"] < r["est_tarif"]
    assert r["est_tarif"] - r["gewst_credit"] == D(1369)  # tariff after the credit
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
    assert 0 <= r["gewst_credit"] <= r["gewst"]
    if p <= 0:  # no trade profit: no Gewerbesteuer, hence no section 35 credit, with or without other income
        assert r["gewst"] == 0 and r["gewst_credit"] == 0
    r0 = est(p, {}, company_type="gewerbe", hebesatz=hs)
    assert 0 <= r0["gewst_credit"] <= r0["gewst"]
    if p <= 0:
        assert r0["gewst"] == 0 and r0["gewst_credit"] == 0


# --- section 35 limbs -------------------------------------------------------------------------------------------------


def test_section35_gewst_limb_binds():
    # O4 profile with Hebesatz 200: gewst = 1 942.50 x 2 = 3 885.00 < 4 x Messbetrag 7 770.00 < Hoechstbetrag 9 139.00
    r = est(80000, O4_PROFILE, company_type="gewerbe", hebesatz=200)
    assert r["gewst"] == D("3885.00") and r["gewst_credit"] == D("3885.00")
    assert r["est_tarif"] == 9139 and r["est"] == D("6808.00")  # 9 139 - 3 885 + 1 554


def test_section35_hoechstbetrag_limb_binds():
    # no insurance, other deductions 50 000: zvE 30 000, T(30 000) = 4 217 < 7 770 and < gewst 8 741.25
    profile = {"health": {"mode": "none"}, "other_deductions_year": 50000}
    r = est(80000, profile, company_type="gewerbe", hebesatz=450)
    assert r["zve"] == D(30000) and r["est_tarif"] == 4217
    assert r["gewst"] == D("8741.25") and r["gewst_credit"] == D("4217.00")
    assert r["est"] == D("0.00")


def test_section35_share_strictly_between_zero_and_one():
    # Gewerbe profit 80 000 and other income 80 000: share 0.5, zvE 160 000 - 140 000 = 20 000, T(20 000) = 1 570
    profile = {"health": {"mode": "none"}, "other_income_year": 80000, "other_deductions_year": 140000}
    r = est(80000, profile, company_type="gewerbe", hebesatz=450)
    assert r["zve"] == D(20000) and r["est_tarif"] == 1570
    assert r["gewst"] == D("8741.25")
    assert r["gewst_credit"] == D("785.00")  # q2(1 570 x 0.5); 4 x Messbetrag 7 770 and gewst are not binding
    assert 0 < r["gewst_credit"] < r["est_tarif"]
    assert r["est"] == D("785.00")


def test_gewerbe_ertrag_rounded_down_to_hundreds():
    r = est(D("80099.99"), O4_PROFILE, company_type="gewerbe", hebesatz=450)
    assert r["gewst"] == D("8741.25")  # ertrag floor(800.99) x 100 - 24 500 = 55 500


# --- boundaries -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profit", "kv", "pv"),
    [
        (69749, "12206.04", "2929.44"),  # base 5812.42, below the cap
        (69750, "12206.28", "2929.56"),  # base 5812.50 = BBG 69 750 / 12
        (69751, "12206.28", "2929.56"),  # capped
    ],
)
def test_health_bbg_boundary(profit, kv, pv):
    r = est(profit, {})
    assert r["insurance"]["kv"] == D(kv) and r["insurance"]["pv"] == D(pv)


@pytest.mark.parametrize(
    ("profit", "rv"),
    [(101399, "18860.28"), (101400, "18860.40"), (101401, "18860.40")],  # base 8449.92, 8450, capped
)
def test_pension_bbg_boundary(profit, rv):
    r = est(profit, {"pension": {"mode": "statutory_income"}})
    assert r["insurance"]["rv"] == D(rv)


@pytest.mark.parametrize(
    ("base", "joint", "expected"),
    [
        (20350, False, "0"),  # at the Freigrenze
        (20351, False, "0.11"),  # 1 x 11.9 % = 0.119, truncated
        (40000, False, "2200.00"),  # 5.5 % limb: 2 200.00 < 19 650 x 11.9 % = 2 338.35
        (40700, False, "2238.50"),  # single: 5.5 % x 40 700 < 20 350 x 11.9 % = 2 421.65
        (40700, True, "0"),  # joint Freigrenze 40 700
        (40701, True, "0.11"),
        (40000, True, "0"),
    ],
)
def test_soli_boundaries(base, joint, expected):
    assert tax._soli(D(base), rules_for(2026), joint) == D(expected)


def test_soli_five_five_percent_limb_end_to_end():
    # no insurance: zvE 200 000 - 36 = 199 964, T = 0.42 x 199 964 - 11 135.63 = 72 849.25 -> 72 849
    r = est(200000, {"health": {"mode": "none"}})
    assert r["zve"] == D(199964) and r["est_tarif"] == 72849
    assert r["soli"] == D("4006.69")  # 5.5 % x 72 849 = 4 006.695 (limb 11.9 % x 52 499 = 6 247.38 is larger)
    assert r["tax_due"] == D("76855.69")


NO_INS = {"health": {"mode": "none"}}


def test_kindergeld_break_even_keeps_kindergeld():
    # zvE 42 970 (profit 43 006, sa 36): T(42 970) - T(38 092) = 8 165 - 6 611 = 1 554 = Kindergeld, not greater
    r = est(43006, {**NO_INS, "children": {"count": 1}})
    assert r["zve"] == D(42970)
    assert r["kfb_used"] == 0 and r["kg_added"] == 0
    assert r["est_tarif"] == 8165 and r["est"] == D(8165)
    assert r["zuschlag_base"] == 6611


def test_just_above_break_even_takes_freibetrag():
    # zvE 43 025 (profit 43 061): T(43 025) - T(38 147) = 8 183 - 6 628 = 1 555 > 1 554
    r = est(43061, {**NO_INS, "children": {"count": 1}})
    assert r["zve"] == D(43025)
    assert r["kfb_used"] == 1 and r["kg_added"] == D(1554)
    assert r["est_tarif"] == 6628 and r["est"] == D(8182)


def test_mixed_children_first_freibetrag_second_kindergeld():
    # zvE 46 000: child 1 delta T(46 000) - T(41 122) = 9 171 - 7 566 = 1 605 > 1 554 -> Freibetrag;
    # child 2 delta T(41 122) - T(36 244) = 7 566 - 6 045 = 1 521 <= 1 554 -> Kindergeld
    r = est(46036, {**NO_INS, "children": {"count": 2}})
    assert r["zve"] == D(46000)
    assert r["kfb_used"] == 1 and r["kg_added"] == D(1554)
    assert r["est_tarif"] == 7566 and r["est"] == D(9120)
    assert r["zuschlag_base"] == 6045  # T(46 000 - 2 x 4 878)


@pytest.mark.parametrize("profit", [0, -5000])
def test_zero_and_negative_profit(profit):
    r = est(profit, {})
    # minimum base 1 318.33: kv 12 x 230.71, pv 12 x 55.37
    assert r["insurance"]["kv"] == D("2768.52") and r["insurance"]["pv"] == D("664.44")
    assert r["social"] == D("3432.96")
    assert r["zve"] == 0 and r["est"] == 0 and r["soli"] == 0
    assert r["rate"] == 0
    assert r["tax_due"] == 0 and r["total"] == D("3432.96")


def test_full_estimate_year_2025():
    # same contributions as O1; 2025 tariff: T(47 364) = 9 768
    r = est(60000, {}, year=2025)
    assert r["warnings"] == []
    assert r["social"] == D("13020.00") and r["zve"] == D("47364.00")
    assert r["est_tarif"] == 9768 and r["est"] == D(9768)
    assert r["soli"] == 0
    assert r["tax_due"] == D("9768.00") and r["total"] == D("22788.00")
    assert r["rate"] == D(9768) / D(60000)


# --- regressions (review findings) --------------------------------------------------------------------------------------

EMP50 = {"gross_year": 50000, "sv_liable": True}


def test_health_none_with_sv_liable_job_pays_no_kv_pv():
    r = est(20000, {"health": {"mode": "none"}, "employment": EMP50})
    i = r["insurance"]
    assert i["ee_kv"] == 0 and i["ee_pv"] == 0
    assert i["ee_rv"] == D("4650.00") and i["ee_alv"] == D("650.04")
    assert r["social"] == D("5300.04")  # 4 650.00 + 650.04
    assert r["vorsorge"]["B"] == 0 and r["vorsorge"]["O"] == D("650.04")


@pytest.mark.parametrize("emp", [{"gross_year": 50000}, {"gross_year": 50000, "sv_liable": None}])
def test_sv_liable_missing_or_null_means_liable(emp):
    r = est(20000, {"employment": emp})
    assert r["insurance"]["ee_rv"] == D("4650.00") and r["insurance"]["ee_alv"] == D("650.04")
    assert r["insurance"]["kv"] == 0  # covered by the job


def test_sv_liable_false_is_not_liable():
    r = est(20000, {"employment": {"gross_year": 50000, "sv_liable": False}})
    assert r["insurance"]["ee_rv"] == 0 and r["insurance"]["ee_alv"] == 0 and r["insurance"]["ee_kv"] == 0
    assert r["insurance"]["kv"] > 0


@pytest.mark.parametrize("hs", [None, "", 400])
def test_hebesatz_null_or_empty_falls_back_to_400(hs):
    r = est(80000, {}, company_type="gewerbe", hebesatz=hs)
    assert r["gewst"] == D("7770.00")  # Messbetrag 1 942.50 x 4


def test_hebesatz_explicit_zero_stays_zero():
    r = est(80000, {}, company_type="gewerbe", hebesatz=0)
    assert r["gewst"] == 0 and r["gewst_credit"] == 0


def test_negative_counts_are_clamped_to_zero():
    r = est(60000, {"children": {"count": -2, "pv_children_under_25": -3}})
    assert r["insurance"]["pv"] == D("2520.00")  # 4.2 %, no child reduction
    assert r["kfb_used"] == 0 and r["kg_added"] == 0
    assert r["est_tarif"] == 9634 and r["zuschlag_base"] == 9634  # not increased by negative allowances
