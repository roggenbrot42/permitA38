# Specification: settings split and real tax burden estimate

Status: **proposed, not scheduled** (roadmap rows "Settings split into business and user" and "Real tax burden
estimate", maintainer request of 2026-11-02). Author: architect. Implementation: Sonnet implementer agents, one work
package each. Review tiers per package, see "Work packages". The implementer follows this document, does not decide
tax questions, and lists every choice it had to make under "Open design decisions" in its report.

All figures in this document are fake demo figures. Rates were checked on 2026-11-03 against the sources named in
"Rule tables"; values that could not be confirmed from an official text are marked **TO VERIFY** and must be
confirmed by the maintainer's tax adviser before the feature is released.

## Goal

1. **A – Settings split.** One page today mixes business master data, the business tax set-up, document defaults,
   per-user cards (calendars, API tokens) and the owner's private tax details (church tax, joint assessment, other
   income, a flat social-insurance amount). Split it into **Business settings** and **My settings**, and move the
   private tax details into a **personal tax profile** that only the boss can read or write.
2. **B – Tax burden.** Replace the flat "social per month" reserve with a full estimate for the owner of a
   Freiberufler or Gewerbe business (and the owner-manager of a UG/GmbH): health and care insurance (public or
   private), pension and unemployment insurance (each with opt-out), an optional employment-income block (gross wage,
   Steuerklasse, bAV, VWL), children with the Günstigerprüfung, the §10 EStG caps for Vorsorgeaufwendungen,
   Einkommensteuer, Solidaritätszuschlag, Kirchensteuer and Gewerbesteuer with the §35 credit. All money is computed
   with exact decimals (`core/money.py`), with year-dated rule tables.

## Scope and non-goals

In scope: one taxpayer (plus a spouse only through joint assessment, see below), assessment years with a rule table,
the categories listed in the goal, a monthly reserve derived from the estimate, legacy settings kept working.

Non-goals (v1), each shown as a sentence on the tax profile page:

* **Not tax advice.** The result is a planning estimate; the page and `/finance` say so.
* Lohnsteuer withholding (Programmablaufplan). The **Steuerklasse is informational only**: it never changes the
  annual Einkommensteuer, which is what this feature computes. The user may enter the tax already withheld
  (`withheld_year`) to see the expected back payment or refund.
* The spouse's own income, insurance and caps (put the spouse's net income into `other_income_year`; the spouse's
  deductible insurance into `other_deductions_year`). Under joint assessment the §10 (3) cap is doubled by law, the
  §10 (4) cap is the taxpayer's own.
* Capital income (Abgeltungsteuer), GmbH dividends, Teileinkünfteverfahren, loss carry-forward, §34 / §34a, the
  Progressionsvorbehalt (Elterngeld, Krankengeld), Riester (§10a), Arbeitnehmersparzulage, Kirchensteuer-Kappung,
  Kinderbetreuungskosten, Gewerbesteuer additions/deductions (§§8, 9 GewStG), Gewerbe loss carry-forward,
  Pflegeversicherung under 23 years, Künstlersozialkasse, Beamte/Beihilfe and the §10 (3) S. 3 cut of the cap for
  people without own pension contributions (both: health mode `none` / pension mode `none` and a note).
* Mixed insurance cases where self-employment is the **main** occupation and an SV-liable job is a side job (the
  model assumes: an SV-liable job, if present, is the main one and carries the public health insurance).

## Part A – Settings split

### New grouping

| Today (one page `#/settings`) | New place |
|---|---|
| company_name, owner_name, street, zip, city, email, phone, website, tax_number, vat_id | **Business settings** → card "Business identity" |
| bank_name, iban, bic, logo | **Business settings** → card "Bank and logo" |
| company_type, state, kleinunternehmer, vat_rate, hebesatz, accounting | **Business settings** → card "Business tax set-up" (hebesatz shown only for `gewerbe` / `gmbh`) |
| default_rate, payment_days, offer_valid_days, default_template, numbering link, templates table | **Business settings** → card "Documents and defaults" |
| team timeline visibility card | **Business settings** (unchanged) |
| data folder, data export/import links | **Business settings** → card "Data and sync" (unchanged) |
| calendars card | **My settings** (shown when `can('planning','write') && scopeOf('planning')==='all'`, as the API already requires) |
| API tokens card | **My settings** (every user; tokens are per user) |
| social_monthly, church_tax, splitting, other_income, other_deductions | **My settings** → link to the **Personal tax profile** page `#/me/tax` (boss only) |
| – | **My settings** → card "Profile": name, username, role, own weekly hours and hourly rate (read-only, from `GET /auth/me`; editing stays on the Team page), buttons "Change password" and "Two-factor authentication" (same actions as the sidebar) |

Hourly rates stay where they are: the business default `default_rate` in Business settings, the per-user
`default_rate` / `rate` on the user record (Team page). No field changes owner.

### Storage and API

The `settings` table is a key/value store (`schema.py`: `key:str value:json`), so **no migration** is needed.

* New settings key **`personal_tax`**: one JSON object, the personal tax profile (model below).
* `SettingsService.get()` removes `personal_tax` from its result (like `team_timeline_visibility`), so
  `GET /settings`, `GET /bootstrap` and the accountant never see it.
