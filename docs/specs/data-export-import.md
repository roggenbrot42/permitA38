# Spec: full data export and import (restore / migrate to a new instance)

*Status: specification, decided by the architect model (see AGENTS.md "Division of labour"). Scheduled to be built
**today** (backend and web UI in parallel by separate agents), then Full-tier review. Review tier: **Full** (parsing
of outside input, uploads, rights, secrets, storage, numbering). HTTP shape, example JSON and error texts:
[`docs/contracts/data-export.md`](../contracts/data-export.md). Where the two differ on the HTTP shape, the contract
wins.*

## Why

The product owner wants to switch to a stable instance (new data folder / new host) and must not lose anything or
re-type anything. Later this is also the escape hatch for every adopter ("I can always get my data out"). The same
archive is the basis for later features: GDPR per-person export, the legal-retention backup module, DATEV/Z3 exports.

## Scope of v1

In:

1. **Full export** of all business data as **one ZIP archive**: manifest, one JSON file per collection (lossless,
   including unknown fields), one CSV per main collection for humans, all attachments with hashes, numbering
   counters and numbering configuration, non-secret settings.
2. **Master data export** (customers, and suppliers as soon as that collection exists) as a smaller archive of the
   same format.
3. **Import of a full archive into a fresh instance** (restore / migrate): validated completely first, then written
   in **one transaction**, ids preserved, attachments restored, numbering counters restored. Dry-run preview.
4. CLI: `permita38 export <file>` and `permita38 import <file>` for headless use.
5. Web UI in Settings (boss only).

Out of v1 (listed in the roadmap):

* **Merge-import into an existing instance** (id remapping, conflict handling). Roadmap: "later".
* **Import of a master-data archive** (use the existing document importer, `type=customers`, which accepts the JSON;
  see "Master data archive").
* **GDPR per-person export** (Art. 15/20): follow-up, reuses the archive writer with a per-person filter.
* **Legal archive**: this export is **not** a GoBD-compliant archive (see "GoBD and GDPR").
* Scheduled / encrypted / verified backups: the planned legal-retention backup module. `permita38 backup` (a
  consistent copy of the SQLite database) stays the tool for disaster recovery on the same engine.
* Encryption or signing of the archive (a note in the UI and docs tells the owner to store it encrypted).
* XLSX export (roadmap alpha.3), DATEV, Z3.

## Existing pieces this builds on

* `Repository` (`core/repository/base.py`): `list/get/save(touch=False)/save_many/transaction/settings/save_settings`,
  `save_attachment`, `attachment_path`. Records come back as flat dicts with the `extra` fields merged in, so
  `list(coll)` -> `save(coll, rec, touch=False)` is lossless (this is what makes unknown fields round-trip). Tables are
  described once in `core/repository/schema.py` (`TABLES`, `COLLECTIONS`, `INTERNAL_COLLECTIONS`).
* `core/database.head_id()` is the migration head id (8 hex characters, see `docs/specs/migration-ids.md`); it is the
  schema version of the archive.
* Document importer (`core/service/importer`, `api/routers/imports.py`): the pattern for dry run / run, upload
  limits, `Needs("settings", "write", full=True)` (boss only), `v.CheckedBody` for JSON bodies.
* Numbering service (`core/service/numbering.py`, `status`, `check`); counters in `number_sequences`, configuration
  in the settings keys `NUMBERING_KEYS` (`invoice_pattern`, `offer_pattern`, `invoice_reset`, `offer_reset`,
  `numbering_shared`).
* Attachments: files in `<data>/attachments/<id><ext>`, referenced by name from `expenses.attachment`,
  `invoices.original_pdf` / `offers.original_pdf` and the logo setting. Names match
  `[0-9a-z]{6,32}\.[a-z0-9]{1,5}` (enforced by `attachment_path`).
* The unreviewed WIP branch `origin/feat/export-gdpr` was read for ideas only (safezip limits, tabular writer). **No
  code is taken from it**; everything is written and reviewed fresh.

## Decisions (made by the architect, not open for the implementer)

