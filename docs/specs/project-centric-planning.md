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
| **capacity** | a person's real hours on a day: `weekly_hours / workdays` on a workday that is not a public holiday, not an absence day, minus calendar out-of-office and all-day blocks (D10; ordinary busy events do not count); never below 0 |
| **supply** of a project | the hours its assignments give it (rule S1; never scaled, see S3) |
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
      The divisor is the available days of the **whole** ISO week; only days inside the assignment period get hours.
      So a week the assignment only partly covers gets its share (E5c), and a holiday still moves hours inside the
      week. This also applies to subcontractors (decided, D14).
    * `total_hours`: the hours **still open** (`total_hours − hours the owner booked on the project inside the period`,
      at least 0; subcontractors normally book nothing) spread evenly over the owner's available days from the day
      after `today` to the assignment's end. A total assignment is a budget that does not expire because a week
      passed; whatever is still open at the **end of the assignment** (the end of the agreement) expires (decided, D12).
    * Subcontractors: public holidays and their own unavailable windows (vacation mid-project, the supplier form's
      "Not available" rows) are their days off, exactly like an employee's holidays and absences (decided, D14).
S2. Past days give no supply. Days before the assignment start or after its end give none.
S3. **Over-assignment is only warned, never scaled** (decided, D4). When a person's assignments of one day add up to
    more than that day's capacity, every project still counts its full assigned hours; the person's week row shows the
    excess as `over_assigned` (sum over the days of `max(0, assigned − capacity)`).
S4. Warning `assignment.over_capacity` per person and week with `over_assigned > 0`, with the arithmetic and the
    projects involved (example E4). Reason `time_off` when the week has holidays or absences on the days that are
    over, else `weekly_hours`. Subcontractors are never checked (their other customers are unknown, decided D13).
S5. Internal projects: their assignments **are** the plan. They consume capacity and never have demand, shortfall or
    "no demand" warnings. Optional `budget_hours`: warning `internal.over_budget` when booked + assigned future hours
    exceed it.

S6. **Working-time law (ArbZG) for employees, a friendly info for the boss.** The law applies to users whose role is
    not `boss`. The boss is not an employee and has **own limits, set in "My settings"** (a new `work_limits` object on
    the boss's user row, JSON column added by this work's migration; strict model `WorkLimitsIn`, every value 1..24
    hours a day or 1..168 a week, `info_day ≤ warn_day ≤ max_day`, 422 otherwise). Defaults:
    `info_day` 10, `warn_day` 12, `max_day` 16, `info_week` 60, `info_average` 48. Without law wording: a planned
    day above `info_day` is an **info** (`assignment.long_day`), above `warn_day` a **warning**
    (`assignment.very_long_day`), above `max_day` a warning "more than your maximum" (`assignment.impossible_day`).
    A week above `info_week` and a 24-week average above `info_average` are infos. **Nothing is ever refused for the
    boss**, neither a plan nor a time booking. Subcontractors are external freelancers and are never checked.
    Automatic breaks for **employees** on booking are a business setting (`auto_break_employees`, default on; roadmap
    item), never applied to the boss. The planner needs no break rule because `weekly_hours` is net working time.
    Planned hours are the person's assigned hours per day after S1, summed over all projects (customer and internal):
    * a day with more than 10 planned hours, or a week with more than 60 (§3: 6 working days × 10 h): the
      `assignment.over_capacity` warning (S4) gets the reason `working_time_law`, with the limit and the paragraph in
      its message;
    * new warning `assignment.working_time`: the planned hours averaged over the rolling 24 weeks that end with a
      planned week are more than 48 h/week (§3: 8 h × 6 working days). It is judged on the planned weeks only (booked
      weeks are checked by time tracking, see the roadmap item "ArbZG averages for booked time");
    * saving an employee with `weekly_hours` above 48 gives the warning `user.weekly_hours_above_law`.
    Breaks need no rule: `weekly_hours` is net working time, so breaks are already outside capacity. All of these are
    plausibility warnings, not legal advice (`docs/compliance.md`), and never block saving.

### Demand

D1. **Contracts** of a project with status `confirmed`; **offers** with status `sent`, a plan, not converted to a
    contract and `valid_until` not passed (as today). Offers are always planned **after** all contracts ("must"
    before "could").
D2. A weekly item (`hours_per_week`) demands, per ISO week, `hours_per_week × weekdays (Mon–Fri) of the week inside
    its period / 5`. Holidays and vacation do **not** reduce the promise (they reduce the supply).
D3. A total item demands `total_hours` over its period.
D4. **Booked attribution** (decided, D5). Time entries (billable or not) on the project, dated
    inside an item's period and up to `today`, are attributed day by day, **all contracts before any offer** (changed
    2026-11-06, so adding an offer never changes a contract's verdict): first that week's weekly contracts (up to their
    weekly demand), then the total contracts whose period contains the entry date (earliest end first, up to their
    `total_hours`), then the weekly offers, then the total offers in the same way. Hours left over are reported as
    `booked_unattributed` (info).
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
C5. Contracts and offers **without a project** (decided, D2) are covered by the team's
    **unassigned** hours with the same rules (a virtual scope "Unassigned").
C6. **Remedy, never automatic:** for every item with a shortfall the answer lists the people with unassigned hours
    inside the item's window (`suggestions: [{user_id, name, hours, from, to}]`); the UI offers "Assign" which opens
    the assignment dialog pre-filled.

### Money

M1. Planned cost of a subcontractor assignment = its hours (S1, over the whole period, booked or not) × `rate`, with
    `core/money.py` (exact decimals, half-up cents). A total assignment's planned net is `q2(total_hours × rate)`.
M2. **Revenue** (decided, D1): no weekly revenue and no "assumed" revenue from capacity. Each
    contract shows `value = promised hours × rate`, `invoiced` (net of its project's invoices in its period, as the
    project report computes) and `open = value − invoiced`; each offer shows its net and the expected net (× win
    probability). Margin per project = contract value − planned subcontractor cost.

## Data model and storage

### Projects

* New column `kind` (`customer` | `internal`), NOT NULL. `customer`: needs a `customer_id`; may have offers,
  contracts, invoices. `internal`: no customer, no offers, contracts or invoices (the services refuse with 409:
  "Internal projects cannot have offers, contracts or invoices"); dates optional.
* The column `personal` is **removed altogether** (decided, D9): not in the table, not in responses, not in requests
  (`ProjectIn` refuses it with 422). Old exports and imports are mapped by `transfer/upgrades.py` and the importer
  (`personal: true` → `kind: internal`, unless a customer is set).
* A customer project needs a `customer_id` when it is created or changed (`ProjectIn`, 422). The database does not
  require it: legacy customer projects without a customer but with documents stay `customer` (migration rule), and
  deleting a customer does not touch `projects.customer_id` today (the reference guards of db-guards-v2 part B guard `projects` against deletion while referenced; `customer_id` stays unguarded).
* ~~A default internal project "Administration"~~ **dropped (owner, 2026-11-06): no project is created by default.**
  Earlier text: it was inserted by the
  migration when no row with that id exists (`INSERT ... WHERE NOT EXISTS`, idempotent). It can be renamed and
  archived, not deleted (409).

### Who may see and book a project (owner decision 2026-11-06, replaces the "Team" field)

* The project field `member_ids` ("Team, leave empty = everyone") is **removed** (table column, models, the web form).
  Requests that still send it are accepted and the field is ignored (old clients); responses no longer carry it.