* `SettingsService.update()` refuses a body that contains `personal_tax` with `ServiceError(400, "The personal tax
  profile is changed with PUT /api/settings/personal-tax")`, before anything is stored. The stored `personal_tax`
  survives every `PUT /settings` (the service merges over the stored settings; add a test).
* New router functions in `permita38/api/routers/settings.py`, both boss-only via
  `Needs("settings", "write", full=True)` (the same dependency as `data_transfer.py`):
  * `GET /api/settings/personal-tax` → `PersonalTax` (stored profile, or the profile derived from the legacy fields,
    with `derived_from_legacy: true`).
  * `PUT /api/settings/personal-tax` (`dependencies=[SessionOnly]`) with body `PersonalTaxIn` → **replaces** the
    stored profile (not a merge; nested objects make a merge ambiguous) and returns `PersonalTax` plus
    `warnings: list[str]` (advisory, never stored, see "Advisory warnings").
* Logic lives in a new `permita38/core/service/personal_tax.py` (`PersonalTaxService(store)` with `get()`,
  `update(data)`, `effective()`), registered where `SettingsService` is.
* **Legacy fields** `social_monthly`, `church_tax`, `splitting`, `other_income`, `other_deductions` stay in `Settings`
  and `SettingsIn` unchanged (API clients keep working) with the field description "deprecated: superseded by
  `personal_tax`; used only while no personal tax profile is stored". The web UI no longer shows them.
* **Derivation** when `personal_tax` is absent: `{mode: "flat", assessment: "joint" if splitting else "single",
  church_tax_percent: church_tax, other_income_year: other_income, other_deductions_year: other_deductions,
  flat_social_monthly: social_monthly}`, everything else default. Saving the profile once stores it; the legacy keys
  stay in storage untouched (round-trip), they no longer influence the estimate.
* Data export already writes every non-secret setting, so `personal_tax` is exported and imported as-is (the export is
  boss-only). The engine reads the stored object defensively (below), so a damaged import cannot break `/finance`.
