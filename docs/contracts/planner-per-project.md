# API contract: the planner per project

Source of truth for the backend, the web UI and the tests, so that they can be built in parallel. Behaviour, rules and
the product owner's decisions: `docs/specs/planner-per-project.md` (the "spec"); the `allocations` table is shared with
`docs/specs/subcontractors.md`. Where this file and the spec differ on the HTTP shape, this file wins. Conventions are
those of `docs/contracts/api-tokens.md` and `docs/contracts/trips.md`: base path `/api`, JSON, session cookie or
`Authorization: Bearer pat_...`; errors are `{"detail": "<message>"}` for 401/403/404/409 and service-level 400/422;
body/query validation errors (422) are `{"detail": [{"loc": ["body","hours_per_week"], "msg": "...", "type": "..."}]}`
(input is never echoed). Dates `YYYY-MM-DD`, weeks are identified by their Monday. Hours are JSON numbers (rounded to
6 decimals in rows, utilisation to 1 decimal percent, like today). Money is a JSON number with at most 2 decimals,
computed through `core/money.py`. Review tier: **Full** (see the spec).

## Decisions fixed by this contract (not open for the implementer)

1. **No new rights area.** All routes use `planning` (boss: read all / write all; employee and accountant: none).
2. **Scope key.** A scope is a project id or the literal string `unassigned`. In JSON bodies of responses the unassigned
   scope has `project_id: null`; in URLs, query strings, request bodies and map keys it is the string `unassigned`.
3. **One data source.** `projects[].internal/external` of the forecast, the day table and the flat `external_*` fields of
   the subcontractor spec are produced by the same engine function (`planning.weeks` / `planning.day_table`).
4. **Gaps are computed per scope, pool and day and summed** (spec rules 5 to 8). `assigned` = confirmed + binding
   (worst case). The team `gap` is the sum of all scope gaps and is never reduced by a surplus.
5. **Only employee allocations are written by these routes.** External (subcontractor) rows are listed read-only.
6. **Nothing is written by a preview**, by `simulate`, by `day-table` or by `GET` routes.
7. **Back-compat.** Every existing field of `GET /api/forecast`, `POST /api/plan/simulate` and their weeks keeps its
   name and meaning. New query parameters and body fields are optional with defaults that give the old call. The only
   changed *value* of an existing field is the team/week `status` (`over` when `gap > 0`, decision D3).

## Shared types

| Name | JSON | Rule |
|---|---|---|
| `Id` | string | `^[0-9a-z]{6,32}$` (`v.Id`); `""` is read as `null` for optional ids |
| `ScopeId` (new primitive `v.ScopeId` in `core/validation.py`) | string | an `Id` or the literal `"unassigned"` |
| `AllocHours` (new primitive `v.AllocHours`) | number | `0.25 <= x <= 80`, finite, at most 2 decimals are meaningful (no rounding by the server; stored as sent) |
| `Pool` | string | `"internal"` or `"external"` |
| `Week` | string | ISO Monday date |

## Rights (additions)

Area `planning`, scope check by `rights.scope_for(principal, "planning", op)` (so a read-only API token is denied every
write). `Needs("planning")` is the declaration; the scope `all` is required by every route below (employee and
accountant have `(none, none)` and get **403 "Forbidden"**). A read-write token of a boss may write; none of these routes
is session-only. There is no `limited` scope for allocations.

| Route | area, op, full | token read-only |
|---|---|---|
| `GET /api/forecast` (changed) | planning, read | allowed |
| `POST /api/plan/simulate` (changed) | planning, read | 403 (read-write token only; the token guard refuses every unsafe method, a compute-only allow-list is a later deliberate change) |
| `GET /api/plan/day-table` (new) | planning, read | allowed |
| `GET /api/allocations` | planning, read | allowed |
| `POST /api/allocations` | planning, write | 403 |
| `PUT /api/allocations/{allocation_id}` | planning, write | 403 |
| `DELETE /api/allocations/{allocation_id}` | planning, write | 403 |
| `POST /api/allocations/bootstrap` | planning, write | 403 (also the preview: it is a write route, `confirm:false` is only a mode) |
| `POST /api/plan/apply`, scenario routes (changed bodies) | unchanged | unchanged |

`tests/backend/test_rbac.py` `EXPECTED` gets these rows (hand-written, `(method, path) -> (area, op, full)`):

```python
("GET", "/api/plan/day-table"): ("planning", "read", False),
("GET", "/api/allocations"): ("planning", "read", False),
("POST", "/api/allocations"): ("planning", "write", False),
("POST", "/api/allocations/bootstrap"): ("planning", "write", False),
("PUT", "/api/allocations/{allocation_id}"): ("planning", "write", False),
("DELETE", "/api/allocations/{allocation_id}"): ("planning", "write", False),
```

`rights.MATRIX` and `docs/roles.md` (generated table) are unchanged; the roles page lists the new routes under
`planning`. `GET /api/allocations/{id}` and `POST /api/allocations/{id}/cancel` belong to the subcontractor spec (area
`procurement`); they must use the path parameter name `allocation_id` too, and must not be registered by this branch.
The list route returns external rows only to a caller who also has `procurement` read (service check, once that area
exists; until the subcontractor branch lands there are no external rows).

## Storage: the shared `allocations` table

