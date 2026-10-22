# Document numbering

permitA38 numbers offers and invoices with one counter per document type and period. Numbers are assigned when a
document is **finalized** (not when it is drafted), in the same transaction that saves the document, so a failed
finalize does not use up a number.

Settings → Numbering (or the API below) shows the next number and lets you change it.

## Legal note

Invoice numbers must be unique and sequential (§ 14 UStG in Germany). Gaps have to be explained. permitA38 never
skips numbers on its own and does not let you delete a numbered invoice (cancel it instead; it keeps its number).
Before you change a number mid-year, talk to your tax advisor.

## Pattern

A pattern is literal text plus tokens:

| Token | Meaning | Example (2026, number 43) |
|---|---|---|
| `{year}` | four-digit year | `2026` |
| `{yy}` | two-digit year | `26` |
| `{month}` | two-digit month | `03` |
| `{n}` | the sequence number | `43` |
| `{n:05d}` | the number zero-padded to 5 digits | `00043` |

Examples:

- `RE-{year}-{n:04d}` gives `RE-2026-0043` (the default for invoices)
- `AN-{year}-{n:04d}` gives `AN-2026-0043` (the default for offers)
- `{yy}{month}-{n:03d}` gives `2603-043`
- `Rechnung {n}/{year}` gives `Rechnung 43/2026`

Rules: exactly one number token; literal text may contain letters (including umlauts), digits, spaces and
`- _ / . #`; at most 40 characters of text next to a token and 100 characters in total.

Patterns that do not sort chronologically produce a warning: an unpadded number (`{n}` instead of `{n:04d}`, since
10 sorts before 9) or a date after the number.

## Reset scope

| Scope | The counter restarts |
|---|---|
| `never` | never |
| `yearly` (default) | on 1 January |
| `monthly` | on the first day of each month |

The scope is taken from the document date. With `numbering_shared` the offers and invoices use one counter.

## Starting from another tool

Enter the last number your old tool used and permitA38 continues after it. Example: your old tool stopped at
`RE-2026-1042`; set the next number to `1043`. Imported documents also move the counter (see
[import](import.md)): after importing `RE-2026-1042` the next number is `RE-2026-1043`.

## API

All routes are for the boss only.

| Route | Purpose |
|---|---|
| `GET /api/numbering/preview?type=invoice` | the next five numbers, nothing is changed |
| `GET /api/numbering/status?type=invoice` | pattern, reset scope, next number, highest number in the period, warnings |
| `POST /api/numbering/check` | dry run of a change: warnings and the resulting preview |
| `PUT /api/numbering` | apply a change: `pattern`, `reset`, `next` (a number), `reason`, `confirm` |
| `GET /api/numbering/log?type=invoice` | audit trail of every change |

`PUT /api/numbering` returns **409** with the warnings when a change is risky and `confirm` is not `true`. Nothing
is written in that case.

Warning codes: `backwards` (the next number is not above the highest one used in the period), `gap` (numbers would
be skipped), `duplicate` (the number is already used), `pattern_change` (existing numbers keep their old format),
`reset_change`, `sorting` (see above).

## Counters and existing documents

The counter of a period is created on first use. If none exists, it is seeded from the existing documents: the
highest number that the current pattern produces in that period, or the number of documents in that period if no
number matches the pattern. Numbers in use are always skipped, so a finalize can never produce a duplicate.

## Concurrency

Allocation runs inside the finalize transaction. The SQLite backend serialises transactions, so two parallel
finalizes receive different numbers (covered by a test with 16 threads × 50 finalizes).
