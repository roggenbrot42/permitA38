# Specification: settings split and real tax burden estimate

Status: **proposed, not scheduled** (roadmap rows "Settings split into business and user" and "Real tax burden
estimate", maintainer request of 2026-11-02; maintainer decisions of 2026-11-03 included). Author: architect.
Implementation: Sonnet implementer agents, one work package each. Review tiers per package, see "Work packages". The
implementer follows this document, does not decide tax questions, and lists every choice it had to make under "Open
design decisions" in its report.

All figures in this document are fake demo figures. Rates were checked on 2026-11-03 against the sources named in
"Rule tables"; values that could not be confirmed from an official text are marked **TO VERIFY** and must be
confirmed by the maintainer's tax adviser before the feature is released.

The estimate is **deliberately approximate**: the product owner accepts a deviation of about 100 EUR per year. Where
an exact rule would need inputs the app does not have, this spec takes the simpler rule and says so.

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
   with exact decimals (`core/money.py`), with year-dated rule tables. The Finance page shows the projected year, a
   monthly reserve and what is **currently owed** on the profit booked so far.

## Scope and non-goals

In scope: one taxpayer (plus a spouse only through joint assessment, see below), assessment years with a rule table,
the categories listed in the goal, a monthly reserve and a "currently owed" view derived from the estimate.

**No backwards compatibility (alpha).** The old flat tax fields (`social_monthly`, `church_tax`, `splitting`,
`other_income`, `other_deductions`) are removed from the models, the API and the UI; there is no flat mode and no
derivation from the old values (see "Legacy keys").

Non-goals (v1), each shown as a sentence on the tax profile page:

* **Not tax advice.** The result is a planning estimate; the page and `/finance` say so.
* Lohnsteuer withholding (Programmablaufplan). The **Steuerklasse is informational only**: it never changes the
  annual Einkommensteuer, which is what this feature computes. The user may enter the tax already withheld
  (`withheld_year`) to see the expected back payment or refund.
* Einkommensteuer-Vorauszahlungen and Gewerbesteuer-Vorauszahlungen are not recorded; "currently owed" and the
  reserve ignore them.
* The spouse's own income, insurance and caps (put the spouse's net income into `other_income_year`; the spouse's
  deductible insurance into `other_deductions_year`). Under joint assessment the §10 (3) cap is doubled by law, the
  §10 (4) cap is the taxpayer's own.
* Rental and capital income have no separate input: they go into `other_income_year`, which (like the profit) is part
  of the voluntary public health insurance base.
* Capital income (Abgeltungsteuer), GmbH dividends, Teileinkünfteverfahren, loss carry-forward, §34 / §34a, the
  Progressionsvorbehalt (Elterngeld, Krankengeld), Riester (§10a), Arbeitnehmersparzulage, Kirchensteuer-Kappung,
  Kinderbetreuungskosten, Gewerbesteuer additions/deductions (§§8, 9 GewStG), Gewerbe loss carry-forward,
  Pflegeversicherung under 23 years, Künstlersozialkasse, Beamte/Beihilfe.
* **Known limitation:** the §10 (3) S. 3 cut of the Altersvorsorge cap (e.g. a GmbH managing director with a pension
  promise but no own contributions) is not modelled; the cap is never reduced.
* Mixed insurance cases where self-employment is the **main** occupation and an SV-liable job is a side job (the
  model assumes: an SV-liable job, if present, is the main one and carries the public health insurance).
* Booking of Gewerbesteuer and Einkommensteuer: the estimate assumes they are **not** booked as ordinary expenses
  (Gewerbesteuer is not deductible, §4 (5b) EStG). Correct booking belongs to double-entry accounting (roadmap).

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
| social_monthly, church_tax, splitting, other_income, other_deductions | **removed**; replaced by the **Personal tax profile** page `#/me/tax` (boss only), linked from My settings |
| – | **My settings** → card "Profile": name, username, role, own weekly hours and hourly rate (read-only, from `GET /auth/me`; editing stays on the Team page), buttons "Change password" and "Two-factor authentication" (same actions as the sidebar) |

Hourly rates stay where they are: the business default `default_rate` in Business settings, the per-user
`default_rate` / `rate` on the user record (Team page). No field changes owner.

### Storage and API

The `settings` table is a key/value store (`schema.py`: `key:str value:json`), so **no migration** is needed.

* New settings key **`personal_tax`**: one JSON object, the personal tax profile (model below).
* `SettingsService.get()` removes `personal_tax` and the five legacy keys from its result (like
  `team_timeline_visibility`), so `GET /settings`, `GET /bootstrap` and the accountant never see them.
* `SettingsService.update()` refuses a body that contains `personal_tax` with `ServiceError(400, "The personal tax
  profile is changed with PUT /api/settings/personal-tax")`, before anything is stored. The stored `personal_tax`
  survives every `PUT /settings` (the service merges over the stored settings; add a test).
* New router functions in `permita38/api/routers/settings.py`, both boss-only via
  `Needs("settings", "write", full=True)` (the same dependency as `data_transfer.py`) **and** `SessionOnly`: API
  tokens (and the MCP server, which uses tokens) never read or write the profile:
  * `GET /api/settings/personal-tax` (`dependencies=[SessionOnly]`) → `PersonalTax`: the stored profile, or the
    defaults with `stored: false` when none is stored.
  * `PUT /api/settings/personal-tax` (`dependencies=[SessionOnly]`) with body `PersonalTaxIn` → **replaces** the
    stored profile (not a merge; nested objects make a merge ambiguous) and returns `PersonalTax` (`stored: true`)
    plus `warnings: list[str]` (advisory, never stored, see "Advisory warnings").
* Logic lives in a new `permita38/core/service/personal_tax.py` (`PersonalTaxService(store)` with `get()`,
  `update(data)`, `effective()`), registered next to `SettingsService` in `permita38/api/deps.py`. The pure function
  `effective_personal_tax(raw_settings: dict) -> dict` (defaults + defensive read) lives in
  `permita38/core/settings.py` next to `effective_settings`, so `calc.finance` can use it without importing a service;
  `PersonalTaxService.effective()` delegates to it.
* Data export already writes every non-secret setting, so `personal_tax` is exported and imported as-is (the export is
  boss-only). The engine reads the stored object defensively (below), so a damaged import cannot break `/finance`.
