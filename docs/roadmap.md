# Roadmap

> **MAINTAINER TODO: Review and finalise the release assignment of each roadmap item (reminder requested by the
> maintainer).** The assignment below is a proposal. Remove this notice when the assignment is final.

Release trains and gates are described in [`release-plan.md`](release-plan.md). Status legend: **released** = in a
tagged release, **next** = scheduled for the next alpha, **planned** = assigned to a later release, **spec** = a
specification exists but no code, **research** = needs investigation before it can be scheduled.

## Released

### v0.4.0-alpha.1 (2026-10-22)

Time tracking, planning/forecast, customers/projects, offers/invoices + PDF, expenses, simple tax estimates; SQLite
storage, migrations, backup command; username/password login and **TOTP two-factor authentication**; Docker image;
release engineering (signed annotated tags, reproducible bundles, SBOM, attestations); customer data extraction from a
website's *Impressum*.

### v0.4.0-alpha.2 (2026-10-23)

| Item | Notes |
|---|---|
| **Roles and rights** (boss / employee / accountant, per-area permissions) | fixed roles; custom roles and per-user overrides not planned yet, see [`roles.md`](roles.md) |
| **Validation and sanity-check layer** incl. ArbZG warnings | [`sanity-checks.md`](sanity-checks.md) |
| **Interactive vacation and capacity planner** with what-if scenarios, binding offers, revenue forecast basis, project start/end, "numbers don't add up" warnings | [`planning.md`](planning.md) |
| **Calendars**: ICS link or file import, meetings reduce capacity | [`calendar.md`](calendar.md) |
| **Adjustable document numbering** and the adopter checklist | [`numbering.md`](numbering.md), [`adopting.md`](adopting.md) |
| **Import of existing documents** (CSV/XLSX/JSON, dry run, undo) | [`import.md`](import.md) |
| **Itemised expenses** with a zoomable bill preview | [`specs/itemised-expenses.md`](specs/itemised-expenses.md) |
| **Migration identifiers** (`NNNN-slug.sql`, id/requires chain) | [`specs/migration-ids.md`](specs/migration-ids.md) |
| Quarterly **tax-rule review** reminder workflow | rule tables with source and valid-from date |

### v0.4.0-alpha.3 (2026-10-25)

| Item | Notes |
|---|---|
| **Team timeline** (page and API, visibility per role) | [`specs/team-timeline.md`](specs/team-timeline.md) |
| **Full data export and import** (ZIP round trip, dry run, all-or-nothing, `permita38 export` / `import`, web page) | [`data-export.md`](data-export.md); not a GoBD archive; import needs an empty instance |
| Planner spreads job hours over available days; request-body validation on every write route | fixes |

## Next: v0.4.0-alpha.4

Order follows the maintainer's priorities: MCP and AI first, then supplier/subcontractor work, then accounting.

| Item | Status |
|---|---|
| **MCP server** with scoped, audited tools on top of the API tokens (read direct, changes only through the proposal queue) | spec: [`specs/mcp-server.md`](specs/mcp-server.md); Full review tier |
| **AI provider and proposal queue** (local / OpenAI-compatible model, suggestions never booked without approval) | spec: [`specs/ai-provider-and-proposals.md`](specs/ai-provider-and-proposals.md) |
| **Suppliers and subcontractors** (suppliers as a collection, external people you allocate time to, incoming offers, capacity ring-fenced to projects) | done on `main` (unreleased): suppliers, incoming offers with accept/unaccept, external allocations in the planner and forecast, commitments; spec [`specs/subcontractors.md`](specs/subcontractors.md), contract [`contracts/subcontractors.md`](contracts/subcontractors.md) |
| **Planner per project** (capacity allocated to projects, ring fence, per-project gaps, "Allocate my capacity" bootstrap) | done on `main`, see [`specs/planner-per-project.md`](specs/planner-per-project.md) and [`planning.md`](planning.md); unreleased |
| **Business trips**: import the real BMF country table for 2026 and 2027 | planned; blocks use of trips abroad |
| **Merge-import** into an instance that already has data (id remapping, conflict policy) | planned; v1 import needs an empty instance |
| **Bill extraction from PDF/image** and e-invoice XML (prefill, you confirm) | planned, builds on the AI provider |
| **Gantt chart** for the overall plan and project subtasks | later |

## v0.4.0-alpha.5

| Item | Status |
|---|---|
| **Accounting** (chart of accounts SKR03/SKR04, journal, receipts) and the **German VAT return (UStVA)** | spec: [`specs/ustva.md`](specs/ustva.md); boxes need the tax adviser |
| **Time tracking element** on the Today page: one row with description, project and task, manual entry (start, stop, date, duration, Add) and timer mode, with a click on the timer to show the earnings instead of the time | spec: [`specs/time-tracking-element.md`](specs/time-tracking-element.md); Light review tier |

## v0.4.0-alpha.6

