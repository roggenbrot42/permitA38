# Planning: projects, assignments, contracts and vacation

This guide explains the Planning pages in plain language. The numbers come from the server; the pages only draw them.
Every figure has a tooltip that shows how it was computed.

The idea in one paragraph: **work happens in projects.** You give people (employees and subcontractors) hours on a
project: an **assignment**. Contracts say what you have promised for a project, offers say what you could still win.
The planner checks whether the assigned hours are enough for the hours still to do and tells you in a sentence if
they are not. A contract or an offer never names a person.

## Words used on the pages

| Word | Meaning |
|---|---|
| **Capacity** | the hours a person can really work in a week: weekly hours spread over the workdays, minus public holidays of your federal state, minus time off (vacation, sick, other) and minus calendar blocks that mean you are away (out of office, all-day blocks). Ordinary meetings do not reduce capacity. Never below zero |
| **Assignment** | hours of one person or subcontractor given to one project for a period, either **hours per week** or **total hours** |
| **Promised** | the hours a contract (or offer) promises |
| **Booked** | hours already tracked on the project up to and including today |
| **Remaining** | promised minus booked, never below zero |
| **Covered** | the part of the remaining hours that the assigned time can deliver |
| **Shortfall** | remaining minus covered: hours nobody is assigned to do |
| **Unassigned** | a person's capacity that is not assigned to any project (customer or internal) |

## Projects: customer or internal

A project is either a **customer project** or an **internal project** (choose the kind in the project form).

- A customer project belongs to a customer and may have offers, contracts and invoices.
- An **internal project** is work that is not for a customer: administration, the accountant, the secretary, your own
  admin time, an internal tool. It has no customer and no offers, contracts or invoices. You give it assignments like
  any other project, and they use up capacity. Time booked on an internal project is **never billable**: the
  billable box is unticked and locked, and the server stores it as not billable. An internal project can have an
  optional **budget in hours**; you get a warning when booked plus assigned future hours exceed it.
- Nothing is created for you: add an internal project such as "Administration" yourself if you want one.

Everyone can see and book time on every project. Which details (for example rates) a person sees depends on the role,
see [roles](roles.md).

## Assignments

Open **Planning → Overview** and press **Assignments (n)** on a project (or on a person). The dialog lists the
assignments of that project or person and lets you add, edit and delete them.

An assignment has:

- the **person or subcontractor** (people and active subcontractor suppliers);
- the **project**;
- the **period** (start and end);
- **hours per week** *or* **total hours** (exactly one of them);
- for a subcontractor the **rate** in € per hour (net). The dialog shows the planned hours and the planned net cost.

How the hours become days:

- **Hours per week** are spread evenly over the available days of each week: workdays that are not a public holiday
  and not a day off. A holiday or a vacation day moves the hours to the other days of that week. A week the
  assignment only partly covers gets its share. A week with no available day gives nothing (you get a warning).
- **Total hours** are spread evenly over the available days from tomorrow to the end of the assignment. Hours that are
  already booked on the project by that person inside the period are subtracted first. A total is a budget: whatever is not
  used in a week stays open for the following weeks and expires at the end of the assignment.
- Past days give nothing: what has already happened is in the booked hours.
- Subcontractors work the same way, with their own days off (their "not available" periods) and public holidays.

When you press **Add assignment** in the dialog of a project, the form proposes what is missing: the total hours of the
project's shortfall and a period from tomorrow to the end of the short contract or offer. Nothing missing means an
empty form.

An assignment that comes from an accepted incoming offer shows the offer. You can edit it (period, hours, rate) and
cancel it, but not delete it. If your edit makes the planned net differ from the offer's net, an info shows both
figures and the difference. Users without the planning write right see the number of assignments only.

**Over-assignment is warned, never scaled.** If a person's assignments of one day add up to more than that day's
capacity, every project still counts its full hours and the week shows the excess as *Over-assigned*. Example: Ada
Example works 20 h (4 h a day), is assigned 12 h a week to Administration and 10 h a week to Orchard: that is 4.4 h a
day against 4 h, so 2 h over in the week. The warning says so, with the projects involved. Subcontractors are never
checked, because their other customers are not known.

## Contracts and offers

Contracts and offers belong to a **project**, never to a person. A new contract needs a project. A contract has
either **hours per week** or **total hours**, a period and a rate.

For every contract and every open offer the Overview shows, per item:

| Column | Meaning |
|---|---|
| **Promised** | hours per week: the hours of every week of the period (a partial week counts pro rata over Monday to Friday); total: the total hours |
| **Booked** | hours already tracked on the project in the period, up to today |
| **Remaining** | promised minus booked |
| **Covered** | what the project's assigned time can deliver of the remaining hours |
| **Shortfall** | remaining minus covered |

