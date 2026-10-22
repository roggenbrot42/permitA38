# Roadmap

> **MAINTAINER TODO: Review and finalise the release assignment of each roadmap item (reminder requested by the
> maintainer).** The assignment below is a proposal. Items marked *(confirm intended meaning)* were dictated and are
> ambiguous. Remove this notice when the assignment is final.

Release trains and gates are described in [`release-plan.md`](release-plan.md). Status legend: **done** = merged,
**planned** = assigned to that release, **research** = needs investigation before it can be scheduled.

## v0.4.0-alpha.1 (first alpha)

| Item | Status |
|---|---|
| Time tracking, planning/forecast, customers/projects, offers/invoices + PDF, expenses, simple tax estimates | done |
| SQLite storage, migrations, backup command | done |
| Username/password login and **TOTP two-factor authentication** | done |
| Docker image and compose files | done |
| Release engineering: signed annotated tags, reproducible bundles (Windows, Linux), SBOM, attestations | done |
| Customer data extraction from a company website's *Impressum* (legal notice): fills name, address, VAT id, contact | planned for alpha.1 if finished, otherwise alpha.2 (feature is built separately) |

## v0.4.0-alpha.2

| Item | Status |
|---|---|
| **Roles and rights** (boss / employee / accountant, per-area permissions) | planned |
| **Validation and sanity-check layer** (plausibility checks for invoices, dates, tax ids, totals) | planned |
| Impressum extraction (if not in alpha.1) | planned |
| Full data export as JSON and CSV, re-importable by the app (round-trip tested) | planned |
| Quarterly **tax-rule review process** (issue reminder, checklist, rule tables with source and valid-from date) | planned (reminder workflow already in place) |

## v0.4.0-alpha.3

| Item | Status |
|---|---|
| **Accounting** (chart of accounts SKR03/SKR04, journal, receipts) and the **German VAT return (Umsatzsteuer-Voranmeldung)** | planned |
| XLSX export; **DATEV export** (Buchungsstapel) | planned |
| **E-invoice export**: XRechnung / ZUGFeRD (EN 16931); import of e-invoice XML (parse and pre-fill) | planned |
| Scoped **API tokens** (read-only / per area) as the base for integrations | planned |

## v0.4.0-alpha.4

| Item | Status |
|---|---|
| **Legal-retention backup module** (scheduled, verified, encrypted backups with retention periods) | planned |
| **GoBD compliance** (immutability of booked/finalised documents, change log, *Verfahrensdokumentation* template, retention rules) *(confirm intended meaning: GoBD)* | planned |
| **GDPR / DSGVO compliance** (record of processing activities, data export per person, erasure/anonymisation with respect to legal retention, privacy notice) *(confirm intended meaning: dictated as "grape compliance")* | planned |
| **GDPdU / Z3 audit export** for tax audits (Betriebsprüfung) | planned |
| **Per-IP login throttling** (on top of the existing per-account throttling) | planned |
| **Server-side session revocation** (token ids with a denylist, or a session table; "log out everywhere") | planned |

## v0.5.0-beta (feature complete for 1.0)

| Item | Status |
|---|---|
| AI-assisted input, all optional and local-first: **OCR / LLM invoice scan pre-fill**, **bank statement matching**, a **proposal queue** (suggestions are never booked without human approval) | planned |
| **Local / OpenAI-compatible model provider** (Ollama, llama.cpp server, any OpenAI-compatible endpoint; no data leaves the machine unless configured) | planned |
| **MCP server** exposing scoped, audited tools to AI assistants (built on the scoped API tokens) | planned |
| German UI translation and the translation framework | planned |
| Windows code signing, PyPI publication, macOS bundle decision | planned |
| Coverage 90 %, upgrade tests from every alpha | planned |

## v1.0

| Item | Status |
|---|---|
| Stability promise: API, config and database migration chain supported within 1.x | planned |
| Complete documentation, security review of all new areas, reproducible builds verified by a second person | planned |

## Later (not scheduled)

| Item | Notes |
|---|---|
| **Android companion app** | offline-first on SQLite (on-device database) syncing the shared database file, or talking to the server API; see [`mobile-concept.md`](mobile-concept.md) |
| **Optional server mode** with **PostgreSQL / MariaDB** backends | behind the existing `Repository` interface, see [`porting-to-postgres.md`](porting-to-postgres.md) |
| **Row-level sync / merge** | removes the "one writing device at a time" rule of the synced-file setup |
| **SSO / OIDC** login | for team and server installations |
| **ELSTER transmission** | research: certificate handling, ERiC library licensing, or an export for the official software |
| Recurring invoices, credit notes, reminders (Mahnungen) | |
| macOS and Linux arm64 bundles | needs signing and notarisation |

## Recurring: quarterly tax-rule review

On the 2nd of January, April, July and October a reminder issue is opened (`tax-review-reminder.yaml`). The checklist:
income-tax tariff and allowances, solidarity surcharge, VAT rates, small-business (*Kleinunternehmer*) limits,
UStVA boxes and filing deadlines, trade tax (*Gewerbesteuer*), SKR/DATEV/GoBD changes and new laws. Each rule in
`permita38/core/tax.py` carries its source and valid-from date.
