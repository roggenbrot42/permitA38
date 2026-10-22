# Maintainer setup checklist

Repository settings that cannot be changed from the code. Do these once (Settings tab of
`github.com/roggenbrot42/permitA38`), and re-check after changes of the release pipeline. Tick them off in a tracking
issue before the first tag.

## Security

- [ ] **Private vulnerability reporting**: Settings > Code security > *Private vulnerability reporting* = enabled
      (`SECURITY.md` and the issue-template contact link point to it).
- [ ] **Dependabot alerts** and **Dependabot security updates** = enabled (version updates come from `.github/dependabot.yml`).
- [ ] **Code scanning**: the `CodeQL` workflow runs on its own; Settings > Code security > *Code scanning* should show
      "CodeQL" as the default analysis, not a second default setup. **Secret scanning** and push protection = enabled.
- [ ] Optional: *Dependency graph* enabled (needed for the dependency review and SBOM export on GitHub).

## Branch protection (ruleset for `main`)

- [ ] Require a pull request before merging; at least **1 approval** (raise to 2 when there are more maintainers);
      dismiss stale approvals; require review of the most recent push.
- [ ] Require status checks: `lint`, `test` (all matrix entries), `openapi`, `build` (Docker, path-filtered: add only if
      it always reports), `CodeQL`.
- [ ] Require **signed commits** and **linear history**; block force pushes and deletions.
- [ ] Require conversation resolution. Do not allow bypass, not even for admins (use a temporary exception if needed).
- [ ] CODEOWNERS review (`.github/CODEOWNERS`) enabled in the same rule.

## Tag protection (ruleset for tags matching `v*`)

- [ ] Restrict **creation** to maintainers, block **update** and **deletion**: release tags are immutable.
- [ ] Optionally require signed tags / verified signatures.

## Actions

- [ ] Settings > Actions > General: *Allow actions and reusable workflows from GitHub and verified creators* or an allow
      list (`actions/*`, `docker/*`, `github/codeql-action`, `ossf/scorecard-action`), and *Require actions to be pinned
      to a full-length commit SHA*.
- [ ] Workflow permissions default = **read repository contents** (workflows request more per job). Do not allow
      Actions to create or approve pull requests.
- [ ] Fork pull request workflows: require approval for first-time contributors.

## Release environment

- [ ] Settings > Environments > **New environment `release`**: *Required reviewers* = you (the second person once there
      is one); *Deployment branches and tags* = selected tags `v*`; no secrets are needed (the workflow only uses
      the automatic `GITHUB_TOKEN`).
- [ ] The `publish` job waits for this approval, so a pushed tag never produces a release by itself.

## Container registry (GHCR)

- [ ] After the first image push: Packages > `permita38` > Package settings: connect the repository, set visibility to
      **public** (alpha testers must be able to pull), and make sure Actions has *write* access to the package.
- [ ] Keep `latest` untouched for pre-releases (the workflow does not set it).

## Secrets and variables

None are required. Do **not** add PyPI tokens or signing keys as repository secrets: PyPI publishing, when added, will
use trusted publishing (OIDC); tags are signed on the maintainer's machine.

## Signing key

- [ ] Create a signing key (SSH `ssh-keygen -t ed25519` or GPG) and add its public part to GitHub as a **Signing key**
      (Settings > SSH and GPG keys); configure git as described in `RELEASING.md`.
- [ ] Keep a hardware-backed or passphrase-protected key; back up the revocation certificate (GPG).
- [ ] Publish the key fingerprint in `SECURITY.md` or the README so users can verify tags.

## Labels and project hygiene

- [ ] Create labels: `alpha-feedback`, `tax-rules`, `bug`, `enhancement`, `security`, `dependencies`, `blocker`.
- [ ] Enable Discussions (optional) and link it from `SUPPORT.md`.
- [ ] Enable *Automatically delete head branches* and *Allow squash merging* only (keeps history linear).