1. **One format, one code path.** Export and import live in `permita38/core/service/transfer/` (`export.py`,
   `importer.py`, `archive.py` (zip reading/writing with limits), `csvout.py`, `manifest.py`, `upgrades.py`). The
   router and the CLI only call the service. No SQL anywhere: only `Repository` methods.
2. **Collections are derived from `schema.TABLES`, never hard-coded.** A guard test fails when a table exists that is
   neither exported nor listed in `EXCLUDED_TABLES` (with a reason). A new feature table is therefore exported by
   default and the author must decide consciously.
3. **Archive layout** (all paths are fixed, lower case, no user-controlled path part except attachment names, which
   match the attachment regex):

   ```
   manifest.json
   README.txt                       what this is, how to restore, the secrets list (plain text, English)
   data/<collection>.json           {"collection": "...", "count": N, "records": [ ... ]}  one file per collection
   data/settings.json               {"settings": {key: value}}   (non-secret keys only)
   csv/<collection>.csv             human use only; never read by the import
   attachments/<name>               original bytes
   ```
4. **Lossless JSON.** Records are written exactly as the repository returns them (flat, `extra` merged, so unknown
   fields survive), ids and `created`/`updated` preserved, keys sorted, UTF-8, `ensure_ascii=False`, floats as stored.
   Import writes with `save(..., touch=False)`.
5. **Integrity.** `manifest.files` lists every file of the archive (except `manifest.json`) with `bytes` and
   `sha256`. Hashes detect corruption and truncation, **not tampering** (no signature in v1; a note in the docs).
   Import refuses a missing, extra (not in the manifest) or mismatching file.
