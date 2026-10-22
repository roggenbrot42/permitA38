#!/usr/bin/env python3
"""Release tooling for permitA38 (standard library only).

Every release has an annotated git tag ``vX.Y.Z[-alpha.N|-beta.N|-rc.N]``. This script is the single place that knows
the version rules; the GitHub release workflow calls it too, so CI and maintainers apply identical checks.

    release.py tag 0.4.0-alpha.1 [--dry-run] [--sign] [--push] [--skip-checks] [--no-fetch]
    release.py check v0.4.0-alpha.1 [--require-annotated]
    release.py notes 0.4.0-alpha.1            # CHANGELOG section for the GitHub release body
    release.py info v0.4.0-alpha.1 [--github-output]
    release.py pep440 0.4.0-alpha.1           # -> 0.4.0a1

Version spellings
-----------------
* git tag / CHANGELOG / GitHub release:  ``v0.4.0-alpha.1``  (SemVer 2.0.0, pre-release counters start at 1)
* package metadata (PEP 440):            ``0.4.0a1``         (``permita38.__version__``)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:-(?P<pre>alpha|beta|rc)\.(?P<n>[1-9]\d*))?$"
)
PEP440_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:(?P<pre>a|b|rc)(?P<n>[1-9]\d*))?$"
)
_PRE_TO_PEP = {"alpha": "a", "beta": "b", "rc": "rc"}
_PEP_TO_PRE = {v: k for k, v in _PRE_TO_PEP.items()}
_HEADING_RE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*-\s*(?P<date>\S+))?\s*$")
_LINK_DEF_RE = re.compile(r"^\[[^\]]+\]:\s")


class ReleaseError(Exception):
    """A release rule is violated; the message tells the maintainer what to fix."""


# ----------------------------------------------------------------------------- versions
def validate_semver(version: str) -> str:
    """Return *version* if it is a valid permitA38 release version, otherwise raise."""
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError(
            f"invalid version {version!r}: expected MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-(alpha|beta|rc).N "
            "(no leading 'v', no leading zeros, pre-release counters start at 1)"
        )
    return version


def semver_to_pep440(version: str) -> str:
    """``0.4.0-alpha.1`` -> ``0.4.0a1``."""
    m = SEMVER_RE.fullmatch(validate_semver(version))
    assert m
    base = f"{m['major']}.{m['minor']}.{m['patch']}"
    return base + (f"{_PRE_TO_PEP[m['pre']]}{m['n']}" if m["pre"] else "")


def pep440_to_semver(version: str) -> str:
    """``0.4.0a1`` -> ``0.4.0-alpha.1``."""
    m = PEP440_RE.fullmatch(version)
    if not m:
        raise ReleaseError(f"package version {version!r} is not a supported PEP 440 release version (X.Y.Z[a|b|rcN])")
    base = f"{m['major']}.{m['minor']}.{m['patch']}"
    return base + (f"-{_PEP_TO_PRE[m['pre']]}.{m['n']}" if m["pre"] else "")


def semver_to_tag(version: str) -> str:
    return "v" + validate_semver(version)


def tag_to_semver(tag: str) -> str:
    if not tag.startswith("v"):
        raise ReleaseError(f"invalid tag {tag!r}: release tags start with 'v' (e.g. v0.4.0-alpha.1)")
    return validate_semver(tag[1:])


def is_prerelease(version: str) -> bool:
    m = SEMVER_RE.fullmatch(validate_semver(version))
    assert m
    return m["pre"] is not None


def normalize_version_arg(value: str) -> str:
    """Accept ``0.4.0-alpha.1`` as well as ``v0.4.0-alpha.1``."""
    return validate_semver(value[1:] if value.startswith("v") else value)


def read_package_version(root: Path = ROOT) -> str:
    text = (root / "permita38" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise ReleaseError("could not find __version__ in permita38/__init__.py")
    return m.group(1)


# ----------------------------------------------------------------------------- changelog
@dataclass
class Section:
    version: str  # "Unreleased" or a SemVer string
    date: str | None
    body: str


def parse_changelog(text: str) -> list[Section]:
    sections: list[Section] = []
    cur: tuple[str, str | None, list[str]] | None = None
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if cur:
                sections.append(Section(cur[0], cur[1], _clean_body(cur[2])))
            cur = (m["version"], m["date"], [])
        elif cur is not None and not _LINK_DEF_RE.match(line):
            cur[2].append(line)
    if cur:
        sections.append(Section(cur[0], cur[1], _clean_body(cur[2])))
    return sections


def _clean_body(lines: list[str]) -> str:
    return "\n".join(lines).strip("\n").rstrip()


def find_section(text: str, version: str) -> Section | None:
    return next((s for s in parse_changelog(text) if s.version == version), None)


def require_release_section(text: str, version: str) -> Section:
    """The CHANGELOG must contain ``## [version] - YYYY-MM-DD`` with content."""
    section = find_section(text, version)
    if section is None:
        raise ReleaseError(
            f"CHANGELOG.md has no section '## [{version}] - YYYY-MM-DD' (move the Unreleased entries into it first)"
        )
    if not section.date:
        raise ReleaseError(
            f"CHANGELOG.md section [{version}] has no release date (expected '## [{version}] - YYYY-MM-DD')"
        )
    try:
        dt.date.fromisoformat(section.date)
    except ValueError:
        raise ReleaseError(
            f"CHANGELOG.md section [{version}] has an invalid date {section.date!r} (use YYYY-MM-DD)"
        ) from None
    if not section.body.strip():
        raise ReleaseError(f"CHANGELOG.md section [{version}] is empty")
    return section


