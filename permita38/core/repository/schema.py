"""The database schema, described ONCE in a dialect-neutral way.

Every backend generates its DDL, upserts and type conversions from this structure
(see `dialect.py`). Migrations are historical snapshots; a test keeps them in sync with
this description and with the pydantic models in `permita38/core/models.py`.

Logical column types: ``id`` (short string key), ``str`` (indexable string), ``text`` (long text),
``int``, ``float``, ``bool`` and ``json`` (lists/dicts, stored as JSON).
Unknown fields of a record are kept in the JSON column ``extra`` (forward compatibility with newer
versions and other devices).
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

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


_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1


def fits(column_type: str, value: Any) -> bool:
    """Does ``value`` fit a column of the logical type ``column_type``? (Values that do not go to the `extra` JSON.)"""
    if column_type in (ID, STR, TEXT):
        return isinstance(value, str)
    if column_type == INT:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return isinstance(value, int) and not isinstance(value, bool) and _INT_MIN <= value <= _INT_MAX
    if column_type == FLOAT:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if column_type == BOOL:
        return isinstance(value, bool)
    return column_type == JSON


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


def _document_table(name: str, more: str = "") -> Table:
    return Table(
        name,
        _cols(_BASE + _DOCUMENT + more + _EXTRA),
        (
            Index(f"idx_{name}_status", ("status",)),
            Index(f"idx_{name}_customer", ("customer_id",)),
            Index(f"idx_{name}_number", ("number",)),
            Index(f"idx_{name}_project", ("project_id",)),
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
                _BASE + "name:str customer_id:id kind:str rate:float budget_hours:float status:str color:str "
                "notes:text offer_id:id start:str end:str extensions:json" + _EXTRA
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
            (Index("idx_contracts_project", ("project_id",)),),
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
        # accounting columns (docs/specs/accounting.md section 8.2), all nullable: NULL reads as the spec default
        _document_table("invoices", " kind:str credit_kind:str corrects_id:id tax_treatment:str issued:json"),
        Table(
            "expenses",
            _cols(
                _BASE + "date:str net:float vat:float vat_rate:float vendor:str description:text category:str "
                "project_id:id deductible:float attachment:str vat_deductible:float external_id:str import_batch:str lines:json "
                "trip_id:id trip_item_id:id supplier_id:id allocation_id:id reverse_charge:bool paid_on:str invoice_no:str "
                "tax_treatment:str service_date:str bill_rev:int pay_rev:int" + _EXTRA
            ),
            (
                Index("idx_expenses_date", ("date",)),
                Index("idx_expenses_project", ("project_id",)),
                Index("idx_expenses_supplier", ("supplier_id",)),
                Index("idx_expenses_allocation", ("allocation_id",)),
            ),
        ),
        # suppliers and subcontractors (docs/contracts/subcontractors.md); kind "person-capacity" sells hours
        Table(
            "suppliers",
            _cols(
                _BASE + "name:str supplier_no:str kind:str active:bool street:str zip:str city:str country:str "
                "vat_id:str tax_number:str vat_mode:str contact:str email:str phone:str notes:text iban:str bic:str "
                "bank_name:str payment_terms_days:int payment_terms_note:str tags:json weekly_hours:float "
                "workdays:json rate:float unavailable:json login_user_id:id external_id:str import_batch:str" + _EXTRA
            ),
            (Index("idx_suppliers_name", ("name",)), Index("idx_suppliers_external_id", ("external_id",))),
        ),
        # offers received from suppliers (not the offers we issue)
        Table(
            "incoming_offers",
            _cols(
                _BASE + "supplier_id:id reference:str title:str received_on:str valid_until:str lines:json "
                "net_total:float vat:float vat_rate:float vat_mode:str gross_total:float plan:json project_id:id "
                "contract_id:id customer_id:id attachment:str status:str status_changed_at:str decided_by:id "
                "decision_note:text allocation_id:id notes:text external_id:str import_batch:str" + _EXTRA
            ),
            (
                Index("idx_incoming_offers_supplier", ("supplier_id",)),
                Index("idx_incoming_offers_status", ("status",)),
                Index("idx_incoming_offers_project", ("project_id",)),
            ),
        ),
        # business trips (docs/specs/business-trips.md): the meal allowance and mileage are computed from these rows
        Table(
            "trips",
            _cols(
                _BASE + "user_id:id title:str purpose:text customer_id:id project_id:id start:str end:str "
                "places:json meals_provided:json mileage:json not_needed:json cost_centre:str status:str allowance_total:float "
                "mileage_km:float mileage_total:float rules_version:str days:json post_rev:int" + _EXTRA
            ),
            (Index("idx_trips_user", ("user_id",)), Index("idx_trips_project", ("project_id",))),
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
            (Index("idx_timers_project", ("project_id",)),),
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
        # hours per week of one resource given to one project (docs/contracts/planner-per-project.md); the
        # CHECK "allocations_one_owner" (exactly one of user_id / supplier_id) lives in the migration SQL only
        Table(
            "allocations",
            _cols(
                _BASE + "user_id:id supplier_id:id incoming_offer_id:id project_id:id contract_id:id customer_id:id "
                "start:str end:str hours_per_week:float total_hours:float rate:float status:str cancelled_from:str "
                "cancel_reason:text" + _EXTRA
            ),
            (
                Index("idx_allocations_user", ("user_id",)),
                Index("idx_allocations_project", ("project_id",)),
                Index("idx_allocations_supplier", ("supplier_id",)),
                Index("idx_allocations_status", ("status",)),
            ),
        ),
        # scanned receipts (docs/specs/receipt-scan.md): a flagged bill is not an expense until the user saves it
        Table(
            "receipt_scans",
            _cols(
                _BASE + "status:str sha256:str invoice_key:str attachment:str filename:str engine:str draft:json "
                "doc:json flags:json expense_id:id created_by:id" + _EXTRA
            ),
            (
                Index("idx_receipt_scans_sha256", ("sha256",), unique=True),
                Index("idx_receipt_scans_status", ("status",)),
                Index("idx_receipt_scans_invoice_key", ("invoice_key",)),
                Index("idx_receipt_scans_expense_id", ("expense_id",)),
            ),
        ),
        # scanned old offers and invoices (docs/specs/document-scan.md)
        Table(
            "document_scans",
            _cols(
                _BASE + "status:str sha256:str doc_key:str attachment:str filename:str engine:str kind:str draft:json "
                "doc:json flags:json record_coll:str record_id:str batch_id:str created_by:id" + _EXTRA
            ),
            (
                Index("idx_document_scans_sha256", ("sha256",), unique=True),
                Index("idx_document_scans_status", ("status",)),
                Index("idx_document_scans_doc_key", ("doc_key",)),
                Index("idx_document_scans_record_id", ("record_id",)),
            ),
        ),
        # accounting journal (docs/specs/accounting.md section 8.1): internal, written only by the accounting services.
        # Every column is nullable at storage level; NOT NULL is enforced by `required` guards (docs/specs/db-constraints.md)
        Table(
            "fiscal_years",
            _cols(
                _BASE + "year:int chart:str income_basis:str vat_basis:str vat_frequency:str dauerfrist:bool "
                "small_business:bool vat_regular_from:str rules_version:str opened_at:int opened_by:id "
                "closed_at:int closed_by:id closing_hash:str closing_entry_no:int" + _EXTRA
            ),
            (Index("idx_fiscal_years_year", ("year",), unique=True),),
        ),
        Table(
            "journal_entries",
            _cols(
                _BASE + "fiscal_year:int entry_no:int document_date:str booking_date:str event:str event_key:str "
                "source_coll:str source_id:id source_number:str reverses_id:id counterparty_id:id cp_vat_id:str "
                "cp_country:str text:text origin:str created_at:int created_by:id prev_hash:str hash:str" + _EXTRA
            ),
            (
                Index("idx_journal_entries_event_key", ("event_key",), unique=True),
                Index("idx_journal_entries_year_no", ("fiscal_year", "entry_no"), unique=True),
                Index("idx_journal_entries_source", ("source_id",)),
                Index("idx_journal_entries_booking", ("booking_date",)),
            ),
        ),
        Table(
            "journal_lines",
            _cols(
                _BASE + "entry_id:id line_no:int role:str account:str amount_cents:int tax_key:str tax_kind:str "
                "tax_rate:str tax_base_cents:int vat_date:str income_date:str open_item_id:id treatment:str" + _EXTRA
            ),
            (
                Index("idx_journal_lines_entry_line", ("entry_id", "line_no"), unique=True),
                Index("idx_journal_lines_vat", ("vat_date", "tax_key")),
                Index("idx_journal_lines_income", ("income_date",)),
                Index("idx_journal_lines_open_item", ("open_item_id",)),
            ),
        ),
        Table(
            "payments",
            _cols(
                _BASE + "document_id:id date:str amount_cents:int method:str kind:str note:text reverses_id:id "
                "created_at:int created_by:id" + _EXTRA
            ),
            (Index("idx_payments_document", ("document_id",)),),
        ),
        Table(
            "period_locks",
            _cols(_BASE + "year:int month:int locked_at:int locked_by:id reason:str" + _EXTRA),
            (Index("idx_period_locks_year_month", ("year", "month"), unique=True),),
        ),
        # VAT returns and ZM reports (docs/specs/ustva.md "Data model", zm.md "Records, status, drift"): internal.
        # `amends_key` = COALESCE(amends_id, '') carries the unique index (a CHECK keeps it equal, see CHECKS)
        Table(
            "vat_returns",
            _cols(
                _BASE + "year:int period_kind:str period_no:int from_date:str to_date:str due_date:str status:str "
                "basis:str frequency:str dauerfrist:bool small_business:bool rules_version:str boxes:json "
                "source_lines:json result_cents:int special_prepayment_credit_cents:int rounding_difference_cents:int "
                "filed_on:str "
                "reference:str note:text amends_id:id amends_key:str replaced_by:id external:bool finalised_at:int "
                "finalised_by:id" + _EXTRA
            ),
            (Index("idx_vat_returns_period", ("year", "period_kind", "period_no", "amends_key"), unique=True),),
        ),
        Table(
            "zm_reports",
            _cols(
                _BASE + "year:int quarter:int status:str due_date:str lines:json source_lines:json rules_version:str "
                "filed_on:str reference:str note:text amends_id:id amends_key:str replaced_by:id finalised_at:int "
                "finalised_by:id" + _EXTRA
            ),
            (Index("idx_zm_reports_quarter", ("year", "quarter", "amends_key"), unique=True),),
        ),
        Table("settings", _cols("key:str value:json"), pk="key", extra_column=None),
    )
}


_SCALARS = (str, int, float, bool)


def _check_scalar(v: object) -> None:
    if not isinstance(v, _SCALARS) or (isinstance(v, float) and not math.isfinite(v)):
        raise ValueError(f"unsupported condition value {v!r} (only str, int, finite float, bool)")


SQL_BLANK = " \t\n\v\f\r"  # the whitespace set the dialects' `trim(x, ...)` strips (not NBSP or other Unicode spaces)


def _is_set(v: object) -> bool:
    """IsSet semantics in Python: not None, not '', and for text not only SQL_BLANK characters."""
    if v is None:
        return False
    return not (isinstance(v, str) and v.strip(SQL_BLANK) == "")


@dataclass(frozen=True)
class Eq:
    """`col = value` (false when the column is NULL)."""

    col: str
    value: Any

    def __post_init__(self) -> None:
        _check_scalar(self.value)

    def columns(self) -> tuple[str, ...]:
        return (self.col,)

    def holds(self, row: dict[str, Any]) -> bool:
        v = row.get(self.col)
        return v is not None and v == self.value and isinstance(v, bool) == isinstance(self.value, bool)


@dataclass(frozen=True)
class In:
    """`col IN values` (false when the column is NULL)."""

    col: str
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))
        if not self.values:
            raise ValueError("In() needs at least one value")
        for v in self.values:
            _check_scalar(v)

    def columns(self) -> tuple[str, ...]:
        return (self.col,)

    def holds(self, row: dict[str, Any]) -> bool:
        v = row.get(self.col)
        return v is not None and v in self.values


@dataclass(frozen=True)
class IsSet:
    """Not NULL, not '' and (for text) not only whitespace."""

    col: str

    def columns(self) -> tuple[str, ...]:
        return (self.col,)

    def holds(self, row: dict[str, Any]) -> bool:
        return _is_set(row.get(self.col))


@dataclass(frozen=True)
class IsBlank:
    """The opposite of `IsSet`."""

    col: str

    def columns(self) -> tuple[str, ...]:
        return (self.col,)

    def holds(self, row: dict[str, Any]) -> bool:
        return not _is_set(row.get(self.col))


Condition = Eq | In | IsSet | IsBlank

GUARD_KINDS = (
    "unique",
    "date",
    "non_negative",
    "order",
    "one_of",
    "exactly_one",
    "required",
    "max_days",
    "range",
    "implies",
    "reference",
    "append_only",
    "frozen_when",
    "set_once",
)

# number of columns a guard of this kind names in `columns` (None = derived from its conditions)
_KIND_ARITY: dict[str, int | None] = {
    "unique": 1,
    "date": 1,
    "non_negative": 1,
    "order": 2,
    "one_of": 1,
    "exactly_one": 2,
    "required": 1,
    "max_days": 2,
    "range": 1,
    "implies": None,
    "reference": 1,
    "append_only": 0,  # table-level: refuses every UPDATE and DELETE
    "frozen_when": 1,  # the state column; the frozen value is `values[0]`
    "set_once": 1,  # the column that may change only from NULL
}


def column_type(table: str, col: str) -> str | None:
    """Logical type of ``table.col`` (None when the table or column is not in TABLES)."""
    t = TABLES.get(table)
    c = t.column(col) if t else None
    return c.type if c else None


@dataclass(frozen=True)
class Guard:
    """A database-level guard checked for new or changed values only (see docs/specs/db-constraints.md).

    `columns` is one column, or two for "order" / "exactly_one" (a, b) / "max_days" (start, end); for "implies" it is
    derived from the conditions (pass `()`). The optional parameters belong to the new kinds (docs/specs/db-guards-v2.md):
    `values` (one_of), `lo` / `hi` / `lo_exclusive` (range, at least one bound), `days` (max_days),
    `if_` / `then` (implies; for implies the code should be `state.implies:<name>`)."""

    name: str  # trigger name stem, e.g. "guard_invoices_number_unique"
    table: str
    kind: str  # one of GUARD_KINDS
    columns: tuple[str, ...]
    code: str  # the RAISE code, e.g. "numbering.duplicate"
    values: tuple[Any, ...] = ()
    lo: int | float | None = None
    hi: int | float | None = None
    lo_exclusive: bool = False
    days: int | None = None
    if_: Condition | None = None
    then: Condition | None = None
    target: str | None = None  # reference: the table whose `id` the column points to
    # frozen_when (docs/specs/db-constraints.md): while the state column equals values[0] only `mutable` columns may
    # change; the state may leave the frozen value only while `reopen_if_null` is unset, and then `reopen_clears` may
    # change too (without `reopen_if_null` the state never leaves the frozen value)
    mutable: tuple[str, ...] = ()
    reopen_if_null: str | None = None
    reopen_clears: tuple[str, ...] = ()
    # frozen_when exemption: a row whose columns equal all `exempt_when` (column, value) pairs while `exempt_until_set`
    # is unset (NULL or blank) is not frozen, neither for UPDATE nor DELETE (checked on the stored OLD row)
    exempt_when: tuple[tuple[str, Any], ...] = ()
    exempt_until_set: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in GUARD_KINDS:
            raise ValueError(f"unknown guard kind {self.kind!r}")
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "values", tuple(self.values))
        object.__setattr__(self, "mutable", tuple(self.mutable))
        object.__setattr__(self, "reopen_clears", tuple(self.reopen_clears))
        object.__setattr__(self, "exempt_when", tuple((c, v) for c, v in self.exempt_when))
        if self.kind == "implies":
            if self.if_ is None or self.then is None:
                raise ValueError("an implies guard needs if_ and then")
            derived = tuple(dict.fromkeys(self.if_.columns() + self.then.columns()))
            if self.columns not in ((), derived):
                raise ValueError("an implies guard derives its columns from if_ / then")
            object.__setattr__(self, "columns", derived)
        elif len(self.columns) != _KIND_ARITY[self.kind]:
            raise ValueError(f"a {self.kind} guard needs {_KIND_ARITY[self.kind]} column(s), got {self.columns!r}")
        self._check_parameters()
        if self.kind in ("exactly_one", "order", "max_days") and self.columns[0] == self.columns[1]:
            raise ValueError(f"a {self.kind} guard needs two different columns, got {self.columns!r}")
        if self.kind == "one_of":
            if not self.values:
                raise ValueError("a one_of guard needs values")
            for v in self.values:
                _check_scalar(v)
            self._check_literals(self.columns[0], self.values)
        if self.kind == "max_days" and (not isinstance(self.days, int) or isinstance(self.days, bool) or self.days < 0):
            raise ValueError("a max_days guard needs days >= 0")
        if self.kind == "range":
            if self.lo is None and self.hi is None:
                raise ValueError("a range guard needs lo or hi")
            for b in (self.lo, self.hi):
                if b is not None:
                    _check_scalar(b)
                    if isinstance(b, (str, bool)):
                        raise ValueError("range bounds must be numbers")
            if self.lo_exclusive and self.lo is None:
                raise ValueError("lo_exclusive needs lo")
            ctype = column_type(self.table, self.columns[0])
            if ctype is not None and ctype not in (INT, FLOAT):
                raise ValueError(f"a range guard needs a numeric column, {self.table}.{self.columns[0]} is {ctype}")
            if self.lo is not None and self.hi is not None:
                if self.lo > self.hi or (self.lo == self.hi and self.lo_exclusive):
                    raise ValueError(
                        f"a range guard needs lo <= hi (and a non-empty range), got {self.lo!r}..{self.hi!r}"
                    )
        if self.kind == "reference":
            if self.target not in TABLES or TABLES[self.target].pk != "id":
                raise ValueError(f"a reference guard needs a target table with pk id, got {self.target!r}")
            if self.code != "ref.dangling":
                raise ValueError(f"a reference guard's code must be 'ref.dangling', got {self.code!r}")
            if self.table in TABLES and not TABLES[self.table].column(self.columns[0]):
                raise ValueError(f"{self.table}.{self.columns[0]} does not exist")
        if self.kind == "append_only" and self.code != "state.append_only":
            raise ValueError(f"an append_only guard's code must be 'state.append_only', got {self.code!r}")
        if self.kind == "set_once" and self.code != "state.set_once":
            raise ValueError(f"a set_once guard's code must be 'state.set_once', got {self.code!r}")
        if self.kind == "frozen_when":
            self._check_frozen_when()
        if self.kind == "implies":
            if self.code != f"state.implies:{self.name}":
                raise ValueError(f"an implies guard's code must be 'state.implies:{self.name}', got {self.code!r}")
            for cond in (self.if_, self.then):
                if isinstance(cond, Eq):
                    self._check_literals(cond.col, (cond.value,))
                elif isinstance(cond, In):
                    self._check_literals(cond.col, cond.values)

    def _check_frozen_when(self) -> None:
        if self.code != "state.frozen":
            raise ValueError(f"a frozen_when guard's code must be 'state.frozen', got {self.code!r}")
        if len(self.values) != 1:
            raise ValueError("a frozen_when guard needs exactly one value (the frozen state)")
        _check_scalar(self.values[0])
        self._check_literals(self.columns[0], self.values)
        state = self.columns[0]
        named = (*self.mutable, *self.reopen_clears, *([self.reopen_if_null] if self.reopen_if_null else []))
        if state in named:
            raise ValueError("the state column cannot be mutable, cleared or the reopen condition")
        if set(self.mutable) & set(self.reopen_clears):
            raise ValueError("a column cannot be both mutable and cleared on reopen")
        if self.reopen_clears and self.reopen_if_null is None:
            raise ValueError("reopen_clears needs reopen_if_null")
        if bool(self.exempt_when) != (self.exempt_until_set is not None):
            raise ValueError("exempt_when and exempt_until_set go together")
        exempt_cols = [c for c, _ in self.exempt_when]
        for c, v in self.exempt_when:
            _check_scalar(v)
            self._check_literals(c, (v,))
        if self.exempt_until_set is not None:
            exempt_cols.append(self.exempt_until_set)
        if state in exempt_cols:
            raise ValueError("the state column cannot be part of the exemption")
        table = TABLES.get(self.table)
        if table is not None:
            for c in (state, *named, *exempt_cols):
                if not table.column(c):
                    raise ValueError(f"{self.table}.{c} does not exist")

    def _check_parameters(self) -> None:
        """Reject parameters that the kind does not use (they would be silently ignored)."""
        used = {
            "one_of": {"values"},
            "range": {"lo", "hi", "lo_exclusive"},
            "max_days": {"days"},
            "implies": {"if_", "then"},
            "reference": {"target"},
            "frozen_when": {
                "values",
                "mutable",
                "reopen_if_null",
                "reopen_clears",
                "exempt_when",
                "exempt_until_set",
            },
        }
        defaults = {
            "values": (),
            "lo": None,
            "hi": None,
            "lo_exclusive": False,
            "days": None,
            "if_": None,
            "then": None,
            "target": None,
            "mutable": (),
            "reopen_if_null": None,
            "reopen_clears": (),
            "exempt_when": (),
            "exempt_until_set": None,
        }
        for param, default in defaults.items():
            if param not in used.get(self.kind, set()) and getattr(self, param) != default:
                raise ValueError(f"a {self.kind} guard does not take {param}")

    def _check_literals(self, col: str, values: tuple[Any, ...]) -> None:
        """A literal compared with a known column must have the column's logical type (no affinity surprises)."""
        ctype = column_type(self.table, col)
        if ctype is None or ctype == JSON:
            return
        for v in values:
            if ctype in (ID, STR, TEXT):
                ok = isinstance(v, str)
            elif ctype == BOOL:
                ok = isinstance(v, bool)
            else:
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            if not ok:
                raise ValueError(f"{self.table}.{col} is {ctype}: literal {v!r} has the wrong type")

    @property
    def read_columns(self) -> tuple[str, ...]:
        """All columns the guard reads (grandfathering clause and UPDATE OF list)."""
        return self.columns


