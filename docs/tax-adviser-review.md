# Tax adviser review checklist

**Purpose.** permitA38 only *estimates* the tax and social insurance burden of a self-employed person, so that the
user can plan monthly set-asides. It is not tax advice and never replaces the tax assessment; an error of about
+-100 EUR per year is acceptable. This list holds every rate and rule of the estimate that still needs a professional
to confirm it. Each item has an ID (TA-nn), what the app does today, the question, and why it matters. Record the
answer in the last two columns and set the Status to `open`, `confirmed` or `changed`. Sections A to C are for the
tax adviser (Steuerberater); section D is for the maintainer only.

References: "spec" is `docs/specs/tax-burden-and-settings.md` (part B, "Algorithm" step N); the rate tables are
`permita38/core/tax_rules/2026.json` and `2025.json` (key = entry name under `values`); the code is
`permita38/core/tax.py`.

## A. Rates marked TO VERIFY

| ID | Topic | What the app does now | Question | Why it matters | Status | Adviser answer / date |
|---|---|---|---|---|---|---|
| TA-01 | Sachsen employee care share | `pv_employer_share_sn` = 1.3 % (employer) in `2026.json`, flagged `to_verify` ("Sachsen value TO VERIFY"); the employee pays 2.3 % there (other states: `pv_employer_share` 1.8 %). Spec step 2, used when `settings.state == "SN"`. | Is the employee share of the Pflegeversicherung in Sachsen 2.3 % (employer 1.3 %) in 2026? | Changes the employee care contribution of employed users in Sachsen, and through it the deductible Vorsorge amount (step 5). | open | |
| TA-02 | Half voluntary unemployment contribution | `alv_voluntary_half_month` = 51.42 (= 2.6 % x 1977.50, half-up), flagged `to_verify`; sources show 51.42 and 51.43. Spec step 4: `voluntary_half` = 12 x 51.42. | Is the monthly amount 51.42 or 51.43? | One cent per month, 12 cents per year; only matters for users with the half voluntary insurance. | open | |
| TA-03 | Gewerbesteuer-Messbetrag rounding | `mess = ertrag x gew_messzahl` (3.5 %), not rounded; `gewst = q2(mess x hebesatz)`. Spec step 9 marks it TO VERIFY. | Is the Messbetrag rounded (and how) before the Hebesatz is applied? | Small rounding difference in the trade tax and in the section 35 credit. | open | |
| TA-04 | Church tax truncation | `church = trunc2(Z x church_tax_percent)`, truncated to cents. Spec step 10 marks it TO VERIFY (Länder law). | Does church tax truncate or round to cents? | Up to one cent per year; recorded for completeness. | open | |
| TA-05 | 2025 table | In `2025.json` only the tariff keys (`est_gf`, `est_z2`, `est_z3`, `est_a2`, `est_a3`, `est_c3`, `est_k4`, `est_k5`), `soli_freigrenze` and `vorsorge_hoechst_altersvorsorge` are not flagged. All other keys are copies of the 2026 values with `status: to_verify` and the note "2025 value not researched": `est_z4` (the 2025 number is a copy of the 2026 value 277825), `soli_rate`, `soli_cap_rate`, `kinderfreibetrag`, `bea_freibetrag`, `kindergeld_month`, `an_pauschbetrag`, `sa_pauschbetrag`, `vorsorge_cap_self`, `vorsorge_cap_employee`, `kv_basis_cut_percent`, `gew_freibetrag`, `gew_messzahl`, `gew_credit_factor`, `kst_rate`, `bezugsgroesse_month`, `bbg_kv_month`, `bbg_rv_month`, `bbg_rv_year`, `kv_min_base_month`, `kv_rate_general`, `kv_rate_reduced`, `kv_zusatz_average`, `pv_rate`, `pv_childless_surcharge`, `pv_child_reduction`, `pv_employer_share`, `pv_employer_share_sn`, `rv_rate`, `rv_min_base_month`, `alv_rate`, `alv_voluntary_month`, `alv_voluntary_half_month`, `bav_tax_free_percent`, `bav_sv_free_percent`. The finance page shows a "not yet confirmed" warning for 2025. | Provide the 2025 values for all listed keys (or confirm the 2026 value applies). | Estimates for the year 2025 (and earlier years, which fall back to 2025) use unchecked social insurance, allowance and child values. | open | |

