"""The catalog of rights and the built-in roles.

A *right* is a permission to do one thing (``invoices.manage``). A *role* is a named set of rights; a user has one
or more roles and may do whatever the union of their rights allows. This module is the single source of truth:
the API, the web app, the documentation (``docs/roles-and-rights.md`` is checked against it by a test) and the
migration seeds are all derived from or tested against it.

Rules of the model
------------------
* Rights are stable identifiers (``<area>.<verb>``). Never rename one; add new ones.
* Some rights *imply* others (``invoices.manage`` implies ``invoices.view``). The implications are expanded when
  rights are resolved, so a role never has to list both.
* Rights flagged ``planned`` are reserved for modules that are not built yet. They can be stored in a role but have
  no effect (and the web app shows them greyed out) until the module ships.
* ``roles.manage`` is an administrator-level right: whoever may define roles can give themselves any right. It is
  treated as such everywhere (see ``docs/roles-and-rights.md``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Right:
    key: str
    group: str
    label: str
    description: str
    planned: bool = False


G_TIME = "Time tracking & time off"
G_PROJECTS = "Projects & tasks"
G_CUSTOMERS = "Customers"
G_PLANNING = "Planning"
G_SALES = "Offers & invoices"
G_EXPENSES = "Expenses"
G_FINANCE = "Finance & reports"
G_TEAM = "Team & access"
G_SETTINGS = "Settings & templates"
G_PLANNED = "Planned modules"

CATALOG: tuple[Right, ...] = (
    # --- time
    Right(
        "time.track_own", G_TIME, "Track own time", "Start/stop the timer and add, edit and delete own time entries."
    ),
    Right(
        "time.view_all", G_TIME, "See everybody's time", "Read the time entries and weekly dashboards of all people."
    ),
    Right(
        "time.edit_all",
        G_TIME,
        "Edit everybody's time",
        "Add, edit and delete time entries of any person, including billing fields (rate, invoiced).",
    ),
    Right("absences.manage_own", G_TIME, "Manage own time off", "Add, edit and delete own vacation and absences."),
    Right("absences.view_all", G_TIME, "See everybody's time off", "Read the absences of all people."),
    Right("absences.manage_all", G_TIME, "Manage everybody's time off", "Add, edit and delete absences of anybody."),
    # --- projects
    Right(
        "projects.view_assigned",
        G_PROJECTS,
        "See assigned projects",
        "See the projects (and their tasks, without rates) the person is assigned to or that are open to everyone.",
    ),
    Right(
        "projects.view_all",
        G_PROJECTS,
        "See all projects",
        "See every project and its tasks (rates, budgets and notes stay hidden without 'Manage projects').",
    ),
    Right(
        "projects.manage",
        G_PROJECTS,
        "Manage projects",
        "Create, edit and delete projects; see rates, budgets and notes; includes managing tasks.",
    ),
    Right("tasks.manage", G_PROJECTS, "Manage tasks", "Create, edit and delete tasks and set task rates."),
    # --- customers
    Right("customers.view", G_CUSTOMERS, "See customers", "Read customer records (addresses, tax numbers, notes)."),
    Right(
        "customers.manage",
        G_CUSTOMERS,
        "Manage customers",
        "Create, edit and delete customers, including the Impressum lookup.",
    ),
    # --- planning
    Right("planning.view", G_PLANNING, "See the planning", "Read contracts and the capacity forecast."),
    Right("planning.manage", G_PLANNING, "Manage the planning", "Create, edit and delete contracts."),
    # --- sales
    Right("offers.view", G_SALES, "See offers", "Read offers and download their PDF."),
    Right("offers.manage", G_SALES, "Manage offers", "Create, edit, finalise and delete offers; mark them accepted."),
    Right("invoices.view", G_SALES, "See invoices", "Read invoices and download their PDF."),
    Right(
        "invoices.manage",
        G_SALES,
        "Manage invoices",
        "Create, edit, finalise and delete invoices; bill tracked time; mark invoices paid.",
    ),
    # --- expenses
    Right("expenses.view", G_EXPENSES, "See expenses", "Read expenses and their attached bills."),
    Right("expenses.manage", G_EXPENSES, "Manage expenses", "Create, edit and delete expenses; upload bills."),
    # --- finance
    Right(
        "finance.view",
        G_FINANCE,
        "See finance",
        "Revenue, profit, VAT and tax estimates, and the profitability of projects.",
    ),
    # --- team
    Right("team.view", G_TEAM, "See the team", "See all users with their working hours, rates and roles."),
    Right(
        "team.manage",
        G_TEAM,
        "Manage the team",
        "Create, edit, deactivate and delete users, set their passwords and reset their two-factor authentication.",
    ),
    Right(
        "roles.manage",
        G_TEAM,
        "Manage roles",
        "Define roles and their rights. Administrator level: it allows granting any right, also to oneself.",
    ),
    # --- settings
    Right("settings.manage", G_SETTINGS, "Manage settings", "Business data, tax settings, defaults and the logo."),
    Right("templates.manage", G_SETTINGS, "Manage document templates", "Create, edit and delete document templates."),
    Right("attachments.view", G_SETTINGS, "See attachments", "Download uploaded files (bills, logo)."),
    # --- reserved for modules that are not built yet (names are stable; they have no effect yet)
    Right("accounting.view", G_PLANNED, "See the books", "Chart of accounts, journal and reports.", planned=True),
    Right("accounting.manage", G_PLANNED, "Manage the books", "Post and correct journal entries.", planned=True),
    Right("vat.view", G_PLANNED, "See VAT returns", "Umsatzsteuer-Voranmeldung preparation.", planned=True),
    Right("vat.manage", G_PLANNED, "Manage VAT returns", "Prepare and close VAT return periods.", planned=True),
    Right("backup.view", G_PLANNED, "See backups", "Backup status and history.", planned=True),
    Right("backup.manage", G_PLANNED, "Manage backups", "Run, verify and restore backups.", planned=True),
    Right("export.run", G_PLANNED, "Export data", "Structured exports of the data.", planned=True),
    Right("gdpr.manage", G_PLANNED, "Data protection tools", "Data-subject exports and erasure.", planned=True),
    Right("audit.view", G_PLANNED, "See the audit log", "Read the change log.", planned=True),
)

RIGHTS: dict[str, Right] = {r.key: r for r in CATALOG}
ALL_RIGHTS: frozenset[str] = frozenset(RIGHTS)
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(r.group for r in CATALOG))

# holding the key implies holding the values
IMPLIES: dict[str, tuple[str, ...]] = {
    "time.edit_all": ("time.view_all", "time.track_own"),
    "absences.manage_all": ("absences.view_all", "absences.manage_own"),
    "projects.view_all": ("projects.view_assigned",),
    "projects.manage": ("projects.view_all", "tasks.manage"),
    "customers.manage": ("customers.view",),
    "planning.manage": ("planning.view",),
    "offers.manage": ("offers.view",),
    "invoices.manage": ("invoices.view",),
    "expenses.manage": ("expenses.view", "attachments.view"),
    "expenses.view": ("attachments.view",),
    "team.manage": ("team.view",),
    "accounting.manage": ("accounting.view",),
    "vat.manage": ("vat.view",),
    "backup.manage": ("backup.view",),
}


def expand(rights) -> frozenset[str]:
    """The given rights plus everything they imply (transitively). Unknown keys are dropped."""
    out: set[str] = set()
    todo = [r for r in rights if r in RIGHTS]
    while todo:
        r = todo.pop()
        if r not in out:
            out.add(r)
            todo.extend(IMPLIES.get(r, ()))
    return frozenset(out)


def implying(right: str) -> frozenset[str]:
    """`right` and every right that implies it (what has to be removed to really lose `right`)."""
    return frozenset(r for r in RIGHTS if right in expand([r]))


# ------------------------------------------------------------------ built-in roles
# fixed ids (the id format of records is 6-32 chars of [0-9a-z]); the migration seeds exactly these rows
BOSS_ID = "sysboss"
MEMBER_ID = "sysmember"


@dataclass(frozen=True)
class RoleDef:
    id: str
    name: str
    description: str
    rights: tuple[str, ...]
    system: bool


MEMBER_RIGHTS = ("time.track_own", "absences.manage_own", "projects.view_assigned")

SYSTEM_ROLES: tuple[RoleDef, ...] = (
    RoleDef(
        BOSS_ID,
        "Boss",
        "Everything, including roles and settings. Locked: cannot be edited or deleted.",
        tuple(sorted(ALL_RIGHTS)),
        True,
    ),  # fmt: skip
    RoleDef(
        MEMBER_ID,
        "Member",
        "Tracks their own time and time off. Locked: cannot be edited or deleted.",
        MEMBER_RIGHTS,
        True,
    ),  # fmt: skip
)

# editable examples seeded by the migration; they can be changed or deleted like any custom role
EXAMPLE_ROLES: tuple[RoleDef, ...] = (
    RoleDef(
        "exaccountant", "Accountant",
        "Reads invoices, offers, customers, finance and the planning; manages expenses and bills.",
        (
            "customers.view", "invoices.view", "offers.view", "finance.view", "expenses.manage",
            "planning.view", "projects.view_all", "attachments.view",
        ),
        False,
    ),
    RoleDef(
        "exprojectmanager", "Project manager",
        "Plans and runs projects and the team's time, without access to money, invoices or settings.",
        (
            "time.track_own", "time.view_all", "time.edit_all", "absences.manage_own", "absences.manage_all",
            "projects.manage", "customers.view", "planning.manage", "team.view",
        ),
        False,
    ),
)  # fmt: skip

SYSTEM_ROLE_IDS = frozenset(r.id for r in SYSTEM_ROLES)
