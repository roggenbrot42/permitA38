# API contract: business trips

Source of truth for the backend and the web UI. Behaviour and tax rules: `docs/specs/business-trips.md`. Where this
file and the spec differ on the HTTP shape, this file wins. Conventions are those of the existing API.

## Conventions (as in the expenses API)

* Base path `/api`, JSON, auth by `Authorization: Bearer <token>` or the session cookie.
* **Money is a JSON number** with at most 2 decimals (whole cents), exactly like `expenses.net`; the server computes
  with `core/money.py` (decimals, half-up) and serialises the cent value. Clients never compute it. Example: `22.4`,
  `71.25`, `0`.
* Dates `YYYY-MM-DD`; local date-times `YYYY-MM-DDTHH:MM` (no zone, no seconds, 24 h clock).
* Ids match `^[0-9a-z]{6,32}$`. An empty string for an optional id is read as `null`.
* Request bodies use the `TripIn` model: known fields are validated strictly; unknown fields are kept
  (`extra="allow"`, as in the rest of the repository), they are not rejected with 422. Responses use the lenient `Trip` model:
  unknown stored fields are returned unchanged; `null` fields are omitted (`response_model_exclude_none`).
* **Response-only** fields are marked below. If a client sends one, it is ignored (not an error), as `net`/`vat` of
  expense lines.
* Errors: `{"detail": "<message>"}` for 401/403/404/409 and for service-level 422; the 422 of body/query
  validation is `{"detail": [{"loc": ["body","purpose"], "msg": "...", "type": "..."}]}` (input is never echoed).
  The web client's `api.js` turns both into `Error.message` with `.status`.

## Rights

New area `trips` in `core/rights.py` (`AREAS["trips"] = "Business trips"`), `COLLECTION_AREA` is NOT extended (trips
have their own router). `Needs(area, op)` only checks "scope is not none"; the scope is applied in the service.

| Role | read | write | meaning |
|---|---|---|---|
| boss | all | all | every trip |
| employee | limited | limited | only trips with `user_id` = self; create/edit/delete only those |
| accountant | all | none | reads every trip and statement |

"Not visible" (another person's trip for a limited role) is **404 "Not found"**, never 403 (existence is not
revealed). A role with scope `none` gets **403 "Forbidden"** on every route. The scope per role may later be changed
by the boss; the server always asks `rights.scope(role, "trips", op)`.

| Route | area, op | scope rule |
|---|---|---|
| `GET /api/trips` | trips, read | all: every trip; limited: own only |
| `POST /api/trips/preview` | trips, **write** | needs write (accountant gets 403); no scope filter, nothing stored |
| `POST /api/trips` | trips, write | limited: `user_id` forced to self |
| `GET /api/trips/{id}` | trips, read | limited: own only (else 404) |
| `PUT /api/trips/{id}` | trips, write | limited: own only (else 404) |
| `DELETE /api/trips/{id}` | trips, write | limited: own only (else 404) |
| `GET /api/trips/{id}/statement.pdf` | trips, read | limited: own only (else 404) |
| `POST /api/trips/{id}/reopen` | trips, write, **full** (`Needs("trips","write", full=True)`) | boss only |

`Needs` for `full=True` gives 403 for an employee. `docs/roles.md` and the every-route-as-every-role test get the
area and all routes above.

## The trip object

Request (`TripIn`, used by POST and PUT; PUT replaces the whole trip, same as other collections):

```json
{
  "title": "Customer visit Hamburg",
  "purpose": "Workshop at the customer's site",
  "customer_id": "k3j9x0ab12",
  "project_id": "p8d2m4qz7w",
  "start": "2026-11-09T08:00",
  "end": "2026-11-11T18:00",
  "places": [
    {"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"},
    {"from_date": "2026-11-11", "country": "AT", "city": "Wien"}
  ],
  "meals_provided": [
    {"date": "2026-11-10", "breakfast": true, "lunch": false, "dinner": false}
  ],
  "mileage": [
    {"date": "2026-11-09", "from": "Berlin", "to": "Hamburg", "km": 288.5, "purpose": "Outbound"}
  ],
  "cost_centre": "project",
  "not_needed": ["material", "food"],
  "status": "draft"
}
```

