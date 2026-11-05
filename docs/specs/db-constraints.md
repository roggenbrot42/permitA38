# Spec: database guards for numbering and basic formats

Status: **spec, not scheduled** (2026-11-05). Depends on [`db-integrity-check.md`](db-integrity-check.md) (`check-db`)
being merged first. Review tier: **Full** (numbering, storage layer, every write path).

## Goal

Make the database itself refuse the most important broken states for **new writes**, as a last line of defence
behind the `*In` models and the services:

1. **Unique document numbers** per table (`invoices.number`, `offers.number`). Today only `numbering.py` guarantees
   this, inside `store.transaction()`; that holds with SQLite's single lock but not with a second process, a second
   backend or a bug in a write path.
2. **Basic formats and invariants**: ISO dates in date columns, non-negative values where every write path already
   forbids negatives, `start <= end` on date periods.

Out of scope: vocabularies (status, role, kind): every new status would need a migration in three dialects, and
`check-db` already reports unknown values through the strict models. Foreign keys: they fight the import undo, the
synced-file setup and the forward-compatible `extra` handling; `check-db` reports dangling references. `STRICT`
tables and NOT NULL: see the roadmap ("Strict storage types").

## Decision: guards are triggers that check only new or changed values (SQLite)

A `UNIQUE` index or a table `CHECK` validates **existing** rows when it is created. `startup()` auto-migrates, so a
database that already holds one duplicate invoice number would fail the migration and the app would not start. The
user could not repair it either: a numbered invoice may legally not be renumbered. A pre-flight that refuses the
migration would leave the user stuck on the old version for ever.

So in SQLite every guard is a pair of `BEFORE INSERT` / `BEFORE UPDATE` triggers that raise
`RAISE(ABORT, 'permita38:<code>')` only when the value is **new or changed**. Existing violations are kept
(grandfathered) and reported by `check-db`. This was verified against the repository's upsert statement
(`INSERT ... ON CONFLICT(id) DO UPDATE`): re-saving a grandfathered duplicate succeeds; a new duplicate, or changing
a number into a duplicate, is refused. Triggers also cost no table rebuild, so a guard can be changed later with
`DROP TRIGGER` / `CREATE TRIGGER`.

Template (number uniqueness; the same shape for every guard):

```sql
CREATE TRIGGER "guard_invoices_number_unique_ins" BEFORE INSERT ON "invoices"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> ''
 AND NOT EXISTS (SELECT 1 FROM "invoices" WHERE "id" = NEW."id" AND "number" IS NEW."number")
 AND EXISTS (SELECT 1 FROM "invoices" WHERE "number" = NEW."number" AND "id" <> NEW."id")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;

CREATE TRIGGER "guard_invoices_number_unique_upd" BEFORE UPDATE OF "number" ON "invoices"
WHEN NEW."number" IS NOT NULL AND NEW."number" <> '' AND NEW."number" IS NOT OLD."number"
 AND EXISTS (SELECT 1 FROM "invoices" WHERE "number" = NEW."number" AND "id" <> NEW."id")
BEGIN SELECT RAISE(ABORT, 'permita38:numbering.duplicate'); END;
```

The `NOT EXISTS (... same id, same value)` clause on INSERT is what grandfathers a row that is only re-saved (the
upsert fires the INSERT trigger first). Plus an index `idx_<table>_number` on `number` so the lookups stay fast.

Every guard's code is the **same code `check-db` uses** for the stored-data finding, so a refused write and the
report speak the same language.

**PostgreSQL and MariaDB** are prepared but not wired, and no database of either exists. Their migrations use native
constraints: PostgreSQL a partial unique index `WHERE number IS NOT NULL AND number <> ''` and `CHECK` constraints;
MariaDB a unique index on a generated column `number_key AS (NULLIF(number, ''))` and `CHECK` constraints. Record in
`docs/porting-to-postgres.md` that the SQLite guards grandfather old rows and the native ones do not, and that a port
of an existing SQLite database must run `check-db` first.

## Guards

Implementation step 0, before writing any SQL: for **every** guard below, list every write path that stores the
column (CRUD routes, finalize, invoice from time, offer to invoice, timer stop, planner, document importer, receipt
and document scans, trips, allocations, the transfer import, `import-json`) and confirm that the path already makes
the rule true (usually through the `*In` model types in `validation.py`). **A guard must never be stricter than
every write path**, otherwise a valid request ends as a 500. Drop a guard that some path can legitimately violate and
list it under "Open design decisions" with the path.

| Code | Tables / columns | Rule (checked for new or changed values only) |
|---|---|---|
| `numbering.duplicate` | `invoices.number`, `offers.number` | unique within the table when set and not `''` |
| `format.date` | `entries.date`; `invoices`/`offers` `date`, `due_date`, `valid_until`, `paid_date`, `service_from`, `service_to`; `expenses.date`, `paid_on`; `absences`, `contracts`, `projects`, `allocations` `start`/`end`; `allocations.cancelled_from`; `incoming_offers.received_on`, `valid_until` | `GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'` when set (format only; the calendar check stays in the models) |
| `range.negative` | `entries.minutes`; `rate` on customers, projects, tasks, entries, contracts, allocations, suppliers; `projects.budget_hours`; `contracts`/`allocations` `hours_per_week`, `total_hours`; `expenses.net`, `vat` | `>= 0` when set (looser than the API on purpose, e.g. `minutes` is 1..1440 there) |
| `state.period_reversed` | `absences`, `contracts`, `projects`, `allocations`, `trips` `start`/`end`; `invoices`/`offers` `service_from`/`service_to` | `end >= start` when both are set |

