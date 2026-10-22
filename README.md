# ⏱ permitA38

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

**Time tracking, planning, offers, invoices and tax reserves for freelancers and small businesses in Germany.**
One small app, one folder of plain files, synced across all your devices with Syncthing.

![Login](docs/img/login.png)

permitA38 answers the questions a freelancer actually asks every day:

- *How many hours do I still have to work this week?*
- *Do I have capacity for that new project in January?*
- *What did I earn this month – and how much of it is **not** mine (VAT, income tax, trade tax)?*
- *Which work have I done but not yet invoiced?*

It runs on **Windows and Linux** (anything with Python 3.10+), needs **no database, no cloud and no account with anyone but yourself**. Your data stays in a folder you own.

> New here? Read the **[Getting started guide](docs/getting-started.md)** – you'll be tracking time in 5 minutes.

![Today](docs/img/today.png)

---

## Features

| | |
|---|---|
| **⏱ Time tracking** | One-click timer or manual entries for customers and personal projects, with tasks, notes and a billable flag. A running timer shows up on your other devices, too. |
| **🎯 Weekly goal** | "You still need **17:30 h** this week – about **4:22 h** per remaining day." Takes public holidays and your time off into account. Optional flex-time balance. |
| **📈 Forecast** | Enter contracts (hours per week *or* a total hour budget between two dates). permitA38 lays them out week by week, compares them with your capacity and shows free hours and expected revenue. Open offers appear as *tentative* load. |
| **🏖 Holidays & time off** | Vacation, personal days, sick days – plus German public holidays for your federal state. They reduce capacity in the forecast. |
| **💶 Rates** | Hourly rate per customer, project, **task**, or person. The most specific one wins: *task → project → customer → person → default*. |
| **👥 Customers** | Address, tax number, VAT ID, point of contact, notes, default rate – and a history of their projects, offers and invoices. |
| **🧾 Offers & invoices** | Create an offer → send → mark accepted (creates project and contract) → bill by tracked time or by items. PDFs from templates you choose (DIN 5008 layout, German or English, logo support). Gap-free numbering, VAT per line, Kleinunternehmer (§19 UStG) support. |
| **💳 Expenses** | Enter bills with an attached PDF/photo, assign them to a project or keep them as general expenses. VAT is split out automatically. |
| **🏛 Taxes (Germany)** | Dashboard for revenue, profit, VAT and income-based taxes for **Freiberufler**, **Gewerbetreibende** (incl. Gewerbesteuer with §35 credit) or **UG/GmbH**. Shows your monthly *set-aside* and what's left for you. |
| **🧑‍🤝‍🧑 Team & roles** | Add people to your workforce. **Members** track their own time and time off. **You, the boss,** plan, bill and see the money. Every person has their own login. |
| **🔄 Sync-friendly** | Every record is its own small JSON file → Syncthing, Nextcloud or Dropbox merge changes from different devices without conflicts. |

### A look around

| Planning forecast | Money & taxes |
|---|---|
| ![Planning](docs/img/planning.png) | ![Money](docs/img/money.png) |

| Invoice editor | PDF output |
|---|---|
| ![Invoice editor](docs/img/invoice-editor.png) | ![PDF](docs/img/invoice-pdf.png) |

*(Screenshots show demo data.)*

---

## Quick start

**Requirements:** Python 3.10 or newer. The launcher creates a virtual environment and installs the (few) dependencies on first run.

```bash
git clone https://github.com/roggenbrot42/permitA38.git
cd permitA38

# Linux / macOS
./start-permita38.sh --data ~/Sync/permitA38

# Windows (double-click start-permita38.bat, or in a terminal:)
start-permita38.bat --data "C:\Users\you\Sync\permitA38"
```

Your browser opens at <http://localhost:8765>. On the first start you create your boss account – done.

- `--data` points to the folder where all your data lives. **It is remembered** in your per-device config file, so next time just run `./start-permita38.sh`.
- Without `--data` the folder defaults to `~/permitA38`. You can also set `PERMITA38_DATA`.
- Other options: `--port 8765`, `--host 0.0.0.0` (let your team reach it over your LAN), `--no-browser`, `-c path/to/config.yaml`.
- If you prefer pip: `pip install -e .` and run `permita38`.

