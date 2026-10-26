# Specification: contracts belong to one customer, and the customer chooses the accepted items

Status: **proposed, draft**. Author: drafted by a Haiku session from the maintainer's bug report; **architect sign-off
pending** (Sonnet 5.5, per `AGENTS.md` "Division of labour"). Until the architect has signed off the open decisions below,
do not implement. Review tier: **Full** (money fields are copied into the contract, and the rule protects which company
a contract is for). The implementer follows this document and lists every choice it had to make under "Open design
decisions" in its report.

Names in this document are placeholders (`Customer A`, `Project P`). Real customer data never goes into the repo.

## Goal

Two things must hold, as the maintainer put it: **a contract is never entered for a different company than the one its
project belongs to**, and **accepting an offer is followed by a contract view in which the user ticks the items the
customer accepted**.

Reported case (2026-10-26): an accepted offer and two contracts were booked on the wrong project. A contract that
belonged to another customer's work was attached to the project of Customer A, so its time appeared as Customer A's
capacity in the planner. The contract form accepts any customer and any project independently, and accepting an offer
creates a contract for every item of the offer without asking the customer what was accepted.

## Product owner decisions (given, not open)

1. A contract belongs to the customer of its project. A contract for a project of another customer is refused.
2. After an offer is accepted, the next view is the contract view, where the user ticks the offer's items that the
   customer accepted. Only the ticked items are kept on the contract.
3. The items view is part of the accept action: the offer is accepted, the project and contract are created, then the
   items view opens. Accepting is not complete until the view is answered or dismissed.

## Current behaviour (verified at `origin/main` a5c92ef)

* `permita38/core/service/sales.py` `_accept_offer`: sets the offer `accepted`, extends the offer's project (or creates
  one), and creates a contract with `customer_id` of the offer, the project, the plan's period and rate. It copies no
  items. It does **not** check that the offer's customer equals the project's customer.
* `permita38/core/service/records.py` `_write`, branch `contracts`: checks `user_ids` only. `customer_id` and
  `project_id` are stored as sent, so a contract can name a customer and a project of different customers.
* `web/app.js` `editContract` (l. 466 ff.): the customer and the project are two independent selects.
* `web/app2.js` `docAccept`: after `POST offers/{id}/accept` it navigates straight to the project. No items view.
* `Contract` (`models.py`) has no items; `ContractIn` is strict (unknown fields are refused).

## Design

### A. Customer rule (server, all contract writes)

* On every contract write (create, update, the generic records route and the accept action), if `project_id` is set and
  the project has a `customer_id`:
  * if the contract has no `customer_id`, it is set to the project's;
  * if the contract has a `customer_id` different from the project's, the write is refused with **422**
    `"A contract must belong to the customer of its project"`.
* A contract without a project is not checked (unchanged behaviour).
* A project without a customer is not checked (unchanged behaviour).

### B. Accept refuses a foreign project

* `_accept_offer`: when the offer has a `project_id` of an existing project whose `customer_id` is set and differs from
  the offer's `customer_id`, the accept is refused with **422** `"The offer's customer is not the customer of its project"`.
  The check runs **before** the offer status changes, so the offer stays open and nothing is written.

### C. Items per contract

* `Contract` gains `lines: list[dict] | None`. Each element is `{description, qty, unit, price}` copied from the offer's
  `lines` element. VAT and totals are not copied (the offer keeps them). Money is not recomputed here.
* `ContractIn` gains `offer_lines: list[int] | None`, a list of positions into the linked offer's `lines`.
  * Bounds: each position `0..999`, at most 200 entries (`validation.py`, a new `LineIndexes` type next to `IdList`).
  * Positions must be unique and must exist in the offer (`< len(offer.lines)`), else **422** `"Unknown item of the offer"`.
  * `offer_lines` needs `offer_id` set and the offer must exist, else **422** `"The items of a contract come from its offer"`.
  * `offer_lines` is **not stored**. The server replaces it by `lines`, copied from the offer at those positions.
  * `offer_lines` absent (`None`): the contract keeps its stored `lines`. An edit of title, dates or rate therefore never
    drops the items.
  * `offer_lines: []` is valid and means "the customer accepted no item".
* Accept: the new contract gets `lines` = copies of **all** items of the offer (default: everything ticked).

### D. Web: the items view after accepting

* `docAccept`: after a successful accept that created a contract, a dialog "What did the customer accept?" opens before
  navigating to the project. It lists every item of the offer as a checkbox, all ticked, and shows the item's
  description, quantity, unit and price as stored in the offer.
* Save: `PUT contracts/{id}` with the contract's own fields (title, customer, project, period, hours, rate, status,
  people, offer) and `offer_lines` = the ticked positions. Then the page navigates to the project.
* "Keep all items" and Escape close the dialog with no change (the contract keeps all items).
* A failed save shows the server's message in the toast; the dialog stays open so the user can correct it.

### E. Not changed by this spec

* The planner's revenue and hours still come from the contract's plan and rate, not from the ticked items. Whether the
  ticked items should change planned revenue is an open decision (below).
* The contract edit dialog does not show or edit the items. It keeps them (rule C, `offer_lines` absent).
* No database migration: unknown record fields are kept in the row's `extra` column (`AGENTS.md`, "backwards compatible").
  The implementer confirms this on the three dialects with the migration-drift tests; if a column is needed, the spec is
  reopened.

## Errors (new)

| Status | Detail | Where |
|---|---|---|
| 422 | `A contract must belong to the customer of its project` | contract write |
| 422 | `The offer's customer is not the customer of its project` | accept |
| 422 | `The items of a contract come from its offer` | contract write with `offer_lines` |
| 422 | `Unknown item of the offer` | contract write with `offer_lines` (out of range or repeated) |

## Tests (required, `tests/backend/test_contract_items.py`)

1. A contract for a project of another customer is refused with the exact message; the contract is not stored.
2. A contract without a customer gets the project's customer.
3. Accepting an offer whose customer differs from its (existing) project is refused; the offer stays `sent`.
4. Accepting copies all items; `offer_lines: [1]` keeps only item 1 with its exact values.
5. A save without `offer_lines` keeps the stored items.
6. Out-of-range and repeated positions are refused.
7. `offer_lines` is not present in the stored contract.
8. OpenAPI contract regenerated (`make generate-openapi`); `make check-openapi` passes.
9. Web: one browser test of accept → items dialog → save, and of "Keep all items". Not run in CI on the draft PR.

## Open design decisions (for the architect)

1. **Existing contracts that already belong to another customer.** Options: (a) leave them and show a sanity warning
   `contract.customer_mismatch` listing them; (b) refuse only new writes. Draft choice: (b) plus the warning, no automatic
   change of data.
2. **Planned revenue and the ticked items.** Draft choice: no change (rule E). The logic review must decide whether
   revenue should follow the ticked items.
3. **A contract without a project** is not checked for customer. Confirm.
4. **Items with an empty or zero price.** Draft choice: copied as they are.
5. **The items dialog after an accept that created no contract** (no plan): not shown.

## Release

* Target: **v0.4.0-alpha.5** (the next pre-release after alpha.4, `main` at `a5c92ef`). Listed under `[Unreleased]` →
  `Fixed` in `CHANGELOG.md` and in `docs/roadmap.md`.
* Branch for the implementation: `fix/contract-items` from `main`, after this spec is merged. Draft PR, then the Full
  review (three Sonnet passes: code, tests, logic). The logic pass recomputes one planned contract by hand and checks
  that copied prices and quantities equal the offer's.
