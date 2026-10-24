# API contract: API tokens

Source of truth for the backend, the web UI and the tests, so that they can be built in parallel. Behaviour and threat
model: `docs/specs/api-tokens.md`. Where this file and the spec differ on the HTTP shape, this file wins. Error format,
auth and date conventions are those of `docs/contracts/trips.md` ("Conventions"): `{"detail": "<message>"}` for
401/403/404/409 and service-level 422; body/query validation errors are
`{"detail": [{"loc": ["body","label"], "msg": "...", "type": "..."}]}`. Times are epoch seconds (integers), like all
other tables. Review tier: **Full** (authentication, rights), see the end.

## Decisions (made by the architect, not open for the implementer)

1. **Hashed at rest, shown once.** Only `sha256(token)` (hex) is stored. The plaintext is in the response of
   `POST /api/tokens` and nowhere else; it is never logged, stored or retrievable. Lost = revoke and create a new one.
2. **Mandatory expiry**, 1-365 days, default 90. No token without expiry. Expired = 401.
3. **Any signed-in user may create tokens** (boss, employee, accountant), for themselves only.
4. **A token never exceeds its owner's rights.** Its scope is the owner's role, evaluated on every request against the
   owner's *current* role and the current `rights.MATRIX`, optionally narrowed to **read-only** (all write operations
   denied). There are no per-area grants in this version.
5. **Transport**: `Authorization: Bearer pat_...` only. Never in a query string, cookie or body.
6. **Audit log**: every request authenticated by a token writes one row (token id, method, route template, status,
   time). No bodies, no query strings, no token, no concrete record ids.
7. **Revoke is immediate**: the token row is checked against the database on every request (no cache).
8. **At most 20 active tokens per user** (not revoked, not expired). The 21st `POST` answers 409.
9. **Session-only routes**: a token can never create, list or revoke tokens, change a password, touch two-factor
   authentication, or use user administration. These answer 403 for a token whatever its scope.
10. **Migration** `0010-api_tokens`, identical in `sqlite/`, `postgresql/` and `mariadb/`.

## Token format

`pat_` + 43 characters = base64url without padding of 32 random bytes (`secrets.token_bytes(32)`), 47 characters in
total, regex `^pat_[A-Za-z0-9_-]{43}$`. `hint` = `pat_` + the first 4 characters of the secret part (8 characters,
`pat_Xk3f`), stored in clear, shown in lists. A bearer value that starts with `pat_` is looked up as a token and is
**never** tried as a session JWT (and a JWT is never tried as a token); a bearer value that does not match the regex
answers 401 without a database lookup.

Lookup: `sha256(token.encode()).hexdigest()` -> row by unique index on `token_hash`, then `hmac.compare_digest` on the
hash. Plain SHA-256, no salt, no device `secret_key` (the secret has 256 bits of entropy; the data folder is synced
between devices with different configs).

## Data model

Dialect-neutral in `core/repository/schema.py`, both collections registered like the others (generic
`list/get/save/find`, no new `Repository` method; contract test for both). Both rows keep unknown fields in `extra`.

```
api_tokens   id  user_id  label  token_hash(unique)  hint  read_only:bool  expires_at:int  revoked_at:int|null
             last_used_at:int|null  created:int  updated:int                                  + extra
audit_log    id  at:int  token_id  user_id  method  route  status:int                         + extra
indexes:     api_tokens(user_id), api_tokens(token_hash) unique, audit_log(token_id, at), audit_log(at)
```

* `id`: the existing 8-hex id generator. `user_id` = owner. `token_id` in `audit_log` is not a foreign key (audit rows
  outlive revoked tokens; they do not outlive the owner, see "Deleting a user").
* `route` is the route **template** (`/api/customers/{customer_id}`), taken from the matched route, or the literal
  `"(unmatched)"` for 404/405 on an unknown path. Never the concrete URL.
* Pydantic: `ApiTokenCreateIn` (strict), `ApiTokenOut`, `AuditEntryOut` (lenient, `extra="allow"`); every new field in
  both the strict and the lenient model with a hard bound (`docs/validation.md`).
