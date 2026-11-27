# Specification: SKR03 and SKR04 chart files for 2026

Status: architect decision (Opus), 2026-11-27. The tax adviser has not checked it yet, so the files ship with
`"status": "to_verify"` and the adviser banner shows. Contract for the files
`permita38/core/charts/skr03-2026.json` and `skr04-2026.json` (format: [`accounting.md`](accounting.md) section 5).

## 1. Sources

| Chart | Document | Edition | sha256 of the PDF |
|---|---|---|---|
| SKR03 | DATEV Kontenrahmen SKR 03, Art.-Nr. 11174 ("Prozessgliederungsprinzip") | "Gültig für 2026", Stand Januar 2026, 40 pages | `fa93f12a1a520dddc81fd0cec5179a116c885cdea4a867de1bd457b57ce8cbbf` |
| SKR04 | DATEV Kontenrahmen SKR 04, Art.-Nr. 11175 ("Abschlussgliederungsprinzip") | "Gültig für 2026", 40 pages | `50ae33016c8cf717fc0615dbeea727af612ed9d0bdadd7bd44de752f76266518` |
| EÜR lines | BMF, Anlage EÜR 2026 (BMF letter of 2026-09-01, form 2026AnlEÜR801-803) | August 2026, 44 pages | `03d3033acc5c5a126729e58743128596ae39193759c55b207872da9432b83a8f` |

The PDFs are DATEV's copyright and **never go into the repository**. The chart files hold only the facts the app
needs: the account numbers of the roles below, their names and classes, and the app's own mapping. The `source` field
reads `"DATEV Kontenrahmen SKR 03 (Art.-Nr. 11174), gültig für 2026; role mapping: architect, to be confirmed by the
tax adviser"` (SKR 04 and 11175 accordingly).

Not obtained: the DATEV "Steuerschlüssel-Tabelle 2026 – SKR03/SKR04" (Hilfe-Center document 0907048, an xlsx behind
a JavaScript-only page). It is needed to confirm the VAT accounts DATEV posts to itself and the § 13b BU keys
(section 6).

## 2. Rules for choosing an account

1. Never an account with the function **R** ("may only be booked once another function has been assigned").
2. `automatic` is set **only** when the PDF prints **AM** or **AV** before the account **and** an app tax key matches
   (`S19`, `S7`, `KU`, `EU_SVC`). An AM/AV account without a matching key is used only where this spec says so, with
   `automatic: null`.
3. Expense accounts are accounts **without a function**, so the BU keys of `datev/2026.json` carry the input VAT.
4. Footnote 18: `4670` / `6670` "Reisekosten Unternehmer" must not be used by EÜR users. The differentiated accounts
   are used instead.
5. Footnote 22 ("nur Einzelunternehmen") accounts are not used; the general accounts serve sole traders and
   partnerships alike.
6. Accounts marked **EÜR** (to be booked only under § 4 Abs. 3 EStG) are used only for roles that only the EÜR basis
   posts (`vat_soll_clearing_*`, `input_vat_clearing`, `rc_vat_nondeductible`, `vat_paid_private`).
7. Names are copied from the PDF with wrapped hyphens joined and footnote markers removed (e.g. `Sonstige
   betriebliche Aufwendungen`, not `Aufwendun-gen` or `…22)`).
8. `accounts` lists exactly the role accounts, each once, in ascending order. No further accounts in v1: a manual
   entry on an account without a role cannot be exported to DATEV (`datev_export.py`, "has no role in the chart").
9. Each role has its own account, except the `shared` groups of section 5. A manual line without a role on a shared
   account is refused by the DATEV export with 422. That is acceptable for these groups because manual entries do
   not use them.

## 3. Roles

`class`: `b` = balance, `pl` = P&L. `auto` = the `automatic` value. EÜR lines: section 4.

