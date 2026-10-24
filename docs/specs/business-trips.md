# Specification: business trips in the expenses

Status: **proposed, not scheduled** (after the 0.4.0 feature freeze; implement after itemised expenses and the
migration IDs, in that order). Author: architect (Sonnet 5.5). The implementer follows this document, does not decide
tax questions, and lists every choice it had to make under "Open design decisions".

All tax statements below were researched from practitioner articles and a summary of the BMF letter, **not** from the
statute or the letter itself. They are marked *(verify)* where the research could not confirm them. Before the feature
ships, the maintainer's tax adviser confirms the "Rules" section and `docs/compliance.md` is updated.

## Goal

Record a business trip once and get, for a self-employed person, everything the tax return needs from it:
the **meal allowance** (*Verpflegungsmehraufwand*, a flat daily amount, no receipts), **mileage** with a private car,
and the **receipts** (hotel, train, taxi, parking) gathered under the trip. A printable *trip statement*
(Reisekostenabrechnung) with all items serves as the record the tax office expects.

Non-goals: employee travel expense reimbursement (a different tax regime with the same flat amounts, planned for
the team features), company cars and the logbook method (*Fahrtenbuch*), automatic route/distance calculation,
foreign-currency conversion, passing travel costs through to customer invoices (possible later, see the end).

## Rules (from research)

Sources: see "Sources" at the end.

**Meal allowance, domestic (2026, unchanged since 2020/2021)**

| Absence on the day | Allowance |
|---|---|
| more than 8 hours (one-day trip) | 14 € |
| arrival and departure day of a trip with an overnight stay (any duration) | 14 € |
| a full day (24 hours) | 28 € |

* For a trip over several days: first and last day 14 €, each day in between 28 €.
* **Reduction when a meal is provided** (hotel breakfast included, host pays lunch): 20 % of the *full-day* rate (28 €)
  for breakfast = 5,60 €, and 40 % (11,20 €) for lunch and for dinner. The reduction is taken even on arrival/departure
  days. The allowance never goes below 0.
* **Abroad**: a flat amount per country (and for some countries per city), two levels (more than 8 hours or arrival/
  departure day, and 24 hours), published yearly in a BMF letter; the 2026 letter is dated **5 December 2025**, GZ
  IV C 5 - S 2353/00094/007/012. For a multi-day trip the day takes the rate of the place reached by 24:00 local time;
  for a one-day trip the place of the last activity. A country not listed uses the rate for **Luxembourg**.
  The meal reductions are 20 %/40 % of the *24-hour rate of that country*.
* **Three-month limit**: at the same place of activity the allowance is paid for the first three months only (an
  interruption of four weeks or more starts a new period) *(verify, especially how it applies to self-employed people
  who return to a client regularly)*.
* Own meals are **not** deducted at cost on a trip: the allowance replaces them. The allowance and the restaurant bill
  for the same meal exclude each other. Hosting a business partner is *Bewirtung* (70 % rule), not part of this.
* No receipts for the allowance; records needed: date, destination, purpose, start and end time.

**Mileage** with a private car on a business trip: **0,30 € per km**, covering all vehicle costs (fuel, wear,
insurance); those must not also be claimed. The 0,38 € figure that appears for 2026 is the commuting allowance
(*Entfernungspauschale*) and does **not** apply to business trips *(verify)*. Record per trip: date, route, purpose, km.

**Accommodation**: for self-employed people only the **actual, receipted** cost is deductible; the flat overnight
amounts (domestic 20 €, abroad per country) exist only for tax-free employer reimbursements.

**Other travel costs** (train, flight, taxi, parking, baggage, tolls): actual cost against receipt. Missing small
receipts: a self-made receipt (*Eigenbeleg*) only in exceptions and never for input VAT.

**Input VAT** on receipted costs follows the normal rules (German VAT on the receipt is input VAT; foreign VAT on
foreign receipts is not German input VAT and is recorded with VAT 0 in this version) *(verify)*. The flat allowance
and the mileage carry no VAT.

## Data model

New collection **`trips`** (strict `*In` model, lenient response model, every field bounded, extra round-trips):

| Field | Type | Notes |
|---|---|---|
| `id`, `user_id` | | owner |
| `title` | short text | "Customer visit Hamburg" |
| `purpose` | short text, required | for the statement |
| `customer_id`, `project_id` | optional | both validated to exist |
| `start`, `end` | local date-time `YYYY-MM-DDTHH:MM`, `end > start`, at most 90 days | |
| `places` | list (max 20) of `{from_date, country, city?}` | the destination per day/leg; first entry = outbound destination |
| `meals_provided` | list of `{date, breakfast, lunch, dinner}` booleans | days not listed = none provided |
| `mileage` | list of `{date, from, to, km, purpose?}` (max 100) | `km` 0.1–5000, one decimal |
| `status` | `draft` / `final` | final locks the computed figures |
| `allowance_total`, `mileage_total` | computed, response only | exact cents |
| `rules_version` | computed | e.g. `2025-12-05` (the BMF letter date) of the rate table used |

