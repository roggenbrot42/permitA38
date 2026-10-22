# Android app tests (future)

There is no Android app yet – see the roadmap (`docs/mobile-concept.md`). This folder reserves the place and records
the test plan so the app is built test-first and stays consistent with the desktop/web version.

## Planned test layers

| Layer | What | Tooling |
|---|---|---|
| **Shared calculation fixtures** | Rate resolution (task > project > customer > user > default), weekly goal / remaining hours, public holidays per federal state, invoice totals with per-line VAT, Kleinunternehmer. The backend publishes the expected results as JSON fixtures (`tests/backend/fixtures/`, to be generated from the oracle in `tests/backend/randomized/oracle.py`); the app's unit tests load the **same** files and must produce identical numbers. | JUnit / Kotlin test |
| **Contract tests** | The app's generated API client must match `api/openapi.json`: every endpoint, model and error shape it uses exists and has the expected types. CI fails when the contract changes without a client update. | OpenAPI generator + JUnit |
| **Repository / storage tests** | If the app reads the synced SQLite file directly: schema revision check (refuse newer `user_version`), migrations, conflict-copy handling, unknown-column tolerance. | Robolectric / Room-style tests |
| **Instrumented UI tests** | Start/stop timer, quick add, week view, time off, receipt photo -> expense draft, login (incl. TOTP), session expiry. | Espresso / Compose UI test |
| **Random / monkey tests** | Android `monkey`-style input against a seeded demo dataset; invariants: no crash/ANR, no leaked data in logs, totals equal the backend oracle. | `adb shell monkey` + assertions |

## Conventions

- Test data is always fake (e.g. "ACME GmbH") – never real customer or business data.
- Seeds are logged so failures can be replayed.
- Nothing here runs in CI until the app exists; the backend/web suites live in `tests/backend/` and `tests/web/`.
