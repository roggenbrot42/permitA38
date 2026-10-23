# Repository Guidelines

## Project structure
- `permita38/core/` – domain logic: `calc.py`, `tax.py`, `holidays.py`, `pdf.py`, `documents.py`, pydantic `models.py`,
  `config.py`, `database.py` (schema revision + migration files) and `service/` (business services).
- `permita38/core/repository/` – the storage layer. `base.py` is the `Repository` interface; `schema.py` describes the
  tables once (dialect-neutral); `dialect.py` generates SQL per dialect; `sql.py` is the generic SQL implementation;
  `sqlite.py` is the default backend. `permita38/core/schema/migrations/<dialect>/` holds the numbered migrations.
- `permita38/api/` – FastAPI app (`server.py`), dependency injection (`deps.py`) and one router per domain in `routers/`.
- `permita38/cli/` – command line entry point (`serve`, `show-openapi`, `set-password`).
- `web/` – vanilla-JS single-page frontend, no build step. It only talks to the HTTP API.
- `api/openapi.json` – generated API contract. Regenerate with `make generate-openapi` after changing routers or models.
- `tests/` is organised per subsystem:
  - `tests/backend/` – Python backend tests (unit, API via `TestClient`, repository contract, migrations); fixtures in
    `tests/backend/conftest.py`. `tests/backend/randomized/` holds the hypothesis state machine and the schema fuzzer
    (only collected when asked for, see below).
  - `tests/web/` – browser UI tests with Playwright plus seeded random user simulations (`tests/web/e2e_random/`).
  - `tests/android/` – placeholder and test plan for the future Android app.
- `docs/` – user documentation and the mobile concept.

## Commands
- `make test` – fast backend suite (`tests/backend`, excludes the randomized suite). `make test-random` – randomized API
  tests (`HYPOTHESIS_PROFILE=thorough` for a long run). `make test-web` – browser/e2e tests (needs Chromium: set
  `PERMITA38_CHROMIUM` or run `python -m playwright install chromium`). `make test-all` – everything. `make lint` – ruff + mypy. `make format` / `make check-format` – ruff formatter.
- `make run` – start the server without opening a browser. `make check-openapi` – verify the contract is current.

## Running tests economically
- While iterating run only the affected files (`pytest tests/backend/test_x.py -q`). Run the browser and randomized
  suites **once, at the end**, with `make test-summary` (one line per suite, full logs in `test-artifacts/`, a lock
  makes concurrent runs wait). Read `test-artifacts/<suite>.log` only for a failing suite.
- Randomized failures print the replay command and the last 12 steps; the whole step log is in
  `test-artifacts/e2e-<kind>-seed<N>.log`. Replay with `PERMITA38_E2E_SEED=<N>`; seeds are fixed by default.

## Conventions
- Routers stay thin; logic belongs in `core/service/`. Services raise `ServiceError`, never HTTP exceptions.
- Request bodies are validated by the strict `*In` models (`core/models.py`, primitives in `core/validation.py`);
  the lenient models describe responses. Add every new field to both, with a hard bound. Money is computed only with
  `core/money.py` (exact decimals, half-up cents) – never with floats. See `docs/validation.md`.
- Never write personal, customer or business data into the repo, tests or screenshots; use fake demo data.
- Services, calc, documents and the API depend only on the `Repository` interface. No SQL and no `sqlite3` outside
  `permita38/core/repository/`; run new storage behaviour through `tests/backend/test_repository_contract.py`.
- Keep the data format backwards compatible: models use `extra="allow"`, so unknown fields must survive a round-trip
  (the repository keeps them in the row's `extra` JSON column).

## Database migrations
- The schema lives in `core/repository/schema.py`. To change it: run `permita38 create-migration <name>` (creates the next
  numbered file in **every** dialect folder under `core/schema/migrations/`), write the SQL for each dialect, update
  `schema.py` and the pydantic model, and **bump `CURRENT_REVISION` in `core/database.py`**. Tests fail if the dialect
  folders drift apart, if `CURRENT_REVISION` is not the highest number, or if a migrated SQLite database differs from
  `schema.py`. Migration SQL must not contain `BEGIN`/`COMMIT` (the runner wraps each file in a transaction and
  records the revision). Never edit a migration that has been released – add a new one.
- Commit messages use Conventional Commit prefixes (`feat:`, `fix:`, `chore:`, `docs:`), imperative summaries.
- Licensed GPL-3.0-or-later; keep new files compatible.

## Division of labour between models
- **Architecture, planning and specifications** (data models, module interfaces and contracts, migration numbering,
  security trade-offs, task breakdown) are decided by the stronger model (Sonnet 5.5) and written down in the task
  specification before implementation starts.
- **Implementation** (code, tests, docs) may be done by a smaller model. It executes the specification and does **not**
  make architectural decisions: on ambiguity it picks the simplest option consistent with the spec and lists it under
  "Open design decisions" in its report, so the reviewer can confirm or change it before the merge.
- **Review** (code, tests, logic) is done by the stronger model, see "Merge gate".

## Merge gate (before anything lands on `main`)
- Implementation may be done with smaller/cheaper models. The review effort scales with the risk of the branch:
  - **Light** (cosmetic UI, docs, tooling, small bug fixes that touch no money, rights, storage or outside input):
    one Haiku pass over the diff plus the targeted tests.
  - **Standard** (schema/migrations, advisory checks, other non-security logic): one Sonnet 5.5 pass covering code and
    tests together; a smaller model may check the test list against the specification.
  - **Full** (money/VAT/numbering, rights, uploads and served files, parsing of outside input, network access): three
    Sonnet 5.5 passes. (1) **code review** of the diff (correctness, security, edge cases), (2) **test review** (do the
    tests prove the intent, are assertions meaningful, are cases missing, would they fail if the code were wrong),
    (3) **logic review** of the domain rules (recompute key numbers by hand or with an independent oracle: capacity,
    VAT/rounding, numbering, calendar hours, permissions). The logic pass is never done by a smaller model.
  - The maintainer or architect assigns the tier in the task specification; when in doubt take the higher one.
  - Findings go back to the branch and are fixed first. Only then: merge, regenerate `api/openapi.json`, run
    `make test lint`, push.
- **Before every release** run the whole suite once on `main` (`make test-summary` with backend, browser, randomized and
  release suites) and click through the main path by hand. Branches only run their affected tests.
- Releases are always tagged (annotated) from `main`, see `RELEASING.md`.

## Security
- Secrets live in the per-device config, never in the synced data folder or the repo.
- Do not commit real data folders or `config.yaml`.
