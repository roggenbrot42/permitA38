# Spec: itemised expenses

*Status: specification by the architect model (AGENTS.md "Division of labour"). Implement on a branch from `main`
**after `feat/migration-ids` has landed** (this needs a migration; use whatever `create-migration` does on `main` then).
Roadmap item: "Itemised expenses: a bill can have several lines, each with its own VAT rate and deductible share".*

## Why

A bill often mixes tax treatments: a hotel invoice has the room at 7 % VAT, breakfast at 19 %, city tax at 0 %, and a
business meal is only 70 % deductible. Today an expense has one amount, one VAT rate and one deductible share, so the
user has to split one paper bill into several bookings, which loses the link to the document and the cross-check
against the bill total.

## Data model

New optional field `lines` on expenses: a new nullable JSON column `lines:json` in the `expenses` table (migration in
**every** dialect, `schema.py`, pydantic models `Expense` / `ExpenseIn`, `ExpenseLineIn`, `ExpenseLine`).

Line (strict `ExpenseLineIn`, lenient `ExpenseLine`, every field with a hard bound; extra="allow"):

| field | type | notes |
|---|---|---|
| `description` | `v.Desc` | e.g. "Room 2 nights" |
| `gross` | `v.NonNegAmount` | amount **as printed on the bill**, incl. VAT (input) |
| `vat_rate` | `v.Percent` | the UI offers 19, 7, 0 |
| `deductible` | `v.Percent`, default 100 | share of **this line** that is deductible |
| `vat_deductible` | `v.Percent \| None` | share of this line's input VAT that is reclaimed; `None` = same as `deductible` (private use). The *Business meals* category sets it to 100: only the cost is 70 % deductible, the input VAT is reclaimed in full (§ 15 Abs. 1a UStG, *verify*). Same field and rule as on single-amount expenses (`fix/meal-input-vat`). |
| `category` | `v.Short \| None` | optional; default = the expense's category |
| `net`, `vat` | derived, ignored on input | stored by the server (see below) |

At most 50 lines. `lines` absent or empty = a classic single-amount expense: **everything that exists today keeps
working unchanged** (API, import, export, tests). The existing top-level `net`, `vat`, `vat_rate`, `deductible`
stay and keep their meaning for consumers that only know them:

* With `lines`: the server **overwrites** the top-level fields on save: `net` = sum of line nets, `vat` = sum of
  line VATs, `vat_rate` = the common rate if all lines share one else `null`, `deductible` = effective share
  `round(100 * sum(net_i * d_i) / sum(net_i), 2)` (100 when the net sum is 0). They are informational for itemised
  bills; the tax figures use the lines (below). Client-supplied top-level `net`/`vat` are ignored when `lines` is set.
