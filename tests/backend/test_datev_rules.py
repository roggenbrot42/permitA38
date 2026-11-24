"""DATEV key data (docs/specs/datev-export.md): literal values, per-rate VAT keys, loader errors."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import datev
from permita38.core.datev import DatevRulesInvalid, datev_rules_for


@pytest.fixture(autouse=True)
def _clear_cache():
    datev._load.cache_clear()
    yield
    datev._load.cache_clear()


def test_literal_values():
    r = datev_rules_for(2026)
    assert (r.year, r.status) == (2026, "to_verify")
    assert "DATEV test import" in r.source
    assert (r.header.extf_version, r.header.category, r.header.category_name, r.header.format_version) == (
        700,
        21,
        "Buchungsstapel",
        13,
    )
    assert r.cancel_automatic_key == "40"
    assert r.bu_key("S19") == "3"
    assert r.bu_key("S7") == "2"


def test_vst_key_depends_on_rate():
    r = datev_rules_for(2026)
    assert r.bu_key("VST", Decimal("19")) == "9"
    assert r.bu_key("VST", Decimal("19.00")) == "9"
    assert r.bu_key("VST", Decimal("7")) == "8"
    assert r.bu_key("VST", Decimal("7.0")) == "8"
    assert r.bu_key("VST", Decimal("5")) is None
    assert r.bu_key("VST") is None
    assert datev.bu_key("VST", Decimal("7")) == "8"


@pytest.mark.parametrize("tax_key", ["SX", "EU_SVC", "RC_EU_SVC", "RC_3C_SVC", "KU", "NONE"])
def test_null_keys(tax_key: str):
    assert datev_rules_for(2026).bu_key(tax_key, Decimal("19")) is None


def test_fallback_warns():
    r = datev_rules_for(2031)
    assert (r.year, r.requested_year) == (2026, 2031)
    assert r.warning == "No DATEV rules for 2031; using 2026."
    assert datev_rules_for(2026).warning is None
    assert datev_rules_for(2000).warning == "No DATEV rules for 2000; using 2026."


def test_missing_tables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datev, "available_years", lambda: ())
    with pytest.raises(datev.DatevRulesMissing):
        datev_rules_for(2026)


def _variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate) -> None:
    raw = json.loads(Path(datev.__file__).with_name("2026.json").read_text(encoding="utf-8"))
    mutate(raw)
    (tmp_path / "2026.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(datev, "_DIR", tmp_path)


def test_valid_copy_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: None)
    assert datev_rules_for(2026).bu_key("S19") == "3"


def test_unknown_tax_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw["bu_keys"].update(BOGUS="1"))
    with pytest.raises(DatevRulesInvalid, match="unknown tax key 'BOGUS'"):
        datev_rules_for(2026)


def test_missing_header_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw["header"].pop("format_version"))
    with pytest.raises(DatevRulesInvalid, match="format_version"):
        datev_rules_for(2026)


def test_missing_top_level_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw.pop("cancel_automatic_key"))
    with pytest.raises(DatevRulesInvalid, match="cancel_automatic_key"):
        datev_rules_for(2026)


def test_non_string_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw["bu_keys"].update(S19=3))
    with pytest.raises(DatevRulesInvalid, match="must be a string"):
        datev_rules_for(2026)


def test_non_string_rate_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw["bu_keys"]["VST"].update({"19": 9}))
    with pytest.raises(DatevRulesInvalid, match="must be a string"):
        datev_rules_for(2026)


def test_non_string_cancel_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw.update(cancel_automatic_key=40))
    with pytest.raises(DatevRulesInvalid, match="cancel_automatic_key"):
        datev_rules_for(2026)


def test_year_must_match_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _variant(tmp_path, monkeypatch, lambda raw: raw.update(year=2025))
    with pytest.raises(DatevRulesInvalid, match="differs from the file name year"):
        datev_rules_for(2026)
