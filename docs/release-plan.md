# Release plan: first alpha (v0.4.0-alpha.1)

*Owner: maintainer. Status: plan, to be confirmed. Mechanics are in [`RELEASING.md`](../RELEASING.md); what comes after
the alpha is in [`roadmap.md`](roadmap.md).*

## 1. Versioning policy

* **SemVer 2.0.0** for everything user-visible: git tag, CHANGELOG, GitHub release, container tag.
* **Package metadata uses PEP 440**, because pip/PyPI do not accept SemVer pre-release spelling:

  | Release | Git tag / container tag | `permita38.__version__` |
  |---|---|---|
  | first alpha | `v0.4.0-alpha.1` / `0.4.0-alpha.1` | `0.4.0a1` |
  | beta | `v0.5.0-beta.1` | `0.5.0b1` |
  | release candidate | `v1.0.0-rc.1` | `1.0.0rc1` |
  | final | `v1.0.0` | `1.0.0` |

  The mapping is one tested function (`semver_to_pep440` in `scripts/release.py`), and CI fails if tag, package and
  CHANGELOG disagree (`tests/release/test_version_consistency.py`).
* **Pre-1.0 rules:** while the major version is 0, a minor bump (0.x) may change the API, the data format and the
  configuration, but every incompatible change needs a CHANGELOG entry under "Changed" and, for stored data, a database
  migration (migrations are append-only and never edited after a release). Patch releases (0.x.y) contain only fixes.
* **From 1.0.0:** breaking API/config/data changes only in a major version; the database migration chain is supported
  from every 1.x revision.
* Pre-release counters start at 1. Tags are never moved, deleted or re-used (a broken alpha is superseded by the next).
* Tax rules are data, not API: a change of statutory rates or limits is released as a patch and called out in the notes.

## 2. Release trains

| Train | Purpose | Tag | Audience | Container / downloads |
|---|---|---|---|---|
| alpha | features land, formats may still break, expect bugs | `v0.4.0-alpha.N` | technical early adopters, with backups | pre-release; no `latest` image tag |
| beta | feature-complete for the milestone, only fixes and docs | `v0.5.0-beta.N` | friendly users with real (copied) data | pre-release |
| rc | candidate for 1.0, no changes except blockers | `v1.0.0-rc.N` | anybody who wants to test the final | pre-release |
| 1.0 | stable | `v1.0.0` | everybody | normal release, `latest` image tag |

Cadence: alphas roughly every 2-3 weeks once alpha.1 is out; each alpha has a short, explicit scope (section 3).

## 3. Scope of v0.4.0-alpha.1

**In (merged today):**

* Time tracking, weekly goal, planning and forecast, holidays
* Customers and projects
* Offers and invoices, PDF output, document templates
* Expenses
* Simple tax estimates and reserves
* SQLite storage (single database file), versioned migrations, backup command (`permita38 backup`)
* Username/password login and **TOTP two-factor authentication**
* Docker image and compose files (non-root, health check)
* Standalone downloads for Windows and Linux (x86_64)

**Explicitly out, planned for later alphas** (see the roadmap for the assignment per release):

| Release | Planned |
|---|---|
| alpha.2 | roles and rights, validation / sanity-check layer |
| alpha.3 | accounting and the German VAT return (Umsatzsteuer-Voranmeldung), structured export |
| alpha.4 | legal-retention backup module (GoBD), per-IP login throttling, server-side session revocation |
| later | Android app, PostgreSQL/MariaDB backends, sync/merge, SSO, e-invoice, DATEV, ELSTER research, translations |

Everything in the "out" list must be **absent or clearly hidden** in alpha.1, not half-working.

## 4. Entry criteria (quality gates)

A release candidate commit may be tagged only when all of this holds. The automated ones are enforced by the Release
workflow; the rest is a manual checklist recorded in the release pull request.

