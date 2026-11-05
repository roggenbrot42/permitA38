# Spec: database integrity check (`permita38 check-db`)

Status: **spec, ready for implementation** (2026-11-05). Review tier: **Standard** (advisory, read-only; touches no
money computation and writes nothing). Follow-up: [`db-constraints.md`](db-constraints.md) adds the constraints that
make the most important findings impossible for new writes.

## Why

The database enforces almost nothing: no NOT NULL, no foreign keys, no CHECKs beyond `allocations_one_owner`, unique
indexes only on `users.username`, `number_sequences(doc_type, period)`, `api_tokens.token_hash`,
`receipt_scans.sha256` and `document_scans.sha256`. Every rule lives in the `*In` models and the services. Data that
reached the file another way (an older version, the importers, a sync conflict, a manual fix, a bug) is never
re-checked. `docs/sanity-checks.md` lists the missing "data health report over stored records".

`check-db` is that report: it reads every stored record, reports what breaks the rules, and **never writes**.

## Command

```
permita38 check-db [--json] [--only CODE_PREFIX ...] [--config ...] [--data ...]
```

- Text output: one line per finding, `SEVERITY  code  collection/id  field  message`, then a summary line
  `N errors, M warnings, K info`. `--json` prints `{"findings": [...], "summary": {...}}` instead.
- `--only` keeps findings whose code starts with one of the prefixes (e.g. `--only numbering. ref.`).
- Exit code: `0` no errors (warnings and info allowed), `1` at least one error, `2` the check could not run (pending
  migrations, newer database, unreadable data folder).
- Messages contain **ids, codes and field names only**: never names, amounts, addresses or document text. A document
  number may appear (the user needs it to find the document); nothing else from the record.

## Opening the database

- The CLI stays thin: it builds the services as the other commands do and calls `IntegrityService(store).run()`.
- Before anything else, compare `store.health()` `revision` with `current_revision`. If they differ, print
  "The database is at revision X, expected Y. Run `permita38 migrate` first." and exit 2. `check-db` never migrates
  (it must also work as a pre-flight before a migration, see `db-constraints.md`).
- All reads run inside **one** `store.transaction()`, so the report is a consistent snapshot. Nothing is written in
  that transaction. The database file must be byte-for-byte unchanged afterwards (test, see below).

## Module

`permita38/core/service/integrity.py`, Repository interface only (no SQL, no `sqlite3`).

```python
@dataclass(frozen=True)
class Finding:
    code: str  # e.g. "numbering.duplicate"
    severity: str  # "error" | "warning" | "info"
    collection: str | None
    id: str | None
    field: str | None
    message: str


class IntegrityService:
    def __init__(self, store: Repository): ...
    def run(self, only: Sequence[str] = ()) -> list[Finding]: ...  # sorted by (severity rank, code, collection, id)
```

Each rule is a small function `(ctx) -> Iterable[Finding]`, registered in a list. A rule that raises is reported as
one finding `check.crashed` (error, message = rule name and exception type only) and the run continues. `ctx` loads
every collection once (`store.list`) plus `store.settings()`.

Prerequisite refactor (no behaviour change):

- Move the collection -> (response model, request model) map from `permita38/api/routers/records.py` into
  `permita38/core/models.py` as `RECORD_MODELS`; the router imports it from there.
- Move the "does this value fit the column type" test out of `SQLRepository._coerce` into a pure function
  `fits(column_type: str, value) -> bool` in `permita38/core/repository/schema.py`; `_coerce` calls it. Same results
  as today (the existing repository tests prove it).

## Rules

Severities: **error** = breaks a legal or accounting rule, or a state the services cannot produce; **warning** = an
inconsistency a person should look at; **info** = probably fine, listed for completeness.

### 1. Storage types (`type.*`)

| Code | Severity | Rule |
|---|---|---|
| `type.misplaced` | error | A decoded record has a key that **is** a column of its table, but the value does not `fits()` the column type. Such a value can only have come through the `extra` fallback, so the column is NULL and indexes and filters miss it. |

Keys that are **not** columns are legitimate (forward compatibility). Never report them.

### 2. Strict model (`model.*`)

