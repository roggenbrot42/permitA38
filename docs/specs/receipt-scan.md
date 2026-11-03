# Specification: automatic receipt scan

Status: **approved for implementation** (2026-11-02); one later question remains under "Open decisions". Roadmap item:
"Automatic receipt scan". Author: architect. The implementer follows this document and lists every choice under "Open
design decisions". **Review tier: Full** (money/VAT, uploads, parsing of outside input).

## Goal

The maintainer's workflow:

1. Upload a receipt, or drop it on the receipt list.
2. It is scanned and added automatically.
3. If anything is ambiguous, the bill is flagged.
4. Clicking a receipt in the list opens it for editing.
5. Duplicates are ignored, with an error message.
6. The entire bill must add up, or it is flagged.
7. The preview marks where each field was found on the bill.

## Decisions

* **Deterministic first.** Extraction is local: e-invoice XML, then the PDF text layer, then local OCR. It works without
  `ai.enabled` and does not wait for the AI provider. A vision model comes later as a second reader (see "Vision model").
* **Clean bills are booked without confirmation.** This replaces the roadmap's "prefill, you confirm" for deterministic
  extraction, because the maintainer asked for it. It does not touch rule 5 of
  [`ai-provider-and-proposals.md`](ai-provider-and-proposals.md): anything a model read gets the flag `model_read` and is
  never booked automatically.