| Role | SKR03 | SKR04 | Name (SKR03 / SKR04 where different) | class | auto |
|---|---|---|---|---|---|
| `bank` | 1200 | 1800 | Bank | b | – |
| `cash` | 1000 | 1600 | Kasse | b | – |
| `receivables` | 1410 | 1210 | Forderungen aus Lieferungen und Leistungen ohne Kontokorrent | b | – |
| `payables` | 1610 | 3310 | Verbindlichkeiten aus Lieferungen und Leistungen ohne Kontokorrent | b | – |
| `customer_credit` | 1700 | 3500 | Sonstige Verbindlichkeiten | b | – |
| `payables_employee` | 1740 | 3720 | Verbindlichkeiten aus Lohn und Gehalt | b | – |
| `revenue_s19` | 8400 | 4400 | Erlöse 19 % USt | pl | S19 |
| `revenue_s7` | 8300 | 4300 | Erlöse 7 % USt | pl | S7 |
| `revenue_sx` | 8200 | 4200 | Erlöse | pl | – |
| `revenue_s0` | 8290 | 4290 | Erlöse 0 % USt (AM in DATEV; no app key, DATEV computes 0 €) | pl | – |
| `revenue_ku` | 8192 | 4184 | Steuerfreie Erlöse Kleinunternehmer nach § 19 Abs. 1 UStG | pl | KU |
| `revenue_eu_svc` | 8336 | 4336 | Erlöse aus im anderen EU-Land steuerpflichtigen sonstigen Leistungen, für die der Leistungsempfänger die Umsatzsteuer schuldet | pl | EU_SVC |
| `vat_out_s19` | 1776 | 3806 | Umsatzsteuer 19 % | b | – |
| `vat_out_s7` | 1771 | 3801 | Umsatzsteuer 7 % | b | – |
| `vat_out_sx` | 1770 | 3800 | Umsatzsteuer | b | – |
| `vat_not_due_s19` | 1766 | 3816 | Umsatzsteuer nicht fällig 19 % | b | – |
| `vat_not_due_s7` | 1761 | 3811 | Umsatzsteuer nicht fällig 7 % | b | – |
| `vat_not_due_sx` | 1760 | 3810 | Umsatzsteuer nicht fällig | b | – |
| `vat_soll_clearing_s19` | 1371 | 1486 | Verrechnungskonto Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam | b | – |
| `vat_soll_clearing_s7` | 1371 | 1486 | (shared, section 5) | b | – |
| `vat_soll_clearing_sx` | 1371 | 1486 | (shared, section 5) | b | – |
| `input_vat_s19` | 1576 | 1406 | Abziehbare Vorsteuer 19 % | b | – |
| `input_vat_s7` | 1571 | 1401 | Abziehbare Vorsteuer 7 % | b | – |
| `input_vat_sx` | 1570 | 1400 | Abziehbare Vorsteuer | b | – |
| `rc_vat_out_eu` | 1787 | 3837 | Umsatzsteuer nach § 13b UStG 19 % | b | – |
| `rc_vat_out_3c` | 1787 | 3837 | (shared, section 5) | b | – |
| `rc_input_vat` | 1577 | 1407 | Abziehbare Vorsteuer nach § 13b UStG 19 % | b | – |
| `vat_prepayments` | 1780 | 3820 | Umsatzsteuer-Vorauszahlungen | b | – |
| `vat_special_prepayment` | 1781 | 3830 | Umsatzsteuer-Vorauszahlungen 1/11 | b | – |
| `vat_previous_year` | 1790 | 3841 | Umsatzsteuerverbindlichkeiten Vorjahr | b | – |
| `input_vat_clearing` | 1580 | 1480 | Gegenkonto Vorsteuer § 4 Abs. 3 EStG | b | – |
| `rc_vat_nondeductible` | 1692 | 3612 | Neutralisierung aufwandswirksamer Sachverhalte für § 4 Abs. 3 EStG | b | – |
| `vat_paid_private` | 1693 | 3613 | Ergebnisneutrale Sachverhalte für § 4 Abs. 3 EStG | b | – |
| `datev_transfer` | 1792 | 3630 | Sonstige Verrechnungskonten (Interimskonten) / (Interimskonto) | b | – |
| `expense_software` | 4806 | 6495 | Wartungskosten für Hard- und Software | pl | – |
| `expense_hardware` | 4855 | 6260 | Sofortabschreibung geringwertiger Wirtschaftsgüter / Sofortabschreibungen geringwertiger Wirtschaftsgüter | pl | – |
| `expense_office` | 4210 | 6310 | Miete (unbewegliche Wirtschaftsgüter) | pl | – |
| `travel_costs` | 4673 | 6673 | Reisekosten Unternehmer Fahrtkosten | pl | – |
| `expense_lodging` | 4676 | 6680 | Reisekosten Unternehmer Übernachtungsaufwand und Reisenebenkosten | pl | – |
| `expense_material` | 3000 | 5100 | Einkauf Roh-, Hilfs- und Betriebsstoffe | pl | – |
| `meals_deductible` | 4650 | 6640 | Bewirtungskosten | pl | – |
| `meals_nondeductible` | 4654 | 6644 | Nicht abzugsfähige Bewirtungskosten | pl | – |
| `expense_gifts` | 4630 | 6610 | Geschenke abzugsfähig ohne § 37b EStG | pl | – |
| `expense_marketing` | 4600 | 6600 | Werbekosten | pl | – |
| `expense_insurance` | 4360 | 6400 | Versicherungen | pl | – |
| `expense_phone` | 4920 | 6805 | Telefon | pl | – |
| `expense_training` | 4945 | 6821 | Fortbildungskosten | pl | – |
| `expense_subcontractors` | 3100 | 5900 | Fremdleistungen | pl | – |
| `expense_fees` | 4970 | 6855 | Nebenkosten des Geldverkehrs | pl | – |
| `expense_other` | 4900 | 6300 | Sonstige betriebliche Aufwendungen | pl | – |
| `bad_debts` | 2400 | 6930 | Forderungsverluste / Forderungsverluste (übliche Höhe) (as printed) | pl | – |
| `cash_discount_granted` | 8700 | 4700 | Erlösschmälerungen | pl | – |
| `trade_tax_expense` | 4320 | 7610 | Gewerbesteuer | pl | – |
| `trade_tax_provision` | 0956 | 3035 | Gewerbesteuerrückstellung nach § 4 Abs. 5b EStG | b | – |
| `corporate_tax_expense` | 2200 | 7600 | Körperschaftsteuer | pl | – |
| `corporate_tax_provision` | 0963 | 3040 | Körperschaftsteuerrückstellung | b | – |
| `tax_refunds` | 2281 | 7641 | Gewerbesteuernachzahlungen und Gewerbesteuererstattungen für Vorjahre nach § 4 Abs. 5b EStG | pl | – |
| `private_withdrawal` | 1800 | 2100 | Privatentnahmen allgemein | b | – |
| `private_deposit` | 1890 | 2180 | Privateinlagen | b | – |
| `equity` | 0880 | 2010 | Variables Kapital | b | – |
| `profit_carryforward` | 0880 | 2010 | (shared with `equity`, section 5) | b | – |
| `opening_balance` | 9000 | 9000 | Saldenvorträge, Sachkonten | b | – |
| `closing_balance` | 9000 | 9000 | (shared with `opening_balance`, section 5) | b | – |

