# Specification: capacity and demand meet at the project

Status: **proposed, draft**. Author: drafted by a Haiku session from the maintainer's domain rule; **architect sign-off
pending** (Sonnet 5.5, `AGENTS.md` "Division of labour"). Do not implement before sign-off. Review tier: **Full** (changes
the planner's capacity and gap numbers, the revenue and margin rows, and which person is counted on a contract).

## Goal

The maintainer's rule (2026-10-26, product owner):

> In the real world, people aren't allocated to contracts, people are allocated to projects. People have a capacity
> that's allocated to the project in a planner. Subcontractors write offers for specific projects and are allocated to
> that specific project.

So the planner works on **projects**:

1. Capacity (an employee's hours, a subcontractor's hours) is allocated to a project. An allocation is the only thing
   that gives a project capacity.
2. Demand (contracts and open offers) belongs to a project and to a customer. A contract does not decide who does the
   work.
3. A project's demand is covered by the capacity allocated to that project, whoever provides it. Employee and
   subcontractor capacity of one project are therefore **one pool**.

## What changes

Today (`docs/planning.md`, `docs/specs/subcontractors.md` W4 and W14, `docs/specs/planner-per-project.md` E5):

* Demand of a contract is spread over its **assignees** (`user_ids`, or `plan.user_ids`). An assignee's hours go to that
  person's own allocations; a subcontractor's hours go to the external pool. Pools are **not netted**.
* Result: an employee with 40 h allocated to P is "idle" on P while a contract assigned to a subcontractor leaves a gap
  on P; a contract without the right assignee shows a gap even when P has spare capacity.

After this spec:

* A contract's or offer's demand is spread over **its project's capacity**, not over assignees. The project pool is the
  sum of the usable employee allocations and the usable external allocations of that project.
* Gaps are per **project and day**, as before: `max(0, assigned - capacity)` of the project's pool. Capacity of one
  project never covers demand of another project (the ring fence stays, decided 2026-10-24).
* `user_ids` of a contract **no longer decides** planner demand. The planner does not read it. Whether the field and the
  "Who works on it" checkboxes stay in the contract form is open decision 6 (the data format is unchanged either way).

## Product owner decisions (given, not open)

1. Capacity is allocated to projects, for employees and subcontractors alike.
2. The ring fence stays: capacity of project P is usable only for demand of project P (no move to other projects).
3. Demand is covered by the project pool; no contract assignment is needed to use a subcontractor's hours.
4. A subcontractor's hours are an allocation to the project named in its accepted offer (already true, see
   `docs/contracts/subcontractors.md`).
5. **A contract reflects only what is owed to a customer** (maintainer, 2026-10-26): customer, project, period, hours,
   rate, status and the accepted items. It does not say who works on the project, so it does not drive capacity.

## Design

### A. Pool per project (planner)

* For each project and day: `cap` = usable employee allocation hours (after the existing pro rata scaling per person
  and day, `planning.md` "Capacity") + usable external allocation hours of that project.
* `assigned` = the demand of all items of the project (contracts with the project, open offers with the project),
  spread as today over the item's period and hours (`hours_per_week` or `total_hours`, holidays and absences excluded
  on the **project's** workdays).
* `gap` = `max(0, assigned - cap)` for the day. The gap is shown on the project, not on a person.
* The internal/external split of a project stays in the response for reporting (`internal` and `external` blocks of
  each scope), but the **gap is computed on the netted pool**. Open decision 1 covers how the split is shown.

### B. Demand without assignees

* An item's demand needs no assignee. An item of a project with **no** allocation has its whole demand as gap (as a
  project with no capacity has today).
* `user_ids` of a contract is ignored by the planner. Every existing `user_ids` value stays in the data.
* `GET /api/plan/day-table`, `GET /api/forecast` and `POST /api/plan/simulate`: the response keeps its fields; the
  per-person blocks (`user_id` filter) keep working for the person's **allocations** only (their own hours and the
  demand of their projects they are allocated to are not separated any more: the person view shows their allocations
  and the project gaps they belong to).

