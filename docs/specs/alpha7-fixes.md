# Spec: 0.4.0-alpha.7 fix release

Milestone `0.4.0-alpha.7` (fixes for bugs reported against 0.4.0-alpha.6). Integration branch: `fix/0.4.0-alpha.7`.
Each work package (WP) is built on its own branch cut from `origin/fix/0.4.0-alpha.7` and merged back by the architect.
Not in this release: #106, #107 (features) and #108 (post-review cleanups with an open decision).

General rules: vanilla JS, no build step, no new libraries; escape all user text with `esc`; labels on every input;
no backend change unless the WP says so; no change to `api/openapi.json`. Fake demo data only.

## WP1 – Business trip bills (#109, #110) – tier Light

Files: `web/trips.js`, `web/app2.js`, `tests/web/test_trips_ui.py` (or a new `tests/web/test_trip_bills_ui.py`).

### #109 Wrong trip preselected in the bill dialog
1. First write a failing Playwright test of the reporter's steps: create two trips (via the API), open trip #2 on its
   expenses step, click **Add bill** on a planned-item row and, separately, **Add bill** on an open checklist item.
   Assert `#f_trip_id` holds trip #2's id in both cases. Find the root cause (e.g. which `trip` object reaches
   `checkItemHTML`, whether `S.d.trips` holds the trip, the order of `tripOptions`, a handler that rewrites the select)
   and fix it. Name the root cause in the report.
2. When the bill dialog is opened **from a trip page** (the button carries `data-trip`; new and existing bills), the
   **Business trip** select is shown **disabled**, preset to that trip (title attribute "Opened from this trip; move
   the bill to another trip on the Expenses page"). The planned-item select stays enabled and lists that trip's items.
   Saving keeps `trip_id` = that trip. Opened from the Expenses page the select stays free as today.
   (`readForm` reads `.value` of a disabled select, so the save path is unchanged; check it.)

### #110 A bill booked against a planned item cannot be opened again
- `itemRowHTML` lists linked bills only as amounts and receipt pills. Add one **Edit bill** button per linked bill
  (`data-a="editExpense" data-id="<bill id>" data-trip="<trip id>"`, `aria-label="Edit bill <vendor or date>"`),
  e.g. in the Receipt or Actions cell, only when `canBill`. `data-trip` keeps the existing wrapper in `trips.js` that
  re-renders the trip after the dialog closes.
- Test: a bill on a planned item → its Edit bill button opens "Edit bill" with that bill's values; change the vendor,
  save, the trip page shows the change.

## WP2 – Today time card: manual entry consistency and one-row layout (#121, #122) – tier Light

Files: `web/app.js`, `web/style.css`, `tests/web/test_time_card_ui.py`, `tests/web/test_add_time_ui.py`,
`tests/web/e2e_random/test_user_journeys_random.py`, `tests/web/e2e_random/test_monkey.py` (the last two only where
they use the changed fields).

### #121 Start, stop and duration always agree; overnight work has its own stop date
Both manual surfaces use **one shared helper** in `web/app.js`: the Today card's manual mode (`timerModeHtml`,
`A.timerTimes`, `A.timerDur`, `A.timerAdd`) and the "Add/Edit time" dialog (`entryForm`).

Fields: **Start date**, **Start** (time), **Stop date**, **Stop** (time), **Duration** (`1:30`, `1.5`, `90m`, as `parseDur`).
Today card ids: keep `t_date` (start date), `t_start`, `t_end`, `t_dur`; add `t_end_date`. Dialog: keep `f_date`,
`f_start`, `f_end`, `f_dur`; add `f_end_date`. Labels: "Date"/"Start"/"Stop date"/"Stop"/"Duration" on the card,
"Date"/"From"/"To date"/"To"/"Duration" in the dialog.

Rules (a "point" is a date plus a time; minutes between points are computed with integer day numbers from
`Date.UTC(y, m-1, d)` / 86400000, never local `Date` arithmetic, so DST does not shift anything):
1. **Edit start date**: if a stop date is set, the stop date moves by the same number of days (duration kept);
   if it is empty it becomes the start date.
2. **Edit start time, stop time or stop date**: if both points are complete, Duration = stop − start.
   If the result is ≤ 0 or > 24:00, Duration is cleared and the field is marked invalid (`aria-invalid="true"`,
   message under the row or a toast "Stop must be after start" / "An entry can be at most 24 hours").
3. **Edit start time while the stop time is empty** and Duration is valid: stop = start + duration (stop date
   follows). **Edit stop time while the start time is empty** and Duration is valid: start = stop − duration
   (start date follows; it must not become later than today).
4. **Edit duration**: if the start time is set, stop = start + duration (the stop date becomes the next day when it
   crosses midnight). Else if the stop time is set, start = stop − duration. Else nothing is filled in (the old
   "today ends now / other days start at 09:00" guess is removed).
5. The stop date is limited to the start date or the next day (`min`/`max` on the input); duration 1 min … 24:00.

Examples (literal; use them in the tests):
| start | stop | duration typed | result |
|---|---|---|---|
| 2026-11-20 22:00 | – | 8:00 | stop 2026-11-21 06:00 |
| 2026-11-20 09:00 | 2026-11-20 12:30 | – | duration 3:30 |
| – | 2026-11-20 17:00 | 1:30 | start 2026-11-20 15:30 |
| 2026-11-20 23:30 | 2026-11-21 00:15 | – | duration 0:45 |
| 2026-11-20 10:00 | 2026-11-20 09:00 | – | invalid, Add refused |
| 2026-11-20 08:00, then start date set to 2026-11-19 | 2026-11-20 10:00 | – | stop date 2026-11-19, duration stays 2:00 |

