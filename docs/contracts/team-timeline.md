# API contract: team timeline

Source of truth for the backend and the web UI. Behaviour: `docs/specs/team-timeline.md`. Where this file and the spec
differ on the HTTP shape, this file wins. Error format, auth and date conventions are those of `docs/contracts/trips.md`
("Conventions"): `{"detail": "<message>"}` for 401/403/404/409 and service-level 422; body/query validation errors are
`{"detail": [{"loc": ["query","end"], "msg": "...", "type": "..."}]}`.

## Rights

New area `team_timeline` in `core/rights.py` (`AREAS["team_timeline"] = "Team timeline (who is away, who works on
what)"`). The matrix only decides whether a role may call the route at all; the **visibility level** (below) decides
what it gets.

| Role | read | write | meaning |
|---|---|---|---|
| boss | all | none | always level `full` |
| employee | limited | none | level from the setting, default `away` |
| accountant | limited | none | level from the setting, default `off` |

| Route | area, op | scope rule |
|---|---|---|
| `GET /api/team/timeline` | team_timeline, read | `Needs("team_timeline")`; then the service resolves the level: `off` -> 403 `"Forbidden"` |
| `PUT /api/settings` (field `team_timeline_visibility`) | settings, write | boss only, as all settings |
| `GET /api/settings` (field `team_timeline_visibility`) | settings, read | boss: the stored map with defaults filled in; other roles: field omitted |

There is no write route for the timeline itself (read-only chart).

## Visibility setting

Field `team_timeline_visibility` of the settings (stored in the settings row's extra JSON, no schema change).
Strict model in `SettingsIn`:

```json
{"team_timeline_visibility": {"employee": "away", "accountant": "off"}}
```

* Keys: exactly the role names `employee` and `accountant` (any other key, including `boss`, -> 422); both optional in
  a PUT; a missing key means the default.
* Values: `"off"`, `"away"`, `"time-off"`, `"full"`.
* Defaults when never set: `employee: "away"`, `accountant: "off"`. The boss always has `full` and cannot be
  configured. The response of `GET /api/settings` for the boss always contains both keys (defaults filled in).
* Changing it takes effect on the next timeline request (no caching).

### Levels

| Level | `absences[].kind` | `absences[].label` | sick days | `contracts` |
|---|---|---|---|---|
| `off` | route answers 403 | | | |
| `away` | `null` | `null` | listed as away (`kind: null`) | `[]` |
| `time-off` | `"vacation"` / `"holiday"` / `"other"` (an allow-list: any other stored kind, legacy or unknown, is `null`) | the entry's `note` (vacation and holiday entries only, else `null`) | listed as away, `kind: null`, `label: null` | `[]` |
| `full` | all kinds incl. `"sick"` | the entry's `note` (every kind) | `kind: "sick"` | all contracts of the person |

**The viewer's own entries are always returned as at `full`** for absences (the `absences` limited scope), at any
level above `off`. The viewer's own contracts are NOT shown below `full` (contracts are not part of the `absences`
right). Redaction is by removal on the server: a hidden value is `null`, never a masked string, and no other field
(ids, notes, user ids of entries, counts) leaks it. Absences carry no `id` for that reason.

## `GET /api/team/timeline`

Query parameters (strict model; unknown parameters 422):

| Param | Type / bounds | Required |
|---|---|---|
| `start` | date `YYYY-MM-DD`, year 2000-2100 | yes |
| `end` | date `YYYY-MM-DD`, year 2000-2100, `end >= start`, `end - start <= 365` days (at most 366 days inclusive) | yes |

Errors: 401; 403 (`"Forbidden"`: role has no access, or level `off`); 422 (missing, malformed or out-of-range
date, `end < start`, window larger than 366 days: for the last two `{"detail": "..."}` strings, for the others the
list form; never 500).

Order of the checks: authentication (401), then the role and the level (403, also for a viewer whose level is `off`),
then the window (422 for `end < start` and a span above 365 days). Query-shape errors (malformed, out of range, unknown
parameter) are answered before the role check, as the list form, and never echo the input.

Example, request `GET /api/team/timeline?start=2026-11-02&end=2026-12-27`, level `full` (boss):

