# Handover (state of 2026-10-23)

Context: permitA38, GPL-3.0-or-later. Released: `v0.4.0-alpha.1` (draft prerelease). Read `AGENTS.md` first
(division of labour, merge gate). Roadmap: `docs/roadmap.md`.

## On `main` (reviewed, all suites green at 24747ab)
- Everything of alpha.1 plus the **planner** (feat/planner): project start/end with extensions, capacity engine with
  binding open offers (worst case), offer feasibility and "numbers don't add up" warnings, contract-first revenue basis,
  interactive "Plan & vacation" page with scenarios. Migration 0003. Reviewed by Sonnet; only SQLite verified, the
  PostgreSQL/MariaDB SQL of 0003 is untested.

## Branches pushed to origin (not merged)
| Branch | State | Next step |
|---|---|---|
| `feat/numbering-import` | Backend reviewed (fixes committed); web UI added afterwards, **browser tests never run** (no Chromium there) | run `tests/web`, second Sonnet review of the UI diff, merge. Migration is 0003 -> renumber to the next free number (0004 once calendar is not yet merged). |
| `feat/calendar` | Implemented (ICS parser, Proton share-link subscription, busy hours provider). Reviewer was still working when this was written; **web monkey seeds 7 and 99 failed** (`#/money/1999` shows a validation error; finance route has `ge=2000`) | finish review, fix that route, merge; migration 0003 -> renumber. `core/service/busy.py` is the shared contract and already exists on main. |
| `feat/accounting`, `feat/vat-return`, `feat/rbac`, `feat/backup`, `feat/sanity`, `feat/sessions`, `feat/export-gdpr`, `feat/pwa` | **WIP snapshots**, not reviewed, may not build | restart from the roadmap items; migrations 0003+ must be contiguous, renumber at merge |

The merge procedure that worked: `git merge --no-ff <branch>`, resolve conflicts (keep both sides), renumber the
migration to `CURRENT_REVISION+1` in all three dialect folders (runner needs contiguous numbers), bump
`CURRENT_REVISION`, update tests that hard-code it (`test_planning.py`, `test_totp.py`), `make generate-openapi`,
then `python -m pytest tests/backend tests/release`, `tests/backend/randomized`, `tests/web` (needs Chromium,
`PERMITA38_CHROMIUM`), `ruff check`, `ruff format --check`, `python -m mypy permita38`.

## Specs of the three branches
`docs/handover/spec-planner.md`, `spec-numbering.md`, `spec-calendar.md` are the original task specs. They mention
migration numbers 0009-0011 and an old Co-Authored-By line; the "IMPORTANT UPDATE" paragraph in each is the valid one.

## Maintainer TODO
- Finalise release assignment in `docs/roadmap.md`; do `docs/maintainer-setup.md` (repo settings, signing key,
  private vulnerability reporting); publish the draft alpha.1 release; verify the Windows bundle and Docker image.
- Roadmap additions from the last days: subcontractors, itemised expenses (per-line VAT), bill extraction from
  PDF/image, Gantt chart.
- Verify tax figures and dates marked "(verify)"; the quarterly tax-review routine first runs 2027-01-02.
- Decide the commit-identity question: the platform stop hook wants `noreply@anthropic.com` committers. `a30a0ed`
  (nic@sft.lol) is still flagged; rewriting needs a force-push of `main`, which was blocked in the cloud session.
- Known limits: wheel lacks the web UI; Windows launcher untested; Windows bundle unsigned.

## Local setup
`git clone https://github.com/roggenbrot42/permitA38.git && cd permitA38 && pip install -e '.[dev]'`;
`python -m playwright install chromium` for the browser tests; `./start-permita38.sh --data ~/Sync/permitA38`.