def one_of_from_literal(name: str, table: str, col: str, literal_type: Any, code: str = "vocab.unknown") -> Guard:
    """A `one_of` guard whose values are the members of a `typing.Literal` (never typed twice)."""
    if get_origin(literal_type) is not Literal:
        raise ValueError(f"{literal_type!r} is not a Literal type")
    return Guard(name, table, "one_of", (col,), code, values=get_args(literal_type))


_DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "entries": ("date",),
    "invoices": ("date", "due_date", "valid_until", "paid_date", "service_from", "service_to"),
    "offers": ("date", "due_date", "valid_until", "paid_date", "service_from", "service_to"),
    "expenses": ("date", "paid_on", "service_date"),
    "absences": ("start", "end"),
    "contracts": ("start", "end"),
    "projects": ("start", "end"),
    "allocations": ("start", "end", "cancelled_from"),
    "incoming_offers": ("received_on", "valid_until"),
}
_NON_NEGATIVE_COLUMNS: dict[str, tuple[str, ...]] = {
    "entries": ("minutes", "rate"),
    "customers": ("rate",),
    "projects": ("rate", "budget_hours"),
    "tasks": ("rate",),
    "contracts": ("rate", "hours_per_week", "total_hours"),
    "allocations": ("rate", "hours_per_week", "total_hours"),
    "suppliers": ("rate",),
    "expenses": ("net", "vat"),
}
# guarded references (docs/specs/db-guards-v2.md section 3): (table, column, target table)
_REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("allocations", "project_id", "projects"),
    ("allocations", "user_id", "users"),
    ("allocations", "supplier_id", "suppliers"),
    ("contracts", "project_id", "projects"),
    ("entries", "project_id", "projects"),
    ("offers", "project_id", "projects"),
    ("invoices", "project_id", "projects"),
    ("tasks", "project_id", "projects"),
    ("expenses", "project_id", "projects"),
    ("incoming_offers", "project_id", "projects"),
    ("trips", "project_id", "projects"),
    ("timers", "project_id", "projects"),
)
_ORDER_TABLES = ("absences", "contracts", "projects", "allocations", "trips")


