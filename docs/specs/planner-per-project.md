# Specification: the planner per project (ring-fenced capacity)

Status: **proposed**. Author: architect (Sonnet 5.5). Builds on `docs/planning.md` and on the ring-fence rules of
`docs/specs/subcontractors.md` (branch `docs/spec-subcontractors`, not merged when this was written; where this
document says "the subcontractor spec" it means that file, section "Product owner decisions" and "Planner
integration"). The implementer follows this document and lists every choice it had to make under "Open design
decisions" in its report. Money uses `core/money.py` only; every tax-related statement is marked `(verify)` (there
are none: this feature moves no money and touches no VAT).

## Goal

Today the planner computes one capacity pool for the whole team. The product owner decided on 2026-10-24 that
capacity is **ring-fenced to projects**, for employees and for subcontractors, because not every person can work on
every task. This spec turns the pool into capacity per project: the weekly rows, the day table, the what-if drafts and
the warnings are per project, and a project's shortfall is a **gap for that project** that no other project's
capacity ever covers.

## Product owner decisions (given, not open)

1. An allocation belongs to one project (employees and subcontractors alike). Only contracts (and open offers) of that
   same project draw on it.
2. Unused capacity of an allocation is not moved to another project.
3. A project's demand that its own allocations do not cover is a **gap of that project**, shown on that project's row
   and never netted against another project's surplus or against the team total.
4. The day table and the what-if drafts are per project.

## Current behaviour (verified in the code at `origin/main`, 21f0cd1)

* `permita38/core/service/planning.py`, `day_table()` (l. 269 ff.): capacity is added **per employee** to one
  `Day.cap` (`weekly_hours / len(workdays)`, minus busy time; zero on public holidays and absence days). There is no
  project dimension in `Day`, `DayTable` or `World`.
* `load()` (l. 134 ff.) builds `Item`s from contracts (not `done`) and from sent, unaccepted, unexpired offers.
  `Item` carries no `project_id`; the project is only used to find the rate (`_item_rate`). The assignees are
  `user_ids` of the plan/contract that exist and are active, else the boss (`make()`, `ids = ... or [boss]`).
* Demand: `hours_per_week` is spread as `hpw * share / n` over the assignee's workdays of each week, **also on
  holidays and absence days** (the promise is not reduced); `total_hours` is spread evenly over the available days
  (workdays that are neither holiday nor off), falling back to all workdays. Confirmed (contracts) and binding
  (offers) go into `Day.confirmed` / `Day.binding`; `Day.expected` is binding x win probability.
* `weeks()` (l. 360 ff.) sums the days into one row per week (capacity, confirmed, binding, free, utilisation, status
  through `status_for`, utilisation in percent with one decimal: `ok` <= 90, `tight` <= 100, `over` above or capacity 0 with
  work (utilisation then null; 0.0 when there is no work)). Revenue basis:
  contract, else offer-binding, else intended (capacity x default rate).
* `permita38/core/service/plan_service.py`: `forecast()`, `simulate()` (what-if: `ScenarioChanges` with
  `absences` and `commitments`), `feasibility()`/`_fit()` (an offer fits when no touched week has
  `free_worst_case < 0` in the **team** rows; the warning is `capacity.offer_over`), `_capacity_warnings()`
  (`capacity.over`, one per week whose status is `over`, no project),
  suggestions (`suggest_vacation`, `suggest_start`, `what_must_move`), `apply()` and scenarios.
* `permita38/api/routers/planning.py`: `GET /api/forecast`, `POST /api/plan/simulate` (`SimulateIn`: `changes`,
  `weeks`, `first`, `user_id`), `POST /api/plan/check-offer`, `/plan/apply`, scenarios. There is **no route for the
  day table**; the browser only gets weekly rows. Area `planning` is boss-only (`core/rights.py`: employee `(N, N)`).
* `web/planner.js` draws the timeline from `POST plan/simulate` (draft of `abs` and `com` changes, a `user` selector:
  me / whole team); `web/app.js` `PAGES.plan` renders the forecast tab from `GET forecast`.

Consequence: a surplus of one project hides a shortage of another, and a subcontractor allocation would (without the
ring fence) join the pool.

## Target behaviour

### Vocabulary