| Gate | How |
|---|---|
| CI green on `main`: Linux and Windows, Python 3.12, 3.13, 3.14 | `ci.yaml` |
| Backend suite passes (no skips except documented); test count does not shrink versus the previous release | `pytest tests/backend` |
| Randomized / monkey suites pass (`make test-random`, one `HYPOTHESIS_PROFILE=thorough` run, web e2e random simulations with a fixed seed list) | manual, results in the PR |
| Coverage of `permita38/` at least 85 % (target 90 % before beta); no drop larger than 1 point | `pytest --cov` |
| Lint, format, mypy clean; `api/openapi.json` up to date | `ci.yaml`, release workflow |
| Release tooling tests pass (versions, changelog, workflows, licenses) | `pytest tests/release` |
| Migrations: up from every previous released revision on a database with realistic data, and a restore of a backup from the previous release opens correctly. There is no down-migration; rollback = restore the backup (section 8) | `tests/backend/test_migrations.py` + manual upgrade test |
| Security review checklist done: auth (hashing, token lifetime, TOTP replay/brute force), CORS and cookie flags, file upload and attachment paths (traversal), PDF/template injection, secrets never in the data folder, logging free of secrets, `SECURITY.md` current | manual, reviewer signs in the PR |
| Dependency audit: `pip-audit` has no known vulnerability in runtime dependencies; licenses GPLv3-compatible; `THIRD_PARTY_NOTICES.md` current | manual + `generate_notices.py --check-licenses` |
| CodeQL (Python, JavaScript) without open high/critical alerts | `codeql.yaml` |
| Docs complete: README, getting-started, Docker, security/2FA, upgrade notes, known limitations | manual |
| Clean-install test of the **wheel** (fresh venv) and of the **bundle** on Windows and Linux: start, set up, log in, create a customer and an invoice, render a PDF | `release.yaml` (`packaging/smoke_test.py`); manual click-through on one real Windows machine |
| Docker image smoke test (health, non-root, data volume survives restart) | `release.yaml` + manual restart check |
| CHANGELOG section dated, tag annotated (and signed), package version matches | `release.py`, `release.yaml` |

## 5. Platforms and artifacts

| Artifact | Decision |
|---|---|
| sdist + wheel | built and verified; **not uploaded to PyPI** for the alpha (name reservation and trusted publishing are a beta task). Note: the wheel contains the API only, the `web/` frontend is shipped in the bundles and the image (known limitation) |
| Windows x86_64 `.zip` (PyInstaller, onedir, `permita38.exe` + launcher) | yes. Unsigned, so Windows SmartScreen will warn; documented in the release notes. Code signing is a beta task |
| Linux x86_64 `.tar.gz` (built on Ubuntu 22.04 for older glibc) | yes |
| macOS arm64 | **not now.** Needs Developer ID signing and notarisation to run without scary warnings, and a Mac runner; macOS users use Docker or `pip install`. Revisit for beta |
| Linux arm64 bundle | no (Docker image is multi-arch: amd64 + arm64) |
| Container image `ghcr.io/roggenbrot42/permita38:0.4.0-alpha.1` | yes, no `latest` tag for pre-releases |
| `SHA256SUMS`, CycloneDX SBOM, build provenance attestations | yes, for every file |

## 6. Known limitations to publish with alpha.1

* Alpha quality: data format and API may still change; **keep backups** (`permita38 backup`) and test with copies.
* No roles/rights yet: every account is a full user; do not expose the server to untrusted people.
* No per-IP login throttling and no server-side session revocation: a stolen token stays valid until it expires;
  change the password / rotate the secret key to invalidate all sessions.
* Not an accounting system and no VAT return yet; tax figures are estimates, not tax advice.
* Syncing one database file with Syncthing is safe only with one writing device at a time.
* Windows bundle is unsigned (SmartScreen warning); no macOS bundle; the wheel has no web UI.
* English UI, German document templates only.