def _build_guards() -> tuple[Guard, ...]:
    out = [
        Guard(f"guard_{t}_number_unique", t, "unique", ("number",), "numbering.duplicate")
        for t in ("invoices", "offers")
    ]
    out += [
        Guard(f"guard_{t}_{c}_date", t, "date", (c,), "format.date") for t, cols in _DATE_COLUMNS.items() for c in cols
    ]
    out += [
        Guard(f"guard_{t}_{c}_nonneg", t, "non_negative", (c,), "range.negative")
        for t, cols in _NON_NEGATIVE_COLUMNS.items()
        for c in cols
    ]
    out += [
        Guard(f"guard_{t}_period_order", t, "order", ("start", "end"), "state.period_reversed") for t in _ORDER_TABLES
    ]
    out += [
        Guard(f"guard_{t}_service_order", t, "order", ("service_from", "service_to"), "state.period_reversed")
        for t in ("invoices", "offers")
    ]
    out += [Guard(f"guard_{t}_{c}_ref", t, "reference", (c,), "ref.dangling", target=g) for t, c, g in _REFERENCES]
    out += _planning_guards()  # migration 0018-project_planning, after 0017-reference_guards
    out += _accounting_guards()  # migration 0019-accounting_journal
    out += _accounting_document_guards()  # migration 0020-accounting_documents
    out += _accounting_row_state_guards()  # migration 0021-accounting_row_state
    return tuple(out)


