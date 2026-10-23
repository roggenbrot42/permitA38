# Mobile version – concept (Android)

*Status: idea only, nothing built yet.*

## Goal

Capture time and expenses and check "how many hours do I still owe this week?" from an Android phone – offline, with the same data as the desktop app, and without a cloud account.

## The idea: a companion app on the same SQLite database

The desktop app keeps everything in one **SQLite database file** (`permita38.sqlite3`) in a folder that Syncthing syncs. SQLite is built into Android, so the phone can use the very same file:

1. **Syncthing-Fork** (Android) syncs the permitA38 data folder to the phone's storage.
2. A small **native Android app** (Kotlin + Jetpack Compose, or Flutter) opens the same database with Android's built-in SQLite (via Room or plain `SQLiteDatabase`, with the folder granted through the Storage Access Framework) and the same schema. No server needed.
3. **Later, with the server mode:** when a PostgreSQL/MariaDB backend exists (the desktop code already hides the database behind a `Repository` interface), the app can instead talk to the HTTP API (`api/openapi.json` → generated client) and does not need the database file at all.

**Caveat of the file route:** a single database file synced by Syncthing is safe when one device writes at a time, but two devices writing offline produce a conflict copy of the *whole* database. The phone app must follow the same rule as the desktop (open on one device at a time, show a conflict warning) – this is the strongest argument for the server/API route or a future row-level merge.

Why not just a web page? The desktop server is Python and not available on a phone, and a PWA can't write into an arbitrary synced folder on Android. A tiny native app can, works offline, and can offer widgets and notifications.

## Scope of v1 – "the daily workflow only"

| Screen | What it does |
|---|---|
| **Today** | Big Start/Stop button, project/task picker, running timer in a persistent notification, quick-stop from the lock screen. Weekly progress ("12:30 h to go"). |
| **Quick add** | Manual entry with duration/times, last-used project preselected. |
| **Week** | Read/edit own entries, day bars with holidays and time off. |
| **Time off** | Add vacation/personal days. |
| **Receipt snap** | Take a photo of a bill → saved to `attachments/` plus a draft row in the `expenses` table (vendor/amount filled in later on the desktop). |
| **Boss glance** (read-only) | Revenue / profit this month, open invoices, free capacity next 4 weeks. |

Out of scope on the phone for v1: writing offers and invoices, templates, tax settings. Those stay on the desktop where a large screen matters.

## Technical notes

- **Shared schema = the contract.** The schema is versioned (applied migration ids in the `schema_migrations` table, `PRAGMA user_version` = number of applied migrations, migrations with an id/`requires` chain in `permita38/core/schema/migrations/sqlite/`). The phone app must read the applied ids, refuse a newer database like the desktop does, and never migrate by itself unless it ships the same migration files. Unknown fields live in each row's `extra` JSON column and must be left untouched.
- **Business logic once.** Rate resolution, capacity/holiday maths and the weekly-goal calculation must give identical results on both platforms – share test vectors (JSON fixtures) between the Python tests and the app tests. Public-holiday tables are small enough to port or to ship as a generated JSON file.
- **Timer.** The running timer is a per-user row in the `timers` table with a start timestamp, so a timer started on the phone can be stopped on the desktop and vice-versa.
- **Conflicts.** IDs are random, so new rows never collide, but the database is one file: concurrent offline edits on two devices give a Syncthing conflict copy of the whole file. The app detects `*.sync-conflict-*` copies and warns, like the desktop. Keep transactions short and close the database when the app goes to the background so Syncthing sees a complete file.
- **Permissions.** Folder access via SAF only; camera for receipts; notifications for the timer. No network permission needed for the file route.
- **Security.** The phone holds a full copy of the data – recommend device encryption + screen lock. Logins on a file-synced app only control what the UI shows (same as desktop). In the future server mode the phone would instead authenticate against the HTTP API with the same username/password login and short-lived bearer tokens (`POST /api/auth/login`), and the OpenAPI contract in `api/openapi.json` can be used to generate a client.

## Alternatives considered

| Option | Verdict |
|---|---|
| **PWA served from the desktop over LAN/VPN (e.g. Tailscale)** | Quick to build and reuses the existing UI, but needs the desktop on and reachable. Good as a *stop-gap* – the server already supports `--host 0.0.0.0`, and with the new token login the phone can sign in with the same username and password. Run it over a VPN such as Tailscale, never open to the internet. |
| **PWA / Capacitor wrapper with its own storage + custom sync** | Reinvents sync; conflicts become our problem. |
| **Native app on the synced SQLite file (chosen first)** | Offline, private, no new infrastructure, same data; one-writer-at-a-time rule. |
| **Self-hosted server with real accounts** | The right answer for larger teams; planned as a separate "server mode" (see roadmap). Phone would then be a thin client. |

## Suggested milestones

1. Document the schema (done: `schema.py` + migrations); extract shared test fixtures.
2. Read-only Android app: Today + Week from the synced database.
3. Timer + quick add (write path), notification and widget.
4. Receipt photo → expense draft.
5. Boss dashboard (read-only).
