# Specification: Zusammenfassende Meldung (ZM, EC sales list) for services

Status: **draft for maintainer sign-off** (release "VAT + accounting", v0.4.0-alpha.7). Author: architect (Opus).
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
* Period of a service: the **performance date** (the `vat_date` of the `EU_SVC` line, accounting 9.1), under Soll and
  Ist alike; it must agree with UStVA Kz 21 of the same months.
* **Corrections** (§ 18a Abs. 10): a credit note, write-off or late change corrects the ZM **of the quarter of the
  original service** (within one month after discovering it), not the current quarter. The UStVA, by contrast,
  corrects in the period of the change (§ 17). The ZM therefore assigns a credit-note line to the quarter of the
  `vat_date` of the corrected invoice's `EU_SVC` line (found through the credit note's `corrects_id`).
* No nil report is required for a quarter without EU services *(verify)*.
* A small business (§ 19) that sells EU B2B services: whether a ZM is due is an adviser question; v1 shows the list
  with a hint and does not mark it due.
* The invoice form requires a customer VAT id (format check `identifiers.vat_id_error`, country ≠ DE) for
  `eu_b2b_service`; the issue snapshot stores it (`cp_vat_id`, `cp_country` on the entry). The ZM uses the snapshot,
  never the current customer record.

## Computation

`zm_lines(year, quarter)` = group by `cp_vat_id` the `tax_base_cents` of all `EU_SVC` lines whose **ZM quarter** is
the requested one (invoice lines by their `vat_date`; credit-note lines by the corrected invoice's `vat_date`). The
result per VAT id: `amount = trunc(Σ / 100)` (rules data), the country prefix, the customer's name (display only),
and the source lines. A VAT id with a total of 0 after corrections is listed with 0 in a corrected report and omitted
in a first report.

## Records, status, drift

Table `zm_reports` (in the accounting migration): `id`, `year`, `quarter`, `status` (`draft`/`final`), `due_date`,
`lines` JSON (`[{vat_id, country, amount, kind: "S"}]`), `source_lines` (ids), `rules_version`, `filed_on`,
`reference`, `note`, `amends_id`, `replaced_by`, `finalised_at/by`. Bounded fields, strict `*In`, lenient responses.

* Draft = computed live. **Finalise** stores the snapshot (no period lock of its own; the UStVA lock covers the months).
* **Filed**: `filed_on`, `reference`.
* **Drift**: lines created after finalising whose ZM quarter is this one (typically a credit note for an earlier
  service) → "the ZM of Q1 must be corrected" banner, also on the Today page; the correction is a new report with
  `amends_id` carrying the full corrected list. Reopen only before filing.

## API (area `vat`, rights as the UStVA)

| Route | Purpose |
|---|---|
| `GET /api/zm?year=2026` | quarters with due date, state, total, drift |
| `GET /api/zm/preview?year=2026&quarter=1` | live list |
| `POST /api/zm` `{year, quarter, amends_id?}` | draft |
| `GET /api/zm/{id}`, `POST /api/zm/{id}/finalize`, `/reopen`, `/filed`, `DELETE` (drafts) | like the UStVA |
| `GET /api/zm/{id}/export.csv` / `.pdf` | exports |

CSV: the BZSt portal import format *(verify the exact column layout against the BZSt "CSV-Upload ZM" description;
the WP fetches it and stores a golden file)*; a second, readable CSV (`;`, decimal comma, BOM). PDF: header,
the list, the adviser banner, footer "Not a filing. Enter or upload these figures in the BZSt online portal."

## Worked examples (oracle)

* **Z1.** Q1 2026: customer A (fake AT id) invoice net 1,000.40 (performance 2026-01-15) and 2,000.70 (2026-03-02);
  customer B (fake FR id) 4,000.00 (2026-03-10, unpaid, Ist). ZM Q1: A **3,001**, B **4,000**; UStVA Kz 21 January
  1,000, March 6,000 (base 2,000.70 + 4,000.00 = 6,000.70 → 6,000).
* **Z2.** Credit note 2026-05-05 against A's March invoice, net 500.00: UStVA May Kz 21 base **−500**; ZM Q2
  unchanged; ZM Q1 drift → corrected Q1 report: A **2,501**, B 4,000.
* **Z3.** Due dates: Q1 2026 → 25 Apr is a Saturday → **Mon 27 Apr 2026**; Q2 → 25 Jul Saturday → **Mon 27 Jul
  2026**; Q3 → 25 Oct Sunday → **Mon 26 Oct 2026**; Q4 → **Mon 25 Jan 2027**.
* **Z4.** A customer without a VAT id cannot get an `eu_b2b_service` invoice (422 at issue).

## Test plan

Oracle Z1–Z4; ZM quarter of credit-note lines; consistency test: Σ ZM amounts of a quarter (before rounding) = Σ Kz 21
bases of its three months minus credit notes for earlier quarters plus credit notes in later quarters for this one;
VAT id format; status and drift like the UStVA; CSV golden file; rights; web tab.

## Questions for the tax adviser

1. ZM duty and form for a small business selling EU B2B services.
2. Rounding of the per-customer sum; negative sums in corrections.
3. Holidays for the ZM deadline (BZSt) and the portal's CSV format version.
4. Correction period for a credit note issued months later (original quarter, as specified).
