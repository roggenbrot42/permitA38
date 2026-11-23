# Specification: money as decimal strings in the whole HTTP API (milestone M0b)

Status: **draft for maintainer sign-off** (maintainer decision of 2026-11-23: "old api endpoints should also use
decimal strings"; ships before the VAT + accounting work, release v0.5.0-alpha.1). Author: architect (Opus). Review
tier: **Full** (money, every API client). Inventory of the current fields: an Explore pass of 2026-11-23 (the tables
below); the implementer re-greps before each WP and lists additions under "Open design decisions".

## Goal

Every money amount the API sends is a **decimal string with exactly two decimals** (`"1190.00"`, `"-57.10"`,
`"0.00"`), never a JSON number, so no client ever sees a binary float for money. Requests accept a decimal string or a
JSON number. The server already computes with `Decimal` (`core/money.py`); floats exist only at the boundary today.

## Non-goals

* **Storage stays as it is.** FLOAT columns and the JSON numbers of stored records are unchanged (cents-exact floats
  written through `money.to_float`/`q2`, which round-trip through `repr`). Exact storage types belong to the planned
  "strict storage types" work. Consequently the **data export bundle is unchanged** (records are exported as stored;
  `FORMAT_VERSION` stays 1) and the transfer importer is untouched.
* Non-money numbers stay numbers: quantities (`qty`), hours and minutes, kilometres, percentages and VAT rates
  (`vat_rate`, `Line.vat` = a percent, `deductible`, `vat_deductible`, `church_tax_percent`, …), ratios (`rate` and
  `effective_rate` inside the tax estimate are ratios 0..1), probabilities, confidence scores.
* PDF, CSV and the web display formats are unchanged.

## The rules

1. **Output types.** Two primitives in `core/validation.py` for the lenient response models; the Python value keeps
   its stored type (float) so internal callers of `model_dump()` (e.g. `service/personal_tax.py`) are unaffected — the
   serializer runs **only in JSON mode** (`PlainSerializer(…, when_used="json")`), and a lenient before-validator
   accepts a stored float, int or decimal string:
   * `Money` — amounts, totals, net/VAT/gross: `money.to_decimal_str` = at least two decimals and **all** significant
     stored decimals up to six (`118.09` → `"118.09"`, `0` → `"0.00"`, a stored `0.125` → `"0.125"`); `-0.00` is
     normalised to `"0.00"`. A stored money amount with more than two decimals is never rounded silently; it is
     emitted as stored and reported by `check-db` (new finding `money.sub_cent`).
   * `UnitPrice` — `Line.price`, incoming-offer `unit_price`, every `rate` (EUR/h) and `mileage_rate` (EUR/km): the
     same function; sub-cent values are legitimate here (no finding).
   JSON schema: `{"type": "string", "format": "decimal", "pattern": "^-?[0-9]+\\.[0-9]{2,6}$"}`. `money.to_float` stays
   for internal and storage use only (rule 5).
2. **Input types.** The money primitives of the strict `*In` models (`Amount`, `NonNegAmount`, `MonthlyAmount`, `Rate`,
   the `Strict*` variants, `Amount100k`/`Amount10k`) accept **either** a JSON number (as today, parsed exactly by
   `money.dec` via `repr`) **or** a decimal string `^-?[0-9]{1,10}(\.[0-9]{1,6})?$` (no exponent, comma, spaces or
   thousands separators, no leading/trailing whitespace or newline — the whole string must match; bools refused). Both paths apply the same bounds, finiteness and the existing whole-cent
   checks; the string path never accepts more decimals than the number path would. After validation the value keeps
   today's Python type (float), so existing services and storage are unchanged. (New accounting models use the
   Decimal-yielding `MoneyIn` instead, accounting 7a.) OpenAPI shows `anyOf: [number, string(pattern)]`;
   `CheckedBody`/`_check_value` let such strings through for money fields only.
3. **Stored-record responses.** Records returned as stored (`StoredJSON`, `api/responses.py`) pass through one
   function `present(model, record)` that converts the fields annotated `Money` in the record's lenient model,
   recursively into nested models (`lines`, `ExpenseLine`, trip parts, plan, allocations). Unknown extra fields are
   left alone (they round-trip untouched). `None`, empty and unparsable stored values are passed through unchanged
   (never a 500). **Derived, response-only money** (`Line.amount`, `open_net`, `invoiced_net`, `items_*_total`,
   trip totals) is dropped from a written record, never stored back from a whole-record PUT.
