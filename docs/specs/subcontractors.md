# Specification: suppliers, subcontractors and incoming offers

Status: **proposed, not scheduled** (needs the planner, the expenses and the rights model as they are on `main`; the
API-token spec is independent). Author: architect (Sonnet 5.5). The implementer follows this document, does not decide
tax or legal questions, and lists every choice it had to make under "Open design decisions" in its report.

All tax and legal statements below come from general knowledge, **not** from the statutes or an adviser. Everything
that is not certain is marked *(verify)*. Before the feature ships, the maintainer's tax adviser answers the list
"Questions for the tax adviser" and `docs/compliance.md` is updated. The application only gives hints; it never
decides whether someone is self-employed or how VAT is treated.

## Goal

The product owner's requirement: *a system where I can enter other suppliers' offers, accept them, and then for
example add freelancer capacity.* Concretely:

1. Keep **suppliers** as master data (like customers), and link expenses to them instead of the free-text `vendor`.
2. Record **incoming offers** (what a supplier or freelancer quotes to us), review them, **accept** or reject them.
3. Accepting an offer from a freelancer (a **subcontractor**) creates an **allocation**: bought capacity for a period.
   The planner counts it as capacity, the forecast shows its planned cost, finance shows the open commitment.
4. Rejecting or letting an offer expire has no effect anywhere.

Non-goals (v1): a login for subcontractors, time tracking by subcontractors, purchase orders sent to suppliers as PDFs
(the acceptance is recorded, no document is generated), supplier invoice matching beyond a link and a warning,
agencies that sell several people (see open questions), Arbeitnehmerüberlassung workflows, automatic currency
conversion (EUR only), a supplier portal.

## Vocabulary

| Term | Meaning |
|---|---|
| supplier | Master data: a company or person we buy from. |
| subcontractor | A supplier of kind `person-capacity`: a person who sells hours (a freelancer). Not a user. |
| incoming offer | A document **received** from a supplier. Distinct from `offers`, the offers *we issue* to customers. |
| allocation | The accepted, bookable result of an incoming offer: hours of one subcontractor in a period. |
| ring fence | Decided 2026-10-24 (product owner). An allocation's capacity is usable only by contracts of its own project (and of its contract, if set). Unused capacity is not moved to other projects. Demand that the project's own allocations do not cover is a gap for that project. |
| commitment | The planned net cost of an allocation. Derived, not an expense, never part of the VAT return. |

## Design decisions (made by the architect)

1. **Two new collections plus one derived view.** `suppliers`, `incoming_offers`, `allocations`. Commitments are
   **computed** from allocations (same pattern as trips and the itemised-expense lines: one helper, no second copy).
2. **Subcontractors have no login in v1.** They are suppliers with capacity attributes. A login (a limited `external`
   role that sees only its own allocations and logs time) is a separate spec, see "Later". Reason: a login pulls in the
   whole rights model, two-factor handling and time-entry validation, and the owner's need (planning and cost
   visibility) is met without it. The data model reserves `login_user_id` (null) so no migration is needed later.
3. **Allocation capacity is the allocated hours**, not the subcontractor's nominal `weekly_hours`. A subcontractor adds
   capacity only inside the window of an active allocation, and only the hours that were bought. `weekly_hours` is a
   validation bound (warning when an allocation exceeds it), not a source of capacity.
4. **Subcontractors are resources in the planner**, injected as synthetic, read-only "external" members: they can be
   assigned to contracts (via `user_ids`) and appear in the day table and weekly rows. Totals include them by default;
   a flag separates them.
5. **`expenses.vendor` stays.** `supplier_id` is added next to it. When `supplier_id` is set the server writes the
   supplier's name into `vendor` (a snapshot), so exports, old clients and the duplicate check keep working.
6. **Expired is derived**, not stored: `effective_status = "expired"` when the stored status is `received` or
   `under_review` and `valid_until < today` (the same comparison the planner uses for issued offers).
7. **Accepting is boss-only** (area `procurement`, write `all`). Accepting spends money.
8. **Money** is computed with `core/money.py` only (exact decimals, half-up cents), as everywhere.
9. **Warnings never block** (they are `planning_warnings`-style `{code, severity, field, message, details,
   confirmable}`), except the hard rules listed per route, which answer 409.

## Product owner decisions

**Ring fence (decided 2026-10-24, decided by the product owner).** Capacity is allocated to projects, because not every
freelancer or worker can work on every task.

1. An allocation belongs to one project (and optionally one contract). Only contracts of that same project may draw on it.
2. An unused allocation is not moved to other projects.
3. Demand of a project that its own allocations do not cover is shown as a gap for that project, not silently filled by
   other projects' capacity.
4. The planner day table and the what-if drafts are per project.

5. The ring fence also applies to internal employees (open question 12, decided 2026-10-24, decided by the product owner):
   employee and subcontractor allocations are both ring-fenced to projects, and the planner's team capacity is per
   project, not one pool.

The former "shared pool" option is dropped (open question 3 records the decision).

## Suppliers

### Fields

New collection **`suppliers`** (strict `SupplierIn`, lenient `Supplier`, `extra="allow"`, every field bounded, see
`docs/validation.md`; the field set follows `Customer`):

| Field | Type | Notes |
|---|---|---|
| `id`, base fields | | as every record |
| `name` | `v.Name`, required | company or person name |
| `supplier_no` | short text, optional | free text like `customer_no`; a duplicate is a warning, not an error |
| `kind` | `company` / `person` / `person-capacity` | `person-capacity` = subcontractor; default `company` |
| `active` | bool, default true | archive instead of delete once referenced |
| `street`, `zip`, `city`, `country` | short text; `country` ISO 3166 alpha-2, default `DE` | |
| `vat_id`, `tax_number` | as customer bounds | the VAT id is only format-checked (no VIES lookup) |
| `vat_mode` | `domestic` / `eu_reverse_charge` / `third_country_reverse_charge` / `small_business` | default derived from `country` (DE: `domestic`; other EU: `eu_reverse_charge`; else `third_country_reverse_charge`); the user may change it *(verify)* |
| `contact`, `email`, `phone`, `notes` | as customer | |
| `iban`, `bic`, `bank_name` | optional | IBAN checksum-validated; secret-free but personal: never logged |
| `payment_terms_days` | int 0-365, optional | days to pay; also used for the due date hint on linked expenses |
| `payment_terms_note` | short text | "2 % discount within 10 days" (informational) |
| `tags` | list of at most 20 short strings | filter in lists |
| capacity attributes (only for `person-capacity`, otherwise ignored and rejected with 422) | | |
| `weekly_hours` | `v.Hours` | what the person can offer per week at most |
| `workdays` | list of weekdays 0-6, as users | default Mon-Fri |
| `rate` | `v.Rate` | usual hourly net rate, default for new incoming offers |
| `unavailable` | list (at most 50) of `{from, to, note?}` dates, `to >= from` | availability windows the person is **not** available; treated like time off |
| `login_user_id` | id or null | reserved for the later login, always null in v1, rejected on input |
| `external_id`, `import_batch` | as customer | import bookkeeping |

