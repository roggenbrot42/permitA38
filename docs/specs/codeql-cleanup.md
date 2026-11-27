# CodeQL clean-up for v0.5.0-alpha.1

Status: plan, written 2026-11-27, awaiting the maintainer's go-ahead. Target: milestone `0.5.0-alpha.1` (#2).

## 1. Starting point

On 2026-11-27, `main` had **103 open code-scanning alerts**. The workflow `.github/workflows/codeql.yaml` uses the
`security-and-quality` suite. Most alerts come from the quality queries, not the security queries. The
integration PR #196 (`feat/accounting-m4-base`) adds one note of its own: `py/unused-import` in
`permita38/core/calc.py`.

| Severity | Count | Rules |
|---|---|---|
| high (security) | 5 | js/xss-through-dom 1, py/weak-sensitive-data-hashing 2, py/clear-text-logging-sensitive-data 2 |
| error (quality) | 18 | py/side-effect-in-assert 17, py/uninitialized-local-variable 1 |
| warning | 5 | py/unreachable-statement 3, py/file-not-closed 1, py/constant-conditional-expression 1 |
| note | 75 | ineffectual-statement 17, empty-except 13, unused-import 11, mixed-returns 9, unused-global 7, cyclic-import 7, js unused-local 5, import-and-import-from 3, catch-base-exception 2, py unused-local 1 |