* Unknown fields round-trip as everywhere (the repository's `extra` column).

## Money rules (the part that must be exactly right)

Everything in `core/money.py` (exact `Decimal`, half-up cents), never floats.

1. **VAT is computed per rate group, not per line**, as a printed bill does it: for each distinct `vat_rate` `r`
   take `G = sum(gross_i)` over the lines with that rate; `vat_group = q2(G * r / (100 + r))`;
   `net_group = G - vat_group`.
2. Line values are derived from the group: each line gets `vat_i = q2(gross_i * r / (100 + r))`, except the **last
   line of the group**, which gets `group_vat - sum(previous vat_i)` so the lines add up to the group exactly;
   `net_i = gross_i - vat_i`.
3. Kleinunternehmer (small-business) mode: the user still enters the printed gross; VAT is stored but, as today, the
   gross amount is the cost and no input VAT is reclaimed.
4. Tax figures (`calc.py` finance/yearly: `expenses`, `vat_in`, category breakdown) iterate over the **effective
   lines** (a helper `expense_lines(x)` yields the stored lines, or one implicit line built from the top-level fields
   for old records): non-klein cost = `q2(net_i * d_i / 100)`, `vat_in += q2(vat_i * v_i / 100)` where `v_i` is the line's `vat_deductible` or, if empty, `d_i`; klein cost =
   `q2(gross_i * d_i / 100)` where `gross_i = net_i + vat_i`. The category of a line is `line.category` or the
   expense category or "Other". The project report (`calc.py` ~l.355) keeps using the expense's top-level `net`
   (full net, not the deductible share), as today.

### Worked example (use it as test oracle, computed by hand)

Hotel bill: room 160,00 @7 %, breakfast 30,00 @19 %, city tax 6,00 @0 %, all 100 % deductible.
7 %: `160 * 7/107 = 10.4673` -> VAT 10,47, net 149,53. 19 %: `30 * 19/119 = 4.7899` -> VAT 4,79, net 25,21.
0 %: VAT 0,00, net 6,00. Totals: gross 196,00, net 180,74, VAT 15,26.
Rounding by group vs by line: two lines of 0,50 @7 %: group VAT `q2(1.00*7/107)` = 0,07 (net 0,93); line 1 VAT 0,03,
line 2 VAT 0,04 (remainder), whereas rounding per line would give 0,03 + 0,03 = 0,06 (this is the case the tests
must pin down). Business lunch 100,00 @19 % with 70 % deductible: VAT 15,97, net 84,03; cost `q2(84.03*0.7)` =
58,82, input VAT **15,97 in full** when the line is a business meal (`vat_deductible` 100); a line with `deductible` 70 and no `vat_deductible` gives `q2(15.97*0.7)` = 11,18 (klein mode: cost `q2(100*0.7)` = 70,00).

## API

* `POST/PUT /api/expenses` accept `lines` (and still accept the old shape). Response carries `lines` with the derived
  `net`/`vat` and the overwritten top-level fields.
* Validation errors name the line index. `gross` of every line must be >= 0 as today (refunds are credit-note invoices).
* Permissions follow the expenses area (as on `main` at that time).
* `api/openapi.json` regenerated (`make generate-openapi`).

## Web UI (`web/app2.js`, Expenses page; vanilla JS, no build step, everything user-supplied through `esc()`)

* The "Add bill" dialog keeps the simple form. A checkbox "Itemise this bill" switches to a lines table: description,
  gross EUR, VAT %, deductible %, remove button, "+ Add line". Totals under the table: gross, net, VAT per rate. The
  display uses the same arithmetic as the server (reuse `web/money.js`, extend it with the group rule; a JS unit test
  in the style of `tests/web/test_money_js.py` compares it with the worked example).
* Switching the checkbox on converts the single amount into line 1; switching it off is allowed only with one line
  left (otherwise disabled with a hint).
* The expense list shows "n items" under the description for itemised bills; the edit dialog opens with the checkbox
  on. Existing bills open as before.
* Sanity layer (review finding on `feat/sanity-v2`): for a bill with `lines` the checks `expense.vat_mismatch`,
  `expense.vat_rate_implausible` and `expense.vat_exceeds_net` must be skipped (the server sets `vat_rate` to null when
  the rates are mixed, so the hotel example would be flagged), or run per rate group (`vat_group == q2(G*r/(100+r))`).
  `amount_outlier` and `possible_duplicate` keep using the summed top-level fields. Add a test with the hotel example.

## Bill preview in the expense dialog (same release)

The user wants to see the uploaded bill (PDF or image) next to the lines and zoom into individual items.

* The "Add bill" / "Edit bill" dialog gets a preview pane beside the lines table (stacked below it on narrow
  screens). It shows the file chosen in the file input immediately (before saving) and the stored attachment when
  editing. Vanilla JS, no new libraries, no build step.
* **Images** (png, jpg, webp, gif): an `<img>` in a pan/zoom container: buttons "+", "-", "Fit", "100 %", mouse
  wheel zoom (Ctrl+wheel or plain wheel inside the pane, centred on the pointer), drag to pan, pinch zoom on touch
  (`touch-action: none`, pointer events), keyboard: `+`/`-`/`0` when the pane is focused, arrow keys pan. Zoom range
  25 %..800 %. Alt text = the file name through `esc()`.
* **PDF**: use the browser's built-in viewer in an `<iframe>` (it brings its own zoom, search and page navigation);
  no PDF.js. If the browser cannot render it inline (no viewer, mobile Safari), show a clear fallback text with
  "Open in a new tab". A new-tab link is always shown.
* **Other file types** (anything not on the allow-list): no preview, only the download link.
* While the file is not yet uploaded the preview uses a `blob:` URL created with `URL.createObjectURL` and revoked
  when the dialog closes or the file changes. Therefore the CSP in `permita38/api/server.py` gets exactly these
  additions and nothing else: `img-src 'self' data: blob:` and `frame-src 'self' blob:` (keep `object-src 'none'`).
  A test asserts the final CSP string.
* **Serving attachments safely (required, security):** today `GET /api/attachments/{name}` serves any file with a
  guessed content type from the same origin, so an uploaded `.html`/`.svg` would run as a page. Change it to an
  allow-list: `application/pdf`, `image/png`, `image/jpeg`, `image/webp`, `image/gif` are served inline with their
  type; **every other type is served as `application/octet-stream` with `Content-Disposition: attachment`**. All
  attachment responses carry `X-Content-Type-Options: nosniff` (already global) and
  `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'` (no `sandbox`, it
  disables the PDF viewer) and may be framed by the same origin only. Upload keeps its 15 MB limit; additionally
  reject a PDF/image upload whose first bytes do not match its extension (magic-number check: `%PDF-`, PNG, JPEG,
  RIFF....WEBP, GIF8) with 415. Tests for each.
* Not in scope: reading amounts out of the bill (separate roadmap item "bill extraction"), rotating/cropping.
* Tests: browser test uploads a generated PNG (draw a known pattern with Pillow or a tiny hand-made PNG) and a
  one-page PDF, checks that the pane shows them, that zoom buttons change the displayed scale (assert on the
  computed transform / a `data-zoom` attribute), that Fit/100 % work, that the blob URL is revoked on close
  (spy on `URL.revokeObjectURL`), that a hostile file name is escaped; API tests for the allow-list, the
  `Content-Disposition`, the CSP header and the magic-number check, including an HTML file renamed `.pdf`.

## Tests (must exist)

1. Money unit tests for the rules and the worked example above, including group-vs-line rounding, the 0 % line,
   deductible per line, klein mode, and a hypothesis property: for any lines, `sum(line.net) + sum(line.vat) ==
   sum(gross)`, and the line VATs of a rate group add up to the group VAT.
2. API tests: create/update/read an itemised bill, old-style bill unchanged, top-level `net`/`vat` ignored when
   `lines` is set, unknown line fields survive a round trip, bounds (51 lines, negative gross, rate 101), error
   messages name the line.
3. `calc` tests: yearly finance totals, `vat_in` and category breakdown for itemised and old-style bills, mixed, with
   an independent oracle in the test (not the code under test); old records without `lines` give identical numbers
   as before the change (golden values).
4. Repository contract + migration tests: the new column exists in all dialect folders, an older database migrates,
   rows without `lines` stay valid.
5. Importer: expense import still works and produces old-style bills.
6. Browser tests: itemise a hotel bill through the UI, check the displayed totals against the worked example, save,
   reopen, edit a line, switch off itemising; hostile text in a line description is escaped; the random monkey/
   journey suites stay green.

## Docs

`docs/` user documentation (how to book a hotel bill, what "deductible" means per line), README expense section,
CHANGELOG, roadmap status. Mark the roadmap line as done when merged.

## Out of scope

Bill extraction from PDF/image (separate roadmap item), per-line project assignment, quantities and unit prices on
expense lines, foreign currencies.

## Open design decisions to list in the report

Anything not fixed above; the simplest option consistent with this spec.