def _accounting_guards() -> list[Guard]:
    """Guards of migration accounting_journal (docs/specs/accounting.md sections 7 and 8.1)."""
    from permita38.core.accounting.types import IncomeBasis, VatBasis
    from permita38.core.vat_rules import TAX_KEYS, TaxKind

    tax_keys = tuple(sorted(TAX_KEYS))  # all keys of docs/specs/accounting.md section 6, incl. NONE
    vat_keys = tuple(sorted(TAX_KEYS - {"NONE"}))  # the keys that need a VAT date

    def required(table: str, *cols: str) -> list[Guard]:
        return [Guard(f"guard_{table}_{c}_required", table, "required", (c,), "state.required") for c in cols]

    def dates(table: str, *cols: str) -> list[Guard]:
        return [Guard(f"guard_{table}_{c}_date", table, "date", (c,), "format.date") for c in cols]

    def one_of(table: str, col: str, values: tuple[str, ...]) -> Guard:
        return Guard(f"guard_{table}_{col}_one_of", table, "one_of", (col,), "vocab.unknown", values=values)

    def implies(table: str, stem: str, if_: Condition, then: Condition) -> Guard:
        name = f"guard_{table}_{stem}_implies"
        return Guard(name, table, "implies", (), f"state.implies:{name}", if_=if_, then=then)

    def bounds(table: str, col: str, lo: int, hi: int | None = None) -> Guard:
        return Guard(f"guard_{table}_{col}_range", table, "range", (col,), "range.bounds", lo=lo, hi=hi)

    def ref(table: str, col: str, target: str) -> Guard:
        return Guard(f"guard_{table}_{col}_ref", table, "reference", (col,), "ref.dangling", target=target)

    out: list[Guard] = []
    out += required(
        "fiscal_years", "year", "chart", "income_basis", "vat_basis", "vat_frequency", "dauerfrist",
        "small_business", "rules_version", "opened_at", "opened_by",
    )  # fmt: skip
    out += dates("fiscal_years", "vat_regular_from")
    out += [
        one_of("fiscal_years", "chart", ("skr03", "skr04")),
        one_of("fiscal_years", "income_basis", get_args(IncomeBasis)),
        one_of("fiscal_years", "vat_basis", get_args(VatBasis)),
        one_of("fiscal_years", "vat_frequency", ("month", "quarter", "none")),
        implies("fiscal_years", "closed_by", IsSet("closed_at"), IsSet("closed_by")),
        implies("fiscal_years", "closed_at", IsSet("closed_by"), IsSet("closed_at")),
        bounds("fiscal_years", "year", 2000, 2100),
        bounds("fiscal_years", "closing_entry_no", 0, 2_000_000_000),
    ]
    out += required(
        "journal_entries", "fiscal_year", "entry_no", "document_date", "booking_date", "event", "event_key",
        "text", "origin", "created_at", "created_by", "prev_hash", "hash",
    )  # fmt: skip
    out += dates("journal_entries", "document_date", "booking_date")
    out += [
        one_of("journal_entries", "origin", ("auto", "manual", "opening")),
        bounds("journal_entries", "fiscal_year", 2000, 2100),
        bounds("journal_entries", "entry_no", 1),
        ref("journal_entries", "reverses_id", "journal_entries"),
        Guard("guard_journal_entries_append_only", "journal_entries", "append_only", (), "state.append_only"),
    ]
    out += required("journal_lines", "entry_id", "line_no", "account", "amount_cents")
    out += dates("journal_lines", "vat_date", "income_date")
    out += [
        one_of("journal_lines", "tax_kind", get_args(TaxKind)),
        one_of("journal_lines", "tax_key", tax_keys),
        bounds("journal_lines", "line_no", 1),
        ref("journal_lines", "entry_id", "journal_entries"),
        implies("journal_lines", "tax_kind_key", IsSet("tax_kind"), IsSet("tax_key")),
        implies("journal_lines", "tax_key_vat_date", In("tax_key", vat_keys), IsSet("vat_date")),
        Guard("guard_journal_lines_append_only", "journal_lines", "append_only", (), "state.append_only"),
    ]
    out += required("payments", "document_id", "date", "amount_cents", "method", "kind", "created_at", "created_by")
    out += dates("payments", "date")
    out += [
        one_of("payments", "method", ("bank", "cash")),
        one_of("payments", "kind", ("payment", "refund", "reversal")),
        ref("payments", "document_id", "invoices"),
        ref("payments", "reverses_id", "payments"),
        implies("payments", "reversal_reverses", Eq("kind", "reversal"), IsSet("reverses_id")),
        implies("payments", "reverses_reversal", IsSet("reverses_id"), Eq("kind", "reversal")),
        Guard("guard_payments_append_only", "payments", "append_only", (), "state.append_only"),
    ]
    out += required("period_locks", "year", "month", "locked_at", "locked_by", "reason")
    out += [
        Guard("guard_period_locks_month_range", "period_locks", "range", ("month",), "range.bounds", lo=1, hi=12),
        Guard("guard_period_locks_append_only", "period_locks", "append_only", (), "state.append_only"),
    ]
    return out


