# Specification: scoped API tokens

Status: **proposed**, roadmap alpha.3 ("Scoped API tokens"). First of three specs, in dependency order:
`api-tokens.md` (this) -> [`mcp-server.md`](mcp-server.md) -> [`ai-provider-and-proposals.md`](ai-provider-and-proposals.md).
Author: architect (Sonnet 5.5). The implementer follows this document, makes no architectural choices and lists every
choice it had to make under "Open design decisions" in its report. **Review tier: Full** (authentication, rights).

## Goal

A signed-in user can create long-lived, revocable **API tokens** for scripts, integrations and the MCP server. A token
acts as its owner, but with a *subset* of the owner's rights, written in the existing rights vocabulary
(`area`, `op`, `scope` from `core/rights.py`). A token can never do more than its owner can do **at the moment of use**.

Non-goals: OAuth/OIDC flows, per-record scopes (a token for one customer only), tokens for subcontractors without an
account, token-managed-by-token, IP allow-lists (later), webhooks. Custom roles stay out of scope.

## Today (what exists)

* Session = stateless HMAC-signed JWT (`AuthService.create_token`, claim `use: session`), delivered as an HttpOnly,
  `SameSite=Strict` cookie `authorization` and also accepted as `Authorization: Bearer <jwt>` (`api/deps.py`).
* Rights: `rights.MATRIX[role][area] = (read scope, write scope)`, scopes `none < limited < all`. Routes declare
  `Needs(area, op)`; services call `rights.scope(user["role"], area, op)` for the limited/own-records view.
* No CSRF token (the cookie is `SameSite=Strict`), no audit log (the GoBD change log is planned for alpha.4),
  `AttemptThrottle` for failed logins, in-memory only. Server-side session revocation is in progress.

## Rules

1. **Format**: `pa38_<secret>`, where `<secret>` = 32 random bytes from `secrets.token_bytes`, base64url without
   padding (43 characters). The fixed prefix lets secret scanners and the auth dependency tell it from a session JWT
   (which always contains two dots and starts with `eyJ`). The display `hint` is `pa38_` + the first 4 characters of the
   secret (`pa38_Xk3f...`), stored in clear to recognise a token in the list.
2. **Shown once**: the plaintext is in the response of `POST /api/tokens` only; it is never stored, logged or
   retrievable. Lost = revoke and create a new one.
3. **Stored hashed with SHA-256** (hex of `hashlib.sha256(token.encode())`). Reason: the secret has 256 bits of entropy,
   so brute force is infeasible and a slow KDF (PBKDF2, as used for passwords) would only add latency to every request
   without adding security; a salt is unnecessary for the same reason (no rainbow tables over 256-bit secrets). Lookup is
   by hash (unique index), then `hmac.compare_digest`. We deliberately do **not** HMAC with the device `secret_key`:
   the data folder is synced between devices that may have different config files, and tokens must keep working there.
4. **Owner**: exactly one user (`user_id`). A deactivated or deleted owner invalidates all its tokens (checked on use;
   deleting a user also deletes the token rows in the same transaction).
5. **Scopes** are a list of grants `{area, op, scope}` with `area` in `rights.AREAS`, `op` in `read|write`, `scope` in
   `limited|all`. A missing entry means `none`. Not grantable at all: area `team`, and `write` on `settings`; token
   management, login, password, TOTP and user routes reject tokens whatever their scopes (rule 8).
6. **Effective scope** = `min(token grant, owner's current scope)` evaluated on **every request** against the
   *current* `rights.MATRIX` and the owner's *current* role, so demoting the owner or changing the matrix takes effect
   immediately. Order `none < limited < all`. A grant above the owner's scope at creation time is rejected with 422;
   a grant that later exceeds it is silently clamped (and listed as `clamped` in the token detail).