Collection `allocations`, registered in `core/repository/schema.py` and in `INTERNAL_COLLECTIONS` (no generic CRUD
route; the router below is the only door). Generic `list/get/save/find`, no new `Repository` method; both nullable
columns appear in the repository contract test; unknown fields round-trip in `extra`.

```
allocations  id  created:int  updated:int  user_id:id|null  supplier_id:id|null  incoming_offer_id:id|null
             project_id:id  contract_id:id|null  customer_id:id|null  start:date  end:date
             hours_per_week:float|null  total_hours:float|null  rate:float|null  status:str
             cancelled_from:date|null  cancel_reason:str|null                                  + extra
indexes      idx_allocations_user(user_id), idx_allocations_project(project_id)
```

(`supplier_id`, `status` and further indexes are created by the subcontractor migration, which only adds indexes on
this table.) Employee rows: `user_id` set, `supplier_id` null, `status: "active"`, `hours_per_week` set, the other
nullable columns null, `customer_id` = the project's customer at save time (server-set, may be null).

### Migration `NNNN-allocations`

`permita38 create-migration allocations`, in **all three** dialect folders, ordinal **0011** (head at the time of
writing is `0010-api_tokens`, id `c8af1eca`; `requires` = the head at merge time, rebase if another migration lands
first, AGENTS.md). No `BEGIN`/`COMMIT`. Rewrites no row. Header
`-- migration: <random 8-hex>` / `-- requires: c8af1eca`. Column list and types, identical in meaning in the three files:

| Column | sqlite | postgresql | mariadb |
|---|---|---|---|
| `id` | `TEXT PRIMARY KEY` | `TEXT PRIMARY KEY` | `VARCHAR(32) PRIMARY KEY` |
| `created`, `updated` | `INTEGER` | `BIGINT` | `BIGINT` |
| `user_id`, `supplier_id`, `incoming_offer_id`, `project_id`, `contract_id`, `customer_id` (all nullable) | `TEXT` | `TEXT` | `VARCHAR(32)` |
| `start`, `end`, `cancelled_from` | `TEXT` | `TEXT` | `VARCHAR(255)` |
| `hours_per_week`, `total_hours`, `rate` | `REAL` | `DOUBLE PRECISION` | `DOUBLE` |
| `status` | `TEXT` | `TEXT` | `VARCHAR(255)` |
| `cancel_reason` | `TEXT` | `TEXT` | `LONGTEXT` |
| `extra` | `TEXT` | `JSONB` | `LONGTEXT` |

Column order in the file: `id, created, updated, user_id, supplier_id, incoming_offer_id, project_id, contract_id,
customer_id, start, end, hours_per_week, total_hours, rate, status, cancelled_from, cancel_reason, extra`. Plus the
table constraint (quoted with `"` in sqlite/postgresql, with backticks in mariadb):

```sql
CONSTRAINT "allocations_one_owner" CHECK (
    ("user_id" IS NOT NULL AND "supplier_id" IS NULL) OR ("user_id" IS NULL AND "supplier_id" IS NOT NULL))
```

and `CREATE INDEX "idx_allocations_user" ON "allocations" ("user_id");` and `idx_allocations_project` on
`("project_id")`. `schema.py` mirrors the columns (`_BASE + "user_id:id supplier_id:id ..."`) and the indexes. The
migration tests (dialect folders in sync, one head, migrated SQLite equals `schema.py`) must stay green; if the
equality test does not know CHECK constraints it must be taught to ignore a named check (see open decision 9).

## Models (`core/models.py`)

```python
class AllocationIn(v.CheckedBody):  # strict: extra="forbid" -> unknown field = 422
    model_config = ConfigDict(extra="forbid")
    user_id: v.Id  # required
    project_id: v.Id  # required (never "unassigned": that scope is implicit)
    start: v.ISODate  # required
    end: v.ISODate  # required; end < start is 409 (service), see Errors
    hours_per_week: v.AllocHours  # required, 0.25..80


class Allocation(BaseModel):  # lenient response model, extra="allow", response_model_exclude_none
    id: str
    kind: Literal["employee", "external"]
    user_id: str | None
    supplier_id: str | None
    incoming_offer_id: str | None
    project_id: str
    contract_id: str | None
    customer_id: str | None
    start: str
    end: str
    hours_per_week: float | None
    total_hours: float | None
    rate: float | None
    status: str
    cancelled_from: str | None
    cancel_reason: str | None
    created: int
    updated: int
    # response only, ignored if sent:
    kind
    project_name: str
    user_name: str | None
    supplier_name: str | None
    planned_hours: float | None  # employee rows: see below
    planning_warnings: list[dict] | None  # only in POST/PUT answers
```

Every new field is added to both models with a hard bound (`docs/validation.md`; string ids by `v.Id`, the period by
`v.check_period`-style bound: `end - start <= 1830 days`, else 422). `kind` is derived: `employee` when `user_id` is set.
`planned_hours` (employee rows) = sum over the days in `[start, end]` that are on the person's workdays and neither a
public holiday nor an absence day of `hours_per_week / len(workdays)` (spec rule 2; busy time is not subtracted),
rounded to 2 decimals. External rows: `planned_hours` as defined by the subcontractor spec.

The what-if change (new, used by `ScenarioChanges.allocations`, `max_length=500`):

