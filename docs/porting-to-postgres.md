# Porting permitA38 to PostgreSQL or MariaDB

> **Honest status:** only **SQLite** is implemented and tested. The PostgreSQL and MariaDB support is *prepared*: the
> dialect classes generate DDL/upserts as strings (unit-tested), migration files exist for both, and a backend-independent
> contract test suite is ready to run against them. Nothing here has ever run against a real PostgreSQL/MariaDB server.
> This guide is the checklist for finishing the job.

## Why this is mostly wiring

```
web → API routers → services → Repository (interface) → SQLRepository (generic SQL) → driver
                                                           │
                                                  Dialect (SQLite | PostgreSQL | MariaDB)
```

- Services, calculations, documents and the API only know `permita38.core.repository.Repository`
  (`list/get/save/delete`, `settings`, `transaction()`, attachments, `backup`, `migrate`, …). There is no SQL outside
  `permita38/core/repository/` and no database driver import anywhere else.
- The schema is described once in `core/repository/schema.py` (tables → columns with logical types `id/str/text/int/
  float/bool/json`, indexes).
- `core/repository/dialect.py` turns that description into dialect-specific DDL, `INSERT … ON CONFLICT` /
  `ON DUPLICATE KEY UPDATE`, quoting, placeholders (`?` vs `%s`, JSONB casts) and value conversion.
- `core/repository/sql.py` (`SQLRepository`) implements records, settings, transactions and migrations **generically** on
  top of a few driver hooks. `sqlite.py` is the only subclass today.

## Steps

1. **Pick the driver** and add it as an optional dependency, e.g. `psycopg[binary]>=3.1` (PostgreSQL) or `mariadb` /
   `PyMySQL` (MariaDB) in `pyproject.toml` (`[project.optional-dependencies] postgresql = [...]`).
2. **Implement the repository class** `permita38/core/repository/postgresql.py` (copy the shape of `sqlite.py`):
   subclass `SQLRepository`, set `backend = "postgresql"` and `dialect = PostgresDialect()`, and implement the hooks:
   `_open` / `_close` (connection or pool), `_exec` / `_rows` (execute with `%s` params; return rowcount / rows),
   `_begin` / `_commit` / `_rollback`, `_read_revision`, `_run_migration`.
   - Revision tracking: create a `schema_migrations(version BIGINT PRIMARY KEY, name TEXT, applied_at BIGINT)` table
     (outside the numbered migrations, in `_open`) and read `MAX(version)`; `_run_migration` executes the SQL and inserts
     the row in the same transaction (PostgreSQL has transactional DDL; **MariaDB does not** – DDL auto-commits, so
     write MariaDB migrations to be re-runnable or split them into small steps).
   - **Concurrency:** the SQLite repository serialises everything through one connection and a lock. For a server
     backend use one connection per thread or a pool, and make `transaction()` real (`BEGIN`, isolation `READ COMMITTED`
     or better). The contract suite contains a read-modify-write test (`test_transactions_are_isolated_read_modify_write`)
     that needs `SELECT … FOR UPDATE` or `SERIALIZABLE` + retry to pass – it will tell you.
   - **Attachments** stay files under `data_dir/attachments` unless you move them into a blob column / object store;
     keep `save_attachment` / `attachment_path` behaviour (the API serves them through the file path).
   - `backup()` / `backup_dir`: leave unimplemented (base class raises `NotImplementedError`, `backup_dir` is `None`) and
     document `pg_dump` / `mariadb-dump`; automatic file backups are skipped for such backends.
   - `conflicts()`: return `[]` – server backends have no sync conflicts.
3. **Register it in the factory** (`core/repository/factory.py`): create the class for `config.database.backend`.
4. **Check the migrations for your dialect.** `core/schema/migrations/postgresql/` and `…/mariadb/` already contain
   `0001_initial.sql`, generated from the neutral schema (`permita38 dump-ddl --dialect postgresql`). Review them against a
   real server. **Rule:** every migration must exist in every dialect folder with the same number and name; test
   `test_every_migration_exists_for_every_dialect` enforces it. `permita38 create-migration <name>` creates the next file
   for all dialects at once.
5. **Run the contract suite** against your server (empty scratch database!):

   ```bash
   PERMITA38_TEST_POSTGRES_URL=postgresql://user:pw@localhost/permita38_test \
   PERMITA38_TEST_MARIADB_URL=mysql://user:pw@localhost/permita38_test \
   python -m pytest tests/backend/test_repository_contract.py -q
   ```

   The suite (`tests/backend/test_repository_contract.py`, fixtures in `tests/backend/repository_registry.py`) covers CRUD,
   JSON/bool/float fidelity, unknown-field preservation, ordering, transactions and rollback, concurrency, settings,
   timers and attachments. Fix the dialect or the repository until it is green; backend-specific tests go next to
   `test_store_sqlite.py`. The parameters are skipped, with a reason, while the environment variables are unset.
6. **Run the rest of the backend tests** with the new backend selected in the test config. Most of them go through the API
   and need nothing else.
7. **Configuration:**

   ```yaml
   database:
     backend: postgresql          # or mariadb
     url: postgresql://permita:secret@db.example.org:5432/permita38
     auto_migrate: true
   ```

## Things to watch

| Topic | SQLite | PostgreSQL | MariaDB |
|---|---|---|---|
| JSON columns | `TEXT` | `JSONB` (driver may return parsed objects – `decode_json` handles both) | `LONGTEXT` holding JSON |
| Booleans | `INTEGER` 0/1 | `BOOLEAN` | `TINYINT(1)` |
| Upsert | `ON CONFLICT … DO UPDATE` | same, `EXCLUDED.col` | `ON DUPLICATE KEY UPDATE col = VALUES(col)` |
| Reserved words | everything is quoted (`"end"`, `"key"`) | same | backticks |
| Strings in keys/indexes | unbounded `TEXT` | `TEXT` | `VARCHAR(32)` for ids, `VARCHAR(255)` for indexable text |
| Ordering | `ORDER BY created, id` is explicit everywhere – never rely on insertion order | | |
| DDL in transactions | yes | yes | **no** |

Not yet decided for a server mode: multi-instance deployments (token secret shared via config), moving attachments into
the database, and per-user row-level security (permissions are enforced in the service layer today).
