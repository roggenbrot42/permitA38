# Specification: scanning old offers and invoices

Status: **ready for implementation** (2026-11-03). Builds on [`receipt-scan.md`](receipt-scan.md) (on `main` since PR #75).
Author: architect. **Review tier: Full** (money, numbering, uploads, parsing of outside input).

## Goal

The maintainer has old offers and invoices that were not made with this app. They upload or drop PDFs (or photos) on the
Offers & Invoices page. Each file is read and, when clean, **added automatically** with its original number; anything
ambiguous is flagged and waits for a check; clicking one opens it; duplicates are refused with a message. The CSV/XLSX import
of [`docs/import.md`](../import.md) stays as it is.

## Decisions by the maintainer

* **Clean documents are added automatically** (2026-11-02). The maintainer checks them where needed. Flagged ones wait.
* Defaults taken for the open points of the draft (2026-11-03, may be changed by the maintainer later, each is one constant):
  * **Paid state**: an old invoice is added as `sent` with no `paid_date`. It shows in open receivables, and under cash
    accounting it is not revenue until the maintainer marks it paid. Offers are added as `sent`.
  * **Past years count normally.** The upload answer names the year, and the UI says "added to 2024".
  * **No automatic link** between an old offer and an old invoice.

## What is reused

* The whole reading side of the receipt scan: `runner.read_file` (worker, e-invoice, PDF text, OCR, rotation, QR), `Word`
  positions, `runner.render_page`, the upload checks of `core/uploads.py`, the page preview and the field overlay of
  `web/receipts.js`.
* From the document import (`core/service/importer/service.py`): customer matching (`_CustomerIndex`, `_customer_record`), the
  record fields `imported`, `original_number`, `original_pdf`, `totals_unverified`, `import_batch`, the batch table and its undo,
  the shared-sequence duplicate rule, `numbering.observe_imported`, `IMPORT_HOOKS`. Where the importer keeps these private,
  move them to a small shared module (`core/service/importer/shared.py`) instead of copying them; the importer's behaviour must
  not change (its tests stay green unchanged).
* Money only through `core/money.py`. Document amounts come from the normal document rules (`core/calc.py` / `documents.py`);
  the printed figures are only compared with them.

## What is different from a receipt

| | Receipt | Old offer or invoice |
|---|---|---|
| Direction | Bill to the user | Document **from** the user |
| Other party | Vendor, free text | **Customer**: the recipient block, matched like the import does, or created |
| Sender | Must not be the user | **Must be the user**: `company_name`, owner name or VAT id / tax number from Settings appears in the text, else flag `not_my_document` |
| Lines | Grouped per VAT rate | Real lines when they prove the net; otherwise one summary line (see "Lines") |
| VAT | Several rates | **One rate per document** (the document model has one `vat_rate`); several rates -> flag `several_vat_rates` |
| Number | Duplicate key | Kept exactly, never renumbered; the counter follows it by `observe_imported` |
| Date | Bill date | Document date; `service_from/to` when printed; `due_date` for invoices, `valid_until` for offers |
| Kind | Always an expense | Invoice or offer, from the **title**; unclear is a flag; credit notes and cancellations are flagged `kind_unsupported` |
| Effect | Cost and input VAT | Revenue and output VAT, by the `accounting` setting |

## Data model

New migration `0015-document_scans` (after `f3fcc516`, all dialect folders) and table `document_scans` in `schema.py`, internal
collection like `receipt_scans`:

```
document_scans: _BASE + status:str sha256:str doc_key:str attachment:str filename:str engine:str
                kind:str draft:json doc:json flags:json record_coll:str record_id:str batch_id:str created_by:id + _EXTRA
indexes: sha256 (unique), status, doc_key, record_id
```

`record_id` is `str`, not `id`: it is polymorphic (invoice or offer, chosen by `record_coll`), so it cannot be a
reference into one table; the scan service tolerates a record that is gone.

* `status`: `flagged` (waits for a check), `added` (a document exists, `record_coll`/`record_id` set), `discarded`.
  An `added` or `discarded` row stays, so the same file is refused again (by `sha256`).
* `doc_key`: `"<number space>:<normalised number>"` where the number space is `shared` with a shared sequence, else the
  collection; normalised = case-folded, spaces and dashes removed (the importer's rule). Empty when no number was read.
* `draft`: the proposed document, in the shape of `DocumentScanConfirmIn` below (plus `customer_match`).
* `doc`: what the overlay needs (pages, word boxes of each field, as in receipt scans), never the full text.
* `attachment`: the stored file name in the attachments area, as for receipts. After adding, the same file becomes the
  document's `original_pdf` (photos are converted to a PDF as receipts do, `photopdf.py`).

The created document carries `imported: true`, `original_number`, `original_pdf`, `import_batch`, `external_id = "scan:<id>"`,
and `totals_unverified: true` only when the user confirmed a document whose printed totals do not match.

## Reading (`core/receipts/docrules.py`, new)

Input: the `ReadResult` of the worker (words with boxes per page) or, for an embedded e-invoice, its structured data. Output:
`DocExtraction` (dataclass, all optional): `kind`, `title_word`, `number`, `date`, `due_date`, `due_days`, `valid_until`,
`service_from`, `service_to`, `recipient` (`name`, `street`, `zip`, `city`, `country`, `vat_id`, `email`), `sender_ok`,
`lines` (`description`, `quantity`, `unit`, `price`, `amount`), `net`, `vat_groups` (rate -> base, vat), `vat_total`, `gross`,
`currency`, `small_business` (§ 19 note found), `sources` (field -> word boxes).

Rules:

1. **Kind from the title** only: the largest or first heading word in the top 40 % of page 1 among `Rechnung`, `Invoice`,
   `Angebot`, `Kostenvoranschlag`, `Offer`, `Quote`, `Gutschrift`, `Rechnungskorrektur`, `Storno(rechnung)`, `Credit note`.
   A word elsewhere in the body never decides it (offers mention "Rechnung" in their terms). None or two different kinds in the
   title area -> `kind_unclear`. Credit note / correction / cancellation -> `kind_unsupported`.
2. **Number**: value next to (right of, or below) a number label for that kind (`Rechnungsnummer`, `Rechnungs-Nr.`, `Rechnung Nr.`,
   `Invoice No.`, `Angebotsnummer`, `Angebots-Nr.`, `Angebot Nr.`, `Nr.`), 1 to 50 characters, at least one digit. Two
   different candidates -> `ambiguous_number`. None -> `no_number`.
3. **Dates**: `Rechnungsdatum`/`Angebotsdatum`/`Datum`/`Date`; due: `fällig am`, `zahlbar bis`, `Zahlungsziel` with a date, or
   `zahlbar innerhalb von N Tagen` / `within N days` -> `date + N`. Valid until: `gültig bis`, `Bindefrist`, `valid until`.
   Service: `Leistungszeitraum` (two dates), `Leistungsdatum`/`Lieferdatum` (one date: from = to). Date in the future, or before
   2000 -> `date_implausible`; no date -> `no_date`.
4. **Recipient**: the address block in the window area (top-left quarter of page 1, below a small sender line if there is one),
   2 to 6 lines, ending at the line with a 5-digit German or other postal code + city, or at an empty gap. First line = name;
   the user's own name / company is never the recipient. Customer VAT id: `USt-IdNr.` or `VAT` near the recipient. None -> flag
   `no_customer`.
5. **Sender**: `sender_ok` when the user's `company_name`, owner name, VAT id or tax number from Settings occurs on page 1
   (case-folded, spacing ignored). Else `not_my_document`.
6. **Totals**: reuse the label rules of `textrules.py` (net `Nettobetrag|Summe netto|Zwischensumme`, VAT `USt|MwSt|Umsatzsteuer`
   with rate, gross `Gesamtbetrag|Rechnungsbetrag|Endbetrag|Total`). Reuse, don't copy: call the textrules helpers.
7. **Lines**: a table whose header has a description column and an amount column (`Pos`, `Beschreibung|Leistung|Bezeichnung`,
   `Menge|Anzahl|Std`, `Einheit`, `Einzelpreis|Preis|Satz`, `Gesamt|Betrag`). A row is a line when its amount parses; wrapped
   description rows join the line above. **Lines are used only when their amounts sum exactly to the net** and each
   `quantity x price` rounds (half-up) to its amount where both are printed; else the lines are replaced by one summary line
   (`description` = the document title or "Leistungen laut Originalbeleg", quantity 1, unit price = net) and the scan notes
   `lines_summarised` (an info note, **not** a flag).
8. **§ 19 UStG**: a small-business note and no VAT printed -> rate 0, no VAT flag.
9. **E-invoice** (XRechnung/ZUGFeRD embedded): seller must be the user (else `not_my_document`), buyer = recipient, lines and
   totals from the XML; the PDF text is only used for the overlay. Extend `einvoice.py` to return the buyer party and the
   document type code (380 invoice, 381 credit note -> `kind_unsupported`, 384 correction -> `kind_unsupported`); keep its
   receipt behaviour.

Robustness rules from the receipt scan apply unchanged: bounded regexes (no catastrophic backtracking, length caps on every
input line), the worker sandbox and timeouts, no file content in logs.

## Reconcile and flags (`core/receipts/docflags.py` or inside docrules, the implementer's choice)

The document is **clean** only if all hold, else it is flagged with every reason found:

* kind is invoice or offer; number, date and recipient read; `sender_ok`;
* currency EUR (`foreign_currency` otherwise);
* exactly one VAT rate, and it is an allowed rate (0, 7, 19 or a historical rate 5 / 16 for dates in 2020-07-01..2020-12-31);
  else `several_vat_rates` / `vat_rate_unusual`;
* net, VAT and gross printed and consistent to the cent with the document rules: `round_half_up(net x rate) == vat` (the
  document's own rounding, i.e. what `calc` computes for these lines) and `net + vat == gross`; else `totals_mismatch`
  (`ambiguous_total` if the receipt rules found competing totals);
* customer match is unique: no match (a new customer is created, not a flag), one match, or two or more matches with similarity
  >= 0.9 -> `customer_ambiguous`;
* no duplicate (below);
* engine `none` or an error -> `unreadable`.

Paid state is not a flag (default above). Unknown number format is not a flag (the importer's counter rule handles it).

## Duplicates

1. Same `sha256` as any row in `document_scans` (any status) -> **409** `duplicate_file`, with the existing document's
   collection and id when there is one (`added`), else the scan id.
2. Same `doc_key` as an existing document (by `original_number` or `number`, in the same number space; with a shared sequence
   offers and invoices are checked against each other) or as another `added`/`flagged` scan -> **409** `duplicate_number`,
   with the existing record. Checked again inside the write transaction (the race fix of the receipt scan).
3. A refused file stores nothing (no attachment left behind).

## Adding a document

In one transaction: create the customer if new (with `import_batch`), create the document through the same record path the
importer uses (so `IMPORT_HOOKS`, validation and the audit log run), call `numbering.observe_imported`, create one
`import_batches` row per uploaded file (`kind: "scan"`, so the existing undo removes the document and a customer created with
it), and mark the scan `added`. Status `sent`, `paid_date` none. `due_date` for invoices: printed, else `date + due_days`, else
the Settings payment term. `valid_until` for offers: printed or empty.

## API (`permita38/api/routers/document_scans.py`, prefix `/api/document-scans`)

Rights: the same dependencies as the receipt router for reading/writing attachments, **plus** the right to write invoices and
offers (whatever `invoices` create requires today). Multipart limits and accepted types: as receipts.

| Method | Path | Body / answer |
|---|---|---|
| `POST` | `/api/document-scans` | multipart `file`. **201** `{status: "added", scan, record: {collection, id, number, year}}` or **201** `{status: "flagged", scan}`. **409** in the receipt pattern: `{detail: <message>, code: "duplicate_file" \| "duplicate_number", existing: {collection, id, number} \| {scan_id}}`. **413/422** as receipts; **415** for anything but PDF and JPEG (PNG, WebP and XML are refused: the original must be a PDF, and only JPEG photos are turned into one by `photopdf`). |
| `GET` | `/api/document-scans?status=flagged` | `{items: [scan]}` (newest first) |
| `GET` | `/api/document-scans/{id}` | `scan` |
| `GET` | `/api/document-scans/{id}/pages/{n}` | page image, as receipts |
| `POST` | `/api/document-scans/{id}/confirm` | `DocumentScanConfirmIn` -> `{record: {collection, id, number, year}, scan}`; flags are cleared by the user's values; duplicates re-checked -> 409; lines that differ from the printed totals without `accept_totals_mismatch` -> 409 `{code: "needs_acceptance", missing: ["totals_mismatch"]}` |
| `DELETE` | `/api/document-scans/{id}` | discard a flagged scan (row stays `discarded`, attachment removed); 409 for an `added` scan (use the document or batch undo) |

`scan` (public; `attachment` is the stored file name, not a path): `id, status, kind, filename, attachment, engine, flags, notes, draft, doc, record_coll, record_id,
created`. `notes` holds info notes such as `lines_summarised`.

Shapes inside `scan` (pinned 2026-11-03, the UI is built against them):

* `flags` and `notes`: lists of `{code, message}` like the receipt scan; `message` is a plain English sentence. Flag codes are
  exactly: `unreadable`, `kind_unclear`, `kind_unsupported`, `no_number`, `ambiguous_number`, `no_date`, `date_implausible`,
  `no_customer`, `customer_ambiguous`, `not_my_document`, `foreign_currency`, `several_vat_rates`, `vat_rate_unusual`,
  `totals_mismatch`, `ambiguous_total`, `small_business_conflict`, `invalid_values`, plus from the rules module `pages_skipped`, `ambiguous_date`, `no_vat`, and from the
  service `duplicate_number` (a clash found later). Note codes: `lines_summarised`, `customer_new`.
* `draft`: `kind, number, date, due_date, valid_until, service_from, service_to, customer_id?, customer {name, street, zip,
  city, country, vat_id, email}, vat_rate, lines [{description, quantity, unit, price}]` (amounts as decimal strings).
* `doc`: the receipt scan's `doc` shape (`pages`, `rotation?`, `boxes [{field, page, x, y, w, h, check, value?}]`, `checks`)
  plus `printed_net`, `printed_vat`, `printed_total` (decimal strings or null). Box fields: `number`, `date`, `due_date`,
  `valid_until`, `service`, `customer`, `kind`, `net`, `vat`, `gross`, `item`.

`DocumentScanConfirmIn` (strict, in `core/models.py`, every field bounded like the other `*In` models): `kind`
(`invoice|offer`), `number` (1..50), `date`, `due_date?`, `valid_until?`, `service_from?`, `service_to?`, `customer_id?` **or**
`customer` (new: `name` 1..200, `street?`, `zip?`, `city?`, `country?`, `vat_id?`, `email?`), `vat_rate` (allowed rates),
`lines` (1..200 of `description` 1..2000, `quantity`, `unit?`, `price`), `accept_totals_mismatch: bool = false` (needed when the
lines do not give the printed totals; sets `totals_unverified`). Regenerate `api/openapi.json`.

## Web UI (`web/docscan.js`, new, plus the Offers & Invoices page)

* A drop zone and an "Upload old documents" button on the Offers & Invoices page; several files at once, uploaded one after
  another; a per-file result line: "RE-… added to 2024", "flagged: …", or the 409 message with a link to the existing document.
* A "To check" panel above the lists with the flagged scans (count badge), like the receipts panel.
* Check dialog: two columns on landscape screens (fields left, page preview with the field overlay right, A4 ratio, the receipt
  dialog's layout); fields for kind, number, dates, customer (combobox of existing customers, or new from the read block), VAT
  rate, lines (add/remove), printed vs computed totals with the difference, and a checkbox "keep the printed totals" when they
  differ. Buttons: Add, Discard. Flag reasons are shown as plain sentences (German UI strings follow the existing UI language
  handling).
* Added documents show an "imported" badge in the lists (if not already shown for imported documents) and open in the normal
  editor; the original PDF is what the PDF button serves (existing behaviour of `original_pdf`).
* Reuse the preview/overlay code of `web/receipts.js` by extracting shared functions, not by copying them.

## Tests

* Unit tests of `docrules` with **invented** documents only: synthetic word lists, and PDFs generated in the test (as the
  receipt tests do). Cover: title vs body word (offer whose body says "Rechnung"), each label variant, wrapped line
  descriptions, lines that do not sum (summary line), § 19, several rates, foreign currency, recipient block with and without
  sender line, not my document, due in N days, service period, credit note.
* API tests: added, flagged, confirm, discard, 409 by file and by number (also across offers/invoices with a shared sequence),
  rights (reader cannot upload, user without invoice rights cannot upload), counter moves after an added invoice, batch undo
  removes the document and the new customer, the race (two uploads of one number) adds one.
* Oracle numbers in the tests are computed by hand in comments (net, VAT half-up, gross).
* Web tests: upload a clean and a flagged invented PDF, the panel, the dialog confirm, the 409 message.
* Randomized suites: extend the state machine model if `document_scans` changes records it models (invoices, offers, customers,
  numbering).

## Review decisions (2026-11-03, Full review of the first implementation)

* **Stale scan rows.** An `added` scan whose document no longer exists (batch undo, deleted) does not block anything: the
  duplicate checks treat it as gone and mark it `undone` (sha256 and doc_key cleared) inside the write transaction.
  **Discard** also clears `sha256` and `doc_key`, so a wrongly read file can be uploaded again.
* **Totals through `calc`.** The added document is compared with `calc.doc_totals(record, settings)`, not with plain money
  maths. If the Settings make the result differ from the printed totals (e.g. `kleinunternehmer` on, 19 % printed), flag
  `small_business_conflict`.
* **Customer matching for auto-add.** Link automatically only on an identifier match (VAT id, e-mail, customer number) or an
  exact normalised name *including* the legal form. A single fuzzy match (similar name, other legal form) is flagged
  `customer_ambiguous` and shown as a suggestion in the dialog.
* **Due days.** A period in a Skonto/discount sentence (`Skonto`, `%`) is never the due period. `Werktage`/working days are not
  converted; the Settings payment term is used.
* **VAT window.** The allowed-rate check uses `service_to`, else `service_from`, else the document date.
* **doc_key.** Case-folded, runs of whitespace collapsed, dashes and other separators **kept** (`2024-1-23` and `2024-12-3`
  must not collide).
* **Plausibility.** `due_date < date`, `valid_until < date`, `service_to < service_from` -> `date_implausible`. The auto path
  builds a `DocumentScanConfirmIn` from the draft; a validation error flags the scan (`invalid_values`) instead of adding it.
* **Photos.** Every JPEG becomes a PDF with `photopdf` (rotation 0 when OCR found none); if the conversion fails the upload is
  refused (422). `original_pdf` is always a `.pdf`.
* **Rights.** Upload and confirm also need `customers` write; the GET endpoints also need `customers` read.
* **Randomized model.** No scan action is added to the state machine (it would need the worker and generated PDFs); scanned
  documents are covered by the API tests. The randomized suites still run once before the merge.
* **Paid state** stays `sent` without a paid date (maintainer default); the UI says "added as open (not paid)" on each result
  line so that the overdue count is no surprise.

## Samples

Nine real PDFs, outside the repo, all with a text layer and no embedded e-invoice. Offers also contain the word `Rechnung` in their
body, so the kind comes from the title at the top of the page. **Never** copy anything from them into the repo, tests, prompts
for implementers or commit messages; the architect scores them locally after the implementation.

## Work packages

1. **Backend** (one implementer): migration, schema, models, `docrules`, `einvoice` buyer, service, router, OpenAPI, tests.
2. **Web UI** (one implementer, in parallel, against the API contract above with a stubbed server answer in its tests where the
   backend is not merged yet): `web/docscan.js`, the page integration, shared preview code, web tests.
3. Merge, Full review (code, tests, logic), fixes, randomized suites, PR, green CI, then the architect scores the samples.
