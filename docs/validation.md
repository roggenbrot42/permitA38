# Money rounding and input validation

## How amounts are rounded

All money is computed with exact decimals (`permita38/core/money.py`, mirrored in `web/money.js`), never with binary
floating point:

1. every document line amount = `quantity × unit price`, rounded **half-up to whole cents** (ties away from zero:
   `0.005 → 0.01`, `-0.005 → -0.01`);
2. the **net total** is the exact sum of the line amounts;
3. **VAT is computed per VAT rate** on the *sum of the net amounts of that rate* and rounded half-up – one VAT amount
   per rate, which is what a §14 UStG document shows – **not** per line;
4. the **gross total** is net plus the sum of the per-rate VAT amounts;
5. Kleinunternehmer (§19 UStG): no VAT at all;
6. quantities derived from tracked time ("invoice from time") are `minutes / 60` rounded half-up to 2 decimals.

Example: 19 % of 621.50 is 118.085 → **118.09** (binary floats gave 118.08).

The backend and the browser use the same rule; `tests/web/test_money_js.py` checks 400 random documents cent for cent.

## Input validation

Request bodies are validated by strict models (`permita38/core/models.py` `*In`, building blocks in
`permita38/core/validation.py`). A violation is answered with **HTTP 422** and `{"detail": [{"loc", "msg", "type"}]}`;
the offending input is never echoed back. Values are stored exactly as typed (no silent trimming). Unknown fields are
kept for forward compatibility, but every value in the body is checked (valid Unicode, finite numbers, 64-bit range,
nesting depth).

| Field kind | Rule |
|---|---|
| Dates | `YYYY-MM-DD`, a real calendar date, 2000-01-01 … 2100-12-31; empty string means "not set" |
| Periods | contract, absence, plan and service period: end must not be before start |
| Times | `HH:MM` (00:00–23:59) |
| Minutes (time entry) | integer 1 … 1440; a person's entries on one day must sum to ≤ 24 h |
| Amounts, prices | finite, \|value\| ≤ 1 000 000 000 (credit-note lines may be negative) |
| Quantities | finite, \|value\| ≤ 10 000 000 |
| Expense net / VAT | 0 … 1 000 000 000 (refunds are credit-note invoices, not negative expenses); deductible 0–100 %, optional `vat_deductible` 0–100 % (share of the input VAT; empty = same as deductible) |
| Expense lines (itemised bill) | ≤ 50 lines per bill; each line: `gross` 0 … 1 000 000 000 (as printed, incl. VAT), `vat_rate` 0–100 and on write one of the allowed rates, `deductible` 0–100 % (default 100), optional `vat_deductible` 0–100 %, description ≤ 2000 chars. Errors name the line index (`loc` contains `lines`, the index and the field). `net` is only optional when `lines` are present |
| Hourly rates | 0 … 10 000 |
| Hours per week | 0 … 168; total hours / budget 0 … 100 000 |
| VAT rates | 0 … 100, and on write one of 0, 5, 7, 16, 19 or the rate configured in the settings |
| Documents | ≤ 500 lines; line description ≤ 2000 chars; line amount and document total ≤ 1 000 000 000 |
| Names (customer, project, task, contract, user, template) | 1 … 200 characters, not blank |
| Short text (street, city, title, …) | ≤ 200 characters; notes/intro/outro ≤ 5 000 |
| Status / kind / role / layout / language | from the allowed sets (e.g. project status `active, paused, done, archived`) |
| Colours | `#rrggbb` |
| Ids | 6–32 characters `0-9a-z`; attachment names `<id>.<ext>` |
| Settings | company type, accounting method, federal state code, Hebesatz 0–1000, payment/offer days 0–365, church tax 0–15 %; numbering patterns may only contain `{year}` and `{n}` / `{n:04d}` and must contain `{n}` |
| Query `year` | 2000 … 2100 |

Attachments: uploads are at most 15 MB. A PDF or image (`.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`) whose first
bytes do not match its extension is refused with **HTTP 415** and nothing is stored. Only those types are served
inline; every other attachment is served as a download (`application/octet-stream`, `Content-Disposition: attachment`).

Only *hard* limits live here – values that can never be right. "Suspicious but possible" input (an amount 100× your
usual one, a date far in the future) is for the soft-warning layer planned for a later alpha.

## Stored data that is already damaged

Records can arrive from an older version or from another device through file sync. `permita38/core/calc.py` therefore
treats every stored value defensively: a record with an invalid date is skipped and a non-numeric value counts as 0,
each with a logged warning, so `/dashboard`, `/forecast`, `/finance` and `/project-report` never fail because of one
bad record. List and detail endpoints return stored records without re-validating them (the model's default values
are filled in) for the same reason.
