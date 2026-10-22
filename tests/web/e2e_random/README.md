# Random end-to-end tests

Seeded random "users" that drive permitA38 through the **real browser UI** and through the **HTTP API**, and
compare what they see with an independent model of what should have happened.

```bash
pip install playwright httpx            # + a Chromium (see below); the API tests need only httpx
pytest tests/web/e2e_random -q              # everything, ~3 minutes
pytest tests/web/e2e_random -m e2e_random   # same, selected by marker
pytest tests/web/e2e_random/test_api_random.py -q          # no browser needed, ~45 s
```

| File | What it does |
|---|---|
| `test_user_journeys_random.py` | A model-based user: first-run setup + login screen, then ~20 weighted random actions (add customer/project, start/stop timer, manual time in `1:30`/`1.5`/`90m`/`2h 15m`/from-to, edit/delete entries, offers with random lines and VAT, finalize, accept → project + contract, invoice from tracked time, mark paid, expenses, vacation, change password, sign out + wrong password, navigation). After **every** action: the page text (weekly *worked of target / to go*, editor totals box, Money KPIs, list rows) and the backend state read via the API are compared with the model (hours, rates, net/VAT/gross, gap-free numbers, revenue/expense/profit, forecast capacity). |
| `test_monkey.py` | Gremlin monkey on a seeded dataset (names that are XSS payloads): random clicks, typing of hostile strings (unicode, 5 000 chars, `<script>`, negatives, bad dates), form episodes (open → fill → save/cancel/delete), document-editor episodes, keys, back/forward/reload, odd routes. Invariants after each step: no uncaught error, no `console.error` (4xx API answers are fine), no HTTP 5xx, no script ran, injected HTML is text, shell never empty, modals always close, session survives. A second run as **member** asserts that no boss-only data or navigation is ever shown. |
| `test_api_random.py` | No browser. A boss and a member fire random state-dependent requests: CRUD on 7 collections with exact model comparison (incl. unknown fields surviving), offers/invoices (line amounts, draft rules, gap-free numbering, idempotent finalize, number tamper-proof, PDF), invoice-from-time (rate precedence task > project > customer > default, personal/non-billable skipped, no double billing, release on delete), timer, dashboard + forecast vs. model, member permission matrix, fuzzed payloads and path tricks (never a 5xx), forged/expired/`alg=none` tokens, 24 parallel writes. |
| `test_regressions.py` | Minimal reproductions of **genuine bugs found by the random tests**, as `xfail(strict=True)`. They turn into failures when the bug is fixed – then delete the marker. |

## Seeds and replay

* Default seeds: `11 23 42 1337 2026` (journeys, API) and `7 99` (monkey).
* `PERMITA38_E2E_SEED="5 6 7"` – run other seeds (any number of them) in all three files.
* A failing run prints the seed and the action log (`journey seed=… failed; replay with PERMITA38_E2E_SEED=…`) and
  saves a screenshot to `/tmp/e2e_*.png`. Replay: `PERMITA38_E2E_SEED=1337 pytest tests/web/e2e_random/test_user_journeys_random.py -x -s`.
* Length: `PERMITA38_E2E_ACTIONS` (journey, default 20), `PERMITA38_E2E_MONKEY_STEPS` (default 110),
  `PERMITA38_E2E_API_STEPS` (default 80). Run `PERMITA38_E2E_SEED=$RANDOM` in a loop to explore.

## Browser

Playwright for Python drives Chromium. `PERMITA38_CHROMIUM=/path/to/chrome` selects a binary (default: the first
of `/opt/pw-browsers/chromium*/chrome-linux/chrome`; otherwise Playwright's own, `playwright install chromium`).
`PERMITA38_E2E_HEADED=1` shows the window. Without Playwright or a browser the browser tests are **skipped**.

Every test starts its own real server (uvicorn on a free port, temp data folder, fresh first-run state).