4. **Dict endpoints.** Each endpoint that builds a dict (table below) converts its money keys at the **response
   boundary** (router or service `…_response` function) with `money.to_decimal_str`; internal functions
   (`calc.doc_totals`, `calc.finance` internals, `tax.estimate`, planner, procurement math) keep their numeric types
   for their internal callers (PDF, planner, sanity rules). Untyped draft dicts (receipt/document scans, incoming
   offer lines) convert the listed keys.
5. **Guards (tests)** — fail closed:
   * An OpenAPI walker: every `number`/`integer` property in a **response** schema must be on an explicit
     **non-money allowlist** (quantities, hours, minutes, km, percentages and VAT rates, ratios, probabilities,
     confidence, counts, ids, years/months, whole-euro bases are *not* on it — they are strings); anything else fails,
     so a new money field cannot slip through as a number. Every `Money`/`UnitPrice` field must carry the decimal
     schema.
   * A response sweep: with seeded fake data, every parameterless GET route (the existing route sweep of the smoke
     test) is called as the boss; the JSON is walked and fails on a number at a key that is not on the allowlist
     for its path.
   * A source check fails when `money.to_float` or a bare `float(` on money appears in a response builder module.
6. **Web.** `eur()` (`web/app.js`) accepts a decimal string (display only). Every arithmetic on money in JS goes
   through `web/money.js` (`Money.parse` already accepts strings; cents as BigInt). Form fields send what the user
   typed as a string (`"12.50"`). Input rule: one comma **or** one dot as the decimal separator; an input with both,
   or with thousands separators (`"1.234,56"`), is refused with a message. D5 checks every money site for the string
   pitfalls: `"0.00"` is truthy, `+` concatenates, `.toFixed` on a string throws, sorting compares text.
7. **Breaking change.** CHANGELOG "Breaking" section; `docs/api-tokens.md` and `docs/validation.md` state the money
   format; the OpenAPI contract changes. There is no MCP module on `main` yet; the MCP spec (`docs/specs/mcp-server.md`)
   gets a line that it emits money as decimal strings.

## Money fields (inventory of 2026-11-23)

**Lenient models (`core/models.py`)** — annotate as `Money`:

| Model | Money fields |
|---|---|
| Customer, Project, Task, Entry, Contract, Plan, Allocation, Supplier | `rate` (EUR/h) |
| ExpenseLine | `gross`, `net`, `vat` |
| Expense | `net`, `vat` |
| Line | `price`, `amount` (**not** `vat`: a percent) |
| UserOut | `default_rate`, `cost_rate` |
| Settings | `default_rate`, `planning_min_rate` |
| PersonalTaxHealth | `pkv_monthly`, `pkv_basis_monthly`, `pkv_care_monthly`, `pkv_employer_subsidy_monthly` |
| PersonalTaxPension | `fixed_monthly` |
| PersonalTaxEmployment | `gross_year`, `bav_employee_year`, `bav_employer_year`, `vwl_employer_year`, `werbungskosten_year`, `withheld_year` |
| PersonalTax | `other_income_year`, `other_deductions_year`, `other_insurance_year` |
| Allocation | `planned_net`, `invoiced_net`, `open_net` |
| TripItem | `expected` |
| TripReduction | `amount` |
| TripDay | `base`, `amount` |
| TripBills | `net`, `vat` |
| TripChecklistItem | `sum` |
| TripFigures, Trip | `allowance_total`, `mileage_rate` (EUR/km), `mileage_total`, `items_expected_total`, `items_actual_total` |
| IncomingOffer | `net_total`, `vat`, `gross_total`; its `lines` dicts: `unit_price` |

**Dict endpoints** — convert at the boundary:

