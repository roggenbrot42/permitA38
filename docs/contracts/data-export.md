# API contract: data export and import

Source of truth for the backend, the web UI, the CLI and the tests, so that they can be built in parallel. Behaviour,
decisions and the test plan: [`docs/specs/data-export-import.md`](../specs/data-export-import.md). Where this file and
the spec differ on the HTTP shape, this file wins. Error format, auth and date conventions are those of
`docs/contracts/trips.md` ("Conventions"): `{"detail": "<message>"}` for 401/403/404/409/413 and service-level 422;
body/query validation errors are `{"detail": [{"loc": ["body","include_accounts"], "msg": "...", "type": "..."}]}`.
Times are epoch seconds (integers). Review tier: **Full**.

## Rights

Every route: **boss only**, **session only** (`Needs("settings", "write", full=True)`, like the document importer).
Employee and accountant: 403. No session: 401. When scoped API tokens exist, all routes below answer 403 to a token
whatever its scope (add them to the session-only list and test it).

Only one export or import runs at a time; a second request answers `409 {"detail": "Another export or import is running."}`.

## Routes

| Method and path | Purpose | Success |
|---|---|---|
| `GET /api/data/export/info` | what an export would contain, whether an import is possible | 200 JSON |
| `POST /api/data/export` | build and stream the archive | 200 `application/zip` |
| `POST /api/data/import/preview` | dry run: validate an archive, write nothing | 200 JSON (`ok` true or false) |
| `POST /api/data/import/run` | import into the empty instance, all-or-nothing | 200 JSON |
| `GET /api/data/transfers` | the transfer log (last 50) | 200 JSON list |

### `GET /api/data/export/info`

```json
{
  "counts": {"customers": 12, "projects": 9, "entries": 3041, "invoices": 87, "expenses": 140, "users": 2},
  "attachments": {"files": 211, "bytes": 48213377},
  "estimated_bytes": 61000000,
  "app_version": "0.4.0-alpha.2",
  "schema_head": "d8a69cde",
  "import": {"possible": false, "reason": "This instance already contains data (customers: 12, invoices: 87)."}
}
```

`counts` has one key per exported collection (derived from `schema.TABLES`, zero counts included).
`import.possible` is `true` with `reason: null` when the instance is empty in the sense of spec decision 8.
`estimated_bytes` is an upper-bound guess (uncompressed JSON + CSV + attachments), for the UI warning above 500 MB.

### `POST /api/data/export`

Body (`application/json`, strict; every field optional):

```json
{"scope": "full", "include_accounts": false, "include_attachments": true}
```

| Field | Type | Default | Rule |
|---|---|---|---|
| `scope` | `"full"` or `"master_data"` | `"full"` | enum |
| `include_accounts` | bool | `false` | adds `users.password_hash` and `pw_changed`; TOTP is never included. Ignored (and rejected with 422) for `master_data` |
| `include_attachments` | bool | `true` | `false` writes no `attachments/` entries; the manifest says so and import warns that referenced files are missing. Rejected for `master_data` (always none) |

Unknown fields: 422. Response: the ZIP stream with the headers