| Field | Type / bounds | Required | Notes |
|---|---|---|---|
| `title` | string, 0-200 | no | |
| `purpose` | string, 1-200, not blank | **yes** | printed on the statement |
| `origin` | string 0-200 or null | no | start place of the trip (the first dialog of the wizard, default: the business settings city); plan only, not in the statement totals |
| `destination` | string 0-200 or null | no | destination place of the trip; prefills the itinerary in the UI |
| `destination_country` | `^[A-Z]{2}$` or null | no | ISO 3166-1 alpha-2, e.g. `DE`; lower case or longer codes are 422 |
| `customer_id` | id or null | no | must exist, else 422 `"Unknown customer"` |
| `project_id` | id or null | no | must exist, else 422 `"Unknown project"` |
| `start`, `end` | local date-time | **yes** | `end` > `start`; at most 90 calendar days (inclusive); else 422 |
| `places` | list, max 20, of `{from_date, country, city?}` | no (default `[]`) | `from_date` date; `country` ISO 3166-1 alpha-2, upper case (`^[A-Z]{2}$`); `city` string 0-200. Empty list = domestic (`DE`). |
| `meals_provided` | list, max 90, of `{date, breakfast, lunch, dinner}` | no | `date` within the trip; booleans default false; days not listed = none |
| `mileage` | list, max 100, of `{date, from, to, km, purpose?}` | no | `date` within the trip; `from`,`to`,`purpose` strings 0-200; `km` number 0.1-5000 with at most one decimal; the JSON key is `from` (not `from_`) |
| `itinerary` | list, max 60, of `{depart, from, to, country?, mode?, note?}` | no (default `[]`) | `depart` local date-time within `[start, end]` (else 422 `"itinerary leg departing <t> is outside the trip"`); `from`, `to` strings 0-200 (required); `country` `^[A-Z]{2}$`; `mode` `train`\|`flight`\|`car`\|`taxi`\|`bus`\|`other`; `note` string 0-200. Stored sorted by `depart` (stable). See "Itinerary and planned items" |
| `items` | list, max 100, of `{id?, date, date_to?, category, from?, to?, text, expected?}` | no (default `[]`) | `date`, `date_to` `YYYY-MM-DD` (`date_to` not before `date`, else 422 `"item date_to is before date"`); `category` one of `hotel`\|`train`\|`flight`\|`taxi`\|`other` (else 422); `from`, `to` strings 0-200; `text` string 0-200 (required, may be empty); `expected` money >= 0, whole cents, optional. Items are not checked against the trip period. `id` is generated when missing or empty, unique within the trip (duplicate sent ids -> 422 `"duplicate trip item id"`), kept on PUT |
| `cost_centre` | `"project"` \| `"general"` \| null | no | the explicit decision for checklist line 6; `"project"` requires `project_id` or `customer_id` (else 422 `"Choose a customer or project, or general"`) |
| `not_needed` | list, max 20, of checklist ids | no | checklist lines marked *Not needed*; unknown id -> 422 `"Unknown checklist item: <id>"` |
| `status` | `"draft"` \| `"final"` | no (default `draft`) | see Finalizing |

Response (`Trip`, the same fields as the request plus the response-only ones below; POST, PUT, GET one; GET list returns an array of the same objects):

```json
{
  "id": "t7f3k2m9qa",
  "created": "2026-10-23T09:12:44Z",
  "updated": "2026-10-23T09:12:44Z",
  "user_id": "u1a2b3c4d5",
  "title": "Customer visit Hamburg",
  "purpose": "Workshop at the customer's site",
  "customer_id": "k3j9x0ab12",
  "project_id": "p8d2m4qz7w",
  "start": "2026-11-09T08:00",
  "end": "2026-11-11T18:00",
  "places": [
    {"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"},
    {"from_date": "2026-11-11", "country": "AT", "city": "Wien"}
  ],
  "meals_provided": [
    {"date": "2026-11-10", "breakfast": true, "lunch": false, "dinner": false}
  ],
  "mileage": [
    {"date": "2026-11-09", "from": "Berlin", "to": "Hamburg", "km": 288.5, "purpose": "Outbound"}
  ],
  "cost_centre": "project",
  "not_needed": ["material", "food"],
  "status": "draft",
  "allowance_total": 66.4,
  "mileage_km": 288.5,
  "mileage_total": 86.55,
  "rules_version": "2025-12-05",
  "days": [
    {"date": "2026-11-09", "country": "DE", "city": "Hamburg", "absence_hours": 16.0, "base": 14.0,
     "reductions": [], "amount": 14.0, "note": ""},
    {"date": "2026-11-10", "country": "DE", "city": "Hamburg", "absence_hours": 24.0, "base": 28.0,
     "reductions": [{"meal": "breakfast", "amount": 5.6}], "amount": 22.4, "note": ""},
    {"date": "2026-11-11", "country": "AT", "city": "Wien", "absence_hours": 18.0, "base": 30.0,
     "reductions": [], "amount": 30.0, "note": "Rate for AT is a placeholder: verify"}
  ],
  "bills": {"count": 2, "net": 180.0, "vat": 22.8},
  "checklist": [ "... see Checklist ..." ]
}
```

