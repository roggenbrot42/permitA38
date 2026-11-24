"""DATEV export key data (docs/specs/datev-export.md, "File" and "From journal entries to rows").

One data file per year (``<year>.json``). :func:`datev_rules_for` returns a frozen, fully typed :class:`DatevRules`;
without a table for the year it uses the nearest older (else the oldest) table and sets ``warning``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Literal, cast

from permita38.core.vat_rules import TAX_KEYS

_DIR = resources.files(__package__)

RulesStatus = Literal["to_verify", "verified"]


class DatevRulesMissing(ValueError):
    """No DATEV rule table exists at all."""


class DatevRulesInvalid(DatevRulesMissing):
    """A rule table file is malformed (missing field, unknown tax key, non-string key)."""


_TOP_KEYS = ("year", "source", "status", "header", "cancel_automatic_key", "bu_keys")
_HEADER_KEYS = ("extf_version", "category", "category_name", "format_version")


def _rate_key(rate: Decimal) -> str:
    return format(rate.normalize(), "f")


def _check_str(value: object, where: str, name: str) -> None:
    if not isinstance(value, str):
        raise DatevRulesInvalid(f"{name}: {where} must be a string, not {value!r}")


def _validate(raw: object, year: int) -> None:
    name = f"{year}.json"
    if not isinstance(raw, dict):
        raise DatevRulesInvalid(f"{name}: file must be an object")
    for key in _TOP_KEYS:
        if key not in raw:
            raise DatevRulesInvalid(f"{name}: file lacks required key '{key}'")
    if isinstance(raw["year"], bool) or not isinstance(raw["year"], int):
        raise DatevRulesInvalid(f"{name}: year must be an integer")
    if raw["year"] != year:
        raise DatevRulesInvalid(f"{name}: year {raw['year']} differs from the file name year {year}")
    header = raw["header"]
    if not isinstance(header, dict):
        raise DatevRulesInvalid(f"{name}: header must be an object")
    for key in _HEADER_KEYS:
        if key not in header:
            raise DatevRulesInvalid(f"{name}: header lacks required field '{key}'")
    for key in ("extf_version", "category", "format_version"):
        if not isinstance(header[key], int) or isinstance(header[key], bool):
            raise DatevRulesInvalid(f"{name}: header.{key} must be an integer")
    if raw["status"] not in ("to_verify", "verified"):
        raise DatevRulesInvalid(f"{name}: status must be 'to_verify' or 'verified', not {raw['status']!r}")
    _check_str(raw["source"], "source", name)
    _check_str(header["category_name"], "header.category_name", name)
    _check_str(raw["cancel_automatic_key"], "cancel_automatic_key", name)
    keys = raw["bu_keys"]
    if not isinstance(keys, dict):
        raise DatevRulesInvalid(f"{name}: bu_keys must be an object")
    for tax_key in TAX_KEYS:
        if tax_key not in keys:
            raise DatevRulesInvalid(f"{name}: bu_keys lacks tax key '{tax_key}'")
    for tax_key, value in keys.items():
        if tax_key not in TAX_KEYS:
            raise DatevRulesInvalid(f"{name}: unknown tax key '{tax_key}'")
        if isinstance(value, dict):
            for rate, key in value.items():
                try:
                    finite = Decimal(rate).is_finite()
                except (InvalidOperation, TypeError):
                    finite = False
                if not finite:
                    raise DatevRulesInvalid(f"{name}: bu_keys.{tax_key}: rate '{rate}' is not a number")
                _check_str(key, f"bu_keys.{tax_key}[{rate}]", name)
        elif value is not None:
            _check_str(value, f"bu_keys.{tax_key}", name)


@dataclass(frozen=True)
class ExtfHeader:
    extf_version: int
    category: int
    category_name: str
    format_version: int


@dataclass(frozen=True)
class DatevRules:
    """The rules of one year. ``year`` is the table used, ``requested_year`` the year asked for."""

    year: int
    requested_year: int
    warning: str | None
    source: str
    status: RulesStatus
    header: ExtfHeader
    cancel_automatic_key: str
    # tax key -> BU key (None: no key) or, for rate dependent keys, rate ("19", "7") -> BU key; read-only
    bu_keys: Mapping[str, str | None | Mapping[str, str]]

    def bu_key(self, tax_key: str, rate: Decimal | None = None) -> str | None:
        """BU key for a tax key (and rate for rate dependent keys); None when there is none."""
        value = self.bu_keys.get(tax_key)
        if isinstance(value, Mapping):
            if rate is None:
                return None
            if not rate.is_finite():
                raise ValueError(f"rate must be finite, not {rate}")
            return value.get(_rate_key(rate))
        return value


def available_years() -> tuple[int, ...]:
    """Years that have a table, ascending."""
    years = [int(p.name[:-5]) for p in _DIR.iterdir() if p.name.endswith(".json") and p.name[:-5].isdigit()]
    return tuple(sorted(years))


@lru_cache(maxsize=None)
def _load(year: int) -> DatevRules:
    raw = json.loads(_DIR.joinpath(f"{year}.json").read_text(encoding="utf-8"))
    _validate(raw, year)
    h = raw["header"]
    return DatevRules(
        year=int(raw["year"]),
        requested_year=year,
        warning=None,
        source=raw["source"],
        status=cast(RulesStatus, raw["status"]),
        header=ExtfHeader(
            extf_version=h["extf_version"],
            category=h["category"],
            category_name=h["category_name"],
            format_version=h["format_version"],
        ),
        cancel_automatic_key=raw["cancel_automatic_key"],
        bu_keys=MappingProxyType(
            {k: MappingProxyType(dict(v)) if isinstance(v, dict) else v for k, v in raw["bu_keys"].items()}
        ),
    )


def datev_rules_for(year: int) -> DatevRules:
    """Rules for ``year``; the nearest older (else the oldest) table with a warning when there is none."""
    years = available_years()
    if not years:
        raise DatevRulesMissing("No DATEV rule tables")
    if year in years:
        used, warning = year, None
    else:
        older = [y for y in years if y < year]
        used = older[-1] if older else years[0]
        warning = f"No DATEV rules for {year}; using {used}."
    return replace(_load(used), requested_year=year, warning=warning)
