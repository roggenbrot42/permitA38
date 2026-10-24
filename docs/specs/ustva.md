# Specification: VAT advance return (Umsatzsteuer-Voranmeldung, UStVA)

Status: **proposed, not scheduled** (roadmap: alpha.3 "accounting and the German VAT return"). Author: architect
(Sonnet 5.5). Review tier: **Full** (money/VAT, rights, immutability). The implementer follows this document, does not
decide tax questions, and lists every choice it had to make under "Open design decisions".

All tax statements below come from the architect's knowledge of the statute and the ELSTER form, **not** from the
current official form or a tax adviser. Every box number (*Kennzahl*, Kz) and every rule that is not certain is marked
*(verify)*. Where a box number is not known with confidence the text says so instead of guessing. Before the feature
ships, the maintainer's tax adviser confirms the "Rules" and "Boxes" sections against the form of the filing year
(see "Open questions for the tax adviser"), and `docs/compliance.md` is updated. All figures in this document are fake
demo figures.

## Goal

Give a regularly taxed (*Regelbesteuerer*) freelancer or small business, for each filing period, a **complete,
explainable set of figures to type into Mein ELSTER**: every box with its amount, every amount traceable to the
invoices and expenses behind it, a deadline with a reminder, and a frozen record of what was filed. The app computes
and documents; the user files.

## Scope and non-goals

In scope (v1):

* Monthly and quarterly returns for a **Regelbesteuerer** (standard rates 19 % and 7 %, other rates, input VAT,
  reverse charge for services received from abroad, tax-free and non-taxable sales to EU businesses, other
  non-taxable sales abroad).
* Soll- and Ist-Versteuerung (tax on invoicing vs. on payment).
* Dauerfristverlängerung (permanent one-month extension) incl. the special prepayment (*Sondervorauszahlung*).
* Deadline calculation with weekend/holiday shift, reminder, status per period.
* The small-business case (§ 19 UStG): clear "no return needed" display, no figures.
* Export: PDF overview, CSV and JSON for manual entry in Mein ELSTER.
* Freeze (finalise) and reopen of a return; a "filed on / reference" note.

Non-goals (v1):

* **Transmission to ELSTER** (ERiC, certificates, authentication). Research item only, see the end.
* The annual VAT return (*Umsatzsteuererklärung*, roadmap alpha.4), the Zusammenfassende Meldung (follow-up spec, see
  below), One-Stop-Shop (OSS), the import-VAT and intra-EU acquisition of **goods** boxes (the app targets service
  providers; the data model leaves room), margin scheme, agricultural flat rates, fixed assets (§ 15a),
  advance-payment invoices (*Anzahlungen*, tax is due on receipt, *verify*), group taxation, foreign VAT registrations.
* Validation of customer VAT ids against the BZSt service (network access; separate decision).
* Telling the user *which* tax treatment applies. The app suggests a treatment from the customer's country and VAT id;
  the user (or the adviser) decides. No tax advice.
* Hard locking of the underlying invoices and expenses (GoBD period lock, belongs to the accounting module);
  v1 detects *drift* instead, see "Immutability".

## What the app already has, and what is missing

Already there (read from `permita38/core/calc.py`, `models.py`, `money.py`):

| Data | Where | Use for the UStVA |
|---|---|---|
| Invoice totals per rate | `calc.doc_totals`: net per line, VAT per rate group, rounded half-up on the **document** | sales base and tax per rate |
| Invoice `status` (`draft/sent/accepted/rejected/paid/cancelled`), `date`, `paid_date`, `service_from/to` | `DocumentIn` | Ist/Soll date, which invoices count |
| Settings `accounting` (`eur` = counted when paid, `accrual` = counted at invoice date), `kleinunternehmer`, `vat_rate`, `state`, `tax_number`, `vat_id` | `Settings` | defaults, holiday state |
| Customer `country`, `vat_id` | `Customer` | suggest the treatment of a sale |
| Expense `net`, `vat`, `vat_rate`, `deductible`, `vat_deductible`, `date`; itemised `lines` with their own rate and shares (`expense_lines()`); business meals reclaim input VAT in full | `calc.finance` | input VAT (Kz 66) |
| Trips (`allowance`, `mileage`) | `calc.trip_amounts` | **no VAT**, so they never touch the return (checked by a test) |
| Business-trip receipts | normal expenses with `trip_id` | input VAT like any expense |
| `money.q2`, `split_gross`, `vat_amount` | `core/money.py` | all arithmetic |
| `holidays.holidays(year, state)` | `core/holidays.py` | deadline shift |
| `rights.py` areas and matrix, `trips` reopen route | | pattern for rights and reopen |

Missing (this spec adds it):

1. **Tax treatment per sale and per expense.** Today everything is "standard rate". There is no way to say: this
   invoice is a reverse-charge sale, a service to an EU business, an export, an exempt sale; this expense is a § 13b
   service received from abroad. Add `tax_treatment` (enum, below) on invoices and expenses, plus a snapshot of the
   regime at issue time.
2. **Regime frozen at issue.** `doc_totals` reads the *current* `kleinunternehmer` setting, so switching the setting
   re-prices old invoices. A document must remember at issue (status leaves `draft`) whether it was issued as
   small-business (`tax_treatment = small_business`), otherwise a return for an earlier period changes retroactively.
