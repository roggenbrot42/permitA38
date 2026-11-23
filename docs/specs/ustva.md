# Specification: VAT advance return (Umsatzsteuer-Voranmeldung, UStVA), v2

Status: **draft for maintainer sign-off** (release "VAT + accounting", v0.4.0-alpha.7; maintainer decisions of
2026-11-23). Author: architect (Opus); v1 by a Sonnet architect, revised after a Fable review (2026-11-23).
Review tier: **Full** (money/VAT, rights, immutability). The implementer follows this document, does not decide tax
questions, and lists every choice it had to make under "Open design decisions".

**What changed from v1:** the return is derived from the **journal** ([`accounting.md`](accounting.md)), not from
documents; the treatments are cut to the release scope; third-country § 13b services go to Kz 84/85 (not 73/74);
Kz 21 and the ZM follow the performance date also under Ist; § 13b tax dates; credit notes are documents effective in
the period of the change; the special-prepayment base includes the previous Kz 39; a small business with § 13b
purchases files; entered tax boxes carry the booked tax; partial payments; frequency per year; the ZM has its own
spec ([`zm.md`](zm.md)); drift is "journal changed since finalising"; reopen only before filing.

All tax statements come from the architect's knowledge and a Fable review, **not** from the official form of the
year or a tax adviser. Every rule not certain is marked *(verify)*; it is listed in `docs/tax-adviser-review.md`
section E and shipped behind the banner "These figures are not yet confirmed by a tax adviser". All figures are fake.

## Goal

For each filing period a **complete, explainable set of figures to type into Mein ELSTER**: every box with its
amount, every amount traceable to the journal lines and documents behind it, a deadline with a reminder, and a frozen
record of what was filed. The app computes and documents; the user files.

## Scope and non-goals

In scope: monthly and quarterly returns; Soll and Ist; the treatments of the release (sales `standard` at 19 %, 7 %
or another rate, `small_business`, `eu_b2b_service`; expenses `standard`, `no_input_vat`, `reverse_charge_eu_service`,
`reverse_charge_third_country`); credit notes, write-offs, partial payments; Dauerfristverlängerung and
Sondervorauszahlung; deadlines with weekend/holiday shift; the small-business case incl. § 13b purchases; finalise,
filed, reopen before filing, amended return (Kz 10); PDF/CSV/JSON export; VAT payments.

Non-goals: ELSTER transmission (research item), the annual VAT return, OSS, goods (intra-EU supplies and
acquisitions, imports, import VAT), domestic § 13b (construction), exempt sales, margin scheme, § 15a, advance-payment
invoices, group taxation, BZSt VAT-id confirmation (network), telling the user which treatment applies (the app
suggests from country and VAT id; the user decides).

## Source of the figures

A box is a sum over **journal lines** ([`accounting.md`](accounting.md) section 6) selected by `tax_key`, `tax_kind`
and `vat_date` in the period `[from, to]` (inclusive, dates without time). Nothing reads invoices or expenses
directly. Therefore:

* Ist vs. Soll, partial payments, credit notes, write-offs and § 13b dates are decided **once**, when the posting
  engine sets `vat_date` (accounting section 9); the return only sums.
* A tax line contributes its booked tax (`-amount_cents` for output, `amount_cents` for input) and its
  `tax_base_cents`; a `base_only` line contributes its base.
* The drill-down of a box lists its lines with entry number, booking date, source document number and amounts.

The VAT rules that decide `vat_date` (summary; details and postings in accounting section 9):

