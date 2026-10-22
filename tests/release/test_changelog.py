import pytest
import release
from release import ReleaseError

SAMPLE = """# Changelog

Intro text.

## [Unreleased]

### Added
- Something new

## [0.4.0-alpha.1] - 2026-11-05

### Added
- First alpha

### Fixed
- A bug

## [0.3.0]

### Changed
- Undated section

## [0.2.0] - 2026-10-01
- Old

[Unreleased]: https://example.invalid/compare/v0.4.0-alpha.1...HEAD
[0.4.0-alpha.1]: https://example.invalid/releases/tag/v0.4.0-alpha.1
"""


def test_parse_sections():
    secs = release.parse_changelog(SAMPLE)
    assert [s.version for s in secs] == ["Unreleased", "0.4.0-alpha.1", "0.3.0", "0.2.0"]
    assert secs[1].date == "2026-11-05"
    assert secs[2].date is None
    assert "First alpha" in secs[1].body and "A bug" in secs[1].body
    assert "Old" in secs[3].body


def test_link_definitions_are_not_part_of_the_last_section():
    last = release.parse_changelog(SAMPLE)[-1]
    assert "example.invalid" not in last.body


def test_release_notes_exclude_neighbouring_sections():
    body = release.require_release_section(SAMPLE, "0.4.0-alpha.1").body
    assert body.startswith("### Added") and "Undated" not in body and "Something new" not in body


def test_missing_section():
    with pytest.raises(ReleaseError, match="no section"):
        release.require_release_section(SAMPLE, "0.9.0")


def test_undated_section_is_rejected():
    with pytest.raises(ReleaseError, match="no release date"):
        release.require_release_section(SAMPLE, "0.3.0")


def test_invalid_date_is_rejected():
    text = "## [1.0.0] - 2026-13-45\n- x\n"
    with pytest.raises(ReleaseError, match="invalid date"):
        release.require_release_section(text, "1.0.0")


def test_empty_section_is_rejected():
    text = "## [1.0.0] - 2026-01-01\n\n## [0.9.0] - 2025-01-01\n- x\n"
    with pytest.raises(ReleaseError, match="empty"):
        release.require_release_section(text, "1.0.0")


def test_unreleased_is_not_a_release():
    with pytest.raises(ReleaseError):
        release.require_release_section(SAMPLE, "Unreleased")


def test_tag_message_contains_changelog():
    sec = release.require_release_section(SAMPLE, "0.4.0-alpha.1")
    msg = release.tag_message("0.4.0-alpha.1", sec)
    assert msg.startswith("permitA38 v0.4.0-alpha.1 (2026-11-05)")
    assert "First alpha" in msg
