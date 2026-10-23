"""Roles and per-area permissions: the single source of truth.

There are three fixed roles. Each role gets, per *area*, a *scope* for reading and a *scope* for writing:

* ``NONE``    – no access (the API answers 403),
* ``LIMITED`` – access to a restricted view: own records only (time, time off), assigned projects without rates
  (projects, tasks), business name and federal state only (settings) or names only (team),
* ``ALL``     – full access.

The API (`permita38/api/deps.py`), the services, the web app (which receives the table with the user) and the
documentation (`docs/roles.md`, checked by a test) all derive from `MATRIX`. Roles stored by older versions
(``member``) are read as ``employee``.
"""

from enum import StrEnum
from typing import Literal

Op = Literal["read", "write"]


class Scope(StrEnum):
    NONE = "none"
    LIMITED = "limited"
    ALL = "all"


BOSS = "boss"
EMPLOYEE = "employee"
ACCOUNTANT = "accountant"
ROLES: tuple[str, ...] = (BOSS, EMPLOYEE, ACCOUNTANT)
LEGACY_ROLES = {"member": EMPLOYEE}  # accepted on input, read from data written by older versions

# area -> what it covers
AREAS: dict[str, str] = {
    "timer": "Own timer and weekly dashboard",
    "time": "Time entries (the timesheet)",
    "absences": "Vacation and other time off",
    "projects": "Projects and tasks",
    "customers": "Customers (and the Impressum lookup)",
    "contracts": "Contracts",
    "planning": "Planning: forecast, what-if scenarios, vacation planner",
    "offers": "Offers",
    "invoices": "Invoices",
    "expenses": "Expenses",
    "finance": "Revenue, profit, VAT and project reports",
    "templates": "Document templates",
    "settings": "Business settings",
    "attachments": "Uploaded files (bills, logo)",
    "team": "Users, passwords and two-factor resets",
    "team_timeline": "Team timeline (who is away, who works on what); the level is set in the settings",
}

N, L, A = Scope.NONE, Scope.LIMITED, Scope.ALL

# role -> area -> (read scope, write scope)
MATRIX: dict[str, dict[str, tuple[Scope, Scope]]] = {
    # the timeline is read-only for everyone; the boss sees every level, the others get the level set in the settings
    BOSS: {area: (A, N) if area == "team_timeline" else (A, A) for area in AREAS},
    EMPLOYEE: {
        "timer": (L, L),
        "time": (L, L),
        "absences": (L, L),
        "projects": (L, N),
        "customers": (N, N),
        "contracts": (N, N),
        "planning": (N, N),
        "offers": (N, N),
        "invoices": (N, N),
        "expenses": (N, N),
        "finance": (N, N),
        "templates": (N, N),
        "settings": (L, N),
        "attachments": (N, N),
        "team": (L, N),
        "team_timeline": (L, N),
    },
    ACCOUNTANT: {
        "timer": (N, N),
        "time": (A, N),
        "absences": (N, N),
        "projects": (A, N),
        "customers": (A, N),
        "contracts": (A, N),
        "planning": (N, N),
        "offers": (A, N),
        "invoices": (A, N),
        "expenses": (A, A),
        "finance": (A, N),
        "templates": (A, N),
        "settings": (A, N),
        "attachments": (A, A),
        "team": (L, N),
        "team_timeline": (L, N),
    },
}

# generic CRUD collection -> area
COLLECTION_AREA = {
    "customers": "customers",
    "projects": "projects",
    "tasks": "projects",
    "entries": "time",
    "contracts": "contracts",
    "absences": "absences",
    "offers": "offers",
    "invoices": "invoices",
    "expenses": "expenses",
    "templates": "templates",
}


def normalize_role(role: object) -> str:
    """Canonical role name; ``member`` (older versions) is ``employee``. Unknown values stay as they are and have
    no permissions at all."""
    return LEGACY_ROLES.get(role, role) if isinstance(role, str) else ""


def scope(role: object, area: str, op: Op = "read") -> Scope:
    row = MATRIX.get(normalize_role(role), {}).get(area)
    if row is None:
        return Scope.NONE
    return row[0] if op == "read" else row[1]


def allowed(role: object, area: str, op: Op = "read") -> bool:
    return scope(role, area, op) != Scope.NONE


def permissions(role: object) -> dict[str, dict[str, str]]:
    """The table the web app gets: ``{area: {"read": scope, "write": scope}}``."""
    return {a: {"read": scope(role, a).value, "write": scope(role, a, "write").value} for a in AREAS}
