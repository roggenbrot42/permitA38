"""The database schema, described ONCE in a dialect-neutral way.

Every backend generates its DDL, upserts and type conversions from this structure
(see `dialect.py`). Migrations are historical snapshots; a test keeps them in sync with
this description and with the pydantic models in `permita38/core/models.py`.

Logical column types: ``id`` (short string key), ``str`` (indexable string), ``text`` (long text),
``int``, ``float``, ``bool`` and ``json`` (lists/dicts, stored as JSON).
Unknown fields of a record are kept in the JSON column ``extra`` (forward compatibility with newer
versions and other devices).
"""

from dataclasses import dataclass, field

ID, STR, TEXT, INT, FLOAT, BOOL, JSON = "id", "str", "text", "int", "float", "bool", "json"
LOGICAL_TYPES = (ID, STR, TEXT, INT, FLOAT, BOOL, JSON)


@dataclass(frozen=True)
class Column:
    name: str
    type: str


@dataclass(frozen=True)
class Index:
    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    indexes: tuple[Index, ...] = ()
    pk: str = "id"
    extra_column: str | None = "extra"
    _by_name: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_by_name", {c.name: c for c in self.columns})

    def column(self, name: str) -> Column | None:
        return self._by_name.get(name)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def _cols(spec: str) -> tuple[Column, ...]:
    """'name:str rate:float ...' -> columns"""
    return tuple(Column(n, t) for n, t in (p.split(":") for p in spec.split()))


_BASE = "id:id created:int updated:int "
_EXTRA = ""  # the `extra` JSON column is added implicitly (Table.extra_column)

_DOCUMENT = (
    "number:str customer_id:id project_id:id status:str date:str due_date:str valid_until:str paid_date:str "
    "service_from:str service_to:str title:str reference:str template_id:str language:str intro:text outro:text "
    "notes:text vat_rate:float lines:json plan:json offer_id:id "
    # set by the document importer (core/service/importer): the legal original is the imported PDF
    "imported:bool original_number:str external_id:str import_batch:str totals_unverified:bool original_pdf:str"
)


def _document_table(name: str) -> Table:
    return Table(
        name,
        _cols(_BASE + _DOCUMENT + _EXTRA),
        (
            Index(f"idx_{name}_status", ("status",)),
            Index(f"idx_{name}_customer", ("customer_id",)),
        ),
    )


