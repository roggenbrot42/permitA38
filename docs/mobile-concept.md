# Mobile version – concept (Android)

*Status: idea only, nothing built yet.*

## Goal

Capture time and expenses and check "how many hours do I still owe this week?" from an Android phone – offline, with the same data as the desktop app, and without a cloud account.

## The idea: a companion app on the same synced folder

The desktop app stores one small JSON file per record in a folder that Syncthing syncs. The phone can join that same sync:

1. **Syncthing-Fork** (Android) syncs the permitA38 data folder to the phone's storage.
2. A small **native Android app** (Kotlin + Jetpack Compose, or Flutter) reads and writes **the same files** through the Storage Access Framework. No server, no extra sync logic – Syncthing does the transport, the file-per-record format already avoids conflicts.

Why not just a web page? The desktop server is Python and not available on a phone, and a PWA can't write into an arbitrary synced folder on Android. A tiny native app can, works offline, and can offer widgets and notifications.

## Scope of v1 – "the daily workflow only"

| Screen | What it does |
|---|---|
| **Today** | Big Start/Stop button, project/task picker, running timer in a persistent notification, quick-stop from the lock screen. Weekly progress ("12:30 h to go"). |
| **Quick add** | Manual entry with duration/times, last-used project preselected. |
| **Week** | Read/edit own entries, day bars with holidays and time off. |
| **Time off** | Add vacation/personal days. |
| **Receipt snap** | Take a photo of a bill → saved to `attachments/` plus a draft `expenses/` record (vendor/amount filled in later on the desktop). |
| **Boss glance** (read-only) | Revenue / profit this month, open invoices, free capacity next 4 weeks. |

Out of scope on the phone for v1: writing offers and invoices, templates, tax settings. Those stay on the desktop where a large screen matters.

## Technical notes

- **Shared format = the contract.** Freeze and document the JSON schema; add a `schema_version` to `settings.json`; both apps ignore unknown fields so they can evolve independently.
- **Business logic once.** Rate resolution, capacity/holiday maths and the weekly-goal calculation must give identical results on both platforms – share test vectors (JSON fixtures) between the Python tests and the app tests. Public-holiday tables are small enough to port or to ship as a generated JSON file.
- **Timer.** The running timer is a per-user file (`timers/<user>.json`) with a start timestamp, so a timer started on the phone can be stopped on the desktop and vice-versa.
- **Conflicts.** IDs are random, so new entries never collide. Edits to the same record on two devices create a Syncthing conflict file; the app ignores `*.sync-conflict-*` files and surfaces a warning like the desktop does.
- **Permissions.** Folder access via SAF only; camera for receipts; notifications for the timer. No network permission needed.
- **Security.** The phone holds a full copy of the data – recommend device encryption + screen lock. Logins on a file-synced app only control what the UI shows (same as desktop). In the future server mode the phone would instead authenticate against the HTTP API with the same username/password login and short-lived bearer tokens (`POST /api/auth/login`), and the OpenAPI contract in `api/openapi.json` can be used to generate a client.

## Alternatives considered

| Option | Verdict |
|---|---|
| **PWA served from the desktop over LAN/VPN (e.g. Tailscale)** | Quick to build and reuses the existing UI, but needs the desktop on and reachable. Good as a *stop-gap* – the server already supports `--host 0.0.0.0`, and with the new token login the phone can sign in with the same username and password. Run it over a VPN such as Tailscale, never open to the internet. |
| **PWA / Capacitor wrapper with its own storage + custom sync** | Reinvents sync; conflicts become our problem. |
| **Native app on the Syncthing folder (chosen)** | Offline, private, no new infrastructure, same data. |
| **Self-hosted server with real accounts** | The right answer for larger teams; planned as a separate "server mode" (see roadmap). Phone would then be a thin client. |

## Suggested milestones

1. Document and version the data format; extract shared test fixtures.
2. Read-only Android app: Today + Week from the synced folder.
3. Timer + quick add (write path), notification and widget.
4. Receipt photo → expense draft.
5. Boss dashboard (read-only).