* MCP server (PR #73, not on `main` at the time of writing): if it exposes settings or finance, it must apply the same
  removal and redaction; the MCP work package owner checks this when rebasing.

### `/finance` redaction

`/finance` is readable by the accountant (`finance: (A, N)`). Church tax reveals religion (GDPR Art. 9), children and
insurance are private. For a caller who is not the boss, `tax_actual` and `tax_projected` contain **only**
`total`, `rate`, `gewst`, `kst` and `redacted: true`; `social_year`, `take_home` and the months' `social` /
`take_home` stay (aggregates). The boss gets the full breakdown. Rights test in `tests/backend/test_finance_api.py`
(or the existing finance API test file).

### Models (`core/models.py`, primitives in `core/validation.py`)

New primitive in `validation.py`: `MonthlyAmount = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]`.
Yearly amounts use the existing `NonNegAmount` (0 … 1 000 000 000). All `*In` models derive from `v.StrictRecord`
(unknown fields kept); all response models use `extra="allow"`.

```text
PersonalTaxIn (StrictRecord)                        default
  mode                 "flat" | "full"              "full"
  assessment           "single" | "joint"           "single"
  church_tax_percent   float 0 … 15                 0        (8 in BY/BW, 9 elsewhere; hint only)
  other_income_year    NonNegAmount                 0        other positive income after its own costs
  other_deductions_year NonNegAmount                0        Sonderausgaben/außergew. Belastungen besides insurance
  other_insurance_year NonNegAmount                 0        liability, disability, term life (§10 (1) Nr. 3a)
  flat_social_monthly  MonthlyAmount                0        only used in mode "flat"
  children             ChildrenIn                   {}
  health               HealthIn                     {}
  pension              PensionIn                    {}
  unemployment         UnemploymentIn               {}
  employment           EmploymentIn | null          null

ChildrenIn: count int 0 … 15 = 0 (children with Kindergeld/Kinderfreibetrag);
            full_allowance bool = false (single assessment: the full Freibeträge were transferred to you);
            pv_parent bool = false (you have or had a child: no childless surcharge);
            pv_children_under_25 int 0 … 15 = 0 (for the care-insurance reduction)
HealthIn:   mode "gkv" | "pkv" | "none" = "gkv";
            krankengeld bool = true (GKV: general rate 14.6 % instead of 14.0 %);
            zusatzbeitrag_percent float 0 … 10 | null = null (null = the table's average Zusatzbeitrag);
            pkv_monthly MonthlyAmount = 0; pkv_basis_monthly MonthlyAmount = 0 (≤ pkv_monthly, 422 otherwise);
            pkv_care_monthly MonthlyAmount = 0; pkv_employer_subsidy_monthly MonthlyAmount = 0
PensionIn:  mode "none" | "statutory_income" | "statutory_regular" | "fixed" = "none";
            fixed_monthly MonthlyAmount = 0 (Rürup, Versorgungswerk, voluntary DRV amount)
UnemploymentIn: mode "none" | "voluntary" | "voluntary_half" = "none"
EmploymentIn: gross_year NonNegAmount (gross wage before Entgeltumwandlung, without VWL and employer bAV);
            sv_liable bool = true; steuerklasse int 1 … 6 | null = null (informational);
            bav_employee_year 0 … 100 000 = 0 (Entgeltumwandlung); bav_employer_year 0 … 100 000 = 0;
            vwl_employer_year 0 … 10 000 = 0; werbungskosten_year NonNegAmount = 0 (0 = Pauschbetrag);
            withheld_year NonNegAmount | null = null (Lohnsteuer + Soli + KiSt withheld)
```

`PersonalTax` (response, `extra="allow"`): the same fields with lenient types plus `derived_from_legacy: bool` and
`warnings: list[str] | None`. Add every field to both models (AGENTS.md); `docs/validation.md` gets one table row
"Personal tax profile" with these bounds.

**Defensive read.** `PersonalTaxService.effective()` validates the stored object with the lenient model; an invalid
or missing nested value falls back to its default with a logged warning (same rule as `calc.py`), never an error.

### Advisory warnings (returned by PUT, never block)

* `steuerklasse` in 3, 4, 5 and `assessment == "single"`: "Steuerklasse III/IV/V means married; check the assessment."
* `employment.sv_liable` and `unemployment.mode != "none"`: "Voluntary unemployment insurance is usually not possible
  next to an employment that is insured."
* `health.mode == "pkv"` and `pkv_basis_monthly == 0` and `pkv_monthly > 0`: "Enter the basic-cover share from your
  insurer's annual statement; without it nothing of the premium counts as basic cover."
* rule table for the current year contains a value with status `to_verify`: "Some rates for <year> are not yet
  confirmed."

## Part B – Calculation

### Rule tables

One JSON file per year, `permita38/core/tax_rules/<year>.json`, loader `permita38/core/tax_rules/__init__.py`
(pattern of `core/trip_rates/`). Every value is an object `{"value": "<decimal string>", "source": "...",
"valid_from": "YYYY-MM-DD"}` with optional `"status": "to_verify"` and `"note"`. The loader returns a frozen
dataclass of `Decimal`s plus the list of `to_verify` keys. `rules_for(year)` uses the year's table; without one it
uses the nearest older (else the oldest) table and the estimate gets the warning "No tax rules for <year>; using
<other year>." (today the fallback is silent).

**2026 table** (`2026.json`). Sources: EStG/SolZG/GewStG/KStG/SvEV on gesetze-im-internet.de (current text),
Sozialversicherungsrechengrößen-Verordnung 2026 (BGBl. 2025 I Nr. 278) as published by the Deutsche
Rentenversicherung, BMG announcement of the average Zusatzbeitrag (BAnz AT 10.11.2025 B7), contribution table for
voluntary members 2026 of a public health insurer (IKK BB, 2025-12-29) for the GKV/PV rates and limits.

| key | value | source / derivation |
|---|---|---|
| `est_gf`, `est_z2`, `est_z3`, `est_z4` | 12348, 17799, 69878, 277825 | §32a (1) EStG, VZ from 2026 |
| `est_a2`, `est_a3`, `est_c3`, `est_k4`, `est_k5` | 914.51, 173.10, 1034.87, 11135.63, 19470.38 | §32a (1) EStG |
| `soli_rate`, `soli_cap_rate` | 5.5, 11.9 | §4 SolZG |
| `soli_freigrenze` | 20350 (joint: ×2 = 40700) | §3 (3) SolZG |
| `kinderfreibetrag`, `bea_freibetrag` | 3414, 1464 per parent (×2 joint or transferred) | §32 (6) S. 1 EStG |
| `kindergeld_month` | 259 | §66 (1) EStG |
| `an_pauschbetrag` | 1230 | §9a S. 1 Nr. 1 a EStG |
| `sa_pauschbetrag` | 36 (joint 72) | §10c EStG |
| `vorsorge_hoechst_altersvorsorge` | 30826 (joint ×2) | §10 (3) EStG: 24.7 % × 124 800 (BBG Knappschaft) = 30 825.60, rounded up |
| `vorsorge_cap_self`, `vorsorge_cap_employee` | 2800, 1900 | §10 (4) EStG |
| `kv_basis_cut_percent` | 4 | §10 (1) Nr. 3 a S. 4 EStG (cut when Krankengeld is included) |
| `gew_freibetrag`, `gew_messzahl` | 24500, 3.5 | §11 (1), (2) GewStG |
| `gew_credit_factor` | 4.0 | §35 (1) EStG |
| `kst_rate` | 15 | §23 (1) KStG (14 in 2028, 13 in 2029, 12 in 2030, 11 in 2031, 10 from 2032 – note in the table) |
| `bezugsgroesse_month` | 3955 | SV-RechGrV 2026 |
| `bbg_kv_month` | 5812.50 | SV-RechGrV 2026 (69 750 / year) |
| `bbg_rv_month`, `bbg_rv_year` | 8450, 101400 | SV-RechGrV 2026 |
| `kv_min_base_month` | 1318.33 | §240 (4) SGB V: Bezugsgröße / 3 (insurer table 2026) |
| `kv_rate_general`, `kv_rate_reduced` | 14.6, 14.0 | §§241, 243 SGB V (insurer table 2026) |
| `kv_zusatz_average` | 2.9 | BMG, BAnz AT 10.11.2025 B7 (§242a SGB V) |
| `pv_rate`, `pv_childless_surcharge`, `pv_child_reduction` | 3.6, 0.6, 0.25 per child from the 2nd to the 5th under 25 | §55 SGB XI (insurer table 2026) |
| `pv_employee_share` / `pv_employee_share_sn` | 1.8 / 2.3 (employer 1.7 / 1.3; childless surcharge and reductions on the employee) | §58 SGB XI; **Sachsen value TO VERIFY** |
| `rv_rate`, `rv_min_base_month` | 18.6, 603 | DRV 2026 (Geringfügigkeitsgrenze 603) |
| `alv_rate` | 2.6 | §341 SGB III (DRV 2026) |
| `alv_voluntary_month`, `alv_voluntary_half_month` | 102.83 (= 2.6 % × 3955), 51.42 (= 2.6 % × 1977.50, half-up) | §345b SGB III; **half value TO VERIFY** (sources show 51.42 and 51.43) |
| `bav_tax_free_percent`, `bav_sv_free_percent` | 8, 4 (of `bbg_rv_year`: 8112, 4056) | §3 Nr. 63 EStG, §1 (1) Nr. 9 SvEV |

Employee shares: KV `kv_rate_general / 2 + zusatz / 2`, RV `rv_rate / 2`, ALV `alv_rate / 2`.

**2025 table** (`2025.json`): tariff and Soli Freigrenze from today's `tax.py` (gf 12096, z2 17443, z3 68480, a2
932.30, a3 176.64, c3 1015.13, k4 10911.92, k5 19246.67; Soli 19950), `vorsorge_hoechst_altersvorsorge` 29344, every
other value copied from 2026 with `"status": "to_verify"` and the note "2025 value not researched". The finance page
then shows the "not yet confirmed" warning for 2025. Earlier years fall back to 2025 with the fallback warning.