6. **Compatibility.** `format_version` is an integer, `1` now. Import refuses a larger `format_version` ("made by a
   newer permitA38, update first") and an unknown `format` marker. Older versions are read by a reader kept per
   version (only v1 exists). `schema_head` is the migration id: **accepted when it is this app's head or an ancestor
   in the migration chain** (then fields added later are simply absent and read as empty), **refused when unknown or
   newer** ("archive was made by a newer permitA38"). A later migration that renames or removes a column must add an
   upgrade function to `transfer/upgrades.py` (a dict `migration id -> function(records_by_collection)`, empty in
   v1); a test fails when a migration after the first release changes a column and has no entry or an explicit
   `NO_UPGRADE_NEEDED` marker. Older archives are therefore read through the chain, not refused.
7. **Secrets, decided once:**

   | Item | Exported? |
   |---|---|
   | Secret key (`secret_key`, `.secret_key`, config) | **Never.** Not part of the data folder content either. |
   | TOTP secrets, TOTP recovery codes, `totp_last_step` | **Never**, also not with "include accounts". On import every user has two-factor off; the owner re-enrols. |
   | API tokens (`api_tokens` table, once it exists) and the token audit log | **Never** (`EXCLUDED_TABLES`). |
   | Sessions / throttle state | Never (not data). |
   | Password hashes (`users.password_hash`, `pw_changed`) | **Only with the explicit option "include accounts".** Default off. With it on, the archive is as sensitive as the database (the UI says so). |
   | User profiles (name, username, role, rates, hours, colour, ...) | Always (entries etc. refer to users; without them the data is meaningless). Without "include accounts" `password_hash` is dropped. |
   | Calendar subscription URLs (`calendar_sources.url`; a "share via link" URL is a secret) | Blanked (`""`); sources are exported so the user can reconnect. `calendar_events` is a cache and is **not exported** (refresh after import). |
   | Running timers (`timers`) | Not exported (`EXCLUDED_TABLES`): transient state of single users. |
   | Settings | All keys except those matching the denylist regex `(?i)(secret|token|pass|cred|auth|api[_-]?key|private|key$)` and keys listed in `SECRET_SETTING_KEYS` (empty list in v1; a guard test checks that no stored key name matches the denylist without being excluded). The IBAN and tax ids are business data and **are** exported. |

   The manifest lists what was deliberately left out (`excluded`) so nobody believes the archive is complete in the
   security sense.
8. **Fresh instance only.** Import is allowed only into an instance with no business data: every table other than
   `users` and `settings` is empty, the attachments folder is empty, and `users` has at most one row (the account
   created by the first-run setup, who must be a boss and is the caller). Otherwise 409 with the counts found. The
   CLI additionally accepts a database with no users at all (before first-run setup).
9. **User rows on import.**
   * Archive users are written with their archive ids (links stay valid).
   * If the instance has the setup user and the archive has a user with the **same username**, that archive row
     replaces the setup row (archive id) but **keeps the local `password_hash`, `pw_changed` and role boss**, so the
     importing owner can still log in. The response says `relogin: true` (the session id changes; the user logs in
     again).
   * If no username matches, the setup user is **kept** next to the archive users (own id) and a warning says so.
   * Archive users without a password hash (archive made without "include accounts") are written as `active=false`
     with an empty hash and listed in the result (`users_without_password`); the owner activates them and sets a
     password (user administration, `permita38 set-password`). `totp_enabled` is false for everyone.
   * The import enforces `manifest.include_accounts`: when it is not `true`, `password_hash` and `pw_changed` are dropped
     from every archive user (they land inactive, listed under `users_without_password`); when it is `true` the hash
     must have the shape `pbkdf2_sha256$<iterations>$<hex salt>$<hex digest>`. All `totp*` keys are always stripped.
     The user is matched to the setup account by casefolded username. The result lists `with_login` and `bosses` (the
     setup account counts) and warns `accounts_can_login` when imported accounts can log in.
   * Unique indexes of `schema.py` are pre-checked for the whole archive (usernames also case-insensitively); roles
     must be known (`member` is mapped to `employee` by the upgrade hook of migration 0006); NaN/Infinity and
     out-of-range floats are refused.
10. **All-or-nothing.** Order: (1) read and fully validate the whole archive in memory/temp files (limits, hashes,
    JSON, per-record validation, references, empty-instance check); only if there are no errors (2) stream
    the attachments (hash and magic bytes checked again) into a staging folder `<attachments>/.import-<random>/`,
    then (3) inside **one** `store.transaction()` re-check that the instance is still empty (409 otherwise) and write
    all records, settings and counters; (4) only after the commit the staged files are renamed into place. On any
    exception or interrupt the transaction rolls back and the staging folder is removed; a crash leaves only a
    staging folder, which the instance ignores and the next start removes after an hour. Nothing is partially imported. Only after the commit the result is
    reported and one entry is added to the transfer log (decision 14).
11. **Id preservation.** Ids are never regenerated. A reference column (`customer_id`, `project_id`, `task_id`,
    `user_id`, `invoice_id`, `offer_id`, `trip_id`, `source_id`, and the list columns `member_ids` / `user_ids`)
    pointing to a missing record is a **warning** (reported by preview and run, import proceeds). The mapping
    column -> collection is a table in `transfer/importer.py`, covered by a test that fails when `schema.py` gets a
    new `id` column that is missing in the table. An archive written by the app has zero dangling references (round-
    trip test asserts this).
12. **Numbering continues.** `number_sequences` rows and the numbering settings are restored exactly. After the
    import the service runs the numbering `check` for invoices and offers and returns its warnings plus, per document
    type, the highest number found in the imported documents next to the counter, so the owner sees "next number will
    be X". The import never recomputes or lowers a counter. The UI shows the result with a link to the numbering page
    (see the adopter checklist in the docs).
13. **Limits (zip-slip, zip-bomb, resource use), enforced on upload and again while reading:**

    | Limit | Value |
    |---|---|
    | Upload size (archive) | 512 MiB |
    | Entries in the archive | 20,000 |
    | Total uncompressed size | 2 GiB |
    | Uncompressed size of one data file (`data/*.json`) | 64 MiB |
    | One attachment | 64 MiB per entry on import; no limit on export except the 2 GiB total |
    | Compression ratio per entry (uncompressed / compressed) | 200 (entries under 1 MiB uncompressed are exempt) |
    | Records per collection | 200,000 |
    | One record serialised | 1 MiB |
    | Depth of nested JSON | 32 (parser guard) |

    Declared sizes in the zip directory are **not trusted**: entries are read in chunks with a running counter that
    aborts at the limit. Entry names must be exactly one of the allowed shapes (`manifest.json`, `README.txt`,
    `data/<known collection>.json`, `data/settings.json`, `csv/<known collection>.csv`, `attachments/<name matching
    the attachment regex>`); anything else (absolute paths, `..`, backslashes, drive letters, NUL, duplicates after
    case folding, symlinks / entries with Unix mode of a link, directories with content, encrypted entries, a
    compression method other than stored/deflate) is **rejected as a whole**, never "skipped". Files are never
    extracted to disk by path from the archive: attachments are read from the zip and written through the
    repository (`stage_attachment`, which validates the name again). `zipfile` is used with the sizes checked
    before reading; no `extractall`.
14. **Audit.** Every export and every import (also a failed or refused attempt, and a dry run) writes one entry to the
    settings key `data_transfer_log` (list, newest first, capped at 50): `at`, `user_id`, `action`
    (`export`, `export_master_data`, `import_preview`, `import`), `ok`, `include_accounts`, `counts`, `archive_sha256`
    (of the file, export only), no contents. Plus a line in the application log (`permita38.audit`). (The token
    audit table from the API-token spec is not on `main`; when it lands, this may move there. No migration is needed
    today.) `GET /api/data/transfers` shows the log to the boss. The log key is excluded from the export.
15. **Rights.** Boss only: `Needs("settings", "write", full=True)` on every route (the same dependency the document
    importer uses). Employees and accountants get 403 on all routes, including the info route. **Session-only**: when
    the scoped API tokens land, the four routes are added to the session-only list ("these answer 403 for a token
    whatever its scope", API-token spec decision 9) and a test covers it. Until then the routes simply do not accept
    anything else than the session.
16. **Streaming download.** The archive is built into a temporary file under `<data>/tmp/` (mode 0600, created with
    `tempfile`, deleted after the response, stale files removed at startup), then streamed in 1 MiB chunks with
    `Content-Disposition: attachment; filename="permita38-export-YYYYMMDD-HHMMSS.zip"` (fixed pattern, no user
    data), `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Content-Length`. Reading the repository
    happens inside one transaction so the export is a consistent snapshot. Only **one export or import at a time**
    (process-wide lock): a second request gets 409 "another export or import is running".
17. **Request validation.** The export options body is a strict `v.CheckedBody` model (`ExportRequest`, extra
    forbidden, booleans and an enum only). Upload routes are multipart with one file plus fixed form fields with
    hard bounds (`docs/validation.md`). No client-controlled file names are used anywhere; the uploaded file name is
    only echoed (truncated to 200 characters, control characters removed).
18. **CSV for humans** (`csv/<collection>.csv`, written for: customers, projects, tasks, entries, contracts, absences,
    offers, invoices, expenses, trips, users, templates and every future record collection that is not internal;
    not for internal tables):
    * UTF-8 **with BOM**, delimiter `;`, line end `\r\n`, quoting per RFC 4180 (fields containing `;`, quotes or
      line breaks are quoted), header row = column names of `schema.py` followed by the names of unknown `extra`
      fields (union over all records, sorted), booleans `true`/`false`, numbers with a decimal **point** and no
      thousands separator, dates as stored (ISO), JSON columns (`lines`, `plan`, ...) as compact JSON text.
    * **Formula-injection safe**: every *string* value whose first character (after leading whitespace) is `=`, `+`,
      `-`, `@`, TAB or CR is prefixed with a single quote `'`. Real numbers are written as numbers and are never
      prefixed (so `-12.50` stays a number). The same function is applied to header names. The CSV is never read
      back, so the prefix cannot corrupt a restore; JSON is the source of truth.
    * No password hashes, no TOTP columns, no secrets (same column filter as the JSON).
    * Known limitation, documented: Excel drops leading zeros of numeric-looking text (`0042`) when a CSV is opened
      by double click; use "Data > From text/CSV" and set the column to text.
19. **No migration.** v1 adds no table and no column. The repository gets two small methods (decision 20).
20. **Repository additions** (storage layer only, contract tested in `tests/backend/test_repository_contract.py`):
    * `list_attachments() -> list[str]`: names in the attachments folder (only names matching the regex).
    * `restore_attachment(name: str, blob: bytes) -> None`: validates the name against the attachment regex, refuses
      to overwrite an existing file with different content (same content is fine), writes atomically (tmp + replace),
      like `save_attachment`. Also `attachment_path` stays the way to read; export reads bytes in chunks via it.
    * `new_stage() / stage_attachment / commit_stage / discard_stage / discard_stale_stages`: the staging area of the import.
    * `delete_attachment(name: str) -> bool` for the compensating cleanup and the tests.
21. **Master data archive.** `scope: "master_data"` writes `manifest.json` (with `"scope": "master_data"`),
    `data/customers.json` (and `data/suppliers.json` when that collection exists, see
    [`subcontractors.md`](subcontractors.md)), and the matching CSVs. No attachments, no settings, no numbering, no
    users. Import refuses it ("this archive contains master data only; use Import documents with type customers").
    The customers JSON file has the shape `{"collection", "count", "records"}`; the document importer already accepts
    "an object with one list", the implementer checks that the extra keys do not confuse it and, if they do, adapts
    the importer to accept this shape (small, covered by a test).
22. **Dry run.** `POST /api/data/import/preview` runs the whole validation of step (1) and returns the manifest
    summary, counts per collection, what would happen to users, the warnings, and the errors. It writes nothing
    except the transfer-log entry. `run` repeats the full validation (the file is uploaded again; simple, no
    server-side staging) and refuses on any error.

## GoBD and GDPR

* **Not a legal archive.** Retention duties (accounting vouchers and invoices; currently 8 years for booking
  vouchers and 6 years for commercial letters *(verify against the current law before quoting in the docs)*)
  require an unalterable, machine-readable, verifiable archive with a procedure documentation (*Verfahrensdokumentation*).
  The ZIP is a **convenience copy for migration and portability**: it can be edited by anyone, has no signature and
  no timestamp authority. The README in the archive, the UI text and `docs/data-export.md` say this in one sentence
  and point to the planned legal-retention backup module (alpha.4) and the GoBD/Z3 items on the roadmap. The
  owner keeps the retention duty on the **originals** (invoice PDFs, receipts) either way; the export includes them
  but the instance is not a substitute for a compliant archive until that module exists.
* **Personal data.** The archive contains personal data of customers, employees and possibly subcontractors
  (names, addresses, hours, absences). It is created only on the boss's request, never stored on the server (the
  temporary file is deleted), logged (decision 14) and the UI warns to store it encrypted. Erasure requests cannot
  be applied to an archive that already left the server: documented. **GDPR per-person export (Art. 15/20) is a
  follow-up** (roadmap, alpha.4 with the GDPR tooling): filter by customer or user, same writer, same CSV rules.

## Web UI behaviour (Settings > "Data export and import", boss only; not shown to other roles)

Detailed states are in the contract. Summary: an "Export" card (checkboxes "include attachments" (default on),
"include user accounts and password hashes" (default off, with a warning text), button "Download archive", button
"Download customers and suppliers only", a size/count estimate from `/api/data/export/info`), an "Import" card
(disabled with the reason when the instance is not empty), file picker, "Check file" (dry run) showing counts,
warnings and errors, then "Import" behind a checkbox "I understand that the data of this instance will be set from
this archive" and a confirmation dialog; after success a result panel (counts, next numbers, users without password,
"log in again"). A list of the last transfers. All text goes through the translation hook the UI already uses;
errors show the server `detail`.