Response-only (computed by the server, stored with the trip on save except `bills` and `checklist`, which are
computed on every read): `id`, `created`, `updated`, `user_id`, `allowance_total`, `mileage_km`, `mileage_total`,
`rules_version`, `days`, `bills`, `checklist`. `mileage_total` = round-half-up(Σkm x 0.30) once per trip.
`bills` = the linked expenses (`expenses.trip_id` = this id): `count`, sum of `net`, sum of `vat` (numbers, cents;
`{"count":0,"net":0,"vat":0}` when none). `id`/`created`/`updated` follow the other collections' format.

Response-only, added to the `Trip` (computed on every read, never stored, a client value is ignored):
`items_expected_total` = sum of the items' `expected` (money; `0` without items) and `items_actual_total` = sum of
the gross (net + VAT) of **all** bills linked to the trip, those booked against an item and unplanned ones alike
(money; `0` without bills). Example for the wizard tests: items 80,00 and 120,00 expected, bill on the first item
gross 95,20 and unplanned bill gross 11,90 -> `items_expected_total: 200`, `items_actual_total: 107.1`.

`user_id` is the owner = the creator; it is never changed by PUT (an employee cannot hand a trip to someone else; the
boss editing a trip keeps the original owner).

## Day breakdown (`days`, and the result of preview)

Times and days: an `end` of exactly 00:00 on a later date is read as 24:00 of the previous day, before the days are
classified (Monday 09:00 to Tuesday 00:00 is a one-day trip on Monday, 15 hours). Absence is elapsed time in
Europe/Berlin, so a DST change day has 23 or 25 hours. An arrival or departure day needs at least one minute of
absence to pay. A country whose rate row is a placeholder adds the note `Rate for <CC> is a placeholder: verify`
(this includes the Luxembourg fallback row). Notes are shown in full, never as tooltips.

```json
{
  "date": "2026-11-10",
  "country": "DE",
  "city": "Hamburg",
  "absence_hours": 24.0,
  "base": 28.0,
  "reductions": [{"meal": "breakfast", "amount": 5.6}],
  "amount": 22.4,
  "note": ""
}
```

| Field | Type | Notes |
|---|---|---|
| `date` | date | one entry per calendar day from `start` to `end` inclusive |
| `country` | `^[A-Z]{2}$` | the country whose row was used (`LU` after fallback) |
| `city` | string \| null | the city of the place entry; omitted if none |
| `absence_hours` | number, 2 decimals max | hours of absence on that day (`24.0`, `7.5`) |
| `base` | money | before reductions |
| `reductions` | list of `{meal: "breakfast"\|"lunch"\|"dinner", amount: money}` | empty when nothing provided; order breakfast, lunch, dinner; each amount rounded half-up on its own |
| `amount` | money | `max(0, base - Σreductions)` |
| `note` | string | `""` or a visible sentence, e.g. `"Country not in the rate table: Luxembourg rate used"`, `"Absence not more than 8 hours: no allowance"`, `"Three-month limit reached: no allowance"` |

Several notes are joined with `"; "`. Display text for the UI is built from these fields; the browser holds no tax
rule.

## Routes

### `GET /api/trips`

No query parameters. 200: array of `Trip` (sorted by `start` descending, ties by `id`). Errors: 401, 403.

### `POST /api/trips/preview`

Body: a `TripIn` (all validation of POST applies; `status` is accepted and ignored; ids must exist). Nothing is saved.
200:

```json
{
  "days": [
    {"date": "2026-11-09", "country": "DE", "city": "Hamburg", "absence_hours": 16.0, "base": 14.0,
     "reductions": [], "amount": 14.0, "note": ""},
    {"date": "2026-11-10", "country": "DE", "city": "Hamburg", "absence_hours": 24.0, "base": 28.0,
     "reductions": [{"meal": "breakfast", "amount": 5.6}], "amount": 22.4, "note": ""},
    {"date": "2026-11-11", "country": "AT", "city": "Wien", "absence_hours": 18.0, "base": 30.0,
     "reductions": [], "amount": 30.0, "note": "Rate for AT is a placeholder: verify"}
  ],
  "allowance_total": 66.4,
  "mileage_km": 288.5,
  "mileage_rate": 0.3,
  "mileage_total": 86.55,
  "rules_version": "2025-12-05"
}
```