### Inputs of the engine

`estimate(profit: Decimal, year: int, settings: dict, profile: dict) -> dict` in `permita38/core/tax.py`.
`profit` is the business profit of the year (may be negative), `settings` gives `company_type` and `hebesatz`,
`profile` is `PersonalTaxService.effective()`. Notation below: `q2` = half-up to cents (`money.q2`), `trunc2` =
truncate to cents, `floor0` = round down to whole euros, `pos(x) = max(x, 0)`. Percent values are divided by 100.

### Algorithm, mode "flat" (legacy)

The current `tax.estimate` ported 1:1 to `Decimal` (same steps, same results within one cent): zvE =
`pos(profit + other_income − 12 × flat_social_monthly − other_deductions)`, Einkommensteuer attributable to the
business = `T(zvE) − T(pos(other_income − other_deductions))`, Soli likewise, church tax on the attributable tax,
Gewerbesteuer and §35 as today, GmbH as below. Social = `12 × flat_social_monthly`. No children, no caps. Existing
users see the same numbers until they switch to "full".

### Algorithm, mode "full"

**1. Employment wage** (only with `employment`):
`tax_free = min(bav_employee + bav_employer, 8 % × bbg_rv_year)`,
`sv_free = min(bav_employee + bav_employer, 4 % × bbg_rv_year)`,
`tax_wage = gross + vwl_employer + bav_employer − tax_free`, `sv_wage = gross + vwl_employer + bav_employer − sv_free`,
`emp_income = pos(tax_wage − max(werbungskosten_year, an_pauschbetrag))`.

**2. Employee contributions** (only with `employment.sv_liable`): monthly bases
`kv_m = min(q2(sv_wage / 12), bbg_kv_month)`, `rv_m = min(q2(sv_wage / 12), bbg_rv_month)`; annual amounts are
`12 × q2(base × rate)`:
`ee_kv` at `kv_rate_general/2 + zusatz/2`, `ee_pv` at `pv_total − employer share` (employer share 1.7, or 1.3 when
`settings.state == "SN"`), `ee_rv` at 9.3, `ee_alv` at 1.3; `er_rv = ee_rv` (tax-free employer share §3 Nr. 62).
`pv_total = pv_rate + (0.6 if not pv_parent) − 0.25 × (min(pv_children_under_25, 5) − 1 if ≥ 2)`.
`zusatz` = `health.zusatzbeitrag_percent` or `kv_zusatz_average`. If `health.mode == "pkv"`, `ee_kv = ee_pv = 0`
(the PKV block applies).

**3. Self-paid health and care.**
* `gkv` without an SV-liable job (voluntary member):
  `base_m = clamp(q2((pos(biz) + pos(other_income_year) + w) / 12), kv_min_base_month, bbg_kv_month)` where
  `biz = profit` (0 for `gmbh`) and `w = tax_wage` when `employment` exists and is not SV-liable, else 0.
  `kv = 12 × q2(base_m × (kv_rate + zusatz))` with `kv_rate = kv_rate_general if krankengeld else kv_rate_reduced`;
  `pv = 12 × q2(base_m × pv_total)`.
