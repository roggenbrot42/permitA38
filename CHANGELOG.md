# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- The planner timeline no longer has the per-project row of stacked bars (allocated / planned / gap); it was unreadable with more than three projects. The week table and the project filter are unchanged.
### Fixed
- **Planner utilisation counts the allocations.** The "Utilisation" of a week (and of each project) was committed hours
  over capacity and ignored the allocations, so an over-allocated person never showed more than 100 %. Utilisation is
  now the larger of committed and allocated hours per day and project. The week table has a new "Allocated" column
  (red above the capacity) and the API rows a new `allocated` field. Without allocations the numbers are unchanged.
  Weeks with allocations can show a higher utilisation and a `tight` or `over` status that they did not show before.
- Planner: hours in the contract rows and tooltips were shown with a decimal point (12.5 h) while the rest of the planner used the decimal comma; all planner numbers now use the app's number format (12,5 h).
- A draft offer or invoice can be deleted again: the document editor has a "Delete draft" button (numbered documents still cannot be deleted).
- An offer, invoice, contract or trip can no longer be saved with a project of another customer: the project pickers list only the chosen customer's projects (and projects without a customer), and the API refuses a mismatch with 422 (`Project belongs to another customer`). Scope: checked on offers, invoices, contracts and trips, and on the conversions (offer to invoice, accepting an offer that creates a contract, invoice from time). Not checked: incoming offers, allocations, contracts saved from a plan and bulk import. An offer that already has a project of another customer (legacy data) must be corrected first (edit the offer so customer and project match) before it can be accepted (when the offer has a plan) or converted; other edits of such a record are still allowed.

## [0.4.0-alpha.4] - 2026-10-26

Fourth alpha. Adds the planner per project (each project gets its own ring-fenced share of your capacity, with
allocations and an "Allocate my capacity" bootstrap), suppliers and subcontractors (supplier master data, incoming
offers you accept or unaccept, external allocations in the planner, forecast and team timeline, a commitments report,
and adoption of vendors from expenses). Adds migrations `0011-allocations` and `0012-suppliers`; an upgraded database
can no longer be opened by alpha.3. Back up your data folder before upgrading (the app also does it automatically).
Not for production bookkeeping yet; the tax rules marked *(verify)* in `docs/compliance.md` still need your tax adviser.

### Added
- **Planner per project**: the planner shows the capacity of each person split into ring-fenced shares per project,
  with allocations (planned hours per project and week), per-day over-allocation warnings, a project filter and a
  week detail in the forecast. "Allocate my capacity" bootstraps the allocations from your working time, with a
  preview before anything is saved. Hours are spread over the available days of each allocation.
- **Suppliers**: a supplier master data list, detail page and form, visible to the roles allowed by the rights table
  (`docs/roles.md`).
- **Incoming offers from subcontractors**: offers can be accepted and unaccepted again; accepting shows a warning
  when the first week has no demand. Offers follow a status workflow.
- **External allocations**: subcontractor time is allocated to projects like employee time and shows in the planner,
  the forecast (external capacity, gap, cost and a subcontractor toggle) and the team timeline. Allocations can be
  cancelled from the allocations dialog; planned, invoiced and open net figures are shown per allocation.
- **Commitments**: a block on the offers and a commitments report of what is committed to subcontractors; an expense
  that overruns its commitment shows a warning.
- **Vendor adoption**: expenses can be linked to a supplier, and suppliers can be suggested and adopted from existing
  expenses.

### Changed
- Team timeline and planner show subcontractor windows and cancelled subcontractor allocations up to the day before
  the cancellation.

### Fixed
- Business trips: finalizing a trip with open checklist lines now asks first and sends one request (no refused `PUT`
  with a `409` in the console); a new trip is saved as a draft before it is finalized.
- Business trips: the cost centre choice "Charge to the customer or project" is kept when you pick the project
  afterwards and is stored as soon as a customer or project is chosen.
- The import and numbering pages no longer fail with a script error for a user who is not the boss (the helper that
  shows the "only available to the boss" message was missing).
- Offers: saving keeps only the client fields, and accept, unaccept and status actions run in one transaction; a
  damaged offer line is refused instead of being stored.
- Vendor adoption keeps the chosen supplier and name, skips refused expenses and keeps the dialog focus and result.
- Accept and adoption dialogs close with Escape like their Close buttons.
- Planner: the simulate fields are sent only when used, so older servers keep working.