```
Content-Type: application/zip
Content-Disposition: attachment; filename="permita38-export-20261024-093015.zip"
Content-Length: 61003412
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Errors: 403/401/409 as above; 413 `{"detail": "The export would be larger than the limit (2 GiB uncompressed)."}`;
500 only for unexpected failures (the temporary file is removed).

### `POST /api/data/import/preview` and `POST /api/data/import/run`

`multipart/form-data` with one part `file` (the ZIP, at most 512 MiB; larger: `413 {"detail": "The file is larger
than 512 MiB."}`). `run` has one more form field `confirm` (bool, must be `true`, else `422 {"detail": "Confirmation
required."}`). No other fields.

Both routes first validate the **whole** archive (spec decisions 5, 6, 8, 11, 13). They differ only in what happens
afterwards.

**Preview** answers 200 whether or not the archive is acceptable:

```json
{
  "ok": true,
  "manifest": {
    "format": "permita38-export", "format_version": 1, "scope": "full",
    "app_version": "0.4.0-alpha.2", "schema_head": "d8a69cde",
    "created": "2026-10-24T09:30:15Z", "created_epoch": 1792834215,
    "include_accounts": false, "include_attachments": true
  },
  "compatibility": {"status": "same", "message": null},
  "counts": {"customers": 12, "projects": 9, "entries": 3041, "invoices": 87, "expenses": 140, "users": 2},
  "attachments": {"files": 211, "bytes": 48213377},
  "users": {"replaces_setup_user": "anna", "kept_setup_user": null, "without_password": ["lena", "tom"]},
  "numbering": {
    "invoices": {"highest_imported": "RE-2026-0087", "counter_period": "2026", "counter": 87, "next": "RE-2026-0088"},
    "offers": {"highest_imported": "AN-2026-0031", "counter_period": "2026", "counter": 31, "next": "AN-2026-0032"}
  },
  "warnings": [
    {"code": "dangling_reference", "message": "entries[41].project_id points to a missing project (a1b2c3d4).", "file": "data/entries.json"},
    {"code": "calendar_url_removed", "message": "Calendar links are not exported; reconnect 2 calendar sources after the import.", "file": null}
  ],
  "errors": []
}
```

`compatibility.status`: `same` (same `schema_head`), `older` (head is an ancestor in the chain; `message` says which
fields may be missing and will be empty), `newer` / `unknown` (error). With errors:

```json
{
  "ok": false,
  "manifest": null,
  "compatibility": {"status": "unknown", "message": "The archive was made by a newer permitA38 (schema 7ecb5a30). Update first."},
  "counts": {}, "attachments": {"files": 0, "bytes": 0}, "users": null, "numbering": null,
  "warnings": [],
  "errors": [{"code": "schema_unknown", "message": "The archive was made by a newer permitA38 (schema 7ecb5a30). Update first.", "file": "manifest.json"}]
}
```

`manifest` is `null` when it could not be read. At most 50 errors and 200 warnings are returned (`"truncated": true`
is added at the top level when more exist). Each item: `code` (stable, English snake case), `message` (English text
for people), `file` (archive path or `null`).

**Run** answers 200 only when the import was committed:

```json
{
  "imported": true,
  "counts": {"customers": 12, "projects": 9, "entries": 3041, "invoices": 87, "expenses": 140, "users": 2},
  "attachments": {"files": 211, "bytes": 48213377},
  "relogin": true,
  "users": {"replaces_setup_user": "anna", "kept_setup_user": null, "without_password": ["lena", "tom"]},
  "numbering": { "...": "as in the preview, plus the numbering check warnings" },
  "warnings": []
}
```

and `422 {"detail": "The archive was not imported: 2 errors.", "errors": [ ...same items as preview... ]}` when there
are errors (nothing was written), `409` when the instance is not empty (`detail` as `import.reason` above).
`relogin: true` when the session user row was replaced (spec decision 9); the UI sends the user to the login page.

### `GET /api/data/transfers`

```json
[
  {"at": 1792834215, "user_id": "a1b2c3d4", "action": "export", "ok": true, "include_accounts": false,
   "counts": {"customers": 12}, "archive_sha256": "9f2c...e1"},
  {"at": 1792830000, "user_id": "a1b2c3d4", "action": "import_preview", "ok": false, "include_accounts": null,
   "counts": {}, "archive_sha256": null}
]
```

`action` is one of `export`, `export_master_data`, `import_preview`, `import`. Newest first, at most 50. No file
contents, no file names.

## Error codes (`code` in preview/run items)

| Code | Meaning |
|---|---|
| `not_a_zip` | the file is not a ZIP or is truncated |
| `unsafe_entry` | a path, link, encryption or method that is not allowed (zip-slip etc.) |
| `limit_exceeded` | entries, size, ratio, records or depth over the limits (message names which) |
| `manifest_missing`, `manifest_invalid` | no or unreadable `manifest.json` |
| `format_unknown`, `format_too_new` | wrong `format` marker, or `format_version` newer than supported |
| `schema_unknown` | `schema_head` is not in this app's migration chain (newer or foreign) |
| `scope_not_importable` | master-data archive |
| `file_unlisted`, `file_missing`, `hash_mismatch` | archive content does not match the manifest |
| `json_invalid`, `record_invalid` | a data file or a record does not parse or fails validation (message names file and index) |
| `duplicate_id` | two records with the same id in one collection |
| `attachment_name_invalid`, `attachment_conflict` | name outside the allowed pattern, or a different file already there |
| `not_empty` | the instance already has data (also returned as 409 on run) |
| `dangling_reference` (warning) | a reference to a record that is not in the archive |
| `calendar_url_removed`, `attachments_not_included`, `users_without_password`, `numbering_counter_low`, `schema_older`, `setup_user_kept` (warnings) | informational |

## Archive format version 1

```
manifest.json
README.txt
data/<collection>.json   customers, projects, tasks, entries, contracts, absences, plan_scenarios, offers, invoices,
                         expenses, trips, users, templates, calendar_sources, timers, number_sequences, numbering_log,
                         import_batches (+ every future table not in EXCLUDED_TABLES)
data/settings.json
csv/<collection>.csv     for the record collections (not for internal tables, not for settings)
attachments/<name>
```

`manifest.json`:

```json
{
  "format": "permita38-export",
  "format_version": 1,
  "scope": "full",
  "app_version": "0.4.0-alpha.2",
  "schema_head": "d8a69cde",
  "created": "2026-10-24T09:30:15Z",
  "created_epoch": 1792834215,
  "include_accounts": false,
  "include_attachments": true,
  "counts": {"customers": 12, "projects": 9, "entries": 3041, "invoices": 87, "expenses": 140, "users": 2},
  "numbering": {"invoices": {"period": "2026", "last_value": 87}, "offers": {"period": "2026", "last_value": 31}},
  "excluded": [
    "secret key", "two-factor secrets and recovery codes", "API tokens", "calendar link URLs", "calendar events (cache)",
    "password hashes (not requested)"
  ],
  "files": {
    "data/customers.json": {"bytes": 5120, "sha256": "e3b0c442...b855"},
    "csv/customers.csv": {"bytes": 2210, "sha256": "..."},
    "attachments/9f3a1c2b7d.pdf": {"bytes": 183422, "sha256": "..."}
  }
}
```

`data/customers.json`:

```json
{
  "collection": "customers",
  "count": 1,
  "records": [
    {"id": "a1b2c3d4", "created": 1790000000, "updated": 1790000500, "name": "Beispiel GmbH", "city": "Köln",
     "rate": 95.0, "x_future_field": {"tags": ["a", "b"], "rank": 3}}
  ]
}
```

Unknown fields (`x_future_field`) are kept exactly. `data/settings.json`: `{"settings": {"company_name": "...", "invoice_pattern": "RE-{year}-{n:04d}", "...": "..."}}`.
`data/users.json` without "include accounts": records without `password_hash`, `pw_changed`, `totp_*`.
`calendar_sources` records have `"url": ""`.

CSV: UTF-8 with BOM, `;`, CRLF, header = columns of `schema.py` then unknown extra field names; strings that start
with `=`, `+`, `-`, `@`, TAB or CR are prefixed with `'`. Example (bytes after the BOM):

```
id;created;updated;name;customer_no;rate;x_note\r\n
a1b2c3d4;1790000000;1790000500;Beispiel GmbH;K-001;95.0;'=1+1\r\n
```

## Bounds (all enforced; see spec decision 13)

Upload 512 MiB; 20,000 entries; 2 GiB uncompressed total; 256 MiB per data file; 25 MiB per attachment; ratio 200
(entries under 1 MiB exempt); 1,000,000 records per collection; 1 MiB per record; JSON depth 32; `confirm` bool;
`ExportRequest` booleans and one enum only; echoed file names at most 200 characters.

## CLI

```
permita38 export FILE [--master-data] [--include-accounts] [--no-attachments] [--force]
permita38 import FILE [--dry-run] [--yes]
```

* `export` writes the archive to FILE (refuses to overwrite without `--force`; file mode `0600`; prints counts, size
  and the SHA-256) and writes the transfer-log entry. `--include-accounts` prints the same warning as the UI and
  needs `--yes` when not run on a terminal.
* `import` validates and prints the preview (counts, warnings, errors). `--dry-run` stops there (exit 0 when `ok`,
  1 otherwise). Without `--dry-run` it asks "Import now? [y/N]" unless `--yes`. Works on an empty database with no
  users (before first-run setup) and on the fresh instance described in spec decision 8. After a successful import
  it prints the numbering lines and, if any, the users without password with the hint `permita38 set-password`.
* Exit codes: 0 ok, 1 refused or failed, 2 usage error.

## UI behaviour (Settings > "Data export and import")

Visible only to the boss (menu entry hidden for others; the route shows nothing but a "not allowed" message).

1. **Load**: `GET /api/data/export/info` and `GET /api/data/transfers`. Show counts, attachment count and size.
2. **Export card**: checkbox "Include attachments (invoice PDFs, receipts)" (on); checkbox "Include user accounts
   and password hashes" (off) with the text "The archive then allows logging in as these users. Store it as carefully as
   the database. Two-factor secrets and API tokens are never included."; button "Download full archive" (POST,
   `fetch` with the session header, response to a Blob, saved as `permita38-export-<date>.zip`); button "Download
   customers and suppliers only". While running: button disabled with a spinner; on 409/403/413 show `detail`. If
   `estimated_bytes` is above 500 MB show "This archive is large; the browser keeps it in memory. Use
   `permita38 export` on the server instead."