* **Everyone may see and book time on every project** (owner, 2026-11-06, replacing the assignment-only rule of the
  same day). Assignments plan hours; they do not restrict booking. What a role may see of a project (e.g. rates and
  money fields for employees) stays governed by `core/rights.py` as before.
* Tasks follow their project (every task is visible).
* Migration: the column is dropped. The release notes say that project team lists are gone and every user sees every
  project.

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
* New contracts need a project (decided, D2): `ContractIn.project_id` required, 422 otherwise.
  Existing rows without a project stay valid (C5).

### Incoming offers

* Accepting a capacity offer creates a **total** assignment: `total_hours` = sum of the quantities of its `h` lines,
  period = `plan.start`..`plan.end`, `rate` = net of the hour lines / their hours (as today). `plan.hours_per_week` is
  accepted for old clients and ignored; `IncomingPlanIn` no longer requires one of the two hour fields.
* `incoming_offer.hours_mismatch` is removed (the two figures can no longer differ).
* Existing subcontractor assignments are **not** converted: they keep their `hours_per_week`; the owner edits them.

### Strict schema (guards, `docs/specs/db-guards-v2.md`)

This work uses the trigger guards of PR #91 (`docs/specs/db-constraints.md`) and the new guard kinds of
`docs/specs/db-guards-v2.md` (its part A, guard machinery, and part B, reference guards, `0017-reference-guards`, are
done in their own PRs **before** this migration). A guard refuses only **new or changed** values. Existing rows are
grandfathered and reported by `check-db` under the same code. There are no table CHECKs, no table rebuild and no
rejects table (D6 and D7 decided this way, 2026-11-05).

