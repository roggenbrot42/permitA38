# Spec: database guards v2 (more guard kinds, references)

Status: **ready for implementation** (2026-11-05; maintainer approved parts A and B). Builds on [`db-constraints.md`](db-constraints.md) (PR #91, migration `0016-guards`).
Requested by the planner rework ([`project-centric-planning.md`](project-centric-planning.md), PR #90). Review tier:
**Full** (storage, every write and delete path, restore and import undo).

## Split of work

| Part | Owner | Migration |
|---|---|---|
| A. Guard machinery: new kinds in `schema.py` / `dialect.py`, `check-db` rules, `guards_suspended` kinds | this spec, own PR | none (code only) |
| B. Reference guards (section 3) | this spec, own PR after A | `0017-reference-guards` |
| C. Planner guards and schema changes (sections 1, 2, 4 applied to projects, allocations, contracts) | the planner rework | its own migration, `requires` = head at merge time (0018 if B lands first) |

The planner declares its guard *instances* in `schema.GUARDS` and writes their SQL in its own migration; it does not
need its own CHECKs or a "rejects" table. A only provides the kinds.

## Rules that hold for every new kind (unchanged from v1)

- SQLite: `BEFORE INSERT` + `BEFORE UPDATE OF <cols>` triggers, `RAISE(ABORT, 'permita38:<code>')`, refusing only new or
  changed values. Every INSERT trigger carries the grandfathering clause over **all columns the guard reads**
  (`NOT EXISTS (SELECT 1 FROM t WHERE id = NEW.id AND c1 IS NEW.c1 AND c2 IS NEW.c2 ...)`).
- **Never stricter than every write path.** A guard bound equals the API bound in `validation.py` / `models.py` or is
  looser. If the planner wants a tighter rule (see section 2), it tightens the API **in the same PR** that adds the
  guard, after a step-0 audit of every write path, exactly as in v1.
- Every kind has a `check-db` rule under the **same code**, derived from `schema.GUARDS` (like `format.date` and
  `range.negative` in v1), so stored grandfathered rows are always reported. A test asserts that every guard kind has
  a rule and that the rule's (table, columns) set equals the guards of that kind.
- PostgreSQL / MariaDB: native `CHECK` constraints where the rule is row-local; triggers only for references
  (section 3). Not wired yet; string generation and tests only.

## 1. New kinds (part A)

Conditions are a tiny dialect-neutral AST in `schema.py`, evaluated in SQL by `dialect.py` and in Python by `check-db`
from the same objects, so the two can never disagree:

```python
(
    Eq(col, value) | In(col, values) | IsSet(col) | IsBlank(col)
)  # IsSet = not NULL, not '' and not only SQL_BLANK whitespace; IsBlank = the opposite
```

| Kind | Parameters | Refuses (new or changed values) | Code |
|---|---|---|---|
| `one_of` | `col`, `values` | `IsSet(col)` and the value is not in `values` | `vocab.unknown` |
| `exactly_one` | `a`, `b` | both set, or both blank | `state.exactly_one` |
| `required` | `col` | `IsBlank(col)` (NULL, `''`, or only whitespace for text) | `state.required` |
| `max_days` | `start`, `end`, `days` | both dates valid and `julianday(end) - julianday(start) > days` | `range.too_long` |
| `range` | `col`, `lo`, `hi`, `lo_exclusive` | value set and outside the bounds | `range.bounds` |
| `implies` | `if_`, `then` (conditions) | `if_` holds and `then` does not | `state.implies:<guard name>` |

v1's `non_negative` (`range.negative`) stays as it is; `range` is for upper bounds and exclusive minimums. `range` is only for numeric (int/float) columns; `Guard` refuses it on any other known column type.

Notes:
- `IsSet` is false for NULL, `''` and strings made only of the `SQL_BLANK` characters (space, tab, LF, VT, FF, CR); a
  non-breaking space (NBSP) and other Unicode spaces count as **set**, in SQL and in Python alike.
- `one_of` treats NULL, `''` and whitespace-only values as "not set" and accepts them: it checks set values only. This is
  looser than the API's `Literal` on purpose.
- `required`: a legacy NULL that is changed to `''` on a `required` column is a change and is refused.
- The condition AST has no `and` / `or` / `not` (a limit); add a kind later if a rule needs it.
- `Guard` rejects, when it is declared: parameters the kind does not use, identical columns for `exactly_one` /
  `order` / `max_days`, `lo > hi` (or an empty exclusive range), an `implies` code other than `state.implies:<name>`,
  and an `Eq` / `In` / `one_of` literal whose Python type does not fit the column's logical type (str for id/str/text,
  int/float for numeric, bool for bool), so SQL affinity and Python comparison cannot differ.
- PostgreSQL / MariaDB: a `range` on a numeric column emits only the NULL test (`'' ` would raise on PostgreSQL and
  equals 0 on MariaDB); PostgreSQL's `max_days` only casts strings that match a real-calendar-date regex, so the CHECK
  never raises; MariaDB compares strings with `BINARY` (its default collation is case-insensitive), escapes backslashes
  and strips the same blank set as `SQL_BLANK`.
- `one_of` values are **generated from the model `Literal`** (`typing.get_args`), not typed twice. A test fails when the
  `Literal` and the guard differ, so adding a status means adding a migration that drops and recreates that one
  trigger (no table rebuild). Use it only for **stable** vocabularies (see answers).
- `exactly_one` for `allocations.user_id` / `supplier_id` is **not** added: the table CHECK `allocations_one_owner`
  already enforces it for every row.
- `required` on a column the repository diverts to `extra` (wrong type) sees NULL and refuses: intended.
- `max_days` ignores malformed dates (`julianday` returns NULL); `format.date` reports those.

## 2. Bounds the planner asked for, checked against the API (2026-11-05)

| Column | Requested | API today | Guard allowed now |
|---|---|---|---|
| `allocations.hours_per_week` | > 0, <= 80 | `AllocHours` 0.25 .. 80 | `range(0, 80, min_exclusive)` |
| `allocations.total_hours`, `contracts.total_hours` | > 0, <= 100,000 | `TotalHours` 0 .. 100,000 (0 allowed) | `range(0, 100000)`; `> 0` only if the planner PR makes the API exclusive first |
| `contracts.hours_per_week` | (with allocations) | `Hours` 0 .. 168 today; PR #90 adds `ContractHours` > 0 .. 10,000 (a contract covers a team; maintainer: "a team of 5 can easily contract 200 h/week") | `range(0, 10000, min_exclusive)` together with `ContractHours` |
| `projects.budget_hours` | > 0 | `TotalHours`, 0 allowed | `range(0, 100000)`; `> 0` only with an API change |
| rates | <= `MAX_RATE` | `Rate` 0 .. 10,000 | `range(0, 10000)` |

## 3. References (part B) — decision on D7: yes, reference triggers

- **Insert/update side** (`ref.dangling`, same code as `check-db`): a new or changed reference must point to an
  existing row. Guarded: `allocations.project_id`, `user_id`, `supplier_id`; `contracts.project_id`;
  `entries.project_id`; `offers.project_id`, `invoices.project_id`. Not guarded: `projects.offer_id` (offers and
  projects point at each other; guarding both would make the write order impossible), `invoices.customer_id` (old
  imports may name customers that were merged; check-db reports it), and every other reference (check-db only).
- **Delete side** (`ref.in_use`): deleting a project, user or supplier that a guarded column still points to is refused.
  Grandfathering does not apply to deletes (deleting is always a change).
- **Services check first; the trigger is the backstop.** Step-0 (2026-11-05) found:
  - `RecordService.delete` of a project checks only allocations (`records.py:288`). It must also check entries,
    contracts, offers and invoices (and a running timer, see below) and refuse with one 409 that names what still uses
    the project ("This project is still used by <counts>; archive it instead.", counts in the order allocations, time entries, contracts, offers, invoices, running timer, e.g. "12 time entries and 1 invoice"). **Behaviour change:** today such a
    delete succeeds and leaves those rows pointing at nothing. Archiving (`status: archived`) is the way out. Confirmed by the maintainer 2026-11-05 ("I love database consistency").
  - `auth.delete_user` and `suppliers.delete` already check allocations (suppliers more); nothing to add.
  - `records._write` has no existence check for `project_id` on entries, contracts, offers and invoices
    (`check_project_customer` passes when the project is missing). Add a 422 "Unknown project" when the value is new or
    changed and the project does not exist, mirroring the trigger (an unchanged legacy value is not re-checked).
  - Timers: `start_timer` validates the project (422 "Unknown project"); deleting a project that a running timer uses
    is refused by the project delete check. A timer from before this version may still point at a project deleted
    back then: `stop_timer` (also run by `start_timer`) then saves the entry without `project_id` (and without
    `task_id` when the task belonged to that project or is gone), keeping the tracked time, and logs one INFO line
    (ids only), so the guard cannot fire.
  - `offer_to_invoice` copies the offer's `project_id`; when that project is gone, refuse with a 422 that says so,
    before the insert.
- **Import undo:** a batch holds only customers, invoices, offers and expenses (no projects, users, suppliers, no
  `project_id`), so no reference guard can fire there; the existing digest check already refuses an undo after an
  edit. No change, no undo test for references.
- **Restore** (transfer import, `import-json`): add the kind `reference` to `RESTORE_SUSPENDED`; suspending it drops the
  insert-side **and** the delete-side triggers. Write order: the transfer import writes `users` before the collections
  (today users come last, after allocations); `import-json` order is already fine. After writing, `check-db`'s `ref.*`
  findings become import warnings, as v1 does for dates; for `ref.dangling` only the guarded (table, column)
  pairs above count (exact code match, so `ref.dangling_audit` and unguarded columns such as `invoices.customer_id`
  are not reported as restore warnings). The number guard stays unsuspendable.
- **Indexes:** add `idx_contracts_project`, `idx_offers_project`, `idx_invoices_project` (the delete trigger looks up
  referencing rows; allocations and entries are indexed already).
- `"unassigned"` (planner scope) is never stored in `allocations.project_id`; if it can be, the guard excludes it
  explicitly and the step-0 list says where.


### Every link to a project (maintainer decision 2026-11-06)

"No inconsistent projects, rather archive if deleting with dangling references isn't possible." So **no cascades**:
a project can be deleted only when nothing points at it; otherwise the delete is refused and the user archives it.

- Reference guards (same migration 0017, still unreleased) also on `tasks.project_id`, `expenses.project_id`,
  `incoming_offers.project_id`, `trips.project_id`, `timers.project_id`; lookup indexes
  `idx_incoming_offers_project`, `idx_trips_project`, `idx_timers_project` (tasks and expenses are indexed already).
- `records._write`: "Unknown project" 422 also for tasks and expenses (new or changed value only). Trips and incoming
  offers keep their existing check on every write (stricter; a legacy dangling trip must get a valid project first).
- Project delete counts, in this order: allocations, tasks, time entries, contracts, offers, invoices, expenses,
  incoming offers, trips, saved planning scenarios, running timer. Planning scenarios store project ids in the JSON
  `plan_scenarios.changes` (no trigger can see them): the service counts them, and `check-db` reports a scenario whose
  project is gone (`ref.dangling`, warning).
- Stopped timers normally carry no project (stop rewrites the row). A legacy stopped timer that still has one does not
  block the delete: the delete clears that timer's `project_id`/`task_id` in the same transaction (the row has no
  meaning; this is not a cascade of data).