7. **Implementation seam** (the confused-deputy guard): `get_current_user` returns the owner's user dict plus a
   private key `_grants` (token requests only). A new `rights.scope_for(user, area, op)` returns
   `min(rights.scope(role, ...), grant)` and is the **only** function routes and services use. All existing call
   sites of `rights.scope(<role>, ...)` / `rights.allowed(<role>, ...)` that take the role from a request user
   (`api/deps.py`, `api/routers/settings.py`, `api/routers/records.py`, `core/service/records.py`,
   `core/service/settings.py`, `core/service/time.py`) are changed to it. A test fails when a request-path module
   calls `rights.scope(` with `.get("role")`/`["role"]` (source scan), and a parametrised test walks every route
   (`declared()` already enumerates them) with a token that grants only `(area, read, limited)`.
8. **Session-only routes**: `/api/tokens/*`, `/api/auth/*`, `/api/totp/*`, `/api/users/*` (and anything that changes
   a password or 2FA) accept the session cookie / session JWT only. A `pa38_` credential there answers `403`
   `{"detail": "This route cannot be used with an API token"}`. A token therefore cannot mint, widen or list tokens.
9. **Expiry**: required, between 1 hour and 365 days (default 90 days). No tokens without expiry. Expired = `401`.
10. **Revocation**: sets `revoked_at`; effective at the next request (state is read from the database each time; no
    cache longer than one request). The owner and any boss can revoke; "Revoke all" for one user is available
    (also used when a user is deactivated).
11. **Last used**: `last_used_at` is updated at most once per 60 seconds per token (synced SQLite: avoid a write
    per request). Exact usage is in the audit log.
12. **Rate limit**: per token, in memory, token bucket, default 60 requests/minute, burst 30, owner-configurable 1-600.
    Over the limit: `429` with `Retry-After` seconds; counted in the audit log as `rate_limited` (at most one row
    per token per minute). Invalid-token attempts are throttled per client IP with `AttemptThrottle` key
    `tokenfail:<ip>` (5 failures / 5 minutes), independent of the login throttle. State is per process (documented).
13. **Audit**: every request authenticated by a token writes one `audit_log` row (below): method, route template (not
    the concrete URL with ids in free text), status, area, and the record id for single-record writes. Never the body,
    never the token, never query strings. Token lifecycle events (create, revoke, expire-seen, edit) are logged with
    the session user as actor. Audit rows are append-only (no update/delete route, no delete in the repository
    wrapper); retention default 365 days, pruned at startup by the boss-configured setting (`audit_retention_days`,
    min 90). The table is shaped so the alpha.4 GoBD change log can reuse it (`source` and `action` are open strings).
14. **Transport**: only `Authorization: Bearer pa38_...`. Never accepted in a query string, a cookie or a body (URLs end
    up in logs). If a request carries both a `pa38_` bearer and the cookie, the bearer wins and the cookie is ignored.
15. **No ambient authority, CSRF**: a bearer token is not sent automatically by a browser, so token requests are not
    CSRF-able; the SameSite=Strict cookie stays the only CSRF defence for sessions and is unchanged. Consequences to
    enforce and test: (a) a `pa38_` credential in the *cookie* is rejected; (b) when a valid token is presented, cookie
    CSRF assumptions are not relaxed for anything else; (c) CORS: tokens work cross-origin only for origins already in
    `cors_origins`, and `allow_credentials` stays as it is (tokens do not need it); (d) no endpoint may set a cookie in
    response to a token request.
16. **Responses never include the owner's secrets**: the user object returned for a token request is `public_user`.
    `GET /api/auth/me` with a token returns the user plus `"auth": {"kind": "token", "token_id", "grants"}` so a client
    can discover what it may do.

## Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| **Leak** (token in a repo, log, chat, backup of an MCP client config) | `pa38_` prefix for scanners; shown once; hashed at rest; mandatory expiry; revoke in the UI; `last_used_at` + audit make misuse visible; read-only default grants in the UI; no token in URLs | A leaked token is valid until revoked or expired, within its grants. Minimise grants and lifetime. |
| **Over-scope** (token creator picks too much, role later changes, matrix changes) | min(token, owner) at every use (rule 6); creation rejects grants above the owner; `team`, settings-write, token/auth routes not grantable; UI preset "Read only" | An owner who is boss can still create a broad token; the UI warns when `write` + `all` is selected. |
| **Replay** (captured request replayed) | TLS is the control: document that tokens over plain `http` are only for `127.0.0.1`; the server logs a warning at token creation when `host` is not loopback and the request is not https; expiry bounds the window | No nonce/signature scheme (not worth it; same trade-off as GitHub/Stripe keys). Idempotency of writes is the caller's job. |
| **Confused deputy** (a tool/route acts with the owner's full session rights instead of the token's) | single seam `rights.scope_for` (rule 7), source-scan test, route-walk test; session-only routes (rule 8); services receive the effective grants, never the bare role; MCP and AI features call the HTTP-equivalent service layer with the token's principal, never an internal super-user | New code that bypasses `Needs`/`scope_for` would escape: covered by the review checklist and the scan test. |
| **Brute force** | 256-bit secret; per-IP failure throttle | none practical |
| **Timing oracle** | hash lookup then `compare_digest`; same 401 body for unknown, revoked, expired | negligible |
| **Insider with data-folder access** | only hashes in the data folder; the plaintext is never written | A user who can edit the database could insert a hash of a token they know: the data folder is already fully trusted (it holds all business data). |
| **Token used against a changed owner password** | passwords do not revoke tokens (they are independent credentials); deactivating the owner and "Revoke all" do | Documented; see Open decision 3. |

## Data model

New tables via migration (dialect-neutral in `core/repository/schema.py`, SQL in every dialect folder):

```
api_tokens   id user_id label token_hash(unique) hint scopes:json expires_at:int revoked_at:int
             last_used_at:int rate_limit:int created_by created updated        + extra
audit_log    id at:int actor_user_id token_id source action area method route status
             record_ref detail:json                                              + extra
             (indexes: audit_log(at), audit_log(token_id, at); api_tokens(user_id))
```

* Times are epoch seconds like the other tables. `source` in `session|token|mcp|ai|system`, `action` free text such
  as `request`, `token.create`, `token.revoke`, `rate_limited`, `auth.denied`.
* `scopes` example: `[{"area":"customers","op":"read","scope":"all"},{"area":"time","op":"read","scope":"limited"}]`.
* Pydantic: `ApiTokenIn` (strict: `label` 1-80 chars, `expires_in_days` 1-365, `rate_limit` 1-600, `scopes` max 40
  grants, each field enum-typed), `ApiTokenOut`, `AuditEntryOut` (lenient, `extra="allow"`, unknown fields survive).
* Repository: `Repository` gets no new methods beyond the generic `list/get/save/find` if the two collections are
  registered in `COLLECTIONS`; `audit_log` is append-only by convention in `core/service/audit.py` (the only writer) and
  a contract test asserts there is no update path. Migration id: see the last section.

## API contract

All token management routes are session-only, `Needs("settings", "read")` is **not** used: a user manages **their own**
tokens; a boss may list and revoke all. Errors use `{"detail": ...}`.

`GET /api/tokens` -> `200`, own tokens (boss: `?all=true` for everyone). Never contains secrets.

```json
[{"id":"c0ffee01","user_id":"u1","label":"Backup script","hint":"pa38_Xk3f","scopes":[{"area":"customers","op":"read","scope":"all"}],
  "created":1766000000,"expires_at":1773776000,"last_used_at":1766400000,"revoked_at":null,
  "rate_limit":60,"status":"active","clamped":[]}]
```

`status` in `active|expired|revoked`. `clamped` lists grants currently reduced by the owner's rights.

`POST /api/tokens` -> `201`.

```json
// request
{"label":"MCP read-only","expires_in_days":30,"rate_limit":60,
 "scopes":[{"area":"customers","op":"read","scope":"all"},{"area":"time","op":"read","scope":"limited"}]}
// response (the only time "token" appears)
{"token":"pa38_3q7mXc0Qd1pZk9Yw2eLr8VbT5uHnA4sJf6GoKiDxE1cM","item":{"id":"c0ffee01","hint":"pa38_3q7m","status":"active", "...":"..."}}
```

