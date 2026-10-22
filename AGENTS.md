# Repository Guidelines

## Project structure
- `permita38/core/` – domain logic and storage: `store.py` (file-per-record repository layer), `calc.py`, `tax.py`,
  `holidays.py`, `pdf.py`, `documents.py`, pydantic `models.py`, `config.py`, and `service/` (business services).
- `permita38/api/` – FastAPI app (`server.py`), dependency injection (`deps.py`) and one router per domain in `routers/`.
- `permita38/cli/` – command line entry point (`serve`, `show-openapi`, `set-password`).
- `web/` – vanilla-JS single-page frontend, no build step. It only talks to the HTTP API.
- `api/openapi.json` – generated API contract. Regenerate with `make generate-openapi` after changing routers or models.
- `tests/` – pytest suite using FastAPI's `TestClient`; shared fixtures in `tests/conftest.py`.
- `docs/` – user documentation and the mobile concept.

## Commands
- `make test` – run pytest. `make lint` – ruff + mypy. `make format` / `make check-format` – ruff formatter.
- `make run` – start the server without opening a browser. `make check-openapi` – verify the contract is current.

## Conventions
- Routers stay thin; logic belongs in `core/service/`. Services raise `ServiceError`, never HTTP exceptions.
- Never write personal, customer or business data into the repo, tests or screenshots; use fake demo data.
- Keep the data format backwards compatible: models use `extra="allow"`, so unknown fields must survive a round-trip.
- Commit messages use Conventional Commit prefixes (`feat:`, `fix:`, `chore:`, `docs:`), imperative summaries.
- Licensed GPL-3.0-or-later; keep new files compatible.

## Security
- Secrets live in the per-device config, never in the synced data folder or the repo.
- Do not commit real data folders or `config.yaml`.
