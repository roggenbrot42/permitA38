# Spec: time tracking element (Today page)

Target: v0.4.0-alpha.5 (see `docs/roadmap.md`).
Merge tier: Light (frontend only, no money/rights/storage change; money display uses `web/money.js` only).

## Where
The timer card at the top of the Today page in `web/app.js` (the `<div class="card"><div class="timer">` block
rendered by the Today page, handlers `timerProj`, `timerStart`, `timerStop`). Replace the timer card's content with
one horizontal row. The "Getting started" and "This week" cards stay as they are.

## One row, left to right
1. **Description**: text input, placeholder "What are you working on?", maps to `note`.
2. **Project**: searchable dropdown (a text input with a filtered list of non-archived projects, same data as
   `projectOptions(false)`), then **Task**: a select filled from `taskOptions(projectId)`, as today.
3. **Mode area**: depends on the mode (below).
4. **Mode icons**: two small icon buttons, "Manual" (pencil) and "Time tracker" (clock). The selected one is marked
   (`aria-pressed="true"`). Mode is kept per device in `localStorage` (`timeMode`, `manual` | `tracker`); default `manual`.
   Switching mode does not lose the description, project or task.

## Manual mode (default)
- **Start** time input (`type="time"`), **Stop** time input, **Date** input (default today).
- **Duration** field: an editable `hh:mm` field showing stop minus start. Editing it recomputes **Stop** from
  **Start** (Start is kept). Editing Start or Stop recomputes Duration. Invalid or zero/negative durations are
  rejected with a message, not saved.
- **Add** button, colored (`class="primary"`). It saves a finished entry (date, start, stop, project, task,
  description) through the existing entry save path (the same call `entryForm` makes for `entries`). Afterwards clear
  Description and reload `entries`.

## Time tracker mode
- The **time field** is non-editable and shows the running clock (`clockStr(timerElapsed())`), or `0:00` when idle.
- **Start** button, colored (`class="primary big"`) when idle; while running it becomes **Stop** (`class="stop big"`),
  using the existing `timer/start` and `timer/stop` calls. The clock tick (`render.after`) already exists; keep it.
- **Money toggle**: clicking the time field while a project is selected switches it to show the money earned
  (elapsed hours × the hourly rate, formatted with `Money` / `eur` helpers). A second click reverts to time.
  The rate is the effective rate of the project (the same chain as `suggestedRate` in `web/app2.js`: project, customer,
  user rate, user default, settings default; task rates are not part of that chain). If that chain is empty, the field
  shows time and a small hint "no rate", and the click does nothing.
  Use the existing rate fields (no backend change). Money maths only via `web/money.js`, never floats.
  The money view is per-view state (not stored).

## Constraints
- Vanilla JS, no build step, no new libraries. Reuse existing classes (`card`, `primary`, `stop`, `pill`).
  Escape all user text with `esc`. Labels for all inputs; icon buttons have `aria-label`.
- Layout works at phone width (one row wraps to several rows; no horizontal page scroll).
- Do not change the backend, `api/openapi.json` or any router.
- Keep the existing `timerStart` / `timerStop` behaviour working for running timers started elsewhere.

## Open design decisions (implementer lists its choices here in the report)
- Exact icon glyphs (inline SVG or emoji).
- Project rate: the implementation uses the effective rate chain of `suggestedRate` (project, customer, user, settings default),
  not only the project's own rate; the hint shows only when that chain is empty. A task rate is not part of the chain (open).
- Bare-name project matching (`projIdOf` accepts a typed bare name) is kept as is.

## Tests
- `node --check web/app.js` must pass.
- If a Playwright test for the Today page exists under `tests/web/`, run only that file. Otherwise add none.
