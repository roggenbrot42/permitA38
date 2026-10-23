"""Statutory retention periods (versioned data file) and the arithmetic around them."""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_FILE = Path(__file__).with_name("retention_rules.json")
CLASS_ORDER = ("books", "vouchers", "letters", "operational")


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def years_for(retention_class: str) -> int:
    return int(load_rules()["classes"][retention_class]["years"])


def retention_end(retention_class: str, reference: date) -> date | None:
    """Last day of the retention period, or None if the class has none.

    The period starts at the end of the calendar year of `reference` (§147 Abs. 4 AO), so a voucher dated 2026-03-14
    with 8 years ends on 2034-12-31."""
    years = years_for(retention_class)
    if years <= 0:
        return None
    return date(reference.year + years, 12, 31)


def longest(classes: list[str], reference: date) -> date | None:
    ends = [e for c in classes if (e := retention_end(c, reference))]
    return max(ends) if ends else None


def calendar() -> list[dict[str, Any]]:
    """The retention table shown in the UI."""
    r = load_rules()
    return [
        {"class": c, "years": v["years"], "label": v["label"], "applies_to": v["applies_to"]}
        for c, v in r["classes"].items()
        if c != "operational"
    ]


def review_status(today: date | None = None) -> dict[str, Any]:
    r = load_rules()
    today = today or date.today()
    due = date.fromisoformat(r["next_review_due"])
    return {
        "reviewed_on": r["reviewed_on"],
        "next_review_due": r["next_review_due"],
        "overdue": today > due,
        "verified_against_official_source": r.get("verified_against_official_source", False),
        "note": r["note"],
    }