For each collection in `RECORD_MODELS`, validate the stored record with its request (`*In`) model after removing the
server-owned keys `id`, `created`, `updated`. Each pydantic error becomes one finding:

| Code | Severity | Rule |
|---|---|---|
| `model.invalid` | warning | `field` = the dotted `loc`, `message` = the pydantic `msg` (no input value). |

This keeps format, range and vocabulary rules identical to the API, with no second copy. **Warning**, not error:
older data may predate a tighter rule. If a stored server-owned field (e.g. `number`, the importer fields) makes a
model fail, add it to a per-collection list of ignored keys, with a comment saying why, and list it under "Open design
decisions".

For the tables without a CRUD model (`allocations`, `suppliers`, `incoming_offers`, `trips`, `users`,
`receipt_scans`, `document_scans`), check only the date columns (`model.date_format`, warning): `start`, `end`,
`cancelled_from` (allocations), `received_on`, `valid_until` (incoming offers),
`tracking_start` (users). Use `validation._iso_date` (make it public as `iso_date` if needed). Trips `start`/`end` are local datetimes
(`YYYY-MM-DDTHH:MM`): check them with `validation.local_datetime`. Do **not** treat
`entries.start`/`end` as dates: they are clock times.

### 3. References (`ref.*`)

A reference value that is set (not None, not `""`) must point to an existing record.

| Column(s) | Target |
|---|---|
| `customer_id` | customers |
| `project_id` | projects |
| `task_id` | tasks |
| `invoice_id` | invoices |
| `offer_id` | offers |
| `contract_id` | contracts |
| `supplier_id` | suppliers |
| `trip_id` | trips |
| `allocation_id` | allocations |
| `incoming_offer_id` | incoming_offers |
| `expense_id` | expenses |
| `source_id` | calendar_sources |
| `token_id` | api_tokens |
| `user_id`, `login_user_id`, `decided_by`, `created_by` | users |
| `member_ids`, `user_ids` (JSON lists) | users, each element |
| `import_batch`, `document_scans.batch_id` | import_batches |
| `document_scans.record_id` | the collection named in `record_coll` |
| `template_id` | templates, unless it names a built-in template (find how the services resolve a template id and accept exactly those) |

| Code | Severity | Rule |
|---|---|---|
| `ref.dangling` | error for `entries.invoice_id`, `invoices.customer_id`, `expenses.trip_id`, `receipt_scans.expense_id`, `document_scans.record_id`; warning otherwise | target missing |
| `ref.dangling_audit` | info | `audit_log.token_id` / `user_id` missing (the log is append-only, targets may be gone legitimately) |
| `ref.self_project_customer` | warning | a record has both `project_id` and `customer_id` and the project's `customer_id` is set and differs |

