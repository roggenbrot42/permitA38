# Data export and import

Move all data of a permitA38 instance to another instance (new host, new data folder) or keep a copy for your own
safety. Boss only. Contract: [`contracts/data-export.md`](contracts/data-export.md), design: [`specs/data-export-import.md`](specs/data-export-import.md).

## What is in the archive

One ZIP file with `manifest.json` (format version, schema version, counts, SHA-256 of every file), `README.txt`,
`data/<collection>.json` (every record, lossless, unknown fields included), `data/settings.json`,
`csv/<collection>.csv` (for spreadsheets, never read by the import) and `attachments/` (receipts, invoice PDFs).
The numbering counters and the numbering settings are included, so the next invoice number continues.

**Not in the archive:** the secret key, two-factor secrets and recovery codes, API tokens, calendar link URLs (the
calendar sources are exported without the link; reconnect them) and the calendar event cache. Password hashes are
only included when you tick "Include user accounts" (or pass `--include-accounts`); the archive then allows logging
in as these users, so store it as carefully as the database.

The CSV files are safe to open in a spreadsheet: text that would be read as a formula starts with a `'`. Excel
drops leading zeros of numeric-looking text when a CSV is opened by double click; use Data > From text/CSV.

## Export

* Web: Settings > Data export and import > "Download full archive" (or "customers and suppliers only").
* Command line: `permita38 export FILE [--master-data] [--include-accounts] [--no-attachments] [--force]`. The file
  gets mode 0600 and the SHA-256 is printed.

## Restore on a new instance

Import works only on an **empty** instance: finish the first-run setup (or, on the command line, start with no users
at all) and do not create other data.

1. Web: Settings > Data export and import > choose the file > "Check file" > read warnings and errors > tick the
   confirmation > "Import now". Command line: `permita38 import FILE --dry-run`, then `permita38 import FILE`.
2. The import is all-or-nothing: any error writes nothing.
3. If your setup account has the same username as an account in the archive, the archive's record replaces it (the
   ids of the old instance are kept) but the local password stays; log in again afterwards.
4. The import trusts password hashes only if the archive was exported with "include accounts" (the manifest says so);
   the result lists which accounts can log in and which of them are bosses. Users without a hash are inactive; activate them and set a password
   (`permita38 set-password <username>`). Two-factor is off for everyone; enrol again.
5. Check the numbering page: the result shows the next invoice and offer number. The import never lowers a counter.
6. Reconnect the calendar links.

A master-data archive cannot be imported; use Import documents with type customers (it accepts the JSON file).

Stop the web server before a command line import (`permita38 import`): the two do not coordinate. A final check inside
the write transaction still refuses the import (409) if data appeared in between.

Not exported: the running timer state (`timers`), calendar events, secrets and anything listed in the manifest `excluded`.

## Limits

Upload 512 MiB; 20,000 archive entries; 2 GiB uncompressed; 64 MiB per data file; 64 MiB per attachment on import;
compression ratio 200; 200,000 records per collection; 1 MiB per record. Only one export or import runs at a time.

## Not a legal archive

The ZIP is a convenience copy for moving and for your own safety. It is not unalterable, not signed and not time
stamped, so it is **not a GoBD-compliant archive**; the hashes only detect corruption. Keep the original invoices and
receipts as the law requires. The archive contains personal data: store it encrypted. Archives are never kept on the
server, and every export and import is listed under "Transfers" (time, user, action; no contents).
