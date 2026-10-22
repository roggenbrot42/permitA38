# Randomized API tests

Tests that behave like many users poking at the API in unpredictable ways, and compare every answer with an
**independent reference model** (`oracle.py` re-derives the expected numbers from the documented rules; it does
not import any permitA38 code).

| File | What it does |
|---|---|
| `test_api_stateful.py` | A [hypothesis](https://hypothesis.readthedocs.io) state machine plays boss, members and strangers: logins, wrong passwords, brute force, token expiry, password changes, CRUD on every collection, timers, offers → accepted → contract, invoices from tracked time, finalizing and numbering, marking paid, PDFs, settings, user management. After **every** step it checks the response against the model and cross-checks the whole system (lists, `/dashboard`, `/finance`, which tokens are valid). |
| `test_api_fuzz.py` | Schema-driven fuzzing: requests are generated from the app's OpenAPI document and then damaged (wrong types, huge numbers, emoji, path traversal, malformed JSON, impossible dates …) and sent as boss, member, anonymous, forged token or cookie. Checks: never a 5xx or traceback, errors are JSON with `detail`, strangers get 401 and never change the database, members only write where they may, path traversal cannot reach files outside the data folder, no secret fields leak, and the database is still consistent (`PRAGMA integrity_check` is `ok`, no temp files, the app restarts on it and every list endpoint answers 200). Writes by a stranger or a member are detected by digesting every table before and after each request. |
| `test_known_bugs.py` | Regression tests for the real bugs these tests found. They state the *correct* behaviour; bugs that are still open are `xfail(strict=True)` and fail the run (XPASS) once fixed, which is your cue to remove the marker. |
| `oracle.py` | The independent oracle: Easter/holidays, rate resolution, invoice totals, weekly goal. |

## Running

```bash
pytest tests/backend/randomized -q                  # default "ci" profile: deterministic, about a minute
pytest -m randomized -q                     # same, selected by marker
HYPOTHESIS_PROFILE=thorough pytest tests/backend/randomized -q     # 10x more examples and steps, not deterministic
PERMITA38_NO_SHRINK=1 pytest tests/backend/randomized -x -q        # first failure fast, skip minimising (triage)
```

A plain `pytest` / `make test` skips this directory (it is the slow suite); it is collected when you pass its path,
use `-m randomized`, or set `PERMITA38_RANDOM=1`. `make test-random` does the right thing.

### Replaying a failure

* **Stateful test**: hypothesis prints the failing sequence of rules and a line
  `You can reproduce this test case by temporarily adding @reproduce_failure('…', b'…')`. Put that decorator on
  `TestApiStateful.runTest`'s test function, or simply re-run: failures are saved in `.hypothesis/` and replayed first.
* **Fuzz test**: the failing example is a single integer `seed=…`. Replay the whole session with
  `python -c "from tests.backend.randomized.test_api_fuzz import run_session; run_session(<seed>, 40)"`
  (from the repository root; set `PERMITA38_FUZZ_RAISE=1` to see the server-side traceback instead of a bare 500).
* To explore new territory use a different seed: `pytest tests/backend/randomized --hypothesis-seed=1234`, or
  `PERMITA38_FUZZ_SEED=1234` (switches on the `thorough` profile unless `HYPOTHESIS_PROFILE` is set).

## Known bugs

`test_known_bugs.py` holds regression tests for bugs these suites found. Fixed ones are ordinary tests; open ones
are `xfail(strict=True)` and turn red once fixed, which is the cue to remove the marker. The input-validation bugs
(dates, numbers, ranges, lone surrogates, damaged stored records) are fixed, so the fuzzer and the state machine
now feed them with hostile values like everything else.
