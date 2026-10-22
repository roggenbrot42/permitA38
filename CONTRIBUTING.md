# Contributing to permitA38

Thanks for helping! This is a small volunteer-run project; bug reports, fixes, translations, docs and ideas are all
welcome. By participating you agree to follow the licensing rules below.

## Licensing and the Developer Certificate of Origin (DCO)

permitA38 is licensed under the **GNU General Public License v3.0 or later** (see [LICENSE](LICENSE)). All
contributions are accepted under that same license. There is no copyright assignment and no CLA – you keep the
copyright in your contribution.

To make clear that you are allowed to contribute your changes, every commit must carry a **sign-off** line:

```
Signed-off-by: Your Name <your.email@example.com>
```

Git adds it for you:

```bash
git commit -s -m "fix: round VAT half-up"
# forgot it? amend the last commit, or rewrite a series:
git commit --amend -s --no-edit
git rebase --signoff main
```

Use your real name (or the name you are known by in the community) and an email address you control. A GitHub
noreply address is fine. By adding the line you certify the following:

> **Developer Certificate of Origin, Version 1.1**
>
> Copyright (C) 2004, 2006 The Linux Foundation and its contributors.
>
> Everyone is permitted to copy and distribute verbatim copies of this license document, but changing it is not
> allowed.
>
> By making a contribution to this project, I certify that:
>
> (a) The contribution was created in whole or in part by me and I have the right to submit it under the open source
> license indicated in the file; or
>
> (b) The contribution is based upon previous work that, to the best of my knowledge, is covered under an appropriate
> open source license and I have the right under that license to submit that work with modifications, whether created
> in whole or in part by me, under the same open source license (unless I am permitted to submit under a different
> license), as indicated in the file; or
>
> (c) The contribution was provided directly to me by some other person who certified (a), (b) or (c) and I have not
> modified it.
>
> (d) I understand and agree that this project and the contribution are public and that a record of the contribution
> (including all personal information I submit with it, including my sign-off) is maintained indefinitely and may be
> redistributed consistent with this project or the open source license(s) involved.

In short: it is your work (or you may legally pass it on), you submit it under the project's license, and you know it
becomes public.

### Practical rules that follow from this

- **Do not copy code** from projects whose license is not compatible with GPLv3 (for example proprietary code,
  code without a license, or "non-commercial" licenses). Permissive licenses (MIT, BSD, Apache-2.0) and GPL/AGPL-3
  code are compatible, but say where it came from in the pull request and keep the original copyright notice.
- **AI-assisted contributions are fine**, but you are the one certifying the DCO: review everything, make sure it does
  not reproduce someone else's licensed code, and say in the pull request that AI tools were used.
- **No personal or business data.** Never commit real customer data, invoices, bank details, tax numbers, secrets or
  backups – not even in tests or screenshots. Use clearly fake demo data (e.g. "ACME GmbH").
- Pull requests whose commits lack a sign-off cannot be merged.

## Reporting bugs and security problems

- Normal bugs and feature ideas: open a GitHub issue with steps to reproduce, expected vs. actual behaviour, version,
  and how you run it (desktop, Docker).
- **Security problems: do not open a public issue** – follow [SECURITY.md](SECURITY.md).

## Development setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/roggenbrot42/permitA38.git
cd permitA38
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install                                  # optional: ruff on every commit

make test            # backend tests
make lint            # ruff + mypy
make format          # ruff format
make run             # start the app with a throw-away data folder
```

Project layout: `permita38/api` (FastAPI routers), `permita38/core` (services, calculations, repository layer),
`web/` (vanilla JS frontend, no build step), `tests/` (per subsystem), `docs/`. See the README's *Architecture*
section and [AGENTS.md](AGENTS.md).

### Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`,
  `chore:`), imperative summaries, small focused commits, each signed off (`-s`).
- **Code style:** ruff with line length 120, type hints, mypy must pass. Keep the frontend dependency-free and
  CSP-friendly (no inline scripts, no CDNs).
- **API changes:** update the models/routers, then regenerate the contract with `make generate-openapi` and commit
  `api/openapi.json`. CI fails if it is out of date.
- **Database changes:** add a numbered migration for **every** SQL dialect (`make create-migration`), never edit an
  existing migration, and bump `CURRENT_REVISION`. See `docs/porting-to-postgres.md`.
- **Tests:** every bug fix needs a regression test, every feature needs tests. Backend tests live in
  `tests/backend/` (`make test`), the randomized API tests in `tests/backend/randomized/` (`make test-random`),
  browser tests in `tests/web/` (`make test-web`) and the future Android app's in `tests/android/`. Randomised/monkey
  tests must stay reproducible by seed. CI runs the backend suite on Linux and Windows (Python 3.10–3.13), and the
  randomized and browser suites with their default deterministic seeds; a nightly workflow explores with fresh seeds.
- **Money and taxes:** use exact decimal arithmetic, never floats. Changes to tax rules, rates, VAT-return boxes or
  account mappings must cite an official source (law text, BMF/ELSTER/DATEV documentation) in the pull request.
  permitA38 provides planning estimates, not tax advice – keep wording and disclaimers accurate.
- **Docs and changelog:** update the README/docs and add an entry under *Unreleased* in `CHANGELOG.md`.

## Pull requests

1. Fork, create a branch from `main`, make your change with tests.
2. Run `make lint test` locally; make sure all commits are signed off.
3. Open the pull request, describe *what* and *why*, link the issue, and mention AI assistance or third-party code
   if applicable.
4. Be patient and kind – reviews happen in people's spare time.

## Code of conduct

Be respectful and constructive. Harassment or personal attacks are not tolerated; maintainers may remove comments and
block contributors who repeatedly violate this.

## Questions

Open a GitHub discussion or issue. Thank you for contributing!