* MCP server (PR #73, not on `main` at the time of writing): if it exposes settings or finance, it must apply the same
  removal and redaction; the MCP work package owner checks this when rebasing.

### Legacy keys

`LEGACY_TAX_KEYS = ("social_monthly", "church_tax", "splitting", "other_income", "other_deductions")`, a constant in
`permita38/core/settings.py`.

* **Models.** The five fields are deleted from `Settings` and `SettingsIn`. Because `SettingsIn` is a `StrictRecord`
  (unknown fields are kept), deleting them is not enough: `SettingsIn` gets a `model_validator(mode="before")` that
  raises `ValueError("<key> was removed; use the personal tax profile (PUT /api/settings/personal-tax)")` when the body
  contains any of the five keys, so `PUT /settings` answers **HTTP 422** and stores nothing.
* **Stored values are ignored and deleted lazily.** Nothing reads them any more (`tax.py` and `calc.py` read only the
  profile after WP-B3). `SettingsService.get()` strips them from its output; `SettingsService.update()` drops them from
  the merged dict before `save_settings` (which replaces the whole table), so they vanish from storage on the first
  successful `PUT /settings`. There is no migration and no one-off clean-up.
* **Import.** An old export that still contains the keys is imported unchanged (no refusal); the same two rules then
  hide and later delete them.
* Interim: until WP-B3 lands, the old `tax.estimate` still reads the raw stored keys. All packages ship in the same
  release, so no released version shows the mixed state.

### `/finance` redaction

`/finance` is readable by the accountant (`finance: (A, N)`). Amended 2026-11-03 (maintainer decision 2): **only the
boss in a browser session** sees anything derived from the personal profile. Everyone else – the accountant, any API
token including a boss token (`rights.is_token`), and any later caller such as an MCP finance tool – gets **business
figures only**. Criterion per key: could the caller derive the owner's personal tax, insurance or income from it (also
by subtracting visible values)? If yes, it goes.

`calc.redact_tax(result) -> dict` **builds a new dict from an allowlist** at every level (it never copies `result` and
deletes keys, so a key added to `finance` later stays hidden until it is added here). The router
`api/routers/finance.py` applies it as today (`is_token(user) or role != boss`).

| level | kept (allowlist) | removed |
|---|---|---|
| top level | `year`, `months`, `totals`, `categories`, `open_receivables`, `kleinunternehmer`, `company_type`, `projected_profit`, `extrapolated`, `warnings` (filtered), `tax_actual`, `tax_projected`, `tax_owed`, `reserve` (all four reduced, see below), plus `redacted: true` | `effective_rate`, `social_year`, `take_home`, every key not listed |
| `months[]` | `month`, `revenue`, `vat_out`, `expenses`, `vat_in`, `profit`, `vat_due`, `vat_set_aside` | `tax_set_aside`, `social`, `take_home` |
| `tax_actual`, `tax_projected` | `gewst`, `kst`, `kst_soli`, `redacted: true` | `profit`, `social`, `est`, `soli`, `church`, `gewst_credit`, `income_tax_total`, `total`, `rate`, `zve`, `tax_due`, `withheld`, `est_tarif`, `kg_added`, `kfb_used`, `zuschlag_base`, `soli_base`, `vorsorge`, `insurance`, `emp_income`, `tax_wage`, `sv_wage`, `balance`, `warnings` |
| `tax_owed` (null stays null) | `as_of`, `gewst`, `kst`, `kst_soli`, `redacted: true` | `profit_ytd`, `est`, `soli`, `church`, `tax_due`, `withheld`, `owed` |
| `reserve` | `redacted: true` only | `due_year`, `withheld`, `owed_now`, `remaining`, `months_left`, `monthly`, `balance` |
| `warnings` | every warning except `NOT_SAVED_WARNING` (the others come from the rule tables and the Kleinunternehmer check) | `NOT_SAVED_WARNING` (it tells whether the owner saved a profile) |

Why per key: `gewst`, `kst`, `kst_soli` depend only on the profit, the Hebesatz and the company type;
`projected_profit` / `extrapolated` only on the profit and the date. `gewst_credit` goes because its term
`q2(est_tarif × share)` reveals the income tax whenever it is the binding minimum; `total`, `rate`, `tax_due`,
`effective_rate`, `tax_set_aside` (the reserve) and `owed` contain the household income tax; `social` and `take_home`
reveal the insurance; `profit_ytd` is business-only but redundant (`totals.profit`) and dropped to keep the list short.

**What a non-boss sees on Money & Taxes** (rendering guard in `web/app2.js`, branching on `f.redacted`; WP-B3b):
* "Set aside for <month>" card: profit this month and VAT to pay only; no tax reserve, social or take-home.
* The tax card is headed "Business taxes <year>": Gewerbesteuer (`gewerbe`, `gmbh`), Körperschaftsteuer and Soli on
  KSt (`gmbh`), "No business taxes (Freiberufler)" otherwise, then VAT payable; note "Income tax, social insurance and
  take-home are shown to the owner only." The projected-profit sentence stays without the tax rate.
* Month table without the "Tax reserve" and "Take-home" columns; no "Currently owed" rows other than Gewerbesteuer /
  KSt / Soli on KSt (only for `gewerbe` / `gmbh`); no monthly reserve line; no expandable list.

### Models (`core/models.py`, primitives in `core/validation.py`)

New primitive in `validation.py`: `MonthlyAmount = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]`.
Yearly amounts use the existing `NonNegAmount` (0 … 1 000 000 000). All `*In` models derive from `v.StrictRecord`
(unknown fields kept); all response models use `extra="allow"`.

```text
PersonalTaxIn (StrictRecord)                        default
  assessment           "single" | "joint"           "single"
  church_tax_percent   float 0 … 15                 0        (8 in BY/BW, 9 elsewhere; hint only)
  other_income_year    NonNegAmount                 0        other positive income after its own costs
  other_deductions_year NonNegAmount                0        Sonderausgaben/außergew. Belastungen besides insurance
  other_insurance_year NonNegAmount                 0        liability, disability, term life (§10 (1) Nr. 3a)
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
            withheld_year NonNegAmount | null = null (Lohnsteuer + Soli + KiSt withheld for the whole year)
```

`PersonalTax` (response, `extra="allow"`): the same fields with lenient types plus `stored: bool` and
`warnings: list[str] | None`. Add every field to both models (AGENTS.md); `docs/validation.md` gets one table row
"Personal tax profile" with these bounds, and its "Settings" row loses "church tax 0–15 %".

