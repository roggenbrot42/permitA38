"""Response-boundary conversion of money in dict endpoints (docs/specs/decimal-money-api.md, rule 4).

The builders (``calc.finance``, the tax estimate, the procurement and planner views) keep their numeric types for
their internal callers; the routers convert the money keys of the finished dict here, with an explicit key set. Money
becomes ``money.to_decimal_str``; ``None``, strings, bools and every key outside the set stay as they are.
"""

from collections.abc import Collection
from decimal import Decimal
from typing import Any

from permita38.core import money

Path = tuple[str, ...]


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def money_strings(
    obj: Any,
    keys: Collection[str],
    *,
    exclude_paths: Collection[Path] = (),
    _path: Path = (),
) -> Any:
    """A copy of ``obj`` (nested dicts and lists) where every number at a dict key in ``keys`` is a decimal string.

    ``exclude_paths`` lists exact key paths (list levels are transparent, e.g. ``("tax_actual", "rate")``) that stay
    numbers although their key name is in ``keys``. Dicts and lists are searched further.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            path = (*_path, k)
            if isinstance(v, dict | list):
                out[k] = money_strings(v, keys, exclude_paths=exclude_paths, _path=path)
            elif k in keys and path not in exclude_paths and _is_number(v):
                out[k] = money.to_decimal_str(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [money_strings(v, keys, exclude_paths=exclude_paths, _path=_path) for v in obj]
    return obj


def all_money_strings(obj: Any, *, except_keys: Collection[str] = ()) -> Any:
    """Like ``money_strings`` but every number leaf is money except the dict keys in ``except_keys`` (ratios,
    counts). For dicts that are money throughout, e.g. the tax estimate or the category totals."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, dict | list):
                out[k] = all_money_strings(v, except_keys=except_keys)
            elif k not in except_keys and _is_number(v):
                out[k] = money.to_decimal_str(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [all_money_strings(v, except_keys=except_keys) for v in obj]
    return obj


# GET /finance -------------------------------------------------------------------------------------------------------
FINANCE_MONTH_KEYS = frozenset(
    {
        "revenue",
        "vat_out",
        "expenses",
        "vat_in",
        "profit",
        "vat_due",
        "tax_set_aside",
        "vat_set_aside",
        "social",
        "take_home",
    }
)
FINANCE_TOP_KEYS = frozenset({"projected_profit", "social_year", "take_home", "open_receivables"})
# tax_actual / tax_projected / tax_owed / reserve: every number is money except these ratios and counts
TAX_NON_MONEY_KEYS = frozenset({"rate", "effective_rate", "months_left", "kfb_used"})
TAX_BLOCKS = ("tax_actual", "tax_projected", "tax_owed", "reserve")


def finance_response(result: dict[str, Any]) -> dict[str, Any]:
    """The /finance dict (full or redacted) with its money as decimal strings."""
    out = dict(result)
    if "months" in out:
        out["months"] = money_strings(out["months"], FINANCE_MONTH_KEYS)
    if isinstance(out.get("totals"), dict):
        out["totals"] = all_money_strings(out["totals"])
    if isinstance(out.get("categories"), dict):
        out["categories"] = all_money_strings(out["categories"])
    for k in FINANCE_TOP_KEYS:
        if k in out and _is_number(out[k]):
            out[k] = money.to_decimal_str(out[k])
    for k in TAX_BLOCKS:
        if isinstance(out.get(k), dict):
            out[k] = all_money_strings(out[k], except_keys=TAX_NON_MONEY_KEYS)
    return out


# GET /project-report ------------------------------------------------------------------------------------------------
PROJECT_REPORT_KEYS = frozenset({"revenue", "cost", "expenses", "margin", "unbilled_revenue", "committed_external_cost"})
# not hours, unbilled_hours


def project_report_response(rows: dict[str, Any]) -> dict[str, Any]:
    return {pid: money_strings(row, PROJECT_REPORT_KEYS) for pid, row in rows.items()}


# GET /finance/commitments -------------------------------------------------------------------------------------------
COMMITMENT_KEYS = frozenset({"planned_net", "invoiced_net", "open_net", "revenue_customer", "revenue_total"})
# not planned_hours, share_percent


def commitments_response(result: dict[str, Any]) -> dict[str, Any]:
    return money_strings(result, COMMITMENT_KEYS)


# GET /plan ----------------------------------------------------------------------------------------------------------
PLAN_KEYS = frozenset(
    {"rate", "value", "invoiced", "open", "expected_value", "planned_subcontractor_cost", "contract_value", "margin"}
)  # not promised, booked, remaining, covered, shortfall, win_probability


def plan_response(result: dict[str, Any]) -> dict[str, Any]:
    return money_strings(result, PLAN_KEYS)
