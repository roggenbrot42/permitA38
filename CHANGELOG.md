# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Sanity-check layer** (`docs/sanity-checks.md`): advisory plausibility warnings (`sanity_warnings`, never stored,
  never blocking) for invoice/offer/expense dates, totals, VAT, duplicates and outliers; USt-IdNr (with check digit),
  Steuernummer, IBAN and BIC format checks for customers and settings; working-time warnings (ArbZG breaks, 10 h,
  11 h rest, Sunday/holiday) on time entries; shown in the web app as a banner.
- **Planning and vacation planner** (`docs/planning.md`): per-day capacity (weekly hours, holidays, absences, busy
  time from `services.busy`) against confirmed contracts and open offers in the worst case; offer feasibility with
  exact weeks, causes and options; numbers that do not add up on offers and invoices; project periods with manual
  and offer-based extensions; revenue basis (contracted, open offers, assumed); vacation entitlement; and the
  **Plan & vacation** page with drag-to-vacation, moving and resizing bars, keyboard editing, draft and apply,
  scenarios and comparison, and suggestions (vacation windows, earliest start, what must move).
  Migration 0003 (renumbered to 0009 at merge time, see the migration header).

### Changed
- Forecast revenue is based first on contracted hours, then open offers, and only then on assumed hours; every week
  states its basis. Contract hours are spread over the workdays of the assignee.

## [0.4.0-alpha.1] - 2026-10-22

First public alpha. Expect rough edges and read [docs/release-plan.md](docs/release-plan.md) for the scope, known
limitations and what is planned for the next alphas. Not for production bookkeeping yet.

### Added
- **Optional TOTP two-factor authentication** (RFC 6238): enrolment with QR code, two-step login, replay protection,
  attempt throttling, ten hashed one-time recovery codes, boss reset (`POST /api/users/{id}/totp/reset`) and
  `permita38 reset-totp`. Database migration 0002 adds the columns; new dependency `segno`.
- **Customer prefill from the Impressum of a company website** (`POST /api/customers/lookup-impressum`, boss only):
  deterministic parser (schema.org JSON-LD/microdata, German/English legal-notice wording, obfuscated e-mail
  addresses, ISO-8859-1/UTF-8) with validators (USt-IdNr check digit, ZIP, e-mail, phone), per-field confidence and
  evidence, and a review UI in the customer form that never overwrites existing input. Read-only: nothing is saved.
  Switch: `features.impressum_lookup` (`PERMITA38_FEATURE_IMPRESSUM_LOOKUP`).
- **SSRF-safe fetching** (`permita38/core/netsafe.py`): http(s) on ports 80/443 only, no credentials, public
  addresses only (IPv4/IPv6, metadata, CGNAT, NAT64/6to4/Teredo, ULA) re-checked on every redirect with the
  connection pinned to the checked address, max 3 redirects, request budget, timeouts incl. an overall deadline,
  2 MB cap (also for gzip), content-type allow-list, per-user rate limit.
- **Input validation (first version of the sanity layer):** strict request models reject impossible dates
  (`2026-13-45`, outside 2000-2100, end before start), times, minutes outside 1-1440, more than 24 h per person and
  day, non-finite or unrealistic amounts and rates, negative expenses, unknown VAT rates, unknown status/kind/layout
  values, bad colours/ids, over-long text and oversized documents. Errors are HTTP 422 naming the field and never
  echo the input. See `docs/validation.md`.
- **Docker support:** multi-stage `Dockerfile` (non-root, health check, pinned dependencies), hardened
  `docker-compose.yml`, HTTPS reverse-proxy examples (Caddy, nginx) and `docs/docker.md`.
- **Server configuration through the environment** (`PERMITA38_*`: host, port, secret key / key file, CORS, token
  lifetime, proxy trust, cookie `Secure` flag, log level) and a container mode that keeps the generated secret in the
  data volume. First-run bootstrap of the boss account from `PERMITA38_BOOTSTRAP_*` (closes the public setup page) and
  `permita38 create-user`. The auth cookie gets the `Secure` flag on https / behind a trusted proxy.
- **Release engineering:** tag-triggered `Release` workflow (sdist/wheel, PyInstaller bundles for Windows and Linux,
  multi-arch image, CycloneDX SBOM, SHA256SUMS, build provenance attestations, draft pre-release),
  `scripts/release.py` (version/CHANGELOG/tag checks, always-annotated tags), `make release`, `RELEASING.md`,
  `docs/release-plan.md`, `docs/roadmap.md`, `docs/maintainer-setup.md`, `THIRD_PARTY_NOTICES.md` with a GPLv3 license
  gate, CodeQL, Dependabot, issue/PR templates and a quarterly tax-rule review reminder.
- Test suites per subsystem: `tests/backend` (incl. repository contract tests and randomized API tests),
  `tests/web` (Playwright and seeded random user simulations), `tests/release`, and a plan for `tests/android`.

### Changed
- The package version is single-sourced from `permita38.__version__` (PEP 440, e.g. `0.4.0a1` for tag `v0.4.0-alpha.1`).
- `docker.yaml` only validates pull requests; publishing the image moved into the Release workflow (no `latest` tag for
  pre-releases).