* **A flagged bill is not an expense yet** (maintainer's decision). It lives in the new table `receipt_scans` and only
  becomes an expense when the user saves it. So it cannot reach the finance figures, VAT, project reports or exports. A
  flagged bill may have no amount at all, which `Expense.net` could not hold.
* **Clean means proven.** A bill is clean only if no check fails **and** at least one independent arithmetic check
  passes. A total that nothing confirms is not clean, whatever the engine.
* **No invoice number is an error above 250 € gross** (maintainer's decision). Such a bill is flagged and only accepted
  after the user confirms it. Up to and including 250 € it is fine, as for small-value bills (§ 33 UStDV, *verify*).
* **Nobody maintains vendors or categories for this.** `vendor` stays free text. A new vendor is never flagged; the
  category comes from history, then words on the bill, then "Other".
* **Not the proposal queue.** Proposals expire, and a receipt must never expire. The queue is also not on `main` yet.
* **A file is never lost.** An unreadable receipt is kept and flagged. Only a duplicate is refused.
* **Flags, no confidence score.** A flag always says what is wrong.

## Bill date and paid date

Today one `date` field, labelled "Paid on", does both jobs. The maintainer wants a separate paid date.

* `date` is the **bill date** (issue date). [`ustva.md`](ustva.md) already treats it that way.
* New nullable `paid_on`. **`null` means "not given": it counts as paid on the bill date.** No backfill, so every existing
  expense keeps its figures.
* `calc.finance`: under `accounting: eur` the **cost** counts in the month of `paid_on or date`; under `accrual` in the
  month of `date`. **Input VAT stays on `date`** under both, as `ustva.md` requires. A bill can therefore put its cost and
  its VAT in different months or years, so the loop must pick the month separately for each.
* The project report and trip statements stay as they are.
* Bill dialog: "Paid on" becomes **Bill date** (`date`) and **Paid on** (`paid_on`, empty = same as bill date), for
  manual bills too. A new `invoice_no` field joins them.
* CSV/XLSX import stays as it is: without a paid date, `paid_on` is `null`.

### Payment kind

The rules decide `doc.payment.kind`. The first row that matches wins, except for the conflict row.

| Kind | Signals | `paid_on` |
|---|---|---|
| conflict | cash signals and open-payment signals on one bill, or a prepayment (`Anzahlung`, `bereits bezahlt`) | flag `payment_unclear` |
| `stated` | a paid date on the bill: `bezahlt am`, `Zahlungsdatum`, `paid on`, or a full payment in an e-invoice | that date |
| `cash` | till or card payment: `bar`, `gegeben`, `Rückgeld`, `EC`, `girocard`, `Kartenzahlung`, `Visa`, `Mastercard`, `kontaktlos`, a TSE signature, `Kassenbon`; every restaurant receipt | the bill date, written explicitly |
| `invoice` | open payment: `zahlbar bis`, `Zahlungsziel`, `bitte überweisen`, `Lastschrift`, a due date after the bill date, an amount due in an e-invoice | `null` (assumed bill date) |
| `unknown` | none of these | `null` (assumed bill date) |

`invoice` and `unknown` are **not flagged**: the maintainer's default for now is the bill date. The expense list shows a
small "paid date assumed" pill on a scanned bill while `paid_on` is `null`. It disappears when a real date is entered,
and a later bank match can fill exactly these bills.

## Pipeline

`POST /api/receipts`, handled in `core/service/receipts.py`; the first failing step decides:

1. **Check the upload**: rights, 15 MB limit, type allow-list `.pdf .png .jpg .jpeg .webp .xml`, magic number
   (`filetypes.magic_ok`, extended for `.xml`). Share these checks with `routers/files.py`.
2. **Exact duplicate**: SHA-256 of the bytes against `receipt_scans.sha256` -> `409`, nothing stored.
3. **Store** the file with `save_attachment`, unchanged.
4. **Extract** in the worker process (below). Each engine returns the same `Extraction`, plain untrusted data.
5. **Duplicate by invoice number**: same `invoice_key` as an existing scan, or same vendor and `invoice_no` as an existing
   expense -> delete the stored file, `409`.
6. **Reconcile and check** (`core/receipts/reconcile.py`, `rules.py`): run the arithmetic, build the draft, compute the flags.
7. **Book or flag**: no flags -> `RecordService.create_scanned` and the scan row in **one transaction**, status `booked`.
   Any flag -> scan row only, status `flagged`. If the draft fails `ExpenseIn`, it gets the flag `invalid_data`.

### Extraction order

| Engine | Input | Notes |
|---|---|---|
| `einvoice` | `.xml`, or the XML embedded in a ZUGFeRD/Factur-X PDF | XRechnung (UBL and CII) and ZUGFeRD. Exact values, no guessing. |
| `pdf_text` | PDF with a text layer of at least 50 usable characters | `pypdfium2`, all pages up to 50, then the text rules. |
| `ocr` | Image, or a PDF without text (up to 10 pages at 300 dpi) | `tesseract` as a local program, then the text rules. |
| `vision` | Later, see "Vision model" | Not part of `POST /api/receipts`. |
| `none` | Nothing readable, or OCR not installed | Flag `unreadable`, file kept. |

Pages beyond a limit are never dropped silently: they raise `pages_skipped`, because the total may be on them (not for an e-invoice, whose values come from the XML, not from the words).

`core/receipts/extraction.py` is the seam between readers and rules: `ReadResult` from a reader, `Extraction` from the
text rules or the e-invoice reader. Its fields are the contract; change them only through the architect.

XML safety: refuse any `<!DOCTYPE` or `<!ENTITY` before parsing; the stdlib parser is then enough, no new dependency.

### Text rules (`core/receipts/textrules.py`, pure functions, no I/O)

German and English labels, in tables at the top of the module.

* **Amounts**: `1.234,56`, `1,234.56`, `12,50`, `12.50`, with `EUR` or `€`. Parsed to `Decimal`, never `float`.
* **Total**: labels such as `Gesamt`, `Summe`, `Rechnungsbetrag`, `Zu zahlen`, `Total`, `Amount due`. Two different
  values under the strongest label found -> `ambiguous_total`. `Zwischensumme`/`Subtotal`, `Gegeben`, `Rückgeld`,
  `Trinkgeld` and `Übertrag` never count as the total. With a prepayment, `Zu zahlen` is the amount due, not the total.
* **Date**: `DD.MM.YYYY`, `DD.MM.YY`, `YYYY-MM-DD`, `D. Monat YYYY`. Prefer a date beside `Datum`/`Rechnungsdatum`.
  `MM/DD/YYYY` is never guessed. Two different labelled dates -> `ambiguous_date`.
* **Invoice number**: `Rechnungsnummer`, `Rechnung Nr.`, `Invoice no.`, and on till receipts `Beleg-Nr`, `Bon-Nr`,
  `Kassenbon-Nr`, then a TSE transaction number. Read even on small bills, because it makes duplicate detection exact.
* **Items**: lines with description, optional quantity and unit price, amount and VAT code (`A`/`B`, `1`/`2`), including
  negative lines (`Rabatt`, `Pfand zurück`). `Übertrag` lines are skipped.
* **VAT table**: rate with amount, e.g. `MwSt 19 % 1,90`, `A 19,00% 10,00 1,90 11,90`.
* **Vendor**: first plausible line of the header, without address, phone or legal-notice lines. Then cleaned like any
  outside text: control and bidi characters removed, cut to the bounds of `v.Short`.
* **Also read when present**: VAT id, due date, paid date, payment signals, tendered, change, card amount.

### The whole bill must add up (`core/receipts/reconcile.py`)

A pure function from `Extraction` to a list of checks, all in exact `Decimal`, **no tolerance**. A check runs only if both
sides were read. Each result is stored in `doc.checks` and shown in the dialog.

| Check | Rule | Flag if it fails |
|---|---|---|
| `item_line` | quantity x unit price = amount, half-up cents | `items_mismatch` |
| `items_group` | items of one VAT code add up to that rate group's gross | `items_mismatch` |
| `items_total` | all items + adjustments (discount, tip) = total, or = subtotal if printed | `items_mismatch` |
| `subtotal` | subtotal + adjustments = total | `total_mismatch` |
| `group_sum` | net + VAT = gross of a rate group | `vat_mismatch` |
| `group_vat` | printed VAT = `q2(G * r / (100 + r))` | `vat_mismatch` |
| `groups_total` | rate groups add up to the total | `total_mismatch` |
| `due` | total - prepaid = amount due | `payment_mismatch` |
| `tendered` | tendered - change = total | `payment_mismatch` |
| `card` | card amount = total | `payment_mismatch` |

**E-invoices** run the EN 16931 sums the same way: sum of BT-131 = BT-106; BT-106 - BT-107 + BT-108 = BT-109; BT-109 +
BT-110 = BT-112; BT-112 - BT-113 + BT-114 = BT-115; for each VAT group, BT-116 x BT-119 / 100 = BT-117 and the groups add
up to BT-110; the nets of the groups add up to BT-109 (BT-116 sum, flag `total_mismatch`) and net + VAT of the groups add up
to BT-112. An e-invoice with BT-113 = BT-112 is paid in full: kind `stated`, not `payment_unclear`.

**Needs one independent passing check that refers to the booked gross** (the total, or all group grosses). Independent:
`group_vat` with a rate above 0, `group_sum`, `groups_total` with several groups, `items_total` with **two or more** items,
`items_group` and `tendered`. Not independent: `groups_total` with one group, `card`, `group_vat` at 0 % (0 = 0), `group_vat`
of a group whose gross was only left over from the total when there are several groups (it has a window of about 7 cents),
and `items_total` over a single item (it may only repeat the total). Of the independent checks, only these refer to the booked
gross: `items_total`, `groups_total`, `tendered`, the e-invoice sums, the QR checks, and `group_vat`/`group_sum` of a bill with
one group. `net_total` and `vat_total` alone, and `items_group`, do not. If none qualifies, the flag is `not_verifiable`. A bill
printing only a total and a rate therefore goes to the user. (A single group keeps its spec oracle: its gross is the total
and the printed VAT is its check.)

**Groups that print only their VAT** (`enth. MwSt 19% 1,90 A`): when the items map to the groups by code and add up to the
total (and the bill has no discount, charge, tip or rounding), the gross of a group is the sum of its items, so `group_vat` runs for every group. If a gross cannot be derived, the
bill is not booked as VAT-free: `not_verifiable`, or `no_vat` if a group has no rate. A gross is booked as VAT-free only when
every group the bill shows is 0 % (a 0 % group beside a taxed one does not count) or it has a § 19 note. The booked line grosses must add up to the printed total (else `total_mismatch`).

On `confirm` the check runs again with the user's figures. If they differ from the printed total, see `accepted` below.

### What is booked

* **One VAT rate**: a classic expense with `gross` split by `money.split_gross` (`net`, `vat`, `vat_rate`).
* **Several rates**: `lines`, one line per rate with the printed gross. Item lines stay in `doc.items` for the check and
  the dialog; they are not booked as expense lines.
* `date` = bill date, `paid_on` per "Payment kind", `invoice_no` as read. No project, trip or supplier.
* `category`, first match wins, never flagged:
  1. the most recent expense with the same vendor (case-folded, trimmed), so a correction is remembered;
  2. the word table in `core/receipts/categories.py`, on vendor and item text. Examples: `Hotel`, `Pension`, `Übernachtung`
     -> Accommodation; `Tankstelle`, `Bahn`, `Taxi`, `Parkhaus` -> Transportation; `Restaurant`, `Gaststätte`, `Bewirtung`,
     `Café`, `Trinkgeld` -> Business meals. Only values of `EXP_CATS`. `bahn` matches only as a word or `-bahn`, not
     `Bahnhof`. A meal word in the **vendor** beats Transportation and Accommodation (`Café am Bahnhof`, `Hotel Restaurant
     Adler` are Business meals at 70 %), unless the text shows a room (`Übernachtung`, `Zimmer`, `Room`);
  3. "Other".
* **Business meals** are booked like the dialog does: `deductible` 70, `vat_deductible` 100. Every other category: 100.
* `attachment` = the stored file. `external_id` = `scan:<scan id>`, which drives the "scanned" pill. Today an expense write
  neither protects nor keeps `external_id`, so the expense branch of `RecordService._write` must change: a `scan:` value
  sent by a client is dropped, and on update a stored `scan:` value is kept. Only `RecordService.create_scanned` may set
  it. The importer's use of the field stays as it is.

Money comes from the existing rules in [`itemised-expenses.md`](itemised-expenses.md). The printed figures are only
compared, never stored as the booked amounts.

### Flags (closed set, `core/receipts/flags.py`)

Any flag stops automatic booking. The message is what the UI shows.

| Code | When | Message |
|---|---|---|
| `unreadable` | no usable text | Could not read this receipt. |
| `pages_skipped` | more pages than the engine's limit | Only part of this bill was read. |
| `no_total` | no total found | No total found. |
| `ambiguous_total` | competing totals | Several possible totals. |
| `no_date` | no date found | No date found. |
| `ambiguous_date` | competing dates | Several possible dates. |
| `date_implausible` | bill date in the future or before 1 January of the previous year; paid date in the future or before the bill date | Check the date. |
| `no_vendor` | no vendor found | No vendor found. |
| `no_invoice_number` | no invoice or receipt number found, and the gross total is above 250 € or unknown | No invoice number found. Enter it, or confirm that the bill has none. |
| `no_vat` | no VAT rate, and no § 19 UStG note or printed 0 % | No VAT rate found. |
| `no_supplier_tax_id` | gross total above 250,00 € and neither a VAT id nor a tax number (*Steuernummer*) was read (*verify* § 14 UStG) | No supplier tax number or VAT id found. |
| `vat_rate_unusual` | a rate other than 0, 7 or 19 | Unusual VAT rate. |
| `vat_mismatch` | `group_sum` or `group_vat` fails, by even one cent | Printed VAT does not match the amounts. |
| `total_mismatch` | `subtotal` or `groups_total` fails | The amounts do not add up to the total. |
| `items_mismatch` | `item_line`, `items_group` or `items_total` fails | The items do not add up. |
| `payment_mismatch` | `due`, `tendered` or `card` fails | The payment does not match the total. |
| `not_verifiable` | no independent check passed | Nothing on the bill confirms this total. |
| `payment_unclear` | conflicting payment signals, or a prepayment | Check how and when this was paid. |
| `foreign_currency` | any currency other than EUR | Not in euros. |
| `credit_note` | `Gutschrift`, `Storno`, `Credit note`, or a negative total | Looks like a refund or credit note. |
| `reverse_charge` | `Steuerschuldnerschaft des Leistungsempfängers`, `reverse charge`, or e-invoice category AE | Reverse charge bill. |
| `possible_duplicate` | same vendor, date and gross as an expense or a waiting scan, unless both have different invoice numbers that OCR did not read | Maybe already booked. |
| `model_read` | read by a model | Read by a model. Please check. |
| `invalid_data` | the draft fails `ExpenseIn` | Some values could not be used. |

### Duplicates

| Case | Result |
|---|---|
| Same file bytes | `409` `match: "file"`, nothing stored |
| Same TSE id (serial, transaction number and start time of the QR code) | `409` `match: "tse"`, new file removed |
| Same invoice number, **no number read by OCR on either side**, and the same document date, or the same VAT id when the number comes from an invoice label (not a till or receipt label such as `Bon-Nr`, `Beleg-Nr`, `Kassenbon`) | `409` `match: "invoice"`, new file removed |
| Same invoice number otherwise (OCR number, or a till or receipt number on another day) | Flag `possible_duplicate`; the user decides. A till number that restarts every day does not refuse the next day's bill. |
| Both sides have a TSE id and the ids differ, or either side is a credit note | The invoice number is not compared. A credit note reaches `credit_note` and is never refused as a duplicate of its invoice. |
| Same vendor, date and gross, and at least one invoice number is missing or came from OCR | Flag `possible_duplicate`, also below 250 €. It is only booked after the user confirms. Two identical coffees cost one click. |
| Same vendor, date and gross, different invoice numbers, none read by OCR | Two real bills, no duplicate |

* The keys of a booked scan are not trusted from the row: they are computed again from the live expense (vendor, invoice number,
  date) with the VAT id of the scan, so editing or deleting the expense frees or moves them.
* `doc.tse_id` = `<whole serial, lower case>-<transaction number>-<start time digits>` of a TSE code, stored on every scan that has one. It is the
  strongest identity and is checked first.
* `invoice_key` (indexed column) = SHA-256 of the case-folded VAT id (else vendor) plus the invoice number. Null without a number.
  `doc.invoice_keys` lists every key the bill can be found under: VAT id and vendor, each as read and normalised
  (case, spaces and punctuation removed). Two scans match when their key sets meet (the lookup reads the scan rows; no new migration).
* `possible_duplicate` compares the vendor normalised, the date and the **gross** (a tip does not hide a match). It also compares
  against scans that still wait for a check. `doc.number_ocr` tells whether the number came from OCR or a vision model; a number made
  of the TSE id does not count as OCR. Different numbers suppress the flag only if neither side is `number_ocr`
  (a number typed at `confirm` is not).
* `doc.number_weak` is true when the number was read from a receipt, till or transaction label; it makes a clash on the VAT id alone soft.
* The upload and `POST /api/receipts/{id}/parts` repeat the duplicate checks inside the booking transaction: a bill that was clean
  but meets a bill stored by a parallel upload meanwhile is flagged `possible_duplicate` instead of booked.
* `confirm` and `POST /api/receipts/{id}/parts` run the same checks (TSE id, keys) as the upload, against all other scans.
* A duplicate counts only against a flagged scan or a scan whose expense still exists. Deleting an expense removes its
  scan row, so the same receipt can be uploaded again.
* **Bills that were attached by hand** are covered too: before each hash check, the service hashes expense attachments
  that have no scan row yet and stores them with status `known`. The first run reads every old attachment once; later
  runs only read new ones. A `known` row has no TSE id; only its file hash and the expense's vendor and number are compared.

### Worker process

PDF and image parsing runs in a child process, as the AI spec already requires for this input.

* A hidden CLI subcommand `permita38 scan-worker <path>` writes JSON to stdout. In the bundled build it is started
  through `sys.executable` (`packaging/entry.py`), so it works without a Python installation.
* Timeout `scan.timeout_s`, stdout capped at 1 MB, extracted text capped at 200 000 characters. On POSIX `RLIMIT_AS`
  (1 GiB), `RLIMIT_CPU` and `RLIMIT_NOFILE`; on Windows only the timeout.
* On timeout or output overflow the parent ends the whole process group on POSIX, so a Tesseract child does not
  survive. On Windows the worker has only the timeout and the output cap (no memory or CPU limit), and it runs with the
  rights of the server.
* The worker bounds its output: each word at 64 characters and at most 8 000 words (and 200 words per line when the
  lines are grouped). Words left out raise `pages_skipped`; the same limits are applied again before the text rules.
  Parsing and rules run before the database transaction, which only re-checks duplicates and writes.
* Tesseract: argument list, no shell, a temporary directory, the same timeout. Rendered pages are written as PNM, which
  needs no image library.
* Timeout, crash or bad output -> flag `unreadable`, the file is kept.
* A semaphore allows `scan.max_concurrent` workers. The route is synchronous, so a dropped batch is simply queued.
* The extracted text is not stored or logged. Only the fields in `draft` and `doc` are kept.
* The worker returns words with positions, not plain text (see "Detected fields on the bill").

### Configuration (per device, `config.yaml`)

```yaml
scan:
  enabled: true
  tesseract_path: null     # null: search PATH
  languages: deu+eng       # OCR counts as available only if these are installed
  timeout_s: 120
  max_concurrent: 2
```

`Config.scan: ScanConfig`, strict bounds, `extra="forbid"`. With `enabled: false`, `POST /api/receipts` returns `503`
and the UI shows no upload control.

## Data model

Migration `0014-receipt_scans` in all dialect folders, created with `permita38 create-migration`; `schema.py` and
pydantic models `ReceiptScan` and `ReceiptConfirmIn`.

```
expenses: new columns  paid_on:str  invoice_no:str          (both in Expense and ExpenseIn, with hard bounds)

receipt_scans (internal collection, exported)
  id status sha256 invoice_key attachment filename engine
  draft:json doc:json flags:json expense_id created_by created updated + extra
indexes: unique (sha256), (status), (invoice_key), (expense_id)
```

* `status`: `flagged -> booked`, or `booked` directly, or `known` for hash-only rows. Discarding deletes the row.
* A unique violation on `sha256` from two parallel uploads is reported as the normal `409`.
* `draft`: the expense as read, every field nullable, in the shape of `ExpenseIn`.
* `doc`: `{vat_id, due_date, currency, printed_total, printed_vat, payment:{kind}, items[], checks[], boxes[], pages}`, at
  most 200 items and 300 boxes.
* `INTERNAL_COLLECTIONS` gets `receipt_scans`. Export it, so duplicate detection and flagged bills survive a move.

## API contract

All routes need `expenses` **and** `attachments` rights: read for `GET`, write otherwise. Today that means boss and
accountant, not employee. Add the routes to the token route-walk test like any others.

### `POST /api/receipts` (multipart field `file`)

Booked, `201`:

```json
{"outcome":"booked",
 "scan":{"id":"s1","status":"booked","filename":"hotel.pdf","engine":"pdf_text","flags":[],"expense_id":"e42"},
 "expense":{"id":"e42","date":"2026-10-12","paid_on":"2026-10-12","invoice_no":"2026-1042","vendor":"Beispiel Hotel GmbH",
            "net":180.74,"vat":15.26,"category":"Accommodation","attachment":"a1.pdf","external_id":"scan:s1","lines":[
              {"description":"VAT 7 %","gross":160.0,"vat_rate":7,"net":149.53,"vat":10.47},
              {"description":"VAT 19 %","gross":30.0,"vat_rate":19,"net":25.21,"vat":4.79},
              {"description":"VAT 0 %","gross":6.0,"vat_rate":0,"net":6.0,"vat":0}]}}
```

Flagged, `201`:

```json
{"outcome":"flagged","expense":null,
 "scan":{"id":"s2","status":"flagged","filename":"IMG_0412.jpg","engine":"ocr","attachment":"a2.jpg","created":1793620000,
         "flags":[{"code":"items_mismatch","message":"The items do not add up."}],
         "draft":{"date":"2026-10-14","paid_on":"2026-10-14","invoice_no":"8841","vendor":"Beispiel Tankstelle",
                  "gross":50.0,"vat_rate":19,"category":null},
         "doc":{"payment":{"kind":"cash"},
                "checks":[{"name":"items_total","expected":"50.00","found":"56.00","ok":false},
                          {"name":"group_vat","expected":"7.98","found":"7.98","ok":true}]}}}
```

Errors:

```json
{"detail":"This receipt is already in the list: Beispiel Hotel GmbH, 12.10.2026, 196,00 €.",
 "code":"duplicate","match":"file","expense_id":"e42","scan_id":"s1"}
```

`409` as above, with `match` `file`, `tse` or `invoice`, raised as `ServiceError(409, message, body={...})`. A duplicate of a
flagged bill has `expense_id: null`. `413` over 15 MB, `415` type not accepted or content does not match the extension,
`403` rights, `503` scanning switched off.

### Other routes

* `GET /api/receipts?status=flagged` -> `{"items":[<scan>, ...]}`, newest first. No other status is exposed in v1.
* `POST /api/receipts/{id}/confirm`, body = the strict fields of `ExpenseIn` plus `accepted` (list of `no_invoice_number`,
  `total_differs`) -> `200 {"expense":{...}, "scan":{...}}`. Runs `create_scanned` and the status change in one
  transaction; `attachment` and `external_id` are set by the server.
  * Above 250 € gross without `invoice_no` in the body, `accepted` must hold `no_invoice_number`.
  * If the saved gross differs from the printed total, `accepted` must hold `total_differs`.
  * Otherwise `409 {"detail":"...","code":"needs_acceptance","missing":["no_invoice_number"]}`, and nothing is booked.
  * A typed `invoice_no` runs the duplicate check again: `409 code: duplicate`.
  * `409` if the scan is not `flagged`, so a double click cannot book twice. `422` field errors as on `POST /api/expenses`.
    `sanity_warnings` are returned as on a normal save.
* `GET /api/receipts/{id}` -> one scan of any status. The dialog of a booked bill finds it through `external_id`.
* `GET /api/receipts/{id}/pages/{n}` -> PNG of page `n` (from 0) at 150 dpi, for the preview. Same headers as
  `GET /api/attachments/{name}`. `404` for a file that is not a PDF or a page that does not exist.
* `DELETE /api/receipts/{id}` -> `204`. Only for `flagged`; removes the row and its attachment file. Otherwise `409`.
* `GET /api/receipts/capabilities` -> `{"enabled":true,"pdf":true,"einvoice":true,"ocr":false,"ocr_install":"available","vision":"off","accepted":[".pdf",".png",".jpg",".jpeg",".webp",".xml"]}`.
  `vision` is `off`, `local` or `remote`. `ocr_install` is `available` or `none`.
* `POST /api/receipts/ocr/install` -> see "OCR on Windows".

Editing a booked bill stays the normal `PUT /api/expenses/{id}`. `RecordService` deletes the scan row when an expense
is deleted. Regenerate `api/openapi.json`.

## Web UI (`web/receipts.js`, hooks in `web/app2.js`)

* **Upload**: an "Upload receipts" button (several files) next to "+ Add bill", and the whole Expenses page as a drop
  target with a visible overlay. Only shown with `enabled` and both write rights.
* **Progress panel** above the list, one row per file: *Scanning* -> *Added: vendor, amount* / *Check needed* / the error.
  Files are sent one at a time. Duplicate errors stay until dismissed and link to the existing bill. Other files of the
  batch carry on.
* **Needs your check (n)** section above the table, newest first: file name, what was read, a warning pill with the
  flag messages. It sits outside the KPI cards, with a note that these bills are not counted yet. The two KPI cards count the bills only
  (labelled "net bills" / "bills (gross)"); `/api/finance` also adds trip amounts. A damaged `paid_on` counts as the bill date.
* **Booked bills** stay in the existing table, with a small "scanned" pill and, if `paid_on` is `null`, "paid date assumed".
* **Click** on a flagged row opens the normal bill dialog with the draft filled in, the stored file in the existing
  preview, and at the top the flag messages plus a checks table (printed vs computed, failed rows marked in text, not only
  in colour). Title "Check receipt"; **Save** calls `confirm`, **Discard receipt** calls `DELETE`. Booked bills open in the
  normal dialog, as today.
* If no invoice number is entered above 250 €, a required checkbox appears: "This bill has no invoice number". If the amounts differ
  from the printed total, the same with "Book this although it differs from the printed total of X". They set `accepted`.
* `A.editExpense` is split so this reuses the dialog; do not copy it. The manual "+ Add bill" path gains only the new
  fields.
* If `ocr` is false, one line says photos are kept and flagged because OCR is not installed on this device. With
  `ocr_install: available`, a boss also gets the button "Download OCR".
* All text read from a file goes through `esc()`. Nothing from a receipt is rendered as HTML or a link.

## Detected fields on the bill

The preview draws a labelled box around each field the scan read, in the dialog of flagged and booked scanned bills.

**This must be built into v1.** Boxes cannot be added afterwards without rewriting the rules, because the rules have to
know which word each value came from.

* **Positions from the readers.** The worker returns words as `{text, page, x, y, w, h}`, from Tesseract's TSV output or
  from pdfium's character boxes. `textrules.py` works on these words, and each extracted value keeps its source words.
* **`doc.boxes`**: `{"field":"total","page":0,"x":0.62,"y":0.81,"w":0.20,"h":0.03,"check":"failed"}`. Coordinates are
  fractions of the page, 0 to 1, from the top left, so they are independent of zoom. Clamped to the page. `check` is `ok`,
  `failed` or `none`, from the check that used this value. `field` is one of `vendor date invoice_no vat_id subtotal total
  vat_group item due_date paid_date tendered change`.
* **Images** use the existing pan/zoom preview: a wrapper around `#e_img` holds a layer of boxes positioned in percent, so
  they follow the zoom without any code.
* **PDFs** cannot get a layer over the browser's viewer, which has its own zoom and scrolling. A scanned PDF is shown as page
  images in the same pan/zoom preview, with page buttons, from `GET /api/receipts/{id}/pages/{n}`. "Open the original" stays.
  Page images are PNG, written with stdlib `zlib`, so this needs no Pillow. Manual bills keep the iframe.
* **Phone photos**: browsers apply the EXIF orientation and Tesseract does not. The worker reads the orientation tag and
  turns the OCR input and the box coordinates to match, or the boxes land in the wrong place.
* **E-invoices**: an `.xml` has no page, so no overlay; the dialog shows only the checks table. For ZUGFeRD, the values are
  looked up in the PDF's text layer, and boxes appear where a value is found once.
* **Vision**: model coordinates are not trusted. Values are looked up in the OCR words where possible; the rest are listed.

UI:

* A "Show detected fields" toggle in the zoom bar, on by default in "Check receipt". The state is kept with `localStorage`
  in `try/catch`.
* Each box has a short text label ("Total"), and a failed check is marked in the label too, not only by colour. Colours
  come from theme tokens, so both themes work.
* Focusing a form field highlights its box and scrolls to it. Hovering or focusing a box shows the value read. Boxes can
  be reached by keyboard. Labels are fixed strings; a value is set with `textContent`.
* Fields read without a position, and expected fields not found, are listed under the preview as "Not marked on the bill".
  Nothing is only in a tooltip.

## OCR on Windows

The Windows release stays small and downloads Tesseract when it is needed (maintainer's proposal). The release stays at
18 MB and only users who want photos read pay for the download.

**Not from the upstream installer.** The UB-Mannheim file is a 2024 `.exe` installer of 50 MB that unpacks to about 250 MB,
with training tools that are not needed. Running an installer from an app is also the wrong trust model.

* **Own package.** A manual workflow `.github/workflows/ocr-tools.yaml` takes the upstream build at a pinned digest, removes
  the training tools and `osd`, adds `tessdata_fast` `deu` and `eng` at a pinned commit plus all licence files, and publishes
  `ocr-tools-win64-<version>.zip` on a GitHub release of its own, with an attestation like our other release files.
* **Digest in the code.** URL, version, size and SHA-256 are constants in `core/receipts/ocr_tools.py`, committed after that
  workflow ran. A swapped file on GitHub is refused unless the permitA38 release itself was changed.
* **Asked for, never automatic.** `ocr_install` is `available` only in the bundled Windows build without working OCR. A boss
  presses "Download OCR" and sees the size and the host. No other outbound request exists for this feature.
* `POST /api/receipts/ocr/install`: boss, session only, one at a time. `200 {"ocr":true,"version":"5.x"}`; `409` already
  installed or running; `404` not offered on this build; `502` download or verification failed, nothing left behind. Normal
  HTTP timeouts would cut this off, so the route streams to disk and the UI shows a spinner, no progress bar in v1.
* **Download**: a new `netsafe.download_pinned(url, sha256, max_bytes)` with the rules of `SafeFetcher` (no private addresses,
  TLS, redirect limit), a host allow-list for GitHub's release hosts, and no content-type check. The digest is checked
  **before** anything is unpacked.
* **Unpack** to a temporary folder under `%LOCALAPPDATA%\permitA38\ocr\`: no path traversal, limits on file count and total
  size. Then rename to `<version>\`, so a half-finished install never counts. Per device, never in the data folder.
* **Lookup order**: `scan.tesseract_path`, `PATH`, the downloaded copy, with `--tessdata-dir` for the latter.
* **Updates**: a new permitA38 release with a new digest offers the download again. Old versions are deleted after a
  successful install.
* **Docker and Linux** are unchanged: the image installs Tesseract, and a Linux bundle needs a system package.

Tests: `download_pinned` against a local server: wrong digest, oversize, redirect to a private address, redirect to another
host. Unpack: path traversal, too many files, an interrupted install is ignored. Route: rights, token refused, `none` on
other builds. CI on `windows-latest`: download the real package and read the fake PNG, in the bundled build.

## Lessons from the first real receipts (2026-11-02)

A few real receipts, kept outside the repo, were phone scans saved as PDF with the scanner app's text layer. That layer held
the amounts and numbers once stray spaces were removed, but the first version of the rules read none of them correctly. These changes are part of v1. Test them with
**invented** receipts of the same layout, never the real ones.

**Reading**

* Stray spaces inside numbers: `24, 60 €`, `14. 10. 2026`, `73 912`.
* Skewed lines: a slightly turned photo moves an amount onto the neighbouring line. Line grouping must follow the slope.
* The block after the items is a summary, not items: `Summe Verkäufe` is the subtotal; `Nettoumsatz`, `Steuersumme`, `Total`
  and the VAT rows are never items.
* Till VAT tables print the gross and the VAT on separate rows: `Ums. inkl. 19% MwSt 8,40 € A` or `Umsatz 19% inkl. 12,00 € A`
  gives the gross, `enth. MwSt 19% 1,34 € A` or `MwSt 19% 1,92 € A` the VAT of the same group.
* Tips appear twice (`Trinkgeld`, `Trinkgelder etc.`): one adjustment of kind `tip`, not an item.
* A line naming a card with an amount is the card amount: `EC-KARTE 24,60 €`, `Visa 47,00 €`.
* `Datum und Zeit:` is a date label.
* Text after `Bewirtungsaufwand-Angaben` is a handwritten form, and card slips and TSE blocks are technical. They give no
  total, date, vendor or items. `Höhe der Aufwendungen` is never a total.
* **Vendor**: a line with a legal form (`GmbH`, `UG`, `AG`, `KG`, `S.A.R.L`, `Ltd`) first; else the line directly above the
  street or postcode line; else the first line. Logos come out as noise. Known misreadings such as `Gmbll` become `GmbH`.
* French labels, for neighbouring countries: `Quittance No.`, `Prix de course`, `TVA`, `Chauffeur` (category Transportation).

**New checks**: `net_total` (sum of group nets = `Nettoumsatz`) and `vat_total` (sum of group VAT = `Steuersumme`), both
independent, flag `vat_mismatch`.

**Tips are booked.** A tip was paid, so the booked gross must equal the total: the draft gets one more line, `Tip`, at 0 %,
with the bill's deductible share. A tip makes a bill multi-line even with one VAT rate.

**OCR tries several page modes.** Tesseract's default mode found only a few of the values on one receipt and mode 6 all of them, while on
another receipt mode 3 found all and mode 6 only half. So the worker runs modes 6, 3 and 11, stopping early if it runs out of
time, and returns all results (`ReadResult.alternatives`). The service evaluates each and keeps the first clean one, else the one
with the fewest flags. Each run took under a second on these pages. A usable text layer is still used first, without OCR.

**Oracle, tip** (invented): items B 11,20 and 3,10, items A 5,20, 3,90 and 0,40; `Summe Verkäufe` 23,80; tip 2,20; total 26,00; A
gross 9,50, VAT 1,52; B gross 14,30, VAT 0,94; `Nettoumsatz` 21,34; `Steuersumme` 2,46; card 26,00 -> booked as Business meals:
lines 9,50 @19 %, 14,30 @7 %, 2,20 @0 %; net 23,54, VAT 2,46, gross 26,00; `/api/finance` cost 16,48, input VAT 2,46.

## Lessons from the first real photos (2026-11-02)

A handful of phone photos, kept outside the repo. None was booked and none was booked wrongly: every misreading was caught. In one,
OCR read a total with one digit too many while the rate groups added up to a smaller sum, and `groups_total` flagged it. But most photos were
flagged for reading problems that better input can fix, so WP I is part of v1.

**WP I, photo reading:**

* **Turn the photo.** Phones do not always record the rotation: two photos were sideways with EXIF orientation 1. At the
  wrong angle OCR reads noise; a quarter turn then read nearly all values of one of them. Landscape images try 90 then 270
  degrees; portrait images try 0 then 180. The first angle whose words contain a total label or at least 5 amounts wins.
  The overlay must follow the chosen angle: the preview shows the turned image, so the server returns the angle in
  `doc.rotation` and the page image route serves the turned photo for an image file too.
* **Page modes** per the section above, on the chosen angle.
* **Add a photo to a receipt.** A long receipt may need several photos. A flagged scan gets "Add another photo", through
  `POST /api/receipts/{id}/parts` with a `file`. The server keeps the photos in upload order, reads them as consecutive
  pages, and evaluates the result again; if it is clean, it books the bill and returns the normal `outcome`.
* **Attachment.** An expense has one `attachment`. A scan with parts stores one generated PDF with the photos as pages,
  JPEGs embedded unchanged (DCTDecode, stdlib only), and PNG or WebP re-encoded only if needed. The originals are deleted
  after that PDF is written. Every part's SHA-256 still counts for duplicates: `doc.part_hashes`, plus one `known` row each.
  Word and box `page` values match the generated PDF.
* **Overlap.** Photos overlap. Lines at the start of a part that repeat the end of the previous part, compared without
  spaces, are dropped before parsing, so an overlapping item is not counted twice. The check catches what this misses.
* `POST /api/receipts/{id}/parts`: same upload checks; `409` if the scan is not flagged, `409 code: duplicate` for a file already
  known, at most 5 parts. Response as `POST /api/receipts`.

Oracle, parts (invented): part 1 ends after `Total 26,00 €` of the tip oracle, part 2 starts with `Total 26,00 €` and carries
the VAT table and card line. Part 1 alone is flagged, both together book the tip oracle's figures.

## Lessons from the real PDF invoices (2026-11-02)

A set of real PDFs, kept outside the repo, of different kinds. None was booked and none was booked wrongly. A real e-invoice
file passed all 6 sums and only failed on a bug. Part of WP I:

* **Dates**: English `October 12, 2026`. `Rechnungsdatum` and `Date of issue` beat `Datum`, which beats a date on the invoice number
  line. `Bestelldatum`, `Lieferdatum`, `Leistungsdatum` and `Date due` are never the bill date, so they cause no `ambiguous_date`.
* **Net subtotal**: on an invoice `Subtotal` or `Total excluding tax` is often net. The `subtotal` check then is subtotal + VAT =
  total. Only if that fails too does the check fail. `Nettosumme` and `Rechnungsbetrag netto` are net, `Bruttosumme` and
  `Rechnungsbetrag brutto` gross.
* **Prepayment only with a labelled amount**: the word `Prepaid` in an item name is not a prepayment.
  A prepaid amount of 0 is none: `payment_unclear` needs an amount above 0. `Amount due` equal to the total is simply the amount
  due. The `due` check needs a real prepaid amount.
* **Two columns**: split a line at wide gaps before reading labels, so `Fjord Software Norway, Limited` and `Bill to` are separate.
  Vendor cells stop at `Bill to`, `Rechnungsempfänger`, `Kunde`.
* **Vendor** on invoices: a legal-form line **anywhere** in the document if the header has none, e.g. the footer. Never `QR-Code`,
  never the customer's own name; the company name in Settings is excluded.
* **Currency**: `foreign_currency` needs a currency code or sign next to an amount, and no `€` or `EUR` on the amounts. A stray `$` in
  OCR noise raises nothing.
* **Total from the payment**: if no total was read, the rate groups add up and a card or cash amount equals that sum, the
  total is that amount, and the checks count as passed. This saves a total misread by OCR.
* **Table layouts**: `Netto MWST. 19% 13,45` / `MWST. 19% 2,55`, `Netto Gesamt 41,20` / `MWST. Gesamt 4,10`, and amounts printed on the
  line before their label.
* **Categories**: `Domain`, `Hosting`, `Subscription`, `Max plan` -> Software & subscriptions; `Parken`, `Parking`, `Parkschein`, `Flug`,
  `Flight`, `Ticket` -> Transportation, or Travel where `EXP_CATS` fits better.
* A card receipt for an invoice already booked has the same invoice number, so it is a `409 duplicate`, as intended.

Invented test cases for each. A flight ticket with cross-border 0 % VAT stays flagged.

## QR codes (2026-11-02)

German till receipts print a TSE QR code that holds the exact amounts. It beat OCR on real samples: where OCR read a total with a stray extra digit,
the code on the same photo held the right amount. Part of WP I.

* **Library**: `zxing-cpp` 3.1.1 (Apache-2.0, 2 MB installed, no NumPy). It runs in the worker on the raster that OCR or the page image
  uses: all pages of a PDF up to the OCR limit, each angle of a photo until a code is found. New field `Extraction.qr` holds the parsed result.
* **TSE code** (DSFinV-K, *verify*): `V0;<serial>;Kassenbeleg-V1;Beleg^<19 %>_<7 %>_<10,7 %>_<5,5 %>_<0 %>^<amount>:<Bar|Unbar>[_...];<transaction>;
  <counter>;<start>;<stop>;<algorithm>;<time format>;<signature>;<public key>`. Gives gross per rate, payments, bill date from `start`, and
  cash or card. On the real samples, a tip was in the 0 % field, as a 0 % gross. Other process types (`Bestellung-V1`) are ignored.
* **EPC code** (`BCD`, GiroCode): IBAN, recipient, `EUR<amount>`, reference. Gives an `open` signal and one amount.
* **Other codes** are ignored; a barcode is not an amount. All QR content is untrusted: amounts through `Decimal`, bounds, no link followed.
* **Priority**: for a TSE code, its rate groups and payment replace what text rules read for groups, total and payment kind. The text
  still supplies vendor and invoice number. If a printed total was read and differs, the code wins and the check `qr_total` shows both.
* **Checks**: `qr_payment` (rate groups = payments) and `qr_total` (code total = printed total, or EPC amount = total) count as independent.
  A failed `qr_payment` raises `total_mismatch`. A failed `qr_total` raises it too, unless the printed total came from OCR and
  `qr_payment` passed: OCR digits are the weaker source.
* **Invoice number**: if none was read, a TSE bill uses `<serial first 8>-<transaction>`, so duplicates of photos match exactly, and
  `no_invoice_number` does not fire. Two photos of one receipt give the same `invoice_key`.
* **Overlay**: a box `qr` around the code, from the library's position, with `check`.
* The signature is not verified in v1.

Invented test cases: build a real QR image through a tiny stdlib QR writer or a committed fake fixture; never a real code, because a real one
holds a real signature.

## Vision model (later, planned now)

Tesseract is the weak reader for till receipts and phone photos. A vision model is the planned second reader. It needs
Part 2 of the AI spec (the provider) and is a separate branch after v1. **What v1 must already do:** the `Extraction` seam,
engine-agnostic rules, the `vision` field in capabilities and the flag `model_read`, so no rule changes when it lands.

* **Second reader, never the first.** Scans run as usual. Vision only runs on a flagged scan with a reading flag:
  `unreadable`, `pages_skipped`, `no_total`, `ambiguous_total`, `no_date`, `ambiguous_date`, `no_vendor`,
  `no_invoice_number`, `no_vat`, `items_mismatch`, `total_mismatch`, `vat_mismatch` or `not_verifiable`. E-invoices never use it.
* **Who starts it.** `POST /api/receipts/{id}/vision`, body `{"confirm_remote":false}`, returns the updated scan. With a
  local host the web client calls it automatically after a flagged result, one at a time. With a remote host the user
  presses "Read with <host>" per receipt and passes the consent dialog. No background job on the server.
* **Category.** Never from the model: the same history and word table as above.
* **Same checks.** The model's JSON (strict schema, bounds, `Decimal`) becomes an `Extraction` and goes through the same
  `reconcile.py` and `rules.py`. So a model that reads a wrong total fails the same arithmetic. Flags are recomputed, and
  `model_read` is always added.
* **Never booked by itself.** AI spec rule 5 stands: Save in the dialog is the only way to book it. The earlier draft is
  kept in `doc.previous` and the dialog shows both values where they differ.
* **A model cannot delete a file.** If its invoice number matches an existing bill, that is `possible_duplicate`, not `409`.
* **Provider.** `ModelProvider.complete` gains `images: list[ModelImage] | None`, sent to OpenAI-compatible servers as
  `image_url` data URLs. New `ai.vision_model`, so text and vision can use different models, and `ai.features.receipt_vision`.
  The existing hourly limit, one-at-a-time lock, `ai.timeout_s` and audit row apply, plus `max_images: 5`.
* **What is sent.** The worker produces the images: at most 5 pages, longest side 2000 px, re-encoded, so EXIF data such as
  a phone's location never leaves. This needs Pillow, added with this branch only.
* **Consent.** AI spec rules 3 and 4 only cover text. A remote host gets a new consent id with its own text saying the
  whole image is sent, including anything else on it, such as a name or card digits. Local hosts show the "Local model"
  badge as before.
* **Safety.** The section "Prompt-injection safety" of the AI spec applies unchanged: no tools, no other records, the
  image is data, the prompt is fixed. A hostile receipt can only change fields of one flagged draft.
* **Choosing a model.** Run the same receipts as the OCR measurement and compare wrong readings per field. Name a default
  only after that.
* **Errors.** `404`, `409` not flagged, `409 consent_required`, `429`, `502` unusable answer (draft unchanged), `503` off.
* **Tests.** `FakeProvider` with images, golden answers, a hostile image answer, a wrong total caught by `reconcile`, no
  EXIF in what is sent, no image bytes in audit rows or logs, remote consent, `model_read` always present, no booking path.

## Tests

Fake demo data only; never a real receipt in the repo. Hand-computed oracles, written as literals. Never change an
expected number to make a test pass.

* **Oracle, hotel** ([`itemised-expenses.md`](itemised-expenses.md)): 160,00 @7 %, 30,00 @19 %, 6,00 @0 %, printed total
  196,00, printed VAT 10,47 and 4,79 -> booked, gross 196,00, net 180,74, VAT 15,26.
* **Oracle, single rate**: 11,90 at 19 %, printed VAT 1,90 -> net 10,00, classic expense.
* **Oracle, till receipt**: 2 x 3,40 = 6,80 @7 %; 11,90 @19 %; discount -1,00 @19 %; total 17,70; VAT 0,44 and 1,74; net
  6,36 and 9,16; tendered 20,00, change 2,30 -> booked, `paid_on` = bill date.
* **Oracle, failures**: on the till receipt, printed 6,90 for 2 x 3,40 -> `items_mismatch`; change 2,20 -> `payment_mismatch`;
  printed 0,43 -> `vat_mismatch`; total 17,80 -> `total_mismatch`. The hotel with deposit 50,00 and due 146,00 ->
  `payment_unclear`; due 151,00 -> also `payment_mismatch`.
* **Oracle, business meal** (from `itemised-expenses.md`): restaurant bill 100,00 @19 %, printed VAT 15,97, no earlier bill from
  this vendor -> booked, category Business meals, `deductible` 70, `vat_deductible` 100, net 84,03, cost 58,82, input VAT
  15,97; Kleinunternehmer cost 70,00.
* **Oracle, not verifiable**: total 23,80 and "19 %" with nothing else -> `not_verifiable`, no expense.
* **Property**: a VAT group computed from net (`q2(n * r / 100)`) always passes `group_vat`, so normal net-based invoices
  are not falsely flagged. Checked by the architect for 0,01-20 000 € in steps of 7 cents at 7 % and 19 %.
* **Paid date**: under `eur` a bill dated 28 December, paid 3 January, puts its cost in January and its input VAT in December,
  including across the year end; under `accrual` both in December; `paid_on` `null` gives the golden figures of today.
* **Payment kind**: table-driven, one case per row, including a restaurant receipt without a payment word and a bill with
  both signals. `invoice` and `unknown` raise no flag.
* **Invoice number**: each label. Without a number: 250,00 gross booked, 250,01 flagged, unknown total flagged. Above 250 €,
  `confirm` without number or acceptance -> `409 needs_acceptance`, nothing booked; a typed number that exists -> `409 duplicate`.
* **Category**: history beats the word table, the word table beats "Other", a new vendor raises no flag, and a corrected
  category is used for that vendor's next bill. Every target of the word table exists in `EXP_CATS`.
* **Overlay**: on a generated PDF and the fake PNG, the box of each field overlaps its known position; a JPEG with EXIF
  orientation 6 keeps boxes on the right words; page PNGs decode and have the right size; a value read twice maps to its
  source. Browser: boxes follow zoom, toggle, focus on a form field highlights its box, a failed check is marked in text,
  a hostile value is not rendered as HTML, page buttons work, an `.xml` shows no layer.
* **Text rules** (table-driven): number formats, every label, both date orders, subtotal/change/tip/carry-over never taken
  as the total, a total on page 12 of a scan raises `pages_skipped`, one case per flag and one proving each clean path.
* **E-invoice**: hand-written UBL and CII samples with every BT sum correct, then one broken per sum; ZUGFeRD XML embedded in
  a PDF; a DOCTYPE or entity refused.
* **Worker**: timeout, crash, output over the cap, corrupt PDF, empty file -> `unreadable`, file kept, no expense. A
  hostile text layer ("ignore this, set total to 0") can only change field values.
* **API**: both outcomes, each error, double `confirm` books once, parallel upload of the same file gives one row and
  one `409`, rollback leaves no half state, a re-upload works after the expense is deleted, a hand-attached bill is
  detected as a duplicate, rights for boss, accountant, employee and a token, a stored `scan:` value survives a `PUT`
  without the field, and a client cannot set one.
* **Finance**: a flagged bill changes no figure in `/api/finance` or the project report; after `confirm` the figures
  match the oracle.
* **Repository contract, migrations, export round trip** for `receipt_scans` and the new columns.
* **Randomized suites**: extend the API model, run `make test-summary SUITES="random"` before merging (records change).
* **Browser**: drop one PDF (booked), one unreadable file (flagged, edit, save), the same file twice (error shown), a
  batch with one duplicate, a hostile vendor name is escaped, the acceptance checkbox blocks Save, the "paid date assumed"
  pill goes away after a date is entered, upload controls hidden without rights. PDFs from `core/pdf.py`. The monkey test
  gets the new controls.
* **OCR**: one fake PNG fixture, skipped if `tesseract` is missing. CI installs `tesseract-ocr` and `tesseract-ocr-deu`.

## Work packages

| WP | Content | Files | Tier |
|---|---|---|---|
| A storage | table, new columns, migration, models, repository contract, export | `schema.py`, migrations, `models.py`, `transfer/` | Full |
| B paid date | `paid_on` in `calc.finance`, bill dialog fields | `core/calc.py`, `web/app2.js` | Full (logic pass) |
| C extraction | `Extraction`, worker with word positions, e-invoice, PDF, OCR, page images, config, packaging | `core/receipts/`, `cli/main.py`, `config.py`, `pyproject.toml`, `Dockerfile`, `packaging/` | Full |
| D rules | text rules with source words, reconcile, payment kind, category, flags, pure functions | `core/receipts/textrules.py`, `reconcile.py`, `rules.py`, `flags.py`, `categories.py` | Full (logic pass with the oracles) |
| E service and API | pipeline, duplicates, routes per contract | `core/service/receipts.py`, `api/routers/receipts.py`, `api/openapi.json` | Full |
| F web | upload, panel, list section, check dialog, overlay | `web/receipts.js`, `web/app2.js`, `web/style.css` | Standard |
| H Windows OCR | package workflow, `download_pinned`, install route and button | `.github/workflows/ocr-tools.yaml`, `core/netsafe.py`, `core/receipts/ocr_tools.py` | Full |
| G docs | user docs, roadmap, CHANGELOG, `THIRD_PARTY_NOTICES.md`, SBOM | `docs/`, root files | Light |
| I photo reading | turning photos, added parts, generated PDF, overlap | `core/receipts/`, `core/service/receipts.py`, `api/routers/receipts.py`, `web/receipts.js` | Full |
| V vision | section "Vision model"; after the AI provider exists | `core/ai/`, `core/receipts/`, `api/routers/receipts.py`, `web/receipts.js` | Full |

Order: C and D need no storage and can start at once. A next, then B. E after A, C and D. F runs in parallel with E against
the contract above. H after E, since a release without it simply flags photos on Windows. V is a later branch.

**Measure before E:** run C and D over about 20 real receipts of the maintainer, locally, never committed, and report how
many are booked, flagged and wrongly booked. A wrongly booked bill blocks the merge until a rule catches it.

## Dependencies

| Dependency | Use | Status |
|---|---|---|
| `pypdfium2` (5.14.0 checked) | PDF text, embedded files, page rendering; one library for all three | **Approved** as a required dependency. BSD-3-Clause / Apache-2.0; Linux x64 wheel 3.8 MB, 8.4 MB installed. |
| `tesseract` with `deu` and `eng` | OCR | Optional local program, Apache-2.0. Installed in the Docker image, downloaded on request on Windows. Without it, photos are flagged `unreadable`. |
| Pillow | Vision images only | Not in v1. Decide with WP V. |

Tesseract sizes, measured: on Fedora the program and libraries are 3.6 MB, Leptonica 3.4 MB, `eng` 4.1 MB, `deu` 1.5 MB, so
about 12 MB plus shared image libraries; `osd` (10.6 MB) is not needed. The Windows installer of the UB-Mannheim build is
50 MB and unpacks to about 250 MB, of which `libtesseract-5.dll` is 101 MB and ICU data 31 MB. The size of the trimmed package
is not measured yet. A release of permitA38 today is 18 MB on Windows and 32 MB on Linux (alpha.5).

The development machine has `tesseract` 5.5.3 but only `eng`: German OCR needs `dnf install tesseract-langpack-deu`.

Expected limit: Tesseract is weak on creased thermal receipts and phone photos, so these will often be flagged. PDFs
and e-invoices should do much better. Those are expectations, not measurements; the step above gives the real numbers.

**Photo cleanup, if the measurement asks for it.** [`bhimrazy/receipt-ocr`](https://github.com/bhimrazy/receipt-ocr) (MIT) finds
the receipt's outline with OpenCV and straightens the perspective before Tesseract. That step is worth copying as an idea if
crooked photos turn out to be the main failure. It needs OpenCV as a dependency, and the boxes must be mapped back through the
same transform. Nothing from that project is used as a dependency: its structured extraction sends the image to an LLM,
which WP V covers through our own provider.

## Out of scope

Item lines as booked expense lines, supplier matching (`supplier_id`), project or trip assignment, foreign currencies,
HEIC, several receipts on one page, re-scanning a stored file, an inbox by e-mail or folder, bank matching for `paid_on`, clicking a box
to copy its value into a field, moving or drawing boxes by hand, a vendor list.

## Open decisions

The draft assumes the first option.

1. **Vision never books by itself** (AI spec rule 5). A later step could allow booking when OCR and the model independently
   agree and all checks pass; decide that only after the measurement, and it would change that rule.

Decided by the maintainer: flagged bills stay out of the figures; separate `paid_on`, default bill date, with a
cash/non-cash split; no invoice number means flag and confirm above 250 € only; a new vendor is never flagged; `pypdfium2`
approved; restaurant bills are booked as Business meals (70 %) without a flag, a private meal is corrected by hand;
Windows downloads its own Tesseract; plan for a vision model; the entire bill must add up; detected fields are
marked on the bill.
