"""Rotation (grandfather-father-son) for operational backups and protection of legal archives.

Scheduled backups are labelled ``daily`` and, when due, also ``weekly`` and/or ``monthly`` (one archive, several
labels). Rotation keeps the newest N archives per label; an archive is deleted only when no label keeps it.
Yearly *closing* archives are legal archives: they are never rotated and are protected until `retention_until`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

LABELS = ("daily", "weekly", "monthly")


@dataclass(frozen=True)
class Policy:
    keep_daily: int = 14
    keep_weekly: int = 8
    keep_monthly: int = 24
    keep_manual: int = 20

    def keep(self, label: str) -> int:
        return {
            "daily": self.keep_daily,
            "weekly": self.keep_weekly,
            "monthly": self.keep_monthly,
            "manual": self.keep_manual,
        }[label]


def kind_labels(kind: str) -> list[str]:
    return [p for p in (kind or "").split("+") if p]


def is_protected(run: dict[str, Any], today: date) -> bool:
    """Legal archives are protected until the end of their retention period."""
    if run.get("kind") != "yearly":
        return False
    until = run.get("retention_until")
    return not until or date.fromisoformat(until) >= today


def select_prunable(runs: list[dict[str, Any]], policy: Policy, today: date) -> list[dict[str, Any]]:
    """Runs (one target, status ok) that may be deleted. Newest first by `started`."""
    runs = sorted(runs, key=lambda r: (r.get("started") or 0, r.get("id") or ""), reverse=True)
    keep: set[str] = set()
    for label in (*LABELS, "manual"):
        n = 0
        for r in runs:
            if label in kind_labels(r.get("kind", "")):
                n += 1
                if n <= policy.keep(label):
                    keep.add(r["id"])
    return [
        r
        for r in runs
        if r["id"] not in keep
        and r.get("kind") != "yearly"  # legal archives are never rotated automatically
        and not is_protected(r, today)
        and not r.get("protected")
    ]


def labels_for(now_date: date, previous: list[dict[str, Any]], weekly_day: int, monthly_day: int) -> list[str]:
    """Labels for the archive created at `now_date`, given the earlier successful scheduled archives.

    `previous` rows carry `_date` (the day they were made). Catch-up friendly: the very first backup starts every
    generation; `weekly` is added on the weekly day (at least 6 days after the last weekly one) or, when that day was
    missed, as soon as 8 days have passed; `monthly` when the calendar month has none yet and the monthly day has
    been reached."""
    labels = ["daily"]
    weekly_dates = [r["_date"] for r in previous if "weekly" in kind_labels(r.get("kind", "")) and r.get("_date")]
    last_weekly = max(weekly_dates, default=None)
    gap = (now_date - last_weekly).days if last_weekly else None
    if gap is None or (now_date.weekday() == weekly_day and gap >= 6) or gap >= 8:
        labels.append("weekly")
    in_month = [
        r
        for r in previous
        if "monthly" in kind_labels(r.get("kind", ""))
        and r.get("_date")
        and (r["_date"].year, r["_date"].month) == (now_date.year, now_date.month)
    ]
    if not in_month and (now_date.day >= monthly_day or not previous):
        labels.append("monthly")
    return labels