# ----------------------------------------------------------------------------- git
def git(*args: str, cwd: Path = ROOT, input: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def check_repo_state(repo: Path, *, remote: str = "origin", branch: str = "main", fetch: bool = True) -> None:
    """Clean tree, on the release branch, in sync with the remote."""
    if git("status", "--porcelain", cwd=repo):
        raise ReleaseError("working tree is not clean (commit or stash your changes)")
    current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    if current != branch:
        raise ReleaseError(f"releases are cut from '{branch}', but the current branch is '{current}'")
    if fetch:
        git("fetch", "--quiet", remote, branch, "--tags", cwd=repo)
    remote_ref = f"{remote}/{branch}"
    if git("rev-parse", "--verify", "--quiet", remote_ref, cwd=repo, check=False) == "":
        raise ReleaseError(f"remote branch {remote_ref} not found")
    ahead, behind = git("rev-list", "--left-right", "--count", f"HEAD...{remote_ref}", cwd=repo).split()
    if behind != "0":
        raise ReleaseError(f"{branch} is {behind} commit(s) behind {remote_ref}: pull first")
    if ahead != "0":
        raise ReleaseError(f"{branch} has {ahead} commit(s) that are not on {remote_ref}: push (and let CI pass) first")


def tag_exists(repo: Path, tag: str, remote: str | None = "origin") -> bool:
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", cwd=repo, check=False):
        return True
    if remote:
        out = git("ls-remote", "--tags", remote, f"refs/tags/{tag}", cwd=repo, check=False)
        return bool(out)
    return False


def create_tag(repo: Path, version: str, message: str, *, sign: bool = False) -> str:
    """Create the annotated (optionally signed) tag and return its name."""
    tag = semver_to_tag(version)
    git("tag", "-s" if sign else "-a", tag, "-F", "-", cwd=repo, input=message + "\n")
    return tag


def tag_message(version: str, section: Section) -> str:
    return f"permitA38 {semver_to_tag(version)} ({section.date})\n\n{section.body}"


# ----------------------------------------------------------------------------- checks
def check_release_metadata(version: str, root: Path = ROOT) -> Section:
    """Package version, CHANGELOG section: everything that must agree before a tag may exist."""
    validate_semver(version)
    pkg = read_package_version(root)
    expected = semver_to_pep440(version)
    if pkg != expected:
        raise ReleaseError(
            f"permita38/__init__.py has __version__ = {pkg!r} but release {version} needs {expected!r} "
            "(bump the version in a 'chore(release)' commit before tagging)"
        )
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return require_release_section(changelog, version)


def run_quality_gates(root: Path = ROOT) -> None:
    print("Running quality gates: make lint test test-release ...")
    proc = subprocess.run(["make", "lint", "test", "test-release"], cwd=root)
    if proc.returncode != 0:
        raise ReleaseError("quality gates failed (fix the problems above, a release is only cut from green code)")


# ----------------------------------------------------------------------------- commands
def cmd_tag(args: argparse.Namespace) -> int:
    version = normalize_version_arg(args.version)
    tag = semver_to_tag(version)
    repo = Path(args.repo).resolve()
    if args.skip_checks and not args.dry_run:
        raise ReleaseError("--skip-checks is only allowed together with --dry-run")
    if args.push and args.dry_run:
        raise ReleaseError("--push cannot be combined with --dry-run")

    check_repo_state(repo, remote=args.remote, branch=args.branch, fetch=not args.no_fetch)
    section = check_release_metadata(version, repo)
    if tag_exists(repo, tag, None if args.no_fetch else args.remote):
        raise ReleaseError(f"tag {tag} already exists (tags are never moved or re-used: bump the version instead)")
    if not args.skip_checks:
        run_quality_gates(repo)

    message = tag_message(version, section)
    kind = "signed" if args.sign else "annotated"
    if args.dry_run:
        print(
            f"[dry-run] all checks passed. Would create {kind} tag {tag} on {git('rev-parse', '--short', 'HEAD', cwd=repo)}:"
        )
        print("\n".join("    " + line for line in message.splitlines()[:12]))
        return 0

    create_tag(repo, version, message, sign=args.sign)
    print(f"Created {kind} tag {tag}.")
    push_cmd = f"git push {args.remote} refs/tags/{tag}"
    if args.push:
        git("push", args.remote, f"refs/tags/{tag}", cwd=repo)
        print(f"Pushed {tag}. The 'Release' workflow now builds the artifacts and prepares a draft GitHub release.")
    else:
        print(f"Review it with:  git show {tag}\nThen publish it with:  {push_cmd}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    tag = args.tag
    version = tag_to_semver(tag)
    repo = Path(args.repo).resolve()
    section = check_release_metadata(version, repo)
    if args.require_annotated:
        kind = git("cat-file", "-t", f"refs/tags/{tag}", cwd=repo, check=False)
        if kind != "tag":
            raise ReleaseError(
                f"{tag} is not an annotated tag (found object type {kind or 'none'}): release tags must be annotated"
            )
    print(f"OK: {tag} -> package {semver_to_pep440(version)}, CHANGELOG section dated {section.date}")
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    version = normalize_version_arg(args.version)
    changelog = (Path(args.repo) / "CHANGELOG.md").read_text(encoding="utf-8")
    section = require_release_section(changelog, version)
    print(section.body)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    version = tag_to_semver(args.tag)
    info = {
        "tag": args.tag,
        "version": version,
        "pep440": semver_to_pep440(version),
        "prerelease": "true" if is_prerelease(version) else "false",
    }
    lines = [f"{k}={v}" for k, v in info.items()]
    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            raise ReleaseError("GITHUB_OUTPUT is not set")
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def cmd_pep440(args: argparse.Namespace) -> int:
    print(semver_to_pep440(normalize_version_arg(args.version)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="release.py", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo", default=str(ROOT), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)

    t = sub.add_parser("tag", help="run all checks and create the annotated release tag")
    t.add_argument("version", help="e.g. 0.4.0-alpha.1")
    t.add_argument("--dry-run", action="store_true", help="run every check, create nothing")
    t.add_argument(
        "--sign", action="store_true", help="create a signed tag (git tag -s: GPG, or SSH via gpg.format=ssh)"
    )
    t.add_argument("--push", action="store_true", help="push the tag (and only the tag) after creating it")
    t.add_argument("--skip-checks", action="store_true", help="skip 'make lint test' (only with --dry-run)")
    t.add_argument("--no-fetch", action="store_true", help="do not contact the remote (used by tests)")
    t.add_argument("--remote", default="origin")
    t.add_argument("--branch", default="main")
    t.set_defaults(func=cmd_tag)

    c = sub.add_parser("check", help="validate a tag against package version and CHANGELOG (used by CI)")
    c.add_argument("tag")
    c.add_argument("--require-annotated", action="store_true")
    c.set_defaults(func=cmd_check)

    n = sub.add_parser("notes", help="print the CHANGELOG section of a release")
    n.add_argument("version")
    n.set_defaults(func=cmd_notes)

    i = sub.add_parser("info", help="print version facts for a tag (optionally to $GITHUB_OUTPUT)")
    i.add_argument("tag")
    i.add_argument("--github-output", action="store_true")
    i.set_defaults(func=cmd_info)

    p = sub.add_parser("pep440", help="convert a release version to its PEP 440 spelling")
    p.add_argument("version")
    p.set_defaults(func=cmd_pep440)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ReleaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
