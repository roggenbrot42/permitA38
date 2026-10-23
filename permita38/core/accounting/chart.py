"""Charts of accounts (SKR03 / SKR04) shipped as JSON data files and the role -> account lookup."""

import json
from functools import lru_cache
from pathlib import Path

CHARTS_DIR = Path(__file__).parent / "charts"
CHARTS = ("skr03", "skr04")

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
SIDE_OF_TYPE = {"asset": "debit", "expense": "debit", "liability": "credit", "equity": "credit", "income": "credit"}

#: Roles the automatic postings need; every shipped chart must provide all of them.
REQUIRED_ROLES = (
    "bank", "cash", "receivables", "payables", "vat_in_7", "vat_in_19", "vat_out_7", "vat_out_19",
    "vat_out_7_pending", "vat_out_19_pending", "revenue_0", "revenue_7", "revenue_19", "revenue_klein",
    "expense_software", "expense_hardware", "expense_office", "expense_travel", "expense_marketing",
    "expense_insurance", "expense_phone", "expense_training", "expense_subcontractors", "expense_fees",
    "expense_other", "expense_nondeductible", "private_withdrawal", "private_deposit", "equity_capital",
    "opening_balance",
)  # fmt: skip


@lru_cache(maxsize=4)
def _load(name: str) -> str:
    return (CHARTS_DIR / f"{name}.json").read_text(encoding="utf-8")


def load_chart(name: str) -> dict:
    """The chart definition (a fresh copy). Raises ValueError for an unknown chart."""
    if name not in CHARTS:
        raise ValueError(f"unknown chart {name!r} (available: {', '.join(CHARTS)})")
    chart = json.loads(_load(name))
    validate_chart(chart)
    return chart


def validate_chart(chart: dict) -> None:
    numbers = [a["number"] for a in chart["accounts"]]
    if len(numbers) != len(set(numbers)):
        raise ValueError("duplicate account numbers")
    roles = [a["role"] for a in chart["accounts"] if a.get("role")]
    if len(roles) != len(set(roles)):
        raise ValueError("duplicate account roles")
    missing = set(REQUIRED_ROLES) - set(roles)
    if missing:
        raise ValueError(f"chart lacks roles: {sorted(missing)}")
    for a in chart["accounts"]:
        if a["type"] not in ACCOUNT_TYPES or a["side"] != SIDE_OF_TYPE[a["type"]]:
            raise ValueError(f"bad type/side for account {a['number']}")


def role_map(accounts: list[dict]) -> dict[str, str]:
    """role -> account number over the ACTIVE accounts (a custom account with a role replaces a deactivated one)."""
    out: dict[str, str] = {}
    for a in accounts:
        if a.get("role") and a.get("active", True) and a["role"] not in out:
            out[a["role"]] = a["number"]
    return out
