"""Charts of accounts (docs/specs/accounting.md section 5).

One data file per chart and year (``<chart>-<year>.json``). :func:`chart_for` returns a frozen, fully typed
:class:`Chart`; without a file for the year it uses the nearest older (else the oldest) one and sets ``warning``.
"""

import json
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Literal, NewType, cast

from permita38.core.vat_rules import TAX_KEYS

_DIR = resources.files(__package__)

Role = NewType("Role", str)
AccountNo = NewType("AccountNo", str)
ChartName = Literal["skr03", "skr04"]
AccountClass = Literal["pl", "balance"]
ChartStatus = Literal["to_verify", "verified"]

CHART_NAMES: tuple[ChartName, ...] = ("skr03", "skr04")

# Section 5, "Roles used by the engine". The ``expense_*`` category roles are not enumerated by the spec; a chart may
# add further ``expense_*`` roles (see EXPENSE_ROLE_PREFIX) for its category table.
ENGINE_ROLES: tuple[Role, ...] = tuple(
    Role(r)
    for r in (
        # money
        "bank",
        "cash",
        # open items
        "receivables",
        "payables",
        "customer_credit",
        "payables_employee",
        # revenue
        "revenue_s19",
        "revenue_s7",
        "revenue_sx",
        "revenue_s0",
        "revenue_ku",
        "revenue_eu_svc",
        # VAT
        "vat_out_s19",
        "vat_out_s7",
        "vat_out_sx",
        "vat_not_due_s19",
        "vat_not_due_s7",
        "vat_not_due_sx",
        "vat_soll_clearing_s19",
        "vat_soll_clearing_s7",
        "vat_soll_clearing_sx",
        "input_vat_s19",
        "input_vat_s7",
        "input_vat_sx",
        "rc_vat_out_eu",
        "rc_vat_out_3c",
        "rc_input_vat",
        "vat_prepayments",
        "vat_special_prepayment",
        "vat_previous_year",
        # purchase clearing
        "input_vat_clearing",
        "rc_vat_nondeductible",
        "vat_paid_private",
        # export
        "datev_transfer",
        # cost
        "expense_software",
        "meals_deductible",
        "meals_nondeductible",
        "travel_costs",
        "expense_other",
        "bad_debts",
        "cash_discount_granted",
        # taxes
        "trade_tax_expense",
        "trade_tax_provision",
        "corporate_tax_expense",
        "corporate_tax_provision",
        "tax_refunds",
        # owner
        "private_withdrawal",
        "private_deposit",
        "equity",
        "opening_balance",
        "closing_balance",
        "profit_carryforward",
    )
)
EXPENSE_ROLE_PREFIX = "expense_"
DEFAULT_CATEGORY_ROLE = Role("expense_other")


class ChartMissing(ValueError):
    """No chart file exists for the requested chart."""


class ChartInvalid(ValueError):
    """A chart file is malformed or breaks a rule of accounting.md section 5."""


@dataclass(frozen=True)
class EurLine:
    line: str
    deductible: bool


@dataclass(frozen=True)
class ChartAccount:
    account: AccountNo
    name: str
    account_class: AccountClass
    eur_line: EurLine | None
    automatic: str | None


@dataclass(frozen=True)
class Chart:
    """One chart file. ``year`` is the file used, ``requested_year`` the year asked for."""

    chart: ChartName
    year: int
    requested_year: int
    warning: str | None
    source: str
    status: ChartStatus
    roles: tuple[tuple[Role, ChartAccount], ...]
    categories: tuple[tuple[str, Role], ...]
    accounts: tuple[ChartAccount, ...]

    def account(self, role: Role) -> AccountNo:
        for r, acc in self.roles:
            if r == role:
                return acc.account
        raise KeyError(f"{self.chart}-{self.year}: unknown role '{role}'")

    def role_for_category(self, category: str) -> Role:
        for c, role in self.categories:
            if c == category:
                return role
        return DEFAULT_CATEGORY_ROLE

    def eur_line(self, account: AccountNo) -> EurLine | None:
        for acc in (*(a for _, a in self.roles), *self.accounts):
            if acc.account == account:
                return acc.eur_line
        raise KeyError(f"{self.chart}-{self.year}: unknown account '{account}'")


