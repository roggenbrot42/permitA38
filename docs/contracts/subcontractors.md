# API contract: suppliers, subcontractors and incoming offers

Source of truth for the backend, the web UI and the tests, so that they can be built in parallel. Behaviour, rules and
the product owner's decisions: `docs/specs/subcontractors.md` (the "spec"); the planner-per-project feature
(`docs/specs/planner-per-project.md`, `docs/contracts/planner-per-project.md`) is **already on `main`**. Where this file
and the spec differ on the HTTP shape or on a name, this file wins. Conventions are those of
`docs/contracts/planner-per-project.md`, `docs/contracts/api-tokens.md` and `docs/contracts/trips.md`: base path `/api`,
JSON, session cookie or `Authorization: Bearer pat_...`; errors are `{"detail": "<message>"}` for 401/403/404/409 and
service-level 400/422; body/query validation errors (422) are
`{"detail": [{"loc": ["body","weekly_hours"], "msg": "...", "type": "..."}]}` (input is never echoed). Dates
`YYYY-MM-DD`, weeks are identified by their Monday. Money is a JSON number with at most 2 decimals, computed only with
`core/money.py` (exact decimals, half-up cents). Hours are JSON numbers (6 decimals in planner rows, 2 in allocation
figures). Review tier: **Full** (see the spec). All tax and legal statements stay marked *(verify)* in UI texts.

## Decisions fixed by this contract (not open for the implementer)

1. **Two new areas** `suppliers` and `procurement` in `core/rights.py`. The planner routes keep the area `planning`.
2. **Suppliers are a normal collection with a dedicated router** (`/api/suppliers`, path parameter `supplier_id`), not the
   generic `crud_router`, because of the filters, the limited employee view and the static sub-routes. Incoming offers
   have their own router (`/api/incoming-offers`, path parameter `incoming_offer_id`).
3. **The `allocations` table, its employee routes and the planner engine's `internal`/`external` pools exist.** This
   feature adds to them, it creates no allocation table (section "What exists, what changes").
4. **Synthetic planner members are NOT put into `World.users`** (the spec says so; this contract overrides it, see
   open decision 1). `World` gets a second map `externals: dict[supplier_id, dict]`. This keeps every
   place that iterates `world.users` (vacation planner, entitlement, over-allocation of employees, `user_list`)
   unchanged by construction.
5. **A cancelled external allocation keeps its days before `cancelled_from`.** The engine therefore loads external
   allocations with status `active` **and** `cancelled`; the effective last day is `cancelled_from - 1 day`.
6. **External allocations are created and removed only by the incoming-offer service** (accept / unaccept) and
   shortened only by `cancel`. `POST/PUT/DELETE /api/allocations` keep answering 409 "This allocation cannot be changed
   here" for them (unchanged).
7. **Nothing in this feature writes a number into the VAT return** or any report built on actual expenses. A
   commitment is derived, never an expense.
8. **Warnings never block** (shape `{code, severity, field, message, details, confirmable}`); the only confirmable one
   is `allocation.exceeds_weekly_hours`. Hard rules are the listed 409/422.

## What exists on `main`, what this feature changes

| Existing (do not re-create) | Change by this feature |
|---|---|
| table `allocations` (migration `0011-allocations`, id `72306069`; nullable `user_id`/`supplier_id`, check `allocations_one_owner`, indexes `idx_allocations_user`, `idx_allocations_project`; status default `active`) | Only two indexes are added (`supplier_id`, `status`). Columns unchanged. |
| `models.Allocation` (lenient; has `supplier_id`, `incoming_offer_id`, `supplier_name`, `rate`, `total_hours`, `cancelled_from`, `cancel_reason`, `planned_hours`) | gains response-only `planned_net`, `invoiced_net`, `open_net`, `days` (detail route), `offer_reference`. |
| `services.allocations.AllocationService._planned_hours` returns `None` for an external row; `_present` sets `kind: "external"`, no `supplier_name` | external rows get `planned_hours`, the money figures and `supplier_name`. |
| `GET /api/allocations` (area `planning` read, boss only); external rows already included for callers with `procurement` read | unchanged route and rights; the external rows now carry the new figures. |
| `POST/PUT/DELETE /api/allocations*` for employees; 409 for external rows | unchanged. |
| `planning.usable_allocation` (employee rows only) and `World.allocations` (employee, active) | new `World.external_allocations` (`usable_external_allocation`: `supplier_id` set, project set, valid period; status `active` or `cancelled`). |
| `planning.day_table` builds `ScopeDay.internal/external` pools; the external pool is always zero; item demand always goes to `internal` | external pool filled from `external_allocations`; demand of an external assignee goes to the external pool. `Day.cap` includes external capacity. |
| week row `projects[].external` `{capacity, assigned, gap}` and `projects[].external_cost` (always `0.0`) | filled with real values. |
| `planning.make()` keeps only assignees that are in `world.users`; a contract with no known assignee falls back to the boss | assignees may also be keys of `world.externals`. |
| `GET /api/plan/day-table`, `GET /api/forecast`, `POST /api/plan/simulate` | optional `include_external` (default on = the new behaviour, off = the old world), flat `external_*` row fields, totals. |
| `AllocationService.bootstrap` | must skip assignees that are external members (employees only). |
| `ExpenseIn`/`Expense`; sanity rule `expense.possible_duplicate` (compares the lower-cased `vendor`) | gain `supplier_id`, `allocation_id`, `reverse_charge`; duplicate rule compares `supplier_id` when both rows have one. |

## Shared types

| Name | JSON | Rule |
|---|---|---|
| `Id` | string | `^[0-9a-z]{6,32}$` (`v.Id`); `""` is read as `null` for optional ids (`v.OptId`) |
| `SupplierKind` | string | `"company"`, `"person"`, `"person-capacity"` (= subcontractor) |
| `VatMode` | string | `"domestic"`, `"eu_reverse_charge"`, `"third_country_reverse_charge"`, `"small_business"` |
| `OfferStatus` | string | stored: `"received"`, `"under_review"`, `"accepted"`, `"rejected"`; derived `effective_status` adds `"expired"` |
| `Country` (new `v.Country`) | string | `^[A-Z]{2}$` |
| `Iban` (new `v.Iban`) | string | up to 34 chars, spaces allowed on input and removed when stored, `^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$` (checksum is a warning, not a bound) |
| `Weekdays` (new `v.Weekdays`) | list of int | 1 to 7 distinct values, each 0 (Monday) to 6 |
| `Tags` (new `v.Tags`) | list of string | at most 20 items, each 1 to 50 chars after trimming, duplicates (case-insensitive) removed |
| `PayDays` (new `v.PayDays`) | int | 0 to 365 |
| `NonNegQty` (new `v.NonNegQty`) | number | 0 to 1e7, finite |
| `AllocHours` (existing) | number | 0.25 to 80 |

## Rights

### Areas and matrix (`core/rights.py`, and the generated table in `docs/roles.md`)

```python
AREAS["suppliers"] = "Suppliers and subcontractors (master data, vendor adoption)"
AREAS["procurement"] = "Incoming offers, supplier allocations, commitments"
```

| Area | boss (read / write) | employee | accountant |
|---|---|---|---|
| `suppliers` | all / all | limited / none | all / all |
| `procurement` | all / all | none / none | all / none |

`MATRIX` entries: `BOSS` is generated (`(A, A)`), `EMPLOYEE["suppliers"] = (L, N)`, `EMPLOYEE["procurement"] = (N, N)`,
`ACCOUNTANT["suppliers"] = (A, A)`, `ACCOUNTANT["procurement"] = (A, N)`. `COLLECTION_AREA` is **not** extended (no
generic CRUD for these collections).

`limited` read of `suppliers` (employee) means the **limited view**: `{id, name, kind, tags, active}` and nothing else,
on list and detail, on all `GET /api/suppliers*` routes the employee may call. Filters `kind`, `active`, `tag` work, `q`
matches the name only. The accountant sees everything, writes suppliers and adopts vendors, but cannot accept, reject,
unaccept, cancel or edit offers (`procurement` write `none`). Accepting and everything else under `procurement` write is
boss only. Employee and accountant keep `planning` = `none`.

### Route table and `tests/backend/test_rbac.py` `EXPECTED`

`declared(route)` must find exactly one `Needs(...)` per route (the test asserts `len == 1`), so no route below needs
two areas. Hand-written rows to add to `EXPECTED` (21 rows):

```python
EXPECTED_SUBCONTRACTORS = {
    ("GET", "/api/suppliers"): ("suppliers", "read", False),
    ("POST", "/api/suppliers"): ("suppliers", "write", False),
    ("GET", "/api/suppliers/vendor-suggestions"): ("suppliers", "write", False),
    ("POST", "/api/suppliers/adopt-vendors"): ("suppliers", "write", False),
    ("GET", "/api/suppliers/{supplier_id}"): ("suppliers", "read", False),
    ("PUT", "/api/suppliers/{supplier_id}"): ("suppliers", "write", False),
    ("DELETE", "/api/suppliers/{supplier_id}"): ("suppliers", "write", False),
    ("GET", "/api/suppliers/{supplier_id}/dependency"): ("procurement", "read", False),
    ("GET", "/api/incoming-offers"): ("procurement", "read", False),
    ("POST", "/api/incoming-offers"): ("procurement", "write", False),
    ("GET", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "read", False),
    ("PUT", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "write", False),
    ("DELETE", "/api/incoming-offers/{incoming_offer_id}"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/review"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/accept"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/reject"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/reopen"): ("procurement", "write", False),
    ("POST", "/api/incoming-offers/{incoming_offer_id}/unaccept"): ("procurement", "write", False),
    ("GET", "/api/allocations/{allocation_id}"): ("procurement", "read", False),
    ("POST", "/api/allocations/{allocation_id}/cancel"): ("procurement", "write", False),
    ("GET", "/api/finance/commitments"): ("procurement", "read", False),
}
```