```python
class AllocationChange(v.CheckedBody):  # extra="forbid"
    id: v.OptId = None  # null = a new allocation
    delete: bool = False
    user_id: v.OptId = None
    project_id: v.OptId = None  # not "unassigned"
    start: v.OptDate = None
    end: v.OptDate = None
    hours_per_week: v.AllocHours | None = None
    # validators: check_period(start, end) (422); delete=true needs id (422); id=null needs user_id, project_id,
    # start, end and hours_per_week (422); omitted fields keep the stored value
```

`SimulateIn` gains `project_id: v.ScopeId | None = None`. `SuggestStartIn` gains `project_id: v.OptId = None`.
`ScenarioChanges` gains `allocations: list[AllocationChange] = Field(default=[], max_length=500)` (a stored scenario
without the key reads as `[]`).

## Routes

### `GET /api/forecast` (changed)

Query: `weeks` (1..156, default 26), `user_id`, `offers` (int, default 1), **new** `project_id` (`ScopeId`) and
`by_project` (int `0|1`, default `1`; other values 422).

* `project_id` unknown id: 404 `"Project not found"`. `project_id=unassigned` is valid. With `project_id`, the week
  rows (`capacity`, `confirmed`, `utilisation`, revenue fields, ...) and `totals` are **that scope's**, `projects[]`
  contains only that scope, and warnings are only that scope's. `user_id` and `project_id` combine (intersection).
* Every week row gains `gap`, `gap_confirmed`, `gap_expected` (sum of the scope gaps; hours) and, unless
  `by_project=0`, `projects[]`. The row's `status` is `over` when `gap > 0`, else `status_for(capacity, demand)`.
  `utilisation` is `used / capacity` in percent (1 decimal; `null` for capacity 0 with work; 0.0 without work), where
  `used` = per day and scope `max(confirmed + binding, allocation)` of the employees (the allocation is unscaled) plus
  the subcontractors' confirmed + binding (decision D4, 2026-10-26). The team row sums `used` over the scopes.
  Without allocations `used` equals `confirmed + binding_offers`, as before.
* `projects[]` (one object per scope with any capacity, any demand, or any allocation overlapping the week, plus
  **always** the `unassigned` scope; sorted by `name` case-insensitive, then `project_id`; `unassigned` last). A project
  that no longer exists is named `"(deleted project)"`. Fields:

| Field | Type | Meaning |
|---|---|---|
| `project_id` | Id or null | null = unassigned |
| `name` | string | project name, `"Unassigned"` for null |
| `capacity`, `confirmed`, `binding_offers`, `expected_offers` | number | sums of the two pools |
| `free_after_confirmed`, `free_worst_case` | number | `capacity - confirmed`, `capacity - confirmed - binding_offers` (may be negative) |
| `utilisation` | number or null | as above (decision D4) |
| `allocated` | number >= 0 | the unscaled employee allocation hours of the scope in the week (hours; 0 without allocations) |
| `status` | `ok` / `tight` / `over` | rule 7 |
| `gap`, `gap_confirmed`, `gap_expected` | number >= 0 | `internal.gap + external.gap` (worst case / confirmed only / confirmed + expected) |
| `internal`, `external` | `{capacity, assigned, gap}` | the pools; `assigned` = confirmed + binding |
| `external_cost` | number | subcontractor cost booked to the project that week (0.0 until the subcontractor feature is present) |

* `by_project=0`: no `projects[]`, no per-project warnings (`capacity.gap`, `allocation.no_demand`,
  `offer.project_gap` for scopes are omitted); `gap*` stay; `capacity.over` uses the old team rule. Team rows are
  identical to `by_project=1`.
* `totals` keeps its keys and gains `gap` (sum over the weeks, hours) and, unless `by_project=0`,
  `gap_by_project: [{project_id, name, gap}]` (only scopes with `gap > 0`, sorted by `gap` desc then name). `over_weeks`
  counts rows with status `over` (including by gap).
* `warnings` as in "Warning codes". The existing `contracts[]` and `expired_offers[]` keys are unchanged; each
  `contracts[]` entry gains `project_id` (Id or null).

Errors: 401; 403 (role or token without planning); 404 project; 422 query validation.

### `POST /api/plan/simulate` (changed)

Body (`SimulateIn`, strict): `changes` (`ScenarioChanges`, now with `allocations`), `weeks` (1..156, 26), `first`
(date), `user_id`, **new** `project_id` (`ScopeId`). Writes nothing. Answer = the forecast answer for `by_project=1`
(`weeks[]` with `projects[]`, `totals`, `warnings`; and the existing `entitlement` when `user_id` is given) plus:

```json
{
  "delta": {
    "gap": 25.0,
    "projects": [{"project_id": "a1b2c3d4", "name": "Demo project P", "base_gap": 20.0, "gap": 45.0, "delta": 25.0}]
  }
}
```

and every `weeks[].projects[]` entry gains `delta` (that week's project `gap` minus the same week's gap in the base,
number, may be negative or 0.0); every week row gains `delta` as well (team `gap` against base). The base is the same
world without `changes`, with the same `weeks`, `first`, `user_id`, `project_id`. `delta.projects` lists scopes where
`base_gap` or `gap` is above 0, same order as `gap_by_project`. A change that moves a contract changes only the rows
of its own project(s); every other project's row is bit-identical to the base (test).

Errors: 404 for an unknown `project_id`; 404 `"Allocation not found"` for an `allocations[].id` that does not exist;
409 `"This allocation cannot be changed here"` for an external row; 404 unknown `user_id`/`project_id` inside a change;
422 validation (including the change rules above). The `changes.allocations` are applied to the in-memory world
(replace/append/remove) before the engine runs.

