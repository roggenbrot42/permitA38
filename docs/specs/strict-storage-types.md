# Spec: strict storage types

Status: **spec** (2026-11-06). Roadmap item "Strict storage types", the next database to-do after
[`db-constraints.md`](db-constraints.md) and [`db-guards-v2.md`](db-guards-v2.md). Review tier: **Full** (storage
layer, a migration that rebuilds every table, every write path, restore).

## Why

Three gaps remain after the guards:

1. **No column types are enforced.** SQLite tables are not `STRICT`, so any column accepts any value type.
2. **Wrong-typed values are hidden, not refused.** `SQLRepository._encode` moves a value that does not `fits()` its
   column into the `extra` JSON (`sql.py`, `_coerce`). The column stays NULL, indexes and guards never see the value,
   and only `check-db` (`type.misplaced`) notices.
3. **Essential fields can be missing.** No column is `NOT NULL`; even `id` is a `TEXT PRIMARY KEY`, which SQLite
   allows to be NULL unless declared `NOT NULL`.

## Goal

- Every SQLite table is a `STRICT` table: the database refuses a value of the wrong type.
- The repository **refuses** a value of the wrong type for a known column instead of moving it to `extra`.
- `id` is `NOT NULL` in every table; the essential fields (table below) are required for new and changed rows.
- Forward compatibility stays: keys that are **not** columns still go to `extra` (newer versions, other devices), and
  a database at an older revision still keeps values of columns it lacks in `extra`.

Out of scope: money as exact decimals in storage (REAL stays; all arithmetic already goes through `money.py`),
foreign keys (the reference guards cover them), PostgreSQL/MariaDB wiring.

## Decisions

### 1. `STRICT` tables via one rebuild migration

SQLite cannot switch an existing table to `STRICT`; each table is rebuilt in one migration
(`NNNN-strict_tables`, `requires` = head at merge time; the planner rework's `0018-project_planning` lands first, so
this is expected to be `0019`):

```
CREATE TABLE "x__new" (... STRICT);         -- generated from schema.py
INSERT INTO "x__new" (cols) SELECT <normalised cols> FROM "x";
DROP TABLE "x";
ALTER TABLE "x__new" RENAME TO "x";
CREATE INDEX ... ;                          -- every index of the table, generated
CREATE TRIGGER ... ;                        -- every guard trigger that reads the table, generated
```

- **Order:** the runner already wraps the file in one transaction; a failure rolls everything back and the
  pre-migration backup exists. Tables are rebuilt in `TABLES` order.