| Endpoint (builder) | Money keys |
|---|---|
| `GET /finance` (`calc.finance`, `tax.py` estimate, `redact_tax`) | months[]: `revenue`, `vat_out`, `expenses`, `vat_in`, `profit`, `vat_due`, `tax_set_aside`, `vat_set_aside`, `social`, `take_home`; `totals{…}`, `projected_profit`, `social_year`, `take_home`, `categories{*}`, `open_receivables`; `tax_actual`/`tax_projected`/`tax_owed`/`reserve`: every key except `rate`, `effective_rate`, `months_left` and counts (the WP lists them from `tax.py` and asserts the list in a test). The redaction allowlists keep working on key names. |
| `GET /project-report` | `revenue`, `cost`, `expenses`, `margin`, `unbilled_revenue`, `committed_external_cost` (not `hours`, `unbilled_hours`) |
| `GET /finance/commitments` (`procurement.py`) | `planned_net`, `invoiced_net`, `open_net` (rows, per supplier, per project, totals), hint details `revenue_customer`, `revenue_total` |
| procurement offer figures, rows, warning details | `net_total`, `vat`, `gross_total`, `rate`, `planned_net`, `difference`, `invoiced_net`, `excess`, `revenue_customer`, `revenue_total` (not `share_percent`, `hours`, `vat_rate`) |
| `GET /plan`, `POST /plan/simulate`, `POST /plan/check-offer` (`planner/views.py`), planner suggestions (`cover.py`) | `rate`, `value`, `invoiced`, `open`, `expected_value`, `planned_subcontractor_cost`, `contract_value`, `margin` (not `promised`, `booked`, `remaining`, `covered`, `shortfall`, `win_probability`) |
| trip preview and save (`trips_calc.py`, `service/trips.py`) | `reductions[].amount`, `days[].base`, `days[].amount`, `allowance_total`, `mileage_rate`, `mileage_total`, `bills.net`, `bills.vat`, `items_expected_total`, `items_actual_total`, `checklist[].sum` |
| expense itemise (`calc.itemise_expense`) | `lines[].net`, `lines[].vat`, `net`, `vat` |
| receipt and document scans (`draft`, `doc`, `given_totals`, `totals_info`) | `gross`, `total`, `net`, `vat`, line `price`/`gross`, expense `net`/`vat` |

**Money-name list for the guards** (rule 5): the union of the field and key names above, minus names that are also
used for non-money values elsewhere (`rate` inside the tax estimate dicts, `vat` on `Line`) — those two are checked
per model/path instead of by name.

## Work packages (M0b; tiers: D1–D4 and D6 Full, D5 Standard)

| WP | Content | Files (indicative) |
|---|---|---|
| D1 | `money.to_decimal_str`, `Money` output type, string-or-number input primitives, `present()`, `CheckedBody` change, unit tests | `money.py`, `validation.py`, `api/responses.py`, tests |
| D2a | Models + routers: documents/offers/invoices (`Line`), customers, projects, tasks, entries, contracts | `models.py`, affected routers/services, tests |
| D2b | Models + routers: plans, allocations, users, settings, personal tax | `models.py`, affected routers/services, tests |
| D3 | Expenses (+ itemise), receipts and document scans, suppliers, procurement, incoming offers, trips | `models.py`, services, tests |
| D4 | Dict endpoints: finance (incl. tax estimate and redaction), project report, commitments, planner | `calc.py` boundary, `finance.py` router, `procurement.py`, `planner/views.py`, tests |
| D5 ×2–3 | Web: `eur()`, every money arithmetic site through `Money` (inventory: `app.js`, `app2.js`, `receipts.js`, `docscan.js`, `trips.js`, `suppliers.js`, `procurement.js`, `allocations.js`, `taxprofile.js`), inputs sent as strings | web files, UI tests |
| D6 | Guards of rule 5, stateful randomized model and `oracle.py` (exact string comparison instead of `approx`), web `e2e_random` models and helpers, OpenAPI regeneration, CHANGELOG, docs | tests, `api/openapi.json`, docs |

`models.py` and `app2.js` are hot files: D2 → D3 in sequence; the D5 parts in sequence on `app2.js`.

## Test plan

* Unit: `to_decimal_str` (`0` → `"0.00"`, `-0.005` → `"-0.005"` (stored decimals kept), `0.1 + 0.2` → `"0.30"` (rounded at the sixth decimal only), `1e7` bound), the input
  primitives (number, string, comma/exponent/space refused, bool refused, bounds, whole cents), `present()` on nested
  records and unknown fields.
* The three guards of rule 5.
* Existing API tests updated to compare strings; a round trip: POST with numbers → GET strings → PUT the strings back
  → unchanged record.
* Randomized suites once locally (they model every endpoint); browser suite in CI.

## Open design decisions (for the implementer's report)

Placement of `present()` (response class vs. router helper), the exact module of the boundary converters for dict
endpoints, the JS helper names.