Notes for the matrix test (`test_every_route_is_denied_exactly_where_the_matrix_says`): `GET /api/suppliers` and
`GET /api/suppliers/{supplier_id}` pass for the employee (limited is not none), every other row is 403 for the roles
without the area. `vendor-suggestions` is declared `suppliers` **write** although it is a GET (it exposes expense
vendors); employee is 403, a read-only token still gets it (safe request, the owner's scope applies, see
`rights.scope_for`). The existing row `("GET", "/api/allocations"): ("planning", "read", False)` stays as it is: the
accountant reads allocations through `GET /api/allocations/{allocation_id}` and `GET /api/finance/commitments`
(open decision 2). `docs/roles.md` lists the new routes under their areas.

### Tokens

* **No route of this feature is session-only.** `SESSION_ONLY` in the RBAC test is unchanged.
* Tokens never exceed the owner's rights. A **read-only** token (boss or accountant) can `GET` every route above; every
  `POST/PUT/DELETE` is 403 for it, including `POST /api/suppliers/adopt-vendors` with `dry_run: true` (it is a write
  route, `dry_run` is only a mode) and every status route. An employee's token can list/get suppliers in the limited
  view only.
* A read-write token of the boss may call accept, reject, reopen, unaccept and cancel (rights allow it). The MCP server
  (`docs/specs/mcp-server.md`) exposes these only behind its write-tool confirmation and lists read tools
  `list_suppliers`, `get_supplier`, `list_incoming_offers`, `list_allocations` (same rights as the HTTP routes).
* The audit log records the route template (`/api/incoming-offers/{incoming_offer_id}/accept`), never ids or bodies;
  the IBAN, BIC and tax numbers of a supplier are never logged.

## Storage and the single migration `0012-suppliers`

Collections `suppliers` and `incoming_offers` are registered in `core/repository/schema.py` (`TABLES`) like `trips`
(generic `list/get/save/find`, **no new `Repository` method**), appear in the data export, and are covered by
`tests/backend/test_repository_contract.py` (round-trip, unknown fields in `extra`). `allocations` stays in
`INTERNAL_COLLECTIONS`.

```text
suppliers        _BASE + name:str supplier_no:str kind:str active:bool street:str zip:str city:str country:str
                 vat_id:str tax_number:str vat_mode:str contact:str email:str phone:str notes:text iban:str bic:str
                 bank_name:str payment_terms_days:int payment_terms_note:str tags:json weekly_hours:float
                 workdays:json rate:float unavailable:json login_user_id:id external_id:str import_batch:str + _EXTRA
incoming_offers  _BASE + supplier_id:id reference:str title:str received_on:str valid_until:str lines:json
                 net_total:float vat:float vat_rate:float vat_mode:str gross_total:float plan:json project_id:id
                 contract_id:id customer_id:id attachment:str status:str status_changed_at:str decided_by:id
                 decision_note:text allocation_id:id notes:text external_id:str import_batch:str + _EXTRA
expenses (+3)    supplier_id:id  allocation_id:id  reverse_charge:bool
indexes          idx_suppliers_name(name), idx_suppliers_external_id(external_id),
                 idx_incoming_offers_supplier(supplier_id), idx_incoming_offers_status(status),
                 idx_allocations_supplier(supplier_id), idx_allocations_status(status),
                 idx_expenses_supplier(supplier_id), idx_expenses_allocation(allocation_id)
```

* Created with `permita38 create-migration suppliers` in **all three** dialect folders: `0012-suppliers.sql`,
  header `-- migration: <random 8-hex>` / `-- requires: 72306069` (the head at the time of writing; rebase `requires`
  onto the head at merge time if another migration lands first, AGENTS.md). No `BEGIN`/`COMMIT`, rewrites no row, does
  **not** touch the `allocations` columns or the check constraint, and only adds the two allocation indexes.
* Column types are those the dialect generator uses for the DSL types (`id` TEXT/`VARCHAR(32)`, `str` TEXT/
  `VARCHAR(255)`, `text` TEXT/`LONGTEXT`, `int` INTEGER/BIGINT, `float` REAL/`DOUBLE PRECISION`/`DOUBLE`, `bool`
  INTEGER/BOOLEAN/`TINYINT(1)`, `json` TEXT/JSONB/`LONGTEXT`), identical to how `0009-trips.sql` and `0011-allocations.sql`
  spell them. The three expense columns are `ALTER TABLE "expenses" ADD COLUMN ...` (nullable, no default).
* `schema.py` mirrors all of it; the existing drift tests (folders in sync, one head, migrated SQLite equals
  `schema.py`) must stay green. `tests/backend/test_rbac.py::test_migration_renames_member_to_employee_and_keeps_boss`
  drops later tables by name; it must learn to drop `suppliers` and `incoming_offers` too (BE-A).