```json
{
  "level": "full",
  "start": "2026-11-02",
  "end": "2026-12-27",
  "holidays": {"2026-12-25": "1. Weihnachtstag", "2026-12-26": "2. Weihnachtstag"},
  "people": [
    {
      "id": "u1a2b3c4d5",
      "name": "Boss Example",
      "color": "#3366cc",
      "weekly_hours": 40,
      "absences": [],
      "contracts": [
        {"id": "c5d6e7f8g9", "title": "Website relaunch", "customer": "Example GmbH",
         "start": "2026-10-01", "end": "2026-12-31", "hours_per_week": 20}
      ]
    },
    {
      "id": "u9z8y7x6w5",
      "name": "Alex Example",
      "color": "#cc6633",
      "weekly_hours": 32,
      "absences": [
        {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
        {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": null}
      ],
      "contracts": []
    }
  ]
}
```

Same request at level `away` (employee): `"level": "away"`, every absence has `"kind": null, "label": null`, every
`contracts` is `[]`; the viewer's own absences are still full.

### Fields (all response-only)

| Field | Type | Notes |
|---|---|---|
| `level` | `"away"` \| `"time-off"` \| `"full"` | the viewer's effective level (never `off`; that is a 403) |
| `start`, `end` | date | echo of the query |
| `holidays` | object date -> name | public holidays in the window for the business's federal state (`settings.state`), same source as the planner; `{}` if none or no state |
| `people` | array | active users only (`active` not false); sorted by name (case-insensitive, then id); **the viewer is first** |
| `people[].id` | id | |
| `people[].name` | string | |
| `people[].color` | `#rrggbb` or `null` | the stored, validated user colour; omitted/`null` if none (the UI picks a neutral) |
| `people[].weekly_hours` | number 0-168 | the user's weekly hours; `null` if unset, and `null` below `full` except on the viewer's own row |
| `people[].absences[]` | `{start, end, kind, label}` | dates `YYYY-MM-DD`, `end` always set (`end = start` for one-day entries), clipped to nothing: entries are returned whole if they overlap the window; sorted by `start`; entries of the same person are not merged |
| `people[].contracts[]` | `{id, title, customer, start, end, hours_per_week}` | only `full`; contracts that overlap the window and list this person in `user_ids`, of every status (`confirmed` and `done`); `customer` is the customer's name or `null`; `end` may be `null` for open-ended; `hours_per_week` number or `null` |

`kind` values: `"vacation"`, `"holiday"`, `"sick"`, `"other"` (the `Absence.kind` values), or `null` when hidden.

## Clash threshold (client-side, no API)

The threshold is a per-viewer number in the UI (default `2`, integer 1-999, not stored on the server). The server sends
no clash data; the UI derives it from `people[].absences` of the response:

```
away(day)   = number of people with an absence entry whose [start, end] contains day
clash(day)  = day is a weekday (Mon-Fri), is not a key of `holidays`, and away(day) > N
```

"More than N" is strict: with `N = 2`, three people away is a clash. At level `away` the count includes sick days
and every kind (they are all listed). The marker is a header stripe plus a text count; the browser tests compute it
with an independent oracle from the fixture.

The backend does not compute clashes and has no clash tests of its own: `tests/backend/test_team_timeline.py` only
checks the fixture's hand-computed clash days against a local oracle, as data for the UI tests.

## Contract decisions (where the spec left a choice)

1. Area `team_timeline` is a real area in the matrix (boss read=all, employee/accountant read=limited, write=none for
   all); the setting does the real gating. A matrix `limited` here means "subject to the visibility setting".
2. `weekly_hours` is shown only at `full` and for the viewer's own row (the spec lists it without a level; hours per
   week are a contract-like detail).
3. Absences carry no `id` and are not clipped to the window; one entry per stored absence.
4. Sick days at `time-off` appear as plain away (`kind` and `label` null); at `away` the same.
5. Own absences are full at every level above `off`; own contracts are not (not part of the absences right).
6. Window limit: `end - start <= 365` days; a violation is a plain-string 422, a malformed date the list-form 422.
7. Setting keys are only `employee` and `accountant`; `boss` is rejected, not ignored.
8. Weekends and holidays never count as clash days.
9. A user without a role-recognised value (legacy `member`) is treated as `employee`.