| Case | `vat_date` |
|---|---|
| `standard` sale, Soll | performance date: `service_to`, else `service_from`, else invoice date *(verify: completion of the service)* |
| `standard` sale, Ist | each payment's date, pro rata (cumulative rule, accounting 9.2) |
| `eu_b2b_service` (Kz 21, ZM) | performance date, **under Soll and Ist** (§ 18b Satz 3) |
| credit note, write-off, discount | Soll: the credit note date for the part not yet paid; the paid part only when it is refunded (§ 17, UStAE 17.1 Abs. 2 *(verify)*); Ist: with the next refund. Never the original period (accounting 9.2) |
| payment after a write-off | the receipt date: the VAT is due again (§ 17 Abs. 2 Nr. 1 Satz 2) |
| input VAT (`standard`) | expense `date` (proxy for receipt of the invoice) *(verify; year-versioned because of the change announced for 2028)* |
| § 13b EU service received | `service_date` (period of performance) |
| § 13b third-country service received | `min(invoice date, last day of the month after service_date)` *(verify)* |

Unpaid invoices under Ist appear in the preview line "not yet taxed (open receivables)", computed from open items.

## Period model

* `vat_frequency` per fiscal year (`month` / `quarter` / `none`), stored in `fiscal_years` (accounting section 4), so
  a change does not invalidate earlier years. Thresholds (hint only, the tax office decides) *(verify)*: previous
  year's tax more than 9,000 € → monthly; up to 9,000 € → quarterly; up to 2,000 € → the office may exempt; § 18 Abs. 2a
  lets a business with a previous-year refund above 9,000 € choose monthly; new businesses filed monthly in the first
  two years, rule suspended 2021–2026 (whether it applies again from 2027 is an adviser question). The app shows the
  previous year's liability as a hint and warns when hint and setting disagree.
* Periods: month 1..12 or quarter 1..4; `from`/`to` calendar dates.
* At most one non-amended return per `(year, kind, no)`. An **amended return** (*berichtigte Anmeldung*, **Kz 10 = 1**)
  is a new return with `amends_id`; it carries the full corrected figures, not the difference.