_TOP_KEYS = ("chart", "year", "source", "status", "roles", "categories", "accounts")
_ACCOUNT_KEYS = ("account", "name", "class", "eur_line", "automatic")
_ACCOUNT_NO = re.compile(r"^[0-9]{4,8}$")


def _bad(name: str, msg: str) -> ChartInvalid:
    return ChartInvalid(f"{name}: {msg}")


def _parse_account(raw: object, where: str, name: str) -> ChartAccount:
    if not isinstance(raw, dict):
        raise _bad(name, f"{where} must be an object")
    for key in _ACCOUNT_KEYS:
        if key not in raw:
            raise _bad(name, f"{where} lacks required key '{key}'")
    no, label, cls, eur, auto = raw["account"], raw["name"], raw["class"], raw["eur_line"], raw["automatic"]
    if not isinstance(no, str) or not _ACCOUNT_NO.match(no):
        raise _bad(name, f"{where}: account must be a 4-8 digit string, got {no!r}")
    if not isinstance(label, str) or not label:
        raise _bad(name, f"{where}: name must be a non-empty string")
    if cls not in ("pl", "balance"):
        raise _bad(name, f"{where}: class must be 'pl' or 'balance', got {cls!r}")
    eur_line: EurLine | None = None
    if eur is not None:
        if not isinstance(eur, dict) or "line" not in eur:
            raise _bad(name, f"{where}: eur_line must be null or an object with 'line' and 'deductible'")
        deductible = eur.get("deductible")
        if not isinstance(deductible, bool):
            raise _bad(name, f"{where}: eur_line lacks the boolean 'deductible' flag")
        if not isinstance(eur["line"], str) or not eur["line"]:
            raise _bad(name, f"{where}: eur_line.line must be a non-empty string")
        eur_line = EurLine(line=eur["line"], deductible=deductible)
    if auto is not None and (not isinstance(auto, str) or auto not in TAX_KEYS):
        raise _bad(name, f"{where}: automatic {auto!r} is not a known tax key")
    return ChartAccount(
        account=AccountNo(no),
        name=label,
        account_class=cast(AccountClass, cls),
        eur_line=eur_line,
        automatic=auto,
    )