## B. Calculation rules

| ID | Topic | What the app does now | Question | Why it matters | Status | Adviser answer / date |
|---|---|---|---|---|---|---|
| TA-06 | Günstigerprüfung per child, half Kindergeld | Spec step 8: children are tested one by one on the zvE left after the previous children; the Freibetrag (`kinderfreibetrag` + `bea_freibetrag`, doubled for joint assessment or full allowance) is used if the tax saving exceeds the Kindergeld (`kindergeld_month` x 12, halved per parent when not jointly assessed, section 31 S. 4 EStG). | Is the staged per-child test what the Finanzamt does, and is counting half the Kindergeld for a parent who is not jointly assessed right in all cases? | Decides for each child whether Kindergeld or the allowance is used, which moves the income tax of families. | open | |
| TA-07 | Bases of Soli and church tax | Spec steps 8 and 10: the surcharge base `Z` is the tariff on the zvE minus all child allowances. Soli: `soli_base = Z` minus a section 35 credit recomputed on `Z` (section 3 (2) SolZG, "festzusetzen wäre"). Church tax: on `Z` without the section 35 credit (section 51a (2) S. 3 EStG). The church tax part was checked against the statute text on gesetze-im-internet.de by a reviewer; adviser to confirm. The Soli part is not yet confirmed. | Confirm both bases. | Soli and church tax of Gewerbetreibende with trade tax credit; wrong base over- or under-states both. | open (checked against the statute text, adviser to confirm) | |
| TA-08 | Section 10 Vorsorge | Spec step 5: Altersvorsorge `dA` = min(A, `vorsorge_hoechst_altersvorsorge`, doubled if joint) minus the employer share; basis cover `B` (health and care, minus `kv_basis_cut_percent` 4 % when Krankengeld is included) is always deductible in full; other insurance `O` only up to the cap `vorsorge_cap_self` or `vorsorge_cap_employee`; `dBO = max(B, min(B + O, C))`. | Is the Basisabsicherung versus Höchstbetrag logic (section 10 (4) S. 4 EStG) modelled correctly, including the 4 % cut and the choice of cap? | Size of the Sonderausgaben deduction, hence the zvE. | open | |
| TA-09 | Section 35 share rule | Spec step 9: `share` = positive business income divided by the sum of the positive items among business profit, employment income and other income; `credit = min(4 x mess, gewst, q2(est_tarif x share))` with `gew_credit_factor` 4.0. | Is the share rule (positive incomes only) the right apportionment of the section 35 EStG credit? | Size of the trade tax credit against income tax when the user has several income types. | open | |
| TA-10 | GmbH case | Spec step 11: corporate tax (`kst_rate` 15 %) and Soli on the GmbH profit after the managing director's salary, trade tax without Freibetrag, no section 35; the salary is taxed personally (steps 1 to 10 with business profit 0). Dividend taxation is not modelled. | Is this a reasonable estimate for a GmbH with a managing director's salary, and is leaving out dividend tax acceptable? | Tax set-asides of GmbH owners; distributions are not covered at all. | open | |
| TA-11 | Not modelled: Progressionsvorbehalt | Not modelled (Elterngeld, Krankengeld); listed in the spec under "Scope and non-goals". | Confirm acceptable to leave out. | Users with such benefits get a too low rate. | open | |
| TA-12 | Not modelled: church tax Kappung | Not modelled; listed in the spec under "Scope and non-goals". | Confirm acceptable to leave out. | Church tax is slightly too high for users above the cap. | open | |
| TA-13 | Not modelled: section 10 (3) S. 3 cap reduction | Known limitation in the spec: the cut of the Altersvorsorge cap (e.g. a GmbH managing director with a pension promise) is not modelled; the cap is never reduced. | Confirm acceptable to leave out. | Deductible pension contributions are too high for the affected users. | open | |
| TA-14 | Not modelled: Vorauszahlungen | Spec "Scope and non-goals": Einkommensteuer and Gewerbesteuer Vorauszahlungen are not recorded; "currently owed" and the reserve ignore them. | Confirm acceptable to leave out. | The shown amount owed can be higher than the real remaining payment. | open | |

## C. Figures checked only from memory by the reviewers

