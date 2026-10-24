"""Rate tables for business trips (docs/specs/business-trips.md, "Rate tables").

One data file per year (``<year>.json``): domestic rates, the mileage rate, the country table and the source of the
BMF letter. Money values are decimal strings, read with :func:`permita38.core.money.dec` (never floats). The checklist
of usual trip expenses lives in ``checklist.json`` so the maintainer can add items without code changes.
"""

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from ..money import dec

_DIR = resources.files(__package__)


class RateTableMissing(ValueError):
    """No rate table exists for the year of a trip day."""


def _money_fields(rows: dict) -> dict:
    return {k: dec(v, default=None) for k, v in rows.items()}


@lru_cache(maxsize=None)
def load_rates(year: int) -> dict[str, Any]:
    """The rate table of ``year`` with Decimal values. Raises :class:`RateTableMissing` when there is none."""
    path = _DIR.joinpath(f"{year}.json")
    if not path.is_file():
        raise RateTableMissing(f"No rate table for year {year}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    countries = {}
    for code, row in raw["countries"].items():
        countries[code] = {
            "name": row["name"],
            "from8h": dec(row["from8h"], default=None),
            "h24": dec(row["h24"], default=None),
            "placeholder": "status" in row,
        }
    return {
        "year": int(raw["year"]),
        "rules_version": raw["rules_version"],
        "source": raw["source"],
        "valid_from": raw["valid_from"],
        "mileage_per_km": dec(raw["mileage_per_km"], default=None),
        "fallback_country": raw["fallback_country"],
        "domestic": _money_fields(raw["domestic"]),
        "countries": countries,
    }


@lru_cache(maxsize=1)
def load_checklist() -> tuple[dict[str, str], ...]:
    """The reminder checklist ("Usual expenses of a trip"): ``({id, label, hint}, ...)`` in display order."""
    items = json.loads(_DIR.joinpath("checklist.json").read_text(encoding="utf-8"))
    return tuple({"id": i["id"], "label": i["label"], "hint": i["hint"]} for i in items)