def _accounting_document_guards() -> list[Guard]:
    """Guards of migration accounting_documents (docs/specs/accounting.md section 8.1 and 8.2)."""
    from permita38.core.accounting.postings_expense import ExpenseTreatment
    from permita38.core.accounting.types import CreditKind, Treatment, VatBasis

    def required(table: str, *cols: str) -> list[Guard]:
        return [Guard(f"guard_{table}_{c}_required", table, "required", (c,), "state.required") for c in cols]

    def dates(table: str, *cols: str) -> list[Guard]:
        return [Guard(f"guard_{table}_{c}_date", table, "date", (c,), "format.date") for c in cols]

    def one_of(table: str, col: str, values: tuple[str, ...]) -> Guard:
        return Guard(f"guard_{table}_{col}_one_of", table, "one_of", (col,), "vocab.unknown", values=values)

    def bounds(table: str, col: str, lo: int, hi: int | None = None) -> Guard:
        return Guard(f"guard_{table}_{col}_range", table, "range", (col,), "range.bounds", lo=lo, hi=hi)

    def ref(table: str, col: str, target: str) -> Guard:
        return Guard(f"guard_{table}_{col}_ref", table, "reference", (col,), "ref.dangling", target=target)

    def implies(table: str, stem: str, if_: Condition, then: Condition) -> Guard:
        name = f"guard_{table}_{stem}_implies"
        return Guard(name, table, "implies", (), f"state.implies:{name}", if_=if_, then=then)

    out: list[Guard] = [
        one_of("invoices", "kind", ("invoice", "credit_note")),
        one_of("invoices", "credit_kind", get_args(CreditKind)),
        implies("invoices", "credit_kind_credit_note", IsSet("credit_kind"), Eq("kind", "credit_note")),
        one_of("invoices", "tax_treatment", get_args(Treatment)),  # the reserved treatments are refused
        ref("invoices", "corrects_id", "invoices"),
        one_of("expenses", "tax_treatment", get_args(ExpenseTreatment)),
        bounds("expenses", "bill_rev", 0),
        bounds("expenses", "pay_rev", 0),
        bounds("trips", "post_rev", 0),
    ]
    out += required("vat_returns", "year", "period_kind", "period_no", "from_date", "to_date", "due_date", "status")
    out += dates("vat_returns", "from_date", "to_date", "due_date", "filed_on")
    out += [
        one_of("vat_returns", "period_kind", ("month", "quarter")),
        one_of("vat_returns", "status", ("draft", "final")),
        one_of("vat_returns", "basis", get_args(VatBasis)),
        one_of("vat_returns", "frequency", ("month", "quarter", "none")),
        bounds("vat_returns", "year", 2000, 2100),
        bounds("vat_returns", "period_no", 1, 12),
        Guard(
            "guard_vat_returns_period_order", "vat_returns", "order", ("from_date", "to_date"), "state.period_reversed"
        ),
        implies("vat_returns", "final_finalised_at", Eq("status", "final"), IsSet("finalised_at")),
        implies("vat_returns", "final_finalised_by", Eq("status", "final"), IsSet("finalised_by")),
        ref("vat_returns", "amends_id", "vat_returns"),
        ref("vat_returns", "replaced_by", "vat_returns"),
    ]
    out += required("zm_reports", "year", "quarter", "status", "due_date")
    out += dates("zm_reports", "due_date", "filed_on")
    out += [
        one_of("zm_reports", "status", ("draft", "final")),
        bounds("zm_reports", "year", 2000, 2100),
        bounds("zm_reports", "quarter", 1, 4),
        implies("zm_reports", "final_finalised_at", Eq("status", "final"), IsSet("finalised_at")),
        implies("zm_reports", "final_finalised_by", Eq("status", "final"), IsSet("finalised_by")),
        ref("zm_reports", "amends_id", "zm_reports"),
        ref("zm_reports", "replaced_by", "zm_reports"),
    ]
    return out


