# Specification: API tokens

Status: **decided**, HTTP contract in [`docs/contracts/api-tokens.md`](../contracts/api-tokens.md) (wins on shapes), roadmap alpha.3 ("Scoped API tokens"). First of three specs, in dependency order:
`api-tokens.md` (this) -> [`mcp-server.md`](mcp-server.md) -> [`ai-provider-and-proposals.md`](ai-provider-and-proposals.md).
Author: architect (Sonnet 5.5). The implementer follows this document, makes no architectural choices and lists every
choice it had to make under "Open design decisions" in its report. **Review tier: Full** (authentication, rights).

## Goal

A signed-in user can create long-lived, revocable **API tokens** for scripts, integrations and the MCP server. A token
acts as its owner: its scope is the owner's role, optionally narrowed to **read-only**. A token can never do more than
its owner can do **at the moment of use**.

Non-goals: OAuth/OIDC flows, per-record scopes (a token for one customer only), tokens for subcontractors without an
account, token-managed-by-token, per-area grants, general rate limiting (only the invalid-token throttle, rule 12),
IP allow-lists (later), webhooks. Custom roles stay out of scope.

## Today (what exists)

* Session = stateless HMAC-signed JWT (`AuthService.create_token`, claim `use: session`), delivered as an HttpOnly,
  `SameSite=Strict` cookie `authorization` and also accepted as `Authorization: Bearer <jwt>` (`api/deps.py`).
* Rights: `rights.MATRIX[role][area] = (read scope, write scope)`, scopes `none < limited < all`. Routes declare
  `Needs(area, op)`; services call `rights.scope(user["role"], area, op)` for the limited/own-records view.
* No CSRF token (the cookie is `SameSite=Strict`), no audit log (the GoBD change log is planned for alpha.4),
  `AttemptThrottle` for failed logins, in-memory only. Server-side session revocation is in progress.

## Rules

Decided by the architect (see the contract for the full list and the decisions taken).

1. **Format**: `pat_<secret>`, `<secret>` = 32 random bytes (`secrets.token_bytes`), base64url without padding (43
   characters). The fixed prefix lets secret scanners and the auth dependency tell it from a session JWT. The display
   `hint` is `pat_` + the first 4 characters of the secret, stored in clear.
2. **Shown once**: the plaintext is in the response of `POST /api/tokens` only; never stored, logged or retrievable.
3. **Stored hashed with SHA-256** (hex). The secret has 256 bits of entropy, so a slow KDF would only add latency and
   a salt is unnecessary. Lookup by hash (unique index), then `hmac.compare_digest`. No HMAC with the device
   `secret_key`: the data folder is synced between devices that may have different config files.
4. **Owner**: exactly one user. Any signed-in user (boss, employee, accountant) may create tokens for themselves. A
   deactivated owner revokes the tokens; a deleted owner deletes them (and their audit rows) in the same transaction.
5. **Scope** = the owner's role, plus a flag `read_only` (default `true` in the API and pre-checked in the UI). A
   read-only token is denied every write operation. There are no per-area grants, except that settings writes are
   session-only for every token (rule 8).
6. **Effective scope** is evaluated on **every request** against the owner's *current* role and the current
   `rights.MATRIX`: demoting the owner takes effect immediately. `read_only` additionally turns every `write` scope
   into `none` and denies all non-safe HTTP methods.
7. **Implementation seam** (the confused-deputy guard): `get_current_user` returns the owner's user dict plus a private
   key `_auth` (token requests only). `rights.scope_for(principal, area, op)` is the only function request paths use
   for the current principal; `rights.scope(role, ...)` remains for role-in/scope-out uses. All call sites that took the
   role from a request user are changed (list in the contract). A source-scan test and a route-walk test enforce it.
8. **Session-only routes**: `/api/tokens/*`, `/api/auth/*` (except `GET /api/auth/me`), `/api/totp/*`, `/api/users/*`
   and the **write routes** (`PUT`, `POST`, `DELETE`) under `/api/settings` accept the session only. A `pat_` credential
   there answers `403 {"detail": "This route cannot be used with an API token"}`, also for a read-write token of a
   boss. A token therefore cannot create, list or revoke tokens, change a password, touch MFA, use user admin, or change
   settings. Settings reads stay allowed with the owner's rights.
9. **Expiry**: mandatory, 1-365 days, default 90. Expired = `401`.
10. **Revocation**: immediate (state read from the database on every request). The owner, or a boss, can revoke. A
    revoked token stays revoked when its owner is reactivated.
