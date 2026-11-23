# Specification: DATEV export (EXTF Buchungsstapel)

Status: **draft for maintainer sign-off** (release "VAT + accounting", v0.4.0-alpha.7). Author: architect (Opus).
Review tier: **Full** (money, outside format). Depends on [`accounting.md`](accounting.md). Format details marked
*(verify)* must be checked against the official DATEV "Formatbeschreibung DATEV-Format" (the work package obtains it
from DATEV's developer portal or the maintainer provides it) and, before the release, by an import at the tax
adviser. All figures are fake.

## Goal

Hand the books to the tax adviser: a DATEV-importable CSV of the journal for a date range, so the adviser can
continue in DATEV (annual statements, returns). The app's own VAT return stays authoritative for what the user files;
known differences between DATEV's derivation and the app are listed in the export's report.

## File

* One file per export: `EXTF_Buchungsstapel_<from>_<to>.csv`, plus `export-report.txt` (counts, sums, differences,
  the adviser banner) in a zip.
* Encoding Windows-1252 (characters outside it replaced and listed in the report) *(verify: UTF-8 acceptance)*;
  separator `;`; text fields in double quotes (quotes doubled); decimal comma; CRLF.
* **Header line** (format version 700, category 21 "Buchungsstapel", format version 13 *(verify)*): `"EXTF";700;21;
  "Buchungsstapel";13;<created YYYYMMDDHHMMSSmmm>;;"";"";"";<consultant no>;<client no>;<fiscal year start YYYYMMDD>;
  <account length>;<from YYYYMMDD>;<to YYYYMMDD>;"<label>";"";1;0;<locked 0/1>;"EUR";...` (remaining fields empty,
  exact count per the format description).
* **Column header line** and one **row per booking** with at least: `Umsatz (ohne Soll/Haben-Kz)` (positive, comma),
  `Soll/Haben-Kennzeichen` (`S`/`H`, from the point of view of `Konto`), `WKZ Umsatz` (`EUR`), `Konto`,
  `Gegenkonto (ohne BU-Schlüssel)`, `BU-Schlüssel`, `Belegdatum` (`DDMM`), `Belegfeld 1` (document number or entry
  number, ≤ 36), `Buchungstext` (≤ 60), `EU-Mitgliedstaat u. UStIdNr` / `EU-Steuersatz` where applicable *(verify)*,
  `Festschreibung` (1 when the month is locked).
* New settings (business settings, validated): `datev_consultant_no` (1001–9999999), `datev_client_no` (1–99999),
  `datev_account_length` (4–8, default 4), `datev_label` (≤ 30). Missing numbers → 422 with a hint.

## From journal entries to rows

The journal is double-entry with explicit VAT lines; DATEV books **gross** amounts and derives VAT from automatic
accounts or BU keys. The conversion per entry:

1. **Anchor.** Each entry has one counter side: the line(s) on a money, open-item or clearing role (`bank`, `cash`,
   `receivables`, `payables`, `payables_employee`, `customer_credit`, `opening_balance`, `vat_soll_clearing`,
   `input_vat_clearing`, `private_*`). Every other line becomes a row against the anchor account. An entry with
   several anchors (e.g. an overpayment) is split by matching amounts; if no exact split exists the entry is exported
   with an interim account `datev_transfer` role and listed in the report.
2. **VAT folding.** A `tax`/`input` line is added to the row of its partner line (same rate group, revenue or cost)
   so the row carries the gross amount; the partner account's `automatic` key (chart file) or the BU key from
   `datev/<year>.json` makes DATEV split it again. A VAT line without a partner in the entry (EÜR/Soll issue:
   `vat_soll_clearing` ↔ `vat_out`; Ist recognition: `vat_not_due` ↔ `vat_out`) is exported as a plain row between
   the two accounts without BU key.
3. **§ 13b.** The cost row carries the **net** amount with the § 13b BU key (EU service / third-country service,
   *(verify)* the key numbers in `datev/<year>.json`); the self-assessed tax lines are folded (DATEV creates both
   sides). The non-deductible part (cost) is a separate row without BU key.
4. **Statistical lines** (`EU_SVC` base_only with amount 0) are not exported; DATEV derives Kz 21 from the revenue
   account at its booking date. Under EÜR this is the payment date, not the performance date → listed in the report
   as a known difference.
5. **Splits after folding.** A VAT line is always folded into **one** row whose gross is the full net it belongs to
   plus the VAT (so DATEV's split reproduces the VAT, see the self-check). Splits of that net (deductible /
   non-deductible meals, non-deductible § 13b VAT, cost shares) follow as **reclassification rows** between the two
   accounts without BU key, with the same `Belegfeld 1`. Itemised bills give one folded row per rate group.
6. Rows are ordered by booking date and entry number; `Belegdatum` = booking date.

## Self-check (in the export and in tests)

A **DATEV simulation** in the export module re-splits every row by its automatic account or BU key with the VAT rates
of the rules file and sums per account. The result must equal the journal's balances per account for the range
(cent-exact; DATEV rounds the derived VAT per row half-up *(verify)*, and per-row folding keeps the document's VAT, so
any difference is a bug). Differences are listed in the report and fail the tests.

## API and UI

`GET /api/accounting/datev?from=2026-01-01&to=2026-03-31` (area `accounting`, boss and accountant) → zip. The
accounting page has "Export for the tax adviser (DATEV)" with the range, the settings check and the report. A
range with unlocked months is allowed; `Festschreibung` is 0 for those rows and the report says so.

## Worked examples (oracle, account numbers from the chart file, here as roles)

* **D1** (accounting A1, accrual/Soll): issue → one row: Umsatz 1.190,00, `S`, Konto `receivables`, Gegenkonto
  `revenue_s19` (automatic S19), no BU key. Payment → 1.190,00 `S` `bank` / `receivables`.
* **D2** (A5, § 13b EU, accrual, 100 %): one row 200,50 `S` `expense_software` / `payables` with the BU key for
  § 13b EU 19 % (DATEV creates 38,10 output and 38,10 input VAT). With `vat_deductible` 50: the same row plus the
  reclassification 19,05 `S` `expense_software` / `rc_input_vat` (no BU key). Simulation: `rc_vat_out_eu` −38,10,
  `rc_input_vat` +19,05, `expense_software` +219,55, `payables` −200,50 = the journal. The adviser confirms the
  import of this case *(verify)*.
* **D3** (A6, meal, accrual): 107,00 `S` `meals_deductible` / `payables` with the BU key for 7 % input VAT (DATEV:
  net 100,00, input VAT 7,00), then the reclassification 30,00 `S` `meals_nondeductible` / `meals_deductible`.
  Simulation: 70,00 / 30,00 costs, 7,00 input VAT, payables −107,00. (Folding 77,00 into one row would make DATEV
  derive 5,04 VAT — wrong; hence rule 5.)

Why the folded gross always reproduces the document VAT: for one rate group the document VAT is
`V = q2(N × r/100)`; DATEV derives `q2((N + V) × r/(100 + r))`, whose exact value differs from `V` by less than
half a cent (`|V − N·r/100| ≤ 0.005` times `100/(100 + r)`), so it rounds back to `V`. The self-check proves it per
export anyway.

## Test plan

Golden files for D1–D3 and for every posting template of accounting section 9 under the four bases; the DATEV
simulation equals the journal for the hypothesis-generated journals; encoding and quoting (`;`, quotes, umlauts,
formula characters are **not** escaped with `'` here: DATEV reads raw text, but text is cut to the field length);
header fields; settings validation; rights. Before the release: one import into DATEV by the adviser (manual gate).

## Questions for the tax adviser

1. Which BU keys and automatic accounts the adviser expects (SKR03 and SKR04) for 19 %, 7 %, other rates, § 13b EU and
   third-country services, EU B2B services.
2. Interim/clearing accounts for EÜR (input VAT before payment, Soll VAT before payment) — acceptable or does the
   adviser prefer a different EÜR representation?
3. Debtor/creditor accounts (personal accounts) or collective accounts in v1?
4. Whether the export should be per month or per year, and with or without `Festschreibung`.