## Work packages (to be assigned today; B and U in parallel against the contract)

| WP | Owner | Content | Files (exclusive) |
|---|---|---|---|
| B1 | backend agent 1 | Repository: `list_attachments`, `restore_attachment`, `delete_attachment` + contract tests | `core/repository/base.py`, `sql.py`, `sqlite.py`, `tests/backend/test_repository_contract.py` |
| B2 | backend agent 1 | Archive writer: `transfer/export.py`, `csvout.py`, `manifest.py`, secret filtering, master-data scope, temp file, limits constants | `core/service/transfer/{__init__,export,csvout,manifest,limits}.py` |
| B3 | backend agent 2 | Archive reader + importer: `transfer/archive.py` (safe zip), `importer.py` (validation, references, users rule, transaction, cleanup, numbering check), `upgrades.py` | `core/service/transfer/{archive,importer,upgrades}.py` |
| B4 | backend agent 2 | API router `api/routers/data_transfer.py`, `ExportRequest` model, services wiring, rights, lock, transfer log, `api/openapi.json` regeneration, CLI `export` / `import` | `api/routers/data_transfer.py`, `api/deps.py` (wiring only), `core/models.py`, `cli/`, `api/openapi.json` |
| U1 | UI agent | Settings section, export card, import card with preview/result states, transfer list, role gating, i18n keys; works against a stub of the contract until B4 lands | `web/` (new `web/data-transfer.js` + the settings page hook) |
| T1 | test agent | Backend tests (list below) | `tests/backend/test_data_export.py`, `test_data_import.py`, `test_data_transfer_security.py`, `randomized/test_export_roundtrip.py` (property) |
| T2 | test agent | Playwright tests | `tests/web/test_data_transfer.py` |
| D1 | docs agent | `docs/data-export.md` (user guide, restore steps, what is and is not in the archive, GoBD note), link from `docs/adopting.md` and `docs/index` pages, README of the archive text, `docs/roles.md` untouched (the matrix is unchanged) | `docs/data-export.md` |