def _parse(raw: object, name: str, chart: ChartName | None, year: int | None) -> Chart:
    if not isinstance(raw, dict):
        raise _bad(name, "file must be an object")
    for key in _TOP_KEYS:
        if key not in raw:
            raise _bad(name, f"lacks required key '{key}'")
    if raw["chart"] not in CHART_NAMES:
        raise _bad(name, f"unknown chart '{raw['chart']}'")
    if chart is not None and raw["chart"] != chart:
        raise _bad(name, f"chart '{raw['chart']}' differs from the file name chart '{chart}'")
    if not isinstance(raw["year"], int) or isinstance(raw["year"], bool):
        raise _bad(name, "year must be an integer")
    if year is not None and raw["year"] != year:
        raise _bad(name, f"year {raw['year']} differs from the file name year {year}")
    if raw["status"] not in ("to_verify", "verified"):
        raise _bad(name, f"status must be 'to_verify' or 'verified', got {raw['status']!r}")
    if not isinstance(raw["source"], str):
        raise _bad(name, "source must be a string")
    for key in ("roles", "categories"):
        if not isinstance(raw[key], dict):
            raise _bad(name, f"'{key}' must be an object")
    if not isinstance(raw["accounts"], list):
        raise _bad(name, "'accounts' must be a list")
    shared_raw = raw.get("shared", [])
    if not isinstance(shared_raw, list) or not all(
        isinstance(g, list) and all(isinstance(r, str) for r in g) for g in shared_raw
    ):
        raise _bad(name, "'shared' must be a list of role name lists")
    shared = [frozenset(g) for g in shared_raw]

    roles: list[tuple[Role, ChartAccount]] = []
    for role, entry in raw["roles"].items():
        if role not in ENGINE_ROLES and not role.startswith(EXPENSE_ROLE_PREFIX):
            raise _bad(name, f"unknown role '{role}' in roles")
        roles.append((Role(role), _parse_account(entry, f"role '{role}'", name)))
    role_names = {r for r, _ in roles}
    for needed in ENGINE_ROLES:
        if needed not in role_names:
            raise _bad(name, f"engine role '{needed}' is missing")
    for group in shared:
        for member in sorted(group):
            if member not in role_names:
                raise _bad(name, f"'shared' names unknown role '{member}'")
    owner: dict[AccountNo, Role] = {}
    for role, acc in roles:
        other = owner.get(acc.account)
        if other is not None and not any({role, other} <= g for g in shared):
            raise _bad(name, f"account {acc.account} is used by both '{other}' and '{role}'")
        owner.setdefault(acc.account, role)
    categories: list[tuple[str, Role]] = []
    for cat, target in raw["categories"].items():
        if not isinstance(target, str) or target not in role_names:
            raise _bad(name, f"category '{cat}' maps to unknown role {target!r}")
        categories.append((str(cat), Role(target)))
    accounts = tuple(_parse_account(a, f"accounts[{i}]", name) for i, a in enumerate(raw["accounts"]))
    listed: set[AccountNo] = set()
    for a in accounts:
        if a.account in listed:
            raise _bad(name, f"account {a.account} is listed twice in 'accounts'")
        listed.add(a.account)
    for role, acc in roles:
        if acc.account not in listed:
            raise _bad(name, f"account {acc.account} of role '{role}' is missing in 'accounts'")
    return Chart(
        chart=cast(ChartName, raw["chart"]),
        year=raw["year"],
        requested_year=raw["year"],
        warning=None,
        source=raw["source"],
        status=cast(ChartStatus, raw["status"]),
        roles=tuple(roles),
        categories=tuple(categories),
        accounts=accounts,
    )


def _decode(text: str, name: str) -> object:
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        raise _bad(name, f"is not valid JSON ({exc})") from exc


def load_chart(path: Path) -> Chart:
    """Load and validate one chart file (used by tests and by :func:`chart_for`)."""
    return _parse(_decode(path.read_text(encoding="utf-8"), path.name), path.name, None, None)


def available_years(chart: ChartName) -> tuple[int, ...]:
    """Years that have a file for ``chart``, ascending."""
    prefix = f"{chart}-"
    years = [
        int(p.name[len(prefix) : -5])
        for p in _DIR.iterdir()
        if p.name.startswith(prefix) and p.name.endswith(".json") and p.name[len(prefix) : -5].isdigit()
    ]
    return tuple(sorted(years))


@lru_cache(maxsize=None)
def _load(chart: ChartName, year: int, base: str) -> Chart:
    text = _DIR.joinpath(f"{chart}-{year}.json").read_text(encoding="utf-8")
    return _parse(_decode(text, f"{chart}-{year}.json"), f"{chart}-{year}.json", chart, year)


def chart_for(chart: ChartName, year: int) -> Chart:
    """The chart for ``year``; the nearest older (else the oldest) file with a warning when there is none."""
    years = available_years(chart)
    if not years:
        raise ChartMissing(f"No chart files for {chart}")
    if year in years:
        used, warning = year, None
    else:
        older = [y for y in years if y < year]
        used = older[-1] if older else years[0]
        warning = f"No {chart} chart for {year}; using {used}."
    return replace(_load(chart, used, str(_DIR)), requested_year=year, warning=warning)