> Tip: make a desktop shortcut / autostart entry for the launcher so permitA38 is one click away.

---

## Login & accounts

- **First run:** a setup screen creates the first account – the **boss** – with a username and a password (at least 8 characters).
- **Everyday use:** sign in with username and password. The server hands out a signed token (valid 12 hours); the web app sends it as `Authorization: Bearer …`. A same-site `HttpOnly` cookie carries the same token so that PDFs and attachments open in a new tab.
- **Team members:** the boss creates accounts under **Team** (username + initial password). Members can change their password via the account menu.
- **Protection:** passwords are stored only as salted PBKDF2-SHA256 hashes; five failed attempts lock a username out for a few minutes; changing a password signs out every other session.
- **Forgot your password?** Run on the machine that has the data:

  ```bash
  permita38 set-password <username>
  ```

> **Honest security note:** logins control what permitA38 *shows*. Because the data is plain files in a folder you sync, anyone who can open that folder can read all of it. Only share the sync folder with people you'd trust with the books. A real server mode (one hosted instance, nobody touches the files) is on the roadmap.

---

## Using permitA38 on all your devices (sync)

permitA38 has no cloud. **The data folder *is* the database**, and a file-sync tool moves it between your computers. This is the simplest approach that works offline, keeps your data private and costs nothing.

**With Syncthing (recommended):**

