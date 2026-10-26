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
  the assignee's **available days** in its period: workdays that are neither a public holiday nor a day off (vacation,
  sick or other). Hours that would fall into a vacation therefore move to the days before and after it; nothing is
  booked into the vacation. Calendar meetings do not change which days are available. If a person has no available
  day in the period, the total is spread over all workdays instead, so the overload stays visible. A partial week
  gets its share of the full week.
- **Binding offers** = offers that were sent, are not accepted yet, and whose validity date (`valid until`) has not
  passed. Until that date the offer is treated as binding.
- Holidays and absences do **not** reduce what an `hours per week` contract promises, but the hours move to the days
  that can work: they are spread over the assignee's available days of the week (workdays that are neither a public
  holiday nor a day off). If you promised 40 h in a week with a holiday, the week shows as over capacity; 32 h fit in
  such a week (8 h on four days). Only when a week has no available day left the nominal spread over all workdays is
  kept.

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

## Capacity per project (ring fence)

Capacity belongs to projects. You give a person's hours to a project with an **allocation** (hours per week for a
period, see Allocations below). Only the contracts and open offers of **that project** draw on it; hours a project
does not use are **not** lent to another project, and a project's shortfall is a **gap of that project**.

- A person's real capacity of a day (see Capacity) is shared out to the projects they are allocated to:
  `hours per week / available days of the week` per allocation (days without holiday or time off, like a contract). If the allocations of one day add up to more than the real capacity
  (over-allocation, or a holiday or vacation day), every allocation is scaled down pro rata: nothing is invented and
  nothing goes to a third project. A holiday or absence day gives every allocation 0 hours.
- What a person has beyond their allocations is **unassigned** capacity (a person with no allocation is entirely
  unassigned). Contracts and offers **without a project** draw on unassigned capacity only.
- The **gap** is `max(0, planned - capacity)` per project and **day**, summed over the week. Because the hours of a
  contract are spread over the available days (see Commitments), a holiday or a day off alone does not create a gap;
  real overload does, and a project's gap on one day is not covered by another day's free hours of the same week.
- The week row of the whole team keeps its fields (capacity, confirmed, utilisation, ...) and gains `gap` (the sum of
  all project gaps) and `projects[]` with one entry per project and `unassigned`. The team status is `over` whenever
  any project has a gap, even if the team total looks fine (`tight` at 100 % used to hide the gap).
- Forecast parameters: `project_id` (a project id or `unassigned`; empty = no filter) shows only that project; `by_project=0` omits
  `projects[]`. `GET /api/plan/day-table?from=&to=` shows capacity, planned hours and gap per day, project and pool
  (at most 190 days).
- Subcontractors are a second pool (`external`) inside a project, not netted with the employees. Until that feature
  exists the external pool is always zero.

**After the upgrade** nobody has allocations, so every project with contracts or open offers shows a gap equal to its
whole demand, while the team total is unchanged and project-less contracts behave as before. Press **Allocate my
capacity** (`POST /api/allocations/bootstrap`): the preview lists one allocation per active contract with a project and
assignee, with the contract's period and the assignee's peak weekly demand rounded up to 0.25 h; confirming creates
them. A second run creates nothing. Open offers are not allocated automatically.

With `by_project=0` there is no project dimension: the team `capacity.over` warning uses the utilisation rule only
(week utilisation above 100 %, `scope: "team"`), not the gaps.

Before the bootstrap every project with contracts shows capacity 0 and a gap equal to its demand, and an offer of a
project warns (`offer.project_gap`), because no hours are allocated to it yet. `what-must-move` takes no project in its
body: the draft is treated as unassigned and only offers without a project are moved.

### Allocations

`GET/POST /api/allocations`, `PUT/DELETE /api/allocations/{id}` (boss only, area `planning`). Hours per week are
0.25 to 80, a period is at most 1830 days, the project must not be done or archived and the person must be active.
Over-allocation is never an error: the answer carries `planning_warnings`
(`allocation.employee_over_allocated`, `allocation.overlap_same_project`). You cannot delete a person or a project
that has allocations; deactivate the person instead.

### Warnings per project

