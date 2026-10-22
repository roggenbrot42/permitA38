"""Fails CI when package version, CHANGELOG and (in release builds) the git tag disagree."""

import os
import re
from pathlib import Path

import release

ROOT = Path(__file__).resolve().parents[2]


def test_package_version_is_a_supported_pep440_release():
    version = release.read_package_version(ROOT)
    assert release.pep440_to_semver(version)  # raises if unsupported


def test_pyproject_reads_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^dynamic\s*=\s*\[[^\]]*"version"', text, re.M), "version must be dynamic (single source)"
    assert not re.search(r"^version\s*=", text.split("[tool.", 1)[0], re.M), "no second, static version in [project]"
    assert 'attr = "permita38.__version__"' in text


def test_changelog_has_a_section_for_the_package_version():
    pkg = release.read_package_version(ROOT)
    version = release.pep440_to_semver(pkg)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.find_section(changelog, version) is not None, (
        f"CHANGELOG.md needs a '## [{version}]' section for the current package version"
    )


def test_changelog_versions_are_valid_and_strictly_descending():
    sections = release.parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert sections and sections[0].version == "Unreleased", "CHANGELOG must start with an [Unreleased] section"
    versions = [s.version for s in sections[1:]]
    for v in versions:
        release.validate_semver(v)
    # compare through the PEP 440 spelling, which has a defined ordering for pre-releases
    packaging_version = __import__("packaging.version", fromlist=["Version"])
    parsed = [packaging_version.Version(release.semver_to_pep440(v)) for v in versions]
    assert parsed == sorted(parsed, reverse=True), "CHANGELOG sections must be in descending version order"
    assert len(set(versions)) == len(versions), "duplicate CHANGELOG version"


def test_every_dated_section_has_a_valid_date():
    import datetime as dt

    for s in release.parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")):
        if s.date:
            dt.date.fromisoformat(s.date)


def test_release_tag_matches_package_and_changelog():
    """Only active in the tag build: the pushed tag must agree with the code it points at."""
    tag = os.environ.get("RELEASE_TAG") or (
        os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
    )
    if not tag:
        import pytest

        pytest.skip("not a tag build")
    release.check_release_metadata(release.tag_to_semver(tag), ROOT)