TABLES: dict[str, Table] = {
    t.name: t
    for t in (
        Table(
            "customers",
            _cols(
                _BASE + "name:str customer_no:str street:str zip:str city:str country:str tax_number:str vat_id:str "
                "contact:str email:str phone:str notes:text rate:float external_id:str import_batch:str" + _EXTRA
            ),
        ),
        Table(
            "projects",
            _cols(
                _BASE + "name:str customer_id:id personal:bool rate:float budget_hours:float status:str color:str "
                "notes:text member_ids:json offer_id:id start:str end:str extensions:json" + _EXTRA
            ),
            (Index("idx_projects_customer", ("customer_id",)),),
        ),
        Table(
            "tasks",
            _cols(_BASE + "project_id:id name:str rate:float" + _EXTRA),
            (Index("idx_tasks_project", ("project_id",)),),
        ),
        Table(
            "entries",
            _cols(
                _BASE + "user_id:id date:str minutes:int project_id:id task_id:id note:text start:str end:str "
                "billable:bool invoice_id:id rate:float" + _EXTRA
            ),
            (
                Index("idx_entries_user_date", ("user_id", "date")),
                Index("idx_entries_project", ("project_id",)),
                Index("idx_entries_invoice", ("invoice_id",)),
            ),
        ),
        Table(
            "contracts",
            _cols(
                _BASE + "title:str customer_id:id project_id:id start:str end:str hours_per_week:float "
                "total_hours:float rate:float status:str user_ids:json offer_id:id" + _EXTRA
            ),
        ),
        Table(
            "absences",
            _cols(_BASE + "user_id:id kind:str start:str end:str note:text" + _EXTRA),
            (Index("idx_absences_user", ("user_id",)),),
        ),
        Table(
            "plan_scenarios",
            _cols(_BASE + "name:str user_id:id changes:json" + _EXTRA),
            (Index("idx_plan_scenarios_user", ("user_id",)),),
        ),
        _document_table("offers"),
        _document_table("invoices"),
        Table(
            "expenses",
            _cols(
                _BASE + "date:str net:float vat:float vat_rate:float vendor:str description:text category:str "
                "project_id:id deductible:float attachment:str vat_deductible:float external_id:str import_batch:str lines:json "
                "trip_id:id" + _EXTRA
            ),
            (Index("idx_expenses_date", ("date",)), Index("idx_expenses_project", ("project_id",))),
        ),
        # business trips (docs/specs/business-trips.md): the meal allowance and mileage are computed from these rows
        Table(
            "trips",
            _cols(
                _BASE + "user_id:id title:str purpose:text customer_id:id project_id:id start:str end:str "
                "places:json meals_provided:json mileage:json not_needed:json cost_centre:str status:str allowance_total:float "
                "mileage_km:float mileage_total:float rules_version:str days:json" + _EXTRA
            ),
            (Index("idx_trips_user", ("user_id",)),),
        ),
        Table(
            "users",
            _cols(
                _BASE + "username:str name:str role:str weekly_hours:float workdays:json default_rate:float "
                "cost_rate:float color:str tracking_start:str active:bool password_hash:str pw_changed:int "
                "totp_secret:str totp_enabled:bool totp_last_step:int totp_recovery:json work_start:str work_end:str timezone:str"
                + _EXTRA
            ),
            (Index("idx_users_username", ("username",), unique=True),),
        ),
        Table(
            "templates",
            _cols(
                _BASE + "name:str layout:str accent:str language:str show_logo:bool intro_offer:text "
                "outro_offer:text intro_invoice:text outro_invoice:text closing:text" + _EXTRA
            ),
        ),
        Table(
            "calendar_sources",
            _cols(
                _BASE + "user_id:id name:str kind:str url:text refresh_minutes:int title_policy:str "
                "all_day_blocks:bool include_keywords:json exclude_keywords:json busy_default:bool "
                "last_fetched:int last_status:str last_error:text etag:str last_modified:str event_count:int" + _EXTRA
            ),
            (Index("idx_calendar_sources_user", ("user_id",)),),
        ),
        Table(
            "calendar_events",
            _cols(
                _BASE + "source_id:id uid:str recurrence_id:str start_utc:str end_utc:str all_day:bool busy:bool "
                "title:str categories:json ooo:bool" + _EXTRA
            ),
            (
                Index("idx_calendar_events_source", ("source_id",)),
                Index("idx_calendar_events_start", ("start_utc",)),
            ),
        ),
        Table(
            "timers",
            _cols(_BASE + "running:bool started:int project_id:id task_id:id note:text" + _EXTRA),
        ),
        # document number counters: one row per document type (or 'shared') and period (see core/numbering.py)
        Table(
            "number_sequences",
            _cols(_BASE + "doc_type:str period:str last_value:int" + _EXTRA),
            (Index("idx_number_sequences_key", ("doc_type", "period"), unique=True),),
        ),
        # audit trail of changes to the numbering (start number, pattern, reset scope)
        Table(
            "numbering_log",
            _cols(_BASE + "doc_type:str action:str before:json after:json warnings:json reason:text" + _EXTRA),
        ),
        # one row per import run; records carry the batch id so a run can be undone
        Table(
            "import_batches",
            _cols(_BASE + "kind:str filename:str status:str counts:json mapping:json finished:int" + _EXTRA),
        ),
        # API tokens (docs/contracts/api-tokens.md): only the SHA-256 of the token is stored
        Table(
            "api_tokens",
            _cols(
                _BASE + "user_id:id label:str token_hash:str hint:str read_only:bool expires_at:int "
                "revoked_at:int last_used_at:int" + _EXTRA
            ),
            (
                Index("idx_api_tokens_user", ("user_id",)),
                Index("idx_api_tokens_hash", ("token_hash",), unique=True),
            ),
        ),
        # append-only: one row per request authenticated by a token (no bodies, no query strings, no secrets)
        Table(
            "audit_log",
            _cols(_BASE + "at:int token_id:id user_id:id method:str route:str status:int" + _EXTRA),
            (
                Index("idx_audit_log_token", ("token_id", "at")),
                Index("idx_audit_log_at", ("at",)),
            ),
        ),
        Table("settings", _cols("key:str value:json"), pk="key", extra_column=None),
    )
}

# internal per-user collections that are not exposed through the CRUD API
INTERNAL_COLLECTIONS = [
    "api_tokens",
    "audit_log",
    "timers",
    "number_sequences",
    "numbering_log",
    "import_batches",
    "plan_scenarios",
    "calendar_sources",
    "calendar_events",
]
# the user-facing record collections
COLLECTIONS = [name for name in TABLES if name not in ("settings", *INTERNAL_COLLECTIONS)]
