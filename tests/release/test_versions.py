import pytest
import release
from release import ReleaseError


@pytest.mark.parametrize(
    "semver,pep440",
    [
        ("0.4.0-alpha.1", "0.4.0a1"),
        ("0.4.0-alpha.12", "0.4.0a12"),
        ("0.5.0-beta.2", "0.5.0b2"),
        ("1.0.0-rc.1", "1.0.0rc1"),
        ("1.0.0", "1.0.0"),
        ("0.4.1", "0.4.1"),
        ("10.20.30", "10.20.30"),
    ],
)
def test_mapping_roundtrip(semver, pep440):
    assert release.semver_to_pep440(semver) == pep440
    assert release.pep440_to_semver(pep440) == semver
    assert release.tag_to_semver(release.semver_to_tag(semver)) == semver


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "v0.4.0",
        "0.4",
        "0.4.0.1",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "0.4.0-alpha",
        "0.4.0-alpha.0",
        "0.4.0-alpha.01",
        "0.4.0-dev.1",
        "0.4.0-ALPHA.1",
        "0.4.0+build",
        "0.4.0-alpha.1.2",
        " 0.4.0",
        "0.4.0\n",
        "latest",
    ],
)
def test_invalid_semver_is_rejected(bad):
    with pytest.raises(ReleaseError):
        release.validate_semver(bad)


@pytest.mark.parametrize("bad", ["0.4.0-alpha.1", "0.4", "0.4.0.dev1", "0.4.0a0", "0.4.0post1", "1.0.0c1", "v0.4.0"])
def test_invalid_pep440_is_rejected(bad):
    with pytest.raises(ReleaseError):
        release.pep440_to_semver(bad)


@pytest.mark.parametrize("bad", ["0.4.0", "V0.4.0", "release-0.4.0", "vv0.4.0", "v0.4", "v0.4.0-alpha"])
def test_invalid_tag_names_are_rejected(bad):
    with pytest.raises(ReleaseError):
        release.tag_to_semver(bad)


def test_prerelease_detection():
    assert release.is_prerelease("0.4.0-alpha.1")
    assert release.is_prerelease("1.0.0-rc.3")
    assert not release.is_prerelease("1.0.0")


def test_normalize_accepts_optional_v():
    assert release.normalize_version_arg("v0.4.0-alpha.1") == "0.4.0-alpha.1"
    assert release.normalize_version_arg("0.4.0-alpha.1") == "0.4.0-alpha.1"


def test_pep440_ordering_matches_release_trains():
    """alpha < beta < rc < final, also in the PEP 440 spelling pip uses."""
    packaging_version = pytest.importorskip("packaging.version")
    order = ["0.4.0-alpha.1", "0.4.0-alpha.2", "0.4.0-beta.1", "0.4.0-rc.1", "0.4.0", "0.4.1-alpha.1", "0.5.0"]
    parsed = [packaging_version.Version(release.semver_to_pep440(v)) for v in order]
    assert parsed == sorted(parsed)
    assert packaging_version.Version("0.4.0a1").is_prerelease
    assert not packaging_version.Version("0.4.0").is_prerelease
