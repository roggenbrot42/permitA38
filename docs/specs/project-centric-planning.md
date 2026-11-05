# Specification: project-centric planning (assignments, verdicts, internal projects, strict schema)

Status: **draft for the product owner** (2026-11-05). Written by the architect. The implementer does not decide any of the
points under "Open decisions". Where this spec and an older document disagree, this spec wins (see "Supersedes").

## Why

The planner of `docs/specs/planner-per-project.md` was built around a person on every contract (`user_ids`), two pools
per project (employees and subcontractors, never netted), a per-day gap and a utilisation that also counts idle
allocations (D4). In real use nobody could explain its numbers:

* the web UI has no field for a contract's or offer's `user_ids`, so every contract landed silently on the boss;
* subcontractor hours sit in the external pool, which only covers contracts that name the subcontractor, so bought
  hours could **never** close a gap;
* an incoming offer carries its hours twice (lines and `plan`), and the allocation was built from the plan, so the
  planned cost differed from the offer;
* the same "spread hours over days" rule is written four times with different results (the allocations table shows
  other hours than the planner uses);
* `capacity.over` means different things depending on a query flag, the week status is decided by two rules, and
  the revenue basis is a per-week either/or.

## Product owner decisions (given, not open)

1. **Projects are the unit of work.** People (employees and subcontractors) are *assigned* to a project with hours.
   That is the project's supply. Offers say what could be, contracts say what must be, invoices reflect that and give
   income. There is **no person on a contract or an offer**.
2. **Not all work is customer work.** A project has a `kind`: `customer` or `internal` (administration, the
   accountant, the secretary, the boss's admin work, an internal tool). Approach of MOCO/Harvest/Toggl: internal work
   is an ordinary project flagged internal, not a separate "accounts" concept.
3. **Booked time counts (decision 1).** Hours already booked on a project reduce what its contracts still need:
   remaining = promised − booked.
4. **Granularity (decision 2).** A contract with `total_hours` is checked over its whole period; a contract with
   `hours_per_week` is checked week by week.
5. **Unassigned time is only suggested (decision 3).** A project's shortfall is never filled automatically from a
   person's unassigned hours; the planner names them as a remedy.
6. **Subcontractor hours come from the offer lines, and the assignment is editable (decision 4).**
7. **The database enforces the rules.** Every table this work touches gets CHECK constraints and NOT NULL in every
   dialect; tests prove the database refuses bad rows.

## Vocabulary (UI and docs)

| Term | Meaning |
|---|---|
| **assignment** | hours of one person or subcontractor given to one project for a period: `hours_per_week` **or** `total_hours`. Stored in `allocations` (table and API path keep their names; the UI says "assignment") |
| **capacity** | a person's real hours on a day: `weekly_hours / workdays` on a workday that is not a public holiday, not an absence day, minus calendar busy time; never below 0 |
| **supply** of a project | the hours its assignments give it (after scaling, see rule S3) |
| **demand** | what contracts (must) and offers (could) of a project still need |
| **remaining** | promised − booked, never below 0 |
| **covered** | the part of the remaining hours that the supply can deliver by the rules below |
| **shortfall** | remaining − covered |
| **unassigned** | capacity − all assignments (customer and internal) of a person, never below 0 |

There is no "gap", no "pool", no "used", no "utilisation" any more. A week row shows how a person's time is
**divided**; whether contracts fit is shown on the **contract and project**, never as a week colour.

## Rules

`today` is the server date. **Booked** hours are the time entries dated up to and including `today`. **Supply**
counts from the day **after** `today`. So nothing is counted twice and nothing that already happened is planned.

### Supply (the only part computed per day)

S1. **One spreading function** `spread(owner, assignment, calendar) -> {date: hours}` in
    `core/service/planning/spread.py`, pure, used by **every** caller (list under "Callers"). For an employee the
    available days are their workdays minus public holidays minus absence days; for a subcontractor its workdays minus
    public holidays minus its unavailable windows.
    * `hours_per_week`: per available day of an ISO week `hours_per_week / available days of that week`. A holiday or
      a day off moves the hours to the other days of that week; a week with no available day gives 0 (warning S4).
    * `total_hours`: the hours **still open** (`total_hours − hours the owner booked on the project inside the period`,
      at least 0; subcontractors normally book nothing) spread evenly over the owner's available days from the day
      after `today` to the end. A total assignment is a budget that does not expire because a week passed.
S2. Past days give no supply. Days before the assignment start or after its end give none.
S3. **Over-assignment** (recommended, Open decision D4): when a person's assignments of one day add up to more than
    that day's capacity, every assignment of that day is scaled by `capacity / sum`. Subcontractors are not scaled (we
    do not know their other customers).