| Item | Status |
|---|---|
| **Legal-retention backup module** (scheduled, verified, encrypted backups with retention periods) | planned |
| **GoBD compliance** (immutability, change log, *Verfahrensdokumentation* template, retention rules) | planned |
| **GDPR / DSGVO compliance** (record of processing, per-person export, erasure/anonymisation, privacy notice) | planned |
| **GDPdU / Z3 audit export** for tax audits | planned |
| **Per-IP login throttling** (on top of per-account; invalid API tokens are already throttled per IP) | planned |
| **Server-side session revocation** ("log out everywhere") | planned |

## v0.5.0-beta (feature complete for 1.0)

| Item | Status |
|---|---|
| AI-assisted input beyond the alpha.4 proposal queue: **OCR / LLM invoice scan pre-fill**, **bank statement matching** | planned |
| XLSX export; **DATEV export** (Buchungsstapel) | planned (moved back, not needed now) |
| **E-invoice export / import**: XRechnung / ZUGFeRD (EN 16931) | planned (moved back; issuing becomes mandatory for domestic B2B in 2027/2028 *(verify)*, so schedule it before then) |
| **Mobile web app (PWA)** with offline-tolerant time tracking | planned (moved back, not started in code) |
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
| **Public customer demo** | per-visitor sandboxes with fake data, risky features off; see [`specs/demo-deployment.md`](specs/demo-deployment.md) |
| **GDPR per-person export** from the export archive (filter by customer or user) | planned (alpha.6, with the GDPR tooling) |
| Recurring invoices, credit notes, reminders (Mahnungen) | |
| macOS and Linux arm64 bundles | needs signing and notarisation |

## Compliance and regulatory radar (proposed, added after the first alpha)

Plain-language explanations for every item are in [`compliance.md`](compliance.md). **GoBD** (rules for keeping books
electronically) and **GDPR/DSGVO** are confirmed. The rows below are suggestions for the maintainer to assign to a
release; `proposed` means not yet scheduled.

| Item | Why it matters | Proposed release |
|---|---|---|
| **E-invoice receiving** (parse XRechnung/ZUGFeRD XML, archive the original) | Receiving e-invoices is already mandatory since 2025 *(verify)* | beta (moved back) |
| **E-invoice issuing** (XRechnung / ZUGFeRD export) | Mandatory for all domestic B2B from 2028, for large businesses 2027 *(verify)* | beta (moved back; before the deadline) |
| **GoBD change log (audit trail)** and **Verfahrensdokumentation generator** | GoBD core requirements | alpha.6 |
| **Zusammenfassende Meldung (ZM)** for EU B2B services | Separate mandatory report next to the UStVA | alpha.5 |
| **Annual VAT return** and **EÜR form export** (*Anlage EÜR*) | Year-end filing | alpha.6 |
| **Income-tax advance-payment reminders** (10 Mar/Jun/Sep/Dec) | Cash planning | alpha.5 |
| **Asset register and depreciation (AfA)** | Needed for correct EÜR/balance sheet | beta |
| **Invoice lifecycle**: credit notes/cancellations, advance and final invoices, early-payment discount | Everyday invoicing needs | alpha.5 |
| **Reminders (Mahnwesen)** with default interest and flat fee (§ 288 BGB) | Getting paid | alpha.5 |
| **Bank statement import** (CAMT.053/MT940/CSV) and payment matching; **EPC GiroCode QR** on invoices | Faster payment and reconciliation | alpha.6 |
| **SEPA direct-debit file export** | Optional collection of recurring payments | beta |
| **Working-time law checks** (ArbZG breaks, rest, 10 h) as sanity warnings; absence entitlement (BUrlG) | Teams with employees | warnings released in alpha.2; entitlement (BUrlG) beta |
| **Scheinselbständigkeit hint** (one customer dominates the forecast) | Informational risk hint for freelancers | beta |
| **One-Stop-Shop (OSS)** support | EU sales to private customers | later |
| **Security baseline self-assessment (OWASP ASVS L2)** and **CRA readiness** review | Trust, regulatory outlook | beta |
| **Accessibility audit (WCAG 2.2 AA)** | Usability, legal outlook | beta (PWA work starts it) |
| **Privacy pack** (privacy notice, Art. 28 agreement outline, breach checklist, erasure concept) | GDPR for hosted instances | alpha.6 (with GDPR tooling) |
| **Mobile web app (PWA)** with offline-tolerant time tracking | Phone use before a native app exists | beta (moved back) |


## Recurring: quarterly tax-rule review

On the 2nd of January, April, July and October a reminder issue is opened (`tax-review-reminder.yaml`). The checklist:
income-tax tariff and allowances, solidarity surcharge, VAT rates, small-business (*Kleinunternehmer*) limits,
UStVA boxes and filing deadlines, trade tax (*Gewerbesteuer*), SKR/DATEV/GoBD changes and new laws. Each rule in
`permita38/core/tax.py` carries its source and valid-from date.
