# Specification: business trip workflow (plan, bills, settlement)

Amends [`business-trips.md`](business-trips.md) (read that first; rules, rate tables and the calculation are unchanged).
Wish of the maintainer: work like a travel tool (Concur): **plan first**, **add items and receipts**, then **settle**.
Review tier: **Full** (money, VAT on re-billed costs, status rules, a new invoice draft path).
Release: alpha.5 (the maintainer asked for it there). Status: **proposal, waiting for confirmation**.

## 1. The three stages

| Stage | Stored `status` | What you do | Locked |
|---|---|---|---|
| **Planned** (shown as *On the road* between start and end) | `planned` | create the plan; attach bills and upload receipts as they appear; edit everything | nothing |
| **Completed** | `completed` | button *Complete trip* once the trip has started; confirm what really happened (meals provided, mileage, final itinerary) | period (`start`/`end`), customer, project, cost centre |
| **Settled** | `settled` | button *Create settlement*: tax statement, and the customer settlement if the customer pays | everything; boss may *Reopen* (back to `completed`) |

"On the road" is a display label computed from `start <= now <= end`, not a stored state.
Migration: `draft` -> `planned`, `final` -> `settled` (a final trip was already locked and had a statement).
`PUT` accepts only the transitions above; any other value is a 422 with the reason. The reminder checklist
(business-trips.md, UI) keeps its meaning but now gates **Complete trip** (confirm to override), not finalizing.

## 2. Stage 1: the plan

Fields (existing ones stay): `purpose` is the **reason for the trip** (*Reisegrund*, required, shown first in the form),
`start`/`end` the travel times, `customer_id`/`project_id`/`cost_centre` the cost assignment (unchanged rule: choose a
project/customer or "general"). New:

* `itinerary`: list (max 30) of legs `{depart: LocalDateTime, from: Short, to: Short, mode, note?: Short}`, with
  `mode` in `train | flight | car | taxi | bus | other`. It documents the route and is printed on the statement. Legs
  must lie inside `[start, end]`. `places` (country per day) stays the input of the allowance; the UI offers
  "take countries from the itinerary" as a convenience, it does not derive them silently.
* `customer_pays`: boolean (default false). "The customer reimburses this trip." Requires `customer_id`.

## 3. Stage 2: items and receipts

* A bill (`expenses`) already carries `trip_id`. The trip page gets one list "Items" with an **Add item** button for
  hotel / transport (train, flight, car) / taxi / other, which opens the bill dialog with the trip, project and
  category preselected, including the receipt upload (existing attachment rules). Bills can be added from the plan
  stage on (a booked hotel or flight), not only after the trip.
* New bill field `rebill`: boolean (default false, only meaningful with `trip_id` on a trip with `customer_pays`).
  Ticked per item: "charge this to the customer". A bill with `rebill` on a trip without `customer_pays` is a 422.
* The list shows per item the net, VAT and gross, whether a receipt is attached, and `rebill`. A bill without
  receipt is marked "receipt missing" (hint, never blocks).

## 4. Stage 3: settlement ("Create settlement")

Allowed from `completed`; sets `settled`, locks, and produces:

1. **Tax statement** (the existing trip statement, now with the itinerary): allowance per day, mileage, all bills.
   Unchanged calculation and deductibility (allowance and mileage 100 %, VAT 0).
2. **Customer settlement** only if `customer_pays`: an **invoice draft** for `customer_id` (project preselected), never
   a finalized invoice. The draft contains, in this order:
   * one line per `rebill` bill: description = bill title/supplier + date, quantity 1, unit price = the bill **net**
     amount (re-billing at cost, no mark-up); itemised bills become one line per bill line. VAT rate: the default
     rate of the issuing business for the main service, **editable in the draft** (see open decision 1).
   * one line "Meal allowance (Verpflegungsmehraufwand)" with the allowance total if the maintainer ticks *Bill
     allowance* (default off), VAT as above;
   * one line "Mileage" `km x rate` if mileage exists and *Bill mileage* is ticked (default off).
   The trip stores `invoice_id`. While that invoice is a draft, *Reopen* and re-running the settlement **replace**
   the draft's trip lines (identified by a `trip_id` marker on the lines) and keep the user's other lines; once
   it is finalized, the trip stays settled and the settlement button is disabled with the reason.
   No settlement draft is ever created twice: the second call returns the existing draft (idempotent).

All sums use `core/money.py` (exact decimals, half-up cents); the draft is computed server-side, the client sends only
the two ticks. Rights: settlement needs `trips:write` **and** `invoices:write`; without the latter only the tax
statement is produced (the customer part is skipped with a visible note).

## 5. API (contract delta)

* `POST /api/trips/{id}/complete`, `POST /api/trips/{id}/settle` (body `{bill_allowance?: bool, bill_mileage?: bool}`,
  response: trip + `invoice_id`), existing `POST /{id}/reopen` goes `settled -> completed`.
* `TripIn`/`Trip`: `itinerary`, `customer_pays`; `status` becomes `planned | completed | settled` and is **read-only**
  on `PUT` (changed only by the three action routes). `Trip`: `invoice_id`, `settled_at`.
* `ExpenseIn`/`Expense`: `rebill`. Migration `NNNN-trip-workflow` in all three dialects (status mapping, new columns).
* Error codes for illegal transitions, customer without `customer_pays`, rebill rules, missing `invoices:write`.

## 6. Tests (the oracle)

Hand-computed: trip Mon 9 Nov 08:00 - Wed 11 Nov 18:00 domestic (allowance 14 + 28 + 14 = 56,00 EUR, existing oracle) with
hotel bill net 200,00 (rebill), taxi net 20,00 (rebill), mileage 100 km x 0,30 = 30,00: draft without the optional
lines = 220,00 net; with both ticks = 306,00 net; at 19 % VAT 364,14 gross (306 x 0,19 = 58,14). Also: transitions
(every allowed and every refused one), migration mapping, idempotent settlement, replace-on-reopen with a user-added
line surviving, finalized invoice blocks, rights matrix, `rebill` refused without `customer_pays`, itinerary bounds,
randomized state machine (trip status model), UI tests for the three stages and the receipt upload.

## 7. Open decisions

1. **VAT on re-billed costs.** Default proposed: the main service's rate, editable in the draft, flagged *(verify)*
   in the UI. Whether pass-through (*durchlaufender Posten*) or the allowance may be billed VAT-free is for the tax
   adviser; the code does not decide it.
2. Should planned (estimated) costs be entered before real bills exist (Concur's estimates)? Proposed: no, only real
   bills and receipts, to keep the scope small.
3. Foreign-currency receipts stay out (see business-trips.md, Later).
