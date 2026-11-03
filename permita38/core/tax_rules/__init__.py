"""Tax and social-insurance rule tables (docs/specs/tax-burden-and-settings.md, "Rule tables").

One data file per year (``<year>.json``). Every value is ``{"value": "<decimal string>", "source", "valid_from"}``
with an optional ``"status": "to_verify"`` and ``"note"``. :func:`rules_for` returns a frozen :class:`TaxRules` of
Decimals; without a table for the year it uses the nearest older (else the oldest) table and sets ``warning``.
"""

import json
from dataclasses import dataclass, fields
from decimal import Decimal
from functools import lru_cache
from importlib import resources

_DIR = resources.files(__package__)


class RulesMissing(ValueError):
    """No rule table exists at all."""


@dataclass(frozen=True)
class TaxRules:
    """The rules of one year. ``year`` is the table used, ``requested_year`` the year asked for."""

    year: int
    requested_year: int
    to_verify: tuple[str, ...]
    warning: str | None
    est_gf: Decimal
    est_z2: Decimal
    est_z3: Decimal
    est_z4: Decimal
    est_a2: Decimal
    est_a3: Decimal
    est_c3: Decimal
    est_k4: Decimal
    est_k5: Decimal
    soli_rate: Decimal
    soli_cap_rate: Decimal
    soli_freigrenze: Decimal
    kinderfreibetrag: Decimal
    bea_freibetrag: Decimal
    kindergeld_month: Decimal
    an_pauschbetrag: Decimal
    sa_pauschbetrag: Decimal
    vorsorge_hoechst_altersvorsorge: Decimal
    vorsorge_cap_self: Decimal
    vorsorge_cap_employee: Decimal
    kv_basis_cut_percent: Decimal
    gew_freibetrag: Decimal
    gew_messzahl: Decimal
    gew_credit_factor: Decimal
    kst_rate: Decimal
    bezugsgroesse_month: Decimal
    bbg_kv_month: Decimal
    bbg_rv_month: Decimal
    bbg_rv_year: Decimal
    kv_min_base_month: Decimal
    kv_rate_general: Decimal
    kv_rate_reduced: Decimal
    kv_zusatz_average: Decimal
    pv_rate: Decimal
    pv_childless_surcharge: Decimal
    pv_child_reduction: Decimal
    pv_employer_share: Decimal
    pv_employer_share_sn: Decimal
    rv_rate: Decimal
    rv_min_base_month: Decimal
    alv_rate: Decimal
    alv_voluntary_month: Decimal
    alv_voluntary_half_month: Decimal
    bav_tax_free_percent: Decimal
    bav_sv_free_percent: Decimal


def available_years() -> tuple[int, ...]:
    """Years that have a table, ascending."""
    years = [int(p.name[:-5]) for p in _DIR.iterdir() if p.name.endswith(".json") and p.name[:-5].isdigit()]
    return tuple(sorted(years))


@lru_cache(maxsize=None)
def _load(year: int) -> dict:
    return json.loads(_DIR.joinpath(f"{year}.json").read_text(encoding="utf-8"))


def load_raw(year: int) -> dict:
    """The parsed JSON of ``year`` (value objects with source, valid_from, status, note)."""
    return _load(year)


def rules_for(year: int) -> TaxRules:
    """Rules for ``year``; the nearest older (else the oldest) table with a warning when there is none."""
    years = available_years()
    if not years:
        raise RulesMissing("No tax rule tables")
    if year in years:
        used = year
        warning = None
    else:
        older = [y for y in years if y < year]
        used = older[-1] if older else years[0]
        warning = f"No tax rules for {year}; using {used}."
    values = _load(used)["values"]
    to_verify = tuple(k for k, v in values.items() if v.get("status") == "to_verify")
    decimals = {k: Decimal(v["value"]) for k, v in values.items()}
    return TaxRules(year=used, requested_year=year, to_verify=to_verify, warning=warning, **decimals)


def rule_keys() -> tuple[str, ...]:
    """The value keys every table must contain."""
    return tuple(f.name for f in fields(TaxRules) if f.name not in ("year", "requested_year", "to_verify", "warning"))