Names: the implementer takes each name from the PDF text (`pdftotext -layout`), following rule 7. Where the two
charts print a name differently, each file gets its own chart's wording.

## 4. Categories and EÜR lines

`categories` maps the expense categories of the web dialog (`EXP_CATS` in `web/app2.js`, exact strings) to roles:

| Category | Role |
|---|---|
| Software & subscriptions | `expense_software` |
| Hardware & equipment | `expense_hardware` |
| Office & coworking | `expense_office` |
| Travel | `travel_costs` |
| Transportation | `travel_costs` |
| Accommodation | `expense_lodging` |
| Material | `expense_material` |
| Business meals | `meals_deductible` |
| Gifts | `expense_gifts` |
| Marketing | `expense_marketing` |
| Insurance | `expense_insurance` |
| Phone & internet | `expense_phone` |
| Training | `expense_training` |
| Subcontractors | `expense_subcontractors` |
| Fees & bank | `expense_fees` |
| Other | `expense_other` |

`eur_line` for every P&L role (balance roles: `null`). `line` is the text below, verbatim. `deductible` as given. The
Anlage EÜR 2026 numbering is used.

| Roles | `line` | deductible |
|---|---|---|
| `revenue_s19`, `revenue_s7`, `revenue_sx`, `cash_discount_granted` | `Z. 15 Umsatzsteuerpflichtige Betriebseinnahmen` | true |
| `revenue_s0`, `revenue_eu_svc` | `Z. 16 Umsatzsteuerfreie, nicht umsatzsteuerbare Betriebseinnahmen, 0 %, § 13b UStG` | true |
| `revenue_ku` | `Z. 12 Betriebseinnahmen als Kleinunternehmer` | true |
| `expense_material` | `Z. 29 Waren, Rohstoffe und Hilfsstoffe` | true |
| `expense_subcontractors` | `Z. 30 Bezogene Fremdleistungen` | true |
| `expense_hardware` | `Z. 37 Aufwendungen für geringwertige Wirtschaftsgüter` | true |
| `expense_office` | `Z. 40 Miete/Pacht für Geschäftsräume` | true |
| `expense_phone` | `Z. 44 Aufwendungen für Telekommunikation` | true |
| `expense_lodging` | `Z. 45 Übernachtungs- und Reisenebenkosten bei Geschäftsreisen` | true |
| `expense_training` | `Z. 46 Fortbildungskosten` | true |
| `expense_insurance` | `Z. 50 Beiträge, Gebühren, Abgaben und Versicherungen` | true |
| `expense_software` | `Z. 51 Laufende EDV-Kosten` | true |
| `expense_marketing` | `Z. 55 Werbekosten` | true |
| `expense_fees`, `expense_other`, `bad_debts` | `Z. 61 Übrige unbeschränkt abziehbare Betriebsausgaben` | true |
| `expense_gifts` | `Z. 63 Geschenke (abziehbar)` | true |
| `meals_deductible` | `Z. 64 Bewirtungsaufwendungen (abziehbar)` | true |
| `meals_nondeductible` | `Z. 64 Bewirtungsaufwendungen (nicht abziehbar)` | false |
| `travel_costs` | `Z. 71 Sonstige tatsächliche Fahrtkosten` | true |
| `trade_tax_expense`, `tax_refunds` | `Z. 68 Gewerbesteuer (nicht abziehbar)` | false |
| `corporate_tax_expense` | `null` (no EÜR for a GmbH) | – |