* Migration `permita38/core/schema/migrations/<dialect>/0010-api_tokens.sql` in all three dialects, created with
  `permita38 create-migration api_tokens` (random 8-hex id). Header `requires`: **the head at merge time**. At the time
  of writing `main` ends at `d8a69cde` (0008-expense_lines); the business-trips branch adds `0009-trips`
  (`9897dd11`, requires `d8a69cde`). Whoever merges second rebases `requires` of the first new migration (see
  AGENTS.md); the ordinal 0010 assumes trips merged first and is cosmetic otherwise. No `BEGIN`/`COMMIT` in the SQL.

## Authentication seam (backend)

`get_current_user` (`api/deps.py`) resolves one **principal**: the owner's user dict (as today, role normalised) plus a
private key `_auth`:

* session: no `_auth` key (unchanged behaviour);
* token: `_auth = {"kind": "token", "token_id": "...", "read_only": true|false}`.

Rules, in order:

1. Credential = bearer if present, else the cookie `authorization`. A bearer wins over a cookie.
2. A `pat_...` value in the **cookie** -> 401. Only the bearer header carries tokens.
3. Token path: unknown hash, revoked (`revoked_at` set), expired (`expires_at <= now`), owner missing or inactive ->
   **401** `{"detail": "Not authenticated"}` with `WWW-Authenticate: Bearer`; the body and headers are identical for
   all four cases (no oracle).
4. The owner's role and `active` flag are read from the database on every request, so a demotion or deactivation is
   effective at once.
5. `last_used_at` is written at most once per 60 seconds per token.
6. `_auth` never appears in any response: `public_user` and every router that returns the user must strip it
   (test: `GET /api/auth/me` with a token has no `_auth` key; it has `auth`, see below).
7. No response to a token request sets a cookie.

### Rights check (`core/rights.py`)