Plus **value**, **invoiced** and **open** for a contract (value = promised hours times the rate; invoiced = what the
project's invoices bill in the period; open = value minus invoiced). For an offer the net and the expected net
(net times the win probability) are shown.

Rules behind the numbers:

- **Holidays and vacation do not reduce a promise.** They reduce the assigned time, and so they show up as a
  shortfall.
- **Booked hours are counted against the promise.** A booked hour is assigned to a contract before any offer, so
  adding an offer never changes the verdict of a contract.
- **Contracts are covered first, offers after them.** An offer is checked as if every open offer is won (worst case).
  A contract is covered first, weekly contracts before total contracts; total contracts whose period ends first take
  the available hours first. An offer only gets what is left.
- **Open offers** are offers that were sent, are not accepted yet and whose *valid until* date has not passed. Each
  offer can carry a win probability; without one the setting `planning_default_win_probability` (default 100 %)
  applies. Only offers are weighted by it, contracts never.
- **Unassigned time is only suggested.** A shortfall is never filled automatically from someone's free hours. Instead
  the item lists the people who have unassigned hours in its period, as buttons such as **Assign Ada Example (24 h)**.
  The button opens the assignment dialog with project, person, period and hours filled in.
- Contracts and offers **without a project** (old data) are checked against the team's unassigned hours. You get an
  info asking you to give them a project.

### The explanation sentence

Every item states its own arithmetic in one sentence, for example:

> 50 h promised − 0 h booked = 50 h remaining. Until 04.12.2026 Orchard has 24 h of assigned time left after its
> weekly contracts. Short 26 h.

A contract with a total ends with "Covered." when the assigned time is enough. A weekly contract says, for example,
"4 h per week from 09.11.2026 to 18.12.2026: covered every week." or names the weeks that are short and the hours
missing. If the current week is short, the sentence adds how many hours are booked this week and how much assigned
time is left after today. A contract or offer whose period has ended with hours not delivered says so.

### Walkthrough

Today is Sunday 08.11.2026 and Ada Example works 40 h a week. She has two assignments: Administration (an internal
project) 30 h a week, and the customer project **Orchard** 4 h a week, both from 09.11. to 18.12. The subcontractor
Sam Sample is assigned 24 h in total on Orchard from 09.11. to 04.12. at 90.00 € per hour. Orchard has two contracts at
120.00 € per hour: **K1** 4 h per week 09.11.–18.12. and **K2** 50 h in total 09.11.–04.12. Nothing is booked yet.

1. Ada gives Orchard 4 h a week, Sam 6 h a week (24 h over 4 weeks), together 10 h a week until 04.12. K1 takes its
   4 h a week, so 6 h a week or 24 h are left, then K2 takes those 24 h of its 50 h.
2. K1: shortfall 0. K2: remaining 50, covered 24, **shortfall 26 h**. The project shows **short**.
3. Ada has 6 h a week unassigned (40 − 30 − 4). The shortfall of K2 offers a button **Assign Ada Example (24 h)** for
   09.11.–04.12.
4. Money: Sam costs 24 h × 90.00 = 2,160.00 €; the contracts are worth 24 h × 120.00 + 50 h × 120.00 = 8,880.00 €
   (K1 promises 4 h a week for 6 weeks = 24 h); the margin of the project is 6,720.00 €.

If Ada books 10 h on Orchard in the first week, K1 takes 4 of them and K2 the other 6: K2's remaining falls to 44 h.

### Status of a project

- **ok**: nothing is short;
- **at risk**: contracts are covered, but one or more open offers would not be (worst case);
- **short**: a contract is short.

Internal projects show *ok* or *over budget*.

## The Overview tab

**Planning → Overview** (the page that was called Forecast). Filters: horizon (4, 12, 26 or 52 weeks), **Who**
(whole team or one person) and **Project** (all, one, or *Unassigned*).

1. **Projects**: a list with the customer projects first and a status next to each name. Open a project to see its
   contracts and offers with promised, booked, remaining, covered, shortfall, the money columns, the explanation
   sentence and the *Assign* buttons. Internal projects show assigned, booked and budget hours. Every project has an
   **Assignments (n)** button.
2. **People**: every person with an **Assignments (n)** button.
3. **Week by week**: capacity, hours on customer projects, internal hours, unassigned hours, over-assigned hours (red),
   the billable share (customer hours divided by capacity) and the subcontractors' hours (not part of capacity). A
   small grey line shows the holiday, vacation, absence and busy hours of the week. The table divides a person's time;
   whether your contracts fit is shown on the contracts, not by a colour of the week.
4. **Planning warnings**, the list of everything the planner noticed.

There is no weekly revenue forecast any more and no revenue that is "assumed" from free capacity: you see value,
invoiced and open per contract, and net and expected net per offer.

## Offer feasibility

When you save or accept an offer, the planner checks the offer against the hours assigned to its project. If they do
not cover it, you get a warning that states the arithmetic, for example hours missing in the offer's period.
Warnings never block saving.

## Numbers that do not add up

Offers and invoices are also checked for:

- hours in the lines versus the hours promised by the plan (`offer.hours_mismatch`);
- an hourly price below the minimum rate `planning_min_rate` (`rate.below_minimum`, `rate.effective_below_minimum`)
  or below the project/customer rate (`rate.below_reference`);
- invoices that bill more hours than were tracked (`invoice.hours_exceed_tracked`) or more than the offer covers
  (`invoice.exceeds_offer_hours`);
- projects over their hour budget (`project.over_budget`).

Each warning has the form `{code, severity, field, message, details, confirmable}`.

### Planner warnings

| Code | Meaning |
|---|---|
| `contract.shortfall` | a contract has hours nobody is assigned to do |
| `offer.shortfall` | an open offer would not be covered if you win it |
| `assignment.over_capacity` | a person is assigned more than their capacity on some day; the reason is time off, weekly hours or the working time law |
| `assignment.overlap` | (info) one person has two overlapping assignments to the same project |
| `assignment.no_demand` | (info) a subcontractor is assigned to a customer project that needs nothing in that period: paid but unused |
| `assignment.differs_from_offer` | (info) an edited assignment costs a different net than the incoming offer |
| `project.no_supply` | a customer project has work still to do and no assignment in the period |
| `internal.over_budget` | an internal project's booked plus assigned hours exceed its budget |
| `booked.unattributed` | (info) booked hours that no contract or offer of the project takes |
| `contract.no_project` | (info) a contract or offer without a project |

## Working time

The planner adds up each person's assigned hours per day over all projects (customer and internal) and warns. All of
this is a plausibility check and not legal advice (see [compliance](compliance.md)); nothing is ever refused.

- **Employees** (the working time law applies): more than 10 planned hours on a day, or more than 60 in a week, adds
  the reason "working time law" to the over-capacity warning. A planned average of more than 48 h a week over the 24
  weeks ending with a planned week gives `assignment.working_time`. Saving an employee with weekly hours above 48
  gives `user.weekly_hours_above_law`.
- **The boss** is not an employee and sets their own limits under **My settings → Work limits**: *Info above*
  (default 10 h a day), *Warning above* (12 h a day), *Maximum* (16 h a day), *Long week above* (60 h a week) and
  *Average above* (48 h a week over 24 weeks). More than the first gives an info, more than the second a warning, more
  than the maximum a warning that it is more than your maximum. The values may be 1 to 24 hours a day or 1 to 168
  hours a week, and the day limits must be in order. Nothing is refused, neither plans nor time bookings.
- **Subcontractors** are never checked.

## Subcontractors

Subcontractors (suppliers of kind person-capacity) are assigned to projects like people, with a rate. They are not
part of your team's capacity.

- Accepting an **incoming offer** of a subcontractor creates a **total** assignment: the hours are the sum of the
  offer's hour lines, the period is the offer's plan period, the rate is the net of the hour lines divided by their
  hours. You can edit the assignment afterwards.
- The **planned cost** of a subcontractor assignment is its hours (over the whole period, booked or not) times the
  rate. The margin of a project is the contract value minus this cost.
- Hours of a subcontractor only help the project they are assigned to. Their hours are spread over their own
  available days (not on public holidays or in their not-available periods).
- Archiving or deactivating a supplier with open assignments leaves the commitments (`GET /api/finance/commitments`)
  in place until you cancel the assignments.
- Expenses linked to an assignment are compared with its planned cost (`allocation.invoiced_exceeds_planned`).

## Project periods

Projects have a **start** and an **end**. Customer projects need both; internal projects may leave them empty. Existing
projects get their period from their earliest and latest contract or time entry when known; otherwise the planner
shows a "set the dates" hint.

- **Extend manually**: on the project page choose a new end date and a reason. The change is recorded in the
  extension history. A project can only be extended, not shortened this way.
- **Extend by offer**: accepting an offer that belongs to a project widens the project's period to cover the offer's
  plan; the extension is recorded with the offer's number.
- Time entries and contracts outside the period produce a soft warning.

## Project time sheet

The project page has a **Time sheet** card: who booked how many hours on this project, per week or per month
(switch and date range in the card), with the total, the billable hours and the hours not invoiced yet. A name opens
that person's week in the time view. **CSV** downloads the same table. The boss sees every person; a user whose time
access is limited sees only their own row. Rates and money are not shown here.

## The Today page

The **Today** page has a card **Your projects this week**. For every project you are assigned to this week (and every
project you booked on this week) it shows today's planned and booked hours, the week's planned and booked hours, and
the hours **left this week**, with a total row. Planned hours come from your assignments, including the days of the
week that are already past. Without assignments the card says "No assignments this week".

- **▶ buttons**: every project row, and below it every task you booked on this week, starts the timer on that project
  and task. A running timer is stopped and saved first, so switching never loses time. Every row of the **Today**
  entry list has the same ▶ button, so you can carry on with earlier work in one click.
- **Switching while the timer runs**: the timer card shows project and task selects while it runs; a change applies
  at once and the start time stays.
- **Stopping under a minute**: if you press Stop less than a minute after the start, you are asked "Less than a minute
  tracked. Delete it instead of booking 1 min?". OK discards the timer (no entry), Cancel books one minute.
- **Manual mode**: in manual mode a duration alone is enough. For today the entry ends now and starts that long ago
  (not before 00:00); for another day it starts at 09:00. Start and end stay editable.
- **Manual while the timer runs**: press ✎ Manual and the form is pre-filled from the timer (date, start, stop now,
  duration, project, task, description). The timer keeps running until you press Add; Add saves the entry and
  discards the timer so the time is booked once. Switching back to the clock without Add shows the running timer.
- **Internal projects are never billable**: choosing one in the time entry form unticks and locks "billable".

## Plan & vacation (what-if)

Open **Planning → Plan & vacation**. The timeline shows, per week and per day: capacity, assigned hours, public
holidays, time off, every contract, every open offer and the assignments.

- **Vacation**: drag on the *Time off* row across the days you want. Drag a bar to move it, drag its edges to change
  its start or end. Press Delete (or Backspace) on a time-off bar to remove it.
- **Contracts and offers**: drag to shift them, drag the edges to change their duration, and use the hours field to
  change hours per week (or total hours). A bar turns red when the item has a shortfall in the draft; its tooltip
  shows the item's explanation sentence.
- **Assignments**: draft changes to assignments are possible too (new, changed hours or period, deleted).
- **Draft**: every change is a *draft*. Nothing is written until you press **Apply** and confirm. **Discard** drops
  the draft. The draft bar shows what changes per project, for example "Orchard: short 26 h → 2 h".
- **Scenarios**: save the draft under a name, load it later, or compare a scenario with the current base. Scenarios
  are yours only.
- **Suggestions**:
  - *Find a time*: the best windows for N working days of vacation. The windows that add the least shortfall win,
    then those that gain the most free calendar days around them (weekends and holidays).
  - *Earliest start for an offer*: the first start day from which N hours fit before a deadline in the chosen project.
  - *What must move?*: for a selected offer, the smallest move of another open offer that makes it fit, or the largest
    amount that fits.

**Keyboard**: click a bar (it is outlined and focused) or Tab to it, then ←/→ moves it one day and
PageUp/PageDown one week; Shift+←/→ changes its end, Ctrl+←/→ its start (Alt is not used: browsers take Alt+←/→ for
Back/Forward). Esc discards the change of that one item, Delete or Backspace removes a time-off entry (a real entry is
only marked for deletion until you press Apply). Every draft change is also listed under the draft bar with its own
**Discard** button. Forms for vacation and the suggestions work without the mouse. **Touch**: dragging works with a
finger on the bars and the time-off row.

## Vacation entitlement

Each person can have `vacation days per year` and `carried over` days (set them in the planner header). The planner
counts **working days** of vacation only: weekends and public holidays are not counted, sick days are not vacation.
It shows used, planned and remaining days for the year, and warns when the planned days exceed the entitlement.

## Settings

| Key | Default | Used for |
|---|---|---|
| `planning_min_rate` | 0 (off) | minimum hourly rate warnings |
| `planning_default_win_probability` | 100 | win probability of offers without one |

The boss's work limits are personal and live under **My settings**, see Working time.

## After upgrading

The planner changed from the earlier "allocate capacity to a project" model. What happens to your data:

- Contracts and offers keep their old person list in the database, but it is ignored and no longer shown or written.
- Existing assignments of subcontractors keep their hours per week. Edit them if you want a total.
- The project field "Team" (who may see a project) is gone: everyone sees every project.
- "Personal" projects became internal projects (or customer projects where a customer was set). Time entries on
  internal projects are no longer billable.
- The "Allocate my capacity" button is gone. Open **Assignments** on a project to add what is missing; the shortfall
  buttons propose the hours.
- Planned cost of a subcontractor assignment can change where a public holiday falls into its period, because their
  weekly hours now move to the other days of the week like an employee's.

## Limits

- The planner recomputes on the server for every change of a draft (a short delay per change). The browser shows what
  the server computes.
- Calendar blocks that count as away (out of office, all-day) reduce capacity; ordinary busy events do not.
- Suggestions consider one project at a time. Contracts are never moved by suggestions; only open offers are.
- Scenario comparison uses the base of the currently selected person or team.