## [0.4.0-alpha.3] - 2026-10-25

Third alpha. Adds business trips (page, allowance, mileage, statement), the team timeline (page and API), personal
API tokens with an audit log (`docs/api-tokens.md`), the data export and import page (`docs/data-export.md`), and fixes
to the planner, the bill preview and request validation. Requires Python 3.12 or newer. Back up your data folder before
upgrading (the app also does it automatically); the schema changes with the migrations `0009-trips` and
`0010-api_tokens`, and an upgraded database can no longer be opened by alpha.2. Not for production bookkeeping yet; the
tax rules marked *(verify)* in `docs/compliance.md` still need your tax adviser.

Known limitations:
- Business trips: only the 2026 rate table exists, and in it every country except Germany is a **placeholder** (nine
  countries, not the BMF figures). A country that is not in the table is calculated with the Luxembourg row and the
  trip shows a note. A trip with a day in a year other than 2026 is refused with "No rate table for year ...". Do not
  use trips abroad for real statements yet.
- The export is a copy for moving or safekeeping, not a legally compliant archive (GoBD). Calendar links, secrets,
  API tokens and the audit log are not exported: reconnect the calendars after an import. An import needs an empty
  instance.

### Fixed
- Request bodies: every write route now validates its body with the strict models and hard limits; a note with an
  invalid character (timer note) no longer causes a server error (`500`).
- Documents: a customer or project rate that reprices a new draft no longer drops the quantities you already typed.
- Planner: a job with a total number of hours was spread over all workdays of its period, including vacation and
  holidays, so the week before a vacation looked overloaded although the work fitted before it. The hours are now
  spread over the available days (workdays that are neither a holiday nor time off); `docs/planning.md` has the rule.
- Bill preview: a PDF or image that the browser downloads or shows blank now has a visible "Not showing? Open or download the bill" line.
### Added
- **Team timeline page** (`#/timeline`): who is away and who has contracts, shown at the level the boss sets for the
  role; read-only. The API is described below.
- **Data export and import page** (Settings > Data export and import, boss only): download a full archive or customers and
  suppliers only, check an archive and import it into an empty instance with an explicit confirmation
  (`docs/contracts/data-export.md`).
- **API tokens in the web UI** (`#/tokens`, and a card on the Settings page for the boss): list with status and last use,
  create dialog that shows the token exactly once (copy button, "I have saved it" before closing), revoke, revoke all,
  activity log per token, boss view of all users. Needs the API token routes of `docs/contracts/api-tokens.md`.
- **API tokens (backend)**: signed-in users create personal tokens (`POST /api/tokens`; the secret is shown once and
  only its SHA-256 is stored) and use them as `Authorization: Bearer pat_...`. A token has the rights of its owner,
  optionally read-only, expires after 1-365 days (default 90), can be revoked at once, and at most 20 are active per
  user. Token routes: `GET /api/tokens`, `POST /api/tokens/{id}/revoke`, `POST /api/tokens/revoke-all`,
  `GET /api/tokens/{id}/audit`. A token can never manage tokens, passwords, two-factor authentication or users, and
  can not write settings. Every request made with a token is logged (method, route template, status; 365 days).
  Invalid tokens are throttled per client IP; `429` answers (login too) now carry `Retry-After`. Migration
  `0010-api_tokens`. See `docs/contracts/api-tokens.md`.
- **Team timeline API** (`GET /api/team/timeline`): who is away and when, public holidays and contracts for a window of
  at most 365 days. What a viewer gets depends on a level per role that the boss sets with
  `team_timeline_visibility` in the settings (`off`, `away`, `time-off`, `full`; defaults: employee `away`, accountant
  `off`). Hidden details are removed on the server. New permission area `team_timeline`. See
  `docs/contracts/team-timeline.md` and `docs/roles.md`.
- **Business trips** (page `#/trips`: trip dialog with a row per day and meal flags, checklist, link bills via the new
  *Trip* field in the bill dialog, finalise, trip statement; migration `0009-trips`). The backend computes trips with destinations per day, meal allowance (*Verpflegungsmehraufwand*) with
  reductions for provided meals, the three-month limit, mileage at 0,30 € per km, linked bills, a checklist, and a
  printable trip statement (`/api/trips`). Figures are computed by the server and frozen when a trip is finalised.
  Allowance and mileage appear in the finance report as deductible lines. Tax rules marked *(verify)* in
  `docs/compliance.md`. New rights area `trips`.