**Migration `0018-project-planning`** (one file per dialect; `requires: dea6e12b` = `0017-reference_guards` of
db-guards-v2 part B, PR #99, stacked on part A #97 with `0016-guards` = `e4b70dba`; if the chain on `main` differs at
merge time, rebase `requires` onto the head). Part B already refuses deleting a project that allocations, entries,
contracts, offers, invoices or a running timer still use (409 "This project is still used by …; archive it
instead."). Order inside the file:

1. Schema: `ALTER TABLE projects ADD COLUMN kind`, then `ALTER TABLE projects DROP COLUMN personal` and
   `DROP COLUMN member_ids` (`DROP COLUMN` keeps the other triggers and indexes; no guard reads either column).
2. Data fixes (v1 guards stay active; none of these trips them):
   * `kind` = `customer` when `customer_id` is set **or** an offer, contract or invoice refers to the project, else
     `internal` (a `personal` project with a customer becomes `customer`). Backfill before the drop of `personal`.
   * contracts and allocations with both hour fields: `total_hours` = NULL (the planner already used `hours_per_week`).
   * `projects.status` NULL → `active`, `allocations.status` NULL → `active`, `contracts.status` NULL → `confirmed`.
   * No project is inserted (the default "Administration" project was dropped on 2026-11-06).
3. `CREATE TRIGGER` for the guard instances below, declared in `schema.GUARDS` with the kinds of v2 part A.

| Table | Guard instances (kind: columns) |
|---|---|
| projects | `required: kind`, `one_of: kind` (customer, internal), `one_of: status` (from `ProjectIn` Literal), `required: name`, `implies: kind = internal ⇒ customer_id blank`, `range: budget_hours` (0, 100000, min exclusive) |
| allocations | `required: project_id, start, end`, `one_of: status` (active, cancelled), `exactly_one: hours_per_week, total_hours`, `range: hours_per_week` (0, 80, min exclusive), `range: total_hours` (0, 100000, min exclusive), `max_days: start, end, 1830`, `range: rate` (0, 10000), `implies: status = cancelled ⇒ cancelled_from set`, `implies: status = active ⇒ cancelled_from blank`, `implies: supplier_id set ⇒ rate set`, `implies: user_id set ⇒ incoming_offer_id blank`. Owner exclusivity stays the existing table CHECK `allocations_one_owner` |
| contracts | `required: title, start, end`, `one_of: status` (confirmed, done), `exactly_one: hours_per_week, total_hours`, `range: hours_per_week` (0, 10000, min exclusive), `range: total_hours` (0, 100000, min exclusive), `range: rate` (0, 10000) |

**The API is tightened in this PR first** (a guard is never stricter than every write path; a step-0 audit of every
write path, including import, transfer and procurement accept, comes before the guard):
* `projects.budget_hours` > 0 (`TotalHours` allows 0 today).
* `total_hours` > 0 on contracts, plans and allocations (`TotalHours` allows 0 today; a new exclusive type
  `PositiveTotalHours`).
* exactly one of `hours_per_week` / `total_hours` on `ContractIn` and `AllocationIn` (422 otherwise).
* A new type `ContractHours` in `validation.py`: > 0, at most 10,000 h/week (a hard "impossible input" bound, about
  250 full-time people, not a business rule; confirmed by the maintainer 2026-11-05). A contract or offer belongs
  to the team, so a team of 5 can easily contract 200 h/week; the old `Hours` (0..168) is a per-person bound. Used on
  `ContractIn.hours_per_week`, `PlanIn.hours_per_week` (offer plans) and `CommitmentChange.hours_per_week` (planner
  drafts), and wherever contract hours per week are written (accepting an offer copies the plan). Covered by the step-0
  audit. `weekly_hours` of users and suppliers keep `Hours`; allocations keep `AllocHours` (0.25..80, one person on one
  project).

**Step-0 audit results and decisions (2026-11-05; the audit is in the WP1 notes):**
* `ContractIn`: exactly one of `hours_per_week` (`ContractHours`) / `total_hours` (`PositiveTotalHours`), 422
  otherwise; `project_id` required; `"unassigned"` is refused as a stored `project_id` everywhere a request carries one
  (contracts, offers, invoices, tasks, entries, expenses, allocations).
* `PlanIn` (offer plan) and `CommitmentChange` (planner draft): at most one hour field (422 for both); none is allowed
  in an offer plan (a draft). Accepting an offer whose plan has no hours books the sum of its `h` lines as
  `total_hours`; an offer without hours and without `h` lines cannot be accepted (422 "The offer has no hours to
  plan"). A draft change that sets one hour field clears the other.
* `ProjectIn.kind` is optional for old clients: missing means `customer` when `customer_id` is set, else `internal`.
  `kind=customer` without a customer and `kind=internal` with a customer are 422; `personal` in a request is 422.
  `budget_hours` is `PositiveTotalHours` (0 is refused; clear the field instead).
* Accepting an offer creates its project with `kind=customer` and a stripped, non-blank name (fallback "Offer
  <number>").
* Procurement accept: `total_hours` = sum of the `h` lines, refused with 422 when it is 0 or above 100000, and when the
  derived rate is above 10000.
* WP1 review (2026-11-06): `kind` is derived from the customer only when a project is **created**; an update that omits
  `kind` keeps the stored one. Changing a project to `internal` while an offer, contract or invoice refers to it is a
  409 ("This project has offers, contracts or invoices; it stays a customer project."). Accepting an offer on an
  internal project is a 409 like creating one. The sum of `h` lines is rounded to 2 decimals before it is checked;
  0 or above 100000 is a 422 ("The offer's hour lines must add up to more than 0 and at most 100000 h"). A legacy plan
  whose `hours_per_week` is 0 or missing falls back to its `total_hours`, then to the `h` lines. Until WP4, a capacity
  offer whose plan has no hours or both fields books the sum of its `h` lines as `total_hours` on accept (422 rules as
  above, rate above 10000 is a 422); plans with only `hours_per_week` keep today's behaviour. (The `internaladmin` rules of
  this bullet were dropped with the default project on 2026-11-06.) PostgreSQL/MariaDB: the guards are CHECKs that also validate existing rows, like
  0016; those dialects are prepared but not wired (db-constraints.md), so this is accepted until they are.

**Data transfer:** an `UPGRADES` hook in `transfer/upgrades.py` maps `personal` to `kind` for archives of older heads
(same rule as the data fix) and applies the hour-field fix. Restores suspend the guard kinds that v2 allows (never the
number guard); the importer reports a row it cannot fix instead of writing it. The upgrade hook also drops `member_ids`. WP4 must
update the transfer shape check `_allocation_problem` (`transfer/importer.py`) so employee allocations with only
`total_hours` are restorable. db-guards-v2 part B (PR #99) guards every project link (allocations, tasks, entries,
contracts, offers, invoices, expenses, incoming offers, trips, timers) and the project ids inside saved scenarios
(`models.scenario_project_refs(changes)`). The scenario field stays `changes.allocations` (no `assignments` alias,
decided 2026-11-06), so that helper needs no change.

### Project time sheet (owner, 2026-11-06: "a time sheet / overview for each project: who booked and how much")

`GET /api/projects/{id}/timesheet?from=&to=&group=week|month` (area `time`, read; `from`/`to` optional ISO dates,
default the project period or, without one, the first to the last entry; at most 400 days; `group` default `week`).
Only time entries of that project count. Response:

```json
{"project_id": "p1", "name": "Orchard", "from": "2026-11-09", "to": "2026-11-22", "group": "week",
 "periods": ["2026-11-09", "2026-11-16"],
 "people": [{"user_id": "u1", "name": "Ada", "hours": 12.5, "billable_hours": 10.0, "unbilled_hours": 4.0,
             "by_period": [8.0, 4.5]}],
 "total": {"hours": 12.5, "billable_hours": 10.0, "unbilled_hours": 4.0, "by_period": [8.0, 4.5]}}
```

* Hours = minutes / 60, rounded to 2 in the response (sums from the exact minutes). `unbilled_hours` = billable hours
  without `invoice_id`. Periods are ISO-week Mondays or the first of the month. People sorted by name; a person whose
  user row is gone is shown as "(deleted user)".
* Who sees what: a user whose scope on `time` is `ALL` (the boss) gets every person; a `LIMITED` user gets only their
  own row (and the total of their own hours); confirmed by the owner 2026-11-06. No money fields (rates stay governed by `core/rights.py`).
* Web: the project page gets a card "Time sheet" with the week/month switch and the date range; each person's name
  links to that person's week in the time view (`#/time/<monday>/<user>`; the time view has no project filter yet, accepted 2026-11-06). CSV download of the same table.
* Work package WP7 (independent of the planner, Standard review tier); tests with literal hours.

## API contract (binding; backend and web UI are built against it in parallel)

All hours are floats rounded to 2 decimals in responses; money as today (floats of exact cents). Every warning keeps
the shape `{code, severity, field, message, details, confirmable}` and **every message states its arithmetic**.

### `GET /api/plan` (replaces `GET /api/forecast` and `GET /api/plan/day-table`)

Query: `weeks` (1–52, default 12), `project_id` (a project id or `unassigned`; filters `projects`), `user_id`
(filters `weeks` and `people`). Area `planning`, read. `people` and `projects[].supply` cover the same range as
`weeks` (from the Monday of the current week to the last Sunday). Supply starts tomorrow (D11), so the current week's `capacity`,
`unassigned` and `over_assigned` (and `people`) count only its days after today. The example is E1 with `weeks=7` (today, Sunday 08.11.2026, belongs to the week of 02.11.).

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
  `assigned_internal` "Internal"; `unassigned` "Unassigned" = per day `max(0, capacity − assigned)`, summed;
  `over_assigned` "Over-assigned" = per day `max(0, assigned − capacity)`, summed (S3); `billable_share` "Billable share" = customer / capacity in percent (null when capacity is
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

Body `{changes: {absences: [...], commitments: [...], allocations: [...]}, weeks, project_id, user_id}` (the
existing `ScenarioChanges` shape). Returns the `GET /api/plan` body plus `delta:
{projects: [{project_id, shortfall_before, shortfall_after, delta}], total}`. Writes nothing.

### `POST /api/plan/apply`, scenarios, suggestions

Unchanged paths. `apply` writes absences, commitment moves and assignments (confirm required, as today).
`suggest-vacation` ranks windows by the increase of the total contract shortfall, then by bridge days.
`suggest-start` takes `{hours, deadline, earliest, project_id}` (no `user_ids`) and returns the first start from
which an offer of `hours` total over `start..deadline` has no shortfall. `what-must-move` keeps its meaning (only
offers move) on the new verdicts. `POST /api/allocations/bootstrap` is **removed** (decided, D8), the
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
| `assignment.working_time` | warning | S6: planned 24-week average above 48 h/week (employees only) |
| `user.weekly_hours_above_law` | warning | S6: an employee's `weekly_hours` above 48 |
| `assignment.long_day`, `assignment.long_week`, `assignment.high_average` | info | S6 for the boss: more than 10 h on a day, 60 h in a week, 48 h/week on the 24-week average (no law wording) |
| `assignment.very_long_day` | warning | S6 for the boss: more than `warn_day` (default 12 h) planned on a day |
| `assignment.impossible_day` | warning | S6 for the boss: more than `max_day` (default 16 h) planned on a day |
| `contract.no_project` | info | C5 items |

Removed: `capacity.gap`, `capacity.over`, `capacity.offer_over`, `offer.project_gap`,
`allocation.employee_over_allocated`, `allocation.no_demand`, `allocation.overlap_same_project`,
`incoming_offer.hours_mismatch`. The document warnings (`offer.hours_mismatch`, rate warnings, invoice warnings,
`project.over_budget`) stay.

## Callers that change (complete list, checked on `origin/main` c63d776)

* `core/service/planning.py` (engine) and `plan_service.py`: replaced by the new package `core/service/planner/`
  (the name `planning` is taken by the old module until WP4 deletes it) with `model.py`, `spread.py` (S1),
  `supply.py` (S2–S6), `cover.py` (D1–D6, C1–C6, M1) and, in WP4, `views.py` (the JSON of the API). WP2 and WP3 are
  pure functions without storage; WP4 loads the data, switches the callers below and deletes the old modules.
* `core/service/external_capacity.py`: its spreading goes into `spread.py`; `figures()` uses it.
* `core/service/allocations.py`: `_planned_hours` uses `spread.py`; bootstrap removed; external rows editable.
* `core/service/planning_warnings.py`: `week_over_allocation`, `over_allocation_on_save` use the supply of S1/S3.
* `core/service/procurement.py`: accept builds a total assignment from the lines; `_planned_hours` via `spread.py`.
* `core/service/sales.py`: accepting an offer no longer copies `plan.user_ids`; extends the project period as today.
* `core/service/records.py`: `_check_contract_users` removed; contract and project rules of this spec.
* `core/service/team_timeline.py`: shows a person's **assignments** instead of contracts by `user_ids`.
* `core/service/transfer/importer.py` (`contracts.user_ids` mapping stays for old files) and `transfer/upgrades.py`.
* `core/calc.py`: `forecast()` delegates to the new view; `Capacity` stays the holiday/absence source.
* Rights: `core/rights.py` (subcontractor allocation PUT/DELETE under `procurement` write, employee rows under
  `planning` write), the `EXPECTED` table of `tests/backend/test_rbac.py`, and the generated `docs/roles.md`.
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
| E4 | Week of 09.11.2026. Cleo 20 h (4/day). Assignments: Administration 12 h/week, customer project Elm 10 h/week. Contract on Elm 10 h/week | Per day 2.4 + 2.0 = 4.4 > 4: 0.4 over on each of 5 days = 2 h. Nothing is scaled | `assignment.over_capacity`: "Week of 09.11.2026: Cleo is assigned 22 h (Administration 12 h, Elm 10 h) but has 20 h of capacity: over by 2 h. The projects still count the full hours." Elm supply 10, contract weekly shortfall 0. Week row: capacity 20, internal 12, customer 10, unassigned 0, over-assigned 2 |
| E5 | Week of 21.12.2026. Dan 40 h, vacation Wed 23.12. Assigned to Ash 20 h/week. Contract on Ash 20 h/week | Available days Mon 21., Tue 22., Thu 24. (25.12. holiday): 20 / 3 = 6.67/day ≤ 8, no scaling, supply 20 | Ash weekly shortfall 0. Week row: capacity 24, holiday 8, vacation 8, customer 20, unassigned 4. **Variant** with vacation also on Tue 22.12.: 2 days, 10/day > 8, over by 2 on each day = 4 h, `assignment.over_capacity` with reason `time_off`; supply stays 20, weekly shortfall 0 |
| E5d | Week of 21.12.2026. Fay (employee, 40 h, Mon–Fri) assigned 40 h/week to Ash; vacation Tue 22. and Wed 23.12. Same assignment for the boss and for subcontractor Sam (Sam unavailable 22.–23.12.) | Fay: available Mon, Thu (25.12. holiday): 40 / 2 = 20 h per day, more than 8 (capacity) and more than 10 (§3) | Fay: `assignment.over_capacity` reason `working_time_law` ("20 h planned on 21.12.2026; the law allows at most 10 h a day (§3 ArbZG)"). Boss: `assignment.over_capacity` reason `time_off`, plus the warning `assignment.impossible_day` ("20 h planned on 21.12.2026; that is more than your maximum of 16 h."). Variant with 24 h/week on those 2 days: 12 h/day, info `assignment.long_day` only (12 is not more than 12). Sam: no warning (D13) |
| E5b | Contract 10 h/week starting Wed 11.11.2026 | D2: 3 weekdays of 5 in the period | demand 6 h in the week of 09.11.2026 |
| E5c | Eve 40 h, assigned to Ash 10 h/week until Thu 19.11.2026. Week of 16.11.2026 (no holiday in BY) | 10 / 5 available days of the week = 2/day, on the 4 days inside the period | supply 8 h in that week. Until Thu 31.12.2026 instead: 01.01.2027 is a holiday, so the week of 28.12. has 4 available days, all inside the period: 10 h. Same for a subcontractor with 10 h/week: 8 h. **Week of 21.12.2026** (25.12. holiday): 10 / 4 = 2.5/day on 4 days = 10 h (today's subcontractor rule gives 2/day on 4 days = 8 h; at 90.00 that is 900.00 instead of 720.00 planned cost, see D14) |
| E6 | Incoming offer from Sam: one line 24 h × 90.00 = 2,160.00 net; plan 09.11.–04.12. with `hours_per_week` 6 | Accept: total assignment 24 h, rate 90.00, `hours_per_week` ignored | Assignment total 24, planned net 2,160.00, no warning. Edit to 30 h: planned net 2,700.00, info `assignment.differs_from_offer`: "Planned 30 h / 2,700.00 € differ from the offer's 24 h / 2,160.00 € (6 h / 540.00 € more)." |
| E7 | E1 plus offer O1 on Orchard, total 10 h 16.11.–04.12., win probability 50 % | Contracts first: K2 already took every leftover hour until 04.12. O1 covered 0 | O1 shortfall 10, `offer.shortfall`; Orchard shortfall_contracts 26, shortfall_worst_case 36, shortfall_expected 26 + 5 = 31, status `short` |
| E8 | Migration: projects (a) `personal` 1, no customer; (b) `personal` 0, no customer, no documents; (c) `personal` 1 with customer; (d) `personal` 0, no customer, one contract; (e) status NULL. Contracts (f) both hour fields; (g) `total_hours` 0 (grandfathered). Allocation (h) employee with `incoming_offer_id` (grandfathered) | Data fixes and guards per the section above | (a) internal, (b) internal, (c) customer, (d) customer, (e) `active`; (f) `total_hours` NULL; (g) kept, re-saving it unchanged succeeds, changing its title succeeds, `check-db` reports `range.bounds`; (h) kept and reported by `check-db` (`state.implies:<guard>`). A new contract with `total_hours` 0 is refused by the API (422) and by the guard. no project is inserted; the `personal` and `member_ids` columns are gone; a second run changes nothing |

## Module interfaces of WP2 and WP3 (binding)

Pure Python, no `Repository`, no `calc`, no SQL. Hours are `float`; money uses `core/money.py` (`dec`, `q2`). Weeks
are ISO weeks (Monday to Sunday). Warnings are built with `planning_warnings.warning(...)`; hours in messages are
formatted with `planning_warnings._fmt`, dates as `DD.MM.YYYY`.

```python
# planner/model.py
OwnerKind = Literal["employee", "boss", "subcontractor"]
DayOff = Literal["holiday", "vacation", "sick", "other", "unavailable"]
Holiday = Callable[[date], str | None]  # the public holiday's name or None (core/holidays.py for the state)


@dataclass(frozen=True)
class WorkLimits:  # S6, the boss's "My settings"; defaults as in S6
    info_day: float = 10.0
    warn_day: float = 12.0
    max_day: float = 16.0
    info_week: float = 60.0
    info_average: float = 48.0


@dataclass(frozen=True)
class Owner:
    id: str
    name: str
    kind: OwnerKind
    weekly_hours: float  # 0 for a subcontractor (not team capacity)
    workdays: tuple[int, ...]  # 0 = Monday
    days_off: Mapping[date, DayOff] = field(default_factory=dict)  # absences or unavailable windows, expanded
    busy: Mapping[date, float] = field(default_factory=dict)  # out-of-office / all-day block hours (D10)
    limits: WorkLimits | None = None  # only for kind "boss"


@dataclass(frozen=True)
class Assignment:
    id: str
    owner_id: str
    project_id: str
    project_kind: Literal["customer", "internal"]
    start: date
    end: date
    hours_per_week: float | None  # exactly one of the two is set
    total_hours: float | None
    booked: float = 0.0  # total assignments: hours the owner booked on the project in the period


@dataclass(frozen=True)
class Item:  # a contract (must) or an open offer (could)
    kind: Literal["contract", "offer"]
    id: str
    title: str
    project_id: str | None  # None = the scope "unassigned" (C5)
    start: date
    end: date
    hours_per_week: float | None
    total_hours: float | None
    rate: Decimal
    win: float = 1.0  # offers: 0..1; contracts always 1.0


@dataclass(frozen=True)
class Booking:  # time entries summed per project and day (all people)
    project_id: str
    day: date
    hours: float
```

```python
# planner/spread.py (S1, S2)
def is_available(owner: Owner, day: date, holiday: Holiday) -> bool
def capacity(owner: Owner, day: date, holiday: Holiday) -> float
    # employee/boss: weekly_hours / len(workdays) on an available day, minus busy, never below 0; subcontractor: 0
def spread(owner: Owner, a: Assignment, holiday: Holiday, today: date | None) -> dict[date, float]
    # today=None: the whole period (planned hours and cost, M1); otherwise only days after today (S2)
    # hours_per_week: hours_per_week / available days of the WHOLE ISO week, on the available days inside the period
    # total_hours with today: max(0, total_hours - booked) / available days in (today, end]; today=None: total / all
    #   available days of the period. No available day: {}
def planned_hours(owner, a, holiday) -> float     # round(sum(spread(..., today=None)), 2)
def planned_net(owner, a, holiday, rate: Decimal) -> Decimal
    # total assignment: q2(total_hours x rate); weekly: q2(dec(round(planned_hours_exact, 6)) x rate)
```

```python
# planner/supply.py (S2-S6)
@dataclass
class PersonDay:
    capacity: float; assigned: float; assigned_customer: float; assigned_internal: float
    holiday: float; vacation: float; absence: float; busy: float   # hours lost (weekly_hours / workdays per lost workday)

@dataclass
class Supply:
    lo: date; hi: date; today: date
    people: dict[str, dict[date, PersonDay]]                    # employees and boss only
    project: dict[str, dict[date, tuple[float, float]]]         # project id -> day -> (employees, subcontractors)
    def unassigned(self, owner_id: str, day: date) -> float     # max(0, capacity - assigned)

def build(owners: Sequence[Owner], assignments: Sequence[Assignment], holiday: Holiday,
          today: date, lo: date, hi: date) -> Supply                # days after today only, inside lo..hi
def warnings(owners, assignments, holiday, today: date, lo: date, hi: date) -> list[dict]
    # S4 assignment.over_capacity per employee/boss and week with over_assigned > 0 or (employees) a working-time-law
    #   hit; details {user_id, week, assigned, capacity, over_by, reason, reasons, projects: [{project_id, hours}]};
    #   reasons subset of ["working_time_law", "time_off", "weekly_hours"] in this order, reason = reasons[0];
    #   working_time_law: a day above 10 h or the week above 60 h (employees only)
    # S6 employees: assignment.working_time once per person: the first week w whose 24 weeks w-23..w average more
    #   than 48 h of planned hours (spread with today=None), details {user_id, week, average}
    # S6 boss: per day the highest of long_day / very_long_day / impossible_day by limits; long_week per week above
    #   info_week; high_average once like the employees' rule with info_average. Subcontractors: nothing.
    # Planned hours of a day = sum of all assignments of the person on that day (customer and internal).
```

```python
# planner/cover.py (D1-D6, C1-C6, M1 value)
@dataclass
class WeekShortfall: week: date; demand: float; supply: float; shortfall: float

@dataclass
class ItemVerdict:
    kind: str; id: str; title: str; type: Literal["weekly", "total"]; start: date; end: date
    promised: float; booked: float; remaining: float; covered: float; shortfall: float
    weekly_shortfalls: list[WeekShortfall]; rate: Decimal; value: Decimal; win_probability: float | None
    explanation: str; suggestions: list[dict]        # [{user_id, name, hours, from, to}], C6

@dataclass
class ProjectVerdict:
    project_id: str | None; supply_employees: float; supply_subcontractors: float
    remaining_contracts: float; shortfall_contracts: float; remaining_offers: float
    shortfall_worst_case: float; shortfall_expected: float
    status: Literal["ok", "at_risk", "short"]; items: list[ItemVerdict]; booked_unattributed: float

def cover(items: Sequence[Item], bookings: Sequence[Booking], supply: Supply, owners: Sequence[Owner],
          today: date, names: Mapping[str, str]) -> list[ProjectVerdict]
    # one entry per customer project with an item, plus project_id None when an item has no project (C5, supply =
    # the team's unassigned hours of each day). Internal projects never appear here.
```

```python
# planner/views.py (WP4a: the JSON of GET /api/plan, pure; the service in WP4b loads the data and calls it)
def verdict_warnings(verdicts: Sequence[ProjectVerdict], names: Mapping[str, str]) -> list[dict]
    # contract.shortfall / offer.shortfall (warning, message = the item's explanation, details {project_id, item_id,
    #   kind, shortfall, weekly_shortfalls}); project.no_supply (warning) for a customer project with remaining
    #   demand and supply 0; booked.unattributed (info) when booked_unattributed > 0; contract.no_project (info) per
    #   item of the scope without a project. Order: projects as in the verdicts, then items.
def plan_json(
    supply: Supply, owners: Sequence[Owner], assignments: Sequence[Assignment], verdicts: Sequence[ProjectVerdict],
    internal: Sequence[dict],            # [{project_id, name, budget_hours: float | None, booked: float}]
    names: Mapping[str, str], subcontractor_cost: Mapping[str, Decimal], invoiced: Mapping[str, Decimal],
    warnings: Sequence[dict], user_id: str | None = None, project_id: str | None = None,
) -> dict
    # the body of GET /api/plan exactly as in "API contract"; weeks = the ISO weeks of supply.lo..supply.hi; with
    #   user_id only that person in weeks and people (subcontractor_hours then 0); with project_id only that scope in
    #   projects ("unassigned" = the scope without a project); hours rounded to 2; money float(q2(...)).
    # Customer project: contract_value = sum of contract item values; margin = contract_value - subcontractor_cost.
    # Item: invoiced = invoiced.get(id, 0) for contracts, 0 for offers; open = value - invoiced; offers also carry
    #   expected_value = q2(value x win), contracts expected_value null.
    # Internal project entry: {project_id, name, kind: "internal", assigned (future hours of its assignments in
    #   the range), booked, budget_hours, status: "over_budget" if budget_hours and booked + assigned > budget_hours
    #   else "ok"}; the caller passes the warning internal.over_budget in "warnings".
```

Confirmed implementation details (WP3 review, 2026-11-05): `WeekShortfall.supply` is the item's covered share of that
week (demand − shortfall); a shortfall or a weekly shortfall counts only when it rounds above 0 at 2 decimals; a total
item whose window has no day after today (it ended undelivered) keeps its whole remaining as shortfall; suggestions
show the item's whole window as `from`/`to` but sum only the days inside the supply range.

Architect decisions after the code and test review (2026-11-05):
* C1 with several weekly items short in one week: the week's shortfall is shared out **pro rata to their remaining
  demand of that week** (not earliest end first).
* `win_probability` in responses is a **percentage** (0..100), like the setting `planning_default_win_probability`.
* `cover()` judges an item only on the days of `supply`. The caller (WP4b) builds the supply from the day after today to
  `max(end of the requested weeks, the latest item end)`, so no item is cut off; the week rows then show only the
  requested weeks.
* A total contract whose end has passed with hours still open keeps them as shortfall until it is set to `done`
  (its explanation then reads "... Ended on {end} with {shortfall} h not delivered."). That is intended: undelivered
  promised hours must stay visible.
* D5 holds also late in the week: hours promised for the current week and neither booked nor coverable by the rest
  of the week are a shortfall of that week.
* S4 reason `time_off` only when a holiday or day off falls on a workday of the week **and** the week is over
  capacity on another day because of it (the hours moved there); an overload on a week without lost days, or caused
  by busy time, is `weekly_hours`. Project lists in messages are sorted by the displayed name.
* Logic review: booked hours dated outside **every** item period of the project are history and are ignored (not
  `booked_unattributed`); total items take booked hours contracts first, then earliest end (D1 "must before could").
  The 24-week average (S6) is reported only for windows that end in a week inside `lo..hi`.
* `cover()` raises `ValueError("supply ends before item <id>")` when an item ends after `supply.hi`; the caller must
  build the supply far enough (see above). A contract value is `q2(dec(round(promised, 6)) x rate)` (no float noise at
  half-cent boundaries).
* The law checks (10 h day, 60 h week) and the boss limits judge **all** planned days of the week, also those on or
  before today (spread with `today=None`), so a week that today cuts in half is not under-counted.

Explanation texts (literal templates; `{h}` via `_fmt`, dates `DD.MM.YYYY`):
* total: `"{promised} h promised − {booked} h booked = {remaining} h remaining. Until {end} {project} has {left} h of
  assigned time left after its weekly contracts. Short {shortfall} h."` where `left` is the leftover available to
  the item before it takes any; the last sentence is `"Covered."` without a shortfall. Offers say "offered" instead of
  "promised" and "after its contracts" instead of "after its weekly contracts". The project name of the scope
  without a project is `"the team's unassigned time"`.
* weekly: `"{hours_per_week} h per week from {start} to {end}: short {shortfall} h in {n} week|weeks ({weeks})."` (singular for one week) with
  the Mondays as `DD.MM.`, or `"{hours_per_week} h per week from {start} to {end}: covered every week."`.
  When the current ISO week (the week of today) is one of the short weeks, the sentence gets a second one
  (owner, 2026-11-06, option (a): the rule stays, the message says why): `" This week {booked} of {hours_per_week} h
  are booked and {left} h of assigned time is left after today."` with `booked` = the hours booked on the item in the
  current week (`got` of that week), `left` = the project's leftover supply of the days after today in that week before
  the weekly group took any (the local `supply` in `_cover_weekly`, kept per item and week; `WeekShortfall.supply`
  stays the covered hours). Example: 6 h per week,
  Friday, nothing booked, no assignment on Saturday/Sunday -> `"... short 6 h in 1 week (02.11.). This week 0 of
  6 h are booked and 0 h of assigned time is left after today."`. Past weeks are never judged (unchanged).

## WP5: the planner UI (binding, 2026-11-06)

Vanilla JS as today, numbers only from the server, every label with its derivation as a `title` tooltip (texts of the
"API contract"). German number format as the rest of the app (one decimal separator, see test_planner_number_format).

* **W1 Overview tab** (`web/app.js`, PAGES.plan, replaces the "Forecast" tab; label "Overview"): filters weeks
  (4/12/26/52), person, project (incl. "Unassigned"). From `GET /api/plan`:
  1. a project list, customer projects first (status pill `ok` / `at risk` / `short`), each opens to its items: one
     row per contract and offer with promised / booked / remaining / covered / shortfall, the explanation sentence in
     full, value / invoiced / open (offers: net and expected net), and for a shortfall the suggestions as buttons
     "Assign <name> (<h> h)" that open the assignment dialog pre-filled (project, person, period = the suggestion's
     from..to, total_hours = the suggested hours); internal projects below with assigned / booked / budget;
  2. a week table (team, or the chosen person): capacity, customer, internal, unassigned, over-assigned, billable
     share, subcontractors, holiday / vacation / absence / busy as a small grey line; over-assigned in red;
  3. the warnings as one list (severity colours as today), and, for the boss only, a collapsed "N damaged records
     were skipped" line from `skipped`.
  4. (owner, 2026-11-06: "I can only edit the allocations if there is an Assign button") **every** project row, also
     internal ones and ones without a shortfall, has a button "Assignments (n)" (n = its assignment rows) that opens
     the dialog filtered to that project, with "Add assignment" in it; every person of the `people` list has
     "Assignments (n)" filtered to that person. Shown to users with planning write; read-only users see the count.
* **W2 Plan & vacation tab** (`web/planner.js`, `web/planner.css`): keeps the timeline and its draft editing
  (absences, contract/offer moves and hours, assignments) and the scenarios; recomputes with `POST
  /api/plan/simulate` (no `first`, no `include_external`); the week header shows capacity and assigned of the chosen
  person or team (red when over-assigned); a contract/offer bar is red when its item has a shortfall in the draft,
  and its tooltip / "why" panel shows the item's explanation (the day-table call is gone); the draft bar shows the
  `delta` per project ("Orchard: short 26 h -> 2 h"). Suggest-start without user_ids.
* **W3 Assignments** (`web/allocations.js`, `web/procurement.js`): the dialog lists and edits employee AND
  subcontractor rows: owner select (people and active capacity suppliers), project, period, "hours per week" or
  "total hours" (exactly one), rate (subcontractors only, required), planned hours and planned net from the server;
  offer-linked rows show the offer, can be edited and cancelled, not deleted; every other writable row has "Edit"
  and "Delete" (with a confirmation) directly in the list row, not only inside the edit form; "Add assignment" in the
  dialog of a project proposes what is missing (owner, 2026-11-06): total hours = the project's `shortfall_contracts`,
  else `shortfall_worst_case`, read from `GET /api/plan?project_id=<id>&weeks=52`; period = from the later of tomorrow
  and the earliest start of a short item to the latest end of a short item; nothing missing = empty field as before;
  the bootstrap button and its preview are
  removed; the save warnings are shown as today. Procurement: the capacity-offer form no longer asks for hours per
  week (the hour lines are booked), it shows the period only.
* **W4 My settings** (`web/app2.js` or the file that renders the "me" page): for a boss a card "Work limits" with the
  five fields and their defaults (GET/PUT `/api/settings/work-limits`), explained in one sentence each; not shown to
  other roles.
* Web tests: each package rewrites the browser tests of its page (fake API answers built from E1/E7 numbers);
  tests of removed features (forecast fields, day table, bootstrap, per-day pools) are deleted.

### Today page: what I need to do (owner, 2026-11-06: "the today page should show how much of what I need to do")

* `GET /api/dashboard` (existing, same rights) gains `projects: [{project_id, name, kind, planned_today,
  planned_week, booked_today, booked_week, left_week}]` for the dashboard's person: every project with an active
  employee assignment of that person in the current ISO week, plus every project the person booked time on this week.
  `planned_*` = the hours of the person's assignments on today / on every day of this week, from
  `planner/spread.py` with `today=None` (the whole period, so days already past this week count as planned);
  `booked_*` = the person's time entries; `left_week` = max(0, planned_week − booked_week). Hours rounded to 2;
  sorted by left_week descending, then name. Computed in the time service (not in calc.py, which the planner imports).
* Web (Today page): a card "Your projects this week" with columns Today planned / Today booked / Week planned / Week
  booked / Left this week, a total row, tooltips with the derivation, and a line "No assignments this week" when the
  list is empty. Clicking a project name starts the timer on it if the existing timer control allows that, else
  links to the project.
* Owner, 2026-11-06 (Today page):
  1. **Change project and task while the timer runs**: `PATCH /api/timer` (area `timer` write) with `project_id`,
     `task_id`, `note` (each optional; a task must belong to the project, a project must exist and not be internal-
     incompatible with the rules of time entries; 409 without a running timer); `started` is kept. The timer card shows
     project and task selects while running and sends the change at once.
  2. **Play buttons**: every row of "Your projects this week" and, below each project, one row per task the person
     booked on this week (`projects[].tasks: [{task_id, name, booked_week}]` in the dashboard) gets a play button that
     calls `POST /api/timer/start` with that project/task; a running timer is stopped and saved first (existing
     behaviour of start), so switching never loses time.
  3. **Manual time without a start**: in the timer card's manual mode a duration alone is enough: for today the entry
     ends now and starts at now − duration (not before 00:00; then it starts at 00:00 and ends at 00:00 + duration);
     for another day it starts at 09:00. Start and end stay editable; entering them keeps today's behaviour.
  4. **Internal projects are never billable** (owner, 2026-11-06): the time-entry dialog unticks and disables
     "billable" when an internal project is chosen (and enables it again for a customer project); the server stores
     `billable: false` for every entry created or changed onto an internal project, also via the timer and the API.
  5. **A timer stopped within the first minute** (owner, 2026-11-06): `POST /api/timer/stop` takes an optional
     `discard: bool = false`; with `true` the timer is reset and no entry is written (`{"running": false}`, no
     `entry`). The web asks when Stop is pressed less than 60 s after `started`: confirm "Less than a minute tracked.
     Delete it instead of booking 1 min?" -> OK sends `discard: true`, Cancel books as today (1 min). From 60 s on
     there is no question.
  6. **Play on today's entries**: every row of the "Today" entry list gets the same ▶ button (`timerPlay`) with the
     entry's project and task (no button for an entry without a project), so earlier work continues in one click.
  7. **Manual while the timer runs** (owner, 2026-11-06): pressing ✎ Manual while a timer runs switches the card to
     the manual form, pre-filled from the timer: date = the day of `started`, start = `started` (HH:MM), stop = now,
     duration from both; description, project and task stay. The timer keeps running on the server until **Add**:
     Add saves the entry (as manual mode does today) and then calls `POST /api/timer/stop` with `discard: true`, so
     the time is booked once. Switching back to ⏱ without Add shows the running clock again. A timer started on an
     earlier day gets stop = 23:59 of that day (an entry ends on its own day).
  8. **A timer that runs past midnight** (review of PR #104): `POST /api/timer/stop` books the entry on the start
     day and ends it at 23:59 of that day at the latest (`minutes` = from start to that end, at least 1); the time
     after midnight is not booked. The response is unchanged; the Today card shows the saved entry.

## WP4b: the planner behind the API (binding, 2026-11-06)

WP4b and WP5 land together on branch `feat/planner-service`: the old endpoints and the old web planner are removed in
the same merge, so main never has a planner page without its API.

### `planner/load.py` (P1)

```python
@dataclass
class Snapshot:
    today: date
    holiday: Holiday                      # public holidays of settings["state"] (core/holidays.py)
    owners: list[Owner]                   # active users and active person-capacity suppliers
    assignments: list[Assignment]
    items: list[Item]                     # D1: confirmed contracts, open binding offers
    bookings: list[Booking]               # all time entries per project and day, dated <= today
    names: dict[str, str]                 # project id -> name ("(deleted project)" for a missing one)
    internal: list[dict]                  # [{project_id, name, budget_hours, booked}] of active internal projects
    invoiced: dict[str, Decimal]          # contract id -> net invoiced (rule below)
    subcontractor_cost: dict[str, Decimal]  # project id -> sum of planned_net of its subcontractor assignments (M1)
    skipped: list[dict]                   # damaged legacy rows that were left out: {coll, id, reason}

def load(store: Repository, today: date, busy: BusyTimeProvider | None = None, changes: dict | None = None) -> Snapshot
```
* Owners: users with `active` true; kind `boss` for role boss, else `employee`; `days_off` from absences (kind
  vacation -> "vacation", sick -> "sick", else "other"); `busy` from the provider in out-of-office mode (D10:
  `BusyTimeProvider.busy_hours(..., ooo_only=True)` counts only out-of-office and all-day blocks); `limits` of a boss
  from the setting `work_limits.<user_id>` (P8; defaults when missing). Suppliers of kind `person-capacity` with
  `active` true become kind `subcontractor`, `weekly_hours` 0, their workdays and `unavailable` windows as
  `days_off` "unavailable".
* Assignments: allocation rows with status `active`, and `cancelled` ones (`Assignment.cut` = `cancelled_from`: the row
  is spread over its original period as if active, then the days from `cut` on are dropped, so a cancellation never
  raises the daily hours; `planned_net` of a cut row is the sum of its kept days);
  `project_kind` from the project. `booked` of a total assignment = the owner's entries on the project inside the
  period, dated <= today.
* Items: contracts with status `confirmed`; offers with status `sent`, a plan with start and end, no contract made from
  them and `valid_until` not passed. An offer plan without hours takes the sum of its `h` lines as `total_hours`
  (like accepting). Rate: the contract's/plan's rate, else the project's, else the customer's, else `default_rate`.
  `win` = plan `win_probability` / 100, else the setting `planning_default_win_probability` / 100.
* Damaged rows (bad dates, end before start, no or both hour fields, an allocation without project or owner) are
  skipped and listed in `skipped`, never raised.
* `changes` (a `ScenarioChanges` dict) is applied in memory exactly like today's `apply_changes` /
  `apply_allocation_changes`: absences added/changed/removed, commitment overrides of period and hours, allocation
  rows added/changed/removed.
* `invoiced`: net (`calc.invoice_amounts`) of the invoices of the contract's project that are not drafts or cancelled,
  attributed to the contract whose period contains the invoice date (earliest end first); others are not counted.

### `planner/service.py` (P2) and `planner/suggest.py` (P3)

`PlannerService(store, busy)` replaces `PlanningService` (same method names where they exist):
* `plan(weeks, user_id=None, project_id=None, today=None) -> dict`: the `GET /api/plan` body. The week rows cover
  the Monday of today's week plus `weeks` weeks; the supply for `cover()` is built from today to
  `max(end of those weeks, latest item end)` (no item is cut off). Warnings: `supply.warnings`, `verdict_warnings`,
  `internal.over_budget`, and `assignment.no_demand` (a subcontractor assignment of a customer project whose items
  need nothing in its period). Also returns `skipped` (damaged rows) for the boss's eyes.
* `simulate(changes, weeks, user_id=None, project_id=None, today=None)`: the same body for the world with `changes`,
  plus `delta` as in the API contract (shortfall_contracts per project before and after, and the total).
* `apply`, `list_scenarios`, `get_scenario`, `save_scenario`, `delete_scenario`, `apply_scenario`, `entitlement`,
  `extend_project`, `cover_plan`, `project_timeline`: ported from `plan_service.py` with unchanged behaviour (they do
  not depend on the old engine except `entitlement`, which uses the snapshot's absences and holidays).
* `check_offer(draft, exclude_id=None)`: `offer.shortfall` of the draft as an offer item next to the others, plus
  `planning_warnings.document_warnings` as today. `warnings_for(coll, rec)`: as today, plus `contract.shortfall` for a
  saved contract and `offer.shortfall` for a saved offer.
* `planner/suggest.py`: `suggest_vacation(user_id, days, window_from, window_to, changes)` ranks windows of `days`
  working days by the increase of the total contract shortfall, then by bridge days (as today);
  `suggest_start(hours, deadline, earliest, project_id, changes)` = the first available start from which an offer of
  `hours` total over start..deadline has no shortfall, or `null`; `what_must_move(plan)` = for an offer draft with a
  shortfall, the smallest shift (in days) of one other open offer of the same project that removes it, else the
  largest `total_hours` that fits. Each returns the body the current endpoint returns, without the `user_ids` input.

### API (P4)

* `GET /api/plan` (area `planning` read) and `POST /api/plan/simulate` as in "API contract"; the other `/api/plan/*`
  paths keep their bodies (SuggestStartIn without `user_ids`); `GET /api/forecast` and `GET /api/plan/day-table` are
  removed. `services.planner` replaces `services.planning` in deps.py, routers/planning.py, routers/sales.py and
  routers/records.py; `sales.py` imports `cover_plan` from the new service.

### Allocations API (P5), procurement (P6), work limits (P8)

* P5: `AllocationIn` = `user_id` or `supplier_id`, `project_id`, `start`, `end`, `hours_per_week` (AllocHours) or
  `total_hours` (PositiveTotalHours), `rate` (Rate; required for a supplier, refused for an employee). PUT/DELETE of
  subcontractor rows under `procurement` write, employee rows under `planning` write (core/rights.py, the EXPECTED
  table of tests/backend/test_rbac.py, docs/roles.md); a row with `incoming_offer_id` can be cancelled and edited but
  not deleted. `planned_hours` / `planned_net` from `planner/spread.py`. Save warnings `assignment.over_capacity`
  (from `supply.warnings` for that person and period), `assignment.overlap`, `assignment.differs_from_offer` (E6).
  `POST /api/allocations/bootstrap` is removed.
* P6: procurement accept of a capacity offer always books `total_hours` = the sum of its `h` lines (E6; 422 rules of
  the step-0 decisions); its figures and `_planned_hours` use `planner/spread.py`; `transfer/importer.py`
  `_allocation_problem` accepts employee rows with only `total_hours`.
* P8: `GET/PUT /api/settings/work-limits` (any signed-in boss, for themself; model `WorkLimitsIn`, rules of S6),
  stored in the setting `work_limits.<user_id>`; saving a user (employee) with `weekly_hours` above 48 returns the
  warning `user.weekly_hours_above_law`.
* P7 (last): delete `core/service/planning.py`, `plan_service.py`, `external_capacity.py`, the old functions of
  `planning_warnings.py` (keep `warning`, `_fmt`, `document_warnings`, `project_warnings`, `budget_warning`,
  `plan_hours`, `line_hours`), `calc.forecast`, and their tests (test_planning.py, test_planner_per_project.py,
  test_planner_external.py, test_utilisation_allocated.py, test_planning_spread.py, test_external_capacity.py and the
  four skipped user_ids tests); rewrite tests/backend/randomized/test_planning_invariants.py for the new engine.
  Before deleting: `procurement.py` still uses `external_capacity` (`_weekly`, the dependency hint, `effective_end` of
  cancelled rows; move `effective_end` into `planner/spread.py` or `load.py`) and the old engine in
  `_first_week_without_demand` (replace it with `assignment.no_demand` of `PlannerService.plan`);
  `AllocationService.present_external` / `commitments()` must use `planner/spread.py`.

Order: P1, then P2 and P5/P6/P8 in parallel, then P3 and P4, then P7, then WP5 (web) and WP6 (docs). Review tier:
Full (one review after P7 over the whole branch).

## Work packages (fresh Sonnet agents, one each; backend and web UI in parallel against the contract)

| WP | Content | Depends on |
|---|---|---|
| WP0 | Not part of this work: guard kinds (db-guards-v2 part A) and reference guards (part B) land first in their own PRs | – |
| WP1 | Step-0 write-path audit, API tightening, migration `NNNN-project-planning` (3 dialects), `kind`, data fixes, guard instances in `schema.GUARDS`, "Administration", models (`ProjectIn.kind`, `ContractIn`, `AllocationIn`, `IncomingPlanIn`), `transfer/upgrades.py`, importer; E8 | WP0 |
| WP2 | `planner/model.py`, `planner/spread.py`, `planner/supply.py` (S1–S6) as pure functions with E4, E5 (and variant), E5c, E5d and the supply figures of E1/E2; the callers are switched in WP4 | – |
| WP3 | `planner/cover.py`: demand, attribution, covering, verdicts (D1–D6, C1–C6, M1 value) as pure functions; E1–E3, E5b, E7 | WP2 |
| WP4 | Service and API: `GET /api/plan`, simulate, apply, suggestions, warnings on save, allocations API, incoming accept (E6), team timeline, removals; `api/openapi.json`; randomized model updated | WP1, WP3 |
| WP5 | Web UI against the contract with E1/E7 fixtures: planner overview (week rows, project and item verdicts with explanation and "Assign"), assignment dialog for people and subcontractors, project form `kind`, offer plan without assignee | contract only; integration after WP4 |
| WP6 | `docs/planning.md` rewritten with E1 as the walkthrough; superseded headers | WP4 |

## Review tier

**Full** (capacity numbers, money of subcontractor cost and margin, a storage migration with data fixes and new guards):
code review, test review, and a logic review that recomputes E1–E8 by hand. The randomized suites run once before
the merge (records, data model and import are touched).

## Decisions (product owner, 2026-11-05) and what is still open

| # | Question | Status |
|---|---|---|
| D1 | Revenue: no weekly and no "assumed" revenue; per contract value, invoiced, open; per offer net and expected net | **decided: yes** |
| D2 | Items without a project use the team's unassigned hours; new contracts need a project | **decided: yes** |
| D3 | How a weekly contract counts a partial week (start or end mid-week) | **decided: pro rata over Mon–Fri** (D2 rule) |
| D4 | Over-assignment | **decided: warn only, never scale** (S3) |
| D5 | Booked attribution: the week's weekly items first, then total items earliest end first | **decided: yes** |
| D6 | Rows that violate a new rule at migration time | **decided: grandfathered** by the trigger guards (PR #91), reported by `check-db`; no rejects table |
| D7 | Foreign keys | **decided: reference triggers** (db-guards-v2 part B, own PR) |
| D8 | Remove "Allocate my capacity" | **decided: yes** |
| D9 | `personal` | **decided: removed altogether** |
| D10 | Calendar busy time and capacity | **decided: only out-of-office and all-day blocks reduce capacity**; ordinary busy events do not |
| D11 | Booked up to and including today, supply from tomorrow | **decided: yes** |
| D12 | Total assignments: open hours carry over from week to week and expire at the assignment's end | **decided** |
| D13 | Subcontractors are never scaled or checked for over-assignment | **decided: yes** |
| D14 | Subcontractors: holidays and their own unavailable windows (vacation mid-project) are days off; weekly hours move to the other days of the week like an employee's | **decided** (changes the planned cost of existing subcontractor assignments with a holiday in their period, E5c; the release notes name it) |

All decisions are taken. Implementation starts when db-guards-v2 parts A and B are merged (WP2 and WP3 do not touch
storage and may start earlier).