11. **Last used**: `last_used_at` is updated at most once per 60 seconds per token.
12. **Limits**: at most 20 active tokens per user (`409` on the 21st). Invalid bearer tokens (unknown, revoked, expired,
    owner missing or inactive, malformed) are counted per client IP with the existing login throttle (same limits as
    failed logins); a locked-out client gets `429` with `Retry-After`. No other rate limit in this version.
13. **Audit**: every token-authenticated request writes one `audit_log` row: token id, owner, method, route template,
    status, time. Never bodies, tokens or query strings. Append-only, 365 days retention, pruned at startup. A failed
    audit write is logged and does not change the response.
14. **Transport**: only `Authorization: Bearer pat_...`. Never a query string, cookie or body. A bearer wins over a cookie.
15. **No ambient authority, CSRF**: a bearer token is not sent automatically by a browser; the SameSite=Strict cookie
    stays the only CSRF defence for sessions. A `pat_` value in the cookie is rejected; no token response sets a cookie.
16. **Responses never include secrets**: `GET /api/auth/me` with a token returns the user plus
    `"auth": {"kind": "token", "token_id", "label", "read_only", "expires_at"}`.
17. **Changing a token**: no PATCH. Label, expiry and `read_only` are fixed at creation; to change them, revoke and
    create a new token. No CLI creates tokens in this version.

## Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| **Leak** (token in a repo, log, chat, backup of an MCP client config) | `pat_` prefix for scanners; shown once; hashed at rest; mandatory expiry; revoke in the UI; `last_used_at` + audit make misuse visible; read-only default in the API and UI; no token in URLs | A leaked token is valid until revoked or expired, within the owner's rights. Prefer read-only and short lifetimes. |
| **Over-scope** (token creator picks too much, role later changes, matrix changes) | owner's current role at every use (rule 6); read-only flag; token/auth/settings-write routes session-only | A boss can still create a read-write token with the boss's rights (except settings writes); the UI warns. |
| **Replay** (captured request replayed) | TLS is the control: document that tokens over plain `http` are only for `127.0.0.1`; the server logs a warning at token creation when `host` is not loopback and the request is not https; expiry bounds the window | No nonce/signature scheme (not worth it; same trade-off as GitHub/Stripe keys). Idempotency of writes is the caller's job. |
| **Confused deputy** (a tool/route acts with the owner's full session rights instead of the token's) | single seam `rights.scope_for` (rule 7), source-scan test, route-walk test; session-only routes (rule 8); services receive the principal, never a bare role; MCP and AI features call the HTTP-equivalent service layer with the token's principal, never an internal super-user | New code that bypasses `Needs`/`scope_for` would escape: covered by the review checklist and the scan test. |
| **Brute force** (guessing tokens) | 256-bit secret; invalid-token throttle per client IP with the login limits (rule 12) | none practical |
| **Timing oracle** | hash lookup then `compare_digest`; same 401 body for unknown, revoked, expired | negligible |
| **Insider with data-folder access** | only hashes in the data folder; the plaintext is never written | A user who can edit the database could insert a hash of a token they know: the data folder is already fully trusted (it holds all business data). |
| **Token used against a changed owner password** | passwords do not revoke tokens (they are independent credentials); deactivating the owner and "Revoke all" do | Documented; see the open decisions in the contract. |

## Data model, API, UI, tests, work packages, migration

See [`docs/contracts/api-tokens.md`](../contracts/api-tokens.md): tables `api_tokens` and `audit_log`, routes
(`GET/POST /api/tokens`, `POST /api/tokens/{id}/revoke`, `POST /api/tokens/revoke-all`, `GET /api/tokens/{id}/audit`),
rights matrix, UI, test list, work-package split, review note, migration `0010-api_tokens`.

## Decisions taken (formerly open)

Plain SHA-256 at rest; expiry mandatory (max 365 days); password change does not revoke tokens (deactivation and
"Revoke all" do); every token request is audited, a failed audit write is logged only (fail-open); audit retention fixed
at 365 days, pruned at startup; scope fixed at creation (no PATCH, revoke and recreate); every user may create tokens
within their own rights; `read_only` defaults to `true`; invalid-token throttle per client IP with the login limits;
settings writes are session-only for tokens; a revoked token stays revoked after reactivation; no CLI `create-token` in
this version.

Proposals need a read-write token in this version; a `proposals-only` token mode is a listed follow-up (see
[`mcp-server.md`](mcp-server.md) and [`ai-provider-and-proposals.md`](ai-provider-and-proposals.md)).

Remaining assumptions: "Open design decisions" in the contract.