### `GET /api/plan/day-table` (new)

Query: `from` (date, required), `to` (date, required), `user_id`, `project_id` (`ScopeId`). Because `from` is a Python
keyword the parameter uses `Query(alias="from")`.

```json
{
  "from": "2026-03-02", "to": "2026-03-03",
  "scope_names": {"a1b2c3d4": "Demo project P", "e5f6a7b8": "Demo project Q", "unassigned": "Unassigned"},
  "days": [
    {"date": "2026-03-02", "cap": 8.0, "busy": 0.0, "vacation": 0.0, "holiday": 0.0, "absence_other": 0.0,
     "confirmed": 8.0, "binding": 0.0, "expected": 0.0, "names": [],
     "scopes": {
       "a1b2c3d4": {"internal": {"cap": 5.0, "assigned": 6.0, "confirmed": 6.0, "binding": 0.0, "expected": 0.0, "gap": 1.0, "gap_confirmed": 1.0},
                    "external": {"cap": 0.0, "assigned": 0.0, "confirmed": 0.0, "binding": 0.0, "expected": 0.0, "gap": 0.0, "gap_confirmed": 0.0}},
       "e5f6a7b8": {"internal": {"cap": 3.0, "assigned": 2.0, "confirmed": 2.0, "binding": 0.0, "expected": 0.0, "gap": 0.0, "gap_confirmed": 0.0},
                    "external": {"cap": 0.0, "assigned": 0.0, "confirmed": 0.0, "binding": 0.0, "expected": 0.0, "gap": 0.0, "gap_confirmed": 0.0}}
     }}
  ]
}
```

* One entry per calendar day from `from` to `to` inclusive (also weekends and holidays; cap is 0 there). `cap`, `busy`,
  `vacation`, `holiday`, `absence_other` (hours), `names`, `confirmed`, `binding`, `expected` are the existing `Day`
  fields (team total, or the filtered person's). `cap` is the sum of all scope `cap`s (internal + external).
* `scopes` has a key for every scope with `cap > 0` or any demand that day (`unassigned` included when it applies);
  scopes with nothing are omitted (the UI treats a missing key as zeros). With `project_id` only that key can appear.
  `assigned` = `confirmed + binding`. Hours rounded to 6 decimals.
* The `external` block maps to the subcontractor spec: `cap` = `external_cap`, `assigned` = `external_assigned`,
  `gap` = `external_gap`.
* `user_id` restricts capacity and demand to that person's (spec "Day table per project"); unknown user: 404
  `"User not found"`; unknown project: 404 `"Project not found"`.
* Range: `to < from` or more than **190 days** (`(to - from).days + 1 > 190`): **400** `"The range may cover at most 190 days"`
  / `"'to' is before 'from'"`. Invalid dates: 422.

### Allocations

`GET /api/allocations?project_id=&user_id=&supplier_id=&status=`: list, sorted by `start` desc then `id`; all filters
optional (`project_id` is an `Id`, not `unassigned`; `status` is `active` or `cancelled`; invalid values 422). Answer is a
JSON array of `Allocation` (no `planning_warnings`).

```json
[
  {"id": "9f3a1c20", "kind": "employee", "user_id": "u1b2c3d4", "user_name": "Demo Boss", "project_id": "a1b2c3d4",
   "project_name": "Demo project P", "customer_id": "c0ffee01", "start": "2026-03-02", "end": "2026-03-27",
   "hours_per_week": 25.0, "status": "active", "planned_hours": 100.0, "created": 1772400000, "updated": 1772400000}
]
```

`POST /api/allocations` creates an employee allocation. Body `AllocationIn`; answer **201** with the `Allocation` plus
`planning_warnings[]` (possibly empty):

```json
{"user_id": "u1b2c3d4", "project_id": "a1b2c3d4", "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 30.0}
```

`PUT /api/allocations/{allocation_id}` replaces the four editable fields (whole `AllocationIn`; `user_id` may change);
answer **200** with the `Allocation` + `planning_warnings[]`. `DELETE /api/allocations/{allocation_id}`: **204**, no
body (allowed on a closed project).

| Status | When |
|---|---|
| 401 | not signed in |
| 403 | role lacks `planning` write/read, or the token is read-only on a write |
| 404 | `"Not found"` unknown allocation; `"User not found"`; `"Project not found"` |
| 409 | `"This allocation cannot be changed here"` for an external row on PUT/DELETE; `"The project is closed"` when the (new) project's status is `done` or `archived` (POST and PUT; `paused` is allowed); `"The user is not active"`; `"The end date is before the start date"` |
| 422 | body validation: missing/unknown field (strict), `hours_per_week` outside 0.25..80 or not finite, bad dates/ids, period longer than 1830 days |

The over-allocation is never an error (D2): it is a warning in `planning_warnings`.

`POST /api/allocations/bootstrap` (spec D1). Body `{"confirm": false}` (`confirm` default `false`, so `{}` is a
preview; there is no 400 on this route). Algorithm (one function, used by preview and confirm, so the preview equals
the write):

1. Contracts: every contract whose status is not `done`, with a `project_id` whose project is not closed.
2. Assignees: the same rule `planning.load` uses (existing, active `user_ids` of the contract, else the boss).
3. For each (contract, assignee): `peak` = the maximum over the Monday-Sunday weeks of the contract period of that
   person's demand hours from this contract in the week (the day-table `item_hours`, holidays and absence days
   included, spec rule 5), rounded **up** to the next multiple of 0.25 h and clamped to at most 80. `peak <= 0`: skip.