Values the planner uses as scopes (`"unassigned"`) are not references: skip them where they can occur. Not in v1:
`expenses.trip_item_id` (points into the trip's JSON); list it under "Not covered".

### 4. Numbering (`numbering.*`)

Use `core/numbering.py` (`parse_pattern(strict=False)`, `Pattern.match`, `date_consistent`, `period_key`,
`normalize_text`) and the effective settings. "Numbering scope" = `invoices` and `offers` separately, or both together
when `numbering_shared` is set.

| Code | Severity | Rule |
|---|---|---|
| `numbering.duplicate` | error | two documents in one numbering scope have the same `number` (exact string). One finding per document involved. |
| `numbering.duplicate_similar` | warning | numbers differ but are equal after `normalize_text` (and are not exact duplicates). |
| `numbering.draft_with_number` | error | `status == "draft"` but `number` is set. |
| `numbering.final_without_number` | error | invoices only: `status` in `sent`, `paid`, `cancelled` and no `number`. |
| `numbering.sequence_behind` | warning | a `number_sequences` row has `last_value` below the highest sequence number that the current pattern matches (`match` + `date_consistent`) in its period. (Allocation skips taken numbers, so nothing breaks; the counter is just stale.) |
| `numbering.gap` | info | inside one counter period, sequence numbers matched by the current pattern have a gap (missing `n` between the lowest and highest). Report the missing range once per period, not per number. Info, because imports and start-number changes cause legitimate gaps (`numbering_log` explains them). |

Cancelled invoices keep their number and count as used. Negative totals are legal (credit notes); never flag them.

### 5. Money (`money.*`)

Recompute on a **deep copy** (the calc functions mutate their input) and compare exactly with `money.py` decimals.

| Code | Severity | Rule |
|---|---|---|
| `money.line_amount` | error | offers/invoices: a line's stored `amount` differs from `money.line_amount(qty, price)`. Skip documents with `totals_unverified`. Compare line amounts only: VAT depends on the current settings, which may have changed since the document was saved. |
| `money.expense_itemised` | error | expenses with `lines`: `calc.itemise_expense(copy)` gives a different `net`, `vat`, `vat_rate`, `deductible` or line `net`/`vat`. |
| `money.not_finite` | error | any money column (`net`, `vat`, `rate`, `deductible`, `vat_deductible`, `net_total`, `gross_total`, `allowance_total`, `mileage_total`, `default_rate`, `cost_rate`) holds NaN/inf or exceeds `validation.MAX_MONEY` in absolute value. |

Not in v1: incoming offer totals and trip allowances (they depend on rule tables and settings). List under
"Not covered".

### 6. Logical state (`state.*`)

| Code | Severity | Rule |
|---|---|---|
| `state.period_reversed` | warning | `end < start` for date pairs: projects, contracts, absences, trips, allocations (`start`/`end`); invoices `service_from`/`service_to`. **Not** entries (clock times; night shifts end before they start). |
| `state.no_active_boss` | error | no user with role `boss` and `active` not false (nobody could administer the instance). |
| `state.receipt_booked_without_expense` | error | a `receipt_scans` row whose status means "booked" (read the service for the exact value) has no `expense_id`. |
| `state.document_scan_record` | error | a `document_scans` row whose status means "booked" has no `record_coll`/`record_id`, or `record_coll` is not `offers`/`invoices`. |
| `state.timer_running_without_start` | warning | a timer with `running` true and no `started`. |

### 7. Files (`file.*`)

| Code | Severity | Rule |
|---|---|---|
| `file.missing` | error | `attachment` (expenses, incoming offers, receipt and document scans) or `original_pdf` (documents) is set but `store.attachment_path(name)` is None. |
| `file.orphan` | info | `store.list_attachments()` holds a file no record refers to. |
| `file.sync_conflict` | warning | one finding per entry of `store.conflicts()`. |

## Tests

`tests/backend/test_integrity.py`. Plant broken rows with `store.save` directly (this bypasses the services, just
like bad data in the field), run the service, and assert **literal** codes, collections, ids and fields:

- one test per code above (positive), plus a matching negative case where a near miss must **not** be reported:
  a cancelled numbered invoice; a credit note with a negative total; an entry with `end < start` (night shift); an
  unknown non-column key in `extra`; legacy role `member`; an imported document with `totals_unverified`; same
  number in offers and invoices without `numbering_shared` (fine) and with it (error);
- **false-positive guard:** the data the normal API fixtures create through the services (customer, project, entries,
  a finalized invoice, an itemised expense, a booked receipt if the fixture exists) gives **zero errors and zero
  warnings**;
- a rule that raises gives exactly one `check.crashed` and the other findings are still reported;
- CLI: exit codes 0/1/2 (2 with a pending migration, using the migration test helpers), `--json` shape, `--only`;
- the database file's SHA-256 is identical before and after `check-db`;
- the moved `RECORD_MODELS` and the extracted `fits()` keep the existing router and repository tests green.

Do not invent "realistic" data: ids like `cust01`, numbers like `RE-2026-0001`, amounts like `10.00`.

## Docs

- `docs/sanity-checks.md`: replace "a data health report over stored records" in "Not covered" with a short section
  "Stored data check" that points to `permita38 check-db` and lists the code prefixes.
- `README.md` or the CLI section of `docs/getting-started.md` (wherever the other commands are listed): one line.
- `CHANGELOG.md`: one line under Unreleased.

## Not covered (v1)

API endpoint / web page for the report; fixing anything automatically; `expenses.trip_item_id`; incoming-offer and
trip totals; VAT totals of documents (depend on settings at the time of saving); per-rule switches.