The strict model requires `weekly_hours` for kind `person-capacity` (422 otherwise), so a capacity is never invented.

### Link from expenses, and the migration-safe plan for `vendor`

* `expenses` gets nullable `supplier_id` (validated to exist, 422 otherwise) and `allocation_id` (validated to exist
  and to belong to the same supplier). Both go into `ExpenseIn` and `Expense` with hard bounds.
* `vendor` is **not** removed or renamed and not migrated automatically. Released data keeps working unchanged. The
  migration adds columns only and rewrites no rows.
* On save of an expense with `supplier_id`, the service sets `vendor = supplier.name`. Saving with `supplier_id`
  null leaves `vendor` as typed (free text keeps working; one-off vendors need no supplier).
* The sanity rule `expense.possible_duplicate` compares `supplier_id` when both rows have one, and the normalised
  `vendor` text otherwise.
* **Adopting existing vendors** is an explicit, repeatable action, never automatic:
  `GET /api/suppliers/vendor-suggestions` groups the distinct `vendor` values of expenses with `supplier_id` null by a
  normalised key (trim, case-fold, collapse spaces, strip a trailing legal form such as GmbH, UG, e.K., Ltd, and
  punctuation). For each group it returns the spelling variants, the count, the sum of net amounts and the best
  existing supplier by the same normalised key (exact key match only in v1, no fuzzy matching).
  `POST /api/suppliers/adopt-vendors` takes a list of `{vendors: [...], supplier_id}` (link to an existing supplier)
  or `{vendors: [...], create: {name, ...}}` (create a supplier and link), has `dry_run` (default **true**) and
  returns what would change: the supplier ids and the expense ids. A real run sets `supplier_id` on those expenses and
  does not touch `vendor`. Running it again changes nothing. It is boss and accountant (suppliers write).
* The expense form shows a supplier picker (type to search, "new supplier" inline) and keeps a free-text vendor field
  when no supplier is chosen.

### Import and export

Suppliers get the same import and export as customers, through the existing importer (`core/service/importer/`): a new
kind `suppliers` with a field mapping (name, supplier_no, kind, street, zip, city, country, vat_id, tax_number,
contact, email, phone, iban, bic, payment_terms_days, tags), dry run first, `external_id` for repeated imports, undo by
`import_batch`. Export is the JSON/CSV export that customers have. Capacity attributes are importable as flat columns
(`weekly_hours`, `workdays` as `1,2,3`, `rate`). `docs/import.md` gets a "Suppliers" section. IBANs are validated; a
bad row is reported, not imported.

## Incoming offers

New collection **`incoming_offers`** (strict `IncomingOfferIn`, lenient `IncomingOffer`):

| Field | Type | Notes |
|---|---|---|
| `id`, base fields | | |
| `supplier_id` | id, required | validated to exist; the supplier must be `active` |
| `reference` | short text | the supplier's own offer number; a duplicate for the same supplier is a warning |
| `title` | short text | "React development, spring" |
| `received_on` | date, required | |
| `valid_until` | date, optional | `valid_until >= received_on` |
| `lines` | list (1-50) of `{description, quantity, unit, unit_price}` | `unit` is `h` or `flat`; amounts are `quantity x unit_price`, net, rounded per line half-up |
| `net_total`, `vat`, `gross_total` | computed, response only | `vat = round(net_total x vat_rate)`; `vat_rate` and `vat_mode` default from the supplier, overridable on the offer |
| `plan` | optional `{start, end, hours_per_week?, total_hours?}` | the period and the hours. Exactly one of `hours_per_week`, `total_hours` (as for contracts). Required to accept when the supplier is `person-capacity` |
| `project_id`, `contract_id`, `customer_id` | `project_id` required for a `person-capacity` supplier (the ring-fence owner); `contract_id` optional, must be a contract of that project (422 otherwise); `customer_id` derived from the project when omitted |
| `attachment` | stored file name | the supplier's **original PDF** via the existing attachments upload (`POST /api/attachments`); stored and served unchanged, never regenerated (GoBD, *(verify)* retention) |
| `status` | `received` / `under_review` / `accepted` / `rejected` | stored. `expired` is derived (`effective_status`) |
| `status_changed_at`, `decided_by`, `decision_note` | | set by the status routes, not by PUT |
| `allocation_id` | id or null | set on acceptance |
| `notes` | note | |
| `external_id`, `import_batch` | | |

Hours of an offer = sum of the `h` lines. The warning `incoming_offer.hours_mismatch` fires when a plan is given and
its hours differ from the line hours by more than 0.01 h (same idea as `offer.hours_mismatch`).

### Status transitions

| From | Action | To | Notes |
|---|---|---|---|
| (new) | create | `received` | |
| `received` | review | `under_review` | optional; a UI convenience, no side effect |
| `received`, `under_review` | accept | `accepted` | creates the allocation, see below |
| `received`, `under_review` | reject | `rejected` | `decision_note` recommended; no side effect |
| `rejected` | reopen | `under_review` | |
| `accepted` | unaccept | `under_review` | only if the allocation has not started and has no linked expense, see below |
| any non-accepted | (time passes) | `effective_status: expired` | derived; accepting needs `confirm_expired` |

Editing (`PUT`) is free while `received`/`under_review`/`rejected`. When `accepted`, only `notes`, `attachment` and
`decision_note` can change (409 for any other field), because the allocation was derived from the rest.

