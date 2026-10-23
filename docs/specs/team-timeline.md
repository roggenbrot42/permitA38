# Specification: Team timeline (who is away, who works on what)

Status: **proposed, not scheduled**. Decided during the 0.4.0 feature freeze, so it is implemented in the release
after it. Depends on the roles work (`feat/rbac-v2`, `permita38/core/rights.py`) and on the planner
(`web/planner.js`). Author: architect (Sonnet 5.5). The implementer follows this document and lists every choice it had
to make under "Open design decisions" in its report.

## Goal

A **Team** tab in *Planning → Plan & vacation* with a read-only timeline chart: one row per person, days on the
horizontal axis, bars for time off and for contracts. It answers "who is away when" and "who is booked on what" at a
glance. Who may see which details is a **setting the boss configures**, not hard-coded.

Non-goals: dependency arrows between tasks (not a project Gantt chart), editing from the team view, a project-per-row
view (possible later), mobile-specific layout beyond horizontal scrolling.

## Visibility is a configurable right

Today `rights.py` is a fixed table and `planning` is boss only. The team timeline needs a level between "nothing"
and "everything", set per role by the boss under *Settings → Team visibility*:

| Level | A person with this level sees |
|---|---|
| `off` | no Team tab (API answers `403`). |
| `away` | who is away and when, **without the reason**: the bar says "away". Contracts are not shown. |
| `time-off` | as `away`, plus the kind (vacation / sick / other) and the label of vacation entries; sick days still show only as "away". |
| `full` | `time-off` plus every person's sick-day kind and the contract bars (title, customer, hours per week). Boss only by default. |

* A new setting `team_timeline_visibility`: `{ "employee": "away", "accountant": "off" }` (roles other than `boss`;
  the boss always has `full`). Defaults: employee `away`, accountant `off`. Stored in the settings row (extra JSON, no
  schema change); validated with a strict `*In` model: keys are role names, values are the four levels.
* The setting is edited with `settings` write access (boss) and shown in the roles documentation page.
* **Redaction happens on the server.** The API never sends details the level does not allow, so a hidden field cannot
  be recovered from the response or the browser developer tools. Anything the viewer may not see is removed, not
  masked.
* A person always sees their own entries in full regardless of the level (`absences` limited scope).
* The permission matrix in `docs/roles.md` gets a new area `team_timeline` documenting the levels and the default per
  role, and the existing test that checks the doc against `rights.py` covers it. The matrix test that calls every
  route as every role gets the new endpoint.

## API

`GET /api/team/timeline?start=YYYY-MM-DD&end=YYYY-MM-DD` (at most 366 days, otherwise 422; dates within 2000–2100).
Requires level `away` or higher. Response:

```json
{
  "level": "away",
  "start": "2026-11-02", "end": "2026-12-27",
  "holidays": {"2026-12-25": "1. Weihnachtstag"},
  "people": [
    {"id": "…", "name": "…", "color": "#…",
     "absences": [{"start": "2026-11-09", "end": "2026-11-13", "kind": null, "label": null}],
     "contracts": [],
     "weekly_hours": 40}
  ]
}
```

* `kind` and `label` are `null` below `time-off`. Sick days always have `kind: null` and `label: null` below `full`.
* `contracts` is empty below `full`; each entry has `id`, `title`, `customer`, `start`, `end`, `hours_per_week`.
* Inactive users are excluded. The list is sorted by name; the viewer is first.
* The state (federal state) of the business determines `holidays`, as in the rest of the planner.
* A strict query model bounds the window; a malformed range is a `ServiceError` (422), never a 500.
* The service lives in `core/service/team_timeline.py` and reads through the `Repository` interface only. The router
  stays thin. `api/openapi.json` is regenerated.

## UI

* A **Team** tab next to *Plan* in the planner (shown only if the API grants it). It reuses the planner's day grid,
  the week/zoom controls and the holiday shading; it does not reuse the draft machinery (nothing is editable here).
* Rows: one per person, label column on the left (name, small colour chip). Time-off bars in the vacation colour,
  `away` bars in a neutral hatched style so a viewer without details cannot mistake them for vacation; contract bars
  (level `full`) in the contract colour, drawn in a second sub-row so they do not hide time off.
* A **today line**, weekends and public holidays shaded, a weekly header with calendar week numbers.
* A clash marker: days on which *more than N people are away* (N configurable in the view, default 2, a plain number
  input, not stored on the server) get a coloured stripe in the header.
* Click or focus on a bar shows its details in a text panel under the chart (no tooltip-only information); the chart is
  keyboard-reachable (Tab through bars, the same selection outline as the planner) and every bar has an
  `aria-label` such as "Alex Example, away, 9 to 13 November".
* Own entries link to the existing planner editing ("edit in Plan") when the viewer may edit them.
* All user-supplied text is escaped; colours come from the `color` field validated server-side.
* Empty state: "Nobody has time off in this period." Error state: shows the message of the 403/422.

## Tests

* Backend, per level: exact JSON for a fixed fixture (three people, vacation, sick, other, two contracts, a holiday);
  assert that at `away` no `kind`, `label`, contract or sick detail appears anywhere in the raw response text (search
  for the secret strings, not only fields); own entries always full; inactive users excluded; window bounds and
  malformed ranges give 422; unauthenticated 401; level `off` gives 403 and the setting validation rejects unknown
  roles and levels; changing the setting changes the response immediately; matrix/doc test updated.
* Logic: a hand-computed set of clash days for the fixture (independent oracle written in the test, not copied from the
  code).
* Web: the tab appears per level (boss, employee `away`, accountant `off`); bars land on the right days for the fixture
  (compare `aria-label`s); redaction visible in the UI; no console errors; keyboard reachability; the existing planner
  tests stay green.
* The seeded random monkey visits the tab once.

## Open decisions for the maintainer

* Defaults: employee `away`, accountant `off` are proposals.
* Should the clash threshold be a stored setting (shared by everyone) instead of a per-viewer input?
* Add a project-per-row view later (same grid), or a "mine" filter first?