- `PERMITA38_DATA` now ranks command line > environment > YAML (it used to be below the YAML file), and environment
  values are no longer written into the YAML file.
- `/api/auth/status` no longer reveals the data folder path; it is part of the boss-only `/api/bootstrap` response.
- Session tokens now carry a session version, so enabling/disabling 2FA and password changes end older sessions
  immediately.

### Fixed
- **Member permissions (security):** members could read the full record (rate, budget, notes) of projects they are not
  assigned to, see every task incl. task rates, and set `rate` / `invoice_id` on their own time entries (overriding
  the billing rate or hiding hours from invoices). Members now only see their projects and those projects' tasks
  without rates, and billing fields on entries are boss/system-only.
- **VAT and money rounding:** amounts were computed with binary floats, so 19 % of 621.50 was booked as 118.08 while
  the editor showed 118,09. All money is now computed with exact decimals (`permita38/core/money.py`): line amounts
  and VAT per rate are rounded half-up to whole cents, and the web editor (`web/money.js`) uses the identical rule
  (checked against the backend by a cross-language test).
- **One damaged record could break everybody:** an invalid date or number in a stored record made `/forecast`,
  `/dashboard` or `/finance` answer HTTP 500 (and, through file sync, on every device). Such records are now skipped
  with a logged warning, and invalid values are rejected when they are entered.
- A lone Unicode surrogate, absurdly large numbers and out-of-range `year` parameters caused HTTP 500 instead of 422.
- The offer/invoice editor lost new documents on every re-render, "Paid" did not record a paid date, unknown document
  ids crashed the page, negative values broke the Money chart and members opening boss-only pages saw an error card.

### Known limitations
- The wheel on PyPI-style installs contains only the API; the web UI ships in the desktop bundles and the Docker image.
- The Windows bundle is unsigned (SmartScreen warns). macOS builds are not provided yet.
- Roles/rights, accounting and the VAT return, legal-retention backups, soft-warning checks, per-IP throttling and
  server-side session revocation are not implemented yet; see the roadmap.

## [0.3.0]

### Changed
- **Data is stored in SQLite** (`<data folder>/permita38.sqlite3`) instead of one JSON file per record. Real tables and
  typed columns; unknown fields are kept in a per-row `extra` JSON column. Attachments stay as files.
- New **storage layer**: services, calculations and the API depend on a backend-neutral `Repository` interface
  (`permita38/core/repository/`). The schema is described once (`schema.py`); SQL for SQLite, PostgreSQL and MariaDB is
  generated by `Dialect` classes. Only SQLite is implemented – the other dialects and their migration files are prepared
  and unit-tested as strings, with a backend-independent contract test suite. See `docs/porting-to-postgres.md`.
- Multi-step operations (invoice from time, deleting an invoice, accepting an offer) are now transactional.
- Tests moved to `tests/backend/`.

### Added
- **Versioned migrations** per SQL dialect (`permita38/core/schema/migrations/<dialect>/`), schema revision stored in the
  database, automatic backup before migrating, and a startup check that refuses a database newer than the app.
- CLI: `permita38 migrate`, `backup [--dest FILE]`, `import-json <dir>`, `create-migration <name>`, `dump-ddl`.
- Automatic daily database backup (`database.auto_backup`, newest `backup_keep` kept) and conflict detection for synced
  database copies (Syncthing, Nextcloud, Dropbox).
- `database:` section in the per-device config (`backend`, `path`, `sqlite_wal`, `auto_migrate`, `auto_backup`).
- Importer for the earlier JSON-file layout.

### Removed
- `permita38/core/store.py` (replaced by the repository package).

## [0.2.0]

### Changed
- Renamed the project from *Timely* to **permitA38** (package, CLI and config are `permita38`).
- New architecture inspired by stustapay: a FastAPI backend (routers → services → storage) and a separate
  vanilla-JS frontend in `web/`, connected by an OpenAPI contract (`api/openapi.json`, interactive docs at `/api/docs`).
- Per-device YAML configuration (`~/.config/permita38/config.yaml`) replaces the old remembered-path file.
- Data folder defaults to `~/permitA38`; environment variable is `PERMITA38_DATA`.
- Endpoints are RESTful (`POST` create, `PUT` update, `DELETE` returns 204) and errors are `{"detail": ...}`.

### Added
- Login screen with username and password, signed bearer tokens, login throttling and password change.
- `permita38 set-password` recovery command and `permita38 show-openapi`.
- Optional separate hosting of the frontend (CORS via `cors_origins`, `serve_web: false`).
- pytest suite (TestClient), ruff/mypy tooling, pre-commit and GitHub Actions CI.
- GPL-3.0-or-later license.

### Removed
- PIN-based user picker (replaced by real accounts).

## [0.1.0]
- Initial version as *Timely*: time tracking, weekly goal, forecast, offers, invoices, expenses, tax estimates.

[Unreleased]: https://github.com/roggenbrot42/permitA38/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/roggenbrot42/permitA38/releases/tag/v0.2.0
