# API tokens

An **API token** lets a program use your permitA38 account without your password: a backup script, a spreadsheet
import, an MCP server for an AI assistant. The program sends the token instead of a login, and permitA38 treats the
request as coming from you, with your rights, and never more.

## When to use one

* **Scripts and integrations** that read your data (reports, backups, a dashboard).
* **MCP / AI assistants** that should look at your projects and time. Use a read-only token (see below).
* **Not** for people: colleagues get their own account under Team, never a shared token.

If you only click around in the web app you do not need a token.

## Create a token

1. Open **Settings** and find the **API tokens** card (every signed-in role has it, it is your own credential).
2. Click **New token**.
3. Give it a **label** that says what it is for ("Backup script", up to 80 characters).
4. Choose how long it stays valid (7, 30, 90, 180 or 365 days; the default is 90).
5. Leave **Read only** ticked unless the program really has to change data.
6. Click create. The token appears once in a dialog; copy it, tick **I have saved it** and close the dialog.

**Show-once rule.** The token (`pat_` followed by 43 characters) is displayed exactly once. permitA38 stores only a
fingerprint (a SHA-256 hash), so nobody, not even the boss, can show it again. If you lose it, revoke it and create a
new one. The list shows only a short hint (`pat_Xk3f`) so you can recognise which token is which.

You can have at most 20 active tokens. Revoked and expired tokens do not count.

## Read-only or read-write

| | Read-only (default) | Read-write |
|---|---|---|
| Reading data your role may read | yes | yes |
| Creating, changing, deleting data | **no** (answer 403) | yes, as far as your role may |

A token never has more rights than its owner. Rights are checked on **every request** against your *current* role: if a
boss is demoted to employee, the very next request of their tokens is limited to an employee's rights. If you are
deactivated, your tokens stop working at once, and they stay revoked even if you are reactivated later.

## Expiry and revoking

Every token expires (1 to 365 days). An expired token answers 401. To end a token earlier click **Revoke** in the list;
it takes effect with the next request. **Revoke all my tokens** ends all of yours. A boss can switch on **Show all
users** to see and revoke anybody's tokens (but never to see a secret). A token cannot be edited: to change the label,
expiry or access, revoke it and create a new one.

## What a token cannot do

Some routes are **session-only**: they need a real sign-in in the web app and answer
`403 {"detail": "This route cannot be used with an API token"}` for any token, read-write or not.

* managing tokens (create, list, revoke, audit log),
* signing in or out, changing the password, two-factor authentication (`/api/auth/*` except `GET /api/auth/me`,
  `/api/totp/*`),
* user administration (`/api/users/*`),
* **writing** settings (`PUT`, `POST`, `DELETE` under `/api/settings`); reading them works with the owner's rights,
* changing the invoice numbering, importing data, and connecting or changing calendars.

So a stolen token can never create more tokens, lock you out or change your company settings.

## Audit log

Every request made with a token is recorded: when, which token, method, route (for example `/api/customers/{customer_id}`)
and the answer status. Nothing else: no request body, no record ids, no query string, no token. Open **Activity** next to
a token to see its log. Entries are kept for 365 days. Requests with an unknown or expired token cannot be assigned to a
token and are not in the list.

## Using a token

Send it in the `Authorization` header (never in the URL, a cookie or the body):

```bash
curl -s -H "Authorization: Bearer pat_xxx" http://127.0.0.1:8765/api/customers
```

Replace `pat_xxx` by your token and the address by your server. Answers:

| Status | Meaning |
|---|---|
| 200 | ok |
| 401 | token unknown, revoked or expired, or the owner is deactivated (always the same message) |
| 403 | read-only token on a write, a session-only route, or your role may not do this |
| 429 | too many failed token attempts from your address, wait (see the `Retry-After` header) |

**Throttle and `trust_proxy`.** Failed token attempts are counted per client address like failed logins, five within
five minutes lock that address out. Behind a reverse proxy permitA38 sees only the proxy's address unless you set
`PERMITA38_TRUST_PROXY` (`trust_proxy`, see [docker.md](docker.md)); with it set, the address the proxy appends in `X-Forwarded-For` is used. Set it
only when the app is reachable exclusively through your proxy, otherwise clients could pick their own address.

## Security tips

* Keep tokens in a password manager or a secret store, not in the code, a chat or a screenshot.
* Prefer read-only tokens and short lifetimes; one token per program, so you can revoke one without breaking the others.
* Use HTTPS when the server is not on the same machine; a token sent over plain HTTP can be read on the network.
* Revoke a token as soon as you suspect it leaked, then look at its **Activity** list to see what it did.
* A token for an AI assistant should be read-only. Remember that what the assistant reads may be sent to its provider.
* Tokens are stored in the data folder only as hashes; the server's `secret_key` stays in the per-device config.

Technical details (rights check, audit schema, routes): [contracts/api-tokens.md](contracts/api-tokens.md). Roles:
[roles.md](roles.md).
