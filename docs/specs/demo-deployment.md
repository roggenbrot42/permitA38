# Specification: deployable customer demo

Status: **proposed, not scheduled** (decided after the 0.4.0 feature freeze; implement in the release after it).
Author: architect (Sonnet 5.5). The implementer follows this document and lists every choice it had to make under
"Open design decisions" in its report.

## Goal

A public, safe, try-it-yourself instance of permitA38 with fake data that customers can open in the browser, click
through every main page and break without consequences. It is based on the local preview (`scripts/preview.sh`,
`scripts/seed_demo.py`, `docs/preview.md`), which exists already and stays for local use.

Non-goals: a multi-tenant product, real accounts, persistence beyond the demo lifetime, any real customer data.

## Threat model

The demo is reachable by anyone. Assume visitors will: change or delete everything, try to take over the shared
account, upload hostile files, point URL fetchers at internal addresses, script the API, and fill the disk.
A demo must therefore never be a way to reach the host, its network or another visitor's data.

## Design

### Demo mode switch

One switch, `PERMITA38_DEMO=1` (YAML key `demo: true`, off by default, never on unless set). When on:

| Area | Behaviour |
|---|---|
| Data | Seeded from `seed_demo.py` fake data, see "Sessions". |
| Login | The login page offers a **"Open the demo"** button; no credentials are shown or typed. |
| Account changes | Password change, 2FA enrolment/reset, user creation/deletion and role changes answer `403 demo_disabled`. |
| Outbound fetching | `impressum_lookup` and calendar URL subscription answer `403 demo_disabled` (calendar **file upload** stays on, it makes no network request). |
| Uploads | Attachments and imports are limited to 1 MB per file, 20 files per session, allow-listed types only (the same allow-list the attachment serving uses); everything else is `415`. |
| PDFs/exports | allowed; they stay inside the session. |
| Banner | A permanent banner "Demo – data is deleted regularly, do not enter real data" and a **"Reset demo"** button. |
| Backups, auto backup, update checks | off. |
| Secrets | A fresh random secret key per process; nothing is written outside the demo data directory. |

`demo_disabled` is a normal `ServiceError` mapped to 403 with a message the UI shows as "Not available in the demo".
The check lives in one place (a small `core/service/demo.py` with `ensure_not_demo(feature)`); services call it, routers
stay thin. The list of disabled features is a constant in that module, so the tests can iterate over it.

### Sessions (decision: one sandbox per visitor)

Each visitor gets **their own data folder** (copy of the seeded template), found through a signed cookie
`demo_session` (random 128-bit id, `HttpOnly`, `SameSite=Lax`, `Secure` behind HTTPS).

- `POST /api/demo/session` creates a sandbox: copies the seeded SQLite template (a single file, so a copy takes
  milliseconds), registers it with a last-used time, and returns the boss login for that sandbox as a normal session
  token. There is no shared password; the token is created server-side.
- Every API request resolves the repository from the session cookie. Without a valid sandbox the API answers `401` and
  the UI shows "Open the demo" again. The repository dependency in `api/deps.py` is the only place that changes.
- Limits: at most `PERMITA38_DEMO_MAX_SESSIONS` (default 50) live sandboxes, idle lifetime 60 minutes, absolute
  lifetime 4 hours, whichever comes first; a janitor task removes expired sandboxes (files included) every minute.
  When the limit is reached `POST /api/demo/session` answers `503` with "The demo is busy, try again in a few minutes".
- Creating sandboxes is rate limited per IP (default 10 per hour). The proxy header is used only when the existing
  `trust_proxy` setting is on.
- "Reset demo" deletes the sandbox and creates a new one from the template.
- Per-sandbox disk quota: the database file plus attachments may not exceed 50 MB; writes beyond that answer `413`.

The fixed `demo`/`demo` login of the local preview is **not** used for the public demo and must never be reachable when
`PERMITA38_DEMO` is on and the server is not bound to loopback (the server refuses to start in that combination with
the weak password).

### Seed data

- `scripts/seed_demo.py` stays the single source. The public demo builds the template once at start (or at image
  build) from it. All names, companies, addresses, IBANs, tax numbers, e-mail addresses are visibly fake (example.com
  domains, `DE00 0000 …`, tax numbers failing validation are not allowed: use well-known sample values the validators
  accept). A test scans the seeded database for any e-mail not ending in `.example`/`example.com`/`example.org`.
- Dates are relative to the start day, so the demo never looks stale (planner, forecast, invoices overdue/not due).
- The template covers every main page: time entries, a running timer, a project over budget, an overdue invoice, a
  draft invoice, an accepted and a sent offer (with a binding plan), expenses with and without VAT, vacation, and the
  roles (boss shown by default; the other roles are reachable through "Switch demo role" which only exists in demo
  mode and re-issues a token for that sandbox's employee/accountant user).

### Hosting recipe

- Docker image from the existing `Dockerfile`; one container, `PERMITA38_DEMO=1`, a volume (or tmpfs) for sandboxes,
  read-only root filesystem, no capabilities, memory/CPU limits in compose. Caddy or nginx terminates HTTPS
  (examples in `docs/docker.md`) with its own rate limit and a request-size limit.
- `docs/demo.md` documents it: how to deploy, how to refresh the template after an upgrade, what is disabled and
  why, and that the maintainer decides the hostname and any custom domain.
- The demo never talks to the outside world (no telemetry, no update check, no fonts or scripts from CDNs).

### Security requirements

1. Sandbox ids are unguessable, never appear in URLs or logs, and a request can never address another sandbox's
   files (path joins only through the registry; a test requests `../` and foreign ids).
2. All paths of one sandbox are inside its own directory; the janitor deletes only paths below the demo root.
3. Demo mode cannot be enabled by a request, only by the process environment/config.
4. With demo mode off, none of the demo endpoints exist (`404`), and the normal login is unchanged.
5. The existing CSP, attachment allow-list and SSRF protections stay on; the demo only turns more things off.

## API and contract changes

- `POST /api/demo/session` (only in demo mode), `POST /api/demo/reset`, `GET /api/demo/status` (enabled flag,
  disabled features, limits, seconds until expiry for the banner), `POST /api/demo/role` (switch demo role).
- `/api/auth/status` gains `demo: bool` so the login page can show the button.
- Regenerate `api/openapi.json`; the strict `*In` models and response models follow the AGENTS.md rules.

## Tests

Backend: demo off by default and endpoints 404; each disabled feature answers 403 (iterate the constant); sandbox
isolation (two sessions never see each other's data; deleting in one leaves the other); expiry and janitor (with a
fake clock); the session limit (503) and the per-IP limit; disk quota (413); path traversal; no weak password when
bound to a non-loopback address; seed scan for real-looking addresses; reset gives a fresh copy. Web: open the demo,
click through the main pages without console errors, reset, expiry message, role switch. Reuse the seeded random
monkey with the demo template.

## Out of scope (possible later)

Per-visitor containers (stronger isolation, much more infrastructure), public sign-up for trials, analytics.

## Open decisions for the maintainer

- Should the role switcher exist, or is a boss-only demo enough?
- Idle/absolute lifetimes and the sandbox limit above are proposals.
- Hostname, hosting provider and who pays for it are not part of this repository.