def _accounting_row_state_guards() -> list[Guard]:
    """Guards of migration accounting_row_state (docs/specs/accounting.md section 8.1, docs/specs/db-constraints.md)."""
    mutable = ("filed_on", "reference", "note", "replaced_by", "updated")
    clears = (
        "finalised_at",
        "finalised_by",
        "boxes",
        "source_lines",
        "result_cents",
        "special_prepayment_credit_cents",
        "rounding_difference_cents",
    )

    def frozen(table: str, clears_: tuple[str, ...], **exempt: Any) -> Guard:
        return Guard(
            f"guard_{table}_frozen",
            table,
            "frozen_when",
            ("status",),
            "state.frozen",
            values=("final",),
            mutable=mutable,
            reopen_if_null="filed_on",
            reopen_clears=clears_,
            **exempt,
        )

    return [
        frozen("vat_returns", clears, exempt_when=(("external", True),), exempt_until_set="replaced_by"),
        frozen("zm_reports", ("finalised_at", "finalised_by", "lines", "source_lines")),
        Guard("guard_vat_returns_filed_on_once", "vat_returns", "set_once", ("filed_on",), "state.set_once"),
        Guard("guard_zm_reports_filed_on_once", "zm_reports", "set_once", ("filed_on",), "state.set_once"),
        Guard("guard_fiscal_years_closed_at_once", "fiscal_years", "set_once", ("closed_at",), "state.set_once"),
        Guard("guard_fiscal_years_closed_by_once", "fiscal_years", "set_once", ("closed_by",), "state.set_once"),
        Guard("guard_fiscal_years_closing_hash_once", "fiscal_years", "set_once", ("closing_hash",), "state.set_once"),
        Guard(
            "guard_fiscal_years_closing_entry_no_once",
            "fiscal_years",
            "set_once",
            ("closing_entry_no",),
            "state.set_once",
        ),
    ]