3. **Notice** under the card (always): "This is a copy for moving and for your own safety. It is not a legally
   compliant archive (GoBD). Keep the original invoices and receipts as the law requires. Store the file encrypted.
   It contains personal data."
4. **Import card**: when `import.possible` is false, show `import.reason` and disable everything ("Import works only
   on an empty instance; to merge data into an existing instance use Import documents."). Otherwise: file input
   (`.zip`), button "Check file" (preview). The result panel shows: archive info (created, app version, scope),
   counts table, attachments, numbering ("Next invoice number will be RE-2026-0088. Check it on the numbering page"
   with a link), users (who replaces the setup account, who has no password), warnings (list, collapsible past 5)
   and errors (list, red; "Import" stays disabled when `ok` is false or `scope` is not `full`).
5. **Run**: checkbox "I understand that this instance will be filled with the data of this archive" enables the
   button "Import now"; click opens a confirmation dialog; on success a result panel (counts, numbering, users
   without password, warnings) and, if `relogin`, a "Log in again" button that clears the session and goes to the
   login page. On 422 show the errors, on 409 the reason; the page stays usable; nothing was imported.
6. **Transfer list**: table of the last transfers (time, user, action, ok, counts); no download of old archives (they
   are never stored).
7. Accessibility: buttons are real buttons, result panels `role="status"`, errors `role="alert"`, file input labelled.

## Work packages and the test list

See the spec ("Work packages", "Test plan"). The UI agent builds against the example JSON above; the backend agent
must make these examples pass as response-shape tests (`tests/backend/test_data_export.py` checks the key set of
every example in this file).