The existing `rights.scope(role, area, op)` stays (role in, scope out; it is also used for `rights.permissions(role)` and
for other users' roles). New, and the **only** function request paths use for the current principal:

```python
def scope_for(principal: dict, area: str, op: Op = "read") -> Scope:
    """Effective scope of the signed-in principal (user session or API token)."""
    base = scope(principal.get("role"), area, op)
    auth = principal.get("_auth")
    if auth and auth.get("kind") == "token" and auth.get("read_only") and op == "write":
        return Scope.NONE
    return base
```

and `allowed_for(principal, area, op)` = `scope_for(...) != NONE`. Defence in depth: a middleware/dependency denies every
**non-safe method** (anything but GET, HEAD, OPTIONS) of a read-only token with 403 `"Forbidden"` regardless of what the
route declares, so a write route that forgets `Needs(..., "write")` is still safe. (Read-write tokens: the route's own
`Needs` decides.)

### Existing call sites to change (found by grep, all must use `scope_for(user, ...)`)

| File | Line (at time of writing) | Now | Change |
|---|---|---|---|
| `permita38/api/deps.py` | `Needs.check`, `rights.scope(user.get("role"), area, op)` | role | `rights.scope_for(user, area, op)`; also the `get_current_user` token branch, the `SessionOnly` dependency, `public_user` strip |
| `permita38/api/routers/records.py` | 31, `rights.scope(user["role"], "planning")` | role | `scope_for(user, "planning")` |
| `permita38/api/routers/settings.py` | 18, `rights.scope(user["role"], "settings", "write")`; 27 `normalize_role(user.get("role")) == BOSS` | role | `scope_for(user, "settings", "write")`; line 27 stays role-based (it decides the view, not a right) but must be reviewed |
| `permita38/core/service/records.py` | 223 `rights.scope(me.get("role"), "team")`; 233 `rights.scope(me.get("role"), COLLECTION_AREA[coll], op)` | role | `scope_for(me, ...)` (this is the `limited` own-records view, `me` is the principal) |
| `permita38/core/service/settings.py` | 51 `rights.scope(me.get("role"), "settings")`; 54 and 59 `rights.allowed(role, ...)` | role | `scope_for(me, ...)` / `allowed_for(me, ...)`; `role` local variable removed |
| `permita38/core/service/time.py` | 61 `rights.scope(me.get("role"), "time")` | role | `scope_for(me, "time")` |
| `permita38/core/service/team_timeline.py` | 66 `level_for(viewer.get("role"), settings)` | role | stays role-based (visibility level is a function of the owner's role, not a write right); documented, token = owner here, read-only does not matter for a GET |
| `permita38/core/service/sanity.py` | 79 `user.get("role") != "boss"` | role | stays (presentation of warnings); review only |
| `permita38/core/service/auth.py` | `user_from_token` (session JWT) | | unchanged; add `AuthService`/`TokenService.user_from_api_token(token)` in the new `core/service/tokens.py` |

Plus the new routes (below), `api/server.py` (register router, add the audit middleware and the non-safe-method guard),
`api/openapi.json` (second security scheme `ApiToken`, HTTP bearer, bearer format `pat_`; `make generate-openapi`),
`docs/roles.md` (link). New code must not call `rights.scope(` with a role taken from a request principal; a source-scan
test enforces it in `permita38/api/` and `permita38/core/service/`.

Services that receive the principal (`me`) keep working for both kinds; they never see anything token-specific except
`_auth`, and only `rights.py` reads it.

### Session-only routes

Dependency `SessionOnly` (in `api/deps.py`), applied to the **routers** `tokens`, `auth` (except one route, below),
`totp` and `users`: a token request answers

```
403  {"detail": "This route cannot be used with an API token"}
```

Allowed with a token from these routers: `GET /api/auth/me` only. Everything else under `/api/auth/*`
(`login`, `logout`, `setup`, `change-password`, `login/totp`), `/api/totp/*`, `/api/users/*` and all of
`/api/tokens/*` is session-only. Public routes (`GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login`,
`POST /api/auth/login/totp`) do not authenticate at all, so they ignore a bearer token (no change); a token presented
there is not looked up and not audited. A test walks `app.routes` and fails for any route that is not exactly one of:
`Needs(...)`, `SessionOnly`, or on an explicit public list.

`GET /api/auth/me` with a token returns the normal user object plus:

```json
"auth": {"kind": "token", "token_id": "c0ffee01", "label": "Backup script", "read_only": true, "expires_at": 1773776000}
```

(`"auth": {"kind": "session"}` for a session.) `permissions` in the user object is the owner's table with all `write`
entries set to `"none"` for a read-only token (`rights.permissions` applied through `scope_for`).

## Rights matrix

| Route | Principal | Rule |
|---|---|---|
| `GET /api/tokens` | session, any role | own tokens; boss: `?all=true` lists everyone's |
| `POST /api/tokens` | session, any role | creates for the caller only |
| `POST /api/tokens/{id}/revoke` | session | owner, or boss for any token; others 404 |
| `POST /api/tokens/revoke-all` | session | own tokens (boss: also `?user_id=`) |
| `GET /api/tokens/{id}/audit` | session | owner, or boss; others 404 |
| all of the above | token | 403 `"This route cannot be used with an API token"` |
| every other route | token | the owner's role as today (`Needs`/`scope_for`); `read_only` token: 403 on any non-safe method |

No rights area is added to `rights.py`: token management is "own credentials", like changing one's own password, and is
not part of `rights.MATRIX`. `docs/roles.md` gets a short note instead. A boss can never see anyone's secret or create a
token for someone else.

## Routes

All under `/api`, `Content-Type: application/json`. Strict models reject unknown fields (422).

### `POST /api/tokens` -> 201

Request (`ApiTokenCreateIn`, strict):

```json
{"label": "Backup script", "expires_in_days": 30, "read_only": true}
```

| Field | Type / bounds | Required | Default |
|---|---|---|---|
| `label` | string, 1-80 chars after trimming, no control characters | yes | |
| `expires_in_days` | integer 1-365 | no | 90 |
| `read_only` | boolean | no | `true` |

Response (the only time `token` appears; `Cache-Control: no-store`):

```json
{
  "token": "pat_3q7mXc0Qd1pZk9Yw2eLr8VbT5uHnA4sJf6GoKiDxE1c",
  "item": {
    "id": "c0ffee01", "user_id": "u1", "label": "Backup script", "hint": "pat_3q7m",
    "read_only": true, "created": 1766000000, "expires_at": 1768592000,
    "last_used_at": null, "revoked_at": null, "status": "active"
  }
}
```

Errors: 401 no/invalid session; 403 token credential (session-only); 409 `{"detail": "Too many active tokens (maximum 20)"}`
when the caller already has 20 active tokens; 422 list form for body errors (missing/blank label, label > 80, days 0 or
366, non-integer days, non-boolean `read_only`, unknown field). The check order is 401, 403, 422, 409. The audit log gets
no row for session requests; the creation is not a token request.

### `GET /api/tokens` -> 200

Query: `all` (boolean, default false). `all=true` for a non-boss -> 403 `"Forbidden"`. Sorted newest `created` first.
Items as `item` above plus, for `all=true`, `owner_name` (the user's display name). Never contains a secret or the hash.
`status`: `"active"`, `"expired"` (not revoked, `expires_at <= now`) or `"revoked"` (revoked wins over expired).

```json
[{"id": "c0ffee01", "user_id": "u1", "label": "Backup script", "hint": "pat_3q7m", "read_only": true,
  "created": 1766000000, "expires_at": 1768592000, "last_used_at": 1766400000, "revoked_at": null, "status": "active"}]
```

### `POST /api/tokens/{id}/revoke` -> 200

Body empty. Returns the item with `"status": "revoked"` and `revoked_at` set. Idempotent: revoking a revoked token
returns it unchanged with 200 (`revoked_at` keeps the first time). Effective at the next request of that token.
Errors: 401; 403 token credential; 404 `{"detail": "Token not found"}` for an unknown id **and** for a token the caller
may not see (no existence leak); 422 if `id` is not 8 hex characters.

### `POST /api/tokens/revoke-all` -> 200

Query `user_id` (optional, boss only; a non-boss giving any `user_id` other than their own -> 403). Revokes all active
tokens of that user (own by default).

```json
{"revoked": 3}
```

(`revoked` counts tokens that were active, `0` is fine.) Deactivating a user (`PUT /api/users/{id}` with
`active: false`) revokes its tokens in the same transaction.

### `GET /api/tokens/{id}/audit` -> 200

Query: `limit` integer 1-200 (default 50), `before` integer epoch seconds (rows with `at < before`, for paging). Newest
first.

```json
{"items": [{"id": "a1b2c3d4", "at": 1766400000, "token_id": "c0ffee01", "user_id": "u1",
            "method": "GET", "route": "/api/customers", "status": 200}],
 "next": 1766399990}
```

`next` = `at` of the last item when `limit` rows were returned, else `null`. Errors: 401, 403 (token credential), 404 (as
revoke), 422 (`limit`/`before` out of range). A boss sees any token's log; others only their own tokens'. Rows exist for
revoked and expired tokens too. No write or delete route exists for the audit log (append-only; see below).

## Audit log

* Written by one middleware around the whole app, after the response status is known, for every request that was
  **authenticated by a valid, unrevoked, unexpired token** (including when the response is 403 session-only, 403
  `Forbidden`, 404, 422, 429/500). Requests with an unknown, revoked or expired token have no known token id: no row
  (they are logged to the server log without the credential).
* Columns: `at`, `token_id`, `user_id` (the owner), `method`, `route` (template), `status`. Nothing else: no body, no
  query string, no headers, no IP, no record id. (Later versions may add to `extra`.)
* Append-only: `core/service/audit.py` is the only writer and has `append(...)` and `list(...)` only. The only deletes
  are the startup prune of rows older than 365 days and the cascade on deleting a user. A repository contract test asserts there is no update path.
* A failing audit write is logged and does not change the response (see Open design decisions, 3).
* Canary test: send a token with a distinctive secret and query string; scan the whole database file bytes and the log
  output: the secret appears nowhere (only its hash), the query string appears nowhere.

## Deleting or changing the owner

* `DELETE /api/users/{id}` deletes that user's tokens and their audit rows in the same transaction.
* Deactivation revokes the tokens (set `revoked_at`), see above; re-activation does not revive them.
* Role change: effective at once because the role is read per request. A token of a demoted boss can use only what an
  employee can; if the user is promoted, the token gains rights up to the new role (documented in the UI text: "A token
  has the rights of its owner").

## Validation bounds (summary)

`label` 1-80, `expires_in_days` 1-365, `limit` 1-200, `before` 0 to 4102444800 (2100-01-01), max 20 active tokens per user,
`id` path parameter = 8 hex characters, bearer value `^pat_[A-Za-z0-9_-]{43}$`. Add `ApiTokenCreateIn` to
`core/validation.py` primitives where they exist; update `docs/validation.md`.

## UI contract (web)

New file `web/tokens.js`, one hook in `web/main.js` and `web/index.html`; no build step; only talks to the API above. The
section "API tokens" is a card on the Settings page, visible to every signed-in role (it is "your own credentials").

* **List** (table, `data-testid="tokens-table"`): label, hint, access ("Read only" / "Read and write"), created, expires,
  last used ("never" when `null`), status badge (`active`/`expired`/`revoked`, text, not colour only), a "Revoke"
  button for active tokens (confirm dialog) and an "Activity" button (opens the audit list below in a dialog, with
  "Load more" using `next`). Empty state: "No API tokens yet". Boss: switch "Show all users" (`?all=true`, extra column
  "Owner"). A "Revoke all my tokens" button (confirm) sits in the card header.
* **New token** button -> dialog: `label` (required, max 80), `expires_in_days` select (7, 30, 90 default, 180, 365),
  checkbox "Read only" (checked by default). Under the checkbox: "A token has the rights of its owner (your role: boss).
  It can never create tokens or change passwords." A visible warning (text, `role="alert"`) when "Read only" is
  unchecked: "This token can change data." Disabled with the explanation "You already have 20 active tokens" when the
  list shows 20 active ones (the server still enforces 409).
* **After create**: a modal with the token in a read-only input (`data-testid="token-secret"`), a "Copy" button
  (`aria-live="polite"` confirmation "Copied"), the text "This is the only time the token is shown", and a checkbox "I
  have saved it" that must be ticked before "Close" is enabled. The secret is kept only in the DOM of this modal: not in
  `localStorage`/`sessionStorage`, not in the URL, not in any list row; the modal's DOM is removed on close. The list
  reloads and shows only the `hint`.
* Errors: 409 -> inline message from `detail`; 422 -> field messages; 401 -> the existing sign-in flow. All text from
  the server is escaped (`textContent`), keyboard operable, no information only in tooltips.
* Demo data and screenshots use fake tokens only (never a real secret).

## Examples (curl)

```
# create (session cookie), read-only, 30 days
POST /api/tokens   {"label": "Backup script", "expires_in_days": 30}
# use
GET /api/customers   Authorization: Bearer pat_3q7mXc0Qd1pZk9Yw2eLr8VbT5uHnA4sJf6GoKiDxE1c      -> 200
POST /api/customers  (same token, read_only)                                                      -> 403 {"detail":"Forbidden"}
POST /api/tokens     (same token)                  -> 403 {"detail":"This route cannot be used with an API token"}
GET /api/customers   (revoked/expired/unknown token) -> 401 {"detail":"Not authenticated"}
```

## Work packages (parallel)

Backend and UI need only this file. Suggested merge order: A (with C), B, D, then E.

| WP | Files | Content | Tier |
|---|---|---|---|
| A backend core | `core/rights.py` (`scope_for`, `allowed_for`), `core/service/tokens.py`, `core/service/audit.py`, `core/models.py` (append only), `core/repository/schema.py`, migration files (3 dialects), `core/database.py` only if needed, `api/deps.py` (principal, `SessionOnly`, `Needs`) | hashing, lookup, principal, rights seam, audit service, prune | Full |
| B backend API | `api/routers/tokens.py`, `api/server.py` (router, audit middleware, safe-method guard), `api/openapi.json` | the routes of this file | Full |
| C call-site sweep | the files in the table above | replace role-based checks with `scope_for(principal)`; source-scan test | Full |
| D web UI | `web/tokens.js`, small hooks in `web/main.js`/`index.html`, `tests/web/test_tokens_ui.py` | the UI contract (mock server data from the examples) | Standard (Full if the secret handling in the DOM is questionable) |
| E docs | `docs/api-tokens.md` (user guide, curl, threat model summary), `docs/roles.md` note, `docs/roadmap.md` row | | Light |
| Tests | `tests/backend/test_api_tokens.py`, additions to `test_rbac.py`, `test_repository_contract.py`, migration tests; hypothesis model | see below | part of A/B/C |

### Tests that must exist

* Hash at rest: the plaintext is not in the database file bytes, `token_hash == sha256(token)`; shown once (second `GET` has
  no `token`); format regex; hint = first 8 chars; uniqueness of hashes.
* Rights: a token never exceeds the owner: for each role and **every route** (`declared()` enumerates them) a token
  gets exactly what the same user session gets, except read-only tokens get 403 on every non-safe method; demote the
  owner (boss -> employee) and the very next request of the same token is clamped; deactivated owner -> 401;
  `limited` scope returns only the owner's own records through a token.
* Session-only: every route in `tokens`, `auth` (except `me`), `totp`, `users` answers the exact 403 body for a token;
  `GET /api/auth/me` works and has `auth`; a route walk fails for a route without `Needs`/`SessionOnly`/public marker.
* Source scan: no `rights.scope(` with `.get("role")`/`["role"]` in request-path modules (`api/`, `core/service/`).
* Lifecycle: expiry (fake clock), revoke immediate (next request 401), revoke idempotent, 20 active ok / 21st 409,
  revoked/expired do not count, boss `all=true` vs non-boss 403, other user's token id 404 for revoke and audit,
  `revoke-all`, deleting a user removes tokens and audit rows, deactivating revokes.
* Transport: `pat_` in the cookie -> 401; token in a query string ignored (401); token + cookie -> token wins; JWT as bearer
  still works; malformed `pat_x` -> 401 without a lookup; no `Set-Cookie` on token responses; 401 bodies identical for
  unknown/revoked/expired.
* Audit: exactly one row per token request (also for 403/404/422), none for session requests, no row for invalid tokens,
  route is the template, canary secret/query string not found in the database bytes; prune; append-only contract test.
* Migration: chain test, three dialect folders identical, migrated schema equals `schema.py`, repository contract test
  for `api_tokens` and `audit_log` (unknown fields survive).
* Randomized (`tests/backend/randomized`): add create/revoke/use of tokens to the state machine, invariant "an effective
  right never exceeds the owner's; read-only never writes".
* Web (`tests/web/test_tokens_ui.py`, fake data): create (read-only by default), secret shown once and gone after reload,
  Close disabled until "I have saved it", revoke, boss "Show all users", activity list, 20-token limit message.

## Review (Full tier, three Sonnet 5.5 passes)

(1) Code review: auth seam, no path where `_auth` is dropped or a token is treated as a session, cookie/bearer
precedence, constant-time compare, no secret in logs/responses/`extra`. (2) Test review: would the route-walk and scan
fail if one `Needs` were wrong; canary test real. (3) Logic review (never a smaller model): recompute the min rule
by hand for the three roles x {read, write} x {read-only, read-write}, check expiry arithmetic (`now + days*86400`,
boundary `expires_at == now` is expired), the 20-token count, role change and deactivation timing.

## Open design decisions (assumed by the architect; confirm or change before the merge)

1. **API default `read_only: true`** when omitted (safe default; the UI also pre-checks it).
2. **No per-token rate limit and no per-IP invalid-token throttle in this version** (the old spec had both). The audit
   log shows abuse; add later if wanted. The existing login `AttemptThrottle` is unchanged.
3. **Audit write failure is fail-open** (logged, response unchanged), because the status is only known after the handler
   has run. Fail-closed would need a pre-write and an update of the row, which breaks append-only.
4. **Fixed retention 365 days**, pruned at startup, no setting.
5. **No PATCH**: label, expiry and `read_only` are immutable; revoke and create a new one.
6. **Settings and `team` are not excluded** for read-write tokens of a boss (a boss token can change settings and users'
   `team` data except the session-only routes). The old spec forbade settings-write grants. If unwanted, add `settings`
   write to the safe-method guard for tokens.
7. **`GET /api/team/timeline`, sanity warnings and the settings view** stay role-based (they are views, not rights).
8. **No CLI `create-token` and no MCP changes** in this version; the MCP spec uses the same tokens
   (`docs/specs/mcp-server.md`) and a CLI is a follow-up.
9. **Token for a deactivated-then-reactivated user** stays revoked.
10. **Display of expired tokens**: kept in the list with status `expired`; no automatic deletion of token rows.