3. **VAT basis setting** `vat_basis` (`ist` / `soll`), independent of `accounting`. EÜR income tax is cash-based, but
   the VAT basis of a business can still be Soll. Default `ist` for `accounting = eur` (the app today counts
   revenue when paid), `soll` for `accrual` (*verify*: the default is only a suggestion; the tax office decides).
4. **Filing settings**: frequency (`month` / `quarter`), `dauerfrist` (bool), `special_prepayment` handling, optional
   `vat_regular_from` date (start of regular taxation after the small-business status).
5. **Return records** (`vat_returns`) with a frozen snapshot; the deadline list; exports.
6. **Net per rate in the document totals.** `doc_totals` has `by_rate` (net per rate) internally; the return needs
   it exposed per document together with the document VAT (so the return is a pure sum of document figures).
7. **Partial payments** do not exist (status `paid` + `paid_date`). Under Ist-Versteuerung a partly paid invoice is
   taxed pro rata; v1 treats an invoice as paid in full on `paid_date` and says so in the UI (*open question*).
8. A sanity rule for sales with rate 0 % and treatment `standard` ("choose a tax treatment").

### Tax treatments (new enum, `tax_treatment`)

On **sales** (invoices); the default `standard` keeps everything that exists working unchanged:

| Value | Meaning | Boxes (all *verify*) |
|---|---|---|
| `standard` | taxable at the document's rates | 81 (19 %), 86 (7 %), 35/36 (other rate) |
| `small_business` | issued under § 19 UStG, no VAT shown | none (outside the return) |
| `reverse_charge_domestic` | recipient owes the tax (§ 13b Abs. 5 UStG, e.g. construction services) | 60 |
| `eu_b2b_service` | service to a business in another EU state, place of supply abroad (§ 3a Abs. 2, § 18b) | 21; ZM |
| `eu_b2b_goods_exempt` | exempt intra-Community supply of goods | 41; ZM |
| `export_exempt` | exempt export to a third country | 43 |
| `non_taxable_other` | other sales whose place of supply is abroad | 45 |
| `exempt_with_deduction` / `exempt_no_deduction` | other exempt sales (§ 4 UStG) | 43 / 48 |

On **expenses**:

| Value | Meaning | Boxes (all *verify*) |
|---|---|---|
| `standard` | German VAT on the receipt | 66 |
| `no_input_vat` | no VAT or not deductible (small-business supplier, private) | none |
| `reverse_charge_eu_service` | service from an EU supplier, no VAT on the receipt, recipient owes (§ 13b Abs. 1) | 46 / 47, input 67 |
| `reverse_charge_third_country` | service from a supplier outside the EU (§ 13b Abs. 2 Nr. 1) | 73 / 74, input 67 |
| `reverse_charge_domestic` | e.g. construction services, § 13b Abs. 2 Nr. 4 | 84 / 85, input 67 |
| `import_vat` | paid import VAT (customs) | 62 |
| later: intra-Community acquisition of goods | | 89/93 and tax boxes (not confirmed, not v1) |

For a reverse-charge expense the user enters the **net** amount of the supplier's invoice; the app computes the
self-assessed VAT at the document's German rate (default 19) and stores it as `vat`. The cash paid and the booked
**cost is the net amount only** (the VAT is never paid to the supplier). `vat_deductible` decides how much of the
self-assessed VAT is also claimed as input VAT (default 100 %).

The default suggestion for a new invoice: customer country DE → `standard`; EU country with a VAT id and a
service → `eu_b2b_service`; otherwise `standard` with a hint. The user always confirms; nothing is silent.

## Period model

* `vat_frequency`: `month` or `quarter`. The tax office assigns it from the **previous year's tax liability**:
  more than 9,000 € monthly; up to 9,000 € quarterly; up to 2,000 € the office may exempt from advance returns;
  newly founded businesses file monthly in the first two years (*verify*, § 18 Abs. 2 UStG; the new-business rule was
  suspended for 2021 to 2026, *verify*). The app does **not** decide; it stores the frequency the user was told, shows
  the previous year's liability as a hint, and warns when the hint and the setting disagree.
* Periods: month `1..12`, quarter `1..4`. Period `from`/`to` are calendar dates (local, `YYYY-MM-DD`, both inclusive).
  Dates carry no time; there is no time-zone logic.
* A return belongs to **one business** (the app is single-business) and one `(year, period_kind, period_no)`; at most
  one non-amended return per period. An **amended** return (*berichtigte Anmeldung*, a Kennzahl on the form,
  *verify*) is a new return with `amends_id` pointing at the filed one; it replaces it, it does not edit it.
* **Dauerfristverlängerung** (§ 46 UStDV): the deadline moves back by one month. For monthly filers it requires a
  special prepayment of 1/11 of the sum of the previous year's prepayments (see below); quarterly filers need none
  (*verify*). The permission is applied for at the tax office (via ELSTER, until the deadline of the first period it
  applies to, *verify*) and is valid until revoked; the app only records `dauerfrist = true` and does not check it.
