# Importing existing documents

Use the import to bring invoices, offers, customers and expenses from another tool into permitA38. Start with a
dry run; nothing is written until you run the import for real.

```
permita38 import-documents rechnungen.csv --type invoices --dry-run
permita38 import-documents rechnungen.csv --type invoices --pdfs originals.zip
```

## Formats

- **CSV**: delimiter (`;`, `,`, tab or `|`) and encoding (UTF-8, UTF-8 with BOM, Windows-1252) are detected.
- **XLSX**: the first sheet, or `--sheet NAME`. Cached values are read; formulas are not evaluated.
- **permitA38 JSON**: a list of records, or an object with one list (for example the output of an export).

Limits: 50 MB per file, 50,000 rows, 20 MB per PDF.

## Columns

Columns are detected by their header names, ignoring case, umlauts, punctuation and spacing. German and English
names work, for example `Rechnungsnummer`, `Datum`, `Kunde`, `Netto`, `USt`, `Brutto`, `Fällig`, `bezahlt am`,
`Status`; `Invoice No`, `Invoice Date`, `Client`, `Net`, `VAT`, `Gross`, `Due date`, `Paid date`. A header is never
used for two fields, and unknown headers are reported, not guessed.

If detection gets a column wrong, pass a mapping file:

```json
{
  "columns": {"number": "Beleg-Nr.", "date": "Belegdatum", "customer_name": "Kunde", "net": "Netto", "gross": "Brutto"},
  "options": {"decimal": "auto", "date_order": "dmy", "on_duplicate": "skip", "accept_unverified": false}
}
```

Options: `decimal` (`auto`, `comma`, `point`; auto reads `1.234,56` and `1,234.56`), `date_order` (`dmy` for
`03/04/2026` read as 3 April, or `mdy`), `on_duplicate` (`skip` or `overwrite`), `accept_unverified`.

Community mapping presets are JSON files of this shape; the documented place for them is
`permita38/core/service/importer/presets/` (the folder is created with the first preset). No vendor-specific preset is
shipped, because the formats of other tools cannot be verified here.

## Invoices and offers

- The **document number is kept** exactly as in the file (`original_number`). Imported numbers are never renumbered.
- Dates use `YYYY-MM-DD` or `DD.MM.YYYY`. Dates must be between 2000 and 2100.
- Status: `draft`, `sent`, `paid`, `cancelled` (invoices) or `draft`, `sent`, `accepted`, `rejected` (offers). German
  words (`offen`, `bezahlt`, `storniert`, …) are accepted. Without a status column: `paid` if a paid date is given,
  otherwise `sent`.
- Only **EUR** is accepted for now.
- **Totals** are checked to the cent: net plus VAT must equal the gross, and the VAT must match the VAT rate
  (rounded half-up, as in [docs/validation.md](validation.md)). A row whose totals do not add up is an error. It can be
  imported with `accept_unverified`; it is then marked `totals_unverified` and keeps the given figures.
- **Lines**: without line columns each row becomes one summary line "Imported total". With line columns (`Position`,
  `Menge`, `Preis`) rows with the same document number form one document.
- After an import the next number follows the highest imported number of the current period.

## Customers

Customers are matched in this order: VAT ID, tax number, e-mail, customer number, then the normalised name (exact, or
similarity of at least 0.9; legal forms such as GmbH are ignored). A match is merged, never duplicated; the report
shows each decision with its similarity score. A customer that is not matched is created.

## Duplicates and repeated imports

- A document whose external ID or number is already in permitA38 is skipped (default).
- `overwrite` replaces only documents that an earlier import created. A manually entered document with the same
  number is an error.
- Importing the same file twice therefore creates nothing the second time.

## Original PDFs

Pass a zip of PDF files. A file matches a document when its name, without extension, equals the document number
(spaces, dashes and case are ignored). The original is stored with a SHA-256 checksum, and the invoice PDF endpoint
serves it instead of a new rendering. Unmatched PDFs are listed in the report.

## Dry run, all-or-nothing and undo

- The dry run and the import use the same code, so they report the same counts.
- An import with any error row writes nothing. Fix the rows (or the mapping) and run again.
- Each run is a batch. `GET /api/import/batches` lists them and `POST /api/import/batches/{id}/undo` removes the
  records of a batch. Undo is refused when a record of the batch changed or was deleted since the import, and for
  batches that overwrote documents. The number counter is not moved back.

## Expenses

Expenses need a date, a net amount (or gross and VAT) and optionally vendor, description, category and VAT rate.
Negative amounts are refused: refunds are credit notes.

## Security

Cell values are stored as text. A cell such as `=HYPERLINK(...)` is data, never a formula. Archives are checked for
their entry count, total unpacked size and compression ratio, and XLSX files with DTD or entity declarations are
refused.

## API

`POST /api/import/preview` (dry run) and `POST /api/import/run` take a multipart form with `file`, `type`
(`invoices`, `offers`, `customers`, `expenses`), and optionally `mapping`, `pdfs`, `on_duplicate`,
`accept_unverified`, `decimal`, `date_order`, `sheet`. The run answers **422** with the report when nothing was
imported.