All fields are response-only. The numbers are identical to what a save of the same body stores (tested). Errors:
401, 403, 422 (validation; `"No rate table for year 2031"` for a year without table).

### `POST /api/trips` -> 200 `Trip`

Returns the created `Trip` (200, same as the generic CRUD routers). Errors: 401, 403, 422. 409 if
`status` is `final` and the checklist has open lines (see Finalizing), unless `?force=true`.

### `GET /api/trips/{id}` -> 200 `Trip`

Errors: 401, 403, 404 (`"Not found"`, also malformed id and not visible).

### `PUT /api/trips/{id}` -> 200 `Trip`

Full replacement of the editable fields, recomputing all figures. Errors: 401, 403, 404, 422, and

* A PUT must send the item ids back to keep them: an item sent without an id gets a new one, and removing an item
  that has bills is **422** `"Item has bills"`.
* **409** `{"detail": "This trip is final; the boss can reopen it"}` if the stored trip is `final` (any body,
  including one that sends `status: "draft"`; only `reopen` changes a final trip).
* **409** `{"detail": "Checklist has open items: Hotel / accommodation, Transportation"}` when the body has
  `status: "final"` and lines are open (see Finalizing), unless `?force=true`.

### `DELETE /api/trips/{id}` -> 204, empty body

Only a draft. Linked bills are kept and lose their `trip_id`. Errors: 401, 403, 404, **409**
`{"detail": "This trip is final; reopen it before deleting"}`.

### `POST /api/trips/{id}/reopen` -> 200 `Trip`

Body: none (`{}` accepted). Boss only (employee and accountant: 403). The trip becomes `draft`; the figures stay as
stored (they are recomputed by the next PUT). Errors: 401, 403, 404, **409** `{"detail": "Only a final trip can be
reopened"}` for a draft.

### `GET /api/trips/{id}/statement.pdf`

200, `Content-Type: application/pdf`, `Content-Disposition: inline; filename="trip-<id>.pdf"` (the same helper as the
invoice PDF; the filename is sanitised). Works for drafts as well (the PDF then shows "Draft"). Content: header
(person, purpose, period, destinations), the day table, mileage table, linked bills (category, net, VAT), totals,
rules version, signature line. Errors: 401, 403, 404. The UI opens it with `api.url('trips/<id>/statement.pdf')`
(cookie-authenticated link, as for invoices).

## Finalizing

`PUT` with `"status": "final"` locks the figures: the server recomputes once and stores them. Preconditions:

1. The usual validation (422).
2. Every checklist line is `added` or `not_needed`; otherwise **409** with the message above listing the labels of
   the `open` lines. The UI shows a confirm and repeats the PUT with `?force=true`, which skips this check only.
   (`force` does not skip the final-lock of a stored final trip.)

A final trip: PUT/DELETE give 409; bills can still be linked/unlinked via the expense routes (the statement shows the
current bills). `reopen` makes it editable again.

## Checklist (`Trip.checklist`, response-only)

The definitions come from the trip rules file (`core/trip_rates/checklist.json`: `id`, `label`, `hint`). The
state is computed on every read:

```json
[
  {"id": "allowance", "label": "Daily allowance", "hint": "Computed from the trip itself; no receipts needed.",
   "state": "added", "count": 3, "sum": 66.4},
  {"id": "accommodation", "label": "Hotel / accommodation", "hint": "...",
   "state": "open", "count": 0, "sum": 0},
  {"id": "transportation", "label": "Transportation", "hint": "...",
   "state": "added", "count": 3, "sum": 266.55},
  {"id": "food", "label": "Food", "hint": "...", "state": "not_needed", "count": 0, "sum": 0},
  {"id": "material", "label": "Material", "hint": "...", "state": "not_needed", "count": 0, "sum": 0},
  {"id": "cost_centre", "label": "Cost centre / project", "hint": "...", "state": "added", "count": 1, "sum": 0}
]
```

The bills behind the example are two Transportation bills: 120,00 net with 22,80 VAT, and 60,00 net with VAT 0 (so bills net 180,00, VAT 22,80; the transportation line counts both bills and the 288,5 km mileage, 266,55 = 180,00 + 86,55).

`state` is one of `"added"`, `"open"`, `"not_needed"`; `count` integer; `sum` money. Rules (first match wins):