### Accepting

`POST /api/incoming-offers/{id}/accept` (boss only). In **one transaction**:

1. Check status is `received`/`under_review` (else 409). If `effective_status` is `expired`, require
   `confirm_expired: true` (else 409 "offer expired on <date>"); a supplier may still honour it.
2. For a `person-capacity` supplier: require a `plan` and a `project_id` (422; the ring fence needs an owner), the supplier
   `active`, `plan.end >= plan.start`. For a plain supplier (company, person) accepting creates **no allocation**:
   the offer becomes `accepted` (a record that the quote was agreed) and the commitment is `net_total` as a **flat
   commitment** without a period (shown in finance only, not in the planner). This keeps "accept a supplier's offer"
   useful for hardware or licences.
3. Run the checks, collect warnings (below). Warnings with `confirmable: true` need their code listed in
   `confirm_warnings`, as the rest of the app does; the rest are returned with the 200.
4. Create the allocation (copy of the plan, `rate` = the weighted net rate of the `h` lines, `incoming_offer_id`,
   `supplier_id`, `project_id`, `contract_id`, `customer_id`), set the offer `accepted`, `allocation_id`, `status_changed_at`, `decided_by`.

Warnings at acceptance (none block):

| Code | Severity | When |
|---|---|---|
| `allocation.exceeds_weekly_hours` | warning, confirmable | allocated hours in any week exceed the supplier's `weekly_hours` (summed over all active allocations of that supplier, so overlapping allocations are caught) |
| `allocation.outside_availability` | warning | the window overlaps a `unavailable` window; the hours on those days are dropped from the allocation, the warning says how many |
| `allocation.cost_differs_from_offer` | info | planned cost (planner days, holidays removed) differs from `net_total` by more than 0.01 |
| `allocation.no_demand` | info | no contract of the allocation's project is assigned to the subcontractor in the window (bought capacity that no contract of its project uses yet) |
| `supplier.dependency_hint` | info | see "Hints" |
| `incoming_offer.hours_mismatch` | warning | see above |

### Unaccepting and cancelling

* `POST /api/incoming-offers/{id}/unaccept`: allowed only if the allocation `start > today` **and** no expense has
  `allocation_id` set to it. The allocation is deleted, the offer returns to `under_review`. Otherwise 409 with the
  reason and the hint to cancel instead.
* `POST /api/allocations/{id}/cancel` with `{effective_from, reason?}`: for a running or finished allocation.
  `effective_from` must be `>= allocation.start`. Days from `effective_from` on are released (capacity and commitment
  disappear for them); days before stay. `status` becomes `cancelled`, `cancelled_from` and `cancel_reason` are
  stored, the offer stays `accepted` (history is kept). Linked expenses are untouched; the open commitment is recomputed
  from the shortened allocation. Cancelling twice, or with `effective_from` after `end`, is 409.
* Allocations cannot be edited or created by hand in v1. A change is a cancel plus a new (revised) incoming offer.

## Allocation (the planner object)

New collection **`allocations`** (response model only; there is no `*In` body, rows are created by the service):

| Field | Notes |
|---|---|
| `id`, `supplier_id`, `incoming_offer_id` | |
| `project_id`, `contract_id`, `customer_id` | ring fence: only contracts of `project_id` (and `contract_id`, if set) may draw on this allocation; unused capacity is not moved (decided, open question 3) |
| `start`, `end`, `hours_per_week` or `total_hours` | copied from the plan |
| `rate` | net hourly rate, exact decimal string in the API (stored as float like every rate, computed through `money.py`) |
| `status` | `active` / `cancelled` |
| `cancelled_from`, `cancel_reason` | |
| computed, response only: `planned_hours`, `planned_net`, `invoiced_net`, `open_net` | see "Cost and commitments" |

### Spreading the hours (the oracle; mirrors how contracts spread)

For one allocation and the subcontractor `S` with `workdays`:

* **available days** = days in `[start, effective_end]` (`effective_end = cancelled_from - 1 day` when cancelled) that
  are in `S.workdays`, are **not** a public holiday of the business state, and are **not** inside an `S.unavailable`
  window.
* `hours_per_week`: each available day gets `hours_per_week / len(S.workdays)`. A week with unavailable days therefore
  has fewer hours: **the allocation is a ceiling per day, not a promise that is moved to other days** (the
  subcontractor bills the hours worked, unlike a contract promise to a customer). Planned hours and cost shrink with
  them.
* `total_hours`: spread evenly over the available days of the whole period (`total_hours / n`), like contracts with a
  total. With no available day at all the allocation books nothing and the warning `allocation.no_available_day`
  (warning) is returned.
* The per-day hours are the allocation's capacity on that day **and** its planned cost basis. Calendar busy time of
  the business owner does not apply to subcontractors.

## Planner integration

### Capacity

`planning.load` builds, from active allocations, one synthetic external member per subcontractor in
`World.users` (`id` = the supplier id, `external: true`, `workdays` and `weekly_hours` from the supplier, `role`
`"external"`, never persisted, never in `team` lists). Rules:

* `day_table` **skips external members in the base-capacity loop** (their capacity is not `weekly_hours / workdays`).
  Instead, for each active allocation it adds the per-day hours above to `Day.cap` (team total) and to `Day.external_cap[project]`
  of the allocation's project only. No other project's row ever receives these hours.
* `S.unavailable` windows are registered as days off in `calc.Capacity.absences` for the synthetic member (kind
  "other"), so that contracts assigned to `S` spread around them exactly as for an employee on leave.
* A contract may list a subcontractor id in `user_ids`. `ContractIn` validation accepts ids of users **or of active
  subcontractor suppliers**; the planner's existing `make()` filter (`if x in users`) then works unchanged. A
  contract whose assignees are all unknown still falls back to the boss.
* Demand assigned to an external member goes into the existing `confirmed`/`binding` buckets (so utilisation counts it)
  and additionally into `Day.external_assigned[project]`, where `project` is the project of the contract that holds the demand.