## 7. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Data loss or corruption by a migration | low | high | migration tests from every revision, automatic backup before migrating, restore documented |
| 2 | Wrong tax numbers misleads users | medium | high | "estimate, not advice" in UI and docs, sources cited in code, quarterly tax-rule review issue |
| 3 | Security flaw in auth/2FA | medium | high | security checklist, CodeQL, private vulnerability reporting, fast patch release |
| 4 | PyInstaller bundle fails on user machines (AV, missing libs) | medium | medium | smoke test of the archive in CI, Docker and pip alternatives, bundle is onedir |
| 5 | Release pipeline breaks on tag day | medium | medium | `workflow_dispatch` dry run one day before, SHA-pinned actions, runbook for failures |
| 6 | Dependency vulnerability appears | medium | medium | Dependabot, `pip-audit` gate, constraints file for the image |
| 7 | Supply-chain tampering | low | high | pinned actions, least-privilege tokens, attestations, signed tags, environment approval |
| 8 | Scope creep delays alpha.1 | high | medium | scope frozen in section 3; everything else goes to alpha.2+ |
| 9 | Tester feedback gets lost | medium | low | `alpha-feedback` issue form and label, triage weekly |

## 8. Rollback plan

* **User side:** every upgrade creates a backup of the database first. Roll back by stopping the app, restoring that
  backup file and starting the previous version (bundle or image tag; old tags stay available). Migrations are not
  reversible; this is why the backup is mandatory.
* **Project side:** a bad alpha is *yanked, not deleted* (`RELEASING.md`): mark the GitHub release as unsuitable with a
  notice, point `latest`-style references to the previous good image digest, publish `alpha.(N+1)` with the fix.

## 9. Communication plan

* CHANGELOG is the source: the GitHub release notes are generated from its section for the tag.
* GitHub release (pre-release) with: highlights, **known limitations**, upgrade/backup instructions, checksums and how to
  verify them, link to the feedback form.
* Issue label **`alpha-feedback`** (issue form `alpha_feedback.yml`); weekly triage; a pinned issue "alpha.1 feedback".
* Security reports go to private vulnerability reporting (`SECURITY.md`), never to public issues.
* Announcement text (README badge, release page, any community channel the maintainer uses) is the release notes, no
  claims beyond them.

## 10. Exit criteria for beta

* All "alpha.2-4" scope items in section 3 are merged and documented, or consciously moved with a CHANGELOG note.
* No known data-loss, security or wrong-calculation bug; no open `alpha-feedback` issue labelled `blocker`.
* Upgrade path from every alpha tested (migration test matrix from alpha.1 on).
* At least three independent testers used a real (copied) data set for two weeks; feedback triaged.
* Coverage 90 %, randomized suites pass with the thorough profile, security review repeated for new features
  (roles, accounting, export).
* Windows bundle code-signing and PyPI publishing decided; macOS decision documented.

## 11. Timeline template (alpha.1)

Dates are relative to the tag day **T**.

| Day | Activity |
|---|---|
| T-10 | Scope freeze. Merge the open feature branches (SQLite/Docker/2FA/release engineering). Create the milestone `0.4.0-alpha.1` |
| T-8 | Release candidate on `main`; run the full manual gate list (randomized, thorough profile, security checklist) |
| T-6 | Fix findings; dependency audit; refresh `THIRD_PARTY_NOTICES.md` |
| T-4 | Docs and known-limitations review; draft CHANGELOG section |
| T-3 | **Dry run** of the Release workflow (`workflow_dispatch`); fix pipeline problems |
| T-2 | Manual click-through of the Windows bundle, Linux bundle and Docker image; upgrade test from the previous release |
| T-1 | Release commit pull request (`chore(release)`), CI green, final review; go / no-go |
| T | Merge, update `main`, `make release VERSION=0.4.0-alpha.1 RELEASE_FLAGS=--sign`, inspect, push tag, approve the `release` environment, review the draft, publish |
| T+1 | Verify downloads and attestations from a clean machine, announce, open the feedback issue |
| T+7 | First triage of `alpha-feedback`; decide on alpha.1.1 vs. fold into alpha.2 |
