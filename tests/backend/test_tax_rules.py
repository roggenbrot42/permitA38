"""Rule tables (docs/specs/tax-burden-and-settings.md, B1). The 2026 literals are the spec table."""

from decimal import Decimal

import pytest

from permita38.core.tax_rules import available_years, load_raw, rule_keys, rules_for

SPEC_2026 = {
    "est_gf": "12348",
    "est_z2": "17799",
    "est_z3": "69878",
    "est_z4": "277825",
    "est_a2": "914.51",
    "est_a3": "173.10",
    "est_c3": "1034.87",
    "est_k4": "11135.63",
    "est_k5": "19470.38",
    "soli_rate": "5.5",
    "soli_cap_rate": "11.9",
    "soli_freigrenze": "20350",
    "kinderfreibetrag": "3414",
    "bea_freibetrag": "1464",
    "kindergeld_month": "259",
    "an_pauschbetrag": "1230",
    "sa_pauschbetrag": "36",
    "vorsorge_hoechst_altersvorsorge": "30826",
    "vorsorge_cap_self": "2800",
    "vorsorge_cap_employee": "1900",
    "kv_basis_cut_percent": "4",
    "gew_freibetrag": "24500",
    "gew_messzahl": "3.5",
    "gew_credit_factor": "4.0",
    "kst_rate": "15",
    "bezugsgroesse_month": "3955",
    "bbg_kv_month": "5812.50",
    "bbg_rv_month": "8450",
    "bbg_rv_year": "101400",
    "kv_min_base_month": "1318.33",
    "kv_rate_general": "14.6",
    "kv_rate_reduced": "14.0",
    "kv_zusatz_average": "2.9",
    "pv_rate": "3.6",
    "pv_childless_surcharge": "0.6",
    "pv_child_reduction": "0.25",
    "pv_employer_share": "1.8",
    "pv_employer_share_sn": "1.3",
    "rv_rate": "18.6",
    "rv_min_base_month": "603",
    "alv_rate": "2.6",
    "alv_voluntary_month": "102.83",
    "alv_voluntary_half_month": "51.42",
    "bav_tax_free_percent": "8",
    "bav_sv_free_percent": "4",
}


@pytest.mark.parametrize("year", [2025, 2026])
def test_every_key_present_with_source_and_valid_from(year):
    raw = load_raw(year)["values"]
    assert set(raw) == set(rule_keys()) == set(SPEC_2026)
    for key, obj in raw.items():
        assert obj["source"], key
        assert obj["valid_from"].startswith(f"{year}-"), key
        Decimal(obj["value"])
        assert obj.get("status", "to_verify") == "to_verify"
    assert set(rules_for(year).__dict__) >= set(rule_keys())


def test_2026_equals_spec_table():
    raw = load_raw(2026)["values"]
    assert {k: v["value"] for k, v in raw.items()} == SPEC_2026
    r = rules_for(2026)
    for key, text in SPEC_2026.items():
        assert getattr(r, key) == Decimal(text), key
    assert r.warning is None and r.year == 2026


def test_2026_to_verify_marks():
    assert set(rules_for(2026).to_verify) == {"pv_employer_share_sn", "alv_voluntary_half_month"}


def test_2025_tariff_and_unresearched_values():
    r = rules_for(2025)
    assert (r.est_gf, r.est_z2, r.est_z3) == (Decimal(12096), Decimal(17443), Decimal(68480))
    assert (r.est_a2, r.est_a3, r.est_c3) == (Decimal("932.30"), Decimal("176.64"), Decimal("1015.13"))
    assert (r.est_k4, r.est_k5) == (Decimal("10911.92"), Decimal("19246.67"))
    assert r.soli_freigrenze == Decimal(19950) and r.vorsorge_hoechst_altersvorsorge == Decimal(29344)
    assert r.to_verify and "est_gf" not in r.to_verify and "soli_freigrenze" not in r.to_verify
    assert "bbg_kv_month" in r.to_verify and "kst_rate" in r.to_verify
    assert load_raw(2025)["values"]["kst_rate"]["note"] == "2025 value not researched"


def test_fallback_year_and_warning():
    assert available_years() == (2025, 2026)
    newer = rules_for(2030)
    assert newer.year == 2026 and newer.requested_year == 2030
    assert newer.warning == "No tax rules for 2030; using 2026."
    older = rules_for(2020)
    assert older.year == 2025 and older.warning == "No tax rules for 2020; using 2025."
    assert rules_for(2025).warning is None