| Code | Meaning |
|---|---|
| `capacity.gap` | a project (and pool) has a gap in a week; other projects' free hours do not cover it |
| `capacity.over` | the unassigned capacity is overbooked in a week |
| `allocation.employee_over_allocated` | judged per day: on some day the person's allocations add up to more than the real capacity of that day, because of too many hours (`weekly_hours`) or because a holiday or day off leaves none (`time_off`). Allocations to different days of a week (P Mon-Tue, Q Wed-Fri) do not warn. A person with allocations over several weeks of leave gets one `time_off` warning per week, which can be noisy |
| `allocation.no_demand` | an allocation has capacity in a week but its project plans nothing (info) |
| `allocation.overlap_same_project` | the same person has two overlapping allocations to one project (info) |
| `offer.project_gap` | an offer of a project does not fit in that project's allocated hours (the whole gap of the week is reported) |

Offers without a project are checked against the unassigned capacity (`capacity.offer_over`). The suggestions
(earliest start, what must move) work per project; vacation suggestions count the resulting gaps of all projects.

The "Not covered" banner of the forecast lists the gap per project and gives a remedy: "Allocate further capacity" (and "Allocate" per project) opens the allocations dialog, filtered to the project or person when the page is. "Allocate my capacity" stays visible while any gap is left, also when allocations exist; the bootstrap previews what it would create and skips people and projects that are already allocated. These buttons need the planning write right; read-only users see the text only. Shortfall and over-allocation warnings get one remedy line with an "Open allocations" button. The server's messages are unchanged.

### What-if with allocations

A draft can contain allocation changes (`changes.allocations`: new, changed hours or period, or deleted).
`POST /api/plan/simulate` returns the same structure as the forecast plus `delta` (the change of the gap against the
base, per week, project and in total). A change to one project's contract changes only that project's rows. Applying
a draft writes allocation rows only with `confirm: true` and returns their warnings as `allocation_warnings`.

## Subcontractors

Subcontractors (suppliers of kind `person-capacity`) are planned like people, but their hours are not part of the
team's base capacity: they come from **external allocations** that an accepted incoming offer creates
(`docs/contracts/subcontractors.md`, "Planner integration").

* Hours per day: a supplier's `hours_per_week` (or the `total_hours` of the plan) is spread over its own workdays,
  leaving out its unavailable windows and public holidays. A cancelled allocation keeps the days before the cut.
* The external pool is per project. Demand of a contract whose assignee is a subcontractor draws on the pool of the
  contract's project only; hours bought for project P never cover demand of project Q (the ring fence). A gap in the
  external pool shows as `capacity.gap` with `pool: "external"`.
* Week rows and totals carry `external_capacity`, `external_assigned`, `external_gap`, `external_cost` (the planned
  cost of the external hours of the week, not invoiced cost) and `margin` (revenue minus external cost). Bought
  capacity without demand is paid for and shows as `allocation.no_demand` (info).
* `include_external=0` on `GET /api/forecast`, `GET /api/plan/day-table` and in `POST /api/plan/simulate` builds the
  world without subcontractors; the numbers then equal those of a database without any supplier.
* Archiving or deactivating a supplier with open allocations (v1 behaviour): the planner no longer counts its
  capacity or its external cost in the forecast, while the commitments (`GET /api/finance/commitments`) still count
  the allocation until it is cancelled or ends. Cancel the allocations first if the hours should leave the plan.
* Expenses linked to an external allocation are compared with its planned cost
  (`allocation.invoiced_exceeds_planned`). Commitments (`GET /api/finance/commitments`) are derived, never written as
  expenses.

## Plan & vacation (what-if)

Open **Planning → Plan & vacation**. The timeline shows, per week and per day: capacity, utilisation, public
holidays, time off, every contract and every open offer.

- **Vacation**: drag on the *Time off* row across the days you want. Drag a bar to move it, drag its edges to change
  its start or end. Press Delete (or Backspace) on a time-off bar to remove it.
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

**Keyboard**: click a bar (it is outlined and focused) or Tab to it, then ←/→ moves it one day and
PageUp/PageDown one week; Shift+←/→ changes its end, Ctrl+←/→ its start (Alt is not used: browsers take Alt+←/→ for
Back/Forward). Esc discards the change of that one item, Delete or Backspace removes a time-off entry (a real entry is only
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
- Suggestions consider one person at a time and one project at a time. Contracts are never moved by suggestions; only open offers are.
- Scenario comparison uses the base of the currently selected person or team.