Not verified against the official publications. Values, source and valid_from are copied from
`permita38/core/tax_rules/2026.json`.

| ID | Topic | Rate-table key and current value | Source and valid_from (as in the JSON) | Question | Why it matters | Status | Adviser answer / date |
|---|---|---|---|---|---|---|---|
| TA-15 | Health insurance contribution ceiling | `bbg_kv_month` = 5812.50 | SV-RechGrV 2026 (69 750 / year), 2026-01-01 | Is the value correct? (spec steps 2, 3) | Caps health and care contributions. | open | |
| TA-16 | Pension insurance contribution ceiling | `bbg_rv_month` = 8450, `bbg_rv_year` = 101400 | SV-RechGrV 2026, 2026-01-01 | Are the values correct? (spec steps 1, 2, 4) | Caps pension contributions and the bAV free amounts. | open | |
| TA-17 | Bezugsgröße | `bezugsgroesse_month` = 3955 | SV-RechGrV 2026, 2026-01-01 | Is the value correct? (spec steps 3, 4; also basis of `kv_min_base_month` 1318.33) | Minimum health base and regular pension contribution. | open | |
| TA-18 | Geringfügigkeitsgrenze | `rv_min_base_month` = 603 | DRV 2026 (Geringfuegigkeitsgrenze 603), 2026-01-01 | Is the value correct and is it the right minimum pension base? (spec step 4) | Minimum pension contribution. | open | |
| TA-19 | Rürup cap | `vorsorge_hoechst_altersvorsorge` = 30826 | section 10 (3) EStG: 24.7 % x 124 800 (BBG Knappschaft) = 30 825.60, rounded up; 2026-01-01 | Is the value and the rounding up correct? (spec step 5) | Cap of deductible pension contributions. | open | |
| TA-20 | Average Zusatzbeitrag | `kv_zusatz_average` = 2.9 | BMG, BAnz AT 10.11.2025 B7 (section 242a SGB V), 2026-01-01 | Is the value correct? (spec steps 2, 3) | Used when the user enters no own Zusatzbeitrag. | open | |
| TA-21 | Kindergeld | `kindergeld_month` = 259 | section 66 (1) EStG, 2026-01-01 | Is the value correct? (spec step 8) | Compared with the child allowance in the Günstigerprüfung. | open | |
| TA-22 | Child allowances | `kinderfreibetrag` = 3414, `bea_freibetrag` = 1464 (per parent) | section 32 (6) S. 1 EStG, 2026-01-01 | Are the values correct? (spec step 8) | Tax saving per child. | open | |
| TA-23 | Arbeitnehmer-Pauschbetrag | `an_pauschbetrag` = 1230 | section 9a S. 1 Nr. 1 a EStG, 2026-01-01 | Is the value correct? (spec step 1) | Deduction from employment income. | open | |

## D. Product decisions for the maintainer (not the adviser)

| ID | Topic | Current behaviour | Decision needed | Status | Answer / date |
|---|---|---|---|---|---|
| TA-24 | Catch-up of owed tax in the current month | Since the 2026-11-03 amendment the current month's `tax_set_aside` carries `owed_now` on top of `monthly` (past year: December). Without it the tax on the profit already booked lands in no month and `take_home` never subtracts it. | Confirm, or revert (then `take_home` needs another per-month tax term). | open | |
| TA-25 | GmbH take_home | For `gmbh` the formula subtracts the household tax (on the salary) and the corporate taxes from the GmbH profit, so `take_home` is the company's cash after set-asides, not the owner's. | Keep, or hide `take_home` for `gmbh`. | open | |
| TA-26 | Recording Vorauszahlungen later | Not recorded (see TA-14); "currently owed" and the reserve ignore them. | Record them in a later version? | open | |

## How to update

When an answer changes a value, edit the rate-table JSON (`permita38/core/tax_rules/<year>.json`): remove
`"status": "to_verify"`, update `source` and `valid_from`. Update the spec
(`docs/specs/tax-burden-and-settings.md`), add or adjust a test with the hand-computed number, and set the Status of
the item to `changed` (or `confirmed` when nothing changes), with the adviser's answer and the date. When a rule (not
a value) changes, update the Algorithm step named in the item and the oracle cases in the spec.
