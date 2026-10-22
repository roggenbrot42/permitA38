# Roadmap

> **MAINTAINER TODO: Review and finalise the release assignment of each roadmap item (reminder requested by the
> maintainer).** The assignment below is a proposal. Remove this notice when the assignment is final.

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
| Customer data extraction from a company website's *Impressum* (legal notice): fills name, address, VAT id, contact | done |

## v0.4.0-alpha.2

| Item | Status |
|---|---|
| **Roles and rights** (boss / employee / accountant, per-area permissions) | in progress |
| **Validation and sanity-check layer** (plausibility checks for invoices, dates, tax ids, totals) | in progress |
| Impressum extraction (if not in alpha.1) | planned |
| Full data export as JSON and CSV, re-importable by the app (round-trip tested) | in progress |
| Quarterly **tax-rule review process** (issue reminder, checklist, rule tables with source and valid-from date) | planned (reminder workflow already in place) |

### Maintainer priorities (added after the first alpha, highest first)

| Item | Status |
|---|---|
| **Interactive vacation and capacity planner** with what-if scenarios: drag vacation and contracts around, see over-capacity weeks instantly, apply or discard | in progress |
| **Binding offers in capacity planning**: every open offer counts (worst case "all accepted"); warnings when an offer does not fit into the free hours of its time frame | in progress |
| **Revenue forecast basis**: contractually owed hours first, intended weekly hours only where nothing is contracted; each row shows its basis | in progress |
| **Projects have a start and an end**; extended manually or by an offer, with history | in progress |
| **"Numbers don't add up" warnings** for offers and billing (hours vs. offered hours, price vs. rate, billed vs. offered budget) | in progress |
| **Calendar connection** (Proton "Share via link" ICS URL, ICS file; works with any ICS source): meetings reduce capacity | in progress |
| **Adjustable document numbering** (pattern, start number, yearly reset, preview) and an adopter checklist | in progress |
| **Import of existing documents** (invoices/offers/customers/expenses from CSV/XLSX/JSON, originals attached, undo) | in progress |

## v0.4.0-alpha.3

| Item | Status |
|---|---|
| **Accounting** (chart of accounts SKR03/SKR04, journal, receipts) and the **German VAT return (Umsatzsteuer-Voranmeldung)** | in progress |
| XLSX export; **DATEV export** (Buchungsstapel) | planned |
| **E-invoice export**: XRechnung / ZUGFeRD (EN 16931); import of e-invoice XML (parse and pre-fill) | planned |
| Scoped **API tokens** (read-only / per area) as the base for integrations | planned |

## v0.4.0-alpha.4

| Item | Status |
|---|---|
| **Legal-retention backup module** (scheduled, verified, encrypted backups with retention periods) | in progress |
| **GoBD compliance** (immutability of booked/finalised documents, change log, *Verfahrensdokumentation* template, retention rules) | planned |
| **GDPR / DSGVO compliance** (record of processing activities, data export per person, erasure/anonymisation with respect to legal retention, privacy notice) | planned |
| **GDPdU / Z3 audit export** for tax audits (Betriebsprüfung) | planned |
| **Per-IP login throttling** (on top of the existing per-account throttling) | in progress |
| **Server-side session revocation** (token ids with a denylist, or a session table; "log out everywhere") | in progress |

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

## Compliance and regulatory radar (proposed, added after the first alpha)

Plain-language explanations for every item are in [`compliance.md`](compliance.md). **GoBD** (rules for keeping books
electronically) and **GDPR/DSGVO** are confirmed. The rows below are suggestions for the maintainer to assign to a
release; `proposed` means not yet scheduled.

| Item | Why it matters | Proposed release |
|---|---|---|
| **E-invoice receiving** (parse XRechnung/ZUGFeRD XML, archive the original) | Receiving e-invoices is already mandatory since 2025 *(verify)* | alpha.3 (first) |
| **E-invoice issuing** (XRechnung / ZUGFeRD export) | Mandatory for all domestic B2B from 2028, for large businesses 2027 *(verify)* | alpha.3 |
| **GoBD change log (audit trail)** and **Verfahrensdokumentation generator** | GoBD core requirements | alpha.4 |
| **Zusammenfassende Meldung (ZM)** for EU B2B services | Separate mandatory report next to the UStVA | alpha.3 |
| **Annual VAT return** and **EÜR form export** (*Anlage EÜR*) | Year-end filing | alpha.4 |
| **Income-tax advance-payment reminders** (10 Mar/Jun/Sep/Dec) | Cash planning | alpha.3 |
| **Asset register and depreciation (AfA)** | Needed for correct EÜR/balance sheet | beta |
| **Invoice lifecycle**: credit notes/cancellations, advance and final invoices, early-payment discount | Everyday invoicing needs | alpha.3 |
| **Reminders (Mahnwesen)** with default interest and flat fee (§ 288 BGB) | Getting paid | alpha.3 |
| **Bank statement import** (CAMT.053/MT940/CSV) and payment matching; **EPC GiroCode QR** on invoices | Faster payment and reconciliation | alpha.4 |
| **SEPA direct-debit file export** | Optional collection of recurring payments | beta |
| **Working-time law checks** (ArbZG breaks, rest, 10 h) as sanity warnings; absence entitlement (BUrlG) | Teams with employees | alpha.2 (warnings in progress), entitlement beta |
| **Scheinselbständigkeit hint** (one customer dominates the forecast) | Informational risk hint for freelancers | beta |
| **One-Stop-Shop (OSS)** support | EU sales to private customers | later |
| **Security baseline self-assessment (OWASP ASVS L2)** and **CRA readiness** review | Trust, regulatory outlook | beta |
| **Accessibility audit (WCAG 2.2 AA)** | Usability, legal outlook | beta (PWA work starts it) |
| **Privacy pack** (privacy notice, Art. 28 agreement outline, breach checklist, erasure concept) | GDPR for hosted instances | alpha.4 (with GDPR tooling) |
| **Mobile web app (PWA)** with offline-tolerant time tracking | Phone use before a native app exists | alpha.3 (in progress) |


## Recurring: quarterly tax-rule review

On the 2nd of January, April, July and October a reminder issue is opened (`tax-review-reminder.yaml`). The checklist:
income-tax tariff and allowances, solidarity surcharge, VAT rates, small-business (*Kleinunternehmer*) limits,
UStVA boxes and filing deadlines, trade tax (*Gewerbesteuer*), SKR/DATEV/GoBD changes and new laws. Each rule in
`permita38/core/tax.py` carries its source and valid-from date.