## [0.4.0-alpha.2] - 2026-10-23

Second alpha. Adds roles, sanity warnings, calendars, document numbering and import, itemised expenses with a bill
preview, the planner, and migration identifiers. Back up your data folder before upgrading (the app also does it
automatically); an alpha.1 database is upgraded in place and can no longer be opened by alpha.1. Not for production
bookkeeping yet; the tax rules marked *(verify)* in `docs/compliance.md` still need your tax adviser.

### Added
- **Document numbering with sequences**: per-type counters for invoices and offers, patterns with `{year}`, `{yy}`,
  `{month}`, `{n}` / `{n:05d}`, reset scope never / yearly / monthly, optional shared sequence, adjustable next
  number with a preview, safety warnings (backwards, gap, duplicate, unsortable) that need confirmation, and an audit
  log (`/api/numbering`). Numbers are allocated atomically in the finalize transaction. See `docs/numbering.md`.
- **Import of existing documents**: invoices, offers, customers and expenses from CSV, XLSX or permitA38 JSON, with
  detected columns (German and English), totals checked to the cent, customer matching, duplicate handling, original
  PDFs attached by number, dry run, all-or-nothing runs and undo (`permita38 import-documents`,
  `/api/import/*`). See `docs/import.md`.
- **Adoption guide** `docs/adopting.md`: what to change when you start using permitA38.
- **Roles and rights** (`docs/roles.md`): the roles `boss`, `employee` (formerly `member`, behaviour unchanged) and
  `accountant` (read access to customers, projects, time, contracts, offers, invoices, finance and settings; can
  enter expenses and upload bills). A per-area permission matrix (`permita38/core/rights.py`) is enforced on every
  API route; the web app hides what a role cannot use. The user record and `/api/bootstrap` carry the resulting
  `permissions`. Migration `0006-roles` renames `member` to `employee`; `member` is still accepted
  as input. `permitA38 create-user --role` accepts `employee` and `accountant`.
- **Sanity-check layer** (`docs/sanity-checks.md`): advisory plausibility warnings (`sanity_warnings`, never stored,
  never blocking) for invoice/offer/expense dates, totals, VAT, duplicates and outliers; USt-IdNr (with check digit),
  Steuernummer, IBAN and BIC format checks for customers and settings; working-time warnings (ArbZG breaks, 10 h,
  11 h rest, Sunday/holiday) on time entries; shown in the web app as a banner.
- **Calendars: meetings reduce your capacity.** Connect a personal calendar by its ICS link (Proton Calendar
  "Share via link", Nextcloud, Google, Outlook, Apple, Fastmail: `https://` or `webcal://`) or by uploading an `.ics`
  file. Read-only, refreshed every 6 hours by default, and the stored events are expanded recurrences (RRULE, EXDATE,
  RECURRENCE-ID) inside a bounded window. Busy time counts only inside each person's working hours and time zone,
  overlaps are merged, and a daily target caps it. No meeting titles are stored by default; the subscription link is
  a secret, is never shown again and never logged. Out-of-office entries are offered as suggestions, never created.
  Settings → Calendars includes a guided Proton Calendar card. Database migration `0007-calendars` adds the calendar tables and the per-user working time fields; the boss account
  manages its own calendars. Command: `permita38 calendar refresh`. See [docs/calendar.md](docs/calendar.md).
- `SafeFetcher.fetch_resource`: the SSRF-safe download for files (https only, conditional requests with ETag and
  Last-Modified, size and content-type limits).
- **Itemised expenses in the dialog**: tick *Itemise this bill* to book a bill with several lines (own amount, VAT rate
  and deductible share per line), with totals per VAT rate while you type. See `docs/getting-started.md`, section 8.
- **Bill preview** in the expense dialog: the chosen or stored bill is shown beside the lines; images zoom and pan
  (buttons, mouse wheel, keys, drag), PDFs use the browser's viewer.
- **Itemised expenses (API)** (`docs/specs/itemised-expenses.md`): an expense can carry up to 50 `lines`, each with its
  own gross amount, VAT rate and deductible share (and optional business-meal VAT share or category). VAT is computed
  per rate group as on a printed bill; the server derives net and VAT per line and the totals for the tax figures use
  the lines (largest-remainder split, so no negative VAT on tiny amounts). Bills without lines work as before. Migration `0008-expense_lines`.
