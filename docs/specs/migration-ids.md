# Spec: migration identifiers instead of contiguous numbers

*Status: specification, decided by the architect model (see AGENTS.md "Division of labour"). Implement on a branch
from `main`, before the pending feature branches (numbering/import, calendar, roles, ...) are merged.*

## Problem

Migrations are `0001_name.sql`, numbered contiguously, and `CURRENT_REVISION` is an integer constant. Every feature
branch that needs a migration takes "the next free number", so parallel branches collide (`feat/calendar` and
`feat/numbering-import` both took 0004) and whoever merges second must rename files in three dialect folders, edit
`CURRENT_REVISION` and fix tests that hard-code it. The model is taken over from stustapay (`-- migration: <id>` and
`-- requires: <id>` headers): the identity of a migration is a random id, the order is the chain of `requires`.

## Format

* File: `permita38/core/schema/migrations/<dialect>/<id>-<slug>.sql`, e.g. `7ecb5a30-planning.sql`.
  `<id>` = 8 lowercase hex characters (random), `<slug>` = `[a-z0-9_]+`. The same file name exists in **every**
  dialect folder. Files carry no ordinal number: the order is defined by the chain, not by the name.
* First lines of every file (before any SQL), exactly:

  ```
  -- migration: 7ecb5a30
  -- requires: 3f9a1c02
  ```

  `requires: none` for the first migration. The header id must equal the id in the file name.
* Rules (all enforced by tests, see below): ids are unique; each `requires` names an existing migration; exactly one
  migration has `requires: none`; every migration is required by at most one other (**linear chain, no forks**);
  hence exactly one head. The three dialect folders contain identical ids, slugs and `requires` values.
* The SQL rules stay as they are (no `BEGIN`/`COMMIT`, never edit a released migration).

## Legacy revisions

Released databases (alpha.1) carry the integer revision 1 or 2 in SQLite `PRAGMA user_version`; `main` has 3.
The existing three migrations become the root of the chain with **fixed ids** (rename the files, add the headers):

| old file | id |
|---|---|
| `0001_initial.sql` | `00000001` |
| `0002_totp.sql` | `00000002` |
| `0003_planning.sql` | `00000003` |

Keeping the old number as the id for these three keeps the upgrade path trivial and recognisable. New migrations use
random ids. (If maintainers prefer random ids here too, the mapping table above is the only thing to change.)

## Tracking applied migrations

* New table `schema_migrations(id TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)` in all dialects.
  It is created by the runner itself (not by a migration file) before the first run, `CREATE TABLE IF NOT EXISTS`.
* On a database without `schema_migrations`: read the legacy revision (`PRAGMA user_version` on SQLite; the existing
  mechanism on the other dialects). Revision `n` (1..3) means the first `n` legacy ids are applied: insert them into
  `schema_migrations` (one transaction) and continue as normal. Revision 0 / empty database: nothing applied.
* The runner applies, in chain order, every migration whose id is not in `schema_migrations`, each inside its own
  transaction together with the insert into `schema_migrations` (as today the revision update is part of it).
* **Downgrade protection stays:** if `schema_migrations` contains an id the running version does not know, refuse to
  open the database with the existing "written by a newer version" message (this replaces `rev > CURRENT_REVISION`).
  If the applied ids are not a prefix of the chain (a hole), refuse with a clear error: this means a migration was
  skipped, which must never happen silently.
* **Old binaries stay safe:** on SQLite the runner also keeps `PRAGMA user_version = <number of applied migrations>`,
  so an alpha.1 binary that opens an upgraded database still sees a revision above its own and refuses it. Do not
  remove this. Document it in the module docstring.
* The pre-migration backup (`_pre_migration_backup`) keeps working; name it with the head id instead of a revision
  number (`...-before-<head id>.sqlite3`); adapt the existing backup glob in tests.
* `health()` / `/api/health`: report `revision` as the id of the last applied migration (`null` for an empty
  database), `current_revision` as the head id, plus `applied` (count). Update `api/openapi.json` and the tests that
  compare `revision == current_revision`.

## Code changes

* `core/database.py`: drop `CURRENT_REVISION` (and the contiguous-number check). `Migration` gets `id`, `name`,
  `requires`, `path`. `list_migrations(dialect)` parses and validates the header, returns the chain in order and
  raises `ValueError` on any rule violation above. Add `head_id()` / `LEGACY_IDS`.
  `create_migration(name)` generates a random id that is not yet used, sets `requires` to the current head and writes
  the header in every dialect folder.
* `core/repository/sql.py` + `sqlite.py`: replace the integer revision logic (`_read_revision`, `_run_migration`,
  `rev > CURRENT_REVISION`, `pending = ...`) by the id-based logic above; keep `get_revision()` returning the last
  applied id for callers that display it. No SQL outside `core/repository/`.
* `cli`: `create-migration` prints the new ids and "set `requires` is done for you; there is no revision constant to
  bump"; `migrate` prints `Migrated <old id|none> -> <head id> (<n> migrations applied)`; add
  `migrate --status` (or `list-migrations`) listing the chain with an applied marker. Remove the "bump
  CURRENT_REVISION" text.
* Docs: update the AGENTS.md section "Database migrations" (no number, no `CURRENT_REVISION`; "merge conflict on
  `requires`: when two branches both added a migration, the second to merge rebases its **first** new migration's
  `requires` onto the new head and nothing else"), `docs/porting-to-postgres.md`, `RELEASING.md` and the comment
  "RENUMBERING NOTE" in `0003_planning` (remove it).
* Every test that mentions `CURRENT_REVISION` or a revision number (`test_planning.py`, `test_totp.py`,
  `test_backup_cli.py`, `test_repository_contract.py`, `test_migrations.py`) is rewritten against the head id /
  chain, never against a literal number.

## Tests (must exist)

1. Chain validation unit tests with synthetic migration folders: duplicate id, missing `requires` target, two roots,
   fork (two migrations requiring the same one), cycle, header id != file name, bad header, dialect drift.
2. The real folders form a valid chain and are identical across dialects.
3. **Legacy upgrade:** build a SQLite database at revision 1, 2 and 3 from the old files (fixtures from the git
   history are fine, or apply the three files by hand and set `user_version`), open it with the new runner: all
   remaining migrations are applied, `schema_migrations` lists them, data is untouched, and the migrated schema
   equals `schema.py` (existing test).
4. Empty database -> full chain; second run is a no-op.
5. Unknown applied id -> refusal with the "newer version" message and no change to the database. Hole in the applied
   ids -> refusal.
6. A failing migration rolls back, leaves no row in `schema_migrations` and is reported with its id and name.
7. `user_version` equals the number of applied migrations after every run.
8. A merge-simulation test: two synthetic branches each add one migration on the same head -> validation reports a
   fork (this is the signal that the second branch must re-point `requires`).

## Out of scope

Squashing old migrations, down-migrations, reordering already released migrations. Do not change any migration SQL
other than adding the two header lines to the three existing files per dialect.

## Open design decisions to list in the report

Anything not fixed above; the simplest option consistent with this spec. In particular state how the PostgreSQL/
MariaDB legacy-revision read is done (only SQLite is verifiable here).
