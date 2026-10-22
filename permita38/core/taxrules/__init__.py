"""Versioned tax rules as data files, with a review clock.

German tax rules change every year (and sometimes mid-year). Everything that depends on them - VAT rates and limits,
filing deadlines, the boxes (*Kennziffern*) of the VAT return, the income tax tariff - is documented in a JSON file in
``data/``. Every file carries:

``valid_for_year``  the tax year the values are written for
``reviewed_on``     the day a maintainer last compared the file with the official sources
``next_review_due`` normally three months after ``reviewed_on`` (the quarterly review, see docs/tax-rules-review.md)
``sources``         where the values come from (``kind``: ``primary`` = law/BMF, ``secondary`` = professional sites)
``verification``    on each value: ``secondary`` (confirmed by >= 2 independent secondary sources) or ``unverified``

:func:`status` turns those dates into the banner level shown to the boss in the web UI:
``ok`` (not due), ``info`` (1-90 days overdue), ``warning`` (91-180 days), ``strong`` (more than 180 days).
A test fails when a file is older than 200 days, so a review cannot be forgotten silently.
"""

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
LEVELS = ("ok", "info", "warning", "strong")
_REQUIRED = ("id", "title", "valid_for_year", "reviewed_on", "next_review_due", "sources")


@dataclass(frozen=True)
class RulesFile:
    id: str
    data: dict[str, Any]

    @property
    def valid_for_year(self) -> int:
        return int(self.data["valid_for_year"])

    @property
    def reviewed_on(self) -> date:
        return date.fromisoformat(self.data["reviewed_on"])

    @property
    def next_review_due(self) -> date:
        return date.fromisoformat(self.data["next_review_due"])


def _parse(path: Path) -> RulesFile:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ValueError(f"{path.name}: missing required keys {missing}")
    rf = RulesFile(data["id"], data)
    rf.reviewed_on, rf.next_review_due  # noqa: B018 - parse dates now so a typo fails at load time
    if rf.next_review_due <= rf.reviewed_on:
        raise ValueError(f"{path.name}: next_review_due must be after reviewed_on")
    return rf


@lru_cache(maxsize=None)
def load(name: str) -> RulesFile:
    return _parse(DATA_DIR / f"{name}.json")


def list_rules() -> list[RulesFile]:
    return [load(p.stem) for p in sorted(DATA_DIR.glob("*.json"))]


def vat_rules() -> dict[str, Any]:
    return load("vat").data


def count_unverified(node: Any) -> int:
    """Number of values flagged ``"verification": "unverified"`` anywhere inside the structure."""
    if isinstance(node, dict):
        own = 1 if node.get("verification") == "unverified" else 0
        return own + sum(count_unverified(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_unverified(v) for v in node)
    return 0


def level_for(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "ok"
    if days_overdue <= 90:
        return "info"
    if days_overdue <= 180:
        return "warning"
    return "strong"


def status(today: date | None = None) -> dict[str, Any]:
    """Review state of every rules file plus the worst level (what the UI banner shows)."""
    today = today or date.today()
    files = []
    for rf in list_rules():
        overdue = (today - rf.next_review_due).days
        files.append(
            {
                "id": rf.id,
                "title": rf.data["title"],
                "valid_for_year": rf.valid_for_year,
                "reviewed_on": rf.reviewed_on.isoformat(),
                "next_review_due": rf.next_review_due.isoformat(),
                "age_days": (today - rf.reviewed_on).days,
                "days_overdue": max(overdue, 0),
                "days_until_due": max(-overdue, 0),
                "level": level_for(overdue),
                "unverified": count_unverified(rf.data),
            }
        )
    worst = max((f["level"] for f in files), key=LEVELS.index, default="ok")
    return {
        "today": today.isoformat(),
        "level": worst,
        "files": files,
        "unverified": sum(f["unverified"] for f in files),
    }