| Term | Meaning |
|---|---|
| scope | A project id, or `unassigned` (`project_id: null` in JSON). Every hour of capacity and of demand belongs to exactly one scope. |
| allocation | Hours per week of one resource (an employee, or a subcontractor through the subcontractor spec) given to one project for a period. |
| internal / external | Employee allocations / subcontractor allocations. They are separate pools **inside** a project (this is how the subcontractor spec computes `external_gap`; see W4 there). |
| gap | `max(0, assigned - capacity)` of one scope, one pool and one **day**; weekly and period gaps are sums of day gaps. |

### Rules

1. **Employee allocation.** Row of the `allocations` collection with `user_id` (instead of `supplier_id`; exactly one
   of the two is set), `project_id`, `start`, `end`, `hours_per_week`. Per day inside the period on the person's
   AVAILABLE days of that week (workdays that are neither a public holiday nor an absence day, like the demand of rule 5):
   `alloc_day = hours_per_week / available days of the week` (`len(workdays)` in a week without time off).
2. **Real day capacity of a person** is as today: `weekly_hours / len(workdays)`, 0 on a public holiday or absence day,
   minus busy time (never negative). An allocation never creates capacity; it only assigns existing capacity.
3. **Over-allocation on a day** (sum of the person's `alloc_day` > real day capacity): every allocation is scaled
   pro rata, `cap_i = real * alloc_i / sum(alloc)`. Hours are never invented and never given to a third project. On a
   holiday or absence day real capacity is 0, so every allocation of that person has 0 that day.
4. **Unassigned remainder.** `real - sum(alloc)` (if positive) is the person's capacity in scope `unassigned`. A person
   with no allocation at all is entirely `unassigned`. Contracts and offers **without a project** draw on
   `unassigned` only.
5. **Demand** of a contract/offer lands in the scope of its `project_id`, per day: `hours_per_week` is spread over the
   assignee's AVAILABLE workdays of each week (workdays that are neither a public holiday nor an absence day), like
   `total_hours` over its period; only when a week has no available day left is the nominal spread over all workdays
   kept, so the overload stays visible. A partial week gets its share of the full week. The weekly totals therefore
   equal the behaviour before this feature (a 32 h contract in a one-holiday week of a 40 h person is 8 h on four days
   and fits). Real overload is still a gap. The assignee list decides internal vs external.
   `Day.confirmed`, `Day.binding` and `Day.expected` therefore get a per-scope version.
6. **Gap per scope, day and pool**: `gap = max(0, assigned - cap)` with `assigned` = confirmed + binding (worst case)
   and `gap_confirmed` with confirmed only, `gap_expected` with confirmed + expected. Internal and external pools of one
   project are **not** netted (the subcontractor spec, W4). The project gap is `internal_gap + external_gap`.
7. **Status of a scope row**: `over` when `gap > 0` (even when utilisation by capacity alone would be `ok`), else
   `status_for(cap, demand)` as today. Utilisation of a scope is `assigned / cap` in **percent with one decimal**, like `status_for` today (0.0 when there is no work, null when cap 0 with work).
8. **Team row** (what the planner shows first, unchanged field names): `capacity` = sum of all scopes (including
   `unassigned` and the unused surplus of projects), `confirmed`, `binding_offers`, `utilisation` as today. New:
   `gap` = sum of all scope gaps, never reduced by any surplus. The team `status` is `over` when `gap > 0` or as
   today. The team total is therefore **no longer able to hide** a project gap (decision D3, decided 2026-10-25).
9. **Unused capacity** stays in its scope's row as `free_*`; it is shown (so the owner sees which allocation is
   idle) but never counted for another scope. Info warning `allocation.no_demand` (as in the subcontractor spec).
10. **Revenue and cost**: unchanged in meaning. Contracted/offer revenue is demand times rate and is independent of
    where capacity sits. "Intended" revenue per row = capacity x default rate; a scope row uses its own capacity, the
    team row the sum. Employee cost (`cost_rate x tracked hours`) is unchanged; subcontractor cost is as in the
    subcontractor spec and is booked to the allocation's project.
11. **Time entries** are not affected by allocations (no blocking when an employee tracks time on a project without an
    allocation; a soft info hint is optional, Later).

### Existing data, defaults and the upgrade consequence

No migration rewrites data. The rule 4 default makes the old world a special case: **every existing employee is
fully `unassigned`**, which is exactly "team capacity becomes an allocation to a default `unassigned` scope".

Consequence for existing users, stated plainly: after the upgrade **every project that has contracts or open offers
and no allocation shows a gap equal to its whole demand**, while the team total is unchanged. Contracts and offers
without a project behave as before. Nothing is lost or blocked; the planner only reports honestly that no capacity
has been assigned to those projects yet.

