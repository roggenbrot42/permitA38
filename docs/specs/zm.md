# Specification: Zusammenfassende Meldung (ZM, EC sales list) for services

Status: **draft for maintainer sign-off** (release "VAT + accounting", v0.5.0-alpha.1). Author: architect (Opus).
Review tier: **Full** (VAT, outside reporting). Depends on [`accounting.md`](accounting.md) and
[`ustva.md`](ustva.md). Rules marked *(verify)* go to `docs/tax-adviser-review.md` section E and ship behind the
adviser banner. All figures and VAT ids in this document and its fixtures are fake.

## Goal

A business that sells services to businesses in other EU states (`eu_b2b_service`) gets, per period, the list it has
to report to the Bundeszentralamt für Steuern (BZSt): every customer VAT id with the sum of its services, a due
date, a CSV for entry/upload in the BZSt online portal, and a frozen record of what was reported, with corrections.

## Rules (all *(verify)*)

* Only **services** (§ 18a Abs. 2 / § 18b: the release sells no goods). Period: always the **calendar quarter**
  (the monthly rule above 50,000 € concerns goods). No Dauerfristverlängerung for the ZM.
* Due: the **25th day after the end of the quarter**, shifted by § 108 Abs. 3 AO to the next working day (holidays of
  settings `state`, *(verify)* which state applies for the BZSt).
* Content per line: the customer's VAT id incl. the country prefix, the **sum of the bases** of the quarter in whole
  euros (rounding of the sum *(verify)*: default truncate toward zero like the UStVA), and the kind "sonstige
  Leistung" (marker `S` in the portal's CSV *(verify)*).
* Period of a service: the **performance date** (the `vat_date` of the `EU_SVC` line, accounting 9.2), under Soll and
  Ist alike; it must agree with UStVA Kz 21 of the same months.
* **Changes of the base** (credit note, discount, write-off, payment after a write-off) are reported in the quarter
  **in which the change occurs** (§ 18a Abs. 7 Satz 2 with § 17 applied accordingly, UStAE 18a.4 *(verify)*), as a
  negative or positive amount for that VAT id; this is the quarter of the credit line's `vat_date`, the same rule as
  UStVA Kz 21. Only a report that was **wrong when filed** (wrong VAT id, wrong amount, a missing invoice) is corrected
  in its original quarter within one month (§ 18a Abs. 10): that is the drift case below.
* No nil report is required for a quarter without EU services *(verify)*.
* A small business (§ 19) files no ZM (§ 18a Abs. 4 *(verify)*); the list is shown for information only.
* The invoice form requires a customer VAT id (format check `identifiers.vat_id_error`, country ≠ DE) for
  `eu_b2b_service`; the issue snapshot stores it (`cp_vat_id`, `cp_country` on the entry). The ZM uses the snapshot,
  never the current customer record.

## Computation

`zm_lines(year, quarter)` = group by `cp_vat_id` the `tax_base_cents` of all `EU_SVC` lines whose `vat_date` lies in
the quarter (invoices and their changes alike). The result per VAT id: `amount = trunc(Σ / 100)` (rules data,
toward zero), the country prefix, the customer's name (display only), and the source lines. A negative total is
reported as negative; a total that truncates to 0 € (e.g. 0.40 €) is omitted in a first report and listed with 0 in a
corrected one. The VAT id comes from the issue snapshot, which was validated at issue; an empty id is an error.

## Records, status, drift

Table `zm_reports` (in the accounting migration): `id`, `year`, `quarter`, `status` (`draft`/`final`), `due_date`,
`lines` JSON (`[{vat_id, country, amount, kind: "S"}]`), `source_lines` (ids), `rules_version`, `filed_on`,
`reference`, `note`, `amends_id`, `replaced_by`, `finalised_at/by`. Bounded fields, strict `*In`, lenient responses.

* Draft = computed live. **Finalise** stores the snapshot (no period lock of its own; the UStVA lock covers the months).
* **Filed**: `filed_on`, `reference`.
* **Drift**: `EU_SVC` lines created after finalising whose `vat_date` lies in this quarter — an invoice written late
  for a service of that quarter (allowed drift case 4 of accounting section 11) — mean the filed report was
  incomplete → "the ZM of Q1 must be corrected" banner, also on the Today page; the correction is a new report with
  `amends_id` carrying the full corrected list. Reopen only before filing.

## API (area `vat`, rights as the UStVA)