* `gkv` with an SV-liable job: the employee amounts of step 2; nothing on the profit.
* `pkv`: `kv = 12 × pkv_monthly`, `pv = 12 × pkv_care_monthly`, `subsidy = 12 × pkv_employer_subsidy_monthly`.
* `none`: all 0.

**4. Pension and unemployment (self-paid).**
`statutory_income`: `12 × q2(clamp(q2(pos(biz) / 12), rv_min_base_month, bbg_rv_month) × rv_rate)`;
`statutory_regular`: `12 × q2(bezugsgroesse_month × rv_rate)` (= 12 × 735.63); `fixed`: `12 × fixed_monthly`.
Unemployment: `voluntary` 12 × 102.83, `voluntary_half` 12 × 51.42, else 0.

**5. Vorsorgeaufwendungen (§10 (1) Nr. 2, 3, 3a; (3); (4) EStG).**
* Altersvorsorge `A = self_rv + ee_rv + er_rv`; cap `H = vorsorge_hoechst_altersvorsorge × (2 if joint)`;
  deductible `dA = pos(min(A, H) − er_rv)`.
* Basis cover `B`: gkv voluntary `kv − cut + pv` with `cut = q2(kv × 4 %)` if `krankengeld` else 0; gkv employed
  `ee_kv − q2(ee_kv × 4 %) + ee_pv` (employees always have Krankengeld); pkv
  `pos(12 × pkv_basis_monthly + pv − subsidy)`; none 0.
* Other `O = alv_self + ee_alv + cut + (12 × (pkv_monthly − pkv_basis_monthly) for pkv) + other_insurance_year`.
* Cap `C = vorsorge_cap_employee` if `employment.sv_liable` or `subsidy > 0`, else `vorsorge_cap_self`.
* Deductible `dBO = max(B, min(B + O, C))` (basis cover is always deductible in full, §10 (4) S. 4).

**6. Income and zvE.** `sum_income = biz + emp_income + other_income_year` (`biz = profit`, 0 for `gmbh`);
`sa = max(other_deductions_year, sa_pauschbetrag × (2 if joint))`;
`zvE = pos(sum_income − dA − dBO − sa)`.

**7. Tariff.** `T(x)`: §32a with `x = floor0(x)`, result `floor0`; joint: `2 × T(floor0(zvE) / 2)`.

**8. Children (Günstigerprüfung §31 EStG), per child in order.** `f = 2 if joint or full_allowance else 1`;
`kfb = (kinderfreibetrag + bea_freibetrag) × f`; `kg = 12 × kindergeld_month × f / 2` (non-joint parents count
Kindergeld in the share of their Freibetrag, §31 S. 4). Start `z = zvE`, `kg_added = 0`; for each child: if
`T(z) − T(pos(z − kfb)) > kg` then `z = pos(z − kfb)`, `kg_added += kg`. `est_tarif = T(z)`.
**Zuschlag base** (Soli and church tax always with all Freibeträge, §51a (2) EStG, §3 (2) SolZG):
`Z = T(pos(zvE − count × kfb))`.

**9. Gewerbesteuer and §35 (company_type `gewerbe`).** `ertrag = pos(floor(profit / 100) × 100 − gew_freibetrag)`,
`mess = ertrag × 3.5 %` (not rounded; **TO VERIFY** whether the Messbetrag is rounded), `gewst = q2(mess × hebesatz)`.
`share = pos(biz) / (sum of the positive items among biz, emp_income, other_income_year)`;
`credit = min(4 × mess, gewst, q2(est_tarif × share))`. For the Soli base the credit is recomputed with `Z`:
`credit_z = min(4 × mess, gewst, q2(Z × share))`. Freiberufler: all 0.

**10. Taxes.** `est = est_tarif − credit + kg_added` (festzusetzende Einkommensteuer);
`soli_base = pos(Z − credit_z)`; `soli = 0 if soli_base ≤ freigrenze (× 2 joint) else trunc2(min(soli_base × 5.5 %,
(soli_base − freigrenze) × 11.9 %))`; `church = trunc2(Z × church_tax_percent)` (no §35 credit in the church-tax
base, §51a (2) S. 3; truncation **TO VERIFY**, Länder law).
`income_taxes = est + soli + church`.