Not `entries.start`/`end`: clock times, night shifts end before they start. Not document totals: credit notes are
negative.

`check-db` already has `numbering.duplicate` and `state.period_reversed`; add `format.date` and `range.negative` there
with the same names when this spec is implemented (they then duplicate part of `model.invalid` on purpose: the guard
codes must be reportable on their own).

**Cross-table uniqueness** (with `numbering_shared`, one number must not appear in both `invoices` and `offers`)
cannot be expressed by an index and depends on a setting. It stays in `numbering.py` and in `check-db`; no guard.
**Empty numbers:** confirm that no write path stores `number = ''`; if one does, make it drop the key instead (the
guard ignores `''` either way).

## Schema description

Guards are part of the schema, so they go into `schema.py`, not only into SQL (do not repeat the
`allocations_one_owner` pattern):

```python
@dataclass(frozen=True)
class Guard:
    name: str  # trigger name stem, e.g. "guard_invoices_number_unique"
    table: str
    kind: str  # "unique" | "date" | "non_negative" | "order"
    columns: tuple[str, ...]  # one column, or (start, end) for "order"
    code: str  # the RAISE code, e.g. "numbering.duplicate"


GUARDS: tuple[Guard, ...] = ...
CHECKS = {"allocations": ("allocations_one_owner", "<sql>")}  # declared for the drift test; SQL stays in 0011
```

`dialect.py` gets `SQLiteDialect.guard_sql(guard) -> list[str]` (the INSERT and UPDATE trigger) and the PostgreSQL /
MariaDB equivalents. The migration files are written by hand as usual (`permita38 create-migration guards`), and a
test asserts that the triggers in a freshly migrated SQLite database equal `guard_sql` for every guard (compare
`sqlite_master` `type='trigger'` names and normalised SQL). Extend
`test_migrated_sqlite_database_matches_the_schema_description` and `test_tables_match_the_declarative_schema`
accordingly.

## Storage layer

- `repository/base.py`: `class ConstraintViolation(Exception)` with `code` (the part after `permita38:`) and
  `collection`. `SQLRepository.save` / `save_many` translate a driver `IntegrityError` whose message starts with
  `permita38:` into it; other integrity errors are re-raised unchanged. The transaction is rolled back as today.
- API: one exception handler maps `ConstraintViolation` to **409** `{"detail": "The database refused the change
  (<code>).", "code": "<code>"}`. Services that already check (finalize, the importer) keep their own friendlier
  messages; the guard only fires when a check was missed.
- **Verbatim restore.** The transfer import (`core/service/transfer/importer.py`, into an empty instance) and
  `import-json` copy existing data, which may contain grandfathered violations; on an empty database they are all
  INSERTs, so the guards would refuse them. Add `Repository.guards_suspended()`, a context manager allowed **only
  inside** `transaction()`: SQLite drops the guard triggers and recreates them from `schema.py` before the
  transaction commits (DDL is transactional in SQLite; a rollback restores them). Use it only in those two restore
  paths, never in the document importer (new data must obey). After the restore, run the `check-db` rules for the
  guard codes and add their findings as warnings to the import report.
- Run the new storage behaviour through `tests/backend/test_repository_contract.py`.

## Tests

- One test per guard and code: a new violating row is refused with `ConstraintViolation(code)`; a valid row passes;
  a **grandfathered** violating row (planted before the migration with the migration helpers) can be re-saved
  unchanged and with other fields changed, but not with the guarded value changed into another violation.
- Upgrade test: a database at the previous head with a duplicate invoice number and a reversed period migrates
  without error; the rows are unchanged; `check-db` reports both.
- API: a forced duplicate (bypass `numbering.py` with a monkeypatched allocator) gives 409 with the code, not 500.
- Restore: a transfer export that contains a duplicate number imports into an empty instance with a warning; the
  guards exist again afterwards (`sqlite_master`); a failing restore leaves the guards in place.
- Step 0 list as a test: for each guarded column, one request per write path with a valid edge value (`minutes` = 1,
  `rate` = 0, `start == end`) succeeds.
- Randomized suites once before merge (`make test-summary SUITES="random"`): this touches numbering and every write
  path.

## Docs

`docs/numbering.md` (legal note: the database now refuses duplicate numbers), `docs/validation.md` (a third layer
after hard limits and sanity checks), `docs/porting-to-postgres.md` (see above), `CHANGELOG.md`.

## Open questions for the maintainer

- Should a grandfathered duplicate invoice number block **finalizing further invoices** until it is resolved, or only
  be reported? This spec only reports it.
