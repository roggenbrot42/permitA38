"""VAT rule tables: boxes of docs/specs/ustva.md and the tax-key map of docs/specs/accounting.md section 6."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import vat_rules
from permita38.core.vat_rules import VatRulesInvalid, VatRulesMissing, available_years, vat_rules_for

# kz -> (kind, tax_kz, computed_tax_rate)
BOX_TABLE = {
    "81": ("base", None, Decimal("19")),
    "86": ("base", None, Decimal("7")),
    "35": ("base", "36", None),
    "36": ("tax_entered", None, None),
    "21": ("base", None, None),
    "46": ("base", "47", None),
    "47": ("tax_entered", None, None),
    "84": ("base", "85", None),
    "85": ("tax_entered", None, None),
    "66": ("amount", None, None),
    "67": ("amount", None, None),
    "39": ("amount", None, None),
    "83": ("result", None, None),
    "10": ("flag", None, None),
}
MAP_ROWS = {
    ("S19", "tax", "81", None),
    ("S7", "tax", "86", None),
    ("SX", "tax", "35", "36"),
    ("EU_SVC", "base_only", "21", None),
    ("RC_EU_SVC", "tax", "46", "47"),
    ("RC_EU_SVC", "input", None, "67"),
    ("RC_3C_SVC", "tax", "84", "85"),
    ("RC_3C_SVC", "input", None, "67"),
    ("VST", "input", None, "66"),
}

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


def test_exact_box_table():
    rules = vat_rules_for(2026)
    assert len(rules.boxes) == len(BOX_TABLE)
    assert {b.kz: (b.kind, b.tax_kz, b.computed_tax_rate) for b in rules.boxes} == BOX_TABLE


def test_exact_map_rows():
    rules = vat_rules_for(2026)
    assert len(rules.map) == 9
    assert {(m.tax_key, m.tax_kind, m.base_kz, m.tax_kz) for m in rules.map} == MAP_ROWS


def test_result_tokens_refer_to_boxes():
    rules = vat_rules_for(2026)
    boxes = {b.kz: b for b in rules.boxes}
    for token in (*rules.result.plus, *rules.result.minus):
        if token in ("81t", "86t"):
            assert token[:-1] in boxes
            assert boxes[token[:-1]].computed_tax_rate is not None
        else:
            assert token in boxes


def _write_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate, name: str = "2026.json") -> None:
    raw = json.loads(Path(vat_rules.__file__).with_name("2026.json").read_text(encoding="utf-8"))
    mutate(raw)
    (tmp_path / name).write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(vat_rules, "_DIR", tmp_path)
    vat_rules._load.cache_clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    vat_rules._load.cache_clear()


def test_valid_copy_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: None)
    assert vat_rules_for(2026).form_year == 2026


def test_form_year_must_match_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: raw.update(form_year=2025))
    with pytest.raises(VatRulesInvalid, match="form_year 2025 differs"):
        vat_rules_for(2026)


def test_missing_top_level_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: raw.pop("klein_limit_cur"))
    with pytest.raises(VatRulesInvalid, match="klein_limit_cur"):
        vat_rules_for(2026)


def test_missing_box_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: raw["boxes"][0].pop("computed_tax_rate"))
    with pytest.raises(VatRulesInvalid, match="computed_tax_rate"):
        vat_rules_for(2026)


def test_map_row_with_unknown_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: raw["map"][0].update(base_kz="99"))
    with pytest.raises(VatRulesInvalid, match="unknown box 99"):
        vat_rules_for(2026)


def test_result_token_with_unknown_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_variant(tmp_path, monkeypatch, lambda raw: raw["result"]["plus"].append("77"))
    with pytest.raises(VatRulesInvalid, match="token 77"):
        vat_rules_for(2026)


def test_invalid_is_a_missing_error():
    assert issubclass(VatRulesInvalid, VatRulesMissing)