* **Decided (D1, 2026-10-25):** keep rule 4 as written and give the owner a one-click
  bootstrap, `POST /api/allocations/bootstrap` (preview, then `confirm: true`), that creates, for every active
  contract with a project and every assignee, one allocation with the contract's period and
  `hours_per_week = the assignee's peak weekly demand of that contract` (rounded up to 0.25 h). Open offers are not
  bootstrapped (they stay a visible gap until accepted). A banner on the planner offers the bootstrap while any
  project has a gap and the person has no allocation rows.
* Rejected: a legacy "pool" switch (it would keep two code paths alive and contradict the decision).

### Over-allocation across projects (decision D2, decided 2026-10-25)

Allocations are **validated softly** and **computed safely**. On saving an allocation the service checks the
person's overlapping allocations per week: if `sum(hours_per_week)` > `weekly_hours` it returns the warning
`allocation.employee_over_allocated` (severity `warning`, `confirmable: true`, never blocks) with `details`
`{user_id, week, allocated, weekly_hours, over_by}`. The planner computes with rule 3 (pro rata scale-down) so the
numbers are always feasible; the shortfall shows up as gaps of the projects involved. A per-day check also runs in the
planner and raises the same code once per week on the forecast (`warnings[]`), so holiday/vacation weeks that make an
otherwise fine allocation over-allocated are visible too (`details.reason: "time_off"`). The check is made per day: it warns only when on some day the person's allocations (`hours_per_week / workdays` each) add up to more than the real capacity of that day, so P Monday-Tuesday 40 h/week and Q Wednesday-Friday 40 h/week give no warning.

### Warnings

| Code | Severity | When |
|---|---|---|
| `capacity.gap` | warning | One per project and week with `gap > 0`. Replaces the project-less `capacity.over` for that case. `details`: `{project_id, project_name, week, pool, assigned, capacity, gap, contributions[]}`. Message names the project and says the hours are not covered by other projects. |
| `capacity.over` | warning | Kept for the `unassigned` scope and for weeks without a project dimension (unchanged text). |
| `allocation.employee_over_allocated` | warning | see D2 |
| `allocation.no_demand` | info | An allocation week with capacity and no demand in its project (idle, cost still runs for subcontractors). |
| `allocation.overlap_same_project` | info | The same person has two allocations to the same project in the same week (they add up; usually a mistake). |
| `offer.project_gap` | warning | Replaces `capacity.offer_over` (today's code) for an offer with a project. `feasibility()` for an offer of project P: the offer does not fit when **P's** weeks have `gap` (not when the team has free hours). Options listed: shorten, start later, allocate more hours to P, reduce scope. The old team-wide check only decides offers without a project. |

All keep the shape `{code, severity, field, message, details, confirmable}` and never block saving.

### Day table per project

The day table (`planning.day_table`) returns, per day, a dict `scopes[scope]` with `internal` and `external` blocks,
each `{cap, confirmed, binding, expected, gap, gap_confirmed}`, plus the unchanged person-level fields (`busy`,
`vacation`, `holiday`, `absence_other`, `names`) which are properties of people, not of scopes. `Day.cap` stays the sum
of all scopes (team total). `item_hours` stays per item; each `Item` gains `project_id` (None = unassigned).
`user_id` filtering keeps working: with a `user_id` only that person's allocations and demand are counted.

### What-if drafts per project

* The draft (`ScenarioChanges`) keeps `absences` and `commitments` and gains **`allocations`**: a list of changes
  `{id | null, user_id, project_id, start, end, hours_per_week, delete: bool}` so the owner can try "move 10 h/week of
  Erika from P to Q" before applying. Strict `*In` model, hard bounds like the other change types
  (`max_length=500`, `hours_per_week` bounded by `v.TotalHours`-style primitive, dates ISO).
* A moved or resized contract changes **only the rows of its own project** (and of the scopes of its new project if
  `project_id` changes in the draft). Other projects' rows and gaps are bit-identical to the base (a test).
* `simulate` returns the same `projects[]` structure as `forecast`, plus `delta` per project (change of gap in hours
  against the base), so the timeline can colour what the draft improved or worsened.
* Scenarios store the new `allocations` changes; `apply` writes allocation rows only with `confirm: true`; applying an
  allocation change returns the D2 warnings.
* Suggestions (`suggest_vacation`, `suggest_start`, `what_must_move`): they now run per project of the item they
  concern. `suggest_start` for an offer of project P looks at P's free hours (capacity of P minus P's demand),
  `what_must_move` moves only offers **of the same project**; vacation suggestions still evaluate the person but count
  the resulting gap per project. Contracts are never moved (unchanged).