**11. GmbH (company_type `gmbh`).** Corporate side on the GmbH profit (after the managing director's salary):
`kst = trunc2(floor0(profit) × kst_rate)`, `kst_soli = trunc2(kst × 5.5 %)` (no Freigrenze for corporations),
`gewst = q2(floor(profit / 100) × 100 × 3.5 % × hebesatz)` (no Freibetrag), no §35. Personal side: steps 1–10 with
`biz = 0` (salary in the employment block, usually `sv_liable: false`).

**12. Result.** `social = kv + pv + self_rv + alv_self + ee_kv + ee_pv + ee_rv + ee_alv − subsidy`;
`total = income_taxes + gewst + kst + kst_soli + social`; `balance = income_taxes − withheld_year` when given.
**Business share** (for the monthly reserve): run steps 1–11 again with `profit = 0`;
`business_tax = (income_taxes + gewst + kst + kst_soli)(profit) − (same)(0)`; `rate = business_tax / profit` if
`profit > 0` else 0. Return all intermediate values (they are shown and tested).

Returned dict (Decimals inside the engine; `calc.finance` converts with `to_float`): the existing keys `profit, social,
est, soli, church, gewst, kst, gewst_credit, income_tax_total, total, rate, zve` keep their meaning (`social` = step
12, `income_tax_total` = `income_taxes`), plus `mode, kst_soli, est_tarif, kg_added, kfb_used, zuschlag_base,
soli_base, vorsorge: {A, H, dA, B, O, C, dBO}, insurance: {kv, pv, rv, alv, ee_kv, ee_pv, ee_rv, ee_alv, subsidy},
emp_income, tax_wage, sv_wage, balance, business_tax, warnings: list[str]`.

### Integration in `calc.finance` (float migration)

* `profit_f`, the annualisation and `projected` become `Decimal` (`frac` as `Fraction` or `Decimal` of days/365);
  no float enters `tax.estimate`.
* `m["tax_set_aside"] = q2(pos(m.profit) × rate)` as today; `m["social"] = q2(proj.social / 12)` replaces
  `social_monthly` (mode flat: `flat_social_monthly`, identical to today).
* `social_year = q2(proj.social / 12) × social_months`; `take_home = profit − (actual.total − actual.social) −
  social_year` – social contributions are subtracted once, never twice.
* `tax_actual` / `tax_projected` warnings are appended to the response `warnings`.
* Gewerbesteuer is **not** a deductible business expense (§4 (5b) EStG); the estimate assumes it is not booked as an
  expense (open question below).
* `tests/backend/test_domain.py`: the tolerant asserts become exact (`T(17800) == 1035`, `T(69879) == 18213`,
  `T(100000) == 30864`).

## Oracle test cases (2026 table, mode "full")

Computed by the architect with a separate scratch script (not in the repo) and spot-checked by hand; the logic review
recomputes each line independently. Anchor: `T(100 000) = 30 864` (existing test). All cases: year 2026, no
`other_*` amounts unless stated, `zusatzbeitrag_percent` 2.9 where GKV.

**O1 – Freiberufler, single, GKV voluntary with Krankengeld, no pension/ALV, childless.** Profit 60 000.
base_m 5 000.00; kv rate 17.5 → kv 10 500.00; pv 4.2 % → 2 520.00; social 13 020.00. B = 10 500 − 420 + 2 520 =
12 600.00; O 420.00; C 2 800 → dBO 12 600.00; sa 36; zvE 47 364.00; T = **9 634**; Soli base 9 634 ≤ 20 350 → 0;
church 0; income_taxes **9 634.00**; total **22 654.00**. With profit 0: base_m 1 318.33, kv 2 768.52, pv 664.44,
taxes 0 → business_tax 9 634.00, rate 0.160566… (9 634 / 60 000).

**O2 – Freiberufler, joint, 2 children (both under 25), PKV, Rürup 500/month, church 9 % – Kindergeld wins.**
Profit 90 000; pkv 600 (basis 480), care 60. social 13 920 (7 200 + 720 + 6 000). A 6 000, H 61 652, dA 6 000;
B 6 480; O 1 440; C 2 800 → dBO 6 480; sa 72; zvE 77 448. kfb 9 756, kg 3 108; child 1: T(77 448) − T(67 692) =
2 962 ≤ 3 108 → Kindergeld; child 2 same → 2 962 → Kindergeld; kfb_used 0. est_tarif = est = **13 616**.
Z = T(77 448 − 19 512) = **7 856**; Soli 0; church trunc2(7 856 × 9 %) = **707.04**; income_taxes **14 323.04**;
total **28 243.04**.

**O3 – same as O2 with profit 200 000 – Freibeträge win, Soli in the Milderungszone.** zvE 187 448; child deltas
4 098 > 3 108 both → kfb_used 2, z 167 936, est_tarif **48 260**, kg_added 6 216, est **54 476**; Z 48 260;
Soli = min(2 654.30, (48 260 − 40 700) × 11.9 % = 899.64) = **899.64**; church **4 343.40**; income_taxes
**59 719.04**; total **73 639.04**.

**O4 – Gewerbe, single, Hebesatz 450, GKV without Krankengeld, pension `statutory_income`, ALV voluntary, 1 child
(pv_parent, under 25), church 8 %.** Profit 80 000. kv base 5 812.50 (cap), rate 16.9 → kv 11 787.72; pv 3.6 % →
2 511.00; rv base 6 666.67 → 14 880.00; alv 1 233.96; social **30 412.68**. A 14 880, dA 14 880; B 14 298.72;
O 1 233.96 → dBO 14 298.72; sa 36; zvE 50 785.28. kfb 4 878, kg 1 554: delta T(50 785) − T(45 907) = 1 686 > 1 554
→ Freibetrag; est_tarif **9 139**, kg_added 1 554. GewSt: ertrag 80 000 − 24 500 = 55 500; mess 1 942.50; gewst
**8 741.25**; share 1; Höchstbetrag 9 139.00; credit min(7 770.00, 8 741.25, 9 139.00) = **7 770.00**.
est = 9 139 − 7 770 + 1 554 = **2 923.00**; Z 9 139; soli_base 1 369 → Soli 0; church trunc2(9 139 × 8 %) =
**731.12**; income_taxes **3 654.12**; total **42 808.05**.

**O5 – employment block plus Freiberufler side income, single, GKV, childless.** Profit 20 000; employment gross
50 000, sv_liable, steuerklasse 1, bav_employee 2 400, bav_employer 360, vwl_employer 480, withheld 7 000.
tax_wage = sv_wage = 48 080 (tax_free = sv_free = 2 760); emp_income 46 850. Monthly base 4 006.67: ee_kv (8.75 %)
4 206.96; ee_pv (2.4 %) 1 153.92; ee_rv 4 471.44; er_rv 4 471.44; ee_alv 625.08; social **10 457.40**. A 8 942.88,
dA 4 471.44; B 5 192.60; O 793.36; C 1 900 → dBO 5 192.60; sa 36; sum_income 66 850; zvE 57 149.96; est
**13 147**; Soli 0; income_taxes **13 147.00**; total **23 604.40**; balance **6 147.00** (back payment).

**O6 – UG/GmbH, Hebesatz 400; owner-manager salary 60 000 not SV-liable, PKV 700 (basis 560), care 70, no employer
subsidy, pension fixed 400/month.** GmbH profit 100 000: kst **15 000.00**, kst_soli **825.00**, gewst **14 000.00**
(corporate 29 825.00). Personal: emp_income 58 770; social 14 040; A 4 800, dA 4 800; B 7 560; O 1 680 →
dBO 7 560; sa 36; zvE 46 374; est **9 297**; income_taxes **9 297.00**; total **53 162.00**.

**O7 – low profit, GKV minimum base.** Freiberufler single, profit 12 000, GKV without Krankengeld, childless.
base_m 1 318.33 (floor); kv 16.9 % → 222.80/month → **2 673.60**; pv 4.2 % → 55.37/month → **664.44**; social
3 338.04; B 3 338.04 (> C, deducted in full); zvE 8 625.96 → est 0; total **3 338.04**.

**Legacy flat mode – L1.** `tax.estimate` in mode flat for the inputs of the three existing `test_domain.py` cases
returns the same values as the current float implementation to the cent (regression test computing the old values
literally in the test, not by calling old code).

## UI (vanilla JS, no build step)

* **Business settings** (`#/settings`, nav label "Business settings", boss as today): cards as in the grouping table.
  `A.saveSettings` sends only the business keys (the legacy tax keys are no longer read from the page and no longer
  sent; `S.settings` still carries them, so `{...S.settings}` must drop `personal_tax` and the five legacy keys
  before PUT – otherwise the stored legacy values are written back unchanged, which is harmless but noisy).
* **My settings** (`#/me`, new nav entry "My settings" for every role, `'*'`): Profile card, calendars card (if
  allowed), API tokens card, and for the boss a card "Personal tax profile" with a short summary (mode, health mode,
  children count) and a link to `#/me/tax`.
* **Personal tax profile** (`#/me/tax`, boss only, `PAGES.metax` in a new `web/taxprofile.js`, loaded from
  `index.html`): sections "Assessment" (single/joint, church tax %, other income, other deductions, other insurance),
  "Children" (count, full allowance, care-insurance parent/under-25 count), "Health and care" (GKV/PKV/none, then only
  the fields of the chosen mode; Zusatzbeitrag empty = average with the value shown as placeholder), "Pension"
  (mode + monthly amount for fixed), "Unemployment" (none / voluntary / half rate in the founding years),
  "Employment" (checkbox "I also have employment income" shows the block; Steuerklasse select with the hint "only
  affects monthly withholding, not the annual tax"), and a banner when `derived_from_legacy` ("Your old flat values
  are used. Save this page to switch to the full calculation.") with a "Keep the simple estimate" choice that saves
  `mode: "flat"`. Save button → PUT; warnings shown as banners. Footer: "Estimate only – not tax advice. Rates
  <year> from the official tables; values marked as not confirmed are listed."
* **Money & Taxes** (`PAGES.money`, `web/app2.js`): the "Tax burden" card shows for mode full: Einkommensteuer (with
  "incl. Kindergeld added back" when `kg_added > 0`), Soli, church tax, Gewerbesteuer with the §35 line, then a
  "Social insurance" block (health, care, pension, unemployment) from `insurance`, total, and – with employment –
  "already withheld" and "expected back payment / refund". An expandable "How this was computed" list shows zvE and
  `vorsorge` values. Redacted response: totals only, no expandable list. Link "Edit personal tax profile" for the
  boss.

## Work packages

Implementer: **Sonnet** (maintainer instruction overrides the Haiku default). One package = one branch = one pull
request, in this order; WP-B1 and WP-A1 can run in parallel.

| WP | Content | Files (owned) | Tests | Review tier |
|---|---|---|---|---|
| **A1 – profile storage and API** | `MonthlyAmount`; `PersonalTaxIn`/`PersonalTax` + nested models; `PersonalTaxService` (get, update, effective, legacy derivation, advisory warnings); `SettingsService.get` removes and `update` refuses `personal_tax`; two routes; `docs/validation.md` row; `make generate-openapi` | `core/validation.py`, `core/models.py`, `core/service/personal_tax.py` (new), `core/service/settings.py`, `core/service/__init__.py` (registration), `api/routers/settings.py`, `docs/validation.md`, `api/openapi.json` | new `tests/backend/test_personal_tax_api.py`: bounds (each field min/max ±1, `pkv_basis > pkv` → 422), unknown field round-trip, boss-only GET/PUT (employee, accountant, API token → 403), `PUT /settings` with `personal_tax` → 400 and stored profile unchanged, `GET /settings` and `/bootstrap` without the key, legacy derivation, PUT replaces; add an export/import round-trip of `personal_tax` to `tests/backend/test_data_export.py` | **Full** (rights, outside input) |
| **B1 – rule tables** | `tax_rules/2026.json`, `2025.json`, loader with fallback warning and `to_verify` list | `core/tax_rules/__init__.py`, `core/tax_rules/2026.json`, `core/tax_rules/2025.json` (new), packaging (`pyproject.toml` package data, as for `trip_rates`) | new `tests/backend/test_tax_rules.py`: every key present in every year, each value has source and valid_from, Decimals parse, fallback year + warning, 2026 values equal the table in this spec literally | **Full** (logic pass checks every number against the source list) |
| **B2 – engine** | rewrite `core/tax.py` on Decimal: tariff, flat mode port, full mode steps 1–12, GmbH, business share; keep `est_single`, `est`, `KLEIN_LIMIT_*` names (return `int` / `Decimal`) | `core/tax.py` | new `tests/backend/test_tax_estimate.py`: O1–O7 and L1 with every listed intermediate asserted exactly; property tests (hypothesis, small): total monotone in profit for fixed profile, `dBO ≥ B`, `dA ≤ H`, Soli never above 5.5 % of its base, Günstigerprüfung never worse than Kindergeld; update `test_domain.py` to exact asserts | **Full** (code, test, logic passes; logic pass recomputes O1–O7 by hand) |
| **B3 – finance integration** | `calc.finance` on Decimal, profile from `PersonalTaxService.effective()`, social from the estimate, warnings, redaction for non-boss | `core/calc.py`, `core/service/` file that calls `calc.finance` (only the profile hand-over), `api/routers/finance.py`, `api/openapi.json` | finance API test: boss sees full breakdown, accountant only `total, rate, gewst, kst, redacted`; month `social` = q2(social/12); take_home not double-counting social; flat mode identical to current numbers for an existing settings dict | **Full** (money, rights) |
| **A2 – web UI** | Business settings trimmed, My settings page, `#/me/tax` page, Money & Taxes card | `web/app2.js`, `web/app.js` (nav, route rights), `web/taxprofile.js` (new), `web/index.html`, `docs/` user guide section on the tax profile | `tests/web/`: settings page no longer shows the five legacy fields; tax profile round-trip (fill, save, reload); employee sees My settings but no tax link; money page shows social block | **Standard** (UI; no money logic in the browser) |

The randomized suites model the settings API: WP-A1 updates `tests/backend/randomized/` for the new key (refused in
`PUT /settings`, invisible in `GET /settings`) and runs `make test-summary SUITES="random"` once before merge.

## Open questions for the maintainer / tax adviser

1. **Rates TO VERIFY**: Sachsen employee care share 2.3 % (2026); half voluntary ALV contribution 51.42 vs 51.43;
   rounding of the Gewerbesteuer-Messbetrag; truncation of church tax to cents; all 2025 values except tariff, Soli
   and the §10 (3) cap.
2. Is the staged per-child Günstigerprüfung (each child tested on the zvE left after the previous children) what the
   Finanzamt does, and is counting half the Kindergeld for a non-jointly assessed parent right in all cases?
3. Soli base: `Z` reduced by a §35 credit recomputed on `Z` – confirm (§3 (2) SolZG refers to the tax "festzusetzen
   wäre"); church tax base without §35 per §51a (2) S. 3.
4. Voluntary GKV base: profit plus `other_income_year` (all income), clamped – enough for the estimate, or should
   rental/capital income be a separate input?
5. Owner-manager of a GmbH not SV-liable: keep the §10 (3) cap unreduced (non-goal today) or add a "pension promise
   without own contributions" flag that cuts it (§10 (3) S. 3)?
6. Should the accountant see the full tax breakdown (e.g. a tax adviser in that role), or keep the redaction? Should the
   legacy fields `church_tax`, `splitting`, `other_income` stay visible in `GET /settings` for the accountant (they are
   today)?
7. Users who book Gewerbesteuer as an expense get a too-low profit (GewSt is not deductible, §4 (5b) EStG): add a
   warning in the expense categories, or leave it?
8. `PUT /settings` with a legacy tax key while a profile exists is stored but has no effect: add an advisory warning, or
   refuse the legacy keys after a grace period?
9. Monthly reserve: keep the marginal "business share" rate, or reserve the full household tax minus `withheld_year`?