Outcome: **33 fixed in code** (32 on `main` plus 1 from #196), **24 closed by a CodeQL config filter**, and **47
dismissed with a written reason**. Every alert gets one of these outcomes, so the list is empty when the release
goes out.

## 2. The five high-severity alerts

| # | Location | Verdict | Reason |
|---|---|---|---|
| 70 | `web/numbering.js` `numWizard` | **Fix** (WP-1) | `${type}` and `${guess.next}` are interpolated into `innerHTML` without `esc()`. `type` comes from a `<select>` and `next` is a parsed number, so the user can only reach their own page. That makes it hard to exploit, but it is still a real pattern bug. |
| 3 | `core/service/auth.py:88` `_sign` | Dismiss: false positive | This is HMAC-SHA256 with the server secret, which is how JWT HS256 signs tokens. It does not hash a password. Passwords use PBKDF2 (`hash_password`). |
| 4 | `core/service/totp.py:112` `hash_recovery_code` | Dismiss: won't fix (architect decision) | Recovery codes are 64-bit CSPRNG values (`secrets.token_hex(8)`), not passwords chosen by a person, and they are salted. The TOTP secret is stored in plain text in the same users row (module docstring). Anyone who can read the row can already produce valid second-factor codes, so a slow KDF would add no protection. Changing the format would also need a dual-format check for existing hashes. If TOTP secrets are ever encrypted at rest, revisit this together with that change. |
| 1 | `cli/main.py:350` (import-json report) | Dismiss: false positive | It prints the **usernames** of users who have no password yet to the operator's terminal. No secret is printed. |
| 2 | `core/service/totp.py:185` audit line | Dismiss: false positive | It logs the username and the number of codes left. The code itself is not logged. |

No high-severity alert is an exploitable vulnerability.

## 3. Work packages

### Ordering

Most of the affected test files and `sql.py`, `cli/main.py`, `app2.js` and `procurement.js` are changed by #196.
**WP-1 may go to `main` at once.** WP-2 to WP-5 are branched from `main` **after #196 has merged**, so they cause
no conflicts with the accounting work. Line numbers move with #196, so find each site by its rule and pattern, not
by line number. Each package is a separate small PR.

The alert numbers in the tables below are the GitHub alert numbers on `main`. Before each package starts, run
`gh api 'repos/{owner}/{repo}/code-scanning/alerts?state=open&per_page=100' --paginate` again to get the current
locations.

### WP-1: XSS pattern in the numbering wizard (implementer, **Standard** tier)
- Files: `web/numbering.js`, `tests/web/test_planner_number_format_ui.py` or a new `tests/web/test_numbering_wizard_ui.py`.
- `numWizard`: run every interpolated value through `esc()` (`type`, `guess.next`). Check `type` against the keys
  of `LABEL` and fall back to `'invoice'` when the key is unknown. Check the other `innerHTML` templates in
  `numbering.js` for interpolations that lack `esc()`, and fix them the same way.
- Test: set an option of `n_wizard_type` to a value with `"><img src=x onerror=...>` and run the wizard. No element
  may be injected, and the button still applies the pattern.

### WP-2: side-effect asserts in backend tests (implementer-small, Light)
- Files: `tests/backend/test_atomicity.py`, `test_auth.py`, `test_calendar_api.py`, `test_flow.py`,
  `test_import_review2.py`, `test_repository_contract.py` (alerts 20–27, 79–82). This package has 6 files, so it goes
  to `implementer`, or it is split into two `implementer-small` runs of 3 files each.
- Change `assert c.delete(...).status_code == N` (or any other call with a side effect inside `assert`) to
  `r = c.delete(...)` followed by `assert r.status_code == N`. The behaviour stays the same.
- In the same files: alert `py/unused-local-variable` `test_repository_contract.py` (~line 576). Drop the binding.

### WP-3: test hygiene in the remaining test files (implementer-small, Light)
- Files: `tests/web/e2e_random/test_api_random.py` (alerts 15–19, side-effect asserts as in WP-2),
  `tests/web/conftest.py` (alert 5: after `pytest.skip` the code falls through to `yield b`, so move
  `yield b` / `b.close()` into the `try … else:` branch), `tests/backend/test_validation.py` (alert 233:
  `float("nan") if False else "x"` becomes `"x"`).

### WP-4: small production-code fixes (implementer-small, Light)
- Files: `packaging/smoke_test.py` (alert 56: open `server.log` in a `with` block or close it in `finally`; also the
  empty `except` there gets a one-line comment that says why it is ignored), `permita38/core/config.py` (the empty
  `except OSError: pass` around `chmod` gets a comment: on Windows and some file systems `chmod` fails and that is
  harmless), `permita38/core/service/calendar/ics.py` (the empty `except (ValueError, OverflowError): pass` gets a
  comment that a broken VTIMEZONE rule is skipped).
- CodeQL `py/empty-except` does not report a handler that contains a comment.

### WP-5: unused names (implementer-small, Light)
- Files: `permita38/core/repository/sql.py` (alerts: remove the unused `_INT_MIN, _INT_MAX`; `schema.py` keeps its
  own copy), `permita38/core/calc.py` (the unused import that #196 added), and then **in a second run**
  `web/app2.js`, `web/planner.js`, `web/procurement.js` (5 `js/unused-local-variable` alerts). Before removing a
  local, check it has no side effect (if the right-hand side has one, keep the call and drop only the binding).

### WP-6: CodeQL configuration (implementer-small, Light)
- Files: `.github/workflows/codeql.yaml` (add `config-file: ./.github/codeql/codeql-config.yml` to the `init` step)
  and the new `.github/codeql/codeql-config.yml`:
  ```yaml
  name: permitA38 CodeQL config
  queries:
    - uses: security-and-quality
  query-filters:
    - exclude:
        id: py/ineffectual-statement   # `...` bodies of Protocol / abstract methods; ruff covers real ones
    - exclude:
        id: py/cyclic-import           # tests import fixtures from each other on purpose
  ```
  Remove `queries: security-and-quality` from the workflow, because the config file now sets the queries.
  After the first scan on `main`, the 17 + 7 alerts are closed automatically.
- This can merge before #196, because the workflow is not part of #196.

## 4. Dismissals (done by Opus after the go-ahead, not by implementers)

Use `gh api -X PATCH repos/{owner}/{repo}/code-scanning/alerts/<n> -f state=dismissed -f dismissed_reason=<r> -f dismissed_comment=<text>`.
GitHub accepts these reasons: `false positive`, `won't fix`, `used in tests`.

| Alerts | Reason | Comment |
|---|---|---|
| 3, 1, 2 | false positive | see section 2 |
| 4 | won't fix | 64-bit random codes; the TOTP secret is stored in plain text in the same row; see docs/specs/codeql-cleanup.md §2 |
| 3 × py/unreachable-statement (test_repository_contract.py) | false positive | `pytest.raises` swallows the exception, so the following lines run |
| 10 × py/empty-except in tests/ | used in tests | best-effort clean-up in test harnesses and monkey tests |
| 11 × py/unused-import in tests/web | used in tests | pytest fixtures (`chromium`, `Server`) imported for injection, marked `noqa: F401` |
| 9 × py/mixed-returns (8 in tests, `cli/main.py:main`) | used in tests / won't fix | `main()` returns `None` or an exit code by design (`sys.exit(main())`) |
| 5 × py/unused-global-variable in tests | used in tests | module-level settings read by pytest/hypothesis |
| 3 × py/import-and-import-from (tests) | used in tests | style only |
| 2 × py/catch-base-exception (numbering tests) | used in tests | the tests catch everything a worker thread raises on purpose |

Total: 47 alerts.

## 5. Keeping the list empty afterwards
- Ask the maintainer whether to turn on **code-scanning merge protection** (a repository ruleset that blocks a PR
  on new alerts of severity `error` and above, and on security `high` and above). Until then, the "Ready = CI green"
  rule also covers new CodeQL alerts in the PR's check run.
- Implementer and reviewer prompts for new packages say: "no new CodeQL alerts; dismissals only through the
  architect".

## 6. Review tiers and release gate
- WP-1: Standard (one Sonnet `reviewer` pass covering code and test). WP-2 to WP-6: Light.
- Release gate for 0.5.0-alpha.1: the open-alert query on `main` returns 0 (or only alerts that came in after this
  plan and have their own issue).