S4. Warning `assignment.over_capacity` per person and week in which S3 scaled something away, with the arithmetic
    (example E4). Reason `time_off` when the week has holidays or absences that cause it, else `weekly_hours`.
S5. Internal projects: their assignments **are** the plan. They consume capacity and never have demand, shortfall or
    "no demand" warnings. Optional `budget_hours`: warning `internal.over_budget` when booked + assigned future hours
    exceed it.

### Demand

D1. **Contracts** of a project with status `confirmed`; **offers** with status `sent`, a plan, not converted to a
    contract and `valid_until` not passed (as today). Offers are always planned **after** all contracts ("must"
    before "could").
D2. A weekly item (`hours_per_week`) demands, per ISO week, `hours_per_week × weekdays (Mon–Fri) of the week inside
    its period / 5`. Holidays and vacation do **not** reduce the promise (they reduce the supply).
D3. A total item demands `total_hours` over its period.
D4. **Booked attribution** (recommended, Open decision D5). Time entries (billable or not) on the project, dated
    inside an item's period and up to `today`, are attributed week by week: first to that week's weekly items (up to
    their weekly demand, contracts before offers), the rest to total items whose period contains the entry date,
    earliest end first, up to their `total_hours`. Hours left over are reported as `booked_unattributed` (info).
D5. Remaining of a weekly item: for the current week `max(0, demand − booked attributed in this week)`, for every
    later week its demand. Past weeks are history and are not checked.
D6. Remaining of a total item: `max(0, total_hours − booked attributed)`.

### Covering (per project, deterministic, explainable)

C1. Weekly contracts take their week's remaining demand from that week's supply first. If the week's supply is
    smaller, the difference is the item's **weekly shortfall** of that week. What they take is taken from every day of
    the week in proportion to the day's supply, so the leftover of a day is `supply × (1 − taken / week supply)`.
C2. Total contracts then take leftover hours **earliest end first** (ties: earlier start, then id), each from its own
    window (`max(start, today + 1)` to `end`), earliest days first, until its remaining is covered. The uncovered
    rest is its **shortfall**.
C3. Offers repeat C1 and C2 on what is left after all contracts (worst case: every offer is won).
C4. Expected view: an offer's remaining and shortfall multiplied by its win probability (default setting
    `planning_default_win_probability`, as today). Contracts are not weighted.
C5. Contracts and offers **without a project** (recommended, Open decision D2) are covered by the team's
    **unassigned** hours with the same rules (a virtual scope "Unassigned").
C6. **Remedy, never automatic:** for every item with a shortfall the answer lists the people with unassigned hours
    inside the item's window (`suggestions: [{user_id, name, hours, from, to}]`); the UI offers "Assign" which opens
    the assignment dialog pre-filled.

### Money

M1. Planned cost of a subcontractor assignment = its hours (S1, over the whole period, booked or not) × `rate`, with
    `core/money.py` (exact decimals, half-up cents). A total assignment's planned net is `q2(total_hours × rate)`.
M2. **Revenue** (recommended, Open decision D1): no weekly revenue and no "assumed" revenue from capacity. Each
    contract shows `value = promised hours × rate`, `invoiced` (net of its project's invoices in its period, as the
    project report computes) and `open = value − invoiced`; each offer shows its net and the expected net (× win
    probability). Margin per project = contract value − planned subcontractor cost.

## Data model and storage

### Projects

* New column `kind` (`customer` | `internal`), NOT NULL. `customer`: needs a `customer_id`; may have offers,
  contracts, invoices. `internal`: no customer, no offers, contracts or invoices (the services refuse with 409:
  "Internal projects cannot have offers, contracts or invoices"); dates optional.