4. Proposal = `{user_id, project_id, start = contract.start, end = contract.end, hours_per_week = peak}`. A contract
   period longer than 1830 days is skipped with reason `period_too_long`.
5. Skip a proposal when the person already has an active allocation to that project whose period contains the
   proposal's period (reason `already_allocated`). This is what makes a second run create nothing.
6. Open offers are not bootstrapped.
7. More than 500 proposals: 409 `"Too many allocations to create at once (max 500)"`.

```json
{
  "confirm": false,
  "created": false,
  "allocations": [
    {"user_id": "u1b2c3d4", "user_name": "Demo Boss", "project_id": "a1b2c3d4", "project_name": "Demo project P",
     "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 30.0, "source_contract_id": "d4e5f6a7",
     "source_contract_title": "Contract C_P"}
  ],
  "skipped": [{"user_id": "u1b2c3d4", "project_id": "e5f6a7b8", "source_contract_id": "b1b1b1b1", "reason": "already_allocated"}],
  "warnings": []
}
```

With `{"confirm": true}`: the same proposals are written in one transaction (all or none), the answer is the same
shape with `"confirm": true`, `"created": true` and each `allocations[]` entry additionally carries the new `id` and
`kind: "employee"`. `warnings[]` = `allocation.employee_over_allocated` / `allocation.overlap_same_project` of the
resulting set (existing plus proposed), never blocking. `source_contract_id` is informational and is **not** stored.
Banner rule for the UI (computed by the client from existing routes): show it when `GET /api/forecast` has
`totals.gap > 0` and `GET /api/allocations?status=active` contains no `kind: "employee"` row.

### Changed bodies

* `POST /api/plan/apply` and `POST /api/plan/scenarios*`: `changes.allocations` is accepted (see `AllocationChange`).
  `apply` with `confirm: true` writes allocation rows (create when `id` is null, update, or delete) in the same
  transaction as the other changes and the answer gains `allocation_warnings: [...]` (the D2 warnings of the rows
  written). `confirm: false`: 400 `"Confirm the change (confirm: true) before anything is written"` (existing).
* `POST /api/plan/check-offer` (body `DocumentIn`, unchanged): an offer with a `project_id` is checked against that
  project's weeks, see `offer.project_gap`; a project-less offer is checked against the `unassigned` scope.
* `POST /api/plan/suggest-start`: optional `project_id`; `what-must-move` and `suggest-vacation`: bodies unchanged,
  behaviour per spec ("What-if drafts per project", last bullet). Response shapes unchanged.
* `GET /api/projects/{rid}/planning` and `planning_warnings` on records: shapes unchanged; they may contain the new
  warning codes.

## Warning codes

Shape of every warning (unchanged): `{code, severity, field, message, details, confirmable}`; none blocks saving.

| Code | severity | field | confirmable | `details` (all keys always present) |
|---|---|---|---|---|
| `capacity.gap` | warning | `weeks` | false | `{project_id, project_name, week, pool, assigned, capacity, gap, contributions[]}` |
| `capacity.over` | warning | `weeks` | false | `{week, over_by, contributions[], scope}` (`scope`: `"unassigned"` or `"team"`; the old keys keep their meaning) |
| `allocation.employee_over_allocated` | warning | `allocations` on save, `weeks` in the forecast | true | `{user_id, week, allocated, weekly_hours, over_by, reason}` |
| `allocation.no_demand` | info | `weeks` | false | `{allocation_id, project_id, project_name, week, capacity}` |
| `allocation.overlap_same_project` | info | `allocations` | false | `{user_id, project_id, week, allocation_ids[]}` |
| `offer.project_gap` | warning | `weeks` | false | `{project_id, project_name, week, pool, assigned, capacity, gap, options[]}` |

* `capacity.gap`: one per project, week **and pool** with `gap > 0` (so at most two per project and week); never for the
  `unassigned` scope (it keeps `capacity.over`). `pool` is `internal` or `external`; `contributions[]` are the items of
  that project/pool/week with hours above 0, in the existing shape `{kind, id, title, hours, binding}`. Message: names
  the project and says that other projects' free hours do not cover it (example in the spec). `capacity.over` is
  emitted per week whose `unassigned` scope has status `over` (with `by_project=0`: per week whose team row has status
  `over` by the old utilisation rule, `scope: "team"`); with `project_id` filter only for the filtered scope.
* `allocation.employee_over_allocated`, judged **per day**: a day is scaled when the sum of the person's active
  allocations of that day (`hours_per_week / available days of the week` each, available = workday without holiday or absence) is above the day's real capacity (`weekly_hours / workdays`,
  0 on a public-holiday or absence day, busy time ignored). A week warns when at least one day is scaled. `allocated` =
  the day-weighted allocation hours of the week, `over_by` = the hours scaled away (sum over the scaled days of
  allocated minus real), `weekly_hours = allocated - over_by`. `reason` is `"weekly_hours"` when the week is already over-allocated without taking holidays and absences out (checked first), else `"time_off"` when it is over-allocated once they are taken out (the allocations are then spread over fewer available days). Without time off and with
  allocations covering the whole week this is `allocated = sum of hours_per_week`, `weekly_hours` = contractual hours.
  P Monday-Tuesday 40 h/week and Q Wednesday-Friday 40 h/week give no warning. E4: `allocated 40, weekly_hours 32,
  over_by 8, reason "time_off"`. **On save** (POST/PUT/bootstrap) only `reason: "weekly_hours"` is checked, for each
  offending week of the saved period, earliest first, at most 10 warnings; **in the forecast** both reasons, one per
  person and week, and only for the weeks shown (with `user_id` only that person).