| Route | Purpose |
|---|---|
| `GET /api/zm?year=2026` | quarters with due date, state, total, drift |
| `GET /api/zm/preview?year=2026&quarter=1` | live list |
| `POST /api/zm` `{year, quarter, amends_id?}` | draft |
| `GET /api/zm/{id}`, `POST /api/zm/{id}/finalize`, `/reopen`, `/filed`, `DELETE` (drafts) | like the UStVA |
| `GET /api/zm/{id}/export.csv` / `.pdf` | exports |

CSV: the BZSt portal import format, per the official "Benutzeranleitung: CSV-Format der Import-Datei im
BZSt-Verfahren Zusammenfassende Meldung" (ELSTER, Stand 03.11.2015,
`https://download.elster.de/bop/importsheet/Benutzeranleitung_ZM_Import.pdf`) *(verify the current version line with
the tax adviser or the BOP help — TA-41)*:
- line 1 the version marker `#v1.0` (constant `ZM_CSV_VERSION`; the 2015 PDF shows `#v1.1`, a BZSt forum reply of 2022
  shows `#v1.0` working; a different minor version is tolerated by the portal);
- line 2 the header, which the portal does not read but always skips: `Laenderkennzeichen,USt-IdNr.,Betrag(EUR),Art der Leistung`;
- then one line per customer VAT id, 1..1000 lines, no empty lines: country code (2 letters, `EL` for Greece, taken
  from the VAT id prefix), VAT id without the prefix (A–Z, 0–9, 5..12 characters; spaces, dots and dashes removed, upper
  case ASCII; ids are aggregated after this normalisation, and a `GR` prefix is read as `EL`), the amount in whole euros (truncated as in Rules; no decimals, no separators; a `-` only for a negative
  total), and the type `S` (sonstige Leistung); example `FR,12345678901,1047,S`;
- separator `,`, no quotes, encoding ISO-8859-15, line end CRLF, every line terminated;
- rows are aggregated per country + VAT id before writing (the portal rejects duplicates);
- the reporting period and the own VAT id are not in the file; they are entered in the portal form; a correction
  (Berichtigung) uploads the full corrected list for the quarter and is marked as a correction in the form; customers that
  were reported in the corrected report and dropped to 0 € are written with amount `0` (customers never reported
  before are not listed when they are at 0);
- more than 1000 lines or a VAT id that does not fit the pattern is an export error (422 listing the rows), never a
  silently shortened file.
A second, readable CSV (`;`, decimal comma, BOM). PDF: header,
the list, the adviser banner, footer "Not a filing. Enter or upload these figures in the BZSt online portal."

## Worked examples (oracle)

* **Z1.** Q1 2026: customer A (fake AT id) invoice net 1,000.40 (performance 2026-01-15) and 2,000.70 (2026-03-02);
  customer B (fake FR id) 4,000.00 (2026-03-10, unpaid, Ist). ZM Q1: A **3,001**, B **4,000**; UStVA Kz 21 January
  1,000, March 6,000 (base 2,000.70 + 4,000.00 = 6,000.70 → 6,000).
* **Z2.** Credit note 2026-05-05 against A's March invoice, net 500.00: UStVA May Kz 21 base **−500**; ZM **Q2**:
  A **−500**; ZM Q1 unchanged (3,001 / 4,000) and **no drift** (the Q1 report was correct when filed).
* **Z3.** Due dates: Q1 2026 → 25 Apr is a Saturday → **Mon 27 Apr 2026**; Q2 → 25 Jul Saturday → **Mon 27 Jul
  2026**; Q3 → 25 Oct Sunday → **Mon 26 Oct 2026**; Q4 → **Mon 25 Jan 2027**.
* **Z4.** A customer without a VAT id cannot get an `eu_b2b_service` invoice (422 at issue).

## Test plan

Oracle Z1–Z4; credit-note lines in the quarter of the change; consistency test: Σ ZM amounts of a quarter (before
rounding) = Σ Kz 21 bases of its three months; drift only for late `EU_SVC` lines of the quarter;
VAT id format; status and drift like the UStVA; CSV golden file; rights; web tab.

## Questions for the tax adviser

1. Confirm that a small business selling EU B2B services files no ZM.
2. Rounding of the per-customer sum; negative sums in corrections.
3. Holidays for the ZM deadline (BZSt) and the portal's CSV format version.
4. Credit notes and discounts reported in the quarter of the change (as specified), and a storno of an invoice that
   was wrong from the start (wrong customer or VAT id): correction of the original quarter instead?
