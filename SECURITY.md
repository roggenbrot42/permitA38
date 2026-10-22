# Security policy

permitA38 handles business and customer data (invoices, bookkeeping, tax figures), so security reports are taken
seriously. This is a volunteer-run open-source project: there is no bug bounty and no SLA, but reports are handled on a
best-effort basis and credited if you wish.

## Supported versions

Only the latest release (and the `main` branch) receives security fixes. Please update before reporting.

| Version | Supported |
|---------|-----------|
| latest release / `main` | yes |
| older releases | no |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private vulnerability reporting:
<https://github.com/roggenbrot42/permitA38/security/advisories/new>

Include, as far as you can:

- what is affected (component, version or commit, deployment: desktop, Docker, behind a proxy);
- steps to reproduce or a proof of concept;
- the impact you expect (data exposure, privilege escalation, data corruption, ...);
- any suggested fix.

**Never include real customer, financial or personal data** in a report – use made-up demo data.

### What to expect

| Step | Target |
|------|--------|
| Acknowledgement | within 7 days |
| Initial assessment | within 14 days |
| Fix or mitigation for confirmed issues | within 90 days (faster for critical issues) |

We follow coordinated disclosure: please give us the time above before publishing details. Once a fix is released we
publish a GitHub security advisory and credit the reporter (unless you prefer to stay anonymous).

## Scope

In scope:

- authentication and session handling (login, tokens, cookies, throttling, password storage);
- authorisation: reading or changing data you have no right to (including role and rights checks);
- injection, XSS, path traversal, unsafe file handling (attachments, backups, imports);
- exposure of secrets or of the data folder path;
- the official Docker image and the example proxy configurations;
- data-integrity problems in bookkeeping, invoices or backups that could be triggered remotely.

Out of scope:

- a deployment served over plain HTTP on an untrusted network, weak or reused passwords, or an exposed first-run setup
  page (see the hardening notes below);
- anyone who already has access to the synced data folder or the database file – that gives full read access **by
  design** (see "Roles and your team" in the README);
- denial of service against a single-user local instance;
- vulnerabilities in third-party dependencies without a demonstrated impact on permitA38 (report those upstream; we
  track them through dependency updates);
- findings that need physical access to an unlocked device.

## Safe harbour

If you act in good faith – test only against your own instance, do not access or keep other people's data, do not
degrade service for others, and report privately – we will not pursue legal action over your research.

## Hardening checklist for operators

- Serve over **HTTPS** (see `docs/docker.md` for Caddy and nginx examples); never expose plain HTTP to the internet.
- Create the first boss account with the `PERMITA38_BOOTSTRAP_*` variables so nobody else can claim the instance
  through the setup page.
- Use strong, unique passwords; give people only the roles and rights they need.
- Turn on **two-factor authentication (TOTP)** for every boss account (account menu, "Two-factor authentication") and
  keep the recovery codes offline. The TOTP secret is stored in the database, so protect the database file and your
  backups at rest: it cannot be encrypted with the per-device key because the database may be shared between devices.
- Keep `PERMITA38_SECRET_KEY` secret; rotating it signs everybody out.
- Keep the data folder and backups readable only by the service user, and encrypt backups that leave the machine.
- Keep the application and the container image up to date and watch the repository's security advisories.
- Back up regularly and test the restore (`permita38 backup`).

## No warranty

permitA38 is licensed under the GNU GPLv3 and comes **without warranty**. It provides planning estimates, not tax or
legal advice.