Order of merging: B1 first (small), then B2+B3+B4, then U1/T2. B and U do not wait for each other: the contract has
complete example responses.

## Test plan

Backend (T1), each of these must fail if the code were wrong:

1. **Round trip (property test, hypothesis).** Generate data through the repository: customers, projects, tasks,
   entries, contracts, offers, invoices (with `lines`), expenses with attachments, trips, users, templates,
   number sequences, settings; every record gets random **unknown extra fields** (nested lists/dicts, numbers,
   booleans, null), unicode (umlauts, emoji, RTL, combining characters, 4-byte code points; no lone surrogates),
   `;`/quote/newline characters, strings starting with `= + - @`. Cases include **zero records everywhere**, one
   **big attachment** (24 MiB, fixed test, not hypothesis) and many small ones. Procedure: export A from instance 1
   -> import into fresh instance 2 -> export B. Assert A and B are **semantically identical**: all
   `data/*.json` equal after sorting records by id (compared as parsed JSON), attachment bytes and hashes equal,
   `number_sequences` equal, settings equal; ignored: manifest `created`, `app_version`; with "include accounts" off
   the setup user rule (decision 9) is the only allowed difference and is asserted explicitly. Assert the dangling
   reference warnings are empty and the next generated invoice number on instance 2 equals the one instance 1 would
   have generated.
