# Compliance and regulatory topics – a plain-language guide

permitA38 is used for real bookkeeping, so a number of German (and EU) rules apply to the data it handles. This page
explains each topic **in plain language**: what it is, who it concerns, what permitA38 does about it today and what is
planned. The status column refers to the [roadmap](roadmap.md).

> **This is a guide for planning, not legal or tax advice.** Rules change (the [quarterly review](roadmap.md#recurring-quarterly-tax-rule-review)
> exists for that reason) and details depend on your situation. Confirm everything that matters with your
> *Steuerberater* or a lawyer. Statements marked *(verify)* are ones we want checked against the official source in the
> next review.

## 1. GoBD – keeping books electronically the way the tax office expects

**What it is.** *GoBD* stands for *Grundsätze zur ordnungsmäßigen Führung und Aufbewahrung von Büchern, Aufzeichnungen
und Unterlagen in elektronischer Form sowie zum Datenzugriff* – a letter from the Federal Ministry of Finance (BMF)
that tells businesses how electronic bookkeeping must work so an auditor can trust it. It is not a law you "sign up
to"; it is how the tax authorities interpret the duty to keep proper books (§§ 145 ff. AO, § 238 HGB).

**Who it concerns.** Everybody who keeps books or records electronically and must file taxes in Germany –
freelancers (even with a simple income/expense calculation), sole traders, GmbHs.

**The principles in one paragraph each**

| Principle | Plain meaning | What it means for software |
|---|---|---|
| Traceability (*Nachvollziehbarkeit*) | A knowledgeable third party can follow every booking from the receipt to the annual figures. | Every booking points to its receipt; reports can drill down. |
| Completeness (*Vollständigkeit*) | Nothing is missing, no booking is skipped. | Gapless booking numbers, no way to silently delete. |
| Correctness and timeliness (*Richtigkeit, Zeitgerechtheit*) | Bookings are right and made promptly (cash movements daily; other business transactions within about ten days *(verify)*). | Booking dates are recorded; late postings are visible. |
| Order (*Ordnung*) | Records are findable and filed systematically. | Search, sensible numbering, chart of accounts. |
| **Immutability** (*Unveränderbarkeit*) | Once posted, a booking must not be changed or deleted without a trace. | Corrections only by a reversal entry; edits are logged. |
| Retention (*Aufbewahrung*) | Keep books and receipts for the legal period and keep them readable. | Retention-protected backups and documents, see §4. |
| **Process documentation** (*Verfahrensdokumentation*) | A written description of how your bookkeeping process works (who enters what, where, how it is backed up). | permitA38 will generate a pre-filled template from its own configuration. |
| Data access (*Datenzugriff*, Z1–Z3) | An auditor may look at the data on your system (Z1), ask you to evaluate it (Z2) or take it on a data carrier (Z3). | Audit export in the standard description format (GDPdU/Z3). |

**Status in permitA38.** Immutable journal entries and period locks arrive with the accounting module (alpha.2/3);
stored copies of every issued invoice PDF arrive with the backup module; the change log (audit trail), the
*Verfahrensdokumentation* generator and the audit export follow in alpha.4. We do **not** claim GoBD certification –
there is no official certification for software; compliance is a matter of how the software is used and documented.

## 2. GDPR (DSGVO) – protecting personal data

**What it is.** The EU General Data Protection Regulation. Your customers' names, addresses, e-mails, phone numbers and
notes are personal data, and so is the data of the people on your team.

**Who it concerns.** You as the *controller* of your customer and team data. If you host permitA38 for other
businesses, you are additionally a *processor* and need a data-processing agreement (Art. 28).

**What you must be able to do**

- Tell people what you do with their data (privacy notice).
- Keep a **record of processing activities** (Art. 30) – a short table of what data you process, why, for how long.
- Give a person their data on request (**access** and **portability**, Art. 15 and 20).
- **Erase** data on request (Art. 17) – *except* where the law makes you keep it. Invoices and bookkeeping data must be
  kept for years, so erasure usually means "anonymise what you may, restrict what you must keep".
- Protect the data with *technical and organisational measures* (TOMs) and report serious breaches within 72 hours
  (Art. 33).

**Status in permitA38.** Data export per person, an erasure wizard that respects retention rules, a pre-filled record of
processing activities, and template texts (privacy notice, processing agreement outline, breach checklist) are planned
for alpha.2/4 (`docs/gdpr.md` once written). The existing measures (hashed passwords, two-factor login, throttling,
SSRF-safe outbound requests, strict input validation) are listed in [SECURITY.md](../SECURITY.md).

## 3. E-invoicing (*E-Rechnung*) – now relevant

**What it is.** For business-to-business invoices within Germany, a PDF is no longer a "proper" invoice. A proper
e-invoice is a structured XML file following the European standard EN 16931 – the formats **XRechnung** (pure XML)
and **ZUGFeRD / Factur-X** (a PDF with the XML embedded).

**Timeline** *(verify)*: since **1 January 2025** every business must be able to **receive** e-invoices; sending
paper/PDF invoices is still allowed during a transition period; **issuing** e-invoices becomes mandatory from
**1 January 2027** for businesses with a prior-year turnover above 800,000 € and from **1 January 2028** for all
domestic B2B invoices. Small-business (*Kleinunternehmer*) invoices and invoices up to 250 € are exempt from the
issuing duty. E-invoices must be archived (8 years) in their original machine-readable form.

**Status in permitA38.** Import of e-invoice XML (parse and pre-fill an expense), and export of XRechnung/ZUGFeRD for
your invoices are planned (alpha.3). Because the *receiving* duty already applies, receiving is scheduled first.

## 4. Retention periods (*Aufbewahrungsfristen*, § 147 AO)

| Kind of record | Retention *(verify)* |
|---|---|
| Accounting vouchers / receipts (*Buchungsbelege*), incl. invoices | 8 years (shortened from 10 by the 2025 bureaucracy-relief act) |
| Books, records, annual accounts, opening balance (*Bücher, Jahresabschlüsse*) | 10 years |
| Business letters and other tax-relevant documents (*Handels- und Geschäftsbriefe*) | 6 years |
| Working-time records beyond 8 hours/day (§ 16 ArbZG) | 2 years |

The period starts at the end of the calendar year in which the record was created. During the period records must stay
readable and unchanged. The backup module (alpha.4) keeps yearly archives protected until the period ends.

## 5. VAT beyond the monthly return

| Topic | Plain meaning | Status |
|---|---|---|
| **Umsatzsteuer-Voranmeldung (UStVA)** | The regular advance VAT return (monthly/quarterly). | in progress (alpha.2/3) |
| **Zusammenfassende Meldung (ZM)** | A separate report of your tax-free B2B sales to other EU countries. | proposed (alpha.3) |
| **One-Stop-Shop (OSS)** | If you sell to private customers in other EU countries above 10,000 € per year you may have to charge their VAT rate and report through one portal. | proposed (later) |
| **Reverse charge (§ 13b UStG)** | For some services from abroad *you* owe the VAT. Appears in the UStVA boxes. | in progress |
| **Kleinunternehmer rules** | Below the turnover limits you charge no VAT; limits and the EU scheme changed in 2025. | partial; rules data reviewed quarterly |
| **Annual VAT return** | The yearly summary after the advance returns. | proposed (alpha.4) |
| **Small-amount invoices (§ 33 UStDV)** | Invoices up to 250 € need fewer fields. | proposed (sanity rules) |

## 6. Income tax and accounting extras

- **Advance payments (*Vorauszahlungen*)** of income tax are due quarterly (10 March, 10 June, 10 September,
  10 December) – a reminder and cash-planning item (proposed).
- **EÜR** (income/expense statement) and the form *Anlage EÜR*: derive the figures from the books and export them
  in the layout of the form (proposed, alpha.4).
- **Fixed assets and depreciation (*Anlagenverzeichnis, AfA*)**: equipment above the low-value limit (800 € net,
  *verify*) is written off over several years; a simple asset register would produce the booking entries (proposed).
- **Trade tax return**, **annual accounts / e-balance sheet (*E-Bilanz*)** for GmbH/UG: out of scope for now (research).
- **Entertainment costs** (70 % deductible) are already supported by the *deductible %* field on expenses.

## 7. People who work for you

If you add team members (employees, not just freelancers) a few more rules apply:

- **Recording working time.** Since a 2022 Federal Labour Court ruling employers must record working time. The
  Working Time Act (ArbZG) limits days to 8 hours (up to 10 with compensation), requires 11 hours of rest and
  breaks of 30 minutes after 6 hours and 45 minutes after 9 hours. permitA38's sanity layer will **warn** about
  violations (it is a plausibility aid, not a compliance guarantee).
- **Minimum wage documentation** (*MiLoG* § 17) for certain sectors and mini-jobs: out of scope, export helps.
- **Paid leave** (*BUrlG*): the absence planner can track entitlement (proposed).
- **Bogus self-employment (*Scheinselbständigkeit*)**: if you work for very few customers or are bound by their
  instructions, you may be treated as an employee – a hint card in planning when one customer dominates the forecast
  (proposed, informational).
- **Artists' social insurance (*Künstlersozialabgabe*)** if you commission creative freelancers (proposed, informational).
- **Payroll** is out of scope; an export for payroll software is possible later.

## 8. Payments and getting paid

- **Late payment (*Verzug*, § 288 BGB)**: between businesses, default interest and a 40 € flat fee apply after the due
  date; reminders (*Mahnungen*) with levels, a PDF letter and the interest calculation are planned.
- **Credit notes and cancellations (*Gutschriften, Stornorechnungen*)**, **advance and final invoices**
  (*Abschlags-/Schlussrechnung*), early-payment discount (*Skonto*): invoice lifecycle features (proposed, alpha.3).
- **SEPA and bank import**: importing bank statements (CAMT.053 / MT940 / CSV) to match payments with invoices, SEPA
  direct-debit files, and the **EPC "GiroCode" QR** on invoices for one-scan payment (proposed).

## 9. Software and platform obligations

| Topic | Relevance | Plan |
|---|---|---|
| **EU Cyber Resilience Act (CRA)** | Applies to products with digital elements placed on the market *commercially*; purely non-commercial open source is largely exempt, but reporting duties and "open-source steward" expectations are phasing in *(verify dates)*. | Keep SBOM, vulnerability policy ([SECURITY.md](../SECURITY.md)) and signed releases; re-assess if the project becomes commercial. |
| **Accessibility (BFSG, WCAG 2.2 AA)** | The accessibility law mainly targets consumer-facing services; micro-enterprises are exempt *(verify)*. | We aim for WCAG 2.2 AA anyway (PWA/mobile work includes an audit). |
| **Cookies / TDDDG** | permitA38 sets only a technical login cookie – no consent banner needed. | Document it; no tracking. |
| **Imprint and privacy notice for a hosted instance** (§ 5 DDG) | Needed if you offer the app to others. | Template in the GDPR docs. |
| **Security baseline** | OWASP ASVS level 2 is a recognised yardstick. | Self-assessment checklist before beta. |
| **Cash registers (KassenSichV/TSE)** | Only if you take cash with an electronic till. | Out of scope; a simple cash book may come later. |

## Where this goes next

The release assignment of every item is in the [roadmap](roadmap.md). The maintainer decides what goes into which
release; the quarterly review keeps the *(verify)* items honest.
