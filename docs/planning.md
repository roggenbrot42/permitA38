# Planning: capacity, commitments, what-if and vacation

This guide explains the Planning pages in plain language. The numbers come from the server; the planner page only
draws them.

## Capacity

Your **capacity** for a week is the time you can work on projects:

- weekly hours divided by your workdays, for every workday of the week;
- minus **public holidays** of your federal state (Settings → state);
- minus **absence days** (vacation, sick days, other time off);
- minus **busy time** from your calendar, if a calendar is connected (the calendar module provides this; without it
  the busy time is zero).

Capacity is never negative. Part-time workdays (for example Monday to Wednesday only) are respected.

## Commitments

- **Confirmed** = contracts (including contracts created by accepting an offer). A contract with `hours per week`
  books that many hours spread over the assignee's workdays. A contract with a `total` books that total evenly over
  the workdays of its period. A partial week gets its share of the full week.
- **Binding offers** = offers that were sent, are not accepted yet, and whose validity date (`valid until`) has not
  passed. Until that date the offer is treated as binding.
- Holidays and absences do **not** reduce what a contract promises. If you promised 40 h in a week you take off,
  the week shows as over capacity. That is the point of the planner.

## Worst case and expected view

The planner assumes **every open offer is accepted** (worst case): if the worst case fits, you can take all of them
without overbooking. Each offer can also carry a win probability (in its plan) for an *expected* view. Without one,
the setting `planning_default_win_probability` (default 100 %) applies, so the default view is the worst case.

Each week shows:

| Field | Meaning |
|---|---|
| capacity | hours you can work |
| confirmed | hours promised by contracts |
| binding offers | hours of open offers (worst case) |
| free (after confirmed) | capacity minus contracts |
| free (worst case) | capacity minus contracts minus open offers |
| utilisation | (contracts + open offers) / capacity |
| status | `ok` up to 90 %, `tight` up to 100 %, `over` above 100 % (or capacity 0 with work booked) |

## Offer feasibility

When you save an offer (or accept one) the planner checks whether it fits. Example of the message:

> You offer 120 h between 12.01.2026 and 28.02.2026 but only 96 h are free in that period. Over capacity in the weeks
> of 16.02.2026 (over by 24 h). Causes: time off (40 h in week of 16.02.2026); Offer AN-2026-0007 (40 h in week of
> 16.02.2026).

Each such warning also lists options: shorten the end, start later, or reduce the scope. The warnings never block
saving; they are returned as `planning_warnings` and shown as a banner.

## Numbers that do not add up

Offers and invoices are checked for:

- hours in the lines versus the hours promised by the plan (`offer.hours_mismatch`);
- an hourly price below the minimum rate `planning_min_rate` (`rate.below_minimum`, `rate.effective_below_minimum`)
  or below the project/customer rate (`rate.below_reference`);
- invoices that bill more hours than were tracked (`invoice.hours_exceed_tracked`) or more than the offer covers
  (`invoice.exceeds_offer_hours`);
- projects over their hour budget (`project.over_budget`).

Each warning has the form `{code, severity, field, message, details, confirmable}`.

## Project periods

Projects have a **start** and an **end**. Customer projects need both (the form requires them); personal projects
may leave them empty. Existing projects get their period from their earliest and latest contract or time entry when
known; otherwise the planner shows a "set the dates" hint.

- **Extend manually**: on the project page, choose a new end date and a reason. The change is recorded in the
  extension history. A project can only be extended, not shortened this way.
- **Extend by offer**: accepting an offer that belongs to a project widens the project's period to cover the
  offer's plan; the extension is recorded with the offer's number.
- Time entries and contracts outside the period produce a soft warning.

## Revenue basis

Every forecast week states where its revenue comes from:

- **contracted** (`contract`): the week has confirmed contract hours; revenue is those hours at their rates;
- **open offers** (`offer-binding`): no contract, but open offers; revenue is their hours at their rates (worst case);
- **assumed** (`intended`): neither; revenue is your capacity that week times the default rate.

The planner splits the totals into contracted and assumed revenue, so you can see how much is not yet contracted.

## Plan & vacation (what-if)

Open **Planning → Plan & vacation**. The timeline shows, per week and per day: capacity, utilisation, public
holidays, time off, every contract and every open offer.

- **Vacation**: drag on the *Time off* row across the days you want. Drag a bar to move it, drag its edges to change
  its start or end. Press Delete on a time-off bar to remove it.
- **Contracts and offers**: drag to shift them, drag the edges to change their duration, and use the hours field to
  change hours per week (or total hours).
- **Draft**: every change is a *draft*. Nothing is written until you press **Apply** and confirm. **Discard** drops
  the draft. Weeks that become over capacity turn red; hover or focus a week to see why.
- **Scenarios**: save the draft under a name, load it later, or compare a scenario with the current base (free hours,
  over-capacity weeks, revenue). Scenarios are yours only.
- **Suggestions**:
  - *Find a time*: the best windows for N working days of vacation. Windows with the least overload win, then those
    that gain the most free calendar days around them (weekends and holidays).
  - *Earliest start for an offer*: the first start day from which N hours fit before a deadline.
  - *What must move?*: for a selected offer, the smallest move of another open offer that makes it fit, or the
    largest amount that fits.

**Keyboard**: focus a bar and press ←/→ to move it one day (Shift: one week); Alt+←/→ changes its end,
Ctrl+←/→ its start. Esc discards the change of that one item, Delete removes a time-off entry (a real entry is only
marked for deletion until you press Apply). Every draft change is also listed under the draft bar with its own
**Discard** button. Forms for vacation and the suggestions work without the mouse. **Touch**: dragging works with a finger on the bars and the
time-off row.

## Vacation entitlement

Each person can have `vacation days per year` and `carried over` days (set them in the planner header). The planner
counts **working days** of vacation only: weekends and public holidays are not counted, sick days are not vacation.
It shows used, planned and remaining days for the year, and warns when the planned days exceed the entitlement.

## Settings

| Key | Default | Used for |
|---|---|---|
| `planning_min_rate` | 0 (off) | minimum hourly rate warnings |
| `planning_default_win_probability` | 100 | win probability of offers without one |

## Limits

- The planner recomputes on the server for every change of a draft (a short delay per change). It does not keep a
  separate client-side copy of the rules; the browser shows what the server computes.
- Calendar busy time comes from a provider that the calendar module will implement; until then it is zero.
- Suggestions consider one person at a time. Contracts are never moved by suggestions; only open offers are.
- Scenario comparison uses the base of the currently selected person or team.