2. **Secrets**: a user with password hash, TOTP secret, recovery codes, and a calendar source with a URL: the archive
   (all files, searched as raw bytes, also inside CSV) contains none of the TOTP values, never the secret key, the URL
   is blanked; with "include accounts" the hash is present and TOTP still absent; a setting named `x_api_key` is
   excluded; guard test for `EXCLUDED_TABLES` / settings denylist.
3. **Guard test**: every table in `schema.TABLES` is exported or in `EXCLUDED_TABLES`; every `id` column is in the
   reference table.
4. **Manifest**: counts equal real counts; every file hash matches; `schema_head == head_id()`; `excluded` present.
5. **Import refusals** (each its own test, HTTP 422/409 with the contract text, and **the instance unchanged**
   afterwards, compared via a full export before/after): non-empty instance, newer `format_version`, wrong `format`
   marker, unknown/newer `schema_head`, ancestor `schema_head` accepted with the warning, missing manifest, file not in
   manifest, hash mismatch (flip one byte), truncated zip, not a zip, JSON invalid, record without id, duplicate ids,
   record too large, bad attachment name, attachment hash mismatch, master-data archive.
6. **Atomicity**: inject a failure after N records (monkeypatched `save`) -> database unchanged and the attachment
   folder empty again; inject a failure while writing attachments -> same.
7. **Zip safety**: zip-slip names (`../x`, `/abs`, `a\..\b`, `C:evil`, NUL, `data/../manifest.json`), a symlink entry,
   duplicate entries, case-fold duplicates, an encrypted entry, too many entries, an entry whose declared size lies
   (declared small, real large) aborts at the limit, a 1 GiB zeros bomb (ratio) rejected quickly with bounded memory
   (assert peak by reading through a counting stream), nested depth bomb in JSON, 1,000,001 records.