## API contract (sketch; the binding version is `docs/contracts/planner-per-project.md`, written first, see work packages)

Routes stay under the existing prefix. New query parameters have defaults that keep old clients working.

### Changed

`GET /api/forecast?weeks=26&user_id=&offers=1&project_id=&by_project=1` (`by_project` default `1`):

```json
{
  "weeks": [
    {
      "week": "2026-03-02",
      "capacity": 40.0, "confirmed": 40.0, "binding_offers": 0.0,
      "free_after_confirmed": 0.0, "free_worst_case": 0.0,
      "utilisation": 100.0, "status": "over", "gap": 5.0,
      "projects": [
        {"project_id": "a1b2c3d4", "name": "Demo project P", "capacity": 25.0, "confirmed": 30.0,
         "binding_offers": 0.0, "free_worst_case": -5.0, "utilisation": 120.0, "status": "over", "gap": 5.0,
         "gap_confirmed": 5.0, "gap_expected": 5.0,
         "internal": {"capacity": 25.0, "assigned": 30.0, "gap": 5.0},
         "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}},
        {"project_id": "e5f6a7b8", "name": "Demo project Q", "capacity": 15.0, "confirmed": 10.0,
         "binding_offers": 0.0, "free_worst_case": 5.0, "utilisation": 66.7, "status": "ok", "gap": 0.0,
         "gap_confirmed": 0.0, "gap_expected": 0.0,
         "internal": {"capacity": 15.0, "assigned": 10.0, "gap": 0.0},
         "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}},
        {"project_id": null, "name": "Unassigned", "capacity": 0.0, "confirmed": 0.0, "binding_offers": 0.0,
         "free_worst_case": 0.0, "utilisation": 0.0, "status": "ok", "gap": 0.0, "gap_confirmed": 0.0,
         "gap_expected": 0.0,
         "internal": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0},
         "external": {"capacity": 0.0, "assigned": 0.0, "gap": 0.0}}
      ]
    }
  ],
  "totals": {"gap": 20.0, "gap_by_project": [{"project_id": "a1b2c3d4", "gap": 20.0}]},
  "warnings": [{"code": "capacity.gap", "severity": "warning", "field": "weeks", "confirmable": false,
                "message": "Week of 2026-03-02, project Demo project P: 30 h are planned but only 25 h are allocated to it (gap 5 h). Other projects' free hours do not cover it.",
                "details": {"project_id": "a1b2c3d4", "week": "2026-03-02", "pool": "internal", "assigned": 30.0, "capacity": 25.0, "gap": 5.0}}]
}
```