* **Ring fence (decided 2026-10-24).** A contract draws on external capacity only if its project equals the allocation's
  project (and its `contract_id`, when set, equals the contract). Per project and day:
  `external_gap = max(0, external_assigned - external_cap)`. The gap stays on that project's row: the planner never moves
  the excess to another assignee or another project's allocation, and the team total does not cancel it. Demand of a
  contract with no matching allocation is therefore a gap in full.
* Time entries are not affected (subcontractors do not track time in v1).

### Weekly rows and the flag

`weeks()` and the endpoints that return rows (`/api/planning/forecast`, `/simulate`, scenario comparison) get the query
parameter **`include_external`** (default `true`). With `false`, the world is built **without** allocations and
without external members: the result must equal the result of a database with no suppliers at all (a test).
Each row gains, always (zero when none):

```json
{
  "external_capacity": 20.0,
  "external_assigned": 25.0,
  "external_gap": 5.0,
  "external_cost": 1600.0,
  "external_by_project": [{"project_id": "a1b2c3d4", "external_capacity": 20.0, "external_assigned": 25.0, "external_gap": 5.0, "external_cost": 1600.0}]
}
```

`capacity` includes `external_capacity` when the flag is on; `confirmed`/`binding_offers` include external-assigned
demand. `contributions[]` entries get `"external": true` when every assignee is external. What-if drafts (`/simulate`, scenario comparison) use the same per-project rules: their
day table, weekly rows and gaps are per project, and a moved contract changes only the rows of its own project. The forecast totals gain
`external_capacity`, `external_cost` and `margin` (`revenue - external_cost`). The "intended" revenue (capacity x default
rate) includes external capacity when the flag is on (open question 4).

### Cost

* `external_cost` of a week = sum over allocations of `q2(dec(round(hours_in_week, 6)) x rate)` (the same rounding the
  revenue column uses). For `total_hours` allocations the **allocation-level** `planned_net = q2(total_hours x rate)` is
  authoritative; the weekly figures are distribution only and may differ from it by a few cents in total (never used for
  money reports).
* The cost of an employee (`cost_rate x tracked hours`) is unchanged.

### Day table

`/api/planning/day-table` (or whichever route exposes it) returns per day and per project `external_cap`, `external_assigned` and `external_gap`; the
UI draws external hours in a different pattern and shows each project's gap on that project's row, never summed away.

## Finance and forecast

* A commitment is **not an expense**: no entry in the expense list, no input VAT, no effect on the VAT return
  (`ustva`) or on profit reports that are built on actual expenses.
* Per allocation: `planned_net = q2(planned_hours x rate)` (or `q2(total x rate)`), `invoiced_net` = sum of `net` of
  expenses with this `allocation_id`, `open_net = max(0, planned_net - invoiced_net)`.
* `GET /api/finance/commitments` lists allocations with those four numbers, per supplier and per project, plus the sum
  of `open_net`. The finance page shows it as its own block "Open supplier commitments", and the project report gets a
  line `committed_external_cost` (planned) beside the existing tracked cost.
* Sanity warning `allocation.invoiced_exceeds_planned` (warning) on saving an expense whose `allocation_id` makes
  `invoiced_net > planned_net` (by more than 0.01), with the excess in `details`.
* Expenses linked to an allocation need no other special treatment: category, VAT and deductibility are as for any
  expense. The expense form pre-fills supplier, allocation and, for `eu_reverse_charge`/`third_country_reverse_charge`
  suppliers, VAT 0 with a note (below).

## Rights

New areas in `core/rights.py` (and `docs/roles.md`, which a test compares against the matrix):

* `suppliers`: master data of suppliers and the vendor adoption.
* `procurement`: incoming offers, allocations and the commitments report.

| Area | boss | employee | accountant |
|---|---|---|---|
| `suppliers` | all / all | limited / none | all / all |
| `procurement` | all / all | none / none | all / none |

`limited` read on `suppliers` for an employee = **name, kind and tags only** (no address, IBAN, tax numbers, rates,
notes); this lets the team timeline name external capacity without leaking commercial data. Employees have no access to
incoming offers or commitments (they contain the rates and the acceptance decision). The accountant reads everything
and writes suppliers (to keep bookkeeping master data and adopt vendors) but cannot accept or reject offers. Accepting,
rejecting, unaccepting and cancelling need `procurement` write `all` (boss only). Employee `planning` stays `none`, so
employees do not see the planner rows.

Team timeline (`docs/specs/team-timeline.md`): external members appear as a separate group with their allocation
windows when the viewer has `suppliers` read `limited` or better; only name and window, no rates.

## API tokens and MCP

* Tokens never exceed the owner's rights. A **read-only token** of a boss or accountant can `GET` suppliers, incoming
  offers, allocations and commitments; every write route (create, edit, status changes, accept, cancel, adopt-vendors)
  is denied for read-only tokens like all writes. An employee's token can list suppliers in the limited view.
* Accepting is a state change that spends money: a read-write token may call it (rights allow it for a boss), but the
  MCP server must expose accept/reject/cancel **only** behind its write-tool confirmation. The first MCP release
  (`docs/specs/mcp-server.md`) lists read tools only: `list_suppliers`, `get_supplier`, `list_incoming_offers`,
  `list_allocations` are added to its tool table, read-only, with the same rights as the HTTP routes.
* The audit log records the route template (`/api/incoming-offers/{id}/accept`), never ids.
* An AI proposal ("create an incoming offer from this PDF") belongs to the proposals spec and is out of scope here.

## Hints and compliance notes

These are notes shown to the user, never blockers, and every one is marked *(verify)* in the UI text and the docs.

**False self-employment (Scheinselbstaendigkeit) *(verify)*.** If a person works like an employee, social-security
contributions can be claimed retroactively from the client. Indicators include working mostly for one client, a
fixed schedule, working on our premises or tools, and following our instructions. The status procedure of the German
pension insurance (*Statusfeststellungsverfahren*, §7a SGB IV) can clarify a case *(verify)*. The application can see
only a part of this: how much one person works for us. Two info-level hints:

1. `supplier.dependency_hint` for a subcontractor: the allocated hours (active and planned, averaged per calendar week
   over the allocation weeks) reach **30 h per week for 12 consecutive weeks** or more. Settings
   `subcontractor_hint_hours_per_week` (default 30) and `subcontractor_hint_weeks` (default 12). Text: "X works about
   30 h per week for us for 12 weeks. Many hours for one client can point to false self-employment (verify). Ask your
   adviser; this is a hint only."
