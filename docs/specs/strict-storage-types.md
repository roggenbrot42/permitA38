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
DROP TRIGGER ... ;                          -- 1. EVERY guard_% trigger, before anything else
CREATE TABLE "x__new" (... STRICT);         -- 2. per table, in TABLES order (pre-flight passed, section 2)
INSERT INTO "x__new" (cols) SELECT cols FROM "x";
DROP TABLE "x";
ALTER TABLE "x__new" RENAME TO "x";
CREATE INDEX ... ;                          -- 3. every index, generated
CREATE TRIGGER ... ;                        -- 4. every guard trigger, generated from schema.GUARDS
```

**All guard triggers are dropped first** (verified 2026-11-06 on SQLite 3.51): `ALTER TABLE ... RENAME` re-parses
the whole schema, and a trigger on another table that still names the dropped table fails it (`error in trigger
g_ins: no such table: main.projects` for a reference trigger on `entries` while `projects` is rebuilt).

- **Order:** the runner already wraps the file in one transaction; a failure rolls everything back and the
  pre-migration backup exists. Tables are rebuilt in `TABLES` order.
- **Triggers:** recreated after all tables and indexes, from `guard_sql` of `schema.GUARDS` (including the planner's
  guards). The drift test (trigger set == `guard_sql`) proves nothing is lost.
- **The migration file is a frozen snapshot** of `TABLES`, indexes and `GUARDS` at the time it is generated. Rule:
  generate it only after the planner's `0018-project_planning` is on `main`, and regenerate it whenever another
  migration lands before it (a test fails anyway if a trigger or index is missing, but regenerate rather than patch).
- **CHECK `allocations_one_owner`** is kept in the new `allocations` table; it moves into `schema.CHECKS` as already
  declared and the DDL generator emits it.
- **Type mapping** stays (`TEXT`, `INTEGER`, `REAL`); these are all valid `STRICT` types.
- **SQLite version:** `STRICT` needs 3.37, `json_valid` 3.38. Startup refuses with a clear message on an older
  SQLite (CI and local have 3.51; Python 3.12+ on Windows bundles a newer one; check the Linux minimum in
  `docs/` and CI).

### 2. Wrong-typed records make the migration fail (maintainer decision 2026-11-06)

No value is normalised, moved or dropped. A database that holds a wrong-typed record is **not** migrated; the user
fixes the records first, with the previous version. "Wrong-typed" means any of:

| Problem | Detected by |
|---|---|
| a column holds a value of the wrong storage type (e.g. text in a `REAL` column, `typeof(c)` not allowed for the logical type: `int` -> integer; `float` -> integer or real; `bool` -> integer 0/1; `id`/`str`/`text`/`json` -> text) | SQL per column |
| `extra` holds a key that is a column name (a value the repository diverted: `type.misplaced` in `check-db`) | `json_each(extra)` joined with the table's column names |
| a `json` column or `extra` is not valid JSON | `json_valid(c) = 0` |
| `id` is NULL | `id IS NULL` |

**Pre-flight, before anything is changed.** The migration ships with a pre-flight query file next to it
(`NNNN-strict_tables.preflight.sql`, per dialect): one `SELECT` that returns a row `(table, id, column, problem)` for
every offending record. The runner (`SQLRepository._run_migration`) runs a migration's pre-flight first, when the file
exists; if it returns rows, it applies nothing and raises `DatabaseRevisionError` with a message that names every
offending record (table, id, column, problem; at most 50 listed, then the count) and says: the database was not
changed; fix these records with the previous version of permitA38 (edit and save them, which rewrites the value with
its proper type) or restore a backup, then start again. The pre-migration backup is taken as today. A generic
pre-flight hook in the runner is new machinery: it is dialect-neutral (a `.preflight.sql` file is optional for every
migration), tested in `test_migrations.py`, and documented in AGENTS.md "Database migrations".

Because `check-db` refuses a database with pending migrations, the pre-flight message is the user's list. In addition,
`check-db` on the **previous** version already reports all four problems (`type.misplaced`, `format.json`,
`state.required` for `id`, and a new `type.wrong_storage` rule added in work package 1 so users can check before
upgrading).

With the pre-flight passed, the plain copy `INSERT INTO "x__new" SELECT ... FROM "x"` cannot fail: every value already
fits the `STRICT` column.

### 3. The repository refuses wrong types

- `_encode`: a key that **is** a column and a value that does not `fits()` → `ConstraintViolation(code="type.mismatch",
  collection, field)` (not `extra`). The API maps it to 409 like the guards. The strict `*In` models make this
  unreachable from the API; it is the backstop for service bugs and raw writes.
- Keys that are not columns, and columns the live database lacks (older revision), still go to `extra`.
- **Verbatim restore** (transfer import, `import-json`) follows the same rule: an archive or folder that holds a
  wrong-typed record is refused as a whole before anything is written, and the message lists the records (same four
  problems; for archives a value under a column name with the wrong type, which is how a misplaced value is exported).
  No lenient mode.
- `_decode` is unchanged. After the migration no misplaced value can exist, so no re-save path can meet one.

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
satisfy `json_valid()`. Grandfathered like the other kinds; it joins `ROW_LOCAL_KINDS` and `RESTORE_SUSPENDED`, and the
generic row-local `check-db` rule covers it (code `format.json`, Python twin `json.loads`). The
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
2. How a misplaced value appears in each release's export format (so the restore check in section 3 catches it).
3. The minimum SQLite version on the supported platforms (Linux distributions in `docs/`, Windows/macOS Python
   builds, the Docker image).
4. `check-db` on a realistic demo database and on databases restored from each release's export format: which
   records the pre-flight would refuse, and how the user fixes each kind with the previous version (if a kind cannot
   be fixed through the UI, the spec must name the fix before implementation — e.g. a one-off repair command in the
   previous release line).

## Work packages

1. **Schema, DDL and check-db:** `Table.strict`, `Column.not_null`, CHECK emission, version check at startup, the
   `type.wrong_storage` check-db rule (ships first, so users can check before the upgrade); tests that the generated
   DDL is `STRICT` and that `dump-ddl` matches.
2. **Pre-flight hook and migration:** the runner's optional `.preflight.sql`, the pre-flight query, the rebuild
   (drop triggers, rebuild, indexes, triggers) in all three dialects. Tests: a database at the previous head with one
   offending record of each problem kind is **refused**, unchanged (byte-identical file), still at the previous
   revision, and the message names each record; a clean database (incl. a grandfathered duplicate number and old rows
   breaking `required`) migrates, every value is unchanged (raw SQL before vs. after), every table is `STRICT`, the
   trigger and index sets equal `schema.py`.
3. **Repository:** `type.mismatch` instead of the `extra` fallback, translation to 409; repository contract tests
   (`test_repository_contract.py`); update `fits()` users.
4. **Required and JSON guards:** the `required` guard list from step 0, the `json` kind and its `check-db` rule; tests
   per guard as in v2.
5. **Restore paths:** both refuse an archive or folder with a wrong-typed record, listing the records; the randomized
   API models (`tests/backend/randomized/test_api_stateful.py` and
   `tests/web/e2e_random/test_api_random.py`) if any behaviour they model changes.
6. **Docs:** `docs/validation.md` (the database refuses wrong types), `docs/porting-to-postgres.md`, `CHANGELOG.md`.

## Tests that must exist

- Writing a wrong-typed value for a known column through the repository raises `type.mismatch`; through the API the
  strict model refuses it first (422).
- An unknown key still round-trips through `extra`; a database at an older revision still keeps a newer column's value
  in `extra`.
- The pre-flight refuses each problem kind and leaves the database byte-identical; a restore with a wrong-typed
  record is refused and writes nothing.
- Raw SQL inserting a wrong type into a `STRICT` table fails (proves the table, not only the repository, refuses it).
- The migration test from work package 2, run on SQLite; the randomized suites once before the merge.

## Decided

- Wrong-typed records make the migration fail (maintainer, 2026-11-06): no normalisation, no automatic repair, no
  lenient restore. The user fixes the listed records with the previous version first.