`project_id=<id>` or `project_id=unassigned` returns only that scope (the team row fields are then that scope's).
`by_project=0` omits `projects[]` and per-project warnings (smaller answer; `gap` stays). The subcontractor spec's
`external_*` fields and `include_external` stay as written there; `external_by_project` is the same data as
`projects[].external` and the implementer builds one source for both. Field mapping to the subcontractor spec:
`external.capacity` = `external_capacity`, `external.assigned` = `external_assigned`, `external.gap` = `external_gap`;
each `projects[]` entry also carries `external_cost` (E5), and the week row keeps the flat `external_*` fields
exactly as written there (team sums).

`POST /api/plan/simulate` body gains `project_id` (optional, same meaning) next to `user_id`; `changes` gains
`allocations`; the answer has the structure above plus `delta`.

`GET /api/plan/day-table?from=2026-03-02&to=2026-03-08&user_id=&project_id=` (new, `planning` read; the route the
subcontractor spec calls "`/api/planning/day-table` (or whichever route exposes it)"; the real prefix is `/api/plan/`):
`{days: [{date, cap, busy, vacation, holiday, absence_other, names, scopes: {"a1b2c3d4": {internal:{...},
external:{...}}}}]}`, the `external` block's `cap`, `assigned`, `gap` being the subcontractor spec's `external_cap`,
`external_assigned`, `external_gap`;
range at most 190 days (400 beyond), the same bound style as other range routes.

### New: employee allocations

| Route | Meaning |
|---|---|
| `GET /api/allocations?project_id=&user_id=&supplier_id=&status=` | list (employee and subcontractor rows; `kind: "employee" \| "external"`; same filters as the subcontractor spec). External rows need `procurement` read, employee rows `planning` read; a caller with only one right sees only those rows |
| `POST /api/allocations` | create an employee allocation |
| `PUT /api/allocations/{id}` | change it |
| `DELETE /api/allocations/{id}` | remove it (204) |
| `POST /api/allocations/bootstrap` | `{confirm: false}` previews, `{confirm: true}` creates (see above) |

```json
{"user_id": "u1", "project_id": "a1b2c3d4", "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 25.0}
```

Response adds `id`, `kind: "employee"`, `planned_hours` (hours of the period on the person's workdays, holidays and
absences taken out, rule 2) and `planning_warnings[]`. Subcontractor allocation rows are read-only here (they are made
by accepting an incoming offer) and answer 409 on `PUT`/`DELETE` through these routes.

Errors: 400 `confirm` missing on bootstrap/apply; 403 role lacks `planning` write; 404 unknown user, project or
allocation; 409 for an external row, a project that is `archived`/done (`allocations` cannot be added to a closed
project), or `end < start`; 422 body validation (`hours_per_week` outside 0.25..80 h, dates, ids), unknown fields
rejected by the strict model. Over-allocation is a warning, not an error (D2). Models: `AllocationIn` (strict) and
`Allocation` (response, lenient, `extra="allow"`), every new field with a hard bound (AGENTS.md); `api/openapi.json`
regenerated.

### Storage

There is **one** `allocations` table, shared with the subcontractor spec (its column list: `id supplier_id
incoming_offer_id project_id contract_id customer_id start end hours_per_week total_hours rate status cancelled_from
cancel_reason` + base + extra). This spec adds the nullable column `user_id` (employee rows); `supplier_id` is nullable
too, and a check requires exactly one of the two. Employee rows use `status: "active"` and `hours_per_week` only
(`total_hours`, `rate`, `incoming_offer_id` and `cancelled_*` stay null). Index `allocations(user_id)`.
Migrations use the id/requires chain of `docs/specs/migration-ids.md` (there is no `CURRENT_REVISION` to bump).
**Order (decided by the architect, 2026-10-25): this spec lands first.** Its migration `NNNN-allocations` (head + 1 at
merge time, identical in `sqlite/`, `postgresql/` and `mariadb/`, no `BEGIN`/`COMMIT`) creates the whole table with
**both** nullable columns and every column listed above, so the subcontractor spec adds nothing to it; that spec's
`NNNN-suppliers` migration then `requires` this one and only creates `suppliers`, `incoming_offers` and the expense
columns. Only if the subcontractor spec must land first does the table move into its migration unchanged and this spec
adds none. Neither migration rewrites a row. Unknown fields round-trip in `extra`. No data migration is needed for the
defaults (rule 4).

## UI sketch

* **Forecast tab** (`PAGES.plan`): the existing week table keeps its columns for the team and gains a column "Gap"
  and an expander per week. The expanded week lists the projects as rows (name, allocated, planned, free or gap,
  status pill). A gap is red text "5 h not covered" on that project's row; the free hours of other projects are
  printed grey and carry the title "not available to project P". A filter "Project: all / P / Q / Unassigned".
* **Plan & vacation** (`web/planner.js`): a project selector next to the person selector (`P.project`, remembered in
  `localStorage` like `plnUser`). The per-project stacked bar row was removed from the timeline (unreadable with more than three projects); per-project figures stay in the week table and day detail. The
  contract/offer bars are grouped by project; bars of other projects are dimmed when a project is selected. An
  "Allocations" row (draggable like time off: move, resize, hours field) lets the owner draft allocation changes.
  The draft bar lists allocation changes with Discard like the others. Keyboard and touch work as for the other bars.
* **Allocations dialog** (project page and person page): table of allocations with add/edit/delete, the
  over-allocation warning inline, and the "Allocate my capacity" bootstrap button with its preview table.
* **Day detail** (hover/focus on a week): per project cap / planned / gap for each day from the day-table route.
* Empty states: a person with no allocation shows "All of your capacity is unassigned. Give hours to a project in
  Allocations."; the upgrade banner (see above).
* All numbers come from the server (existing principle: the page only draws).

## Rights

Area `planning` only (no new area). Boss: read `all`, write `all`. Employee: `(N, N)` as today, so the planner and
the allocation routes are boss-only; employees do not see allocations. (If the team-timeline spec later gives
employees a read view, it shows whole projects' presence, not gaps or allocations.) API tokens: routes follow the
existing `Needs("planning")`; the token scope list in `docs/specs/api-tokens.md` gains no new entry. Subcontractor
allocations keep the `procurement` rule of the subcontractor spec. Rights tests: employee token/user gets 403 on all
new routes; the boss with a read-only token can GET but not POST/PUT/DELETE.

## Hand-computed worked examples (these become tests; do not recompute them with the code under test)

Common setup (same calendar as the subcontractor spec): today = 2026-03-02 (Monday); no public holiday between
2026-03-02 and 2026-03-27; Good Friday 2026-04-03 is a national holiday. Boss "Demo Boss": 40 h/week, Mon-Fri,
8 h/day. Employee "Erika Beispiel": 32 h/week, Mon-Thu, 8 h/day. Projects P and Q (fake). Period 2026-03-02 to
2026-03-27 (4 weeks) unless stated. Weeks start on Monday; `h` = hours.

| # | Case | Hand computation | Expected |
|---|---|---|---|
| E1 | Two projects, one person. Boss allocations: P 25 h/week, Q 15 h/week. Contract C_P 30 h/week (boss), C_Q 10 h/week (boss) | Day: P 25/5 = 5, Q 15/5 = 3, real 8, sum 8 so no scaling. Demand per day: C_P 30/5 = 6, C_Q 10/5 = 2. P: 6 - 5 = 1 gap/day x 5 = 5 per week. Q: 2 < 3, gap 0, 5 h idle. | Week 1 (same each week): P capacity 25, confirmed 30, gap 5, utilisation 120.0, `over`. Q capacity 15, confirmed 10, gap 0, utilisation 66.7, `ok`, free 5. Team: capacity 40, confirmed 40, utilisation 100.0 (the pool alone would say `tight`), team `gap` 5, team status `over`. Four weeks: P gap 20. Q's 5 idle h/week are **not** given to P. |
| E2 | Existing data, no allocation row. Boss, contract C_P 30 h/week (project P), contract N 8 h/week (no project). Then the bootstrap | Before the upgrade: capacity 40, confirmed 38, utilisation 95.0 `tight`. After the upgrade: boss entirely `unassigned`: cap 8/day, 40/week. N (no project) draws on `unassigned`: 8 h. C_P (project P): P capacity 0, assigned 30, gap 30. Bootstrap creates P allocation = peak weekly demand 30 h (6/day). Then P cap 30, gap 0, utilisation 100.0 `tight`; `unassigned` cap 40 - 30 = 10, N 8, free 2. | Without allocation: team capacity 40, confirmed 38 and utilisation 95.0 (same as before), team gap 30 and team status `over` (was `tight`: the only team field that changes, by D3), P gap 30 (`over`, capacity 0 with work, utilisation null), unassigned capacity 40 / assigned 8 / free 32. After bootstrap: P gap 0; unassigned capacity 10, assigned 8, free 2, utilisation 80.0 `ok`; team utilisation 95.0 `tight`, team gap 0. |
| E3 | Over-allocation. Boss allocations P 30 h/week and Q 20 h/week (50 > 40). Contract C_P 30 h/week, C_Q 20 h/week (boss) | Warning `allocation.employee_over_allocated`, over_by 10. Day: alloc P 6, Q 4, sum 10 > real 8: scale 0.8, so cap_P = 4.8, cap_Q = 3.2. Demand per day: P 6, Q 4. Gap/day P 1.2, Q 0.8. Week: P 6.0, Q 4.0. | Week 1: P capacity 24, gap 6; Q capacity 16, gap 4; team capacity 40, confirmed 50, team gap 10 (= the over-allocation). No capacity appears from nowhere: 24 + 16 = 40. |
| E4 | Vacation inside allocations. Boss allocations P 20 h/week, Q 20 h/week. Vacation Wednesday 2026-03-04. Contract C_P 20 h/week (boss); no contract in Q | Real capacity Wed = 0, so cap_P and cap_Q are 0 that day. P: 4 days x 4 = 16. Q: 16. The 20 h of the hpw contract are spread over the 4 available days: 5 per day, cap 4 per day, gap 1 x 4 = 4 (the promise is not reduced, it moves to the days that can work). | Week of 2026-03-02: P capacity 16, confirmed 20, gap 4, `over`. Q capacity 16, confirmed 0, free 16, info `allocation.no_demand`. Other weeks: P 20 / 20, gap 0, utilisation 100.0 `tight`. The per-day check also raises `allocation.employee_over_allocated` for the week (`reason: "time_off"`: 40 h allocated, 32 h real). |
| E5 | Employee and subcontractor in one project (consistent with W4 of the subcontractor spec). Boss allocation P 40 h/week (8/day); S allocation P 20 h/week (Mon-Thu, 5/day, rate 80.00). Contract C_P 50 h/week, `user_ids` [boss, S] | Share 1/2: boss 25/5 = 5 per day (Mon-Fri), S 25/4 = 6.25 per day (Mon-Thu). Internal: cap 8, assigned 5, gap 0 (3 idle h/day). External: cap 5, assigned 6.25, gap 1.25 x 4 days = 5. Pools are not netted. Variant C_P with `user_ids` [boss] only: assigned 10/day vs cap 8, internal gap 2 x 5 = 10. | Week 1: P capacity 60, confirmed 50, project utilisation 83.3 but status `over` (gap > 0); internal gap 0, external gap 5, project gap 5, `external_cost` 1.600,00 (20 h x 80.00). Variant: P capacity 60 (internal 40 + S's 20 h, which stay in P's external pool, idle but still paid: `external_cost` 1.600,00, as W14 of the subcontractor spec), confirmed 50, internal gap 10, external gap 0, project gap 10. Project Q rows unchanged. |
| E6 | What-if per project. Base: E1. Draft: move C_P one week later (2026-03-09 to 2026-04-03) | Base P gap: 4 x 5 = 20. Draft: week 2026-03-02 demand 0 (gap 0); weeks 03-09, 03-16, 03-23: 6/day vs 5, gap 5 each = 15; week 2026-03-30 to 04-03: allocation ended 03-27 so capacity 0, demand 30 (booked on the four available days, Good Friday is a holiday: 7.5 per day) vs capacity 0 = gap 30. Total 45. Q untouched. | `simulate` P gap 45 (delta +25); Q rows identical to the base (capacity 15, confirmed 10, gap 0); team capacity of the first four weeks unchanged at 40. Nothing written to the database. |
| E7 | Open offer in the same project. Base E1 plus a sent, unexpired offer O in project P, 10 h/week, same period, win probability 50 % | Per day: confirmed 6, binding 10/5 = 2, cap 5. Worst case: 8 - 5 = 3 per day x 5 = 15. Confirmed only: 6 - 5 = 1 x 5 = 5. Expected: 6 + 1 = 7 - 5 = 2 x 5 = 10. Q's 5 idle h are not offered. | P: `gap` 15, `gap_confirmed` 5, `gap_expected` 10, status `over`. `check-offer` for O answers `offer.project_gap` for each of the four weeks (gap 15 in each; options listed). Q: free 5, unchanged. An offer in Q of 5 h/week: Q binding 1/day vs cap 3 - 2 = 1 free, fits, no warning. |
| E8 | Part-time employee, total-hours contract, time off. Erika (32 h, Mon-Thu), allocation P 16 h/week (4/day). Contract T of project P: 80 h total, period 03-02 to 03-27, assignee Erika. Variant: vacation Tuesday 2026-03-10 | Available days 4 weeks x 4 = 16; 80/16 = 5 per day; cap 4; gap 1 x 16 = 16 (4 per week). Variant: available days 15, 80/15 = 5.3333 per day; cap 0 on 03-10. Week 2 (03-09, 11, 12): the 16 h allocation is spread over its 3 available days (5.3333 per day, real capacity 8), so cap 3 x 5.3333 = 16; assigned 3 x 5.3333 = 16, gap 0. Other weeks: assigned 4 x 5.3333 = 21.3333, cap 16, gap 5.3333. Total gap 3 x 5.3333 = 16; assigned total 3 x 21.3333 + 16 = 80. | Without vacation: each week P capacity 16, confirmed 20, gap 4 (utilisation 125.0, `over`); period gap 16. Variant: weekly gaps 5.3333 / 0.0 / 5.3333 / 5.3333, period gap 16.0; the total hours (80) are fully booked in the available days and nothing is booked on 03-10. |

Further tests:

* Equivalence (regression of behaviour that must not change): a database with no allocations and only project-less
  contracts gives the same team rows as before the change (field-by-field, existing planner tests stay green). The same
  data **with** project contracts and still no allocation gives the same team `capacity`, `confirmed`, `binding_offers`,
  `free_*`, `utilisation`, revenue fields and day-table `cap`/`confirmed`/`binding`; only `status` (D3) and the new `gap`
  fields may differ (E2). A database with allocations but `by_project=0` returns the
  same team rows as `by_project=1`.
* Ring fence property (hypothesis, in `tests/backend/randomized/`): for random allocations, contracts and offers,
  (a) the sum of scope capacities equals the sum of the persons' real capacities (nothing invented, nothing lost);
  (b) the team `gap` equals the sum of the scope gaps; (c) removing every contract of project Q leaves P's rows
  identical; (d) adding capacity to Q never changes P's gap.
* Oracle in `tests/backend/test_planner_per_project.py` uses the table above with literal numbers; the repository
  contract test covers `allocations` with `user_id`; API tests: validation bounds, 403 for the employee, 404/409/422
  cases, bootstrap preview equals the later write, bootstrap twice creates nothing the second time.
* Migration tests: the shared-collection migration (both nullable columns, exactly-one check), dialect folders in sync,
  one head in the `requires` chain, migrated SQLite equals `schema.py`.
* Browser tests (`tests/web/`): create two projects and two allocations, see the gap on one project while the other
  shows free hours; draft an allocation move, see the delta, discard; bootstrap banner from an old-style data set.
  One seeded random-journey step is added to `tests/web/e2e_random/`. Demo data uses only fake names and
  `example.invalid` addresses.

## Review tier

**Full** (AGENTS.md): capacity maths, money-adjacent figures (revenue basis, subcontractor cost per project) and
rights. Three Sonnet 5.5 passes: code, tests, logic. The logic pass recomputes E1 to E8 independently, never with the
code under test.

## Work packages (backend and web UI in parallel)

0. **Contract first (architect, small).** `docs/contracts/planner-per-project.md`: the JSON of `forecast`, `simulate`,
   `day-table`, `allocations`, `bootstrap`, error codes, and fixtures of E1, E2, E5 as example responses. The web UI
   starts against these fixtures.
1. **Backend A, engine (`core/service/planning.py`)**: `Item.project_id`, scoped `Day`, `day_table` per scope and
   pool, rules 1 to 9, `weeks()` per scope and team `gap`, `include_external` consistent with the subcontractor spec.
   Oracle tests E1 to E8 first.
2. **Backend B, service and warnings (`plan_service.py`, `planning_warnings.py`)**: `capacity.gap`,
   over-allocation, `offer.project_gap` in `feasibility`, per-project suggestions, `simulate` with `allocations`
   changes and `delta`, scenarios.
3. **Backend C, storage and API**: `allocations` collection (shared migration; nullable `user_id` and `supplier_id`), `AllocationIn`/`Allocation`,
   `routers/allocations.py` (thin), bootstrap service, `api/openapi.json`, rights, repository contract test.
4. **Web A, forecast tab and filters** (`web/app.js` `PAGES.plan`): gap column, project expander, project filter,
   upgrade banner. Works against the fixtures from package 0.
5. **Web B, Plan & vacation** (`web/planner.js`): project selector, per-project header bars, allocations row with
   drafting, delta colouring, dimming.
6. **Web C, allocations dialog and bootstrap preview.**
Packages 1 to 3 and 4 to 6 run in parallel; 2 depends on 1; 3 is independent except for the shared migration with the
subcontractor branch (see Storage). Merge order: this spec's engine and employee allocations can land **before** the
subcontractor feature (external pool is simply empty); the subcontractor branch then plugs into the same scopes.
`docs/planning.md` is updated in the same branch (capacity per project, gaps, allocations, bootstrap).

## Later (not in this version)

* Employees see their own allocations (read-only) in the team timeline.
* Time entries on a project without allocation: soft hint.
* Percent-of-time allocations instead of hours per week; skills and roles per allocation.

## Decisions (2026-10-25, product owner)

1. **First run (D1).** A one-click "Allocate my capacity" bootstrap behind a banner. Nothing runs automatically; the
   automatic first-start variant and the setting `planning_ring_fence_seeded` are dropped.
2. **Over-allocation (D2).** A warning (`allocation.employee_over_allocated`) that never blocks the save; the planner
   scales the allocations down pro rata (rule 3), as written above.
3. **Team status (D3).** The team row says `over` whenever any project has a gap, even if the pool has free hours
   (rule 8, example E1).

## Open questions for the product owner

None remain. Questions that come up in implementation go under "Open design decisions" in the implementer's report.

## Open design decisions (for the implementer's report)

The implementer lists here any choice not covered above. Pre-decided by the architect, to be confirmed in review:
internal and external pools of one project are not netted (follows the subcontractor spec); gaps are computed per
day and summed; the bootstrap rounds the peak weekly demand up to 0.25 h; projects/`unassigned` keys in URLs use the
literal `unassigned`.