2. `business.customer_dominance` for the owner: one customer's net invoiced revenue in the trailing 12 months is **83 %
   or more** of the total (setting `customer_dominance_percent`, default 83; the figure is the old "5/6 of income" rule
   of thumb and is not a statutory threshold) *(verify)*. Shown on the dashboard as a note.

Neither hint changes any number or blocks any action. Both can be dismissed per subcontractor/customer.

**Reverse charge, §13b UStG *(verify)*.** For services bought from a supplier established abroad, the recipient (we)
owes the VAT. The supplier's invoice then shows no VAT; we declare the VAT ourselves and, if fully deductible, deduct it
in the same return, so the net effect is zero. `vat_mode` on the supplier and the incoming offer drives this: for
`eu_reverse_charge` and `third_country_reverse_charge` the offer shows `vat = 0` and the expense form offers "reverse
charge: self-assessed VAT 19 % (verify)". How this appears in the VAT return is a matter for the `ustva` spec
(`docs/specs/ustva.md`, not on `main` yet): this spec only stores `vat_mode` on the supplier and offer and records the
reverse-charge amounts on the expense (`vat` = self-assessed amount, a flag `reverse_charge`), nothing more. §13b also
applies to some domestic cases (for example construction services between businesses) *(verify)*; v1 does not model
those.

**Construction only, §48b EStG *(verify)*.** The exemption certificate (*Freistellungsbescheinigung*) matters only for
construction services. The application does not handle it. If the owner's field is construction, the tax adviser
decides; a certificate attachment on the supplier is a follow-up.

**KSK (Kuenstlersozialkasse) *(verify)*.** The artists' social levy can apply to fees paid to self-employed artists
and publicists (design, writing, photography, music) above a small yearly limit. For software freelancers it is
normally not applicable. v1 shows nothing; a supplier tag `kuenstler` is a manual reminder only.

**Other points for the adviser:** employee-leasing law (*Arbeitnehmerueberlassung*, AUeG) if freelancers work under our
instructions at a customer *(verify)*; retention of the supplier's original PDF as an accounting document *(verify)*;
the 2025+ e-invoice receipt duty for domestic suppliers (we must be able to receive and archive structured invoices)
*(verify)*, which is about the expense side, not about offers.

### Questions for the tax adviser

1. Are the 30 h / 12 week and 83 % hints reasonable, or should the thresholds be different?
2. Does a quote from a freelancer, once accepted, need to be kept as a document (retention period), or is the invoice
   enough?
3. Reverse charge: which VAT-return boxes does the self-assessed VAT and the deduction go to, and is full deduction
   always right for us?
4. Do any of our typical subcontractors fall under KSK?
5. Is any of the work construction-related (§13b domestic, §48b)?
6. Is a person who delivers only to us and uses our tools a risk we must record somewhere (for example a written
   statement of independence)?
7. Foreign suppliers: must we collect anything beyond the VAT id (for example a certificate of residence for
   third-country suppliers)?

## Data model and migration

Dialect-neutral in `core/repository/schema.py`, three tables registered like the others (generic `list/get/save/find`;
no new `Repository` method); contract tests for all three. Unknown fields round-trip through `extra`.

```text
suppliers       id name supplier_no kind active street zip city country vat_id tax_number vat_mode contact email phone notes
                iban bic bank_name payment_terms_days payment_terms_note tags:json
                weekly_hours workdays:json rate unavailable:json login_user_id external_id import_batch   + base + extra
incoming_offers id supplier_id reference title received_on valid_until lines:json net_total vat vat_rate vat_mode
                gross_total plan:json project_id contract_id customer_id attachment status status_changed_at decided_by
                decision_note allocation_id notes external_id import_batch                                + base + extra
allocations     id supplier_id incoming_offer_id project_id contract_id customer_id start end hours_per_week total_hours rate
                status cancelled_from cancel_reason                                                         + base + extra
expenses        + supplier_id:id  + allocation_id:id  + reverse_charge:bool
indexes         suppliers(name), suppliers(external_id), incoming_offers(supplier_id), incoming_offers(status),
                allocations(supplier_id), allocations(status), expenses(supplier_id), expenses(allocation_id)
```

* One migration, `NNNN-suppliers`, created with `permita38 create-migration suppliers`, identical in `sqlite/`,
  `postgresql/` and `mariadb/`; `requires` is the head at merge time (see `docs/specs/migration-ids.md`); no
  `BEGIN`/`COMMIT`. It adds three tables, three nullable columns and indexes, and **rewrites no existing row**.
* Pydantic: `SupplierIn`, `IncomingOfferIn` (strict) with the lenient `Supplier`, `IncomingOffer`, `Allocation`; every
  new field in both, with a hard bound. `reverse_charge` and the two new expense links go into `ExpenseIn`/`Expense`.
* Cascade: deleting a supplier is 409 while any incoming offer, allocation or expense references it (archive with
  `active: false`). Deleting an `accepted` incoming offer is 409. Deleting a project, contract or customer that an active
  allocation references follows the existing rule (409 while referenced).
* The synthetic planner members never touch the `users` table.

## API contract (sketch; the binding version is `docs/contracts/subcontractors.md`, written first, see work packages)

Conventions as in `docs/contracts/api-tokens.md`: errors `{"detail": "<message>"}` for 401/403/404/409 and service-level
422; body validation errors in FastAPI's list form. Dates `YYYY-MM-DD`; money fields are JSON numbers with at most two
decimals in requests (`v.Money`), exact decimals internally. Auth: session or token, rights as above.

### Suppliers

| Route | Rights | Notes |
|---|---|---|
| `GET /api/suppliers?kind=&active=&tag=&q=` | suppliers read | `limited` view for employees |
| `POST /api/suppliers` | suppliers write | 201, returns the supplier, may carry `warnings` (duplicate `supplier_no`) |
| `GET /api/suppliers/{id}`, `PUT`, `DELETE` | | `DELETE` 409 when referenced |
| `GET /api/suppliers/vendor-suggestions` | suppliers write | see above |
| `POST /api/suppliers/adopt-vendors` | suppliers write | `dry_run` default true |
| `GET /api/suppliers/{id}/dependency` | procurement read | the numbers behind the hint (weekly average, weeks) |

