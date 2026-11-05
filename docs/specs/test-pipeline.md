# Test pipeline: less noise, same protection

Review tier: **Light** (CI/tooling and tests only, no product code).

## Findings (2026-11-05)

- `test_monkey_as_boss[7]` fails on its own coverage guard (`watch.writes >= 3`), not on a product bug. Same code, same
  seed, 3 local runs: 1 pass, 2 fail. Every random choice is taken from the elements visible *at that moment*, so render
  timing changes the walk; the dataset also follows `date.today()`. Patched twice before (PR #37, #61); no product bug
  found by the boss monkey since.
- The nightly has been red for 10 nights; nobody is told. Recurring classes: `'str' object has no attribute 'get'`
  (test bug, WP2), `403 == 409` and "29.3 hours on a day" in the stateful model (model drift, later WP), fresh-seed
  journey/monkey 8 s timeouts.
- Windows backend legs take 23-43 min (Linux 5-8) and gate every PR. The repo is public: minutes are free, wall time and
  triage are the cost.
- `tests/web/test_docscan_ui.py::open_check` races a re-render (`#ds_add` never visible, 5 s timeout), twice in CI.

## WP1 - CI and monkey (implementer)

1. `pyproject.toml`: register marker `monkey_walk: unbounded random UI walk, nightly only in CI (tests/web/e2e_random/test_monkey.py)`.
2. `tests/web/e2e_random/test_monkey.py`: mark **only** `test_monkey_as_boss` with `@pytest.mark.monkey_walk`.
   `test_monkey_as_member` (role leak check) stays in PR CI. Replace the hard `assert watch.writes >= 3` with
   `warnings.warn(...)` (same message plus the seed) when `writes < 3`; the run continues to the backend checks.
   Amendment: when env `PERMITA38_E2E_STRICT=1` the boss run additionally asserts `watch.writes >= 1` (message: "the
   monkey never managed to save anything - the test is not exercising the UI"); `writes < 3` still warns in all cases.
   `nightly-random.yaml` sets `PERMITA38_E2E_STRICT: "1"` in the env of the web-random-seeds pytest step.
3. `.github/workflows/ci.yaml`:
   - web job: add `-m "not monkey_walk"` to the pytest call.
   - test matrix: on `pull_request` exclude `windows-latest` with Python 3.12 and 3.14 (keep 3.13, it runs the OCR
     download step). Push to `main` keeps the full 2x3 matrix. Use an `exclude:` expression:
     `${{ github.event_name == 'pull_request' && fromJSON('[{"os":"windows-latest","python":"3.12"},{"os":"windows-latest","python":"3.14"}]') || fromJSON('[]') }}`
4. `.github/workflows/nightly-random.yaml`: the web job keeps running all of `tests/web` (monkey included). Add a final job
   `report` (`needs: [backend-thorough, web-random-seeds]`, `if: failure() && github.event_name == 'schedule'`,
   `permissions: issues: write`) that uses `gh` with `GH_TOKEN: ${{ github.token }}`: if an open issue titled exactly
   `Nightly randomized tests failing` exists, comment the run URL; otherwise create it with the run URL in the body.
   No third-party actions. Top-level permissions stay `contents: read`.
5. `AGENTS.md` ("Running tests economically"): CI line says Linux Python 3.12-3.14 + Windows 3.13 on PRs, full Windows
   matrix on `main`; the boss monkey runs only in the nightly and `make test-web`; a red nightly opens/updates the issue.
6. `tests/web/e2e_random/README.md`: same facts for the monkey row / seeds section.

## WP2 - fuzz sanitizer (implementer-small)

`tests/web/e2e_random/test_api_random.py` `ApiSim.sanitize`: unknown fields round-trip (`extra="allow"`), so a fuzzed
customer may carry `lines: "NaN"`. Check `lines` only when it is a list, and only its `dict` entries; treat a
non-numeric `qty`/`price` as not-a-number-to-check (skip). Replay: `PERMITA38_E2E_SEED=839954 pytest
tests/web/e2e_random/test_api_random.py -q` must pass (it fails today after 31 ops), and the default seeds still pass.

## WP3 - docscan race (implementer)

`tests/web/test_docscan_ui.py::open_check`: find out why `#ds_add` sometimes never appears after the row click (a
settle re-render replacing the row or closing the dialog). If the cause is the **test** clicking too early, wait for a
deterministic signal (e.g. the settled panel state) or retry the click (max 3 attempts, 2 s each, only while `#ds_add`
is absent). If the app closes an **open** dialog on re-render, do not change `web/`: report it as a product bug.
Prove it: run the file 15 times (`for i in $(seq 15); do pytest tests/web/test_docscan_ui.py -q -p no:cacheprovider; done`).

## Not done here

- Pinning the generator date: the UI shows the real current week, so a pinned dataset date would move the data out of
  view and change what the tests exercise; the dominant nondeterminism is timing, not the date. Revisit only with a
  server-side clock override.
- Stateful model drift (`403 == 409`, "29.3 hours") and fresh-seed browser timeouts: separate triage, start from the
  nightly issue.