* The column `personal` is **removed** (recommended, Open decision D9). The response keeps `personal` (=
  `kind == "internal"`) for one release, marked deprecated in the OpenAPI description; `ProjectIn` accepts
  `personal` for one release and maps `true` → `internal`; giving both with a contradiction is a 422.
* A default internal project **"Administration"** with the fixed id `internal-administration` is inserted by the
  migration when no row with that id exists (`INSERT ... WHERE NOT EXISTS`, idempotent). It can be renamed and
  archived, not deleted (409).

### Allocations (assignments)

* Employee and subcontractor rows are edited through the same API. Exactly one of `user_id` / `supplier_id`, exactly one
  of `hours_per_week` / `total_hours`.
* Subcontractor rows created by accepting an incoming offer keep `incoming_offer_id` (read-only) and may be edited
  (period, hours, rate). An edit whose planned net differs from the offer's net gives the info
  `assignment.differs_from_offer` with both figures and the difference (E6). Cancel stays as it is.
* `customer_id` and `contract_id` are no longer written (derived from the project when needed); existing values stay.

### Contracts and offers

* `user_ids` stays in the table and in the lenient models (backwards compatibility, `extra="allow"` style), is no
  longer written by the web UI or by accepting an offer, and is **ignored** by every planner function. `ContractIn`
  and `PlanIn` still accept it (old clients) and drop it.
* Exactly one of `hours_per_week` / `total_hours` on a contract (today both are accepted and `hours_per_week` wins).
* New contracts need a project (recommended, Open decision D2): `ContractIn.project_id` required, 422 otherwise.
  Existing rows without a project stay valid (C5).

### Incoming offers

* Accepting a capacity offer creates a **total** assignment: `total_hours` = sum of the quantities of its `h` lines,
  period = `plan.start`..`plan.end`, `rate` = net of the hour lines / their hours (as today). `plan.hours_per_week` is
  accepted for old clients and ignored; `IncomingPlanIn` no longer requires one of the two hour fields.
* `incoming_offer.hours_mismatch` is removed (the two figures can no longer differ).
* Existing subcontractor assignments are **not** converted: they keep their `hours_per_week`; the owner edits them.

### Strict schema (work package 0 and the migration)

**Infrastructure (WP0).** `schema.py` gains a dialect-neutral `Check(name, expr)` on `Table`, with a small expression
vocabulary that `dialect.py` renders per dialect (no raw SQL strings in `schema.py`):
`not_null(col)`, `non_blank(col)`, `one_of(col, values)`, `exactly_one(col_a, col_b)`, `range(col, lo, hi,
lo_open=False)`, `iso_date(col)` (SQLite `date(col) IS col`; PostgreSQL/MariaDB a regex plus a cast check),
`not_before(col_a, col_b)` (`col_a >= col_b` when both are set), `max_days(col_a, col_b, n)`,
`implies(cond, cond)` and `null(col)`. The existing `allocations_one_owner` CHECK moves into `schema.py`. The
"migrated SQLite equals `schema.py`" test compares the CHECK constraints as well (parsed from `sqlite_master.sql`,
normalised), and a new test inserts one violating row per constraint and expects the database to refuse it, in
SQLite always and in PostgreSQL/MariaDB where the test containers run.

**Rows that violate a new constraint are never dropped silently** (recommended, Open decision D6). The migration first
copies them into a new table `migration_rejects` (`id`, `migration_id`, `table_name`, `row_id`, `reason`, `row` JSON,
`created`), then rebuilds the table without them. `permita38 migrate --status` prints the reject count per table, the
health endpoint reports it, and the boss sees a banner "N records could not be migrated, see Settings → Data". A
CLI command `permita38 rejects list|restore <id>` (restore validates the row against the current rules first)
belongs to the same work package.

**SQLite rebuild.** Inside the runner's transaction (no `BEGIN`/`COMMIT` in the file): create `<t>_new` with the
constraints, `INSERT INTO migration_rejects SELECT ... WHERE NOT (<checks>)`, apply the repairs below, `INSERT INTO
<t>_new SELECT ... WHERE <checks>`, `DROP TABLE <t>`, `ALTER TABLE <t>_new RENAME TO <t>`, recreate the indexes.
PostgreSQL/MariaDB: the same rejects and repairs, then `ALTER TABLE ... ADD CONSTRAINT`.