### C. Revenue, cost and margin (unchanged formulas)

* Planned revenue of an item: unchanged (contract or offer plan, rate from the item, else project, else customer).
* External cost: unchanged, the planned cost of the external allocation's hours (`ext.week_cost`), paid whether or not
  demand is assigned. Margin = revenue minus external cost (unchanged).
* No money is recomputed in a new way: the change moves **hours** between rows, not euro amounts. The logic review
  checks that the euro figures of a week equal the figures before this change when all demand is covered.

### D. Warnings

* `allocation.no_demand` (info) now means: the project's pool has capacity in a week and the project has no demand.
  It no longer means "the contractor is not assigned to a contract".
* `contract.assignee_mismatch` is **removed** from the plan (not added): there are no assignees to mismatch.
* New info warning `project.capacity_unused` is **not** added in this spec (open decision 4).

### E. Not changed by this spec

* Ring fence per project (decided), external allocations are created only by accepting an incoming offer, and the
  rules of accept, unaccept and cancel (`docs/contracts/subcontractors.md`).
* Customer rule on contracts (`docs/specs/contract-items.md`, separate fix).
* Data format: no migration. `user_ids` stays on contracts and offers, unknown fields stay in `extra`.

## Tests (required)

Rewrite the planner expectations of `docs/specs/subcontractors.md` W4, W14 and `docs/specs/planner-per-project.md` E5
for the project pool, with hand-computed numbers:

1. Employee 40 h/week on P, subcontractor 20 h/week on P (both accepted), a contract of P with **no** assignee and
   50 h/week over one week: project P capacity 60, assigned 50, gap 0, no `allocation.no_demand`.
2. Same, with 70 h/week demand: gap 10 on P only, Q unchanged.
3. P has a subcontractor allocation and no demand: `allocation.no_demand` (info) for that week; external cost unchanged.
4. A contract with `user_ids` of a person not allocated to P: no effect on P's numbers.
5. Sum of gaps over projects equals the team total gap (no hidden netting across projects).
6. Randomized: the ring fence property (a project's gap never depends on another project's allocations) stays in
   `tests/backend/randomized/`, rewritten for the pool model. Run once locally before merge (`make test-summary
   SUITES="random"`), because the planner and the data model are touched.

## Open design decisions (for the architect)

1. **Split of the netted pool.** Show the internal and external shares of a project's assigned hours separately (day
   by day, which pool covers demand first), or only the netted gap? Draft choice: show both shares, cover demand with
   the internal pool first (so that employee hours are not left idle while a subcontractor is paid), gap on the netted
   pool.
2. **Person view.** A person's own load (the `user_id` filter): show the person's allocated hours per project, not
   contract demand. Draft choice: yes, allocations only.
3. **Contracts with no project.** Their demand has no pool. Draft choice: unchanged (the `unassigned` scope), no gap.
4. **Unused project capacity warning** (info): not in this spec. Confirm.
5. **Existing data.** Contracts with `user_ids` keep working. Planned figures of existing data change where a contract
   was assigned to a person without allocation to its project. Confirm that this change is expected and needs no data
   migration.
6. **Assignees on contracts.** Remove "Who works on it" from the contract form (a contract is only what is owed), or keep
   it as information? Draft choice: keep the field in the data and hide it from the form, so existing values survive;
   the subcontractor assignee fix (draft PR #41) then becomes unnecessary. Maintainer to confirm.

## Release

* Target: **v0.4.0-alpha.5** (listed in `docs/roadmap.md` under alpha.5). Full review tier: code, tests and logic
  passes (three Sonnet 5.5 passes). The logic pass recomputes one project's week by hand before and after.
* Branch for the implementation after sign-off: `feat/project-capacity` from `main`.