Errors: `422` unknown area/op/scope, empty `scopes`, non-grantable area (`team`, `settings` write), expiry out of range;
`403` a grant above the caller's own scope (`{"detail":"Not allowed to grant timesheet write (all): your role has read"}`);
`409` more than 20 active tokens per user.

`PATCH /api/tokens/{id}` -> `200`. Only `label` and `rate_limit` may change. Scopes and expiry are **immutable**
(create a new token to widen; shorter-lived is an explicit revoke+create). Reasoning: no silent privilege growth.

`POST /api/tokens/{id}/revoke` -> `200` the item with `status: "revoked"` (idempotent). `404` for a token the caller may
not see (not `403`, no existence leak). `POST /api/tokens/revoke-all?user_id=...` -> `{"revoked": 3}` (own, or boss).

`GET /api/tokens/presets` -> `200` the offered scope presets for the caller (computed from the caller's rights, so the UI
never offers what would be rejected):

```json
[{"id":"read-all","label":"Read everything I can read","scopes":[{"area":"customers","op":"read","scope":"all"}]},
 {"id":"time-own","label":"My time entries (read/write)","scopes":[{"area":"time","op":"read","scope":"limited"},{"area":"time","op":"write","scope":"limited"}]}]
```

`GET /api/audit?token_id=&source=&area=&from=&to=&limit=100&before=` -> `200`, newest first, boss only
(`Needs("settings","read", full=True)`) plus "own tokens only" for other users. Returns `{"items":[...],"next":"<cursor>"|null}`:

```json
{"items":[{"id":"a1","at":1766400000,"source":"token","token_id":"c0ffee01","actor_user_id":"u1","action":"request",
  "area":"customers","method":"GET","route":"/api/customers","status":200,"record_ref":null}],"next":null}
```

**Existing routes with a token**: `401 {"detail":"Not authenticated"}` for unknown/expired/revoked tokens (identical body);
`403 {"detail":"Forbidden"}` when the effective scope is `none`; `429` with `Retry-After` and
`{"detail":"Rate limit exceeded"}`. `api/openapi.json` gets a second security scheme (`ApiToken`, HTTP bearer, description
"pa38_ token"). `make generate-openapi`.

## Rights