- **Triggers:** `DROP TABLE` drops the table's triggers, and the delete triggers of reference guards live on the
  target table (`projects`, `users`, `suppliers`). The migration recreates **every** guard trigger after all tables
  are rebuilt, from `guard_sql` of `schema.GUARDS` at that point (including the planner's guards). The drift test
  (trigger set == `guard_sql`) proves nothing is lost.
- **CHECK `allocations_one_owner`** is kept in the new `allocations` table; it moves into `schema.CHECKS` as already
  declared and the DDL generator emits it.
- **Type mapping** stays (`TEXT`, `INTEGER`, `REAL`); these are all valid `STRICT` types.
- **SQLite version:** `STRICT` needs 3.37, `json_valid` 3.38. Startup refuses with a clear message on an older
  SQLite (CI and local have 3.51; Python 3.12+ on Windows bundles a newer one; check the Linux minimum in
  `docs/` and CI).

### 2. The copy normalises; it never fails on old data

`STRICT` refuses a value of the wrong type in the `INSERT ... SELECT` and would fail the migration. Values the
repository could never have written cannot be dropped silently either. The copy therefore normalises per column type:

| Logical type | Copy expression | Old value that does not fit |
|---|---|---|
| `int` | `CASE WHEN typeof(c) = 'integer' THEN c WHEN typeof(c) = 'real' AND c = CAST(c AS INTEGER) THEN CAST(c AS INTEGER) ELSE NULL END` | moved into `extra` under the column's key (below) |
| `float` | `CASE WHEN typeof(c) IN ('integer','real') THEN CAST(c AS REAL) ELSE NULL END` | moved into `extra` |
| `bool` | `CASE WHEN c IS NULL THEN NULL WHEN typeof(c) IN ('integer','real') THEN (c <> 0) ELSE NULL END` | moved into `extra` |
| `id`, `str`, `text`, `json` | `CASE WHEN typeof(c) = 'text' THEN c WHEN c IS NULL THEN NULL ELSE CAST(c AS TEXT) END` | none (text conversion is lossless) |

"Moved into `extra`": the copy sets `extra = json_set(COALESCE(extra, '{}'), '$.<col>', <old value>)` for a row whose
value does not fit, so nothing is lost and `check-db` keeps reporting it as `type.misplaced`. This is exactly where the
repository would have put it. (Implementation: one `UPDATE` per affected column before the copy, inside the migration.)

### 3. The repository refuses wrong types

- `_encode`: a key that **is** a column and a value that does not `fits()` → `ConstraintViolation(code="type.mismatch",
  collection, field)` (not `extra`). The API maps it to 409 like the guards. The strict `*In` models make this
  unreachable from the API; it is the backstop for service bugs and raw writes.
- Keys that are not columns, and columns the live database lacks (older revision), still go to `extra`.
- **Verbatim restore** (transfer import, `import-json`) copies old data that may hold misplaced values (they come back
  from `_decode` under the column's key). Add `Repository.lenient_types()`, a context manager allowed only inside
  `transaction()`, under which `_encode` keeps today's behaviour (divert to `extra`). Only the two restore paths use it,
  together with `guards_suspended`; afterwards `type.misplaced` findings become import warnings like the other restore
  findings.
- `_decode` is unchanged: a misplaced value in `extra` is still returned under its key, so old rows keep working, and
  re-saving such a record outside a restore now fails with `type.mismatch` until the value is fixed. Services that
  re-save stored records unchanged (adopt-vendors, plan apply, offer accept, trip unlink, invoice delete releasing
  entries) must be checked in step 0: if a re-save of an old misplaced value is possible there, the service drops or
  repairs that key explicitly with a comment, never by silently diverting again.

### 4. Required fields: `id NOT NULL`, the rest as `required` guards

- `id` is `NOT NULL` in every table (column constraint). The repository always sets it; `check-db` gets a rule
  `state.required` for a stored row without id (practically impossible; if one exists the migration fails and
  `check-db` names it before the upgrade).
- Every other essential field becomes a `required` guard (guards v2 part A, grandfathered: refused only for new or
  changed values, old rows stay and `check-db` reports them). A column-level `NOT NULL` would fail the rebuild on old
  data, which the guard design exists to avoid.

| Table | Required |
|---|---|
| customers | name |
| projects | name |
| tasks | project_id, name |
| entries | user_id, date, minutes |
| contracts | title, start, end |
| offers, invoices | date, status |
| expenses | date |
| absences | user_id, start, end, kind |
| users | username, role |
| suppliers | name, kind |
| incoming_offers | supplier_id, status |
| trips | user_id, start, end |
| allocations | project_id, start, end, status |
| templates | name |
| number_sequences | doc_type, period, last_value |
| api_tokens | user_id, token_hash |
| receipt_scans, document_scans | status, sha256 |
| calendar_sources | user_id, kind |
| calendar_events | source_id, start_utc |

The list is a proposal from the models; **step 0 decides it**: a field stays in the table only if every write path
already sets it (cite the model field or code line). Fields the planner rework already guards (`projects.kind`,
statuses) are not duplicated.

### 5. JSON columns are valid JSON

A new row-local guard kind `json` (guards v2 machinery): a set value of a `json` column (and every `extra` column) must
satisfy `json_valid()`. Grandfathered like the other kinds; `check-db` gets the matching rule (`format.json`). The
repository always writes JSON through `encode_json`, so this is a backstop for raw writes and restores.

### 6. `schema.py` describes it all

- `Table.strict = True` for every table; `Column.not_null` (only `id` for now); the DDL generator emits `STRICT`,
  `NOT NULL` and the table CHECKs. `initial_migration_sql`/`dump-ddl` follow.
- The required fields are `required` guards in `schema.GUARDS`; the JSON validity guards are generated for every
  `json` column and every `extra` column.
- PostgreSQL / MariaDB: already typed natively; their migration adds `NOT NULL` on `id` and the guard constraints
  (string generation only, not wired).

## Step 0 (before implementation)

A read-only audit, results written into this spec before the first work package starts:

1. For every field in the required table: every write path that creates or changes the row, and whether it always
   sets the field. Drop fields that some path can legitimately leave empty.
2. Every path that re-saves a stored record unchanged (section 3): can it carry a misplaced value from `extra`?
3. The minimum SQLite version on the supported platforms (Linux distributions in `docs/`, Windows/macOS Python
   builds, the Docker image).
4. `check-db` on a realistic demo database and on a database restored from each release's export format: how many
   `type.misplaced`, `state.required`, `format.json` findings would the migration and the guards meet.

## Work packages

1. **Schema and DDL:** `Table.strict`, `Column.not_null`, CHECK emission, version check at startup; tests that the
   generated DDL is `STRICT` and that `dump-ddl` matches.
2. **Migration:** the rebuild with normalisation, extra-moves, index and trigger recreation for all three dialects;
   tests: a database at the previous head with a misplaced value in every column type, a NULL in a required field,
   invalid JSON in a json column and a grandfathered duplicate number migrates without error, nothing is lost
   (`check-db` before and after reports the same rows), every table is `STRICT` afterwards, the trigger and index sets
   equal `schema.py`.
3. **Repository:** `type.mismatch` instead of the `extra` fallback, `lenient_types()`, translation to 409; repository
   contract tests (`test_repository_contract.py`); update `fits()` users.
4. **Required and JSON guards:** the `required` guard list from step 0, the `json` kind and its `check-db` rule; tests
   per guard as in v2.
5. **Restore and re-save paths:** both restore paths under `lenient_types()` with `type.misplaced` warnings; the
   re-save paths from step 0; the randomized API models (`tests/backend/randomized/test_api_stateful.py` and
   `tests/web/e2e_random/test_api_random.py`) if any behaviour they model changes.
6. **Docs:** `docs/validation.md` (the database refuses wrong types), `docs/porting-to-postgres.md`, `CHANGELOG.md`.

## Tests that must exist

- Writing a wrong-typed value for a known column through the repository raises `type.mismatch`; through the API the
  strict model refuses it first (422).
- An unknown key still round-trips through `extra`; a database at an older revision still keeps a newer column's value
  in `extra`.
- A misplaced value already in `extra` is still returned by `get`/`list`; re-saving the record unchanged outside a
  restore is refused, inside a restore it is kept and reported.
- Raw SQL inserting a wrong type into a `STRICT` table fails (proves the table, not only the repository, refuses it).
- The migration test from work package 2, run on SQLite; the randomized suites once before the merge.

## Open questions for the maintainer

- Should a stored record with a misplaced value block its own editing (as section 3 proposes, until fixed), or should
  services repair it automatically by dropping the bad value with a warning? The proposal blocks, because a silent
  repair loses data.