* **Dauerfristverlängerung** (§ 46 UStDV): deadline one month later. Monthly filers pay a Sondervorauszahlung (below);
  quarterly filers none. Application by 10 February (monthly) or 10 April (quarterly filers' first period)
  *(verify)*; the app records `dauerfrist` per year and does not check the permission.
* **Small business** (§ 19): no regular returns (`vat_frequency` is irrelevant). Exception: when the period contains
  § 13b tax lines (`RC_EU_SVC` / `RC_3C_SVC`), a return is due for that period (§ 18 Abs. 4a) with Kz 46/47 or 84/85
  and **no** Kz 67 (no input VAT for a small business); the period is the calendar **quarter** (§ 18 Abs. 2 Satz 1,
  *(verify)*), regardless of the `vat_frequency` setting (the frequency check of the API does not apply). The return
  list shows such periods on demand.

## Boxes (Kennzahlen) and the form arithmetic

Boxes, labels, rounding modes and the tax-key mapping live in `permita38/core/vat_rules/<year>.json` (package data +
`packaging/permita38.spec`), one per form year:

```json
{ "form_year": 2026, "source": "...", "status": "to_verify",
  "base_rounding": "truncate_toward_zero",
  "boxes": [ { "kz": "81", "label_de": "...", "label_en": "...", "kind": "base", "tax_kz": null, "computed_tax_rate": "19" }, ... ],
  "map": [ { "tax_key": "S19", "tax_kind": "tax", "base_kz": "81", "tax_kz": null }, ... ],
  "result": { "kz": "83", "plus": ["81t", "86t", "36", "47", "85"], "minus": ["66", "67", "39"] },
  "klein_limit_prev": "25000", "klein_limit_cur": "100000",
  "input_vat_timing": "invoice_date" }
```

A return stores the `rules_version` it was computed with. A test fails if a year with journal lines has no file.
`KLEIN_LIMIT_*` move here from `core/tax.py`. Boxes of the release (every one *(verify)* against the form of the
year):

| Kz | Meaning | Kind | From |
|---|---|---|---|
| 81 | taxable sales at 19 % | base (whole euros); tax computed by the form | `S19` tax lines |
| 86 | taxable sales at 7 % | base; tax computed by the form | `S7` |
| 35 / 36 | taxable sales at other rates | base / tax **entered** (booked tax, cents) | `SX` |
| 21 | non-taxable services to EU businesses (§ 18b Satz 1 Nr. 2) | base | `EU_SVC` base_only |
| 46 / 47 | § 13b Abs. 1 services from EU suppliers | base / tax entered (booked) | `RC_EU_SVC` tax lines |
| 84 / 85 | other § 13b services, incl. Abs. 2 Nr. 1 third-country services | base / tax entered (booked) | `RC_3C_SVC` tax lines |
| 66 | input VAT from invoices of other businesses | amount | `VST` input lines |
| 67 | input VAT from § 13b services | amount | `RC_*` input lines |
| 39 | credit of the Sondervorauszahlung | amount (whole euros *(verify)*) | December/Q4 return with Dauerfrist |
| 83 | remaining prepayment (+) or refund (−) | result | computed |
| 10 | amended return (enter 1) | flag | `amends_id` set |

Not in the release (named so nobody adds them silently): 41, 43, 45, 48, 60 (sales), 73/74 (GrESt sales only), 89/93,
91, 61, 62, 63, 64, 59, 65, 69, 50/37 (§ 17 uncollectible information), 87, 26 (SEPA mandate revocation), 29
(set-off of a refund). **Do not add a box without the adviser's confirmation.**

**Form amounts.**
1. Base boxes: `form_base = round(Σ tax_base_cents / 100, base_rounding)`; default rule: truncate to whole euros
   **toward zero** (also for negative totals *(verify)*).
2. Kz 81/86: the form computes `form_tax = q2(form_base × rate / 100)`.
3. Kz 36/47/85: the **booked tax** (Σ of the lines, cents) is entered. Kz 66/67: booked, cents.
4. Kz 83 = 81t + 86t + 36 + 47 + 85 − 66 − 67 − 39 (in this order on the form; +65/+69 not in the release)
   *(verify)*. Cents, not rounded.
5. The preview shows both **form** and **booked** figures; the export carries the form figures. The difference
   (booked − form for 81/86) is stored as `rounding_difference`; it stays on the VAT accounts until the annual return
   (accounting 9.6).

## Sondervorauszahlung (special prepayment)

Monthly filers with Dauerfrist only. Amount for year Y (§ 47 UStDV) *(verify all)*:

```text
base   = Σ Kz 83 of the returns of Y−1 (as filed, amended returns replace the originals) + Kz 39 of Y−1
         (a partial previous year is annualised: base × 12 / number of months with a business; a business starting
         in Y: the user enters the estimate)
amount = max(base, 0) / 11, rounded per rules data (default: to cents)
```

The app proposes it from the stored returns (or lets the user enter it), shows a reminder for 10 February (shifted),
records its payment (accounting 9.6) and credits it as **Kz 39** in the December return (or the last period of the
year, or the last period before the business ends). When `dauerfrist` is revoked during the year, the credit falls
into the last period with the extension *(verify)*.

## Deadlines

* Due: the 10th day after the end of the period (monthly: 10th of the next month; quarterly: 10 Apr, 10 Jul, 10 Oct,
  10 Jan). Dauerfrist: one month later.
* Shift (§ 108 Abs. 3 AO): Saturday, Sunday or a public holiday of the state of the tax office (settings `state`) →
  the next working day. New helper `holidays.next_working_day(date, state)`. 24 and 31 December are not holidays.
* Payment grace of three days for transfers (§ 240 Abs. 3 AO) is an information line, never the due date.
* List states: `open`, `due soon` (7 days), `overdue`, `final`, `filed`. The Today page shows a line for due-soon
  and overdue returns and ZMs (no other reminder mechanism exists).
* Test dates (checked against the calendar): Q1 2026 → Fri 10 Apr 2026; Dec 2025 → Mon 12 Jan 2026; Q3 2026 → Mon
  12 Oct 2026; Q1 2026 + Dauerfrist → Mon 11 May 2026; Q1 2023 → Tue 11 Apr 2023 (Easter Monday); Dec 2026 +
  Dauerfrist → Wed 10 Feb 2027; Feb 2026 → Tue 10 Mar 2026.

## Status, finalise, filed, reopen, amend, drift

* `draft`: computed from the journal on every read (cheap; no cache, no refresh route).
* **Finalise** (boss, accountant): stores the boxes, the form and booked figures, the source line ids, the
  `fiscal_years` snapshot and `rules_version`, `finalised_at/by`, and **locks the months of the period**
  (accounting section 11; a gap of unlocked earlier months is locked too, after a confirmation listing them).
* **Filed**: `filed_on` (date) and `reference` (transfer ticket, ≤ 100 chars) and `note` (≤ 500); requires `final`.
* **Reopen** (boss, accountant): only `final` and not filed; back to `draft`. The months stay locked (no unlock).
  After filing there is no reopen; the correction is an **amended return**.
* **Amended return**: `POST` with `amends_id` of a filed return of the same period; computed from the journal (which
  now contains the late lines), Kz 10 = 1; it replaces the original in the list and in the Sondervorauszahlung base.
* **Drift**: the journal is append-only, so a final return differs from a recompute only through lines added later
  — exactly the **allowed drift cases** of [`accounting.md`](accounting.md) section 11: a Soll invoice issued after
  the return for a performance in the period, an Ist payment dated in the period but entered later, a § 13b tax date
  in the period, an `eu_b2b_service` performance date in the period. Late bills never drift (their input VAT moves to
  the booking month). The detail shows "N entries after finalising change the tax by x,xx" with the lines (`created_at >
  finalised_at`); the user files an amended return. Nothing is moved automatically.

## Data model (`vat_returns`, migration in the accounting migration)

| Field | Type | Notes |
|---|---|---|
| `id`, `year`, `period_kind` (`month`/`quarter`), `period_no` | | unique (`year`,`period_kind`,`period_no`) where `amends_id` is null and the row is not replaced |
| `from_date`, `to_date`, `due_date` | date | due date after the shift |
| `status` | `draft` / `final` | CHECK |
| `basis`, `frequency`, `dauerfrist`, `small_business`, `rules_version` | | snapshot from `fiscal_years` at finalise |
| `boxes` | JSON | `{ "81": {"base": 3500, "tax": "665.00", "booked_base": "3500.50", "booked_tax": "665.10"}, "66": {"amount": "76.00"} }` |
| `source_lines` | JSON | ids of the journal lines (max 20,000; above that the return stores the per-box sums and a hash of the id list, and the drill-down queries the journal by period) |
| `result` (Kz 83), `special_prepayment_credit` (Kz 39), `rounding_difference` | decimal | |
| `filed_on`, `reference`, `note` | | bounded, escaped |
| `amends_id`, `replaced_by` | id | |
| `external` | bool | a return filed outside the app before the accounting start of the year was set up (accounting section 16 step 5b): `boxes` hold the entered figures (at least Kz 83), drift compares them with the journal |
| `finalised_at`, `finalised_by` | | |

Strict `*In` models, lenient responses, unknown fields round-trip; all dialects, `schema.py`, contract and drift
tests, `api/openapi.json`.

## API

Rights: area `vat`; boss and accountant (A, A), employee (N, N) ([`accounting.md`](accounting.md) section 17).

| Route | Purpose |
|---|---|
| `GET /api/vat-returns?year=2026` | the period list incl. periods without a record (`id: null`), due date, state; for a small business only periods with § 13b lines and a `not_required` marker otherwise |
| `GET /api/vat-returns/preview?year=2026&kind=month&no=1` | live figures for a period, nothing saved |
| `POST /api/vat-returns` | create a draft `{year, kind, no, amends_id?}` |
| `GET /api/vat-returns/{id}` | one return (final: the snapshot plus drift; draft: computed) |
| `POST /api/vat-returns/{id}/finalize` | freeze + lock |
| `POST /api/vat-returns/{id}/reopen` | final and not filed only |
| `POST /api/vat-returns/{id}/filed` | `{filed_on, reference?, note?}` |
| `DELETE /api/vat-returns/{id}` | drafts only |
| `GET /api/vat-returns/{id}/boxes/{kz}` | the lines behind one box |
| `POST /api/vat-returns/{id}/payment` | record the payment/refund of Kz 83 (accounting 9.6) |
| `GET/PUT /api/vat/special-prepayment?year=2026` | proposal, user value, payment |
| `GET /api/vat-returns/{id}/export.pdf` / `.csv` / `.json` | exports |

Errors: 404, 409 (finalise twice, change a final return, reopen after filing, amend an unfiled return), 422 (period
out of range, `kind` not matching the year's frequency except the small-business § 13b quarters, accounting not
initialised for the year), 403.

**Exports.** CSV: UTF-8 with BOM, `;`, CRLF, **decimal comma** (a new formatter next to `transfer/csvout.py` that
keeps its formula-injection guard), header `kz;label;base;tax`; a second file `export-lines.csv`
`kz;entry_no;booking_date;document;base;tax`. JSON: the return as the API returns it. PDF (`core/pdf.py`, escaped
text): header (business, tax number, period, basis, due date), the box table with form figures, booked figures and
the rounding line, the adviser banner while rules are unconfirmed, an appendix of source lines per box, footer "Not a
filing. Enter these figures in Mein ELSTER." and the rules version. No ELSTER XML.

## UI sketch

Page **VAT** (menu next to Money; area `vat`), tabs *Returns* and *ZM* ([`zm.md`](zm.md)).

```text
VAT 2026   monthly · Soll · Dauerfrist off · rules 2026 (not confirmed by a tax adviser)
Period | Due              | State    | Payable     | Lines | 
Jan    | Tue 10 Feb 2026  | filed    |  589,00 EUR | 4     | [Open] [PDF]
Feb    | Tue 10 Mar 2026  | final    |  440,38 EUR | 4     | [Open] [Mark filed]
Mar    | Fri 10 Apr 2026  | open     |  (preview)  |       | [Create draft]

Detail February 2026 (final, finalised 2026-03-04)        [Export PDF/CSV/JSON] [Record payment]
  Taxable sales 19 % (81)  base 2.000 EUR  tax 380,00                  [2 lines]
  Taxable sales  7 % (86)  base 1.234 EUR  tax  86,38  booked 86,45     [1 line]
  Input VAT          (66)                  26,00                        [2 lines]
  Remaining payment  (83)                 440,38
  ⚠ 1 entry after finalising changes the tax by +19,00 → [Create amended return]
```

Invoice and expense forms get the **tax treatment** select (default from the customer's country/VAT id, or the
supplier's `vat_mode`; always visible, never silent) and the expense form a **service date** field for § 13b.
Everything escaped, keyboard operable, German and English.

## Worked examples (oracle; booked = sum of lines, form = what the export carries)

1. **Rounding, Ist, January.** Paid invoices: A net 1,000.00 (VAT 190.00); B net 2,500.50 (VAT `475.095` → 475.10).
   Expense VAT 76.00. Kz 81 base 3,500.50 → **3,500**, form tax **665.00**, booked 665.10; Kz 66 **76.00**; Kz 83
   **589.00**; rounding difference 0.10.
2. **Two rates.** 19 % net 2,000.00 (380.00); 7 % net 1,234.99 (`86.4493` → 86.45); expenses VAT 19.00 + 7.00. Kz 81
   2,000 / 380.00; Kz 86 **1,234** / form **86.38** (booked 86.45); Kz 66 **26.00**; Kz 83 **440.38**.
3. **Per document, not per period.** Three invoices of net 10.50 @ 19 %: VAT per invoice 2.00, booked **6.00**; form
   base 31, tax **5.89**; a period-wide computation (5.99) is never used.
4. **Ist vs. Soll.** Invoice 2026-03-20 (no service date), net 3,000.00, VAT 570.00, paid 2026-04-08, quarterly. Ist:
   **Q2** Kz 81 3,000 / 570.00; Soll: **Q1**. Expense 2026-03-28 VAT 38.00, paid in April: Kz 66 **38.00 in Q1** under
   both. Open receivables in the Q1 Ist preview: 3,570.00.
5. **Itemised expenses.** Hotel bill (gross amounts): room 160.00 @ 7 % (VAT 10.47), parking 30.00 @ 19 % (4.79), city
   tax 6.00 @ 0 %; input VAT **15.26**. Business lunch gross 107.00 @ 7 % *(verify: food 7 % from 2026)*, 70 % cost,
   `vat_deductible` 100: VAT **7.00** in full (a plain 70 % rule would give 4.90). Kz 66 = **22.26**.
6. **§ 13b EU service received** (accounting A5). Net 200.50, service 2026-05-20: May Kz 46 **200**, Kz 47 **38.10**
   (booked), Kz 67 **38.10**; effect on Kz 83 **0.00**. With `vat_deductible` 50: Kz 67 **19.05**, Kz 83 **+19.05**.
7. **§ 13b third-country service.** Same net, service 2026-05-20, invoice 2026-07-10: **June** Kz 84 **200**, Kz 85
   **38.10**, Kz 67 **38.10**.
8. **EU B2B service plus a domestic sale.** `eu_b2b_service` net 4,000.00 (performance in March): Kz 21 **4,000**;
   domestic net 1,000.00 @ 19 %: Kz 81 1,000 / 190.00; expense VAT 50.00; Kz 83 **140.00**. **Ist variant:** the EU
   invoice is unpaid at the end of March and Kz 21 is still **4,000 in March**; the domestic one counts when paid.
9. **Refund.** Sale net 500.00 @ 19 % (95.00); equipment net 1,578.95 (VAT `300.0005` → 300.00): Kz 83 **−205.00**.
10. **Sondervorauszahlung.** First year with Dauerfrist: Σ Kz 83 of 2025 = 6,600.00, Kz 39 of 2025 = 0 → **600.00**
    (due Tue 10 Feb 2026). December 2026: output 1,000.00 (Kz 81 base 5,251 → 997.69; Kz 86 base 33 → 2.31), input
    400.00, Kz 39 600 → Kz 83 **0.00**; with input 550.00 → **−150.00**. Second year: Σ Kz 83 of 2026 = 6,000.00 + Kz 39 of 2026 = 600.00 → base 6,600.00 →
    **600.00**. Partial year: business from 1 April 2025, Σ Kz 83 Apr–Dec = 4,500.00 → annualised 6,000.00 →
    **545.45**.
11. **Other rate.** Fake historic rate 16 %: net 1,000.00 (160.00): Kz 35 **1,000**, Kz 36 **160.00**.
12. **Credit note in the period of the change.** Soll, March: invoice net 1,000.00 (190.00) and a credit note against
    an unpaid January invoice, net 300.50 (VAT `−57.095` → −57.10): Kz 81 base 699.50 → **699**, form tax **132.81**, booked
    132.90. A period with the credit note alone: base −300.50 → **−300** (toward zero *(verify)*), form tax
    **−57.00**. January is never changed.
13. **Small business.** Invoices net 5,000.00 without VAT: no return, list marked `not_required`, preview 422. With the
    § 13b EU service of example 6 in May: a return for Q2 2026 with Kz 46 **200**, Kz 47 **38.10**, no Kz 67, Kz 83 **38.10**.
14. **Trips.** Allowance 56.00 and mileage 71.25 change no box.
15. **Partial payments, Ist** (accounting A2). March: Kz 81 base 579.71 → **579**, form **110.01** (booked 110.15);
    Kz 86 289.85 → **289**, form **20.23** (booked 20.29); Kz 83 **130.24**. April: Kz 81 **420** / **79.80** (79.85);
    Kz 86 **210** / **14.70** (14.71); Kz 83 **94.50**.
16. **Drift and amended return.** January (Soll) filed with Kz 83 589.00. On 2026-02-20 an invoice with
    `service_to` 2026-01-28, net 100.00 @ 19 %, is issued: its VAT line has `vat_date` 2026-01-28 (January is locked,
    allowed for an automatic Soll posting), drift **+19.00**; the amended January return (Kz 10 = 1) has Kz 81 base
    3,600, form tax **684.00**, Kz 83 **608.00** (from example 1's figures under Soll with the same dates).
17. **Deadlines.** The seven dates of the deadline section; Q1 2026 filed on 10 Apr 2026 is `filed`, `due soon` from
    3 Apr 2026.

## Test plan

* **Oracle:** examples 1–17 as journal-level fixtures (lines in, boxes out) in
  `tests/backend/fixtures/accounting_oracle.json`, written by hand from this document.
* **Independent oracle:** hypothesis generates journal-producing events under all four bases; a second computation
  with `fractions.Fraction` and its own rounding helpers recomputes the boxes from the event list (not from the
  journal) and must agree.
* **Unit:** box mapping from the rules file; rounding modes incl. negatives; inclusive period boundaries (31 Jan /
  1 Feb, 29 Feb 2028); Kz 39 only in the last period; small-business on-demand periods; frequency per year.
* **Reconciliation:** Σ of the 12 monthly returns of a year = Σ VAT lines of the year by `vat_date`; with
  `standard` documents only and matching bases, it also equals the old `finance()` totals.
* **Status:** finalise locks the months; finalise twice 409; reopen after filing 409; amend requires a filed original;
  drift appears only for lines created after finalising.
* **Deadlines:** all periods 2023–2028 against a hand-checked list; every due date is a weekday and not a holiday.
* **API/rights:** strict bounds, every route as every role, error codes.
* **Exports:** PDF text contains every box and source line; CSV BOM, `;`, decimal comma, formula guard, parses back;
  JSON equals the API body.
* **Web:** page for boss and accountant, not employee; create, drill down, finalise, mark filed, amend, export; the
  treatment select and the service date; the random monkey reaches the page.
* **Reviews (Full tier):** code, tests, logic (recompute examples 1, 2, 6, 10, 12, 15, 16 and one random set).

## Open design decisions (for the implementer's report)

The page layout of the drill-down, the exact settings labels, the shape of the box JSON beyond the fields above.

## Questions for the tax adviser (also in `docs/tax-adviser-review.md` section E)

1. Whole-euro rule for bases, also negative totals; which figure is primary when form and booked tax differ by cents,
   and is leaving the difference to the annual return correct?
2. Credit notes and storno: period of the correction under Soll (agreement vs. refund) and under Ist; when an amended
   return is required instead of a current-period correction.
3. Monthly filing for new businesses from 2027; the 2028 change of input VAT timing for invoices of Ist suppliers.
4. Small business with § 13b purchases: filing period (month?), paragraph after the 2025 reform; ZM duty for a small
   business selling EU B2B services.
5. Invoice date as proxy for invoice receipt (input VAT) and the performance date for Soll (`service_to`).
6. § 13b dates: EU services at the performance period; third-country services at `min(invoice, end of following
   month)`.
7. Sondervorauszahlung: rounding, the base with refunds and amended returns, revocation during the year, Kz 39 in
   whole euros?
8. Box numbers and arithmetic of the 2026 form for every box in the table, incl. Kz 10 and the order of Kz 39.
9. Is finalising a return a sensible trigger for the GoBD period lock?
