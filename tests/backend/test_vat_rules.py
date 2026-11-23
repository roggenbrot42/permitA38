"""VAT rule tables: boxes of docs/specs/ustva.md and the tax-key map of docs/specs/accounting.md section 6."""

from decimal import Decimal

import pytest

from permita38.core.vat_rules import VatRulesMissing, available_years, vat_rules_for

BOX_KINDS = {
    "81": "base",
    "86": "base",
    "35": "base",
    "36": "tax_entered",
    "21": "base",
    "46": "base",
    "47": "tax_entered",
    "84": "base",
    "85": "tax_entered",
    "66": "amount",
    "67": "amount",
    "39": "amount",
    "83": "result",
    "10": "flag",
}
# accounting.md section 6 without KU and NONE (no box)
MAPPED_KEYS = {"S19", "S7", "SX", "EU_SVC", "RC_EU_SVC", "RC_3C_SVC", "VST"}


def test_2026_is_available():
    assert 2026 in available_years()


def test_boxes_present_with_kind_and_labels():
    rules = vat_rules_for(2026)
    assert {b.kz: b.kind for b in rules.boxes} == BOX_KINDS
    assert len(rules.boxes) == len(BOX_KINDS)
    assert all(b.label_de and b.label_en for b in rules.boxes)
    assert [(b.kz, b.computed_tax_rate) for b in rules.boxes if b.computed_tax_rate is not None] == [
        ("81", Decimal("19")),
        ("86", Decimal("7")),
    ]


def test_map_covers_every_tax_key_but_ku_and_none():
    rules = vat_rules_for(2026)
    keys = {m.tax_key for m in rules.map}
    assert keys == MAPPED_KEYS
    by = {(m.tax_key, m.tax_kind): (m.base_kz, m.tax_kz) for m in rules.map}
    assert by[("S19", "tax")] == ("81", None)
    assert by[("S7", "tax")] == ("86", None)
    assert by[("SX", "tax")] == ("35", "36")
    assert by[("EU_SVC", "base_only")] == ("21", None)
    assert by[("RC_EU_SVC", "tax")] == ("46", "47")
    assert by[("RC_EU_SVC", "input")] == (None, "67")
    assert by[("RC_3C_SVC", "tax")] == ("84", "85")
    assert by[("RC_3C_SVC", "input")] == (None, "67")
    assert by[("VST", "input")] == (None, "66")


def test_map_refers_to_existing_boxes():
    rules = vat_rules_for(2026)
    kzs = {b.kz for b in rules.boxes}
    for m in rules.map:
        assert m.base_kz is None or m.base_kz in kzs
        assert m.tax_kz is None or m.tax_kz in kzs


def test_form_rules():
    rules = vat_rules_for(2026)
    assert rules.form_year == 2026
    assert rules.status == "to_verify"
    assert rules.source
    assert rules.base_rounding == "truncate_toward_zero"
    assert rules.result.kz == "83"
    assert rules.result.plus == ("81t", "86t", "36", "47", "85")
    assert rules.result.minus == ("66", "67", "39")
    assert rules.klein_limit_prev == Decimal("25000")
    assert rules.klein_limit_cur == Decimal("100000")
    assert rules.input_vat_timing == "invoice_date"
    assert rules.warning is None


def test_rules_are_frozen():
    rules = vat_rules_for(2026)
    with pytest.raises(AttributeError):
        rules.status = "verified"  # type: ignore[misc]


def test_fallback_to_nearest_older_year_warns():
    rules = vat_rules_for(2031)
    assert rules.form_year == 2026
    assert rules.requested_year == 2031
    assert rules.warning == "No VAT rules for 2031; using 2026."
    assert vat_rules_for(2026).warning is None


def test_year_before_first_uses_oldest():
    rules = vat_rules_for(1999)
    assert rules.form_year == available_years()[0]
    assert rules.warning


def test_missing_tables_raise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("permita38.core.vat_rules.available_years", lambda: ())
    with pytest.raises(VatRulesMissing):
        vat_rules_for(2026)