```json
{
  "id": "5d1c0aa2",
  "name": "Example Freelance Studio (Demo)",
  "kind": "person-capacity",
  "supplier_no": "L-0003",
  "country": "DE",
  "vat_mode": "domestic",
  "vat_id": "DE123456789",
  "email": "demo.freelancer@example.invalid",
  "payment_terms_days": 14,
  "tags": ["frontend"],
  "weekly_hours": 24,
  "workdays": [0, 1, 2, 3],
  "rate": 80.0,
  "unavailable": [{"from": "2026-04-13", "to": "2026-04-15", "note": "trade fair"}],
  "active": true
}
```

### Incoming offers and allocations

| Route | Rights | Notes |
|---|---|---|
| `GET /api/incoming-offers?supplier_id=&status=&project_id=` | procurement read | `status=expired` filters on the derived value |
| `POST /api/incoming-offers` | procurement write | 201; warnings in `planning_warnings` style |
| `GET /api/incoming-offers/{id}`, `PUT`, `DELETE` | | `PUT`/`DELETE` 409 rules above |
| `POST /api/incoming-offers/{id}/review` | procurement write | `received` to `under_review` |
| `POST /api/incoming-offers/{id}/accept` | procurement write | body below |
| `POST /api/incoming-offers/{id}/reject` | procurement write | `{"reason": "..."}` |
| `POST /api/incoming-offers/{id}/reopen` | procurement write | `rejected` to `under_review` |
| `POST /api/incoming-offers/{id}/unaccept` | procurement write | 409 rules above |
| `GET /api/allocations?supplier_id=&status=&project_id=` | procurement read | with computed `planned_*`, `invoiced_net`, `open_net` |
| `GET /api/allocations/{id}` | procurement read | adds `days: [{date, hours}]` (the oracle's output) |
| `POST /api/allocations/{id}/cancel` | procurement write | `{"effective_from": "2026-03-13", "reason": "project paused"}` |
| `GET /api/finance/commitments` | finance read and procurement read | |

Incoming offer:

```json
{
  "id": "9b3e51c7",
  "supplier_id": "5d1c0aa2",
  "reference": "ANG-2026-117",
  "title": "Frontend capacity, spring",
  "received_on": "2026-02-20",
  "valid_until": "2026-03-01",
  "lines": [{"description": "Frontend development", "quantity": 80, "unit": "h", "unit_price": 80.0}],
  "net_total": 6400.0,
  "vat_rate": 19,
  "vat": 1216.0,
  "gross_total": 7616.0,
  "plan": {"start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20},
  "project_id": "a1b2c3d4",
  "attachment": "angebot-ang-2026-117.pdf",
  "status": "under_review",
  "effective_status": "under_review",
  "allocation_id": null
}
```

Accept request and response:

```json
{ "confirm_expired": false, "confirm_warnings": [], "project_id": "a1b2c3d4" }
```

```json
{
  "offer": { "id": "9b3e51c7", "status": "accepted", "allocation_id": "c04d77e1" },
  "allocation": {
    "id": "c04d77e1", "supplier_id": "5d1c0aa2", "start": "2026-03-02", "end": "2026-03-27",
    "hours_per_week": 20, "rate": 80.0, "status": "active",
    "planned_hours": 80.0, "planned_net": 6400.0, "invoiced_net": 0.0, "open_net": 6400.0
  },
  "planning_warnings": [
    {"code": "allocation.no_demand", "severity": "info", "field": null,
     "message": "No contract uses this capacity yet.", "details": {}, "confirmable": false}
  ]
}
```

Errors (all `{"detail": ...}` unless noted):

| Status | When |
|---|---|
| 401 | not signed in / bad token |
| 403 | right missing; a read-only token on a write route; employee on procurement |
| 404 | unknown id (also supplier/project in the body of a 422-style link check is 422, see below) |
| 409 | wrong status for the action; expired without `confirm_expired`; accept of an inactive supplier; unaccept after start or with linked expenses; edit of locked fields; delete of a referenced record; double cancel; confirmable warning not confirmed (`detail` lists codes) |
| 422 | body validation; unknown `supplier_id`/`project_id`/`allocation_id`; capacity attributes on a non-capacity supplier; `plan` with both or neither of `hours_per_week`/`total_hours`; accepting a capacity offer without plan or project/customer |

## UI sketch

All screens live in the vanilla-JS app, talk only to the API, and follow the existing list/form patterns.

```text
Suppliers                                   [+ New supplier] [Import] [Export]
 filter: [kind v] [tag v] [search ________] [x active only]
 +--------+----------------------------+-----------------+--------+-------------+
 | No.    | Name                       | Kind            | Tags   | Open comm.  |
 | L-0003 | Example Freelance Studio   | Subcontractor   | fronte | 6.400,00 EUR|
 | L-0004 | Demo Print Shop GmbH       | Company         |        |      0,00   |
 +--------+----------------------------+-----------------+--------+-------------+
 Supplier form: master data | bank | payment terms | [capacity: weekly h, workdays, rate, unavailable windows]
 Banner on the Expenses page: "12 expenses have a vendor without a supplier. [Review and link...]"

Incoming offers                             [+ Record offer]
 tabs: [Open] [Accepted] [Rejected] [Expired]
 row: ANG-2026-117 | Example Freelance Studio | 80 h | 6.400,00 net | valid to 01.03.2026 | under review
 detail: lines, plan, attached PDF (preview), warnings, buttons [Review] [Accept...] [Reject...]
 Accept dialog: project (required for capacity), contract (optional), period (from plan), shows planned hours and cost, per-week free capacity chart before/after,
                warnings with checkboxes for confirmable ones, [Accept] (boss only; other roles do not see the buttons)

Planner (existing): row group "External" under the team; switch [x include subcontractors];
 external hours drawn hatched, one external row per project, a gap drawn on the project's row; week tooltip: capacity 60 h (of which external 20 h), planned external cost 1.600,00 EUR
Finance: block "Open supplier commitments" with the table of allocations.
```

Accessibility and keyboard behaviour as the rest of the app; the dialog works without a mouse; texts in the app's
language (German and English strings like the other pages).

## Test plan

Backend tests per subsystem (`tests/backend/`): repository contract for the three tables and the new expense columns
(round-trip, unknown fields), migrations in all three dialects (the existing drift tests), strict/lenient model bounds,
rights matrix and `docs/roles.md` consistency, status transitions (a table-driven test over every pair of status and
action), API tests with `TestClient`, importer dry run equals real run, vendor adoption idempotence, token tests
(read-only token reads suppliers, is denied every write), and the hypothesis state machine gets the new actions with an
invariant "capacity with `include_external=false` equals the pre-feature model".

### Planner oracle: hand-computed worked examples (these become tests; do not recompute them with the code under test)

Common setup: today = 2026-03-02 (Monday), business state without a public holiday between 2026-03-02 and 2026-03-27,
Easter Sunday 2026 = 5 April so Good Friday = 3 April and Easter Monday = 6 April (national holidays). Boss: 40 h/week,
Mon-Fri. Subcontractor S: `workdays` Mon-Thu, `weekly_hours` 24, rate 80.00 unless stated.

| # | Case | Hand computation | Expected |
|---|---|---|---|
| W1 | `hours_per_week` 20, 2026-03-02 to 2026-03-27 | 20 / 4 workdays = 5 h per day; 4 weeks x 4 days = 16 days | 80 h, `planned_net` 6.400,00; every week 20 h, 1.600,00 |
| W2 | same, window 2026-04-06 to 2026-04-17 (Easter Monday in week 1) | week 1: Tue, Wed, Thu = 3 x 5 = 15 h; week 2: 4 x 5 = 20 h | 35 h; 1.200,00 + 1.600,00 = 2.800,00 |
| W3 | `total_hours` 100, S Mon-Fri, rate 75.00, 2026-03-02 to 2026-03-20, unavailable 2026-03-09 to 2026-03-11 | 15 weekdays - 3 = 12 available days; 100 / 12 = 8,3333 h per day; week 1: 5 days = 41,6667 h; week 2: Thu, Fri = 16,6667 h; week 3: 41,6667 h | `planned_net` 7.500,00; weekly cost 3.125,00 / 1.250,00 / 3.125,00 |
| W4 | capacity before and after, ring fence. Setup: contract C of project P, 50 h/week, 2026-03-02 to 2026-03-27, `user_ids` [boss]. Employee allocation (ring fence): the boss's 40 h/week (8 h x Mon-Fri) are planned to project P, nothing to any other project. W1's allocation of S (project P) is accepted. Then C's `user_ids` become [boss, S] | **Before**, week of 2026-03-02, project P: employee capacity 40, employee assigned 50, employee gap 10; team total capacity 40, confirmed 50, utilisation 1,25 `over`. **After**, project P: employee rows: capacity 40, assigned 25 (C splits 25 h each), gap 0, 15 h of the boss stay unused in P (not moved elsewhere); external rows: capacity 20, assigned 25 (6,25 h per day over Mon-Thu vs 5 h), **external gap 5 h**, utilisation 1,25 `over`. Team total of P: capacity 40 + 20 = 60, confirmed 50, free after confirmed 10 (= 15 unused boss hours - 5 h gap), utilisation 50 / 60 = 0,8333. The team total does not cancel the gap: the 5 h stay on P, they are not moved to the boss's unused hours or to another project | as stated; `external_capacity` 20, `external_assigned` 25, `external_gap` 5 (project P), `external_cost` 1.600,00; employee gap 0 (after), 10 (before) |
| W5 | W4 with `include_external=false` | S dropped, contract falls back to boss: capacity 40, confirmed 50, utilisation 1,25 `over`, `external_*` all 0 | identical to the database without any supplier |
| W6 | states with no effect | incoming offer for W1 in `received`, `under_review`, `rejected`, and with `valid_until` 2026-03-01 (derived `expired`): forecast rows and commitments | identical to W5 for all four; after accept: W4; accept of the expired one without `confirm_expired` is 409 and leaves rows unchanged; `valid_until` = 2026-03-02 (today) is **not** expired |
| W7 | rounding of an offer | 37,5 h x 82,45 = 3.091,875 -> 3.091,88; VAT 19 % of 3.091,88 = 587,4572 -> 587,46; gross | `net_total` 3.091,88; `vat` 587,46; `gross_total` 3.679,34 |
| W8 | reverse charge | W7 offer from `eu_reverse_charge` supplier | `vat` 0, `gross_total` 3.091,88; expense form proposes self-assessed 587,46 (verify) |
| W9 | cancel | W1 cancelled with `effective_from` 2026-03-13; Mon-Thu days kept: 2, 3, 4, 5, 9, 10, 11, 12 March = 8 x 5 h | 40 h, `planned_net` 3.200,00; the released 40 h are free again; linked expense net 1.600,00 gives `open_net` 1.600,00 |
| W10 | over-invoiced | W9 with linked expenses net 3.300,00 | `open_net` 0,00 (floored), warning `allocation.invoiced_exceeds_planned`, excess 100,00 |
| W11 | weekly-hours check | S `weekly_hours` 24; allocation A 20 h/week and allocation B 8 h/week overlapping in week 2026-03-02 | 28 h in the week, warning `allocation.exceeds_weekly_hours`, over by 4 h; single allocation of 25 h/week: over by 1 h |
| W12 | dependency hint | S Mon-Fri, `hours_per_week` 30, 2026-06-08 to 2026-08-28 (12 full weeks, no national holiday): average 30,0 | hint raised; with 29,9 h/week none; with the window one week shorter none |
| W13 | customer dominance | trailing 12 months net revenue 60.000,00, customer A 49.800,00 = 83,0 % | hint raised; 49.799,99 (82,99998 %) none |
| W14 | cost of unassigned bought capacity, ring fence | W1 accepted (project P), no contract of P assigned to S. Variant: contract D of project Q, 10 h per week, `user_ids` [S], no allocation of Q | P: external capacity 20 per week, `external_assigned` 0, `external_gap` 0, info `allocation.no_demand`, cost still 1.600,00 per week; the 20 h are **not** offered to Q. Variant: Q `external_capacity` 0, `external_assigned` 10, `external_gap` 10 h per week; P's 20 h stay unused |

Further tests: unaccept allowed before start with no expense, 409 once an expense is linked or the allocation started;
`PUT` of a locked field on an accepted offer is 409; a plain-supplier acceptance creates no allocation and no planner
effect, only a flat commitment of `net_total`; `include_external` default is true; scenario simulate with a draft that
moves a contract assigned to S behaves like for an employee, per project (a draft moving contract C of project P leaves project Q's rows and gap unchanged); ring fence: a contract of another project never draws on an allocation, and unused capacity stays unused (W14); vendor adoption dry run vs real run counts equal, `vendor`
text unchanged; employee `limited` supplier view has no IBAN field; sanity duplicate rule with and without
`supplier_id`.

Browser tests (`tests/web/`): create a supplier and a subcontractor, record an incoming offer with a (fake) PDF, accept
it, see capacity change in the planner and the commitment in finance, cancel, adopt vendors from the expense banner. One
seeded random-journey step is added to `tests/web/e2e_random/`. Demo data uses only fake names, `example.invalid`
addresses and documentation IBANs.

## Review tier

**Full** (AGENTS.md): money (commitments, rounding, VAT/reverse charge), rights (a new accepting power), capacity maths,
outside input (PDF upload, import, IBAN). Three Sonnet 5.5 passes: (1) code review of every work package diff, (2) test
review (would the worked examples fail if the spreading rule were wrong?), (3) logic review: recompute W1-W14 by hand or
with an independent small oracle written by the reviewer, check the half-up rounding and the holiday handling, and the
reverse-charge text with the adviser's answers. The spec itself needs a **Standard** review before work starts.

## Work packages (backend and web UI in parallel)

0. **Contract first (architect, small):** write `docs/contracts/subcontractors.md` from the API section with final field
   names and error cases, plus fixture JSON files for the UI (`web/` dev fixtures or `tests/web/fixtures/`). Everything
   below starts after this is merged to a docs branch; the UI packages build against the fixtures.
1. **A. Backend: suppliers** (models, schema, migration, repository contract tests, routes, importer kind, export,
   expense link and `vendor` snapshot, vendor adoption, rights areas, `docs/roles.md`, `docs/import.md`). Independent of B
   except the migration, which A creates **including the other two tables** so there is one migration file.
2. **B. Backend: incoming offers and allocations** (service `procurement.py` in `core/service/`, status machine, accept,
   unaccept, cancel, warnings, sanity rules, hints, settings keys, routes, attachment link). Needs A's migration/schema.
3. **C. Backend: planner and finance** (`planning.load/day_table/weeks`, synthetic members, `include_external`, cost,
   `finance/commitments`, project report line, contract `user_ids` validation, team-timeline group). Needs the allocation
   model from B (the pure function "allocation to per-day hours" is written first and unit-tested against W1-W3).
4. **D. Web UI: suppliers and expense picker** (list, form, import/export entry, vendor banner). Against the fixtures.
5. **E. Web UI: incoming offers, accept dialog, planner row group and toggle, finance block.** Against the fixtures, then
   against the real API.
6. **F. Docs and contract wrap-up:** `docs/planning.md` section "Subcontractors", `docs/compliance.md` notes, roadmap
   row update (by the maintainer), `make generate-openapi`, MCP tool table entries, tax-adviser answers merged.

A, then B and C in parallel (C starts with the pure spreading function), D parallel to A, E parallel to B/C. Merge order
A, B, C, D, E, F; `make test lint` after each; the full browser and randomized suites once at the end.

## Later (not in this version)

* A **subcontractor login**: a limited `external` role (own allocations, own time entries, no rates of others, no
  customer data beyond assigned projects), time tracking against allocations, and reconciliation of tracked hours with
  supplier invoices. Needs its own rights spec and two-factor policy.
* Supplier-invoice matching (e-invoice import), agency suppliers with several people, a certificate attachment (§48b), purchase-order PDFs, VIES lookup, per-supplier state for holidays.

## Open questions

1. **Login for subcontractors:** confirm "no login in v1". If wanted early, how limited is the role and does it log time?
2. **Does an accepted incoming offer for a non-capacity supplier (hardware, licences) create anything beyond a flat
   commitment?** For example a planned expense on a date, so cash-flow forecasts include it.
3. **Ring fence (DECIDED 2026-10-24, decided by the product owner):** capacity from an allocation is usable only by
   contracts of the allocation's project (and contract, if set); unused capacity is not moved to other projects; demand
   the project's own allocations do not cover is a gap for that project. The shared-pool option is dropped. See
   "Product owner decisions".
4. **Assumed ("intended") revenue:** should external capacity raise the assumed revenue (capacity x default rate)? v1 says
   yes with the cost shown beside it and a margin line; the alternative is to exclude external hours from "intended".
5. **Unavailability and holidays:** is the business state's holiday calendar right for freelancers in other states, and
   should hours lost to holidays reduce the cost (v1: yes, hours are ceilings per day) or be made up on other days as for
   customer contracts?
6. **Hours versus fixed price:** do subcontractors ever quote a flat price for a deliverable? v1 allows `flat` lines but
   spreads hours only when a `plan` is given; a flat-price allocation without hours has no capacity effect.
7. **Agencies:** a supplier selling several people. v1 needs one supplier per person or one supplier with one capacity.
8. **Accepting authority:** boss only (v1). Should a project lead (employee) propose a decision the boss confirms?
9. **Tax-adviser answers** to the seven questions above, notably reverse charge boxes and the dependency thresholds.
10. **Numbering:** `supplier_no` as free text (v1) or a counter in the numbering service like invoice numbers?
11. **Retention of the supplier PDF** and whether accepting should store a signed copy of our acceptance (v1: no).
12. **Ring fence for internal team members (DECIDED 2026-10-24, decided by the product owner):** the same project ring
    fence applies to INTERNAL team members (employees with a planner allocation). Employee and subcontractor allocations
    are both ring-fenced to projects; the planner's team capacity is also per project; unused hours are not moved to
    other projects; uncovered demand is a gap for that project.

## Open design decisions (for the implementer's report)

The implementer lists here every choice the specification did not settle, with the simplest option taken, for the
reviewer to confirm.