**Defensive read.** `effective_personal_tax()` validates the stored object with the lenient model; an invalid or
missing nested value falls back to its default with a logged warning (same rule as `calc.py`), never an error.
Without a stored profile the defaults apply (single, GKV voluntary with Krankengeld, average Zusatzbeitrag,
childless, no pension, no unemployment insurance, no employment) and the estimate carries the warning "No personal
tax profile saved; the estimate uses the defaults (single, public health insurance, no children)."

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
| `pv_employer_share`, `pv_employer_share_sn` | 1.8, 1.3 (the employee pays the rest: 1.8 / 2.3 with one child; childless surcharge and child reductions are the employee's) | §58 SGB XI (insurer table 2026); **Sachsen value TO VERIFY** |
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
`profit` is the business profit of the year (may be negative), `settings` gives `company_type`, `state` and
`hebesatz` (nothing else is read from the settings), `profile` is `effective_personal_tax()`. Notation below: `q2` =
half-up to cents (`money.q2`), `trunc2` = truncate to cents, `floor0` = round down to whole euros, `pos(x) = max(x,
0)`. Percent values are divided by 100. There is one algorithm (no modes).

### Algorithm

**1. Employment wage** (only with `employment`):
`tax_free = min(bav_employee + bav_employer, 8 % × bbg_rv_year)`,
`sv_free = min(bav_employee + bav_employer, 4 % × bbg_rv_year)`,
`tax_wage = gross + vwl_employer + bav_employer − tax_free`, `sv_wage = gross + vwl_employer + bav_employer − sv_free`,
`emp_income = pos(tax_wage − max(werbungskosten_year, an_pauschbetrag))`.

**2. Employee contributions** (only with `employment.sv_liable`): monthly bases
`kv_m = min(q2(sv_wage / 12), bbg_kv_month)`, `rv_m = min(q2(sv_wage / 12), bbg_rv_month)`; annual amounts are
`12 × q2(base × rate)`:
`ee_kv` at `kv_rate_general/2 + zusatz/2`, `ee_pv` at `pv_total − pv_employer_share` (`pv_employer_share_sn` when
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
* Altersvorsorge `A = self_rv + ee_rv + er_rv`; cap `H = vorsorge_hoechst_altersvorsorge × (2 if joint)` (never
  reduced, see "Known limitation"); deductible `dA = pos(min(A, H) − er_rv)`.
* Basis cover `B`: gkv voluntary `kv − cut + pv` with `cut = q2(kv × 4 %)` if `krankengeld` else 0; gkv employed
  `ee_kv − cut + ee_pv` with `cut = q2(ee_kv × 4 %)` (employees always have Krankengeld); pkv
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
`share = pos(biz) / (sum of the positive items among biz, emp_income, other_income_year)` (0 when that sum is 0);
`credit = min(4 × mess, gewst, q2(est_tarif × share))`. For the Soli base the credit is recomputed with `Z`:
`credit_z = min(4 × mess, gewst, q2(Z × share))`. Freiberufler: all 0.

**10. Taxes.** `est = est_tarif − credit + kg_added` (festzusetzende Einkommensteuer);
`soli_base = pos(Z − credit_z)`; `soli = 0 if soli_base ≤ freigrenze (× 2 joint) else trunc2(min(soli_base × 5.5 %,
(soli_base − freigrenze) × 11.9 %))`; `church = trunc2(Z × church_tax_percent)` (no §35 credit in the church-tax
base, §51a (2) S. 3; truncation **TO VERIFY**, Länder law).
`income_taxes = est + soli + church`.

**11. GmbH (company_type `gmbh`).** Corporate side on the GmbH profit (after the managing director's salary):
`kst = trunc2(floor0(pos(profit)) × kst_rate)`, `kst_soli = trunc2(kst × 5.5 %)` (no Freigrenze for corporations),
`gewst = q2(floor(pos(profit) / 100) × 100 × 3.5 % × hebesatz)` (no Freibetrag), no §35. Personal side: steps 1–10
with `biz = 0` (salary in the employment block, usually `sv_liable: false`).

**12. Result.** `social = kv + pv + self_rv + alv_self + ee_kv + ee_pv + ee_rv + ee_alv − subsidy`;
`tax_due = income_taxes + gewst + kst + kst_soli` (all taxes of the household and the business, no social
insurance); `total = tax_due + social`; `withheld = withheld_year or 0`; `balance = income_taxes − withheld` (only
when `withheld_year` is given, else null); `rate = tax_due / profit` if `profit > 0` else 0 (an indicator for the
page; it includes the employment-related tax). There is no "business share" re-run: nothing uses it any more.

Returned dict (Decimals inside the engine; `calc.finance` converts with `to_float`): the existing keys `profit, social,
est, soli, church, gewst, kst, gewst_credit, income_tax_total, total, rate, zve` keep their names (`social` = step
12, `income_tax_total` = `income_taxes`), plus `kst_soli, tax_due, withheld, est_tarif, kg_added, kfb_used,
zuschlag_base, soli_base, vorsorge: {A, H, dA, B, O, C, dBO}, insurance: {kv, pv, rv, alv, ee_kv, ee_pv, ee_rv,
ee_alv, subsidy}, emp_income, tax_wage, sv_wage, balance, warnings: list[str]`.

### Integration in `calc.finance` (float migration)

`finance(store, year, today)` reads `settings` and `profile = effective_personal_tax(store.settings())`.

* `profit_ytd = tot.profit` (Decimal, the profit booked in `year`). **Projection** (amended 2026-11-03, maintainer
  decision 1: no projection before 60 days). `days = (today − date(year, 1, 1)).days + 1` – the inclusive day of the
  year: 1 Jan is day 1, 31 Dec is day 365 (366 in a leap year). Current year (`year == today.year`):
  * `days < 60` (1 Jan … 28 Feb in every year, day 59 = 28 Feb): `projected = pos(profit_ytd)`, `extrapolated = false`
    – no extrapolation;
  * `days ≥ 60` (from 1 Mar, in a leap year from 29 Feb): `projected = pos(q2(profit_ytd × 365 / min(days, 365)))`,
    `extrapolated = true` (the 30-day floor is gone; the 365 cap keeps 31 Dec of a leap year at factor 1).
  Past and future years: `projected = pos(profit_ytd)`, `extrapolated = false` (unchanged). No float enters
  `tax.estimate`. New response key `extrapolated: bool` (business-only, kept in the redacted view).
* `proj = estimate(projected, …)` → `tax_projected`; `actual = estimate(profit_ytd, …)` → `tax_actual`.
  **Before day 60** both get the same profit (when `profit_ytd ≥ 0`), so `proj == actual` and, with the unchanged
  formulas below, `due_year = owed_now`, `remaining = 0`, `monthly = 0`, `months_left = 13 − today.month` (12 or 11);
  the whole owed amount is set aside in the current month by the catch-up (see Months). On day 60 the figures jump to
  the linear projection; that is intended.
* **Currently owed** (`tax_owed`, current and past years; `null` for future years): what the assessment would ask
  for if the business booked nothing more this year. It uses `actual` (profit booked so far, **no annualisation**,
  same engine and tax year). The employment block, `other_*` amounts and `withheld_year` are annual inputs and are
  used in full: the employer withholds on the full wage, so pro-rating them would show not-yet-withheld wage tax as
  owed. `owed = actual.tax_due − actual.withheld` (signed; negative = refund so far). Fields:
  `tax_owed = {as_of: today (current year) or "YYYY-12-31", profit_ytd, est, soli, church, gewst, kst, kst_soli,
  tax_due, withheld, owed}`. Vorauszahlungen are not recorded and therefore not subtracted.
* **Monthly reserve** (`reserve`, option 2 of the maintainer decision): the full household tax of the projected year
  minus the tax withheld, spread over the remaining months. `due_year = pos(proj.tax_due − proj.withheld)`.
  Current year: `months_left = 13 − today.month` (the current month counts), `remaining = pos(due_year −
  pos(tax_owed.owed))` – the owed amount is what should already be set aside today, so only the rest is spread –,
  `monthly = q2(remaining / months_left)`. Future year: `months_left = 12`, `remaining = due_year`. Past year:
  `months_left = 0`, `remaining = monthly = 0` (the owed figure is the answer). Fields:
  `reserve = {due_year, withheld, owed_now, remaining, months_left, monthly, balance}` (`owed_now = pos(owed)` or 0,
  `balance = proj.balance`). Social insurance is not part of the reserve; it is shown per month as `social`.
* **Months** (amended 2026-11-03, maintainer decision 3: one `take_home` definition).
  * **Counted months** `c`: `today.month` for the current year, 12 for a past year, 0 for a future year (the months
    that have begun).
  * **Start month** `s`: `today.month` for the current year, 12 for a past year, 1 for a future year.
    `m.tax_set_aside = 0` for `m < s`; `= owed_now + monthly` for `m = s` (**catch-up**: the tax on the profit already
    booked is set aside now, nothing records earlier set-asides); `= monthly` for `m > s`. The months therefore add up
    to `owed_now + months_left × monthly` = `due_year` up to the cents of `monthly` (past year: December carries
    `owed_now`).
  * **Self-paid social insurance** `self_social = proj.insurance.kv + pv + rv + alv − subsidy` (the employee shares
    `ee_*` are withheld from the wage by the employer and never leave the business account);
    `social_m = q2(self_social / 12)`; `m.social = social_m` for `m ≤ c`, 0 for `m > c`.
  * `m.take_home = m.profit − m.tax_set_aside − m.social` for `m ≤ c`; `null` for `m > c` (the month has not begun).
  * Yearly: `social_year = social_m × c` (= sum of the months' `social`); `take_home = sum of m.take_home over
    m ≤ c` (0 for a future year). Current year: `= profit(1..c) − owed_now − monthly − c × social_m`.
  * **Definition** (one meaning, monthly and yearly): take-home is the cash a begun month leaves in the business after
    its own set-asides – tax reserve and self-paid insurance; the year is the sum of its begun months. Wage, withheld
    wage tax and the employee shares stay outside because they never pass through the business account.
  * **Withheld tax and refunds** enter only through `tax_due − withheld` inside `owed` / `due_year`; a refund (withheld
    above the tax) clamps the set-aside to 0 and is never added to `take_home` (the boss sees it as `balance`).
  * Profit booked in a month after `c` (a future-dated invoice) counts in `profit_ytd` and the taxes but not in
    `take_home`.
* `effective_rate = rnd(proj.rate, 4)`; `projected_profit = projected`.
* `tax_actual` / `tax_projected` warnings (including "No personal tax profile saved …") are appended to the response
  `warnings`, deduplicated.
* `tests/backend/test_domain.py`: the tolerant asserts become exact (`T(17800) == 1035`, `T(69879) == 18213`,
  `T(100000) == 30864`).

## Oracle test cases (2026 table)

O1–O7 were computed by the architect with a scratch script and recomputed on 2026-11-03 with a second, independently
written implementation (not in the repo); the F-cases were redone for the 2026-11-03 amendment (see "F-cases").
The logic review recomputes each line independently. Anchor: `T(100 000) = 30 864` (existing test). All cases: year 2026, no `other_*` amounts unless stated, `zusatzbeitrag_percent` 2.9 where GKV,
business `state` not `SN`.

**O1 – Freiberufler, single, GKV voluntary with Krankengeld, no pension/ALV, childless.** Profit 60 000.
base_m 5 000.00; kv rate 17.5 → kv 10 500.00; pv 4.2 % → 2 520.00; social 13 020.00. B = 10 500 − 420 + 2 520 =
12 600.00; O 420.00; C 2 800 → dBO 12 600.00; sa 36; zvE 47 364.00; T = **9 634**; Soli base 9 634 ≤ 20 350 → 0;
church 0; income_taxes **9 634.00**; tax_due **9 634.00**; total **22 654.00**; rate 9 634 / 60 000 = 0.160566…
(**changed**: `rate` is now `tax_due / profit`; the business-share re-run with profit 0 is gone).

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
**731.12**; income_taxes **3 654.12**; tax_due **12 395.37**; total **42 808.05**.

**O5 – employment block plus Freiberufler side income, single, GKV, childless.** Profit 20 000; employment gross
50 000, sv_liable, steuerklasse 1, bav_employee 2 400, bav_employer 360, vwl_employer 480, withheld 7 000.
tax_wage = sv_wage = 48 080 (tax_free = sv_free = 2 760); emp_income 46 850. Monthly base 4 006.67: ee_kv (8.75 %)
4 206.96; ee_pv (2.4 %) 1 153.92; ee_rv 4 471.44; er_rv 4 471.44; ee_alv 625.08; social **10 457.40**. A 8 942.88,
dA 4 471.44; B 5 192.60; O 793.36; C 1 900 → dBO 5 192.60; sa 36; sum_income 66 850; zvE 57 149.96; est
**13 147**; Soli 0; income_taxes **13 147.00**; total **23 604.40**; balance **6 147.00** (back payment).

**O6 – UG/GmbH, Hebesatz 400; owner-manager salary 60 000 not SV-liable, PKV 700 (basis 560), care 70, no employer
subsidy, pension fixed 400/month.** GmbH profit 100 000: kst **15 000.00**, kst_soli **825.00**, gewst **14 000.00**
(corporate 29 825.00). Personal: emp_income 58 770; social 14 040; A 4 800, dA 4 800; B 7 560; O 1 680 →
dBO 7 560; sa 36; zvE 46 374; est **9 297**; income_taxes **9 297.00**; tax_due **39 122.00**; total **53 162.00**.

**O7 – low profit, GKV minimum base.** Freiberufler single, profit 12 000, GKV without Krankengeld, childless.
base_m 1 318.33 (floor); kv 16.9 % → 222.80/month → **2 673.60**; pv 4.2 % → 55.37/month → **664.44**; social
3 338.04; B 3 338.04 (> C, deducted in full); zvE 8 625.96 → est 0; total **3 338.04**.

**F-cases (finance level).** Unless stated: settings `freiberufler`, `state` BY, Hebesatz 400; profile `{}` (= O1
profile: single, GKV voluntary with Krankengeld, average Zusatzbeitrag 2.9, childless, no church tax); one paid invoice
(no VAT) dated **2026-02-10**; `calc.finance(repo, year, today)` with a pinned `today`. Month lists are Jan…Dec;
"null" = `take_home` of a month that has not begun. Provenance (2026-11-03 amendment): the tax figures of the
O1-profile cases (F1, F3–F6, F8, F10, F11) were recomputed with an independent plain-Python script from the rate
tables (tariff, GKV/PV, Vorsorge, Soli) and cross-checked against the engine's `tax.estimate`; F2, F7 and F9 take
their tax figures from the independently verified O5 and O4 intermediates and were checked against the engine (F7's
balance and owed by engine and by hand); the month figures (set-aside, social, take-home, sums) of all cases were
derived by hand from the rules in "Integration". **Changed** values are marked.

**F1 – owed and reserve, O1 profile.** `today = 2026-05-26` (day 146), 24 000 booked. projected = q2(24 000 × 365 /
146) = **60 000.00**, `extrapolated` true → `tax_projected` = O1, due_year **9 634.00**. `tax_actual` on 24 000:
base_m 2 000.00; kv 4 200.00; pv 1 008.00; social 5 208.00; B = 4 200 − 168 + 1 008 = 5 040.00; O 168.00; dBO
5 040.00; sa 36; zvE 18 924.00; T(18 924) = (173.10 × 0.1125 + 2 397) × 0.1125 + 1 034.87 = 1 306.72 → **1 306**;
tax_owed.owed **1 306.00**. reserve: months_left 8, remaining 9 634 − 1 306 = **8 328.00**, monthly **1 041.00**.
`tax_set_aside`: Jan–Apr 0, May **2 347.00** (**changed**, catch-up 1 306 + 1 041; was 1 041), Jun–Dec 1 041.00
(sum 9 634.00). self_social 13 020.00 → social_m **1 085.00**; `social` Jan–May 1 085.00, Jun–Dec **0** (**changed**,
was 1 085 in every month); social_year **5 425.00**. `take_home`: Jan −1 085.00, Feb 22 915.00, Mar −1 085.00, Apr
−1 085.00, May −3 432.00, Jun–Dec null; yearly **16 228.00** (**changed**, was 17 269; the old months summed to
17 534 over Jan–May).

**F2 – owed and reserve with employment, O5 profile.** `today = 2026-05-26`, 8 000 booked. projected **20 000.00** →
`tax_projected` = O5; due_year = 13 147 − 7 000 = **6 147.00**. `tax_actual` on 8 000: insurance as O5 (employed,
nothing on the profit); zvE 57 149.96 − 12 000 = 45 149.96; T(45 149) = (173.10 × 2.735 + 2 397) × 2.735 + 1 034.87
= 8 885.49 → **8 885**; owed = 8 885 − 7 000 = **1 885.00**. reserve: months_left 8, remaining 6 147 − 1 885 =
**4 262.00**, monthly **532.75**. `tax_set_aside`: Jan–Apr 0, May **2 417.75** (**changed**), Jun–Dec 532.75.
self_social **0** (kv = pv = rv = alv = 0; all 10 457.40 are employee shares) → `social` 0 in every month
(**changed**, was 871.45), social_year **0.00**. `take_home`: Jan 0, Feb 8 000.00, Mar 0, Apr 0, May −2 417.75,
Jun–Dec null; yearly **5 582.25**.

**F3 – early lump sum (January boundary).** 60 000 booked 2026-01-10, `today = 2026-01-10` (day 10). projected
**60 000.00**, `extrapolated` false (the old rule projected 730 000.00 → tax_due 319 054.15, monthly 25 785.01).
`tax_projected` = `tax_actual` = O1: tax_due **9 634.00**; owed **9 634.00**; reserve due_year 9 634.00, owed_now
9 634.00, remaining **0.00**, months_left **12**, monthly **0.00**. `tax_set_aside` Jan **9 634.00**, Feb–Dec 0.
social_m 1 085.00 (Jan only); `take_home` Jan **49 281.00**, Feb–Dec null; yearly 49 281.00; social_year 1 085.00.

**F4 – day 59 (no projection).** `today = 2026-02-28`. projected **24 000.00**, `extrapolated` false; `tax_projected`
= `tax_actual`: social 5 208.00, tax_due **1 306.00**; owed 1 306.00; due_year 1 306.00, owed_now 1 306.00, remaining
0.00, months_left **11**, monthly **0.00**. `tax_set_aside` Feb **1 306.00**, all other months 0. social_m **434.00**
(Jan, Feb); `take_home` Jan −434.00, Feb **22 260.00**, Mar–Dec null; yearly **21 826.00**; social_year 868.00.

**F5 – day 60 (linear projection).** `today = 2026-03-01`. projected q2(24 000 × 365 / 60) = **146 000.00**,
`extrapolated` true. `tax_projected` (KV ceiling, zone 4, full Soli): base_m 5 812.50; kv 12 × 1 017.19 = 12 206.28;
pv 12 × 244.13 = 2 929.56; social **15 135.84**; cut 488.25; B 14 647.59; dBO 14 647.59; zvE **131 316.41**;
T = 0.42 × 131 316 − 11 135.63 = 44 017.09 → **44 017**; Soli base 44 017 > 20 350: min(2 420.935, 2 816.37) →
trunc2 **2 420.93**; tax_due **46 437.93**. owed 1 306.00 (as F4); months_left **10**; remaining **45 131.93**;
monthly **4 513.19**. `tax_set_aside` Jan, Feb 0, Mar **5 819.19**, Apr–Dec 4 513.19. social_m **1 261.32**;
`take_home` Jan −1 261.32, Feb 22 738.68, Mar −7 080.51, Apr–Dec null; yearly **14 396.85**; social_year 3 783.96.

**F6 – December boundary.** `today = 2026-12-15` (day 349). projected q2(24 000 × 365 / 349) = **25 100.29**;
`tax_projected`: social 5 446.80 (kv 4 392.60, pv 1 054.20), zvE 19 793.19, tax_due **1 519.00**. owed 1 306.00;
months_left **1**; remaining **213.00**; monthly **213.00**; `tax_set_aside` Jan–Nov 0, Dec **1 519.00**. social_m
**453.90** (all 12 months); `take_home` Jan −453.90, Feb 23 546.10, Mar–Nov −453.90, Dec −1 972.90; yearly
**17 034.20**; social_year 5 446.80.

**F7 – refund (withheld above the tax).** F2 with `withheld_year` **20 000**. `tax_projected`: tax_due 13 147.00,
balance **−6 853.00**; `tax_owed.owed` **−11 115.00**; reserve owed_now 0.00, due_year **0.00**, remaining 0.00,
monthly 0.00; `tax_set_aside` 0 in every month; social 0; `take_home` Feb 8 000.00, Jan/Mar/Apr/May 0, Jun–Dec null;
yearly **8 000.00** (the refund is not added).

**F8 – zero and negative profit.** `today = 2026-05-26`. (a) Nothing booked: projected 0.00; tax_due 0.00 (projected
and actual); social at the minimum base: kv 12 × 230.71 = 2 768.52, pv 12 × 55.37 = 664.44, self_social **3 432.96**,
social_m **286.08**; owed 0.00, reserve all 0.00; `take_home` Jan–May −286.08 each, yearly **−1 430.40**; social_year
1 430.40. (b) Instead of the invoice one paid expense of 5 000 net, 0 % VAT, dated 2026-02-10: profit_ytd −5 000.00,
projected **0.00** (`pos`); `tax_actual` tax_due 0.00, social 3 432.96; owed 0.00; `take_home` Feb **−5 286.08**,
yearly **−6 430.40**.

**F9 – Gewerbe through `calc.finance`, past-year literal (O4).** Settings `gewerbe`, Hebesatz 450, `state` BY; O4
profile; 80 000 booked 2026-02-10; `today = 2027-03-15`, `year = 2026`. projected **80 000.00**, `extrapolated`
false; `tax_projected` = `tax_actual` = O4: gewst **8 741.25**, gewst_credit 7 770.00, tax_due **12 395.37**, social
30 412.68. `tax_owed`: as_of **"2026-12-31"**, owed **12 395.37**, gewst 8 741.25. reserve months_left 0, remaining
0.00, monthly 0.00, owed_now 12 395.37; `tax_set_aside` Jan–Nov 0, Dec **12 395.37**. social_m **2 534.39** (all 12
months); `take_home` Feb 77 465.61, Dec −14 929.76, the other ten months −2 534.39; yearly **37 191.95**;
social_year **30 412.68**. Redacted: `tax_actual` = `tax_projected` = `{gewst: 8741.25, kst: 0, kst_soli: 0,
redacted: true}`, `tax_owed = {as_of: "2026-12-31", gewst: 8741.25, kst: 0, kst_soli: 0, redacted: true}`.

**F10 – future-year literal.** `today = 2026-05-26`, `year = 2027`, 24 000 booked **2027-02-10**. projected
**24 000.00**, `extrapolated` false; `tax_projected` tax_due **1 306.00** (2027 falls back to the 2026 table: warning
"No tax rules for 2027; using 2026."); `tax_owed` **null**; reserve due_year 1 306.00, owed_now 0.00, months_left
**12**, remaining 1 306.00, monthly **108.83**; `tax_set_aside` 108.83 in every month; `social` 0 and `take_home`
null in every month; take_home **0.00**; social_year **0.00**.

**F11 – leap year (projection only).** 24 000 booked 2028-01-15, `year = 2028`: `today = 2028-02-28` (day 59) →
projected **24 000.00**, `extrapolated` false; `2028-02-29` (day 60) → **146 000.00**, true; `2028-12-31` (day 366)
→ **24 000.00**, true. Non-leap check: `2026-12-31` (day 365) → 24 000.00 with the invoice of F1.

## UI (vanilla JS, no build step)

* **Business settings** (`#/settings`, nav label "Business settings", boss as today): cards as in the grouping table.
  The five legacy inputs are gone (WP-A1); `A.saveSettings` sends only the business keys and drops `personal_tax`
  from `{...S.settings}` before PUT (the server refuses it).
* **My settings** (`#/me`, new nav entry "My settings" for every role, `'*'`): Profile card, calendars card (if
  allowed), API tokens card, and for the boss a card "Personal tax profile" with a short summary (health mode,
  children count, "not saved yet" when `stored` is false) and a link to `#/me/tax`.
* **Personal tax profile** (`#/me/tax`, boss only). Routing follows the existing pattern (`#/money/2026` calls
  `PAGES.money('2026')`): `PAGES.me = async sub => sub === 'tax' ? taxProfilePage() : mySettingsPage()`, both in a
  new `web/taxprofile.js` loaded from `index.html`; the route-rights helper in `app.js` treats `me` as `'*'` and
  `taxProfilePage` itself shows a "boss only" message for other roles: sections "Assessment" (single/joint, church tax
  %, other income, other deductions, other insurance), "Children" (count, full allowance, care-insurance parent/under-25
  count), "Health and care" (GKV/PKV/none, then only the fields of the chosen mode; Zusatzbeitrag empty = average with
  the value shown as placeholder), "Pension" (mode + monthly amount for fixed), "Unemployment" (none / voluntary /
  half rate in the founding years), "Employment" (checkbox "I also have employment income" shows the block;
  Steuerklasse select with the hint "only affects monthly withholding, not the annual tax"). When `stored` is false a
  banner says "Not saved yet – the estimate uses the defaults shown here." Save button → PUT; warnings shown as
  banners. Footer: the non-goal sentences and "Estimate only – not tax advice. Rates <year> from the official tables;
  values marked as not confirmed are listed."
* **Money & Taxes** (`PAGES.money`, `web/app2.js`):
  * "Tax burden" card (projected year): Einkommensteuer (with "incl. Kindergeld added back" when `kg_added > 0`),
    Soli, church tax, Gewerbesteuer with the §35 line, KSt for a GmbH, then a "Social insurance" block (health, care,
    pension, unemployment) from `insurance`, total, and – with employment – "already withheld" and "expected back
    payment / refund" (`balance`). An expandable "How this was computed" list shows zvE and `vorsorge` values.
  * **"Currently owed" card** directly next to (below on narrow screens) the Tax burden card, current and past years
    only: heading "Owed on the profit booked so far (as of <as_of>)", rows profit booked so far, taxes (`tax_due`),
    withheld (when > 0), **owed** (or "refund so far" when negative), and the note "Advance payments
    (Vorauszahlungen) are not taken into account."
  * "Monthly reserve" line in the same card group: "Set aside <monthly> per month for the remaining <months_left>
    months (<remaining> of <due_year> still to reserve)", followed by "plus <owed_now> now" when `owed_now > 0` (the
    current month's card shows `owed_now + monthly`); past years show "Year closed" instead.
    Before day 60 (`extrapolated` false in the current year) and when `monthly` is 0 the line reads "Set aside
    <current month's tax_set_aside> now; the monthly reserve starts once 60 days of the year have passed" (or "No
    further reserve needed" when `due_year` is 0).
  * Months whose `take_home` is `null` show "–" in the Take-home column.
  * Redacted response (`f.redacted`): exactly what "/finance redaction – What a non-boss sees" lists (business taxes
    only, no reserve line, no Tax reserve / Take-home columns, no expandable list). Link "Edit personal tax profile"
    for the boss.

## Work packages

Implementer: **Sonnet** (maintainer instruction overrides the Haiku default). One package = one branch = one pull
request, at most 6 files and about 300 changed lines (AGENTS.md "Task size"). Order: B1 → B2a → B2b and A1 → A2 → A3
can run in parallel; B3 needs A2 and B2b; A4 needs A1 and A3; A5 needs A4 (it extends `web/taxprofile.js`); A6
needs B3b and A4 (all three own `web/app2.js`); B3a needs B3, B3b needs B3a. All packages ship in one
release. Every prompt carries the four lines below verbatim (`Contract`, `Files`, `Stop`, `Report`), with
`Contract: docs/specs/tax-burden-and-settings.md, <section>`.

**A1 – remove the legacy tax fields.** Tier **Full** (outside input, settings visible to the accountant).
* Contract: "Legacy keys", "Storage and API" (`get` / `update` rules for `personal_tax`).
* Files: `permita38/core/models.py`, `permita38/core/settings.py`, `permita38/core/service/settings.py`,
  `web/app2.js`, `tests/backend/test_validation.py`, `api/openapi.json`.
* Work: delete the five fields from `Settings`/`SettingsIn`, add the refusing `model_validator`, `LEGACY_TAX_KEYS`;
  `get()` strips `personal_tax` and the legacy keys, `update()` refuses `personal_tax` (400, nothing stored), drops
  the legacy keys before `save_settings` and keeps a stored `personal_tax`; remove the five inputs and their `nums`
  entries from the settings page; `make generate-openapi`.
* Tests (in `test_validation.py`): `PUT /settings` with each legacy key → 422, nothing stored (replaces the
  `social_monthly: -5` case); with `personal_tax` → 400 and the stored profile unchanged; a store seeded with legacy
  keys and `personal_tax`: `GET /settings` and `/bootstrap` (boss and accountant) contain none of them, after one
  `PUT /settings` the raw store has no legacy key but still has `personal_tax`.
* Stop: when the listed tests and `tests/backend/test_validation.py` pass and lint is clean. Report: files changed,
  test result, open design decisions (≤ 10 lines).

**A2 – profile models and service.** Tier **Full** (outside input).
* Contract: "Models", "Advisory warnings", "Storage and API" (service, `effective_personal_tax`).
* Files: `permita38/core/validation.py`, `permita38/core/models.py`, `permita38/core/settings.py`,
  `permita38/core/service/personal_tax.py` (new), `docs/validation.md`, `tests/backend/test_personal_tax_model.py`
  (new).
* Tests: bounds of every field (min/max ±1, `pkv_basis > pkv` → error), defaults without a stored profile
  (`stored: false`), defensive read of a damaged object (falls back per field, no exception), unknown field
  round-trip, each advisory warning, `update` replaces (no merge).
* Stop / Report: as A1.

**A3 – profile routes.** Tier **Full** (rights).
* Contract: "Storage and API" (routes), "Legacy keys" (import).
* Files: `permita38/api/routers/settings.py`, `permita38/api/deps.py`, `api/openapi.json`,
  `tests/backend/test_personal_tax_api.py` (new), `tests/backend/test_data_export.py`,
  `tests/backend/randomized/test_api_stateful.py`.
* Tests: boss session GET/PUT; employee, accountant and a boss API token → 403 on both; PUT returns warnings and
  `stored: true`; export/import round-trip of `personal_tax`; the stateful machine knows that `PUT /settings` refuses
  `personal_tax` and the legacy keys and that `GET /settings` never shows them. Run `make test-summary
  SUITES="random"` once before merge.
* Stop / Report: as A1.

**B1 – rule tables.** Tier **Full** (the logic pass checks every number against the source list).
* Contract: "Rule tables".
* Files: `permita38/core/tax_rules/__init__.py`, `permita38/core/tax_rules/2026.json`,
  `permita38/core/tax_rules/2025.json` (all new), `pyproject.toml` (package data, as for `trip_rates`),
  `tests/backend/test_tax_rules.py` (new).
* Tests: every key present in every year, each value has source and valid_from, Decimals parse, fallback year +
  warning, 2026 values equal the table in this spec literally.
* Stop / Report: as A1.

**B2a – engine: insurance, Vorsorge, tariff, Soli, church.** Tier **Full**.
* Contract: "Inputs of the engine", "Algorithm" steps 1–7, 10, 12 (no children: `kg_added = 0`, `Z = T(zvE)`; no
  Gewerbesteuer, no GmbH yet).
* Files: `permita38/core/tax.py`, `tests/backend/test_tax_estimate.py` (new), `tests/backend/test_domain.py`.
* Work: rewrite `tax.py` on Decimal with the new signature; keep `est_single`, `est`, `KLEIN_LIMIT_*` names (return
  `int` / `Decimal`). `calc.finance` is not touched (B3): until then `profile` is optional and defaults to
  `effective_personal_tax(settings)`, and `profit` is converted with `money.dec` (B3 makes both required/Decimal).
* Tests: O1, O5, O7 with every listed intermediate asserted exactly; exact `test_domain.py` asserts; property tests
  (hypothesis, small): total monotone in profit for a fixed profile, `dBO ≥ B`, `dA ≤ H`, Soli never above 5.5 % of
  its base.
* Stop / Report: as A1.

**B2b – engine: children, Gewerbesteuer, GmbH.** Tier **Full** (logic pass recomputes O1–O7 by hand).
* Contract: "Algorithm" steps 8, 9, 11.
* Files: `permita38/core/tax.py`, `tests/backend/test_tax_estimate.py`.
* Tests: O2, O3, O4, O6 with every listed intermediate exactly; property: Günstigerprüfung never worse than
  Kindergeld; `share` 0 when no positive income.
* Stop / Report: as A1.

**B3 – finance integration.** Tier **Full** (money, rights). **Landed** in 7682734 on `feat/tax-burden`; the
2026-11-03 amendment (projection threshold, catch-up, `take_home`, redaction allowlist) is done by B3a and B3b on top
of that code.
* Contract: "Integration in `calc.finance`", "`/finance` redaction", oracle cases F1 and F2.
* Files: `permita38/core/calc.py`, `permita38/core/tax.py` (make `profile` required, Decimal `profit` only),
  `permita38/api/routers/finance.py`, `api/openapi.json`, `tests/backend/test_finance_tax.py` (new).
* Tests: F1 and F2 exactly (owed, reserve, per-month `tax_set_aside`) with a pinned `today`; past year: `months_left`
  0 and owed = actual; future year: `tax_owed` null, reserve over 12 months; no stored profile → defaults warning;
  boss session sees full breakdown, accountant and a boss API token only the redacted keys (including `tax_owed`,
  `reserve`); month `social` = q2(social/12); `take_home` does not double-count social. Run `make test-summary
  SUITES="random"` once before merge (the stateful machine reads `/finance`).
* Stop / Report: as A1.

**B3a – projection threshold, set-aside catch-up and `take_home`.** Tier **Full** (money).
* Contract: docs/specs/tax-burden-and-settings.md, "Integration in `calc.finance`" (Projection, Months), oracle
  cases F1–F11.
* Files: `permita38/core/calc.py`, `tests/backend/test_finance_tax.py`.
* Work (in `finance()` only, `redact_tax` untouched): 60-day rule and `extrapolated`; start month `s` with the
  catch-up; counted months `c`; `self_social` from `proj["insurance"]`; months' `social` / `take_home` (null after
  `c`); `social_year` and `take_home` as sums of the months, all in Decimal until `to_float` at the end.
* Tests (pinned `today`, literals from F1–F11 exactly, no float arithmetic in the expectations): update F1/F2 and
  `test_social_and_take_home_count_social_once` to the new literals; F3 (January boundary, early lump sum), F4 (day
  59) and F5 (day 60) as a pair, F6 (December boundary), F7 (refund), F8 a/b (zero and negative profit), F9
  (gewerbe through `calc.finance`, past-year literal, replaces the relative past-year test), F10 (future-year
  literal, replaces the relative future-year test), F11 (leap year and day 365/366); for F1, F5, F6, F9, F10 assert
  `sum(m.take_home for begun months) == take_home` and `sum(m.social) == social_year`, and that the sum of
  `tax_set_aside` equals `owed_now + months_left × monthly`. Existing warning/legacy-key tests keep passing.
* Stop: when `tests/backend/test_finance_tax.py` and `tests/backend/test_tax_estimate.py` pass and lint is clean.
  Report: files changed, test result, open design decisions (≤ 10 lines).

**B3b – redaction allowlist and the non-boss page.** Tier **Full** (rights).
* Contract: docs/specs/tax-burden-and-settings.md, "`/finance` redaction" (table and "What a non-boss sees"), "UI"
  (Money & Taxes: null take-home, redacted response), oracle F9 (redacted values).
* Files: `permita38/core/calc.py` (`redact_tax` only), `tests/backend/test_finance_redaction.py` (new),
  `tests/backend/test_finance_tax.py` (remove the redaction tests moved to the new file), `web/app2.js`,
  `tests/web/test_money_redacted_ui.py` (new). No change to `api/routers/finance.py` or `api/openapi.json` (the
  response type is `dict[str, Any]`).
* Work: `redact_tax` builds the result from module-level allowlist tuples (`_PUBLIC_TOP`, `_PUBLIC_MONTH`,
  `_PUBLIC_TAX`, `_PUBLIC_OWED`), adds `redacted: true` at the top level and in each reduced block, `reserve =
  {redacted: true}`, drops `NOT_SAVED_WARNING`. `web/app2.js`: branch on `f.redacted` as listed in "What a non-boss
  sees"; "–" for a `null` take-home. A6 later reworks the cards and keeps this behaviour.
* Tests, backend (`calc.redact_tax(calc.finance(repo, y, TODAY))` with `TODAY = date(2027, 3, 15)` pinned and
  `y ∈ {TODAY.year − 1, TODAY.year, TODAY.year + 1}`; every date is derived from `TODAY` – F9 books its invoice at
  `date(TODAY.year − 1, 2, 10)` and is the `y = TODAY.year − 1` case; the current and future years fall back to the
  2026 table, which is fine for key sets – so no other literal year appears in this file):
  * **exact key sets, boss view** (unredacted result): top level `{year, months, totals, tax_actual, tax_projected,
    tax_owed, reserve, projected_profit, extrapolated, effective_rate, social_year, take_home, categories,
    open_receivables, warnings, kleinunternehmer, company_type}`; month `{month, revenue, vat_out, expenses, vat_in,
    profit, vat_due, tax_set_aside, social, vat_set_aside, take_home}`; `tax_owed` `{as_of, profit_ytd, est, soli,
    church, gewst, kst, kst_soli, tax_due, withheld, owed}`; `reserve` `{due_year, withheld, owed_now, remaining,
    months_left, monthly, balance}`; `tax_actual` = `tax_projected` = the engine key list of "Algorithm" step 12;
  * **exact key sets, redacted view** for each `y`: the "kept" column of the table, nothing else, at every level
    (future year: `tax_owed` is `None`); `NOT_SAVED_WARNING` absent while the rule-table warnings stay;
  * an unknown key injected into the finance result (top level, a month, `tax_actual`, `tax_owed`, `reserve`) does not
    reach the redacted output (allowlist, not denylist);
  * F9 redacted values literally (gewerbe, gewst 8 741.25, kst 0, kst_soli 0);
  * API, **without** a `year` parameter (the server picks the current year; neither a literal year nor
    `date.today().year` in the test): boss session → full key sets; accountant session, a boss read-only token and a
    boss read-write token → redacted key sets (`redacted is True`).
* Tests, web (`tests/web/test_money_redacted_ui.py`, live date, no clock pinning): seed a gewerbe data folder with a
  profile and one paid invoice; the accountant opens `#/money`: page text contains "Business taxes" and
  "Gewerbesteuer", contains none of "NaN", "undefined", "Einkommensteuer", "Take-home", "Tax reserve", "What stays
  with you"; the boss sees the "Take-home" column.
* Run `make test-summary SUITES="random"` once before merge (the stateful machine reads `/finance` as the boss).
* Stop: when `tests/backend/test_finance_redaction.py`, `tests/backend/test_finance_tax.py` and
  `tests/web/test_money_redacted_ui.py` pass and lint is clean. Report: files changed, test result, open design
  decisions (≤ 10 lines).

**A4 – Business settings and My settings pages.** Tier **Standard** (UI; no money logic in the browser).
* Contract: "New grouping", "UI" (Business settings, My settings).
* Files: `web/app2.js`, `web/app.js` (nav, route rights for `me`), `web/taxprofile.js` (new; `PAGES.me` and
  `mySettingsPage` only), `web/index.html`, `tests/web/test_settings_pages_ui.py` (new).
* Tests: Business settings cards as grouped; employee sees My settings (profile, tokens) but no tax link; boss sees
  the tax summary card.
* Stop / Report: as A1.

**A5 – Personal tax profile page.** Tier **Standard**.
* Contract: "UI" (Personal tax profile), "Models".
* Files: `web/taxprofile.js`, `docs/tax-profile.md` (new user guide section), `tests/web/test_tax_profile_ui.py`
  (new).
* Tests: round-trip (fill, save, reload), mode-dependent fields, warnings shown, "not saved yet" banner, non-boss
  sees the "boss only" message.
* Stop / Report: as A1.

**A6 – Money & Taxes cards.** Tier **Standard**.
* Contract: "UI" (Money & Taxes), "Integration in `calc.finance`" (field names), "`/finance` redaction" (keep B3b's
  non-boss rendering).
* Files: `web/app2.js`, `tests/web/test_money_tax_ui.py` (new).
* Tests: the live server uses `date.today()` (no clock pinning in `tests/web/`), so the test seeds a data folder,
  reads `GET /api/finance` and asserts that the rendered social block, "Currently owed" card and monthly reserve line
  show exactly the response values (formatted); refund wording when `owed` is negative; the accountant sees only
  Gewerbesteuer/KSt (and Soli on KSt), no reserve line, no expandable list. The F-case numbers are pinned in B3a's
  backend tests.
* Stop / Report: as A1.

## Open questions for the maintainer / tax adviser

1. **Rates TO VERIFY**: Sachsen employee care share 2.3 % (2026); half voluntary ALV contribution 51.42 vs 51.43;
   rounding of the Gewerbesteuer-Messbetrag; truncation of church tax to cents; all 2025 values except tariff, Soli
   and the §10 (3) cap.
2. Is the staged per-child Günstigerprüfung (each child tested on the zvE left after the previous children) what the
   Finanzamt does, and is counting half the Kindergeld for a non-jointly assessed parent right in all cases?
3. Soli base: `Z` reduced by a §35 credit recomputed on `Z` – confirm (§3 (2) SolZG refers to the tax "festzusetzen
   wäre"); church tax base without §35 per §51a (2) S. 3.
4. Vorauszahlungen are not recorded, so "currently owed" and the reserve ignore them – record them later?
5. **Catch-up in the current month** (2026-11-03 amendment, architect's choice): the current month's `tax_set_aside`
   now carries `owed_now` on top of `monthly` (past year: December carries the owed amount). Without it the tax on
   the profit already booked lands in no month, `take_home` would never subtract it, and before day 60 every month
   would show a reserve of 0 after a large invoice. The `reserve` block itself is unchanged. Confirm or revert (then
   `take_home` needs another per-month tax term).
6. **GmbH take-home**: for `gmbh` the same formula subtracts the household tax (on the salary) and the corporate taxes
   from the GmbH profit, so `take_home` is the company's cash after set-asides, not the owner's. Keep, or hide
   `take_home` for `gmbh`?