## 4. Notes for the planner's migration (part C)

- **Dropping `projects.personal`:** use `ALTER TABLE ... DROP COLUMN` (SQLite >= 3.35; CI and local have 3.51), not a
  rebuild. DROP COLUMN keeps the table's other triggers and indexes; it fails only if a trigger reads the dropped
  column (none of v1's guards read `personal`). A rebuild (`CREATE` new, copy, `DROP`, `RENAME`) drops the old
  table's triggers, so if a rebuild is ever needed the migration must recreate them; the v1 drift test
  (trigger set == `guard_sql` of `GUARDS`) catches a forgotten one.
- **Order inside the migration:** schema changes, then data fixes (`kind` backfill, `total_hours = NULL` where both
  hour fields are set), then `CREATE TRIGGER` for the new guards, so the backfill is not refused by its own guards.
  v1 triggers stay active during the data fixes; setting a column to NULL never trips them.
- **The fixed internal project id must match `ID_PATTERN` `^[0-9a-z]{6,32}$`:** `internal-administration` contains a
  hyphen and would fail `Id` validation everywhere. Use e.g. `internaladmin`.
- `INSERT ... SELECT ... WHERE NOT EXISTS` for that row is fine.
- **Data transfer:** the migration needs an entry in `transfer/upgrades.py`: an `UPGRADES` hook that maps `personal`
  to `kind` for archives from older heads (not `NO_UPGRADE_NEEDED`, since a column changes meaning).

## 5. Answers to the planner's questions

- **Vocabularies as guards?** Yes, for stable ones: `projects.kind`, `projects.status`, `allocations.status`,
  `contracts.status`. v1 excluded them because a table CHECK needs a rebuild per new value; with triggers a new value
  is one `DROP TRIGGER` / `CREATE TRIGGER` in a migration, and the Literal-equals-guard test forces that migration.
  Keep fast-moving vocabularies (calendar kinds, scan states, import kinds) out.
- **Does check-db cover the grandfathered rows?** Today: `format.date`, `range.negative`, `state.period_reversed`,
  `numbering.duplicate`, `ref.dangling` (all the references above), and the strict-model pass (`model.invalid`) covers
  the `projects` and `contracts` vocabularies, but **not** allocations (no CRUD model). Part A adds a rule per new kind,
  derived from `GUARDS`, so every grandfathered row any new guard leaves behind is reported.

## Tests (parts A and B)

Per kind: a refused new row, an accepted edge value, a grandfathered row that can be re-saved unchanged and with other
fields changed, but not changed into another violation; the `check-db` rule reports the grandfathered row with the
same code. Literal-equals-guard test for every `one_of`. References: insert with a missing target refused; delete of a
referenced target refused with the service message (API) and `ref.in_use` (store); restore with dangling references
succeeds with warnings; import undo order. Randomized suites once before merge.
