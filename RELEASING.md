# Releasing permitA38

This is the maintainer runbook. **Every release has a git tag - no exceptions.** The tag is the one fact all other
artifacts derive from: the GitHub release, the wheel and sdist, the standalone downloads, the container image, the
checksums and the provenance attestations. A release without a tag cannot be reproduced, audited or yanked cleanly.

## Version spellings

| Where | Spelling | Example |
|---|---|---|
| git tag, CHANGELOG, GitHub release, container tag | SemVer 2.0.0 | `v0.4.0-alpha.1` (image tag: `0.4.0-alpha.1`) |
| package metadata (`permita38.__version__`, wheel name) | PEP 440 | `0.4.0a1` |

Pre-release counters start at 1 (`alpha.1`, `beta.1`, `rc.1`). The mapping is implemented and tested in
`scripts/release.py` (`python scripts/release.py pep440 0.4.0-alpha.1` prints `0.4.0a1`). The version is single-sourced:
edit only `permita38/__init__.py`; `pyproject.toml` reads it.

## Pre-flight checklist

1. All work for the release is merged to `main` through pull requests; CI on `main` is green (Linux + Windows,
   Python 3.10-3.13, lint, OpenAPI contract).
2. Quality gates of the release train are met (see `docs/release-plan.md`, "Entry criteria").
3. Prepare a **release commit** (`chore(release): 0.4.0-alpha.1`) via pull request that
   - sets `__version__ = "0.4.0a1"` in `permita38/__init__.py`,
   - turns `## [Unreleased]` entries into `## [0.4.0-alpha.1] - YYYY-MM-DD` (keep an empty `## [Unreleased]` above it)
     and fixes the compare links at the bottom,
   - regenerates `THIRD_PARTY_NOTICES.md` (`python scripts/generate_notices.py`) if dependencies changed,
   - passes `make lint test test-release`.
4. Update your local `main` (`git switch main && git pull --ff-only`).
5. Your signing key is configured (below) and `git config user.name/user.email` match your verified GitHub identity.

## Cut the tag

```sh
make release VERSION=0.4.0-alpha.1 RELEASE_FLAGS="--dry-run"   # all checks, creates nothing
make release VERSION=0.4.0-alpha.1 RELEASE_FLAGS="--sign"      # annotated + signed tag, not pushed
git show v0.4.0-alpha.1                                        # read the message and the signature
git push origin refs/tags/v0.4.0-alpha.1                       # starts the Release workflow
```

`scripts/release.py tag` refuses unless: the version string is valid; the working tree is clean; you are on `main`;
`main` equals `origin/main`; `__version__` matches the tag; `CHANGELOG.md` has a dated, non-empty section for it; the tag
does not exist locally or on the remote; and `make lint test test-release` pass (`--skip-checks` exists only together
with `--dry-run`). The tag message is the CHANGELOG section. The script never pushes unless you pass `--push`, and then
pushes only that tag.

### Signing (recommended, required once branch/tag protection is configured)

SSH signing (no GPG needed):

```sh
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global tag.gpgSign true          # sign every tag; commit.gpgSign true for commits
# add the same public key on GitHub as a *signing key* (Settings > SSH and GPG keys)
```

GPG:

```sh
git config --global user.signingkey <KEYID>
git config --global tag.gpgSign true
```

Plain annotated, unsigned tag: `git tag -a v0.4.0-alpha.1 -F message.txt` (what the script does without `--sign`).
Lightweight tags (`git tag v...`) are rejected by CI.

### Verify a tag

```sh
git fetch --tags
git cat-file -t v0.4.0-alpha.1      # must print: tag   (annotated)
git tag -v v0.4.0-alpha.1           # checks the signature (needs the signer's key in your keyring / allowed_signers)
```

For SSH signatures keep an `allowed_signers` file and set `gpg.ssh.allowedSignersFile`. GitHub also shows a "Verified"
badge next to the tag.

## What the Release workflow does

`.github/workflows/release.yaml`, triggered by the tag push:

1. **validate** - tag format, annotated, commit is on `main`, package version == tag, CHANGELOG section exists.
2. **test** - lint, OpenAPI contract, backend + release tests on Linux and Windows, license gate.
3. **dist** - sdist + wheel, `twine check --strict`, clean-venv install and server smoke test, CycloneDX SBOM.
4. **bundle** - PyInstaller bundles for Linux and Windows (x86_64), smoke-tested as built and again from the archive.
5. **docker** - image smoke test, multi-arch push to `ghcr.io/roggenbrot42/permita38:<version>` (`latest` only for
   final releases), provenance attestation.
6. **publish** (environment `release`, needs reviewer approval) - `SHA256SUMS`, provenance attestations for every file,
   release notes from the CHANGELOG, and a **draft** GitHub release (marked *pre-release* for alpha/beta/rc).

Nothing is public until you review the draft and press **Publish release**. A manual run (Actions > Release > Run
workflow, enter the version) builds and tests everything but publishes nothing - use it before the real tag.

## After the release

1. Open the draft, check the notes, the 6+ assets and `SHA256SUMS`; download one bundle and run it; then publish.
2. `gh attestation verify <file> --repo roggenbrot42/permitA38` for one artifact.
3. Pull the image and run it once (`docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/roggenbrot42/permita38:<version>`).
4. Announce (see the communication plan in `docs/release-plan.md`), label feedback issues `alpha-feedback`.
5. Start the next cycle: keep `## [Unreleased]` in the CHANGELOG; do not bump the version until the next release commit.

## If the release workflow fails

* Failed **before** `publish`: nothing was published except possibly the container image. Fix the cause on `main`
  (pull request), then release a **new version** (`alpha.2`). Do not move or delete a pushed tag that anybody could have
  seen; an unused tag whose workflow failed in `validate` may be deleted (`git push origin :refs/tags/vX`, then
  `git tag -d vX`) and recreated only if no release or image exists for it.
* Failed in `publish`: re-run the failed job from the Actions page (artifacts are kept for the run). If the draft
  release exists, delete the draft and re-run.
* Flaky smoke test: re-run once; a second failure is a real failure.

## Hotfix

Branch `hotfix/X.Y.Z` from the tag of the broken release, cherry-pick the fix (it must also land on `main`), prepare
the release commit with patch bump (`0.4.1`) and tag from that branch. Because the script insists on `main`, hotfixes are
normally released from `main` after reverting/fixing there; only if `main` has moved on incompatibly use a hotfix branch
and run the checks with `--branch hotfix/X.Y.Z`.

## Yanking a bad release

Never delete tags or rewrite history. Instead: edit the GitHub release and mark it as a pre-release (or revert it to
draft), prepend a **"Do not use"** notice with the reason and the fixed version, remove the `latest` container tag if
it points to it (`docker buildx imagetools create` the previous digest to `latest`), add a `### Security` or `### Fixed`
note to the CHANGELOG of the fixed release, and publish the fix as a new version. For security problems follow
`SECURITY.md` (advisory first, details after the fix is available).

## One-time repository setup

See `docs/maintainer-setup.md` (branch protection, tag protection for `v*`, release environment, vulnerability
reporting, signing key registration).