* **Soll- vs. Ist-Versteuerung.** `vat_basis` decides the *period of a sale*:
  * `soll`: the period of the performance. v1 date used: `service_to` if set, else the invoice `date` (the real rule
    is the date the service is completed, *verify*; invoices without a service date use the invoice date).
  * `ist`: the period of the **payment**: `paid_date` of invoices with `status = paid`. Unpaid invoices are not in any
    return; they appear in the "not yet taxed (open receivables)" line of the preview.
  * Drafts and cancelled documents are never in a return. A `sent`/`accepted` document is in a Soll return and not in
    an Ist return.
  * Conditions for Ist (*verify*): turnover up to 800,000 € per year, or a freelancer (*Freiberufler*, § 20 Satz 1
    Nr. 3), or no duty to keep books; the app shows the condition as a hint and does not enforce it.
* **Input VAT** is always claimed in the period of the **expense date** (`date`, which is the invoice date), under
  both bases, as soon as the invoice exists and the service is received, independent of payment (§ 15 Abs. 1
  Nr. 1 UStG; *verify* for Ist-Versteuerer, who still deduct on receipt of the invoice). The existing finance report
  does the same (`expenses` by `date`).
* `finance()` (the monthly figures `vat_out`, `vat_in`, `vat_due`) uses `accounting` for the sales date. The return
  uses `vat_basis`. When `vat_basis` is equivalent to `accounting` (`ist`/`eur`, `soll`/`accrual`) the 12 monthly
  returns of a year sum to `finance()`'s totals for standard sales (reconciliation test). When they differ the finance
  page shows a one-line note "VAT figures follow the VAT basis, not the income basis".

## Boxes (Kennzahlen) and mapping

The form changes yearly. Boxes and labels live in a **rules data file**, one per form year:
`permita38/core/vat_rules/2026.json` (`form_year`, `valid_from`, `source` note, list of boxes with `kz`, `label_de`,
`label_en`, `kind` = `base` | `tax` | `input` | `result`, `rate`). A return stores the `rules_version` it was computed
with. A test fails if a filing year in use has no file. The numbers below are the architect's recollection of the
standard form; **every one is `(verify)` against the form of the filing year, and a box listed with "unsure" must not
be used before it is confirmed**.

| Kz | Meaning | Base / tax | Source in the app |
|---|---|---|---|
| 81 | taxable sales at 19 % | base (full euros), tax computed by the form | `standard` sales, rate 19 |
| 86 | taxable sales at 7 % | base, tax computed by the form | `standard` sales, rate 7 |
| 35 / 36 | taxable sales at other rates | base / tax (entered) | `standard` sales, any other rate above 0 |
| 41 | tax-free intra-Community supplies of goods | base | `eu_b2b_goods_exempt` |
| 43 | other tax-free sales with input VAT deduction (e.g. exports) | base | `export_exempt`, `exempt_with_deduction` |
| 48 | tax-free sales without input VAT deduction (§ 4 Nr. 8 ff.) | base | `exempt_no_deduction` |
| 21 | non-taxable services to EU businesses (§ 18b Satz 1 Nr. 2) | base | `eu_b2b_service` |
| 45 | other non-taxable sales (place of supply abroad) | base | `non_taxable_other` |
| 60 | sales where the recipient owes the tax (§ 13b Abs. 5) | base | `reverse_charge_domestic` sales |
| 46 / 47 | services from other EU states (§ 13b Abs. 1) | base / tax | `reverse_charge_eu_service` expenses |
| 73 / 74 | services from third-country suppliers (§ 13b Abs. 2 Nr. 1 and 5a) | base / tax | `reverse_charge_third_country` |
| 84 / 85 | other § 13b services (e.g. construction) | base / tax | `reverse_charge_domestic` expenses |
| 66 | input VAT from invoices of other businesses | amount | `standard` expenses |
| 67 | input VAT from § 13b services | amount | reverse-charge expenses, `vat_deductible` share |
| 62 | paid import VAT | amount | `import_vat` |
| 61 | input VAT from intra-Community acquisitions | unsure, not v1 | |
| 63 / 64 / 59 | flat-rate input VAT, § 15a correction, new vehicles | not v1 | |
| 65 / 69 | tax from change of taxation, wrongly shown tax (§ 14c) | not v1; 69 appears when an invoice shows VAT while `small_business` | |
| 39 | credit for the special prepayment (1/11) | amount, only in the last period of a Dauerfrist year | settings + previous year |
| 83 | remaining advance payment (positive) or refund (negative) | result | computed |
| 26 | "Verrechnung des Erstattungsbetrags" | unsure, not v1 | |

The exact box for "amended return" and the whole-euro rules of the tax boxes are open questions (below). **Do not add a
box that is not in this table without the adviser's confirmation.**

### What the app calls the result

```text
output_tax  = tax(81) + tax(86) + tax(36) + tax(47) + tax(74) + tax(85)        (+ 65, 69 later)
input_tax   = 66 + 67 + 62                                                      (+ 61, 63, 64, 59 later)
Kz 83       = output_tax - input_tax - Kz 39
```

*(verify)* that this is the form's arithmetic; in particular where Kz 39 is subtracted. A positive Kz 83 is payable, a
negative one is a refund.

## Calculation rules

All arithmetic with `core/money.py` (exact `Decimal`, `q2` half-up cents), never floats; amounts leave the service as
`to_float` cents-exact numbers like the rest of the API. Dates are compared as dates.

1. **Which documents.** For the period `[from, to]`: sales per the basis above; expenses by `date` in the period. Both
   are filtered by `tax_treatment`.