* Anyone signed in by session: create/list/revoke **own** tokens (area `settings` is not involved; the right is "own
  credentials", like changing one's own password). Accountant and employee may create tokens within their own rights.
* Boss: list all tokens and the audit log, revoke any token, set `audit_retention_days`. A boss cannot see another
  user's secret (nobody can) or create a token for someone else.
* `tests/backend/test_rbac.py` gains the new routes: session-only, own-or-boss, 401/403/404 cases.

## UI (Settings > API tokens)

New section in `web/` (new file `web/tokens.js`, one hook line in `web/main.js` and `index.html`):

* Table: label, hint, scopes summary ("Customers: read, Time: read (own)"), created, expires, last used, status, rate
  limit; "Revoke" with confirm. Boss sees a "Show all users" switch.
* "New token" dialog: label, expiry (select 7/30/90/365 days, default 90), preset select then an editable matrix of
  the grantable areas x read/write with `none/limited/all`, greyed beyond the user's own rights, a visible red note when
  write+all is chosen, rate limit. On save a modal shows the token in a read-only field with a Copy button and the text
  "This is the only time the token is shown", closing requires ticking "I have saved it". The token is kept only in the
  DOM of that modal, never in `localStorage` or in the URL.
* Link per token to the filtered audit view ("Activity"). Everything escaped, keyboard operable, no information only in
  tooltips, `aria-live` on the copy confirmation.
* Settings page shows a warning banner when a token is used from a non-loopback address over http.

## CLI

`permita38 create-token --user <username> --label X --days 30 --preset read-all` for headless setups (prints the token
once; requires access to the config and data folder, hence implicitly trusted). Used by the MCP server doc.

## Tests

Backend, `tests/backend/test_api_tokens.py` and additions:
* Hash at rest (the plaintext is not in the database file bytes; `token_hash` equals SHA-256), shown once (second GET has
  no token), uniqueness, format.
* min-rule: owner demoted from boss to employee -> token grant clamps on the next request without a restart; matrix
  monkeypatched; limited scope returns only the owner's records.
* Route-walk: for every route and every `(area, op)` grant, a token granting only that gets 2xx/4xx-non-403 on routes
  needing it and 403 on all others; session-only routes answer 403 for tokens.
* Source scan against raw `rights.scope(...role...)` in request-path modules.
* Expiry, revocation immediate, deactivated owner, deleted owner removes rows, 21st token 409, scopes immutable.
* Rate limit with a fake clock: 429 + `Retry-After`, per-token isolation; invalid-token throttle per IP.
* CSRF/cookie: `pa38_` in the cookie rejected; token + cookie -> token wins; no `Set-Cookie` in token responses.
* Audit: one row per request, no body/query/token in any column (scan with a canary secret), append-only, retention prune.
* Migration: chain test, migrated schema equals `schema.py`, repository contract test for the two collections.
* Randomized: add tokens to the hypothesis state machine (create/revoke/use; invariant: an effective right never exceeds
  the owner's).
Web (`tests/web/test_tokens_ui.py`): create with a preset, token shown once and not after reload, revoke, boss list-all,
unavailable areas greyed out. Fake demo data only.

## Work packages (parallel) and review tiers

| WP | Owner (files) | Content | Tier |
|---|---|---|---|
| A backend core | `core/rights.py` (`scope_for`), `core/service/tokens.py`, `core/service/audit.py`, `core/service/ratelimit.py`, `core/models.py` (new models only, append), `core/repository/schema.py` (two tables), migration files, `api/deps.py`, `tests/backend/test_api_tokens.py` | auth seam, hashing, limits, audit | **Full** (3 passes) |
| B backend API | `api/routers/tokens.py`, `api/server.py` (register router only), `cli/` (`create-token`), `api/openapi.json`, `test_rbac.py` additions | routes and CLI per contract (stub service until A lands; contract above is fixed) | **Full** |
| C call-site sweep | the six modules in rule 7 + scan test | replace `rights.scope(role)` by `scope_for(user)` | **Full** (mechanical but auth) |
| D UI | `web/tokens.js`, small hooks in `web/index.html`/`main.js`, `tests/web/test_tokens_ui.py` | Settings section per contract (mock server data from the examples above) | **Standard** (token handling in the DOM; Full if the modal storage rule is doubtful) |
| E docs | `docs/api-tokens.md` (user guide), `docs/roles.md` link, `docs/roadmap.md` row, `docs/specs/migration-ids.md` untouched | usage, curl examples, threat model summary | **Light** |

A and C touch different files from B/D/E; B and D need only the contract. Merge order: A+C, B, D, E.

**Migration**: one new migration `<next ordinal>-api_tokens` (random 8-hex id, `requires` = the head at merge time;
run `permita38 create-migration api_tokens`) containing both tables in all dialects. No ordinal is fixed here because
parallel branches (business trips, accounting) also add migrations; the `requires` chain decides.

## Open decisions for the maintainer

1. **Hash**: plain SHA-256 (recommended, rule 3) vs HMAC with the device secret (breaks sync across devices with different
   configs).
2. **Expiry mandatory** (recommended, max 365 days) vs allowing "never".
3. **Password change / "log out everywhere" also revokes tokens?** Recommended: no, tokens are separate credentials;
   deactivation and "Revoke all" revoke them. A one-click "Revoke all my tokens" sits next to "Change password".
4. **Audit scope for reads**: log every token request (recommended; volume bounded by the rate limit and 365-day
   retention) vs writes only.
5. **Scopes immutable after creation** (recommended) vs editable downwards.
6. **Who may create tokens**: every user within their own rights (recommended) vs boss only.