def _model_literal(model: Any, field_name: str) -> Any:
    """The `Literal` of a pydantic field (a `Literal | None` is unwrapped), so a vocabulary is never typed twice."""
    ann = model.model_fields[field_name].annotation
    for arg in (ann, *get_args(ann)):
        if get_origin(arg) is Literal:
            return arg
    raise ValueError(f"{model.__name__}.{field_name} is not a Literal")


def _planning_guards() -> list[Guard]:
    """Guards of migration project_planning (docs/specs/project-centric-planning.md, "Strict schema")."""
    from permita38.core.models import ContractIn, ProjectIn  # late: models do not import the repository

    def implies(table: str, stem: str, if_: Condition, then: Condition) -> Guard:
        name = f"guard_{table}_{stem}_implies"
        return Guard(name, table, "implies", (), f"state.implies:{name}", if_=if_, then=then)

    def required(table: str, col: str) -> Guard:
        return Guard(f"guard_{table}_{col}_required", table, "required", (col,), "state.required")

    def rng(table: str, col: str, lo: float, hi: float, exclusive: bool) -> Guard:
        return Guard(
            f"guard_{table}_{col}_range", table, "range", (col,), "range.bounds", lo=lo, hi=hi, lo_exclusive=exclusive
        )

    def exactly_one(table: str) -> Guard:
        return Guard(
            f"guard_{table}_hours_exactly_one",
            table,
            "exactly_one",
            ("hours_per_week", "total_hours"),
            "state.exactly_one",
        )

    out = [
        required("projects", "kind"),
        one_of_from_literal("guard_projects_kind_one_of", "projects", "kind", _model_literal(ProjectIn, "kind")),
        one_of_from_literal("guard_projects_status_one_of", "projects", "status", _model_literal(ProjectIn, "status")),
        required("projects", "name"),
        implies("projects", "internal_customer", Eq("kind", "internal"), IsBlank("customer_id")),
        rng("projects", "budget_hours", 0, 100000, True),
    ]
    out += [required("allocations", c) for c in ("project_id", "start", "end")]
    out += [
        Guard("guard_allocations_status_one_of", "allocations", "one_of", ("status",), "vocab.unknown",
              values=("active", "cancelled")),
        exactly_one("allocations"),
        rng("allocations", "hours_per_week", 0, 80, True),
        rng("allocations", "total_hours", 0, 100000, True),
        Guard("guard_allocations_period_max_days", "allocations", "max_days", ("start", "end"), "range.too_long",
              days=1830),
        rng("allocations", "rate", 0, 10000, False),
        implies("allocations", "cancelled_from", Eq("status", "cancelled"), IsSet("cancelled_from")),
        implies("allocations", "active_no_cancelled_from", Eq("status", "active"), IsBlank("cancelled_from")),
        implies("allocations", "supplier_rate", IsSet("supplier_id"), IsSet("rate")),
        implies("allocations", "user_no_incoming_offer", IsSet("user_id"), IsBlank("incoming_offer_id")),
    ]  # fmt: skip
    out += [required("contracts", c) for c in ("title", "start", "end")]
    out += [
        one_of_from_literal(
            "guard_contracts_status_one_of", "contracts", "status", _model_literal(ContractIn, "status")
        ),
        exactly_one("contracts"),
        rng("contracts", "hours_per_week", 0, 10000, True),
        rng("contracts", "total_hours", 0, 100000, True),
        rng("contracts", "rate", 0, 10000, False),
    ]
    return out