8. **CSV**: golden test of the exact bytes (BOM, `;`, CRLF); formula injection for each of `= + - @ \t \r` as first
   character, also after leading spaces and in headers; numbers not prefixed; quoting of `;`, quotes, newlines;
   extra-field columns; no secret columns.
9. **Rights/API**: employee and accountant get 403 on all routes; unauthenticated 401; boss ok; body validation
   (unknown field in `ExportRequest` -> 422); upload over the size limit -> 413; concurrent second request -> 409;
   response headers; the temporary file is gone afterwards (also after an aborted import); transfer log entries for
   success, refusal and dry run, containing no contents; the log key is not in the export. (Token 403: added with the
   API-token branch.)
10. **Users rule**: same username keeps the local password and role, id becomes the archive id, `relogin: true`;
    different username keeps both; archive users without hash are inactive; TOTP is off for all.
11. **Numbering**: archive with invoices `RE-2026-0007` and counter 7 -> the next created invoice is `-0008`; the
    result lists the highest imported number per type; a counter lower than the highest imported number produces the
    numbering warning.
12. **CLI**: `export` then `import --dry-run` then `import` against a temp data dir; refusal to overwrite an existing
    file without `--force`; output file mode `0600`; exit codes (0 ok, 1 error, 2 usage).
13. **Master data**: only customers (and suppliers if present) in the archive, no attachments, no users; the document
    importer accepts the JSON.
14. **Streaming**: a large export does not hold the whole archive in memory (assert via tracemalloc peak under a
    bound with a 24 MiB attachment).

Web (T2, Playwright, fake demo data only): boss sees the section, accountant/employee do not (no menu entry, direct
route shows no section); export download yields a zip whose manifest has the expected counts; "include accounts"
shows the warning; import card disabled on a non-empty instance with the reason; on an empty instance dry run shows
counts and warnings, errors disable "Import", import needs the checkbox and the confirmation, result panel shows next
numbers and "log in again"; a refused (corrupt) archive shows the server message and nothing changes. One seeded
random e2e step is optional.

Review (Full, three Sonnet 5.5 passes, AGENTS.md "Merge gate"): (1) code: zip handling, limits, secret filtering,
transaction and cleanup, rights; (2) tests: would each test fail if the code were wrong (especially secrets, zip
attacks, atomicity, round trip with extras); (3) logic: independent oracle for the round trip (compare against the
raw database rows / `sqlite3` dump in the test, not only against the exporter), numbering continuation, counts, the
users rule, CSV bytes. The logic pass is not done by a smaller model.

## Open design decisions (simplest option chosen; the reviewer confirms or changes before the merge)

| # | Question | Chosen (simplest) | Alternative |
|---|---|---|---|
| O1 | Upload once and stage on the server between preview and run? | No: upload twice, full re-validation | server-side staging with a token |
| O2 | Is `calendar_events` exported? | No (cache); sources exported without URL | export events too |
| O3 | Archive signature / encryption | None in v1; hashes against corruption only | age/GPG encryption, HMAC with a user password |
| O4 | Dangling references | Warning, import proceeds | error |
| O5 | Where is the transfer log kept | settings key `data_transfer_log` (no migration) | audit table after the API-token migration |
| O6 | Excel leading zeros | Documented limitation | write `="0042"` (breaks other tools) |
| O7 | Master data as ZIP only | ZIP only | also bare CSV/JSON download |
| O8 | Browser download of a large archive | `fetch` + blob (needs the Authorization header); the UI warns above 500 MB | one-time download ticket URL |
| O9 | Memory: collections are loaded as lists | yes, 200,000 records/collection cap; each data file is parsed once and dropped | cursor paging in the repository |
| O10 | Exporting `import_batches` | yes (undo history continues) | skip |

## Roadmap follow-ups (not in v1)

Merge-import into an existing instance (id remapping, conflict policy); GDPR per-person export; encrypted
archives; scheduled exports; XLSX; GoBD-grade archive with the legal-retention backup module; DATEV / Z3; invoice lines
as their own CSV; importing master-data archives directly.