The implementer checks each `Z.` number against the form text (`euer-full.txt`). The line of Z. 50 is taken from
the form; it wraps in the PDF. On a mismatch the implementer stops and reports; it does not choose another line.

## 5. Shared groups

`"shared": [["vat_soll_clearing_s19", "vat_soll_clearing_s7", "vat_soll_clearing_sx"], ["rc_vat_out_eu",
"rc_vat_out_3c"], ["equity", "profit_carryforward"], ["opening_balance", "closing_balance"]]`. Reasons:

* EÜR Soll clearing: DATEV has no per-rate counterpart. The app keeps the rate on the role and on the tax line.
* § 13b: DATEV books the tax owed for EU and third-country services on the same 19 % account. The UStVA boxes come
  from the lines' tax keys, not from the account.
* A sole trader's profit is closed into capital.
* DATEV carries balances forward on 9000 and has no closing-balance account.

## 6. Open points (tax adviser brief, TA-46..TA-50)

* **TA-46 VAT accounts and § 13b BU keys.** Confirm against the Steuerschlüssel-Tabelle 2026 that DATEV posts the
  VAT of BU 2/3/8/9 and of the § 13b keys to the accounts of section 3. Fill `RC_EU_SVC` / `RC_3C_SVC` in
  `datev/2026.json` (still `null`). The maintainer downloads the xlsx from the DATEV Hilfe-Center (document 0907048)
  in a browser.
* **TA-47 Category accounts.**
  * Software subscriptions on 4806/6495 (EÜR Z. 51), or licence account 4964/6837 (GewSt add-back)?
  * "Office & coworking" as rent (Z. 40), or office supplies 4930/6815 (Z. 52)?
  * Hardware as GWG (only up to the GWG limit; above it the asset needs depreciation, which the app does not do)?
* **TA-48 Travel.** Trips post all items to `travel_costs` (Fahrtkosten, Z. 71). Hotels belong on Z. 45 and per-diem
  allowances on Z. 65. Follow-up WP **C2**: trip postings split by item category into `travel_costs`,
  `expense_lodging` and a new `travel_per_diem` role (4674/6674, Z. 65). This changes code (E15), not the chart alone.
* **TA-49 Legal form.** The owner roles fit sole traders (`freiberufler`, `gewerbe`). A GmbH needs `equity` →
  the "Gezeichnetes Kapital" account (SKR03 0800, SKR04 to look up), `profit_carryforward` → 0860/2970, and no private accounts. Follow-up WP **C3**:
  per-company-type role overrides in the chart file. Until then the accrual year closing of a GmbH posts to the
  sole-trader accounts.
* **TA-50 EÜR clearing accounts.** `input_vat_clearing` → 1580/1480 "Gegenkonto Vorsteuer § 4 Abs. 3 EStG",
  `rc_vat_nondeductible` → 1692/3612, `vat_paid_private` → 1693/3613, Soll clearing → 1371/1486. Are these the
  accounts the adviser expects in a DATEV import of an EÜR client?

## 7. Tests (work package C1)

* Both files load through `chart_for` (no warning for 2026) and pass the existing validator.
* An oracle test holds the table of section 3 as literal `(role, skr03, skr04, automatic)` tuples and the category
  table of section 4, and compares them with the files. It is written from this spec, not from the files.
* Every account is a 4-digit string. `accounts` equals the set of role accounts, sorted, without duplicates.
* The reviewer (logic pass) checks every account number, function code and name of both files against the PDF
  text; the test cannot, because the PDFs are not in the repository.
* The existing suites that use the synthetic fixture (`chart_dir`) stay unchanged. Tests that relied on "No chart
  files" (409) are adapted.