Saving (both surfaces) stores what the server already understands (no model change): `date` = start date,
`start`/`end` = the two times, `minutes` = duration. A stop on the next day is stored as `end` < `start` (the night
shift convention of `sanity_rules._span` and `docs/sanity-checks.md`). Duration only (no times) stays allowed:
`start`/`end` = null. When start and stop are both set, `minutes` is always stop − start (never a different typed
duration). Editing a stored entry derives the stop date: start date + 1 when `end` ≤ `start`, else the start date.
The existing future-date refusal of `entryForm` applies to the start date on both surfaces.
A running timer's prefill (`timerPrefill`): start = the timer start, stop = now with its own date; if that is more
than 24 h, stop = start date 23:59 as before.

### #122 The time card stays one row on desktop
`main` is at most 1180 px wide, so the card row has about 1120 px at any screen resolution; the jump comes from the
manual fields being wider than the tracker fields, so the row wraps and the mode buttons move to a second row.
- From a viewport width of 1100 px up, the timer card is **one row that never wraps** in either mode: `.timer`
  `flex-wrap: nowrap`, every child `min-width: 0`, Description `flex: 1 1 0` (it absorbs the difference), the other
  fields explicit compact widths (date and time inputs sized for Firefox's intrinsic widths, e.g. date ≈ 8.5em,
  time ≈ 6em, duration ≈ 5em). The ✎/⏱ buttons stay the last items of the same row (`margin-left: auto`).
  Labels may be shortened. Below 1100 px the row wraps as today (phone width: no horizontal page scroll).
- Test (Playwright, viewport 1400×900 and 1100×800): in manual and tracker mode, idle and while a timer runs, before
  and after each mode click, the `y` of the mode buttons' bounding box equals that of `#t_note` (±2 px) and the card's
  height does not change between modes (±2 px). The PR notes that Firefox needs a manual check.

## WP3 – Nightly: backend (#102) – tier Full (billing rule in `records.py`)

Files: `permita38/core/service/records.py`, `tests/backend/test_internal_not_billable.py`,
`tests/backend/randomized/test_api_stateful.py`, `tests/web/e2e_random/test_api_random.py` (only if it writes entries).

1. **Server bug (confirmed by the architect):** `PUT /api/entries/{id}` with `project_id: null`, `billable: true`
   on an entry that was on an **internal** project answers 200 with `project_id: null` but `billable: false`. The
   internal-project rule must look at the project the entry **has after the write**, resolved exactly like the store
   resolves it (a PUT replaces the row). Rule: `billable` is forced to `false` iff that project exists and is internal;
   otherwise the request value (default `true`) is kept. Regression test in `test_internal_not_billable.py`:
   internal → null with `billable: true` gives `true`; internal → customer project with `billable: true` gives `true`;
   customer → internal gives `false`; for boss and member.
2. **Model gap:** the server refuses (422 "That would be … hours on <date>; a day has only 24") when one person's
   entries on one date would exceed 1440 minutes (`_check_day_total`, counted on the entry's `date`, the entry being
   written replaces its old version). The stateful model (`test_api_stateful.py`, including `seed_world`, create and
   update of entries and the timer stop if it books an entry) must predict that refusal. Check whether
   `tests/web/e2e_random/test_api_random.py` writes entries and needs the same rule.
3. Do not weaken assertions; the model follows the server's documented behaviour.

## WP4 – Nightly: browser journeys (#102) – tier Light (tests), Standard if app code changes

Files: `tests/web/e2e_random/test_user_journeys_random.py`, `tests/web/e2e_random/helpers.py`,
`tests/web/e2e_random/test_monkey.py`, `tests/web/e2e_random/test_regressions.py`, plus at most two app files if a
replay shows an app bug.

Failing seeds from the last nightlies (replay each with `PERMITA38_E2E_SEED=<n> pytest tests/web/e2e_random -k journey -p no:cacheprovider`):
- **122196, 240667** – `a_allocate` asserts `12,5 h/week`, the row shows `h in total`. Cause: after an accepted offer
  the project has a contract and **+ New** opens prefilled with the proposed missing hours in "in total" mode
  (`web/allocations.js`); the test fills `#al_hours` but never sets `#al_mode`. Fix the test: select `week`
  explicitly. Grep the monkey and `test_regressions.py` for the same pattern.
- **191602, 733301** – `Page.click` timeout on the **Sign out** nav link ("element is outside of the viewport").
- **156458** – `wait_for_selector("main:not([data-old])")` timeout after deleting an entry.
- **358810, 648768, 86027** – click / wait timeouts ("waiting for element to be visible, enabled and stable").
For each: replay, classify (test bug vs app bug), fix. A test fix must keep the check meaningful (e.g. scroll the nav
link into view, wait for the right state), never just raise a timeout or skip the step. An app bug is fixed in the
app with a regression test; report it. If a seed does not reproduce locally, say so and do not guess a fix.

## Merge
The architect merges WP branches into `fix/0.4.0-alpha.7`, runs the reviews per tier, runs the randomized suites
once (`make test-summary SUITES="random"`) and a `workflow_dispatch` of the nightly on the branch, and opens a draft
PR "fix: 0.4.0-alpha.7" (Fixes #109 #110 #121 #122, Refs #102 until a nightly is green). No version bump here.