| Line id | `not_needed` in `not_needed[]` wins | `added` when | `count` / `sum` |
|---|---|---|---|
| `allowance` | no (computed) | `allowance_total > 0`; otherwise state is `not_needed` ("no days over 8 hours") | days with `amount > 0` / `allowance_total` |
| `accommodation` | yes | any linked bill with `category` = `Accommodation` | bills / Σnet |
| `transportation` | yes | any linked bill with category `Transportation` OR `mileage` non-empty | bills + mileage entries / Σnet + `mileage_total` |
| `food` | yes | always (informational line, never blocks) | `0` / `0` |
| `material` | yes | any linked bill with category `Material` | bills / Σnet |
| `cost_centre` | no (not skippable) | `cost_centre` is `"project"` (with a project or customer) or `"general"` | 1 / 0 |

Other checklist ids added to the file later: `added` when a linked bill has `category` equal to the item `id`'s label,
else `open`, skippable. Matching of `category` is case-insensitive on the stored text. The checklist is a view: it
is never stored, only `not_needed` and `cost_centre` are.

## Itinerary and planned items (wizard, docs/specs/business-trips-wizard.md)

Both are stored in the trip record (the `extra` JSON column; no migration) and returned as they are stored.

Example of a trip response part:

```json
{
  "itinerary": [
    {"depart": "2026-11-09T08:30", "from": "Berlin", "to": "Hamburg", "country": "DE", "mode": "train"},
    {"depart": "2026-11-11T16:45", "from": "Wien", "to": "Berlin", "mode": "flight"}
  ],
  "items": [
    {"id": "k3j9x0ab12", "date": "2026-11-09", "category": "train", "text": "ICE Berlin - Hamburg", "expected": 80},
    {"id": "p8d2m4qz7w", "date": "2026-11-09", "category": "hotel", "text": "Hotel Hamburg", "expected": 120}
  ],
  "items_expected_total": 200,
  "items_actual_total": 0
}
```

* A travel day in the UI is built from two legs (start -> destination, destination -> end place); the server does not
  group legs into days.
* Items are planning figures only. `expected` never enters `days`, `allowance_total`, `mileage_total`, the statement
  totals, the checklist, VAT or any report; the statement prints the itinerary table only.
* Deleting an item that still has bills: `PUT` -> 422 `{"detail": "Item has bills"}`; the trip is unchanged.
* `DELETE` of a draft trip also clears `trip_item_id` of its bills.

## Finance

The finance report counts only **final** trips: a draft adds nothing until it is finalised. A final trip adds its
meal allowance per day on that day's date and its mileage at the trip start date, with the rate of the start year,
as deductible expenses without VAT ("Meal allowance" and "Mileage").

## Expense changes

`ExpenseIn`/`Expense` already carry `trip_id` (id or null, validated: unknown trip -> 422 `"Unknown trip"`; a limited
role's own trips only, another's trip is `"Unknown trip"` too). `GET /api/expenses` is unchanged; the UI filters
by `trip_id` client side (no new query parameter). Expense categories gain `Accommodation`, `Transportation`,
`Material`.

`ExpenseIn`/`Expense` gain `trip_item_id` (id or null). Only together with `trip_id`; the item must be one of that
trip's `items`, else 422 `"Unknown trip item"` (also when `trip_id` is missing). A bill without `trip_item_id` but with
`trip_id` is an unplanned bill.

## Contract decisions (where the spec left a choice)

1. Money is a JSON number (cents), not a decimal string: the expenses API uses numbers.
2. `reductions` is a list of `{meal, amount}` per day (the spec's `reductions[]`); the draft computation returning a
   single number per day has to be changed to this shape (the day total is the sum of the list).
3. `cost_centre` is a stored field (`project`/`general`/null) because the spec says the checklist stays open until an
   explicit decision; "general" cannot be inferred from an empty `project_id`.
4. The checklist is part of every trip response (computed), not a separate route; preview does not contain it
   (no bills yet).
5. Finalize is `PUT` with `status: "final"`, no separate route; the open-checklist guard is a 409 that `?force=true`
   skips (the spec: "override with a confirm").
6. Not visible = 404; no read/write access at all = 403. Preview needs write access.
7. Create answers 200 like the generic CRUD routers, delete 204.
8. `places`: empty means domestic; countries are two-letter codes; unknown codes fall back to Luxembourg with the
   note (no 422), so the table can be extended without breaking saved trips.
9. Deleting a draft trip keeps its bills and clears their `trip_id`.
10. The statement works for drafts too.
11. No list filters or pagination in this version.
12. Itinerary and items are stored in the trip's `extra` JSON (no migration); items are not checked against the trip
    period (only legs are), and an item id is not a global id: it is unique within its trip.
13. A `trip_item_id` without `trip_id` gives the same 422 `"Unknown trip item"` as a foreign item.
14. The statement prints the itinerary table (date/time, from, to, mode) only when legs exist; items are not printed.