* `allocation.no_demand`: forecast warning, one per employee allocation (and per external allocation) and week in which
  the allocation's pool of its project has `capacity > 0` and `assigned == 0`; `capacity` = that pool's capacity in the
  week. Not emitted with `by_project=0`.
* `allocation.overlap_same_project`: on save/bootstrap only: the same person has two or more allocations to the same
  project overlapping a week; one warning per such week (at most 10), `allocation_ids` includes the saved row.
* `offer.project_gap`: returned by `check-offer` (and `feasibility`) for an offer with a project, **replacing**
  `capacity.offer_over` for it. Computed with the offer added to the world (worst case: confirmed + binding): one
  warning per touched week of the offer's project, pool and `gap > 0`; `gap` is the project's whole gap that week
  (including gaps that exist without the offer, E7). `options` is always
  `["shorten", "start_later", "allocate_more", "reduce_scope"]` (machine keys; the UI words them).
  A project-less offer keeps `capacity.offer_over`, checked against the `unassigned` scope rows.

## Worked examples (hand computed; the fixtures the web UI builds against and the tests assert)

Setup of the spec: today = Monday 2026-03-02, no public holiday 2026-03-02..2026-03-27, boss "Demo Boss" (40 h/week,
Mon-Fri, 8 h/day), projects P (`a1b2c3d4`) and Q (`e5f6a7b8`), period 2026-03-02..2026-03-27. Request
`GET /api/forecast?weeks=4`. Fields not shown (`week_end`, revenue fields, `vacation_hours`, ...) keep their existing
values/meaning and are not asserted here. All four weeks are identical in E1 and E2; `totals` is over the four weeks.

### Example A (spec E1): two projects, one person, no netting

Data: allocations boss->P 25 h/week, boss->Q 15 h/week; contract C_P 30 h/week, C_Q 10 h/week (both boss). Per day:
cap P 25/5 = 5, Q 15/5 = 3 (sum 8 = real, no scaling); demand P 6, Q 2. P gap 1/day = 5/week; Q 0, idle 5/week.

```json
{
  "weeks": [{
    "week": "2026-03-02", "capacity": 40.0, "confirmed": 40.0, "binding_offers": 0.0,
    "free_after_confirmed": 0.0, "free_worst_case": 0.0, "utilisation": 112.5, "status": "over",
    "gap": 5.0, "gap_confirmed": 5.0, "gap_expected": 5.0,
    "projects": [
      {"project_id": "a1b2c3d4", "name": "Demo project P", "capacity": 25.0, "confirmed": 30.0, "binding_offers": 0.0,
       "expected_offers": 0.0, "free_after_confirmed": -5.0, "free_worst_case": -5.0, "utilisation": 120.0,
       "status": "over", "gap": 5.0, "gap_confirmed": 5.0, "gap_expected": 5.0,
       "internal": {"capacity": 25.0, "assigned": 30.0, "gap": 5.0},
       "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}, "external_cost": 0.0},
      {"project_id": "e5f6a7b8", "name": "Demo project Q", "capacity": 15.0, "confirmed": 10.0, "binding_offers": 0.0,
       "expected_offers": 0.0, "free_after_confirmed": 5.0, "free_worst_case": 5.0, "utilisation": 100.0,
       "status": "tight", "gap": 0.0, "gap_confirmed": 0.0, "gap_expected": 0.0,
       "internal": {"capacity": 15.0, "assigned": 10.0, "gap": 0.0},
       "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}, "external_cost": 0.0},
      {"project_id": null, "name": "Unassigned", "capacity": 0.0, "confirmed": 0.0, "binding_offers": 0.0,
       "expected_offers": 0.0, "free_after_confirmed": 0.0, "free_worst_case": 0.0, "utilisation": 0.0,
       "status": "ok", "gap": 0.0, "gap_confirmed": 0.0, "gap_expected": 0.0,
       "internal": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0},
       "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}, "external_cost": 0.0}
    ]
  }],
  "totals": {"gap": 20.0, "over_weeks": 4, "gap_by_project": [{"project_id": "a1b2c3d4", "name": "Demo project P", "gap": 20.0}]},
  "warnings": [
    {"code": "capacity.gap", "severity": "warning", "field": "weeks", "confirmable": false,
     "message": "Week of 2026-03-02, project Demo project P: 30 h are planned but only 25 h are allocated to it (gap 5 h). Other projects' free hours do not cover it.",
     "details": {"project_id": "a1b2c3d4", "project_name": "Demo project P", "week": "2026-03-02", "pool": "internal",
                 "assigned": 30.0, "capacity": 25.0, "gap": 5.0,
                 "contributions": [{"kind": "contract", "id": "d4e5f6a7", "title": "Contract C_P", "hours": 30.0, "binding": false}]}}
  ]
}
```