`expenses` gets an optional **`trip_id`** (bill belongs to a trip; validated to exist). Allowance and mileage are not
expenses: `calc.py` reads them from the trips through one helper (same pattern as the itemised-expenses lines), so
there is no second copy to drift. Both count as **deductible 100 %, VAT 0**. They appear in the finance reports as
their own lines ("Meal allowance", "Mileage").

Migration: new table `trips` and a nullable `expenses.trip_id` in all three dialects, using the new migration-ID
scheme. Round-trip and contract tests as for every table.

### Rate tables

`permita38/core/trip_rates/` holds one data file per year (`2026.json`): domestic rates, mileage rate, and the country
table (`country_code` → `{name, from8h, h24, city_overrides?}`), plus `source` (the BMF letter's date and GZ) and
`valid_from`. Money values are decimal strings and are read with `core/money.py` (no floats). The rate for a trip day
comes from the table of that day's year. A yearly maintenance task (extend the quarterly tax-rule review reminder) adds
the new file; a test fails if a year in use has no table. Until the full country table is imported, an unknown country
falls back to Luxembourg exactly as the rule says, and the statement shows "country not in the table".

## Calculation (the oracle the tests use)

Per calendar day *d* of the trip, in local time:

0. An `end` of exactly 00:00 on a later date is read as 24:00 of the previous day, before the days are classified.
   Absence is elapsed time in Europe/Berlin (a DST change day has 23 or 25 hours); an arrival or departure day needs
   at least one minute of absence to pay.
1. Absence on *d* = elapsed time between max(start, d 00:00) and min(end, d+1 00:00).
2. Domestic/abroad rate of *d*: for a multi-day trip the country of the place reached by 24:00 of *d* (`places`
   entry with the greatest `from_date` ≤ *d*); for a one-day trip the last `places` entry.
3. Base amount:
   * one-day trip (start and end on the same date): `from8h` if absence is **strictly more than 8 hours**, else 0;
   * multi-day: first and last date → `from8h`; dates in between → `h24`.
4. Reduction: `20 % × h24` for breakfast provided, `40 % × h24` for each of lunch and dinner, rounded to cents
   (half-up, `core/money.py`). `amount = max(0, base − reductions)`.
5. Apply the three-month limit per place: days beyond the limit give 0 and carry a note.
6. `allowance_total` = sum over days; mileage = Σ km × 0,30 € rounded **once per trip** to cents (half-up).

Worked examples (domestic 2026 values, to be used as tests):

| Case | Result |
|---|---|
| Day trip 07:00–15:00 (exactly 8 h) | 0,00 |
| Day trip 07:00–15:01 | 14,00 |
| Two days, overnight, Mon 09:00 → Tue 12:00 | 14 + 14 = 28,00 |
| Three days Mon 08:00 → Wed 18:00 | 14 + 28 + 14 = 56,00 |
| Three days as above, breakfast provided on Tue | 14 + (28 − 5,60) + 14 = 50,40 |
| Day trip 9 h, lunch and dinner provided | max(0, 14 − 11,20 − 11,20) = 0,00 |
| 237,5 km | 237,5 × 0,30 = 71,25 |
| Abroad, unknown country, 3 days | Luxembourg row, shown with the note |

## API

* `GET/POST /api/trips`, `GET/PUT/DELETE /api/trips/{id}`; `POST /api/trips/preview` accepts a trip body and returns the
  per-day breakdown (date, place, absence hours, base, reductions, amount, note) **without saving**, so the form can
  show live figures (the browser does not copy the rules, as with the planner).
* `GET /api/trips/{id}/statement.pdf` – the trip statement: header (person, purpose, period, destination), the day
  table, mileage table, linked bills with category/net/VAT, totals, the rules version and the signature line.
  Same PDF module as invoices, same fonts, escaped text; tested like the other PDFs.
* `GET /api/expenses` and the expense form accept `trip_id`.
* Rights (decided by the maintainer: configurable per role): new area `trips` in `core/rights.py` with the usual
  read/write scopes. Defaults: **boss** all/all; **employee** limited/limited (their own trips: create, edit, delete
  and see only those); **accountant** all/none (reads every trip and the statements). The boss can change the
  scope per role under *Settings → Roles* (the same mechanism as the team timeline visibility, see
  `specs/team-timeline.md`); the server enforces it on every route, and the matrix doc and the every-route-as-every-
  role test are updated. An employee's trip creates bills only under their own name.
* Services in `core/service/trips.py`; the router is thin; no SQL outside the repository.

## UI

* A separate **Business trips** page (own entry in the menu, next to Expenses; also reachable from the Expenses page). The trip list shows period, destination, purpose, allowance,
  mileage, bills total.
* Trip dialog: title, purpose, customer/project, start and end, a destination row per day (country select with search,
  city), a day grid with the three meal-provided checkboxes, the km table, and a live **breakdown panel** (from
  `/trips/preview`) that explains every figure in words: "Tue 12.11.: 24 h in Germany: 28,00 € − breakfast provided
  5,60 € = 22,40 €". Notes (country fallback, three-month limit) are visible, not tooltips.
* **Reminder checklist** ("Usual expenses of a trip") shown in the dialog and on the trip page, one line per item
  with a state of *Added* (count and sum), *Open*, or *Not needed* (a checkbox, remembered on the trip):
  1. **Daily allowance** – computed from the trip itself; the line shows the amount, or "no days over 8 hours".
  2. **Hotel / accommodation** – bills in the category *Accommodation*; hint: itemise the breakfast and city tax.
  3. **Transportation** – train, flight, taxi, parking, tolls (receipts) and the **mileage** table (private car).
  4. **Food** – meals while travelling are covered by the allowance; entertaining a business partner is *Business
     meals* (70 % rule, with participants and occasion). The line explains this instead of asking for receipts.
  5. **Material** – equipment, printing, samples bought for the trip.
  6. **Cost centre / project** – a required decision: pick the customer/project the trip is charged to, or choose
     "general" explicitly. The checklist stays *Open* until one is chosen.
  Each *Open* line has an "Add" button that opens the bill dialog with the category, the trip and the project
  preselected (the allowance and mileage lines open the trip's own sections). A trip cannot be finalized while a
  line is *Open* unless it is marked *Not needed* (the user can override with a confirm). The checklist items are a
  list in the trip rules file, so the maintainer can add categories (for example *Visa*, *Conference fee*)
  without code changes. Expense categories gain *Accommodation*, *Transportation*, *Material* if missing.
* "Add bill to this trip" opens the normal bill dialog with the trip preselected; the hotel bill uses itemised lines
  (room 7 %, breakfast part, city tax 0 %). A bill belongs to one trip.
* Finalize locks the figures; the statement button appears. A final trip shows "Reopen" for the boss.
* Everything escaped; keyboard operable; no information only in tooltips.

## Reports and compliance

* Finance and the tax estimate include allowance and mileage as 100 % deductible expenses without VAT, only for
  **final** trips (drafts add nothing). Mileage is booked at the trip start date with the start year's rate.
* `docs/compliance.md` gets a "Business trips" section with the rules above and the *(verify)* markers; the roadmap
  row links here.

## Tests

* Unit: the oracle table above, as exact-cent tests against the rate fixture; boundary cases (8 h exactly, midnight
  crossing, DST change days, start = end rejected, 90-day limit); reductions never below 0; unknown country uses the
  Luxembourg row; year change inside a trip uses each day's year; three-month limit and the four-week reset.
* API/contract: strict model bounds, round trip of unknown fields, `trip_id` validation, preview equals saved figures,
  matrix test for the new area, 401/403/404/422 cases, PDF content contains the day lines.
* Web: create a trip, change a meal checkbox and see the figure change, add a bill to the trip, finalize, download the
  statement; the seeded random monkey gets the tab.
* Finance: a trip with known figures changes the year totals by exactly the expected cents.

## Prerequisites and related fixes

* **Business-meal VAT bug** (separate bug fix, not part of this feature): "Deductible %" must apply to the net cost
  only; input VAT of appropriate, documented *Bewirtung* is reclaimed in full (§ 15 Abs. 1a UStG) *(verify)*. Today
  `calc.py` and the expense page apply the percentage to the VAT too, and the worked business-lunch example in
  `docs/specs/itemised-expenses.md` repeats this. Fix both before itemised expenses are implemented.
* Itemised expenses supply the hotel-bill lines (room 7 %, breakfast, city tax).
* A *Bewirtung* receipt needs participants and occasion; a separate small change to the business-meals dialog.

## Later (not in this version)

Passing mileage and travel costs on to customer invoices (they take the VAT rate of the main service, § 3 Abs. 10
UStG *(verify)*), employee trips, a logbook for company cars, foreign-currency receipts, importing the full BMF table
automatically.

## Open decisions for the maintainer

* Should the checklist block finalizing (as proposed) or only warn?
* Is a 90-day limit per trip right, or should long projects be modelled as several trips?
* Does the maintainer's tax adviser confirm the three-month rule for self-employed people, the 0,30 € mileage rate
  (and not 0,38 €), and the treatment of foreign VAT?

## Sources (secondary; replace with primary text during review)

* Domestic allowance, reductions and the three-month remark: steuerschroeder.de (Verpflegungsmehraufwand 2026),
  spendesk.com, pleo.io, n2f.com.
* Foreign rates 2026 and the BMF letter of 5 December 2025: ebnerstolz.de, ihk-muenchen.de, zdh.de, tk.de,
  haufe.de (Reisekosten Ausland), steuerberater-berlin-schuermann.de.
* Mileage and accommodation for the self-employed: gruender.de, steuerberaten.de, kostenlose-erechnung.de,
  selbststaendigkeit.de.
* Input VAT and *Bewirtung*: roedl.com, otto-schmidt.de, haufe.de, rechtslupe.de.