1. Install [Syncthing](https://syncthing.net/) on each desktop and share one folder, e.g. `~/Sync/permitA38`.
2. On every device, start permitA38 once with `--data` pointing at that folder.
3. Use it on any device with the same logins. Changes appear on the others within seconds (reload the page).

**Why this works well**

- **One file per record** (`entries/<id>.json`, `invoices/<id>.json`, …) with random IDs. Two devices almost never edit the same file, so there is nothing to conflict.
- Files are written atomically; half-synced files are ignored until complete.
- If a conflict ever happens anyway, Syncthing creates a `*.sync-conflict-*` file. permitA38 **ignores** those and shows a warning banner so you can compare and delete the one you don't need.
- Your running timer is stored per person, so you can start it on the laptop and stop it on the desktop.
- Each device has its own `config.yaml` (with its own secret signing key) – it is **not** in the synced folder, so sessions are per device.

**Alternatives:** Nextcloud, Dropbox, OneDrive, a NAS share or a USB stick work just as well. Keep Syncthing's file versioning on – it doubles as a free undo history.

**Back-ups:** the data is plain, human-readable JSON. Back up the folder like any other – and keep one copy that is *not* synced.

---

## Roles and your team

| | Boss | Member |
|---|:---:|:---:|
| Track own time, own time off | ✅ | ✅ |
| See projects they are assigned to | ✅ | ✅ |
| Customers, rates, contracts, forecast | ✅ | – |
| Offers, invoices, expenses, tax dashboard | ✅ | – |
| Edit others' time, manage team | ✅ | – |

---

## How the money part works

Set your **company type**, **federal state**, **Kleinunternehmer** status and **monthly health/pension insurance** under *Settings*. Then permitA38 computes, from your paid invoices and your expenses:

- **Revenue, expenses, profit** – for the month and for the year (EÜR logic: revenue counts when it is *paid*; switch to "when invoiced" in Settings if you do accrual accounting).
- **VAT payable** = VAT on your revenue − input VAT on your expenses (none for Kleinunternehmer).
- **Income tax** with the current §32a EStG tariff (2025/2026), optional splitting, church tax and **Solidaritätszuschlag**; **Gewerbesteuer** (Freibetrag 24,500 €, Hebesatz, §35 credit) for trade businesses; **KSt + Soli + GewSt** for UG/GmbH.
- **Monthly set-aside** = VAT + your projected effective tax rate × this month's profit + social insurance → *"You can take home about …"*.

> ⚠️ **These are planning estimates, not tax advice.** They simplify a lot (e.g. social insurance is treated as fully deductible, no Hinzurechnungen for Gewerbesteuer, no GmbH owner salary/dividends). Use them to know how much to put aside – and let your Steuerberater do the real calculation. Tax tables live in [`permita38/core/tax.py`](permita38/core/tax.py) and are easy to update each year.

---

## Architecture

permitA38 is split into a **backend** (HTTP API) and a **frontend** that only talks to that API. The layout follows the approach of [stustapay](https://github.com/stustapay/stustapay).

```
web/                      vanilla-JS single-page app (no build step)  ──HTTP/JSON──┐
                                                                                   ▼
permita38/api/            FastAPI app: thin routers + auth dependencies (one router per domain)
permita38/core/service/   services: business rules, permissions, numbering, timers, documents
permita38/core/           pure domain logic (rates, capacity, forecast, tax, holidays, PDF) + pydantic models
permita38/core/store.py   repository layer: one JSON file per record in the data folder
api/openapi.json          the generated API contract
```

- **Contract first:** the API is described by OpenAPI. `api/openapi.json` is committed (`make generate-openapi`, checked in CI); interactive docs are served at `/api/docs`. A mobile app or a different frontend can be generated from it.
- **Services, not routers, hold the logic.** Routers parse requests and call a service; services raise domain errors that the API maps to `{"detail": …}` responses.
- **Typed records, tolerant files:** pydantic models describe every record, but unknown fields are preserved so files written by a newer version or another device survive a round-trip.
- **Storage is a layer:** everything goes through `core/store.py`. Another backend (SQLite, Postgres) could be swapped in for a hosted multi-user mode without touching routers or the frontend.
- **Frontend hosted separately?** By default the API serves `web/` itself. To host the frontend elsewhere set `serve_web: false` and `cors_origins: ["https://your-frontend"]` in the config, and point the frontend at the API URL.

**Per-device config** (`~/.config/permita38/config.yaml`, on Windows `%APPDATA%\permita38\config.yaml`) is created on first run:

```yaml
data_dir: /home/you/Sync/permitA38
secret_key: <random, generated>   # signs login tokens; never share, never sync
host: 127.0.0.1
port: 8765
cors_origins: []
serve_web: true
```

**Inspired by stustapay:** FastAPI + service layer + pydantic, OpenAPI contract between backend and frontend, bearer-token auth via dependencies, CLI sub-commands, ruff/mypy/pre-commit/CI conventions.
**Deliberately different:** no PostgreSQL and no always-on server – the synced folder is the database so it works offline on a laptop; no React/Nx build – the UI is plain JS you can read and change in a text editor.

---

## Where is my data?

```
permitA38/               ← your data folder (sync this)
├── settings.json        company data, tax settings, defaults
├── users/ customers/ projects/ tasks/
├── entries/             one file per time entry
├── contracts/ absences/ offers/ invoices/ expenses/
├── templates/           your own document templates
├── attachments/         uploaded bills and your logo
└── timers/              the running timer of each person
```

Everything is UTF-8 JSON – easy to read, back up, script or migrate.

---

## Document templates

Pick a template per document: **Classic** (colour bar), **Modern** (header band, English) or **Minimal** (black & white) – or create your own under *Settings → Document templates*: layout, accent colour, language, intro/closing texts with placeholders such as `{customer_name}`, `{due_date}`, `{number}`. Upload a JPEG logo in Settings to put it on every document.

Invoices include what German law requires (§14 UStG): both addresses, tax number or VAT ID, sequential number, date, service period, net/VAT/gross per rate, and the §19 note for Kleinunternehmer.

---

## Roadmap

- 📱 **Android companion app** – see [`docs/mobile-concept.md`](docs/mobile-concept.md): a small offline-first app that reads/writes the same synced folder (via Syncthing-Fork).
- Optional **server mode** (hosted instance, database storage backend) for teams
- Recurring invoices, credit notes, reminders (Mahnungen)
- DATEV / CSV export for your tax advisor
- UI translations (currently English UI, German document templates)

Ideas and pull requests are welcome.

---

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

make test              # pytest (FastAPI TestClient, no network needed)
make lint              # ruff + mypy
make format            # ruff formatter
make run               # start the server on :8765
make generate-openapi  # refresh api/openapi.json after changing routers/models
make check-openapi     # fail if the contract is stale (also runs in CI)
```

See [AGENTS.md](AGENTS.md) for repository conventions and [CHANGELOG.md](CHANGELOG.md) for what changed.

## License

permitA38 is free software, licensed under the **GNU General Public License v3.0 or later** – see [LICENSE](LICENSE). You may use, study, change and share it; derived works must stay under the same license.