(The real answer has four weeks and four such warnings; the team row alone would say `tight`/100 % without the gap, D3
makes it `over`. Q's 5 idle hours are not given to P. No `allocation.no_demand` because Q has demand.) The day table for
`from=2026-03-02&to=2026-03-02` is the day object in the day-table section above (P: cap 5, assigned 6, gap 1; Q: cap 3,
assigned 2, gap 0; day `cap` 8, `confirmed` 8).

### Example B (spec E2): existing data, no allocation, then bootstrap

Data: boss, contract C_P 30 h/week (project P), contract N 8 h/week (no project), no allocation rows. Before the
upgrade the planner showed capacity 40, confirmed 38, utilisation 95.0, `tight`.

Without allocations (week 1, four identical weeks, `totals.gap` 120.0, `over_weeks` 4; team capacity/confirmed/utilisation
as before, `status` and `gap` are the only changes):

```json
{"week": "2026-03-02", "capacity": 40.0, "confirmed": 38.0, "binding_offers": 0.0, "free_after_confirmed": 2.0,
 "free_worst_case": 2.0, "utilisation": 95.0, "status": "over", "gap": 30.0, "gap_confirmed": 30.0, "gap_expected": 30.0,
 "projects": [
   {"project_id": "a1b2c3d4", "name": "Demo project P", "capacity": 0.0, "confirmed": 30.0, "free_worst_case": -30.0,
    "utilisation": null, "status": "over", "gap": 30.0, "gap_confirmed": 30.0, "gap_expected": 30.0,
    "internal": {"capacity": 0.0, "assigned": 30.0, "gap": 30.0}, "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}},
   {"project_id": null, "name": "Unassigned", "capacity": 40.0, "confirmed": 8.0, "free_worst_case": 32.0,
    "utilisation": 20.0, "status": "ok", "gap": 0.0, "gap_confirmed": 0.0, "gap_expected": 0.0,
    "internal": {"capacity": 40.0, "assigned": 8.0, "gap": 0.0}, "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}}
 ]}
```

(Other per-project keys as in example A.) Team `free_worst_case` 2.0 = -30 + 32. `POST /api/allocations/bootstrap`
with `{"confirm": false}`: peak weekly demand of C_P for the boss = 6/day x 5 = 30 -> 30.0 (already a multiple of
0.25); N has no project and is not bootstrapped:

```json
{"confirm": false, "created": false,
 "allocations": [{"user_id": "u1b2c3d4", "user_name": "Demo Boss", "project_id": "a1b2c3d4", "project_name": "Demo project P",
                  "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 30.0,
                  "source_contract_id": "d4e5f6a7", "source_contract_title": "Contract C_P"}],
 "skipped": [], "warnings": []}
```

With `{"confirm": true}` the same row is created (201 is not used: the route answers **200**). A second `confirm:true` call
answers `"allocations": []`, `"skipped": [{..., "reason": "already_allocated"}]`, `"created": true` and writes nothing.
After the bootstrap, week 1: P `capacity 30.0, confirmed 30.0, utilisation 100.0, status "tight", gap 0.0`; unassigned
`capacity 10.0` (2/day), `confirmed 8.0`, `free_worst_case 2.0`, `utilisation 80.0`, `status "ok"`; team
`capacity 40.0, confirmed 38.0, utilisation 95.0, status "tight", gap 0.0`; `totals.gap 0.0`, no `capacity.gap` warning.

### Example C (spec E5): employee and subcontractor in one project (needs the subcontractor feature)

Data: boss allocation P 40 h/week (8/day); subcontractor S allocation P 20 h/week (Mon-Thu, 5/day, rate 80.00);
contract C_P 50 h/week with `user_ids` [boss, S]. Shares 1/2: boss 25/5 = 5/day (Mon-Fri); S 25/4 = 6.25/day
(Mon-Thu). Internal cap 8, assigned 5, gap 0. External cap 5, assigned 6.25, gap 1.25 x 4 days = 5. Not netted.
Cost 20 h x 80.00 = 1600.00. Week 1 `projects[]` entry of P (Q and unassigned unchanged and zero):

```json
{"project_id": "a1b2c3d4", "name": "Demo project P", "capacity": 60.0, "confirmed": 50.0, "binding_offers": 0.0,
 "expected_offers": 0.0, "free_after_confirmed": 10.0, "free_worst_case": 10.0, "utilisation": 83.3, "status": "over",
 "gap": 5.0, "gap_confirmed": 5.0, "gap_expected": 5.0,
 "internal": {"capacity": 40.0, "assigned": 25.0, "gap": 0.0},
 "external": {"capacity": 20.0, "assigned": 25.0, "gap": 5.0}, "external_cost": 1600.0}
```

`capacity.gap` warning: `pool: "external"`, `assigned 25.0`, `capacity 20.0`, `gap 5.0`. Team row: `capacity 60.0`, `confirmed 50.0`,
`utilisation 83.3`, `status "over"`, `gap 5.0`. Variant with `user_ids` [boss] only: assigned 10/day vs cap 8 -> internal
`{capacity 40, assigned 50, gap 10}`, external `{capacity 20, assigned 0, gap 0}` (idle but paid, `external_cost`
1600.0), project `gap 10.0`, `capacity 60.0`, `utilisation 83.3`, `status "over"`.

The spec's cases E3, E4, E6, E7 and E8 are asserted by the backend oracle tests exactly as written in the spec
(E6 additionally through `simulate`: P `base_gap 20.0`, `gap 45.0`, `delta 25.0`, Q rows identical to the base).

## Work packages and file ownership

Two parallel tracks. A file is owned by exactly one package; a package never edits another package's files (ask the
architect). Backend merges first or independently of the web UI; the web UI builds against the fixtures above.