2. **Sales: per document, then summed.** Each document's net per rate and its VAT per rate come from `doc_totals`
   (net per rate, `vat_amount` per rate group, rounded **per document**: this is what the customer's invoice shows).
   The return sums the nets and the document VATs of all documents of the period:
   `base_r = Σ net_{d,r}`, `booked_tax_r = Σ vat_{d,r}`. Nothing is re-rounded per period. (Per-document rounding
   differs from a period-wide computation by cents, see example 3; the invoices are the legal truth.)
3. **Form amounts.** Bases are entered in **whole euros, rounded down** (toward zero for negative totals, *verify*):
   `form_base_r = trunc(base_r)`. For Kz 81 and 86 the form computes the tax itself:
   `form_tax_r = q2(form_base_r × r / 100)`. For Kz 36, 47, 74, 85 the tax is entered; the app proposes
   `q2(form_base × rate / 100)` for the reverse-charge boxes and the booked tax for Kz 36 *(verify)*.
   The preview shows **both** (booked from the documents, and the form's), and the export uses the **form figure**
   (so what the user types matches what ELSTER displays). The difference is shown as an informational line "rounding
   difference to booked VAT: x,xx" and is stored in the return; it is not corrected anywhere (*open question*).
4. **Input VAT per line.** Each expense is read through `expense_lines(x)` (the itemised-expenses helper). Per line,
   `vat_in = q2(line_vat × vat_pct / 100)` with `vat_pct = vat_deductible` if set, else `deductible`; this is the
   function `finance()` already uses, **extracted into one shared helper** so the two can never drift. Kz 66 is the
   sum of these line amounts in cents (the supplier invoice is printed per document, but the existing per-line rule
   is kept; itemised bills are split by `money.split_gross`, so a document's lines add up to its printed VAT).
   Kleinunternehmer: no input VAT at all (the return does not exist).
5. **Reverse-charge expenses.** Per expense: `tax = q2(net_total_of_rate_group × rate / 100)`; boxes 46/47 (or 73/74,
   84/85) get `trunc(Σ net)` and `Σ tax`; Kz 67 gets `Σ q2(tax × vat_deductible / 100)`. The cost of the expense is the
   net amount only.
6. **Sales without VAT** (21, 41, 43, 45, 48, 60) have only a whole-euro base `trunc(Σ net)`.
7. **Trips and mileage** carry no VAT and never enter any box.
8. **Special prepayment (Kz 39).** Monthly filers with Dauerfrist pay `sonder = round_to_full_euro(Σ previous year's
   Kz 83 of the paid prepayments / 11)` by 10 February *(verify rounding)*; the app proposes it from the stored returns of the
   previous year (or lets the user type it when there is no history), shows a reminder, and credits it as Kz 39 in the
   **December** return (the last period of the year; a year-end return shows the credit). No special prepayment for
   quarterly filers (*verify*).
9. **Totals.** Sums are exact cents; nothing is rounded between boxes. Kz 83 is not rounded to whole euros
   (the form takes cents).
10. **Idempotence.** Recomputing a draft from unchanged data gives the same boxes. A finalised return is never
    recomputed.

## The small-business case (§ 19 UStG)

* `kleinunternehmer = true` → no advance returns. The VAT page shows: "As a small business you do not file advance VAT
  returns and charge no VAT" with the revenue limits: 25,000 € in the previous year and 100,000 € in the current year
  (law of 2025, *verify*); crossing the 100,000 € limit makes the transaction that exceeds it, and everything after,
  taxable. The existing warning in `finance()` only checks 25,000 €; the 100,000 € check is a separate sanity rule.
* Switching to regular taxation: `vat_regular_from` (a date). Documents issued before it keep `small_business`
  (frozen at issue, see "missing" item 2); the first return covers the period containing the date, with only the
  documents from that date.
* A small business that receives a § 13b service owes that VAT and has filing duties *(verify, § 19 Abs. 1 Satz 3 and
  § 18 Abs. 4a)*. **Not in v1**: the page shows a note and the user handles it with the adviser (*open question*).
* The regime on a document is frozen at issue; a document issued as `small_business` that nevertheless shows VAT is
  the § 14c case (Kz 69) and raises a sanity warning.

## Filing deadlines

* Due date: the **10th day after the end of the period**. Monthly: the 10th of the next month. Quarterly: 10 April,
  10 July, 10 October, 10 January. With Dauerfrist: one month later (the 10th of the second month after the period;
  quarterly: 10 May, 10 August, 10 November, 10 February).
* **Shift** (§ 108 Abs. 3 AO, *verify*): if the day is Saturday, Sunday or a public holiday, the deadline is the next
  working day. Holidays come from `core/holidays.holidays(year, settings.state)` (the state of the tax office, which
  the settings `state` stands for); a function `next_working_day(date, state)` loops while the date is a weekend or in
  that set. Dec 24 and Dec 31 are not public holidays.
* Payment is due on the same day; a three-day grace for payment by transfer (*Schonfrist*, not for filing) is shown
  as an information line, not used for the due date (*verify*). With a SEPA direct-debit mandate the tax office
  collects on the due date.
* The list shows each period with state `open` / `due soon` (within 7 days) / `overdue` / `filed` and the due date
  after the shift. Reminders reuse the app's existing reminder mechanism if there is one, else a dashboard line.
  Late-filing penalties are not computed.

Examples (checked against the calendar, used as tests): Q1 2026 → Fri 10 Apr 2026 (no shift); Dec 2025 → 10 Jan 2026 is
a Saturday → **Mon 12 Jan 2026**; Q3 2026 → 10 Oct 2026 is a Saturday → **Mon 12 Oct 2026**; Q1 2026 with Dauerfrist →
10 May 2026 is a Sunday → **Mon 11 May 2026**; Q1 2023 → 10 Apr 2023 is Easter Monday → **Tue 11 Apr 2023**; Dec 2026 with
Dauerfrist → Wed 10 Feb 2027; Feb 2026 → Tue 10 Mar 2026.

## Zusammenfassende Meldung (ZM) as a follow-up

Separate follow-up spec. The return only prepares the data: invoices with `eu_b2b_service` / `eu_b2b_goods_exempt` and a
customer VAT id are grouped by customer VAT id and period; `vat_returns` rows hold the box 21/41 amounts, and each
source row carries the customer id, so the later ZM spec can group them. Services only: ZM per quarter (or monthly
when thresholds are exceeded), due the 25th day after the period; goods monthly *(verify all)*. The UStVA export
shows a hint "ZM needed" when box 21 or 41 is non-zero. The invoice form requires a customer VAT id for these two
treatments (validation by format only, no network).

## Immutability, status and reopen

* `status`: `draft` → `final`. A draft is computed live on every read (and cached on "refresh"). **Finalise** stores the
  boxes, the sources and the settings snapshot and sets `finalised_at/by`. A final return is read-only: PUT/DELETE
  refused (409), no recompute.
* **Filed**: after the user has entered the figures into Mein ELSTER they can record `filed_on` (date) and a
  `reference` (the transfer ticket number, free text, escaped). `filed_on` requires `final`.
* **Reopen** (boss only, like trips: `POST /api/vat-returns/{id}/reopen`) returns a final return to draft and clears
  `finalised_at`. If `filed_on` is set, the call needs `confirm_filed: true` and writes a note "reopened after filing"
  into the return's history; the proper way after filing is an **amended** return (`POST` with `amends_id`).
* **Drift**: the list and the detail show a banner "documents changed after finalisation: +x,xx tax" when a recompute
  of a final period's contribution differs from the snapshot (computed on read, never stored). The correction is booked
  in the current period or an amended return; the app does not move anything by itself.
* The returns carry no free text from other users except `reference` and `note`; both are bounded and escaped.

## Data model additions (migration sketch, no ids)

New table `vat_returns` (strict `*In`, lenient response, every field bounded, unknown fields round-trip):

| Field | Type | Notes |
|---|---|---|
| `year`, `period_kind` (`month`/`quarter`), `period_no` | | unique with `amends_id` null |
| `from_date`, `to_date`, `due_date` | dates | `due_date` after the shift |
| `status` | `draft` / `final` | |
| `basis`, `dauerfrist`, `rules_version` | text, bool, text | snapshot of the settings used |
| `boxes` | JSON | `{ "81": { "base": 3500, "tax": 665.00, "booked_tax": 665.10 }, ... }` |
| `sources` | JSON | `[ { "box": "81", "kind": "invoice", "id": "...", "number": "RE-2026-0001", "net": 1000.00, "vat": 190.00, "date": "2026-01-14" } ]`, max 5,000 |
| `result` (Kz 83), `special_prepayment_credit` (Kz 39), `rounding_difference` | numbers | cents |
| `filed_on`, `reference`, `note` | date, text, text | |
| `amends_id` | optional id | |
| `finalised_at`, `finalised_by` | | |

Other changes: nullable `invoices.tax_treatment` and `expenses.tax_treatment` columns (text, default `standard`;
`NULL` reads as `standard`, so existing rows need no backfill); settings keys `vat_basis`, `vat_frequency`,
`dauerfrist`, `special_prepayment`, `vat_regular_from` (settings are stored without a migration). Every dialect gets
the table and the two columns using the new migration-ID scheme (`specs/migration-ids.md`); `schema.py`, the pydantic
`*In` and response models, the repository contract test and the drift test between dialects are updated as usual.
`api/openapi.json` is regenerated.

## API contract sketch

Rights: new area `vat` in `core/rights.py`. **Boss** all/all, **accountant** all/none, **employee** none/none. The
matrix doc and the every-route-as-every-role test are extended.

| Route | Purpose |
|---|---|
| `GET /api/vat-returns?year=2026` | the period list incl. periods without a record yet (`id: null`), due date, state |
| `GET /api/vat-returns/preview?year=2026&kind=month&no=1` | live figures for a period, nothing saved |
| `POST /api/vat-returns` | create a draft `{year, kind, no, amends_id?}` |
| `GET /api/vat-returns/{id}` | one return (final: the snapshot; draft: recomputed) |
| `POST /api/vat-returns/{id}/refresh` | recompute a draft |
| `POST /api/vat-returns/{id}/finalize` | freeze |
| `POST /api/vat-returns/{id}/reopen` | boss only, body `{confirm_filed?: bool}` |
| `POST /api/vat-returns/{id}/filed` | `{filed_on, reference?, note?}` |
| `DELETE /api/vat-returns/{id}` | drafts only |
| `GET /api/vat-returns/{id}/boxes/{kz}` | the sources behind one box |
| `GET /api/vat-returns/{id}/export.pdf` / `.csv` / `.json` | exports |

Preview/return response (shortened):

```json
{
  "id": null,
  "year": 2026,
  "kind": "month",
  "no": 1,
  "from_date": "2026-01-01",
  "to_date": "2026-01-31",
  "due_date": "2026-02-10",
  "status": "draft",
  "basis": "ist",
  "dauerfrist": false,
  "rules_version": "2026",
  "boxes": {
    "81": { "base": 3500, "tax": 665.0, "booked_tax": 665.1 },
    "66": { "amount": 76.0 }
  },
  "result": 589.0,
  "rounding_difference": 0.1,
  "open_receivables": 1190.0,
  "warnings": ["Invoice RE-2026-0004 has VAT 0 % and tax treatment standard"]
}
```

Errors: 404 unknown id, 409 for edits of a final return or finalising twice, 422 for a bad period (`no` out of range,
`kind` not matching the configured frequency), 403 per rights. Validation uses the strict `*In` models.

CSV (`export.csv`): UTF-8 with BOM, `;` separator, decimal comma (opens correctly in German spreadsheets); header
`kz;label;base;tax`. A second CSV `export-documents.csv` lists `box;kind;number;date;net;vat`. JSON (`export.json`)
carries the same boxes with decimal numbers and the whole return. PDF (`export.pdf`, same PDF module and fonts as
the other documents, escaped text): header (business, tax number, period, basis, due date), the box table with form
amounts, the booked amounts and the rounding line, an appendix with the source documents per box, a footer "Not a
filing. Enter these figures in Mein ELSTER." and the rules version. **No ELSTER XML.**

## UI sketch

New page **VAT return** (menu next to Finance; hidden for employees).

```text
VAT return 2026   [ Settings: monthly | Ist | Dauerfrist off ]                        (Regular taxation)
+--------+-----------------+---------+--------------+------------+----------------------------+
| Period | Due             | State   | Payable      | Documents  |                            |
+--------+-----------------+---------+--------------+------------+----------------------------+
| Jan    | Tue 10 Feb 2026 | filed   |    589,00 EUR| 3 + 1      | [Open] [PDF]               |
| Feb    | Tue 10 Mar 2026 | draft   |    440,38 EUR| 2 + 2      | [Open]                     |
| Mar    | Fri 10 Apr 2026 | open    |       -      |            | [Create draft]             |
+--------+-----------------+---------+--------------+------------+----------------------------+

Detail: February 2026 (draft)                       [Refresh] [Finalise] [Export PDF/CSV/JSON]
  Taxable sales 19 %  (81)   base   2.000 EUR   tax   380,00     [3 documents]
  Taxable sales  7 %  (86)   base   1.234 EUR   tax    86,38     [1 document]  rounding to booked 86,45: 0,07
  Input VAT           (66)                               26,00   [2 expenses]
  Remaining payment   (83)                              440,38
  Not yet taxed (open invoices, Ist basis): 1.190,00      Warnings: ...
  Each row opens the list of source documents (drill-down, links to the invoice/expense).
```

Small-business accounts see an explanation instead of the table. Banners: overdue, drift, "box not confirmed" never
shown (unconfirmed boxes are not offered). Invoice and expense forms get a **tax treatment** select (default
Standard, with the hint line from the customer's country/VAT id). Everything escaped, keyboard operable, no
information only in tooltips; text is available in German and English like the rest of the web UI.

## Worked examples (the test oracle, computed by hand)

All figures fake. "Form" means the figure the export carries (bases rounded down to whole euros, tax computed by the
form for 81 and 86), "booked" the sum of document VATs. Examples 1 to 13 are fixtures; each carries its expected boxes.

1. **Rounding of one document.** Ist, January 2026, monthly. Paid invoices: A net 1,000.00 (VAT 190.00); B net
   2,500.50, VAT `2500.50 × 0.19 = 475.095` → **475.10** (half-up). Expense net 400.00, VAT 76.00.
   Kz 81: base 3,500.50 → **3,500**; form tax `3,500 × 0.19 = 665.00`; booked 190.00 + 475.10 = 665.10.
   Kz 66 = 76.00. Kz 83 = 665.00 − 76.00 = **589.00** (booked basis would be 589.10, difference 0.10).
2. **Two rates.** February 2026. Invoices: 19 % net 2,000.00 (VAT 380.00); 7 % net 1,234.99 (VAT `86.4493` → 86.45).
   Expenses: net 100.00 at 19 % (VAT 19.00) and net 100.00 at 7 % (VAT 7.00).
   Kz 81 = 2,000 / 380.00; Kz 86 = base **1,234**, form tax `1,234 × 0.07 = 86.38` (booked 86.45); Kz 66 = 26.00;
   Kz 83 = 380.00 + 86.38 − 26.00 = **440.38** (booked basis 440.45).
3. **Per document, not per period.** Three invoices each net 10.50 at 19 %: VAT per invoice `1.995` → 2.00, booked
   **6.00**. A period-wide computation would give `31.50 × 0.19 = 5.985` → 5.99 (not used). Form: base 31, tax
   `31 × 0.19 = 5.89`. All three figures appear in the test (6.00 booked, 5.89 form, never 5.99).
4. **Ist vs. Soll.** Invoice dated 2026-03-20, net 3,000.00, VAT 570.00, paid 2026-04-08, quarterly. Ist: Q1 has no
   sale, **Q2: Kz 81 base 3,000, tax 570.00**. Soll: **Q1**. An expense dated 2026-03-28 with VAT 38.00 paid in April is
   in **Q1** (Kz 66 = 38.00) under both bases. Open receivables in the Q1 Ist preview: 3,570.00.
5. **Itemised expenses.** Hotel bill (room 160.00 @ 7 %, breakfast 30.00 @ 19 %, city tax 6.00 @ 0 %, all 100 %): VAT
   `160 × 7/107 = 10.4673` → 10.47 and `30 × 19/119 = 4.7899` → 4.79, input VAT **15.26**. Business lunch gross 100.00
   @ 19 %, 70 % cost, `vat_deductible` 100: VAT `100 × 19/119 = 15.966` → 15.97 in full. Kz 66 = 15.26 + 15.97 =
   **31.23**. (The same lunch as a plain 70 % expense would wrongly give `q2(15.97 × 0.7)` = 11.18.)
6. **Reverse charge, EU service received.** Fake supplier invoice net 200.00, no VAT, German rate 19 %:
   Kz 46 base **200**, Kz 47 tax **38.00**, Kz 67 **38.00** (100 %). Net effect on Kz 83: 0.00. The expense cost
   is 200.00, not 238.00. With `vat_deductible` 50 %: Kz 67 = **19.00**, Kz 83 changes by +19.00.
7. **Own EU B2B service plus a domestic sale.** Invoice to a fake company in another EU state with a VAT id, net
   4,000.00 (`eu_b2b_service`): Kz 21 base **4,000**, no tax, a ZM hint. Domestic invoice net 1,000.00 @ 19 %: Kz 81
   base 1,000, tax 190.00. Expense VAT 50.00. Kz 83 = 190.00 − 50.00 = **140.00**.
8. **Refund.** Sale net 500.00 @ 19 % (VAT 95.00); equipment net 1,578.95, VAT `1,578.95 × 0.19 = 300.0005` → 300.00.
   Kz 83 = 95.00 − 300.00 = **−205.00** (refund).
9. **Special prepayment.** Previous year's prepayments 6,600.00 → special prepayment `6,600 / 11` = **600.00** (due
   10 Feb 2026, Tue). December 2026 return, Dauerfrist, output tax 1,000.00, input VAT 400.00: pre-credit 600.00,
   Kz 39 = 600.00, Kz 83 = **0.00**. If the pre-credit result is 450.00: Kz 83 = **−150.00**.
10. **Deadlines.** The seven dates in the deadline section, plus: Q1 2026 Ist, filed on 10 Apr 2026 is "filed", due-soon
    state from 3 Apr 2026.
11. **Other rate.** Fake historic rate: invoice net 1,000.00 at 16 % (VAT 160.00): Kz 35 base **1,000**, Kz 36 tax
    **160.00** (booked, entered). With the 19 % line from example 1 in the same period the two boxes are independent.
12. **Credit note.** Invoice net 1,000.00 (VAT 190.00) and credit note net −300.50 (VAT `−57.095` → **−57.10**, ties away
    from zero), both paid in the period: base 699.50 → **699**, form tax `699 × 0.19 = 132.81`, booked 190.00 − 57.10 =
    132.90. A period with the credit note alone: base −300.50 → **−300** (toward zero, *verify*), tax −57.00.
13. **Small business.** `kleinunternehmer = true`, invoices net 5,000.00 with no VAT: no return, no list of periods,
    no boxes; a test asserts that the API answers `GET /api/vat-returns` with an empty list and a `not_required`
    marker, and that `preview` answers 422.
14. **Trips.** A final trip with allowance 56.00 and mileage 71.25 changes no box.

## Test plan

* **Independent oracle.** The examples above are stored as a JSON fixture (documents in, boxes out) written **by
  hand from this document**, never produced by the code under test. A second oracle in the test module recomputes
  any random data set with `fractions.Fraction` and explicit rounding helpers, not `core/money.py`, so a bug in
  `q2` or `split_gross` cannot cancel out. Hypothesis generates random documents with rates 0/7/19/16, credit notes,
  cent-level amounts, itemised expenses, both bases, and compares the two.
* **Unit:** one test per rule 1 to 10; inclusive period boundaries (31 Jan / 1 Feb, leap-year 29 Feb 2028);
  whole-euro truncation incl. negatives; draft/cancelled excluded; Ist vs. Soll (examples 4); unpaid excluded under
  Ist; half-up on `.xx5`; `vat_deductible` 100 vs. 70; reverse charge cost = net; the shared input-VAT helper gives
  identical results in `finance()` and the return; trips contribute nothing; the regime snapshot keeps an old
  small-business invoice free of VAT after the setting is switched.
* **Reconciliation:** the 12 monthly (or 4 quarterly) returns of a year sum to `finance()`'s `vat_out − vat_in`
  when the basis matches and all documents are `standard`.
* **Deadlines:** a table test of all periods for 2023 to 2028 against a hand-checked list (the dates in this spec);
  every returned date is a weekday and not a holiday of the state; Dauerfrist moves by one month before the shift.
* **API/contract:** strict bounds, round trip of unknown fields, rights matrix for the new area (every route as every
  role: boss all, accountant read only, employee 403), 401/403/404/409/422 cases, finalise twice, edit after
  finalise, reopen needs boss, reopen after filed needs the flag, delete only drafts, amended return links,
  `kind` not matching the frequency.
* **Migration:** all dialect folders in step, migrated SQLite equals `schema.py`, rows without `tax_treatment` read as
  `standard`.
* **Exports:** PDF contains every box line and the source numbers (text extraction), CSV has BOM, `;`, decimal comma
  and is parseable back to the same numbers, JSON equals the API body; text from documents is escaped in the PDF.
* **Web:** page renders for boss and accountant, not for employee; create draft, drill down, finalise, export, reopen;
  tax treatment select on invoice and expense; the seeded random monkey gets the page.
* **Reviews (Full tier):** (1) code review of the diff, (2) test review (do the fixtures fail if the code is wrong),
  (3) logic review: recompute at least the examples 1, 2, 5, 6, 9 and one random data set by hand or with the
  independent oracle; not done by a smaller model.

## Work packages (web UI and backend in parallel)

Contract first: WP0 is this spec plus the JSON fixtures of the examples (they double as mock responses for the UI).

| WP | Content | Depends on |
|---|---|---|
| WP0 | contract: `api/openapi.json` draft, fixtures from the examples, tax-adviser sign-off list | spec |
| WP1 (backend) | `vat_rules/2026.json`, box mapping, `core/vat_return.py` (pure functions, shared input-VAT helper, `doc_totals` per-rate exposure), deadlines with `next_working_day` | WP0 |
| WP2 (backend) | migration (3 dialects), `schema.py`, models (`tax_treatment`, `vat_returns`), repository contract tests, regime snapshot at issue | WP0, migration-ids |
| WP3 (backend) | service `core/service/vat_returns.py`, thin router, rights area, finalise/reopen/filed/amend | WP1, WP2 |
| WP4 (backend) | exports PDF/CSV/JSON | WP1 |
| WP5 (web) | VAT return page (list, detail, drill-down), settings fields | WP0 (mock fixtures), later real API |
| WP6 (web) | tax treatment selects on invoice and expense forms, hint from customer country/VAT id | WP0 |
| WP7 | docs: `docs/compliance.md`, user guide, roadmap row, accountant role note | WP3 |
| WP8 | tests: oracle fixtures, hypothesis, reconciliation, web tests; one full end-to-end run before release | all |

WP5 and WP6 start with WP0 against the fixtures and switch to the real API when WP3 lands. WP1, WP2 and WP5 can run
in parallel from day one.

## Research items (not in v1)

* **Direct transmission to ELSTER**: the ERiC library (licence, distribution, platform binaries), the certificate
  (*Zertifikatsdatei*) and its handling, test mode, error codes, and the data security of storing a certificate in
  the per-device config. Research only; no code, no commitment.
* ZM transmission (BZSt) and the VAT-id confirmation service.
* The annual return and its link to the sum of the advance returns.

## Open design decisions (for the implementer's report)

The implementer picks the simplest option and lists it: where `tax_treatment` lives (column vs. `extra`; the spec says
column), the exact settings keys, the page name in the menu, the shape of the sources list, caching of drafts,
reminder mechanism.

## Open questions for the tax adviser

1. **Rounding and the form amount.** Are bases entered in whole euros rounded down, also for negative totals? Is the
   tax in Kz 81/86 always the form's own computation, so that booked VAT and filed VAT differ by cents, and is that
   difference simply ignored, carried forward, or corrected? (Decides which figure is primary in the export.)
2. **Ist-Versteuerung details.** Is the default "Ist when EÜR" acceptable as a suggestion? Treatment of partial
   payments, advance payments (*Anzahlungen*), credit notes issued after payment, and the date of performance for
   Soll (`service_to` vs. invoice date). Are freelancers always allowed Ist (§ 20 Nr. 3)?
3. **Input VAT timing.** Confirm that input VAT is deducted in the period of the invoice date (or payment) for both
   bases, and the rule for invoices received late. Confirm business meals (input VAT in full, 70 % cost).
4. **Box numbers and arithmetic of the current form**: 81, 86, 35/36, 41, 43, 48, 21, 45, 60, 46/47, 73/74, 84/85,
   66, 67, 62, 39, 83, the amended-return box, and where exactly Kz 39 is credited. Which boxes the adviser sees
   used in practice for a service-only freelancer, and which of ours are wrong or missing.
5. **Frequency and Dauerfrist.** Confirm the thresholds, the new-business rule for 2026, that quarterly filers
   with Dauerfrist pay no special prepayment, the rounding of the 1/11 amount, and the application deadline.
6. **Deadline shift and grace.** Confirm § 108 AO use (state of the tax office vs. taxpayer), and the payment grace.
7. **Reverse charge.** Which treatment applies to common cases (cloud software from the EU or the US, freelancers
   abroad)? Place-of-supply questions for the sales side (`eu_b2b_service`), and whether a Kleinunternehmer receiving a
   § 13b service must file (and how).
8. **Kleinunternehmer limits and transition**: 25,000 € / 100,000 € mechanics in the year of crossing, `vat_regular_from`,
   and invoices that show VAT (Kz 69, § 14c).
9. **Corrections after filing**: amended return vs. correction in the current period; what to do with a cancelled
   or credited invoice from a filed period.
10. **Retention and proof**: is a stored PDF/JSON snapshot of the return plus the sources enough as documentation
    (GoBD), and does the adviser need an extra format (DATEV)?
