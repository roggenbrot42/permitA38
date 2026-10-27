# Specification: business trips as a step-by-step page (wizard)

Amends [`business-trips.md`](business-trips.md) (rules, rate tables, calculation unchanged) and the UI part of the
workflow proposal ([`business-trips-workflow.md`](business-trips-workflow.md); its stages `planned/completed/settled`
and the customer settlement are **not** part of this slice). Status: decided by the maintainer's wish of 2026-10-26.
Review tier: **Standard** (backend: two optional fields and one reference check, no tax logic) plus **Light** for the UI.
Release: alpha.5.

## 1. Problem

A trip is one big dialog with everything at once (days, countries, meals, mileage, breakdown, checklist, bills). The
maintainer wants: a small dialog for the initial plan, then **one view per step** that only shows what is still to do,
with forward/backward navigation, the itinerary as a few lines per day, and expenses as a normal table page.

## 2. Flow

1. **New trip** (small dialog, the only dialog of the flow): *Reason for the trip* (`purpose`, required), `title`
   (optional), customer, project (only the customer's projects, existing rule), start, end, and the two places of the trip:
   **start place** (`origin`, default: the city of the business settings, editable) and **destination** (`destination` +
   `destination_country`, default `DE`). Both are reused later (section 3). *Create* stores the trip
   (status `draft`) and navigates to the trip page. Edit of these fields later: step 1 of the page (same fields, inline).
2. **Trip page** `#/trips/<id>` (and `#/trips/<id>/<step>`): header with title, status pill, the stepper, and
   *Back* / *Next* buttons. Steps, in order:

   | # | Step | Content |
   |---|---|---|
   | 1 | **Plan** | the fields of the dialog above, cost centre / project decision |
   | 2 | **Itinerary** | day by day: where I started, where I went, where I ended up (section 3) |
   | 3 | **Expenses** | **the main view of a trip** (opens by default for a stored trip): planned and already paid costs, mileage (section 4) |
   | 4 | **Review** | days with country and meals provided, live breakdown, reminder checklist, *Statement (PDF)*, *Finalize trip* |

   *Next* and *Back* save the current step first (one `PUT`), then move; a validation error stays on the step and
   shows the message. The stepper items are links and do **not** save: a change made on a step is kept only by *Back* or
   *Next* (or *Finalize*). An inline edit (an expected cost, *Not needed*) saves when it is made. Next from a step without
   changes sends no `PUT`. The stepper items are links (any step can be opened directly). **A step shows only its own
   fields**: what the user entered in an earlier step is not repeated (the Plan step is a summary line above steps 2-4:
   reason, customer/project, period, with an *Edit* link to step 1). A new trip opens on step 2 (itinerary), an existing trip on step 3 (expenses).
3. A **final** trip shows all steps read-only (existing rules: boss may *Reopen*, delete only draft). The list page
   links each row to `#/trips/<id>`. No dialog remains except *New trip* and the existing bill dialog.

## 3. Itinerary (step 2)

* Stored as `itinerary`: list (max 60) of **legs** `{depart: LocalDateTime, from: Short, to: Short, country?: ISO-3166
  alpha-2, mode?: train|flight|car|taxi|bus|other, note?: Short}`; legs lie inside `[start, end]`, ordered by
  `depart` (server sorts, 422 for outside the period).
* **Display: three rows for the whole trip, not per day.**
  1. **Departure**: where I start from and at what time (first leg: `from`, `depart`; prefilled with the home place).
  2. **Stay**: where I spent my time, from when to when (place = `to` of the first leg, from = its arrival, until = the
     `depart` of the next leg).
  3. **Return**: where I arrived back (the `to` of the last leg, usually home) and when.
  The two stored legs of a plain trip are start -> place and place -> home; the Stay row is derived from them.
  **Add stay** inserts a further place between two rows (one more leg, one more Stay row with its from/until);
  **Remove** drops one. A trip with one place is therefore exactly three rows, a trip with three places five. Rows are
  never created per day, so a four-week stay is still three rows. Times default to the trip's start and end.
* **Reuse of the first dialog.** `origin` and `destination` prefill the three rows: Departure from `origin` at `start`,
  Stay at `destination` (country from `destination_country`), Return to `origin` at `end`; the UI creates the two legs
  when the itinerary is empty, and editing the rows never changes `origin`/`destination` (they stay the plan).
  **Moving the period** (Plan step): when the start changes, the stored legs move with it by the same number of days and
  keep their times of day (toast: "Itinerary moved by N days"); the Itinerary step then saves against the new period. A
  period that still cuts off a leg after the shift is refused with the message to correct the times on the Itinerary step. They also
  prefill the *from/to* of the item dialogs (Train, Flight, Taxi, Car), the place of a Hotel and the country/city
  of the days in the Review step. Changing them in step 1 offers (does not force) to update empty itinerary rows only.
* Optional per leg: transport mode and the destination country (default: the previous leg's country, else `DE`).
* *Take over for the allowance*: on saving step 2 the UI derives `places` (existing field, range based, one row per
  change of country/city) from the legs and writes it with the same `PUT`; step 4 still lets the user correct
  country/city/meals per day (the days table of today, only in the Review step).

## 4. Expenses (step 3)

A normal page section, not a dialog. One table, one row per **item**:

| Column | Source |
|---|---|
| Date, Category (hotel, train, flight, taxi, car, meal, other), Description | item |
| **Expected** (gross, EUR) | item `expected` (editable inline, optional) |
| **Actual** (gross) | sum of the gross of the bills (`expenses`) linked to the item; empty until a bill exists |
| Receipt | "attached" / "missing" (hint only) per linked bill; *Add bill* button opens the existing bill dialog with trip, project, category and item preselected (this is the only dialog on the page) |
| Remove | only for an item without bills |

* ***Add item*** is a drop-down of typical items: **Hotel, Train, Flight, Car (private), Taxi / other transport,
  Other**. Choosing one opens a small dialog whose fields depend on the type (the only dialog of this page besides the
  bill dialog); *Save* adds the row to the table, a row is edited by clicking it (same dialog), expected values can also
  be changed inline.

  | Type | Dialog fields | Stored as |
  |---|---|---|
  | Hotel | place, check-in, check-out, expected total | item `category: hotel`, `text` = place, `date`, `date_to` |
  | Train / Flight | from, to, date, carrier or note, expected | item `train`/`flight`, `from`, `to`, `date`, `text` |
  | Taxi / other transport | from, to, date, expected | item `taxi`, same fields |
  | Car (private) | date, from, to, km | **not an item**: a mileage row (existing rule, rate from the rate table); the table shows it with the computed amount as its expected cost, read-only |
  | Other | description, date, expected | item `other`, `text`, `date` |

  Every item can also hold an already paid cost: *Add bill* on the row opens the existing bill dialog (receipt upload).
* Bills linked to the trip but not to any item appear as rows with only the actual amount ("not planned"), so nothing
  that is booked is hidden. The footer shows **Expected total**, **Actual total** and the difference. The per-row
  figures (actual, difference, car cost) are display-only client arithmetic in exact cents; the footer comes from the
  server, and its expected total is labelled "(without mileage)" when car rows are listed.
* Mileage (private car) is a second small table below with *Add mileage* (existing fields, existing rules). The cost per
  row is shown as "approx." and is display-only: each row is rounded to the cent, so the sum of the rows can differ from
  the trip total by a cent (the server total is the figure of record).
* Inline edits and the item and mileage dialogs are written through one queue, in the order they were made, each on the
  trip as stored when its write runs; a second quick edit on another row is queued, never dropped.
* Expected costs are **planning figures only**: they are never part of the statement, the allowance, VAT or any report.

## 5. Data and API (delta; the contract file `docs/contracts/trips.md` gets the same rows)

* `TripIn` / `Trip`: `origin?: Short`, `destination?: Short`, `destination_country?: alpha-2`, `itinerary` (above, default `[]`), `items`: list (max 100) of `{id, date: Date, date_to?: Date,
  category: hotel|train|flight|taxi|other, from?: Short, to?: Short, text: Short, expected?: Money >= 0}`; `id` is generated by the server when missing or empty (same id pattern as other
  ids), unique within the trip, kept on `PUT`. Items need no migration (stored in the existing extra JSON); if the
  repository cannot keep them there, add migration `NNNN-trip-itinerary-items` in every dialect.
* `ExpenseIn` / `Expense`: optional `trip_item_id` (id). Rule: only with `trip_id`; the item must exist on that trip,
  else 422 "Unknown trip item". Deleting an item that still has bills is 422 on the trip `PUT` ("Item has bills").
* `Trip` response gets `items_expected_total` and `items_actual_total` (money, computed server-side with
  `core/money.py`; actual = gross of linked bills **plus** unplanned bills of the trip). No new routes.
* The statement PDF prints the itinerary as a compact table (date/time, from, to, mode) when present; no expected costs.
* Rights: unchanged (`trips` area); bills still need `expenses:write`.

## 6. Tests (the oracle)

* Backend: round trip of `itinerary` and `items`; legs outside the period -> 422; legs get sorted; item ids generated
  and stable across `PUT`; `trip_item_id` of a foreign trip or without `trip_id` -> 422; deleting an item with a bill -> 422;
  totals: items 80,00 + 120,00 expected, one bill gross 95,20 on the first item and an unplanned bill gross 11,90 ->
  expected 200,00, actual 107,10; employees only see/modify own trips (existing matrix gets the new fields).
* Randomized stateful model gets the two rules (new field acceptance, item/bill rule); the field list of the model is
  updated, no expected value is adjusted to fit.
* UI (Playwright, fake data): new-trip dialog creates the trip and lands on step 2; a trip shows exactly three rows
  (departure, stay, return) and *Add stay* adds a stay row; a 28-day single-city trip also shows three rows; Back/Next keep the
  entered values and a step never shows fields of another step; expenses table: add item, set expected, add a bill
  from the row (actual appears, receipt "missing"/"attached"), totals; a final trip is read-only; keyboard operable
  stepper (links, `aria-current="step"`).
* No test sends real data; the 2026 rate table oracle (Mon 9 Nov - Wed 11 Nov = 56,00 EUR allowance) is unchanged.

## 7. Out of scope (next slices)

Stages `planned/completed/settled`, *Complete trip*, customer settlement invoice draft, `rebill` and `customer_pays`
(workflow proposal sections 1, 3 (rebill), 4). Foreign-currency receipts. Estimating costs from rates.

## 8. Decisions taken (to confirm)

1. Expected costs sit on **trip items**, actual costs come from **bills** linked to an item; this keeps the existing
   bill/receipt/VAT logic untouched. The alternative (expected costs as unbooked bills) was rejected: it would pollute
   bookkeeping and reports.
2. The three rows (departure, stay, return) are a **display** of the stored legs; extra places add stay rows.
3. Step order Plan, Itinerary, Expenses, Review; allowance days/meals are decided last because they depend on the
   itinerary.