* Cascade rules: deleting a supplier is **409** while any incoming offer, allocation or expense references it ("archive
  it with active=false"); deleting an `accepted` incoming offer is 409; deleting a project, contract or customer that an
  active external allocation references follows the existing rule (409 while referenced). Deleting a supplier is allowed
  when nothing references it.

## Models (`core/models.py`, `core/validation.py`)

Every request model is a `v.StrictRecord` (unknown fields allowed for forward compatibility, every value checked;
response-only keys sent by a client are **ignored**, never stored) unless it says `forbid`. Every new field goes into
the strict `*In` model **and** the lenient response model with a hard bound (`docs/validation.md`).

```python
class UnavailableIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: v.ISODate = Field(alias="from")
    end: v.ISODate = Field(alias="to")  # end >= start (422)
    note: v.Short | None = None


class SupplierIn(v.StrictRecord):
    name: v.Name
    supplier_no: v.Short | None = None  # a duplicate is a warning
    kind: SupplierKind = "company"
    active: bool = True
    street: v.Short | None = None
    zip: Annotated[str, StringConstraints(max_length=20)] | None = None
    city: v.Short | None = None
    country: v.Country = "DE"
    vat_id: Annotated[str, StringConstraints(max_length=30)] | None = None
    tax_number: Annotated[str, StringConstraints(max_length=50)] | None = None
    vat_mode: VatMode | None = (
        None  # omitted: derived from country (DE domestic, other EU eu_reverse_charge, else third_country_reverse_charge) and stored
    )
    contact: v.Short | None = None
    email: Annotated[str, StringConstraints(max_length=254)] | None = None
    phone: Annotated[str, StringConstraints(max_length=50)] | None = None
    notes: v.Note | None = None
    iban: v.Iban | None = None
    bic: Annotated[str, StringConstraints(max_length=11)] | None = None
    bank_name: v.Short | None = None
    payment_terms_days: v.PayDays | None = None
    payment_terms_note: v.Short | None = None
    tags: v.Tags = []
    # capacity attributes: only for kind "person-capacity" (422 for any non-null value on another kind)
    weekly_hours: v.Hours | None = None  # required and > 0 for person-capacity (422 otherwise)
    workdays: v.Weekdays | None = None  # default [0, 1, 2, 3, 4] for person-capacity
    rate: v.Rate | None = None
    unavailable: Annotated[list[UnavailableIn], Field(max_length=50)] = []
    login_user_id: None = None  # reserved; any non-null value is 422


class Supplier(BaseModel):  # lenient response, extra="allow", response_model_exclude_none
    id: str
    name: str
    kind: str = "company"
    active: bool = True
    # ... every field of SupplierIn as optional, plus:
    created: int | None = None
    updated: int | None = None
    sanity_warnings: list[dict] | None = None  # POST/PUT only


class SupplierLimited(BaseModel):  # the employee's view
    id: str
    name: str
    kind: str
    tags: list[str] = []
    active: bool = True
```

The `GET` routes of suppliers use `response_model=list[Supplier | SupplierLimited]` semantics: the service returns
the limited dict for `rights.scope_for(user, "suppliers") == LIMITED`, the full record otherwise (OpenAPI documents both).

```python
class IncomingLineIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    description: v.Desc = ""
    quantity: v.NonNegQty
    unit: Literal["h", "flat"]
    unit_price: v.NonNegAmount


class IncomingPlanIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    start: v.ISODate
    end: v.ISODate  # end >= start, end - start <= 1830 days (422)
    hours_per_week: v.AllocHours | None = None
    total_hours: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)] | None = None
    # exactly one of hours_per_week / total_hours (422 otherwise)


class IncomingOfferIn(v.StrictRecord):
    supplier_id: v.Id  # must exist and be active (422 "Unknown supplier" / 409 for inactive on create)
    reference: v.Short | None = None
    title: v.Short | None = None
    received_on: v.ISODate
    valid_until: v.OptDate = None  # >= received_on (422)
    lines: Annotated[list[IncomingLineIn], Field(min_length=1, max_length=50)]
    vat_rate: Literal[0, 5, 7, 16, 19] | None = None  # omitted: 19 for domestic, 0 otherwise
    vat_mode: VatMode | None = None  # omitted: the supplier's
    plan: IncomingPlanIn | None = None
    project_id: v.OptId = None  # required for a person-capacity supplier when it is accepted
    contract_id: v.OptId = None  # must be a contract of project_id (422)
    attachment: v.OptAttachment = None
    decision_note: v.Note | None = None
    notes: v.Note | None = None


class IncomingOffer(BaseModel):  # lenient response
    id: str
    supplier_id: str
    supplier_name: str | None = None
    reference: str | None = None
    title: str | None = None
    received_on: str
    valid_until: str | None = None
    lines: list[dict] = []
    hours: float = 0.0  # sum of the quantities of the "h" lines
    net_total: float
    vat_rate: float
    vat: float
    gross_total: float
    vat_mode: str
    plan: dict | None = None
    project_id: str | None = None
    project_name: str | None = None
    contract_id: str | None = None
    customer_id: str | None = None  # derived from the project
    attachment: str | None = None
    status: str
    effective_status: str
    status_changed_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    allocation_id: str | None = None
    notes: str | None = None
    created: int | None = None
    updated: int | None = None
    planning_warnings: list[dict] | None = None  # POST/PUT only


class AcceptIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    confirm_expired: bool = False
    confirm_warnings: Annotated[list[Annotated[str, StringConstraints(max_length=80)]], Field(max_length=20)] = []
    project_id: v.OptId = None  # wins over the offer's project and is written back to it
    contract_id: v.OptId = None
    decision_note: v.Note | None = None


class RejectIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    reason: v.Note | None = None  # stored as decision_note


class CancelAllocationIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    effective_from: v.ISODate
    reason: v.Short | None = None


class AdoptVendorItemIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    vendors: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=200)]], Field(min_length=1, max_length=50)
    ]
    supplier_id: v.OptId = None
    create: SupplierIn | None = None  # exactly one of supplier_id / create (422)


class AdoptVendorsIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True
    items: Annotated[list[AdoptVendorItemIn], Field(min_length=1, max_length=200)]
```

Changes to existing models: `ExpenseIn` gains `supplier_id: v.OptId = None`, `allocation_id: v.OptId = None`,
`reverse_charge: bool = False`; `Expense` gains the same three (all optional). `Allocation` (lenient) gains
`planned_net: float | None`, `invoiced_net: float | None`, `open_net: float | None`, `days: list[dict] | None`,
`offer_reference: str | None`. `ContractIn.user_ids` accepts ids of users **or** of active `person-capacity`
suppliers (other unknown ids keep today's behaviour). `SimulateIn` gains `include_external: bool = True`.
Settings (`SettingsIn`, strict, in the settings extra JSON, no schema change): `subcontractor_hint_hours_per_week`
(number, 1 to 168, default 30), `subcontractor_hint_weeks` (int, 1 to 104, default 12), `customer_dominance_percent`
(number, 1 to 100, default 83).

## Routes

### Suppliers

| Route | Rights | Notes |
|---|---|---|
| `GET /api/suppliers?kind=&active=&tag=&q=` | suppliers read | list sorted by `name` case-insensitive then id. `kind` is a `SupplierKind`, `active` is `true`/`false`, `tag` matches one tag case-insensitively, `q` is a case-insensitive substring of name, supplier_no or contact (employee: name only). Invalid values 422. |
| `POST /api/suppliers` | suppliers write | **201**, the stored supplier, `sanity_warnings[]` |
| `GET /api/suppliers/{supplier_id}` | suppliers read | 404 `"Not found"` |
| `PUT /api/suppliers/{supplier_id}` | suppliers write | whole `SupplierIn`; `kind` may not change away from `person-capacity` while an incoming offer with a plan, or an allocation, references the supplier (409). `active: false` is always allowed. |
| `DELETE /api/suppliers/{supplier_id}` | suppliers write | 204; 409 when referenced |
| `GET /api/suppliers/vendor-suggestions` | suppliers write | below |
| `POST /api/suppliers/adopt-vendors` | suppliers write | below |
| `GET /api/suppliers/{supplier_id}/dependency` | procurement read | below |

Registration order: `vendor-suggestions` and `adopt-vendors` are registered **before** the `/{supplier_id}` routes.

Saving rules: `vat_mode` omitted is derived (see model) and stored; `workdays` default for `person-capacity`;
`kind` other than `person-capacity` with any non-null capacity field is 422; `weekly_hours` missing or 0 for
`person-capacity` is 422; `login_user_id` non-null is 422; `unavailable[].end < start` is 422. Warnings
(`sanity_warnings`): `supplier.duplicate_number` (same non-empty `supplier_no`, case-insensitive, on another supplier),
and the existing `check_identifiers` warnings for `vat_id`, `tax_number`, `iban`, `bic` (a wrong IBAN checksum is a
warning on the API, a rejected row in the importer).

`GET /api/suppliers/vendor-suggestions`: groups the distinct `vendor` values of expenses whose `supplier_id` is null and
whose `vendor` is not blank. **Normalised key**: Unicode NFKC, case-fold, remove every character that is not a letter,
digit or whitespace (so `e.K.` is `ek`, `&` disappears), collapse whitespace, trim, then strip trailing legal-form
tokens repeatedly: `gmbh mbh ug ek ohg kg gbr ag se ltd llc inc co`. A group whose key is empty is skipped. The best
existing supplier is the one (by `name` normalised the same way, any `active`) with an equal key; none or several: the
first by name, `null` if none. Sorted by `count` descending, then key.

```json
{
  "groups": [
    {"key": "demo print shop", "variants": ["Demo Print Shop GmbH", "demo print shop"], "count": 3, "net_total": 120.5,
     "supplier": {"id": "7a1b2c3d", "name": "Demo Print Shop GmbH"}},
    {"key": "example hosting", "variants": ["Example Hosting"], "count": 1, "net_total": 9.9, "supplier": null}
  ],
  "unlinked_expenses": 4
}
```

`POST /api/suppliers/adopt-vendors`, body (`dry_run` default **true**):

```json
{"dry_run": true, "items": [
  {"vendors": ["Demo Print Shop GmbH", "demo print shop"], "supplier_id": "7a1b2c3d"},
  {"vendors": ["Example Hosting"], "create": {"name": "Example Hosting", "kind": "company"}}
]}
```

An item selects the expenses whose `supplier_id` is null and whose `vendor` equals (exact string) one of `vendors`.
Answer **200** (dry run and real run have the same shape, `dry_run` echoes the mode):

```json
{
  "dry_run": true,
  "items": [
    {"vendors": ["Demo Print Shop GmbH", "demo print shop"], "supplier_id": "7a1b2c3d", "supplier_created": false,
     "would_create": false, "expense_ids": ["e1000001", "e1000002", "e1000003"], "count": 3},
    {"vendors": ["Example Hosting"], "supplier_id": null, "supplier_created": false,
     "would_create": true, "expense_ids": ["e1000009"], "count": 1}
  ],
  "expenses_changed": 4
}
```

A real run (one transaction, all or none) creates the suppliers of the `create` items, sets `supplier_id` on the
selected expenses and **does not touch `vendor`**; `supplier_id` in the answer is then the created id and
`supplier_created` true. An item that selects no expense is a no-op (nothing created), so a second run changes nothing
and returns `count: 0`. Errors: 404 `"Supplier not found"` (`supplier_id` of an item), 422 (both or neither of
`supplier_id`/`create`, a `create` that fails `SupplierIn`).

`GET /api/suppliers/{supplier_id}/dependency` (`person-capacity` only, else 409 `"Not a subcontractor"`): the numbers
behind `supplier.dependency_hint`. Rule (open decision 5): per Monday-Sunday week sum the hours of the supplier's
allocations (`active` and `cancelled`, future included, spreading as in "Spreading the hours"); a week *counts* when the
sum is `>= threshold_hours_per_week`; the hint is raised when the longest run of consecutive counting weeks is
`>= threshold_weeks`.

```json
{"supplier_id": "5d1c0aa2", "threshold_hours_per_week": 30.0, "threshold_weeks": 12, "raised": true,
 "longest_run_weeks": 12, "average_hours_per_week": 30.0, "from": "2026-06-08", "to": "2026-08-30"}
```

(`from`/`to` = first Monday and last Sunday of the longest run; both `null` and `longest_run_weeks` 0 without counting
weeks; `average_hours_per_week` is the mean over the run.)

### Incoming offers

| Route | Rights | Notes |
|---|---|---|
| `GET /api/incoming-offers?supplier_id=&status=&project_id=` | procurement read | sorted by `received_on` descending then id. `status` is one of the four stored values or `expired` (filters the derived value; the stored filters are on the stored value). |
| `POST /api/incoming-offers` | procurement write | **201**, status `received`; `planning_warnings[]` |
| `GET /api/incoming-offers/{incoming_offer_id}` | procurement read | 404 `"Not found"` |
| `PUT /api/incoming-offers/{incoming_offer_id}` | procurement write | whole `IncomingOfferIn`; see locking |
| `DELETE /api/incoming-offers/{incoming_offer_id}` | procurement write | 204; 409 when `accepted` |
| `POST .../review` | procurement write | no body; `received` -> `under_review`; answer: the offer |
| `POST .../accept` | procurement write | body `AcceptIn` (may be `{}`); answer below |
| `POST .../reject` | procurement write | body `RejectIn` (may be `{}`); `received`/`under_review` -> `rejected`; answer: the offer |
| `POST .../reopen` | procurement write | no body; `rejected` -> `under_review`; answer: the offer |
| `POST .../unaccept` | procurement write | no body; answer: the offer |

Computed on every save (never taken from the client): `hours` = sum of `quantity` of the `h` lines (2 decimals);
`net_total` = sum over lines of `q2(quantity x unit_price)`; the effective VAT is 0 when `vat_mode` is
`eu_reverse_charge`, `third_country_reverse_charge` or `small_business` (then `vat_rate` is stored as `0`), else
`vat = q2(net_total x vat_rate / 100)`; `gross_total = net_total + vat`. `customer_id` = the project's customer.
`effective_status` = `"expired"` when the stored status is `received` or `under_review` and `valid_until` is before
today (`valid_until` equal to today is **not** expired), else the stored status.

Create / update checks: the supplier exists (422 `"Unknown supplier"`) and, on create, is active (409 `"The supplier is
not active"`); `project_id` exists (422 `"Unknown project"`); `contract_id` is a contract of `project_id` (422);
`attachment` follows the existing `v.OptAttachment` rule. `project_id` is **not** required to save, only to accept.
Warnings in `planning_warnings`: `incoming_offer.hours_mismatch` (for a capacity supplier: the hours the plan books,
i.e. the sum of the external allocation's days, `external_capacity.allocation_days`; for other suppliers the plan hours
of the existing `planning_warnings.plan_hours`; compared against `hours`, difference > 0.01) and `incoming_offer.duplicate_reference` (same
non-empty `reference` for the same supplier on another offer).

Locking: a `PUT` on an `accepted` offer may change only `notes`, `attachment` and `decision_note`; any other submitted
field that differs from the stored value is **409** `"An accepted offer can only change notes, attachment and decision
note"`. Editing a `rejected` or open offer is free; `status` and the other server fields are never changed by `PUT`.

Status table (anything else is 409 `"This action is not possible in status <status>"`):

| From | Action | To | Side effect |
|---|---|---|---|
| (new) | create | `received` | |
| `received` | review | `under_review` | none |
| `received`, `under_review` | accept | `accepted` | allocation (capacity supplier) or flat commitment (other supplier) |
| `received`, `under_review` | reject | `rejected` | none |
| `rejected` | reopen | `under_review` | none |
| `accepted` | unaccept | `under_review` | allocation deleted |
| `accepted`, `rejected` | review | 409 | |

`status_changed_at` (date), `decided_by` (user id) are set by every status action.

**Accept** (one transaction, boss only). Order of the checks:

1. status must be `received`/`under_review` (409). `effective_status` `expired` needs `confirm_expired: true`, else 409
   `"The offer expired on <valid_until>; confirm to accept it anyway"`.
2. the supplier must be active (409 `"The supplier is not active"`).
3. Plain supplier (`company`, `person`): no allocation, no planner effect. The offer becomes `accepted`,
   `allocation_id` stays null, and it appears in the commitments as a **flat** commitment of `net_total`.
4. `person-capacity`: needs a `plan` (422 `"A capacity offer needs a plan"`), a project (`AcceptIn.project_id`, else the
   offer's; 422 `"A capacity offer needs a project"`; the project must exist and not be `done`/`archived`, 409 `"The
   project is closed"`), at least one `h` line (422 `"A capacity offer needs an hour line"`), and a `contract_id`, if
   any, of that project (422).
5. Warnings are collected (table "Warning codes"). A confirmable one whose code is not in `confirm_warnings` is **409**
   `"Confirm these warnings first: allocation.exceeds_weekly_hours"` and nothing is written (the 409 body is
   `{"detail": "...", "codes": ["allocation.exceeds_weekly_hours"]}`).
6. The allocation row is written (kind external: `supplier_id` set, `user_id` null, `incoming_offer_id`, `project_id`,
   `contract_id`, `customer_id`, `start`, `end`, `hours_per_week` or `total_hours` from the plan, `rate` = net of the
   `h` lines divided by their hours, rounded half-up to 4 decimals, `status: "active"`), and the offer becomes
   `accepted` with `allocation_id`, `status_changed_at`, `decided_by`, `decision_note`, and the project/contract written
   back.

Accept answer (200):

```json
{
  "offer": {"id": "9b3e51c7", "status": "accepted", "effective_status": "accepted", "allocation_id": "c04d77e1"},
  "allocation": {"id": "c04d77e1", "kind": "external", "status": "active", "planned_hours": 80.0, "planned_net": 6400.0,
                 "invoiced_net": 0.0, "open_net": 6400.0},
  "commitment": null,
  "planning_warnings": []
}
```

(`offer` and `allocation` are the full response objects; shortened here. For a plain supplier `allocation` is `null`
and `commitment` is `{"kind": "flat", "planned_net": 3091.88, "open_net": 3091.88}`.)

**Unaccept**: allowed when the allocation has not started (`start > today`) **and** no expense has its `allocation_id`;
for a flat commitment always. The allocation is deleted, the offer returns to `under_review`, `allocation_id` null.
Otherwise 409 `"The allocation has started or has linked expenses; cancel it instead"`.

### Allocations (additions)

| Route | Rights | Notes |
|---|---|---|
| `GET /api/allocations/{allocation_id}` | procurement read | an external row, with `days`. An employee row is **404** `"Not found"` unless the caller also has `planning` read (then it is returned without `days`). |
| `POST /api/allocations/{allocation_id}/cancel` | procurement write | body `CancelAllocationIn`; answer: the allocation (200) |

`GET /api/allocations` (existing) additionally returns, for external rows, the computed `planned_hours` (2 decimals),
`planned_net`, `invoiced_net`, `open_net` (money, 2 decimals), `supplier_name`, `offer_reference`. Filters unchanged.

Computed figures: `planned_hours` = sum of the per-day hours (below), `planned_net = q2(planned_hours_exact x rate)`;
for a `total_hours` allocation `planned_net = q2(total_hours x rate)` when nothing was cancelled, else
`q2(total_hours x kept_days / n x rate)` where `n` is the number of available days of the **original** period and
`kept_days` those before `cancelled_from`. `invoiced_net` = sum of `net` of expenses with this `allocation_id`;
`open_net = max(0, planned_net - invoiced_net)`.

**Cancel**: `effective_from >= start` (422 `"effective_from must not be before the start"`), `effective_from <= end`
(409 `"effective_from is after the end of the allocation"`), status `active` (409 `"The allocation is already
cancelled"`), kind external (409 `"This allocation cannot be changed here"`). Sets `status: "cancelled"`,
`cancelled_from`, `cancel_reason`. Days from `effective_from` on lose capacity and cost. The offer stays `accepted`.
Linked expenses are untouched.

### Finance: commitments

`GET /api/finance/commitments?supplier_id=&project_id=` (area `procurement` read). Items are external allocations with
status `active`, plus `cancelled` ones whose `open_net > 0`, plus one `flat` item per accepted offer without an
allocation.

```json
{
  "as_of": "2026-03-02",
  "items": [
    {"kind": "allocation", "allocation_id": "c04d77e1", "incoming_offer_id": "9b3e51c7", "offer_reference": "ANG-2026-117",
     "supplier_id": "5d1c0aa2", "supplier_name": "Example Freelance Studio (Demo)", "project_id": "a1b2c3d4",
     "project_name": "Demo project P", "start": "2026-03-02", "end": "2026-03-27", "status": "active",
     "planned_hours": 80.0, "planned_net": 6400.0, "invoiced_net": 0.0, "open_net": 6400.0},
    {"kind": "flat", "allocation_id": null, "incoming_offer_id": "9b3e51c8", "offer_reference": "L-77",
     "supplier_id": "7a1b2c3d", "supplier_name": "Demo Print Shop GmbH", "project_id": null, "project_name": null,
     "start": null, "end": null, "status": "accepted", "planned_hours": null, "planned_net": 3091.88,
     "invoiced_net": null, "open_net": 3091.88}
  ],
  "by_supplier": [{"supplier_id": "5d1c0aa2", "name": "Example Freelance Studio (Demo)", "planned_net": 6400.0, "invoiced_net": 0.0, "open_net": 6400.0}],
  "by_project": [{"project_id": "a1b2c3d4", "name": "Demo project P", "planned_net": 6400.0, "invoiced_net": 0.0, "open_net": 6400.0}],
  "totals": {"planned_net": 9491.88, "invoiced_net": 0.0, "open_net": 9491.88},
  "hints": []
}
```

`by_project` has `project_id: null` / `name: "No project"` for flat items; flat items have `invoiced_net: null` and
count with 0 in `totals.invoiced_net` and fully in `open_net` (they close only by unaccepting, open decision 7).
Sort: items by `start` descending (flat last), then id. `hints[]` has `business.customer_dominance` (below) when
raised. `GET /api/project-report` (existing, area `finance`) gains the field `committed_external_cost` (money) on every
project row: the sum of `planned_net` of the project's external allocations (cancelled ones shortened), `0.0` if none.
Nothing else of that report changes; actual tracked cost stays as it is.

### Expenses (links)

`ExpenseIn` gains `supplier_id`, `allocation_id`, `reverse_charge` (the existing routes `/api/expenses*` and their
rights are unchanged). Rules on save:

* `supplier_id` must exist (422 `"Unknown supplier"`). When set, the server writes `vendor = supplier.name` (snapshot);
  when null, `vendor` stays as typed.
* `allocation_id` needs `supplier_id` (422 `"allocation_id needs a supplier"`), must exist, be external and belong to
  the same supplier (422 `"The allocation belongs to another supplier"`).
* `reverse_charge` is just stored. For a reverse-charge supplier the client sends `vat` = the self-assessed amount
  and `reverse_charge: true`; the finance figures are not changed (open decision 6).
* Sanity: `expense.possible_duplicate` compares `supplier_id` when **both** rows have one, otherwise the lower-cased
  `vendor` as today. New rule `allocation.invoiced_exceeds_planned` (below) in `sanity_warnings`.
* Deleting/changing the expense recomputes `invoiced_net` on read; nothing is stored on the allocation.

### Team timeline

`GET /api/team/timeline` (area `team_timeline`, unchanged rights and levels) gains the top-level key `external`, always
present. It lists, for a viewer with `suppliers` read `limited` or better (boss, employee, accountant all have it; any
future role without it gets `[]`), the active and cancelled external allocations that overlap the window, grouped by
supplier; only name and window, never rates, hours or money:

```json
"external": [{"supplier_id": "5d1c0aa2", "name": "Example Freelance Studio (Demo)",
              "windows": [{"start": "2026-03-02", "end": "2026-03-27"}]}]
```

A cancelled allocation's window ends the day before `cancelled_from`; one that ends before it starts is omitted.
Sorted by name case-insensitive, windows by `start`. A supplier with `active: false` is still listed while it has a
window. Existing keys (`people`, `holidays`, ...) are unchanged.

## Planner integration

Everything below is computed by the same engine functions as today (`planning.load`, `day_table`, `weeks_detail`,
`plan_service`); there is no second implementation.

### Spreading the hours (the oracle; one pure function)

`external_capacity.allocation_days(supplier, allocation, cap) -> dict[date, float]` (new file
`permita38/core/service/external_capacity.py`). With `S` = the supplier row, `effective_end = cancelled_from - 1 day`
for a cancelled allocation, else `end`:

* **available day** = a day in `[start, effective_end]` that is in `S.workdays`, is not a public holiday of the
  business state (`cap.holiday(x)`), and is not inside an `S.unavailable` window (both ends inclusive).
* `hours_per_week`: every available day gets `hours_per_week / len(S.workdays)` (the **supplier's** workdays, a ceiling
  per day, not moved to other days: this differs on purpose from employees, who get `hours_per_week / available days of
  the week`).
* `total_hours`: `total_hours / n` on each available day of the **original** period `[start, end]`, restricted to the
  days up to `effective_end` when cancelled; with `n = 0` the allocation books nothing and the acceptance warning
  `allocation.no_available_day` is returned.
* Calendar busy time does not apply.

### What the engine does with it

* `planning.load` builds `World.externals` (supplier id -> supplier row, `workdays`, `weekly_hours`, `external: true`)
  for active `person-capacity` suppliers that have at least one usable external allocation **or** are assigned to a
  contract, and `World.external_allocations`. Both are empty with `include_external=false`.
* `S.unavailable` windows are registered as days off (`kind` other) in the capacity helper for `S`, so contracts
  assigned to `S` spread around them like around an employee's leave. `make()` accepts an assignee id that is a key of
  `world.users` **or** `world.externals`; a contract whose assignees are all unknown still falls back to the boss.
* `day_table`: the base-capacity loop is over `world.users` only (externals never get `weekly_hours / workdays`).
  For each external allocation and each day of `allocation_days`: `days[x].cap += h`;
  `days[x].scope(project_id).external.cap += h` (only the allocation's project; if `contract_id` is set it is
  informational for the ring fence below, the pool is still the project's). Demand of an item is split per assignee: an
  assignee in `world.externals` adds its share to `scope(item.project_id or "unassigned").external`
  (`confirmed`/`binding`/`expected`), an employee's share to `.internal`, as today. `Day.confirmed/binding/expected`
  keep the team totals of both.
* **Ring fence.** A contract draws on external capacity only if a **same-project** allocation of that supplier exists
  on that day; because the pool is per project and day, an item of project Q never uses P's external hours, and
  `external.gap = max(0, external.assigned - external.cap)` stays on Q's row. When the allocation has a `contract_id`,
  only that contract's demand counts as *assigned* to it: demand of other contracts of the project assigned to the
  same supplier is added to the pool's `assigned` but the allocation's hours are capacity for the project pool
  (open decision 4, simplest option).
* `include_external=false` builds the world **without** `externals` and `external_allocations`: forecast, day table and
  simulate must equal a database with no suppliers at all (a test, W5).
* Not affected: time entries, `GET /api/plan/vacation`, `entitlement`, employee over-allocation warnings,
  `AllocationService.bootstrap` (skips external assignees), the `user_id` filter (a supplier id as `user_id` is 404
  `"User not found"`, as today).

### Changed responses

`GET /api/forecast?weeks=&user_id=&offers=&project_id=&by_project=&include_external=`: new query parameter
`include_external` (int `0|1`, default `1`, other values 422). `POST /api/plan/simulate`: body field
`include_external: bool = true` (the base for `delta` uses the same value). `GET /api/plan/day-table` gets the same
query parameter. Routes whose world is built from a stored scenario use the default.

**Week rows** (forecast and simulate, with or without `by_project`; also with a `project_id` filter, then that scope's
numbers) gain these keys, **always present** (`0.0`/`[]` when there is nothing external):

```json
{
  "external_capacity": 20.0,
  "external_assigned": 25.0,
  "external_gap": 5.0,
  "external_cost": 1600.0,
  "external_by_project": [
    {"project_id": "a1b2c3d4", "external_capacity": 20.0, "external_assigned": 25.0, "external_gap": 5.0, "external_cost": 1600.0}
  ]
}
```

* `external_capacity/assigned/gap` = sum over the scopes of `projects[].external.capacity/assigned/gap`;
  `external_cost` = sum over the scopes of `projects[].external_cost`. `external_by_project` lists scopes with any of
  the four values non-zero, ordered like `projects[]`. They are always the same numbers as `projects[].external` (one
  source).
* `projects[].external_cost` (was always `0.0`): for the scope, the sum over its external allocations of
  `q2(dec(round(hours_in_week, 6)) x rate)`; `hours_in_week` = the `allocation_days` hours inside that Monday-Sunday
  week. The row figure is the sum of the allocations' rounded amounts (not rounded after summing hours).
* `capacity`, `confirmed`, `binding_offers`, `free_*`, `utilisation`, `status` keep their formulas; external hours are
  now inside them (when the flag is on). `revenue` keeps its basis rule; `intended_revenue` = capacity (including
  external) x default rate (open decision 3).
* `contributions[]` entries gain `"external": true` **only when every assignee of the item is external** (otherwise
  the key is absent, so entries without suppliers are byte-identical to today).
* `totals` gains `external_capacity`, `external_gap`, `external_cost` (sums over the weeks, 2 decimals, always present)
  and `margin` = `revenue - external_cost` (2 decimals).
* The day table: `scopes[key].external` blocks carry real numbers (shape unchanged); `cap` of the day includes them.
* Warnings: `capacity.gap` with `pool: "external"` (already specified), `allocation.no_demand` is now also emitted
  for external allocations (their pool of the project has capacity and `assigned == 0`; same `details`), and the
  existing `offer.project_gap` pool logic is unchanged.

### Byte-compatible existing planner behaviour (must not change)

1. With no supplier and no external allocation every existing response of `/api/forecast`, `/api/plan/simulate`,
   `/api/plan/day-table`, `/api/plan/check-offer`, `/api/plan/apply`, scenarios, `/api/projects/{rid}/planning` is
   identical to today **except** the new always-present keys listed above (a test compares the old golden values).
2. All existing field names, rounding (6 decimals in rows, utilisation 1 decimal), ordering of `projects[]`
   (name case-insensitive, `unassigned` last) and the status rule (`over` when `gap > 0`) stay.
3. Employee allocation routes, bootstrap, their 409/422 texts, `allocation.employee_over_allocated`,
   `allocation.overlap_same_project` and their caps (10 weeks) stay.
4. Employee spreading of contracts (`hpw x share / available days of the week`, holiday/absence handling, the nominal
   fallback when no day is available) stays; only the pool an *external* assignee's share is booked in changes.
5. `World.users`, `user_list`, vacation planner, entitlement, `suggest-vacation`, `what-must-move`, `suggest-start`
   logic for employees stays; `contracts[]` and `expired_offers[]` keys of the forecast stay.
6. `include_external=false` reproduces exactly the pre-feature numbers for any database (property in the randomized
   suite).
7. `GET /api/allocations` list route, rights and employee rows; `AllocationIn`/`AllocationChange`; the
   `allocations_one_owner` check.

## Warning codes

Shape of every warning: `{code, severity, field, message, details, confirmable}`; `details` keys are always present.
Planner warnings are in `planning_warnings`, expense warnings in `sanity_warnings`, supplier warnings in
`sanity_warnings`, hints in `hints`.

| Code | severity | field | confirmable | where | `details` |
|---|---|---|---|---|---|
| `supplier.duplicate_number` | warning | `supplier_no` | false | supplier POST/PUT | `{other_id}` |
| `incoming_offer.duplicate_reference` | warning | `reference` | false | offer POST/PUT | `{other_id}` |
| `incoming_offer.hours_mismatch` | warning | `plan` | false | offer POST/PUT, accept | `{offer_hours, plan_hours, difference}` |
| `allocation.exceeds_weekly_hours` | warning | `plan` | **true** | accept | `{supplier_id, week, allocated, weekly_hours, over_by}` (one per offending week, earliest first, at most 10) |
| `allocation.outside_availability` | warning | `plan` | false | accept | `{supplier_id, dropped_hours, days}` (`days` = the unavailable workdays inside the plan that are not holidays, ISO dates, at most 20) |
| `allocation.no_available_day` | warning | `plan` | false | accept | `{supplier_id}` |
| `allocation.cost_differs_from_offer` | info | `net_total` | false | accept | `{incoming_offer_id, planned_net, net_total, difference}` (when `abs(planned_net - net_total) > 0.01`) |
| `allocation.no_demand` | info | `weeks` | false | accept, forecast | `{allocation_id, project_id, project_name, week, capacity}` (at acceptance: the first week of the allocation) |
| `supplier.dependency_hint` | info | `null` | false | accept (with the new allocation included), `dependency` | `{supplier_id, supplier_name, hours_per_week, weeks, threshold_hours_per_week, threshold_weeks, from, to}` |
| `allocation.invoiced_exceeds_planned` | warning | `allocation_id` | false | expense POST/PUT | `{allocation_id, planned_net, invoiced_net, excess}` (when `invoiced_net - planned_net > 0.01`, including the expense being saved) |
| `business.customer_dominance` | info | `null` | false | `finance/commitments.hints` | `{customer_id, customer_name, share_percent, revenue_customer, revenue_total, from, to}` |
| `capacity.gap` (existing) | warning | `weeks` | false | forecast | `pool: "external"` possible |

* `allocation.exceeds_weekly_hours`: `allocated` = the sum of the `allocation_days` hours of **all** active (and
  cancelled, shortened) allocations of that supplier **plus the one being accepted** in that Monday-Sunday week;
  `weekly_hours` = the supplier's; `over_by = allocated - weekly_hours` (> 0.005).
* `supplier.dependency_hint` is raised with the rule of `GET .../dependency`.
* `business.customer_dominance`: revenue = the net of the invoices the existing `GET /api/finance` counts as revenue
  (same selection: status not draft or cancelled; EUeR/cash accounting uses the paid date and only paid invoices,
  accrual accounting the invoice date), in the trailing 12 months ending today (`today - 12 months < date <= today`);
  raised when `100 x customer >= customer_dominance_percent x total` (exact decimals, total > 0).
* Dismissing a hint is a client convenience (localStorage); no server state.
* Null response keys: the offer and allocation routes omit keys whose value is null (`response_model_exclude_none`);
  the commitments, dependency and accept answers keep their null keys as shown in the examples.
* Accepting an offer writes the project and contract given in the body back to the offer (decision confirmed); an
  explicit `null` in the body clears the link.

## Errors (summary)

| Status | When |
|---|---|
| 401 | not signed in / bad token |
| 403 | right missing; read-only token on a write route; employee on procurement; accountant on a procurement write |
| 404 | unknown supplier/offer/allocation id in the path (`"Not found"`); `"Supplier not found"` inside `adopt-vendors` |
| 409 | wrong status for the action; expired without `confirm_expired`; inactive supplier on accept/create; unaccept after start or with expenses; locked fields of an accepted offer; delete of a referenced supplier or of an accepted offer; second cancel; cancel after the end; external allocation via the employee routes; unconfirmed `allocation.exceeds_weekly_hours`; `dependency` for a non-subcontractor; closed project on accept |
| 422 | body validation (list form); unknown `supplier_id`, `project_id`, `allocation_id`; capacity fields on a non-capacity supplier; `weekly_hours` missing for a subcontractor; `login_user_id`; plan with both or neither of the hour fields; accepting a capacity offer without plan, project or hour line; `effective_from` before the start; an allocation of another supplier on an expense |

## Worked examples (hand computed; fixtures for the web UI and literal expectations for the tests)

Common setup (spec): today = Monday 2026-03-02, no public holiday between 2026-03-02 and 2026-03-27, Easter 2026-04-05
(Good Friday 04-03 and Easter Monday 04-06 are national holidays). Boss "Demo Boss" `u1b2c3d4`: 40 h/week, Mon-Fri.
Customer `c0ffee01`, project P `a1b2c3d4` ("Demo project P"). Supplier S `5d1c0aa2` ("Example Freelance Studio
(Demo)"): `person-capacity`, `workdays` [0,1,2,3] (Mon-Thu), `weekly_hours` 24, `rate` 80.0, DE, `domestic`. Names,
e-mails (`example.invalid`) and IBANs are demo values. The fixtures are extracted verbatim into
`tests/web/fixtures/subcontractors/*.json`; the tests assert the same numbers independently of the code under test.

### W1: the offer, the accept, the allocation days

Offer (after `POST /api/incoming-offers` with the body fields below; response adds the computed fields):

```json
{
  "id": "9b3e51c7", "supplier_id": "5d1c0aa2", "supplier_name": "Example Freelance Studio (Demo)",
  "reference": "ANG-2026-117", "title": "Frontend capacity, spring",
  "received_on": "2026-02-20", "valid_until": "2026-03-31",
  "lines": [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80.0}],
  "hours": 80.0, "net_total": 6400.0, "vat_rate": 19, "vat": 1216.0, "gross_total": 7616.0, "vat_mode": "domestic",
  "plan": {"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20.0},
  "project_id": "a1b2c3d4", "project_name": "Demo project P", "customer_id": "c0ffee01",
  "attachment": "5c0ffee1.pdf", "status": "received", "effective_status": "received",
  "allocation_id": null, "planning_warnings": []
}
```

`POST /api/incoming-offers/9b3e51c7/accept` with `{}` (status `received` is accepted directly; today 2026-03-02,
`valid_until` 2026-03-31, not expired). 20 h / 4 workdays = 5 h per day, 4 weeks x 4 days = 16 days = 80 h,
`planned_net` = 80 x 80.00 = 6 400.00. S's 20 h per week do not exceed `weekly_hours` 24; no contract is assigned to S
yet, so only the `no_demand` info:

```json
{
  "offer": {"id": "9b3e51c7", "status": "accepted", "effective_status": "accepted", "allocation_id": "c04d77e1",
            "status_changed_at": "2026-03-02", "decided_by": "u1b2c3d4", "project_id": "a1b2c3d4"},
  "allocation": {
    "id": "c04d77e1", "kind": "external", "supplier_id": "5d1c0aa2", "supplier_name": "Example Freelance Studio (Demo)",
    "incoming_offer_id": "9b3e51c7", "offer_reference": "ANG-2026-117",
    "project_id": "a1b2c3d4", "project_name": "Demo project P", "customer_id": "c0ffee01",
    "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20.0, "rate": 80.0, "status": "active",
    "planned_hours": 80.0, "planned_net": 6400.0, "invoiced_net": 0.0, "open_net": 6400.0
  },
  "commitment": null,
  "planning_warnings": [
    {"code": "allocation.no_demand", "severity": "info", "field": "weeks", "confirmable": false,
     "message": "Week of 2026-03-02: 20 h are allocated to project Demo project P but nothing is planned there.",
     "details": {"allocation_id": "c04d77e1", "project_id": "a1b2c3d4", "project_name": "Demo project P",
                 "week": "2026-03-02", "capacity": 20.0}}
  ]
}
```

`GET /api/allocations/c04d77e1` adds `days` (16 entries, all 5.0):

```json
{"days": [
  {"date": "2026-03-02", "hours": 5.0}, {"date": "2026-03-03", "hours": 5.0}, {"date": "2026-03-04", "hours": 5.0}, {"date": "2026-03-05", "hours": 5.0},
  {"date": "2026-03-09", "hours": 5.0}, {"date": "2026-03-10", "hours": 5.0}, {"date": "2026-03-11", "hours": 5.0}, {"date": "2026-03-12", "hours": 5.0},
  {"date": "2026-03-16", "hours": 5.0}, {"date": "2026-03-17", "hours": 5.0}, {"date": "2026-03-18", "hours": 5.0}, {"date": "2026-03-19", "hours": 5.0},
  {"date": "2026-03-23", "hours": 5.0}, {"date": "2026-03-24", "hours": 5.0}, {"date": "2026-03-25", "hours": 5.0}, {"date": "2026-03-26", "hours": 5.0}
]}
```

Weekly view: every week of the window has `external_capacity` 20.0 and `external_cost` 1 600.00 (20 x 80).

### W2: Easter Monday inside the window

Same allocation, plan 2026-04-06 to 2026-04-17. Week of 04-06: Monday is a holiday, Tue-Thu = 3 x 5 = 15 h, cost
1 200.00; week of 04-13: Mon-Thu = 4 x 5 = 20 h, 1 600.00. `planned_hours` 35.0, `planned_net` 2 800.00. Not made up
on other days.

### W3: `total_hours` with an unavailable window

S Mon-Fri (`workdays` [0,1,2,3,4]), `weekly_hours` 45 (the week of 03-02 books 41.67 h, so 45 is needed for no weekly warning; the example names no value), `rate` 75.0, `unavailable` [`{"from": "2026-03-09", "to": "2026-03-11"}`], plan
2026-03-02 to 2026-03-20, `total_hours` 100. 15 weekdays - 3 = 12 available days; 100 / 12 = 8.333333 h per day.
Weeks: 03-02: 5 days = 41.666667 h, 3 125.00; 03-09: Thu+Fri = 16.666667 h, 1 250.00; 03-16: 41.666667 h, 3 125.00.
`planned_hours` 100.0, `planned_net` `q2(100 x 75)` = **7 500.00** (3 125.00 + 1 250.00 + 3 125.00 = 7 500.00 here;
the allocation figure is authoritative if rounding ever differs by cents). Accepting returns
`allocation.outside_availability` with `dropped_hours` 25.0 (3 workdays x 8.333333) and `days`
`["2026-03-09","2026-03-10","2026-03-11"]`.

### W4 and W5: planner with and without the subcontractor (ring fence)

Data: contract C `d4e5f6a7` of project P, 50 h/week, rate 100.00, 2026-03-02 to 2026-03-27; employee allocation
boss -> P 40 h/week (8 h x Mon-Fri); W1's allocation of S accepted. **Before** C's `user_ids` include S (`[boss]`):
internal cap 40, assigned 50, gap 10. **After** `user_ids` = [boss, S]: C splits 25 h each; boss 5 h/day (cap 8, gap 0,
15 h/week unused in P), S 25 / 4 = 6.25 h/day against 5 h cap: **external gap 1.25 x 4 = 5 h**. `GET /api/forecast?weeks=4`,
week 1 (the four weeks are identical; revenue = 50 h x 100.00 = 5 000.00 per week):

```json
{
  "week": "2026-03-02", "capacity": 60.0, "confirmed": 50.0, "binding_offers": 0.0,
  "free_after_confirmed": 10.0, "free_worst_case": 10.0, "utilisation": 83.3, "status": "over",
  "gap": 5.0, "gap_confirmed": 5.0, "gap_expected": 5.0,
  "revenue": 5000.0, "basis": "contract", "contracted_revenue": 5000.0,
  "external_capacity": 20.0, "external_assigned": 25.0, "external_gap": 5.0, "external_cost": 1600.0,
  "external_by_project": [
    {"project_id": "a1b2c3d4", "external_capacity": 20.0, "external_assigned": 25.0, "external_gap": 5.0, "external_cost": 1600.0}
  ],
  "contributions": [{"kind": "contract", "id": "d4e5f6a7", "title": "Contract C_P", "hours": 50.0, "binding": false}],
  "projects": [
    {"project_id": "a1b2c3d4", "name": "Demo project P", "capacity": 60.0, "confirmed": 50.0, "binding_offers": 0.0,
     "expected_offers": 0.0, "free_after_confirmed": 10.0, "free_worst_case": 10.0, "utilisation": 83.3, "status": "over",
     "gap": 5.0, "gap_confirmed": 5.0, "gap_expected": 5.0,
     "internal": {"capacity": 40.0, "assigned": 25.0, "gap": 0.0},
     "external": {"capacity": 20.0, "assigned": 25.0, "gap": 5.0}, "external_cost": 1600.0},
    {"project_id": null, "name": "Unassigned", "capacity": 0.0, "confirmed": 0.0, "binding_offers": 0.0,
     "expected_offers": 0.0, "free_after_confirmed": 0.0, "free_worst_case": 0.0, "utilisation": 0.0, "status": "ok",
     "gap": 0.0, "gap_confirmed": 0.0, "gap_expected": 0.0,
     "internal": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0},
     "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}, "external_cost": 0.0}
  ]
}
```

(`contributions[]` has no `external` key: the contract has an employee assignee too.) Totals over the four weeks:

```json
{
  "capacity": 240.0, "confirmed": 200.0, "binding_offers": 0.0, "free_after_confirmed": 40.0, "free_worst_case": 40.0,
  "contracted_revenue": 20000.0, "offer_revenue": 0.0, "assumed_revenue": 0.0, "revenue": 20000.0,
  "over_weeks": 4, "gap": 20.0,
  "gap_by_project": [{"project_id": "a1b2c3d4", "name": "Demo project P", "gap": 20.0}],
  "external_capacity": 80.0, "external_gap": 20.0, "external_cost": 6400.0, "margin": 13600.0
}
```

The warning `capacity.gap` (`pool: "external"`, `assigned` 25.0, `capacity` 20.0, `gap` 5.0, project P) is returned for
each of the four weeks (as in the planner contract, example C). The team total does not cancel the gap: the boss's 15
unused hours stay on P.

**W5** `?include_external=0` (and the "before" state): S dropped, C falls back to `[boss]` -> week 1:
`capacity 40.0, confirmed 50.0, free_after_confirmed -10.0, free_worst_case -10.0, utilisation 125.0, status "over",
gap 10.0`, `external_capacity/assigned/gap/cost` all `0.0`, `external_by_project: []`, P entry
`internal {capacity 40.0, assigned 50.0, gap 10.0}`, `external {0.0, 0.0, 0.0}`, `external_cost 0.0`; totals `gap 40.0`,
`external_*` and `margin` = revenue (`20000.0`). Identical to a database without any supplier.

### W6: states without effect

The W1 offer in `received`, `under_review`, `rejected`, and with `valid_until` 2026-03-01 (`effective_status`
`expired`) leave forecast rows and commitments exactly as in W5. Accepting the expired one with `{}` is 409
`"The offer expired on 2026-03-01; confirm to accept it anyway"` and changes nothing; with `{"confirm_expired": true}`
it behaves as W1. `valid_until` = 2026-03-02 is not expired.

### W7 and W8: rounding and reverse charge

Offer with one line `{"quantity": 37.5, "unit": "h", "unit_price": 82.45}`, domestic, `vat_rate` 19: 37.5 x 82.45 =
3 091.875 -> 3 091.88; VAT 19 % of 3 091.88 = 587.4572 -> 587.46:

```json
{"hours": 37.5, "net_total": 3091.88, "vat_rate": 19, "vat": 587.46, "gross_total": 3679.34, "vat_mode": "domestic"}
```

W8: the same offer from a supplier with `vat_mode` `eu_reverse_charge`:

```json
{"hours": 37.5, "net_total": 3091.88, "vat_rate": 0, "vat": 0.0, "gross_total": 3091.88, "vat_mode": "eu_reverse_charge"}
```

(the expense form proposes `vat` 587.46 and `reverse_charge: true`, *(verify)*; the server stores what it gets.)

### W9 and W10: cancel, commitments, over-invoicing

`POST /api/allocations/c04d77e1/cancel` with `{"effective_from": "2026-03-13", "reason": "project paused"}`. Kept days
(Mon-Thu, 5 h): 03-02..05 and 03-09..12 = 8 days = 40 h; 03-13 and later released. One expense with
`allocation_id` `c04d77e1`, `supplier_id` `5d1c0aa2`, net 1 600.00:

```json
{
  "id": "c04d77e1", "kind": "external", "status": "cancelled", "cancelled_from": "2026-03-13",
  "cancel_reason": "project paused", "start": "2026-03-02", "end": "2026-03-27",
  "planned_hours": 40.0, "planned_net": 3200.0, "invoiced_net": 1600.0, "open_net": 1600.0
}
```

`GET /api/finance/commitments` then has one item with those figures, `totals {planned_net 3200.0, invoiced_net 1600.0,
open_net 1600.0}`. The forecast frees the released 40 h: `external_capacity` is 20.0 in the weeks of 03-02 and 03-09
(Mon-Thu, 4 x 5 h) and 0.0 in the weeks of 03-16 and 03-23. **W10**: with expenses linked for net
3 300.00 in total, `open_net` 0.0 (floored) and saving the expense that makes it 3 300.00 returns in `sanity_warnings`:

```json
{"code": "allocation.invoiced_exceeds_planned", "severity": "warning", "field": "allocation_id", "confirmable": false,
 "message": "The linked expenses (3300.00) are 100.00 above the planned cost of the allocation (3200.00).",
 "details": {"allocation_id": "c04d77e1", "planned_net": 3200.0, "invoiced_net": 3300.0, "excess": 100.0}}
```

### W11: weekly-hours check

S `weekly_hours` 24. Allocation A 20 h/week (5 h/day Mon-Thu) active, accepting B 8 h/week (2 h/day) over the same week
2026-03-02: 4 x 7 = 28 h in the week:

```json
{"code": "allocation.exceeds_weekly_hours", "severity": "warning", "field": "plan", "confirmable": true,
 "message": "Example Freelance Studio (Demo) would be allocated 28 h in the week of 2026-03-02, 4 h above the 24 h per week they can offer.",
 "details": {"supplier_id": "5d1c0aa2", "week": "2026-03-02", "allocated": 28.0, "weekly_hours": 24.0, "over_by": 4.0}}
```

Without `"confirm_warnings": ["allocation.exceeds_weekly_hours"]` the accept is 409 with
`{"detail": "Confirm these warnings first: allocation.exceeds_weekly_hours", "codes": ["allocation.exceeds_weekly_hours"]}`.
A single allocation of 25 h/week (6.25 h x 4): `allocated 25.0`, `over_by 1.0`.

### W12: dependency hint

S Mon-Fri, `hours_per_week` 30 (6 h/day), plan 2026-06-08 to 2026-08-28 (12 full weeks, no national holiday):
`GET /api/suppliers/5d1c0aa2/dependency` returns the fixture in the route section (`raised: true`,
`longest_run_weeks: 12`, `average_hours_per_week: 30.0`). With 29.9 h/week, or with the window one week shorter:
`raised: false`, `longest_run_weeks` 0 resp. 11.

### W13: customer dominance

Trailing-12-month net revenue 60 000.00, customer A 49 800.00 = 83.0 %: `commitments.hints` contains
`{"code": "business.customer_dominance", "severity": "info", "field": null, "confirmable": false, "message": "...",
"details": {"customer_id": "c0ffee01", "customer_name": "Demo Customer A", "share_percent": 83.0, "revenue_customer":
49800.0, "revenue_total": 60000.0, "from": "2025-03-03", "to": "2026-03-02"}}`. With 49 799.99 (82.99998 %) `hints`
is `[]`.

### W14: bought capacity without demand, and the ring fence

W1 accepted (project P), no contract of P assigned to S. Week 1 of P: `external {capacity 20.0, assigned 0.0, gap 0.0}`,
`external_cost` 1 600.00 (idle but paid), info `allocation.no_demand`. Variant: contract D of project Q `e5f6a7b8`,
10 h/week, `user_ids` [S], no allocation of Q: Q's row `external {capacity 0.0, assigned 10.0, gap 10.0}` and
`status "over"`, P's 20 h stay unused (P: `external {20.0, 0.0, 0.0}`); the team `gap` is 10.0.

### Errors (fixtures)

```json
{"detail": "The offer expired on 2026-03-01; confirm to accept it anyway"}
{"detail": "A capacity offer needs a project"}
{"detail": "The allocation has started or has linked expenses; cancel it instead"}
{"detail": "An accepted offer can only change notes, attachment and decision note"}
{"detail": "Supplier is used by incoming offers, allocations or expenses; archive it instead"}
```

(The last is the 409 of `DELETE /api/suppliers/{supplier_id}`.) Employee `GET /api/suppliers/5d1c0aa2`:

```json
{"id": "5d1c0aa2", "name": "Example Freelance Studio (Demo)", "kind": "person-capacity", "tags": ["frontend"], "active": true}
```

## Tests this contract pins (beyond the examples)

Repository contract for both tables and the three expense columns (round-trip, unknown fields); migration drift tests
in all three dialects; strict/lenient bounds of every new field; the 21 `EXPECTED` rows and `docs/roles.md`
consistency; a table-driven test over every (status, action) pair; accept of a plain supplier creates no allocation
and no planner effect; unaccept before/after start and with a linked expense; locked fields on an accepted offer;
vendor-suggestions grouping and `adopt-vendors` dry run equals real run, idempotent, `vendor` text untouched;
employee limited view has no IBAN; read-only token reads, is denied every write; `include_external=false` equals the
pre-feature world (also in the hypothesis state machine); a what-if draft moving a contract assigned to S changes only
its project's rows; importer kind `suppliers` dry run equals real run; duplicate rule with and without `supplier_id`.

## Work packages and file ownership

A file is owned by exactly one package; a package never edits another package's files (ask the architect). Merge order
**A, C0, then B and C in parallel, then F**; web D after the wiring commit, E after B/C land or against the fixtures.

**Backend**

| Package | Owns |
|---|---|
| BE-A foundation: suppliers, storage, rights, expense links | `permita38/core/rights.py`, `docs/roles.md`; `permita38/core/repository/schema.py`; `permita38/core/schema/migrations/{sqlite,postgresql,mariadb}/0012-suppliers.sql`; **all of** `permita38/core/models.py` and `permita38/core/validation.py` changes of this feature (also the models used by B and C); `permita38/core/service/records.py` (expense validation, `vendor` snapshot, supplier existence), a new `permita38/core/service/suppliers.py` (CRUD, limited view, vendor suggestions/adoption, importer glue), `permita38/api/routers/suppliers.py`, router registration in `permita38/api/server.py`, `permita38/core/service/importer/` (kind `suppliers`), export manifest, `docs/import.md`, settings keys (`core/service/settings.py`), `tests/backend/test_rbac.py` (EXPECTED rows, migration helper), `test_repository_contract.py`, migration and supplier API tests, `api/openapi.json` |
| BE-C0 pure function (small, first after A) | new `permita38/core/service/external_capacity.py` (`allocation_days`, weekly aggregation helper), `tests/backend/test_external_capacity.py` (W1-W3, W9, W11 numbers) |
| BE-B procurement | new `permita38/core/service/procurement.py` (offers, status machine, accept/unaccept/cancel, warnings, hints, commitments), `permita38/api/routers/incoming_offers.py`, the two new routes appended to `permita38/api/routers/allocations.py`, `permita38/api/routers/finance.py` (commitments route, `project-report` field), `permita38/core/service/allocations.py` (external figures in `_present`, `get`), `permita38/core/service/sanity_rules.py` (new rule and the changed duplicate rule), `permita38/core/service/sales.py`/finance service for `committed_external_cost` and the commitments report, `tests/backend/test_procurement*.py`, `docs/compliance.md` notes |
| BE-C planner | `permita38/core/service/planning.py`, `plan_service.py`, `planning_warnings.py`, `team_timeline.py`, `permita38/api/routers/planning.py`, `permita38/api/routers/team.py` (timeline route only if needed), `tests/backend/test_planner_external.py` (W4, W5, W6, W14 oracle, simulate per project), `tests/backend/randomized/` additions (`include_external=false` invariant), `docs/planning.md` section "Subcontractors" |

`core/models.py`/`validation.py` are touched only by BE-A; B and C use the models A merges first (A therefore lands the
**complete** model set of this contract, including `AcceptIn`, `CancelAllocationIn`, `IncomingOffer`). The
`ContractIn.user_ids` validation is A's (records/models); the planner's `make()` change is C's.

**Web UI** (against the fixtures above, then the real API; the UI never computes capacity, gaps, cost, VAT or
commitments, it draws server numbers)

| Package | Owns |
|---|---|
| WEB-W wiring (first, tiny commit) | `web/index.html` script tags for `suppliers.js` and `procurement.js`, nav entries in `web/app.js`, empty stubs of the two new files |
| WEB-D suppliers and expense picker | new `web/suppliers.js` (list, form, capacity fields, import/export entry, adoption dialog), the expense form/list region of `web/app.js` (supplier picker, allocation picker, reverse-charge note, vendor banner), `web/style.css` additions under the `.sup-` prefix |
| WEB-E incoming offers, accept dialog, planner, finance | new `web/procurement.js` (offers list/detail, accept/reject dialogs, allocations and cancel), `web/planner.js` and `web/planner.css` (external row group per project, toggle, hatched hours, tooltip), `web/timeline.js` (external group), the finance page region of `web/app.js` (block "Open supplier commitments"), `web/style.css` additions under `.proc-` |
| Web tests and fixtures | `tests/web/test_suppliers_ui.py`, `tests/web/test_procurement_ui.py` (route interception, no backend), `tests/web/test_subcontractors_live.py` (real backend), `tests/web/fixtures/subcontractors/*.json` (verbatim from this document), one seeded step in `tests/web/e2e_random/` |

D and E share `web/app.js` and `web/style.css`: each edits only its marked region / prefix; D and E do not touch the
planner contract's `PAGES.plan` forecast region owner (WEB-A of that contract is finished). Neither web package touches
`docs/contracts/`.

**F docs and wrap-up** (maintainer/architect): `docs/compliance.md` merge of the tax-adviser answers, roadmap row,
`make generate-openapi` after B and C, MCP tool table entries.

## Open design decisions (picked by the architect for this contract; confirm or change before merge)

1. **Synthetic members live in `World.externals`, not in `World.users`.** The spec says `World.users`; that would leak
   suppliers into the vacation planner, entitlement, the employee over-allocation loop and `user_list`. A `user_id`
   filter with a supplier id stays 404.
2. **`GET /api/allocations` stays `planning` read (boss only).** The accountant reads external allocations through
   `GET /api/allocations/{allocation_id}` and the commitments report. Alternative: change the list to `procurement`
   read and filter employee rows by `planning` read (would change an existing `EXPECTED` row).
3. **`intended_revenue` includes external capacity** when the flag is on (spec open question 4, v1 says yes), shown with
   `external_cost` and `margin`.
4. **`contract_id` on an allocation is informational in v1**: the external pool is per project; a stored `contract_id`
   does not restrict which contract of the project draws on the hours. Alternative: restrict to that contract
   (needs a pool per contract).
5. **Dependency hint rule**: a week counts when its allocated hours are `>= 30`; raised at 12 consecutive counting
   weeks (spec: "average per calendar week over the allocation weeks"). Simpler to test (W12), same boundary results.
6. **Reverse-charge expenses are stored only** (`reverse_charge` flag, `vat` as sent); `GET /api/finance` and the VAT
   figures are not changed (the ustva spec and the adviser decide the treatment). Until then the report counts the
   `vat` of such an expense like any input VAT.
7. **Flat commitments** (offers of plain suppliers) never close automatically: `invoiced_net` is `null`, they stay in
   `open_net` until the offer is unaccepted (always allowed for them).
8. **IBAN checksum** is a warning on the API (like customers) and a rejected row in the importer; the hard bound is
   format and length.
9. **`status_changed_at` is a date** (`YYYY-MM-DD`), `decided_by` a user id.
10. **Allocation `rate`** is a JSON number (rounded half-up to 4 decimals when derived), not an exact decimal string as
    the spec table says; money figures stay numbers with 2 decimals.
11. **`adopt-vendors` body is `{dry_run, items:[...]}`** (the spec says a list); an item that matches no expense is a
    no-op so a repeated run changes nothing and creates no duplicate supplier.
12. **The `business.customer_dominance` hint is returned in `GET /api/finance/commitments.hints`** (procurement read),
    not on a dashboard route; the dashboard note is drawn by the web UI from it.
13. **Accept with a project given in the body writes it back to the offer**; `contract_id` likewise.
14. **Editable fields of an accepted offer** are `notes`, `attachment`, `decision_note` (the spec lists them), detected
    by comparing the submitted value with the stored one.
15. **Cancelled allocations stay in the engine** for the days before `cancelled_from` and appear in
    `finance/commitments` only while `open_net > 0`.
16. **Suppliers `PUT` on `kind`**: changing away from `person-capacity` while referenced by an offer plan or allocation
    is 409; the spec does not say.
