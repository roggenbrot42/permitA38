# Fast backend tests on Windows CI

Review tier: **Light** (test and CI tooling only; production code unchanged).

## Problem

The Windows backend CI job took 42–46 min, against 2.5–4 min on Ubuntu. Running it on 4 pytest-xdist workers did not
help (44m38s). Measured on PR #210, run 46087071590, Windows, 4 workers:

| variant | backend step |
|---|---|
| baseline (`synchronous = FULL`) | 45m41s |
| Defender real-time scanning off | 37m34s |
| SQLite `synchronous = OFF` + `journal_mode = MEMORY` (test-only patch) | 4m20s |

Each test creates a fresh database and runs every migration in its own transaction. With `synchronous = FULL`, each
commit forces several disk flushes, and the flushes to the runner's single disk queue up behind each other across workers.

## Decision

1. **Tests run SQLite without fsync.** `tests/backend/conftest.py` has an autouse fixture `_fast_sqlite`. It
   monkeypatches `permita38.core.repository.sqlite.SQLiteRepository._open`: call the original, then
   `self._conn.execute("PRAGMA synchronous = OFF")` when `self._conn` is not None. The journal mode stays as production
   sets it (DELETE, or WAL when `wal=True`), so the tests keep the production journal behaviour. Production code is not
   changed.
2. **Opt-out marker `durable_sqlite`**, registered in `pyproject.toml` `[tool.pytest.ini_options].markers`. The fixture
   does not patch a test that carries the marker (`request.node.get_closest_marker("durable_sqlite")`).
   In `tests/backend/test_store_sqlite.py` the tests that assert the production pragmas
   (`test_pragmas_are_sync_friendly`, `test_wal_is_opt_in_and_checkpointed_on_close`) carry the marker. The unmarked
   `test_backend_tests_run_without_fsync` checks that the fixture applies.
3. **Timing guard uses CPU time.** `tests/backend/test_planner_suggest.py::test_suggestions_are_fast_on_a_realistic_world`
   measures with `time.process_time()` instead of `time.perf_counter()`. The bound stays at 3.0 s. Wall-clock time on a
   shared runner with 4 workers measured 3.26 s.
4. **CI:** the backend step keeps `-n 4 --dist worksteal`. The temporary `win-exp` job is removed, and so is the
   temporary `PERMITA38_TEST_NOSYNC` block at the end of `tests/backend/conftest.py`.
5. **AGENTS.md**, "Running tests economically": one sentence saying that the backend tests run SQLite with
   `synchronous = OFF` (opt out with `@pytest.mark.durable_sqlite`) and that CI runs them on 4 workers.
