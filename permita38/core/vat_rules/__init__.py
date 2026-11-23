"""VAT return (UStVA) rule tables (docs/specs/ustva.md, "Boxes (Kennzahlen) and the form arithmetic").

One data file per form year (``<year>.json``). :func:`vat_rules_for` returns a frozen, fully typed
:class:`VatRules`; without a table for the year it uses the nearest older (else the oldest) table and sets
``warning``.
"""

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Literal, NewType, cast

_DIR = resources.files(__package__)

TaxKey = NewType("TaxKey", str)
BoxKind = Literal["base", "tax_entered", "amount", "result", "flag"]
TaxKind = Literal["tax", "input", "base_only"]
RoundingMode = Literal["truncate_toward_zero"]
InputVatTiming = Literal["invoice_date"]
RulesStatus = Literal["to_verify", "verified"]


class VatRulesMissing(ValueError):
    """No VAT rule table exists at all."""


class VatRulesInvalid(VatRulesMissing):
    """A rule table file is malformed (wrong year, missing key, unknown box reference)."""


# Stable tax-key vocabulary (accounting.md section 6); the charts module imports it for an account's `automatic`.
TAX_KEYS: frozenset[str] = frozenset({"S19", "S7", "SX", "KU", "EU_SVC", "VST", "RC_EU_SVC", "RC_3C_SVC", "NONE"})

_TOP_KEYS = (
    "form_year",
    "source",
    "status",
    "base_rounding",
    "boxes",
    "map",
    "result",
    "klein_limit_prev",
    "klein_limit_cur",
    "input_vat_timing",
)
_BOX_KEYS = ("kz", "label_de", "label_en", "kind", "tax_kz", "computed_tax_rate")
_MAP_KEYS = ("tax_key", "tax_kind", "base_kz", "tax_kz")
_RESULT_KEYS = ("kz", "plus", "minus")


def _require(obj: object, keys: tuple[str, ...], where: str, name: str) -> None:
    if not isinstance(obj, dict):
        raise VatRulesInvalid(f"{name}: {where} must be an object")
    for key in keys:
        if key not in obj:
            raise VatRulesInvalid(f"{name}: {where} lacks required key '{key}'")


def _validate(raw: object, year: int) -> None:
    name = f"{year}.json"
    _require(raw, _TOP_KEYS, "file", name)
    assert isinstance(raw, dict)
    if int(raw["form_year"]) != year:
        raise VatRulesInvalid(f"{name}: form_year {raw['form_year']} differs from the file name year {year}")
    for key in ("boxes", "map"):
        if not isinstance(raw[key], list):
            raise VatRulesInvalid(f"{name}: '{key}' must be a list")
    for b in raw["boxes"]:
        _require(b, _BOX_KEYS, "box", name)
    for m in raw["map"]:
        _require(m, _MAP_KEYS, "map row", name)
    _require(raw["result"], _RESULT_KEYS, "result", name)
    kzs = {b["kz"] for b in raw["boxes"]}
    for b in raw["boxes"]:
        if b["tax_kz"] is not None and b["tax_kz"] not in kzs:
            raise VatRulesInvalid(f"{name}: box {b['kz']} refers to unknown box {b['tax_kz']}")
    for m in raw["map"]:
        for ref in (m["base_kz"], m["tax_kz"]):
            if ref is not None and ref not in kzs:
                raise VatRulesInvalid(f"{name}: map row {m['tax_key']}/{m['tax_kind']} refers to unknown box {ref}")
    res = raw["result"]
    if res["kz"] not in kzs:
        raise VatRulesInvalid(f"{name}: result box {res['kz']} is unknown")
    for token in (*res["plus"], *res["minus"]):
        kz = token[:-1] if token.endswith("t") else token
        if kz not in kzs:
            raise VatRulesInvalid(f"{name}: result token {token} refers to unknown box {kz}")


@dataclass(frozen=True)
class Box:
    kz: str
    label_de: str
    label_en: str
    kind: BoxKind
    tax_kz: str | None
    computed_tax_rate: Decimal | None


@dataclass(frozen=True)
class MapEntry:
    tax_key: TaxKey
    tax_kind: TaxKind
    base_kz: str | None
    tax_kz: str | None


@dataclass(frozen=True)
class ResultFormula:
    kz: str
    plus: tuple[str, ...]
    minus: tuple[str, ...]


@dataclass(frozen=True)
class VatRules:
    """The rules of one form year. ``form_year`` is the table used, ``requested_year`` the year asked for."""

    form_year: int
    requested_year: int
    warning: str | None
    source: str
    status: RulesStatus
    base_rounding: RoundingMode
    boxes: tuple[Box, ...]
    map: tuple[MapEntry, ...]
    result: ResultFormula
    klein_limit_prev: Decimal
    klein_limit_cur: Decimal
    input_vat_timing: InputVatTiming


def available_years() -> tuple[int, ...]:
    """Years that have a table, ascending."""
    years = [int(p.name[:-5]) for p in _DIR.iterdir() if p.name.endswith(".json") and p.name[:-5].isdigit()]
    return tuple(sorted(years))


@lru_cache(maxsize=None)
def _load(year: int) -> VatRules:
    raw = json.loads(_DIR.joinpath(f"{year}.json").read_text(encoding="utf-8"))
    _validate(raw, year)
    boxes = tuple(
        Box(
            kz=b["kz"],
            label_de=b["label_de"],
            label_en=b["label_en"],
            kind=cast(BoxKind, b["kind"]),
            tax_kz=b["tax_kz"],
            computed_tax_rate=None if b["computed_tax_rate"] is None else Decimal(b["computed_tax_rate"]),
        )
        for b in raw["boxes"]
    )
    entries = tuple(
        MapEntry(
            tax_key=TaxKey(m["tax_key"]),
            tax_kind=cast(TaxKind, m["tax_kind"]),
            base_kz=m["base_kz"],
            tax_kz=m["tax_kz"],
        )
        for m in raw["map"]
    )
    res = raw["result"]
    return VatRules(
        form_year=int(raw["form_year"]),
        requested_year=year,
        warning=None,
        source=raw["source"],
        status=cast(RulesStatus, raw["status"]),
        base_rounding=cast(RoundingMode, raw["base_rounding"]),
        boxes=boxes,
        map=entries,
        result=ResultFormula(kz=res["kz"], plus=tuple(res["plus"]), minus=tuple(res["minus"])),
        klein_limit_prev=Decimal(raw["klein_limit_prev"]),
        klein_limit_cur=Decimal(raw["klein_limit_cur"]),
        input_vat_timing=cast(InputVatTiming, raw["input_vat_timing"]),
    )


def vat_rules_for(year: int) -> VatRules:
    """Rules for ``year``; the nearest older (else the oldest) table with a warning when there is none."""
    years = available_years()
    if not years:
        raise VatRulesMissing("No VAT rule tables")
    if year in years:
        used, warning = year, None
    else:
        older = [y for y in years if y < year]
        used = older[-1] if older else years[0]
        warning = f"No VAT rules for {year}; using {used}."
    return replace(_load(used), requested_year=year, warning=warning)