**Constraints and repairs of migration `0016-project-planning`** (one file per dialect, `requires` = current head):

| Table | Constraint | Existing rows that violate it |
|---|---|---|
| projects | `kind` NOT NULL, `one_of(kind, customer, internal)` | repaired: `customer` when `customer_id` is set, else `internal` (a `personal` project with a customer becomes `customer`) |
| projects | `non_blank(name)` | repaired to `(unnamed project)` |
| projects | `status` NOT NULL, `one_of(status, active, paused, done, archived)` | NULL → `active`; any other value → rejects |
| projects | `implies(kind = internal, customer_id IS NULL)`, `implies(kind = customer, customer_id IS NOT NULL)` | follows from the kind repair |
| projects | `iso_date(start)`, `iso_date(end)`, `not_before(end, start)` | rejects |
| projects | `range(budget_hours, 0, 100000, lo_open)`, `range(rate, 0, <v.Rate max>)` | rejects |
| allocations | `exactly_one(user_id, supplier_id)` (exists), `project_id` NOT NULL | rejects |
| allocations | `start`, `end` NOT NULL, `iso_date`, `not_before(end, start)`, `max_days(start, end, 1830)` | rejects |
| allocations | `exactly_one(hours_per_week, total_hours)` | both set → `total_hours` set NULL (today's engines use `hours_per_week` first, so no number changes); none → rejects |
| allocations | `range(hours_per_week, 0, 80, lo_open)`, `range(total_hours, 0, 100000, lo_open)` | rejects |
| allocations | `status` NOT NULL, `one_of(status, active, cancelled)` | NULL → `active`; other → rejects |
| allocations | `implies(status = cancelled, cancelled_from IS NOT NULL)`, `implies(status = active, cancelled_from IS NULL)`, `iso_date(cancelled_from)` | rejects |
| allocations | `implies(supplier_id IS NOT NULL, rate IS NOT NULL)`, `range(rate, 0, <v.Rate max>)`, `implies(user_id IS NOT NULL, incoming_offer_id IS NULL)` | rejects |
| contracts | `non_blank(title)`, `start`, `end` NOT NULL, `iso_date`, `not_before(end, start)` | rejects |
| contracts | `exactly_one(hours_per_week, total_hours)` | both → `total_hours` NULL (the engine used `hours_per_week`); none → rejects (the planner skipped them anyway) |
| contracts | ranges of `hours_per_week`, `total_hours`, `rate` as in `ContractIn` | rejects |
| contracts | `status` NOT NULL, `one_of(status, confirmed, done)` | NULL → `confirmed`; other → rejects |

Foreign keys are **not** part of this migration (Open decision D7). `transfer/upgrades.py` (data export/import of
older versions) and the importer must produce rows that pass these constraints (same repairs; a row that cannot be
repaired is reported in the import result, never written).

## API contract (binding; backend and web UI are built against it in parallel)

All hours are floats rounded to 2 decimals in responses; money as today (floats of exact cents). Every warning keeps
the shape `{code, severity, field, message, details, confirmable}` and **every message states its arithmetic**.

### `GET /api/plan` (replaces `GET /api/forecast` and `GET /api/plan/day-table`)

Query: `weeks` (1–52, default 12), `project_id` (a project id or `unassigned`; filters `projects`), `user_id`
(filters `weeks` and `people`). Area `planning`, read.

```json
{
  "today": "2026-11-08",
  "weeks": [
    {"week": "2026-11-09", "week_end": "2026-11-15",
     "capacity": 40.0, "holiday_hours": 0.0, "vacation_hours": 0.0, "absence_hours": 0.0, "busy_hours": 0.0,
     "assigned_customer": 4.0, "assigned_internal": 30.0, "unassigned": 6.0, "over_assigned": 0.0,
     "billable_share": 10.0, "subcontractor_hours": 6.0}
  ],
  "people": [
    {"user_id": "u1", "name": "Ada", "capacity": 240.0, "assigned": 204.0, "unassigned": 36.0,
     "assignments": [{"id": "a1", "project_id": "p-orchard", "hours": 24.0}]}
  ],
  "projects": [
    {"project_id": "p-orchard", "name": "Orchard", "kind": "customer",
     "supply": {"employees": 24.0, "subcontractors": 24.0, "total": 48.0},
     "remaining_contracts": 74.0, "shortfall_contracts": 26.0,
     "remaining_offers": 0.0, "shortfall_worst_case": 26.0, "shortfall_expected": 26.0,
     "status": "short", "planned_subcontractor_cost": 2160.0, "contract_value": 8880.0, "margin": 6720.0,
     "items": [ "...item verdicts, see below..." ]}
  ],
  "warnings": []
}
```

* Week row fields (labels in the UI, with the derivation as tooltip):
  `capacity` "Capacity" = workday hours − holidays − time off − busy; `assigned_customer` "Customer projects";
  `assigned_internal` "Internal"; `unassigned` "Unassigned" = capacity − both (≥ 0); `over_assigned` "Over-assigned" =
  hours scaled away by S3; `billable_share` "Billable share" = customer / capacity in percent (null when capacity is
  0); `subcontractor_hours` "Subcontractors" (not part of capacity).
* `status` of a project: `ok` (no shortfall), `at_risk` (only the worst case is short), `short` (a contract is
  short). Internal projects: `ok` or `over_budget`; their entry carries `assigned`, `booked`, `budget_hours`.
* The scope `unassigned` appears in `projects` only when an item without a project exists (C5).

**Item verdict** (contracts and offers):

```json
{"kind": "contract", "id": "k2", "title": "Data migration", "type": "total",
 "start": "2026-11-09", "end": "2026-12-04", "promised": 50.0, "booked": 0.0, "remaining": 50.0,
 "covered": 24.0, "shortfall": 26.0, "weekly_shortfalls": [], "rate": 120.0, "value": 6000.0,
 "invoiced": 0.0, "open": 6000.0, "win_probability": null,
 "explanation": "50 h promised − 0 h booked = 50 h remaining. Until 04.12.2026 Orchard has 24 h of assigned time left after its weekly contracts. Short 26 h.",
 "suggestions": [{"user_id": "u1", "name": "Ada", "hours": 24.0, "from": "2026-11-09", "to": "2026-12-04"}]}
```

`type: "weekly"` items carry `weekly_shortfalls: [{week, demand, supply, shortfall}]` (only weeks with a shortfall) and
`shortfall` = their sum; `promised` is the sum of D2 over the period.

### `POST /api/plan/simulate`

Body `{changes: {absences: [...], commitments: [...], assignments: [...]}, weeks, project_id, user_id}`;
`changes.allocations` is accepted as an alias for one release. Returns the `GET /api/plan` body plus `delta:
{projects: [{project_id, shortfall_before, shortfall_after, delta}], total}`. Writes nothing.

### `POST /api/plan/apply`, scenarios, suggestions

Unchanged paths. `apply` writes absences, commitment moves and assignments (confirm required, as today).
`suggest-vacation` ranks windows by the increase of the total contract shortfall, then by bridge days.
`suggest-start` takes `{hours, deadline, earliest, project_id}` (no `user_ids`) and returns the first start from
which an offer of `hours` total over `start..deadline` has no shortfall. `what-must-move` keeps its meaning (only
offers move) on the new verdicts. `POST /api/allocations/bootstrap` is **removed** (Open decision D8), the
suggestions (C6) replace it.

### `/api/allocations`

`AllocationIn`: `user_id` **or** `supplier_id`, `project_id`, `start`, `end`, `hours_per_week` **or** `total_hours`,
`rate` (required for a subcontractor, refused for an employee). `PUT` and `DELETE` work for subcontractor rows too
(right: `procurement` write for subcontractor rows, `planning` write for employee rows); a row with
`incoming_offer_id` cannot be deleted, only cancelled. Responses carry `planned_hours` (S1 over the whole period),
`planned_net` for subcontractors, and `planning_warnings` (`assignment.over_capacity`, `assignment.overlap`,
`assignment.differs_from_offer`).

### Warning codes (replace all planner codes)

| Code | Severity | When |
|---|---|---|
| `contract.shortfall` | warning | a contract's shortfall > 0 (forecast and contract save) |
| `offer.shortfall` | warning | an offer's worst-case shortfall > 0 (forecast, offer save and accept) |
| `assignment.over_capacity` | warning | S4 |
| `assignment.overlap` | info | two assignments of one owner to one project overlap |
| `assignment.no_demand` | info | a subcontractor assignment of a customer project whose items need nothing in its period (paid but unused) |
| `assignment.differs_from_offer` | info | E6 |
| `project.no_supply` | warning | a customer project with remaining demand and no assignment in the item's window |
| `internal.over_budget` | warning | S5 |
| `booked.unattributed` | info | D4 left-over booked hours |
| `contract.no_project` | info | C5 items |

Removed: `capacity.gap`, `capacity.over`, `capacity.offer_over`, `offer.project_gap`,
`allocation.employee_over_allocated`, `allocation.no_demand`, `allocation.overlap_same_project`,
`incoming_offer.hours_mismatch`. The document warnings (`offer.hours_mismatch`, rate warnings, invoice warnings,
`project.over_budget`) stay.

## Callers that change (complete list, checked on `origin/main` c63d776)

* `core/service/planning.py` (engine) and `plan_service.py`: rewritten as a package `core/service/planning/` with
  `spread.py` (S1), `supply.py`, `demand.py` (D1–D6), `cover.py` (C1–C6), `views.py` (the JSON of the API).
* `core/service/external_capacity.py`: its spreading goes into `spread.py`; `figures()` uses it.
* `core/service/allocations.py`: `_planned_hours` uses `spread.py`; bootstrap removed; external rows editable.
* `core/service/planning_warnings.py`: `week_over_allocation`, `over_allocation_on_save` use the supply of S1/S3.
* `core/service/procurement.py`: accept builds a total assignment from the lines; `_planned_hours` via `spread.py`.
* `core/service/sales.py`: accepting an offer no longer copies `plan.user_ids`; extends the project period as today.
* `core/service/records.py`: `_check_contract_users` removed; contract and project rules of this spec.
* `core/service/team_timeline.py`: shows a person's **assignments** instead of contracts by `user_ids`.
* `core/service/transfer/importer.py` (`contracts.user_ids` mapping stays for old files) and `transfer/upgrades.py`.
* `core/calc.py`: `forecast()` delegates to the new view; `Capacity` stays the holiday/absence source.
* `api/routers/planning.py` (`SuggestStartIn` without `user_ids`, new `/plan`), `api/routers/allocations.py`.
* `web/planner.js`, `web/allocations.js`, `web/procurement.js`, `web/timeline.js`, `web/app.js` (project form `kind`,
  forecast tab), `web/app2.js` (offer plan).
* The read-only MCP server (PR #73) reads `GET /api/plan` once it is merged.

## Supersedes

`docs/specs/planner-per-project.md`, `docs/contracts/planner-per-project.md`, the sections "Planner integration" and
the allocation figures of `docs/contracts/subcontractors.md`, and the planner parts of `docs/planning.md` (rewritten in
WP6 in plain language with E1 as the walkthrough). The older files get a header line "Superseded by
docs/specs/project-centric-planning.md" and are otherwise left as history.

## Hand-computed worked examples (invented data; these become literal test expectations)

Calendar: state BY. Between 09.11.2026 and 18.12.2026 there is no public holiday in BY. 25.12.2026 (Friday) is one,
24.12. is not. People work Monday to Friday unless stated. Nobody has calendar busy time.

| # | Setup | Computation | Expected |
|---|---|---|---|
| E1 | Today Sun 08.11.2026. Ada 40 h. Internal "Administration": Ada 30 h/week 09.11.–18.12. Customer project Orchard: Ada 4 h/week 09.11.–18.12.; subcontractor Sam total 24 h 09.11.–04.12., rate 90.00. Contracts on Orchard: K1 4 h/week 09.11.–18.12., rate 120.00; K2 total 50 h 09.11.–04.12., rate 120.00. Nothing booked | Supply Orchard: Ada 0.8/day; Sam 24 h / 20 days = 1.2/day until 04.12. Weeks 09.11.–04.12.: supply 4 + 6 = 10 per week, K1 takes 4, leftover 6 per week, 24 in total. Weeks 07.12. and 14.12.: supply 4 = K1 4, leftover 0. K2 takes 24 of 50 | K1 shortfall 0. K2 remaining 50, covered 24, shortfall 26, `contract.shortfall`. Orchard supply employees 24, subcontractors 24, total 48; remaining_contracts 74; shortfall_contracts 26; status `short`. Ada unassigned 6 h/week, suggestion for K2: Ada 24 h 09.11.–04.12. Week of 09.11.: capacity 40, customer 4, internal 30, unassigned 6, billable share 10.0, subcontractors 6. Planned subcontractor cost 2,160.00; contract value 24 × 120 + 50 × 120 = 8,880.00; margin 6,720.00 |
| E2 | E1, but today Sun 15.11.2026, and Ada booked 10 h on Orchard between 09.11. and 13.11. | Attribution week 09.11.: K1 takes 4, K2 gets 6, so K2 remaining 44. Sam's 24 h are still open (he booked nothing): 24 / 15 days (16.11.–04.12.) = 1.6/day. Weeks 16.11., 23.11., 30.11.: supply 4 + 8 = 12, K1 4, leftover 8, 24 in total | K2 remaining 44, covered 24, shortfall 20. Check: remaining demand K1 5 × 4 = 20 plus K2 44 = 64; supply Ada 20 + Sam 24 = 44; 64 − 44 = 20 |
| E3 | Today Sun 08.11.2026. Ben 40 h, assigned to Pine 10 h/week 09.11.–04.12. (2/day). Contracts T1 total 25 h 09.11.–27.11., T2 total 20 h 09.11.–04.12. | Earliest end first: T1 window has 15 days × 2 = 30 h, takes 25. Leftover 5 h until 27.11., plus 10 h in the week of 30.11. T2 takes 15 | T1 shortfall 0; T2 covered 15, shortfall 5. Check: 45 − 40 = 5 |
| E4 | Week of 09.11.2026. Cleo 20 h (4/day). Assignments: Administration 12 h/week, customer project Elm 10 h/week. Contract on Elm 10 h/week | Per day 2.4 + 2.0 = 4.4 > 4, scale 4 / 4.4 = 10/11: Administration 2.1818/day = 10.91 h, Elm 1.8182/day = 9.09 h | `assignment.over_capacity`: "Week of 09.11.2026: Cleo is assigned 22 h but has 20 h of capacity; her assignments of that week are scaled to 90.9 % (Administration 10.91 h, Elm 9.09 h)." Elm contract weekly shortfall 0.91 (10 − 9.09). Week row: capacity 20, internal 10.91, customer 9.09, unassigned 0, over-assigned 2 |
| E5 | Week of 21.12.2026. Dan 40 h, vacation Wed 23.12. Assigned to Ash 20 h/week. Contract on Ash 20 h/week | Available days Mon 21., Tue 22., Thu 24. (25.12. holiday): 20 / 3 = 6.67/day ≤ 8, no scaling, supply 20 | Ash weekly shortfall 0. Week row: capacity 24, holiday 8, vacation 8, customer 20, unassigned 4. **Variant** with vacation also on Tue 22.12.: 2 days, 10/day > 8, scaled to 8/day, supply 16, `assignment.over_capacity` with reason `time_off`, weekly shortfall 4 |
| E5b | Contract 10 h/week starting Wed 11.11.2026 | D2: 3 weekdays of 5 in the period | demand 6 h in the week of 09.11.2026 |
| E6 | Incoming offer from Sam: one line 24 h × 90.00 = 2,160.00 net; plan 09.11.–04.12. with `hours_per_week` 6 | Accept: total assignment 24 h, rate 90.00, `hours_per_week` ignored | Assignment total 24, planned net 2,160.00, no warning. Edit to 30 h: planned net 2,700.00, info `assignment.differs_from_offer`: "Planned 30 h / 2,700.00 € differ from the offer's 24 h / 2,160.00 € (6 h / 540.00 € more)." |
| E7 | E1 plus offer O1 on Orchard, total 10 h 16.11.–04.12., win probability 50 % | Contracts first: K2 already took every leftover hour until 04.12. O1 covered 0 | O1 shortfall 10, `offer.shortfall`; Orchard shortfall_contracts 26, shortfall_worst_case 36, shortfall_expected 26 + 5 = 31, status `short` |
| E8 | Migration: projects (a) `personal` 1, no customer; (b) `personal` 0, no customer; (c) `personal` 1 with customer; (d) status NULL; (e) end before start. Contracts (f) both hour fields; (g) none. Allocation (h) employee with `incoming_offer_id` | Repairs and rejects per the table | (a) internal, (b) internal, (c) customer, (d) `active`, (e) in `migration_rejects` and not in `projects`; (f) `total_hours` NULL, (g) rejected; (h) rejected. `migrate --status` reports 3 rejects. `internal-administration` exists once after two runs |

## Work packages (fresh Sonnet agents, one each; backend and web UI in parallel against the contract)

| WP | Content | Depends on |
|---|---|---|
| WP0 | `Check` in `schema.py`, rendering in `dialect.py` (3 dialects), CHECK comparison in the schema test, refusal tests, `migration_rejects` table, `migrate --status`/health/CLI `rejects` | – |
| WP1 | Migration `0016-project-planning` (3 dialects), `kind`, constraints and repairs, "Administration", models (`ProjectIn.kind`, `ContractIn`, `AllocationIn`, `IncomingPlanIn`), `transfer/upgrades.py`, importer; E8 | WP0 |
| WP2 | `planning/spread.py` and supply (S1–S5) with E4, E5, E5b; switch `allocations`, `procurement`, `planning_warnings`, `external_capacity` to it | – |
| WP3 | Demand, attribution, covering, verdicts (D1–D6, C1–C6, M1–M2) as pure functions; E1–E3, E7 | WP2 |
| WP4 | Service and API: `GET /api/plan`, simulate, apply, suggestions, warnings on save, allocations API, incoming accept (E6), team timeline, removals; `api/openapi.json`; randomized model updated | WP1, WP3 |
| WP5 | Web UI against the contract with E1/E7 fixtures: planner overview (week rows, project and item verdicts with explanation and "Assign"), assignment dialog for people and subcontractors, project form `kind`, offer plan without assignee, rejects banner | contract only; integration after WP4 |
| WP6 | `docs/planning.md` rewritten with E1 as the walkthrough; superseded headers | WP4 |

## Review tier

**Full** (capacity numbers, money of subcontractor cost and margin, a storage migration that repairs and rejects data):
code review, test review, and a logic review that recomputes E1–E8 by hand. The randomized suites run once before
the merge (records, data model and import are touched).

## Open decisions for the product owner (recommendation first)

| # | Question | Recommendation |
|---|---|---|
| D1 | Revenue: drop the weekly revenue and the "assumed" revenue (capacity × default rate); show per contract value, invoiced, open; per offer net and expected net | yes |
| D2 | Contracts and offers without a project: covered by the team's unassigned hours (C5); new contracts must have a project | yes |
| D3 | Partial weeks of weekly items pro-rated by Mon–Fri (company calendar), not by each person's workdays | yes |
| D4 | Over-assignment: scale a person's assignments pro rata on that day (today's rule) and warn | yes |
| D5 | Booked attribution (D4 rule): weekly items of the week first, then total items earliest end first | yes |
| D6 | Rows that violate a new constraint go to `migration_rejects` (kept, visible, restorable) instead of stopping the upgrade | yes |
| D7 | Foreign keys: a separate later work item. Cost: `PRAGMA foreign_keys=ON` on every SQLite connection (cannot be switched inside the migration transaction), dangling references already exist (the planner shows "(deleted project)"), every delete path must then refuse or cascade explicitly | later, own spec |
| D8 | Remove "Allocate my capacity" (bootstrap); the per-item suggestions replace it | yes |
| D9 | Remove the `personal` column; keep `personal` in responses and requests for one release | yes |
| D10 | Busy time from connected calendars keeps reducing capacity | yes |