**Backend (one branch `feat/planner-per-project-backend`)**

| Package | Owns |
|---|---|
| BE-A engine | `permita38/core/service/planning.py`; tests `tests/backend/test_planner_per_project.py` (E1-E8 literal oracle), `tests/backend/randomized/` additions (ring-fence property) |
| BE-B service and warnings (after A) | `permita38/core/service/plan_service.py`, `permita38/core/service/planning_warnings.py`; tests for warnings, suggestions, simulate delta, scenarios |
| BE-C storage and API (independent) | `permita38/core/repository/schema.py`, `permita38/core/schema/migrations/{sqlite,postgresql,mariadb}/0011-allocations.sql`, `permita38/core/models.py`, `permita38/core/validation.py`, new `permita38/core/service/allocations.py` (CRUD + bootstrap), `permita38/core/service/__init__`/service registry (deps wiring), new `permita38/api/routers/allocations.py`, `permita38/api/routers/planning.py` (new params, day-table route), router registration in `permita38/api/server.py`, `api/openapi.json` (`make generate-openapi`), `tests/backend/test_rbac.py` (EXPECTED rows), `tests/backend/test_repository_contract.py`, migration tests, API tests (`tests/backend/test_allocations_api.py`), `docs/planning.md`, `docs/roles.md` (route list only) |

BE-C stubs the engine calls it needs behind the service interfaces until BE-A/B land (the `/api/plan/day-table` route
calls `planning.day_table`). `core/models.py` is touched only by BE-C (it also defines `AllocationChange` and the
`ScenarioChanges.allocations` field that BE-B consumes).

**Web UI (one branch `feat/planner-per-project-web`)**

| Package | Owns |
|---|---|
| WEB-A forecast tab | `web/app.js` (`PAGES.plan` forecast part only), `web/style.css` additions under a `.pp-` class prefix |
| WEB-B plan and vacation | `web/planner.js`, `web/planner.css` |
| WEB-C allocations dialog and bootstrap | new `web/allocations.js` (dialog, table, bootstrap preview; exposes `openAllocations({projectId?, userId?})`), its `<script>` line in `web/index.html`, the two entry-point buttons (project page, person page) added by WEB-A inside `web/app.js`/`web/app2.js` as a one-line call to `openAllocations` |
| Web tests and fixtures | `tests/web/test_planner_per_project_ui.py` (fixtures served by route interception, no backend), `tests/web/test_planner_per_project_live.py` (real backend, after the backend branch), `tests/web/fixtures/planner_per_project/*.json` (extracted verbatim from examples A, B, C and the day-table section), one seeded step in `tests/web/e2e_random/` |

Web A, B and C do not share a file except `web/app.js`/`web/app2.js`/`web/index.html`: WEB-A owns the `PAGES.plan`
region of `app.js`; the entry-point lines in `app.js`/`app2.js` and the `index.html` script tag are added by WEB-C in a
**single small commit made first**, before the other web packages start. The UI never computes capacity or gaps; it
draws server numbers (spec). Backend and web branches meet in the live tests; neither touches `docs/contracts/`.

## Open design decisions (picked by the architect for this contract; confirm or change before merge)

1. **`end < start` on the allocation routes is 409** (as the spec says), while `AllocationChange` in a what-if draft
   validates the period with 422 like the other change types. Alternative: 422 on both.
2. **`capacity.gap` is per project, week and pool** (two warnings possible per week) because `details.pool` is single.
3. **Project-less offers are checked against the `unassigned` scope** (spec rule 4/5), not against the team rows as
   the spec's wording "old team-wide check" says; with no allocations both are identical.
4. **Bootstrap never answers 400** (`confirm` defaults to false = preview); 200 for confirm (no 201). The spec's "400
   confirm missing on bootstrap" applies to `plan/apply` only.
5. **Bootstrap idempotence rule** (skip when an existing allocation of the same person and project contains the
   contract period) and **no `contract_id` stored** on employee rows.
6. **Range errors of `day-table` are 400** (spec "400 beyond"), including `to < from`.
7. **`GET /api/allocations` is declared `planning` read** (boss only); external rows are added only for callers who
   also have `procurement` read, so a procurement-only role gets 403 until the subcontractor spec relaxes it.
8. **Warning caps**: `employee_over_allocated` and `overlap_same_project` on save return at most 10 weeks each;
   `time_off` over-allocation ignores calendar busy time.
9. **The CHECK constraint is not expressible in the `schema.py` DSL.** If the "migrated SQLite equals `schema.py`"
   test compares constraints, the backend agent teaches that test to ignore the named `allocations_one_owner` check
   (and says so in its report); the constraint stays in the SQL.
10. **`projects[]` membership**: scopes with capacity, demand or an overlapping allocation, plus always `unassigned`;
    ordered by name. `simulate` always returns `projects[]` (no `by_project` switch there).
11. **Allocation hours need no step** (0.25..80, any decimal), and a project in status `paused` accepts allocations;
    only `done` and `archived` are closed.