# trips.start / trips.end are local datetimes ("YYYY-MM-DDTHH:MM"): only the order guard, no date format guard
GUARDS: tuple[Guard, ...] = _build_guards()

#: table CHECK constraints declared for the drift test; the SQL stays in the migration that created them
CHECKS = {
    "allocations": (
        "allocations_one_owner",
        '("user_id" IS NOT NULL AND "supplier_id" IS NULL) OR ("user_id" IS NULL AND "supplier_id" IS NOT NULL)',
    ),
    "payments": ("payments_amount_nonzero", '"amount_cents" <> 0'),
    "vat_returns": (
        "vat_returns_amends_key",
        '"amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", \'\')',
    ),
    # a quarter has the periods 1..4 (the guard range 1..12 covers the month)
    "vat_returns.quarter_no": ("vat_returns_quarter_no", '"period_kind" <> \'quarter\' OR "period_no" BETWEEN 1 AND 4'),
    "zm_reports": ("zm_reports_amends_key", '"amends_key" IS NOT NULL AND "amends_key" = COALESCE("amends_id", \'\')'),
    "period_locks": (
        "period_locks_reason_vocab",
        "\"reason\" IN ('manual', 'year_close') OR \"reason\" LIKE 'vat_return:%'",
    ),
}

# internal per-user collections that are not exposed through the CRUD API
INTERNAL_COLLECTIONS = [
    "allocations",
    "api_tokens",
    "audit_log",
    "timers",
    "number_sequences",
    "numbering_log",
    "import_batches",
    "plan_scenarios",
    "calendar_sources",
    "calendar_events",
    "receipt_scans",
    "document_scans",
    "fiscal_years",
    "journal_entries",
    "journal_lines",
    "payments",
    "period_locks",
    "vat_returns",
    "zm_reports",
]
# the user-facing record collections
COLLECTIONS = [name for name in TABLES if name not in ("settings", *INTERNAL_COLLECTIONS)]