- **Planning and vacation planner** (`docs/planning.md`): per-day capacity (weekly hours, holidays, absences, busy
  time from `services.busy`) against confirmed contracts and open offers in the worst case; offer feasibility with
  exact weeks, causes and options; numbers that do not add up on offers and invoices; project periods with manual
  and offer-based extensions; revenue basis (contracted, open offers, assumed); vacation entitlement; and the
  **Plan & vacation** page with drag-to-vacation, moving and resizing bars, keyboard editing, draft and apply,
  scenarios and comparison, and suggestions (vacation windows, earliest start, what must move).
  Migration `0003-planning`.
- **Migration identifiers instead of contiguous numbers** (`docs/specs/migration-ids.md`): migrations are
  `NNNN-slug.sql` files with a `-- migration: <id>` / `-- requires: <id>` header, ordered by the `requires` chain, so
  parallel branches no longer collide on "the next number". Applied ids are recorded in a new `schema_migrations`
  table; databases written by alpha.1 (integer `PRAGMA user_version` 1 to 3) are adopted automatically, and
  `user_version` keeps counting applied migrations so old binaries refuse upgraded databases. `permita38 migrate`
  prints ids and `migrate --status` lists the chain.

### Changed
- Numbered invoices can no longer be deleted (cancel them instead) so that the numbers stay gapless.
- Numbering, import columns and counters come with migration `0005-numbering`. Back up before upgrading; the app does
  this automatically.
- `/api/health`: `revision` and `current_revision` are migration ids (`revision` is `null` for an empty database);
  new field `applied`. `CURRENT_REVISION` is gone. Pre-migration backups are named
  `pre-migration-<timestamp>-before-<head id>.sqlite3`.
- Forecast revenue is based first on contracted hours, then open offers, and only then on assumed hours; every week
  states its basis. Contract hours are spread over the workdays of the assignee.

### Fixed
- Itemised expenses: line amounts must be whole cents
- Attachments: a file uploaded with a `.html` or `.svg` name was served from the app's own origin and could run as a
  page. Only PDF and image types are now served inline; all other files are downloads. Uploads whose content does not
  match a PDF or image extension are refused (415).
- Planner: after the first keyboard move the bar lost keyboard focus (the page redrew), so it could not be moved or
  resized again. Focus now survives redraws, Ctrl+←/→ resizes the start, and each draft change has its own Discard
  button (and Esc on a focused bar).
- Planner: bars could not be dragged at all (the pointer sat on the bar, so no day was found under it); a clicked bar
  was neither focused nor visibly selected (Firefox hides `:focus-visible` after a click); Alt+←/→ was taken by the
  browser's Back/Forward. Dragging now measures the distance, clicking selects and outlines the bar, and the keys are
  ←/→ (move), PageUp/PageDown (a week), Shift+←/→ (end), Ctrl+←/→ (start).
- Expenses: there was no "Business meals" category to choose. It exists now and proposes 70 % deductible (your own value is
  never overwritten); a category entered elsewhere stays selectable when editing.
- Expenses: the deductible share was also applied to the input VAT, so business meals (70 % deductible) under-claimed 30 % of
  the VAT. Input VAT of bills in the category *Business meals* is now reclaimed in full (§ 15 Abs. 1a UStG, to be
  confirmed by your tax adviser); other categories are unchanged. Bills you entered earlier keep their old figures: open
  and save them again after choosing the category. New column `expenses.vat_deductible` (migration `0004-expense_vat_deductible`).
- Money & Taxes: a year outside 2000–2100 in the address (typed or stale) showed a validation error page; it is clamped
  now and the arrows stop at the ends (found by the nightly randomized browser run).

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

[Unreleased]: https://github.com/roggenbrot42/permitA38/compare/v0.4.0-alpha.4...HEAD
[0.4.0-alpha.4]: https://github.com/roggenbrot42/permitA38/compare/v0.4.0-alpha.3...v0.4.0-alpha.4
[0.4.0-alpha.3]: https://github.com/roggenbrot42/permitA38/compare/v0.4.0-alpha.2...v0.4.0-alpha.3
[0.4.0-alpha.2]: https://github.com/roggenbrot42/permitA38/compare/v0.4.0-alpha.1...v0.4.0-alpha.2
[0.4.0-alpha.1]: https://github.com/roggenbrot42/permitA38/releases/tag/v0.4.0-alpha.1
[0.2.0]: https://github.com/roggenbrot42/permitA38/releases/tag/v0.2.0
