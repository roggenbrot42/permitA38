"""Exercise scripts/release.py against real git repositories in a temp dir (never the project's own repo)."""

import subprocess

import pytest
import release

CHANGELOG = """# Changelog

## [Unreleased]

## [0.4.0-alpha.1] - 2026-11-05

### Added
- First alpha
"""


def sh(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit_all(cwd, msg="x"):
    sh("git", "add", "-A", cwd=cwd)
    sh("git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", msg, cwd=cwd)


@pytest.fixture
def repo(tmp_path):
    """A working clone on 'main' with a bare 'origin', containing a releasable tree for 0.4.0-alpha.1."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    sh("git", "init", "-q", "-b", "main", str(work), cwd=tmp_path)
    sh("git", "config", "user.name", "Tester", cwd=work)
    sh("git", "config", "user.email", "tester@example.invalid", cwd=work)
    sh("git", "config", "tag.gpgSign", "false", cwd=work)
    (work / "permita38").mkdir()
    (work / "permita38" / "__init__.py").write_text('__version__ = "0.4.0a1"\n')
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    sh("git", "remote", "add", "origin", str(origin), cwd=work)
    commit_all(work, "initial")
    sh("git", "push", "-q", "-u", "origin", "main", cwd=work)
    return work


def run_tag(repo, *extra, version="0.4.0-alpha.1"):
    return release.main(["--repo", str(repo), "tag", version, "--skip-checks", "--dry-run", *extra])


def test_dry_run_passes_and_creates_nothing(repo, capsys):
    assert run_tag(repo) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert sh("git", "tag", "-l", cwd=repo) == ""


def test_creates_annotated_tag_with_changelog_message(repo, monkeypatch):
    # real run, quality gates replaced by a stub so the test needs no make/pytest
    monkeypatch.setattr(release, "run_quality_gates", lambda root=None: None)
    assert release.main(["--repo", str(repo), "tag", "0.4.0-alpha.1"]) == 0
    assert sh("git", "cat-file", "-t", "refs/tags/v0.4.0-alpha.1", cwd=repo) == "tag"  # annotated, not lightweight
    msg = sh("git", "tag", "-l", "--format=%(contents)", "v0.4.0-alpha.1", cwd=repo)
    assert "First alpha" in msg and "2026-11-05" in msg
    # nothing was pushed
    assert sh("git", "ls-remote", "--tags", "origin", cwd=repo) == ""


def test_push_flag_pushes_only_the_tag(repo, monkeypatch):
    monkeypatch.setattr(release, "run_quality_gates", lambda root=None: None)
    sh("git", "branch", "other", cwd=repo)
    assert release.main(["--repo", str(repo), "tag", "0.4.0-alpha.1", "--push"]) == 0
    remote = sh("git", "ls-remote", "origin", cwd=repo)
    assert "refs/tags/v0.4.0-alpha.1" in remote and "refs/heads/other" not in remote


def test_refuses_dirty_tree(repo, capsys):
    (repo / "dirty.txt").write_text("x")
    assert run_tag(repo) == 2
    assert "not clean" in capsys.readouterr().err


def test_refuses_wrong_branch(repo, capsys):
    sh("git", "checkout", "-q", "-b", "feature", cwd=repo)
    assert run_tag(repo) == 2
    assert "current branch is 'feature'" in capsys.readouterr().err


def test_refuses_unpushed_commits(repo, capsys):
    (repo / "a.txt").write_text("a")
    commit_all(repo)
    assert run_tag(repo) == 2
    assert "not on origin/main" in capsys.readouterr().err


def test_refuses_when_behind_remote(repo, tmp_path, capsys):
    other = tmp_path / "other"
    sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
    (other / "b.txt").write_text("b")
    commit_all(other)
    sh("git", "push", "-q", "origin", "main", cwd=other)
    assert run_tag(repo) == 2
    assert "behind" in capsys.readouterr().err


def test_refuses_version_mismatch(repo, capsys):
    (repo / "permita38" / "__init__.py").write_text('__version__ = "0.3.0"\n')
    commit_all(repo)
    sh("git", "push", "-q", cwd=repo)
    assert run_tag(repo) == 2
    assert "needs '0.4.0a1'" in capsys.readouterr().err


def test_refuses_missing_changelog_section(repo, capsys):
    assert run_tag(repo, version="0.4.0-alpha.2") == 2  # package says a1, so the version check fires first
    assert "__version__" in capsys.readouterr().err
    (repo / "permita38" / "__init__.py").write_text('__version__ = "0.4.0a2"\n')
    commit_all(repo)
    sh("git", "push", "-q", cwd=repo)
    assert run_tag(repo, version="0.4.0-alpha.2") == 2
    assert "no section" in capsys.readouterr().err


def test_refuses_existing_tag_local_or_remote(repo, monkeypatch, capsys):
    monkeypatch.setattr(release, "run_quality_gates", lambda root=None: None)
    assert release.main(["--repo", str(repo), "tag", "0.4.0-alpha.1", "--push"]) == 0
    assert run_tag(repo) == 2
    assert "already exists" in capsys.readouterr().err
    sh("git", "tag", "-d", "v0.4.0-alpha.1", cwd=repo)  # only the remote still has it
    assert run_tag(repo) == 2
    assert "already exists" in capsys.readouterr().err


def test_skip_checks_requires_dry_run(repo, capsys):
    assert release.main(["--repo", str(repo), "tag", "0.4.0-alpha.1", "--skip-checks"]) == 2
    assert "only allowed together with --dry-run" in capsys.readouterr().err


def test_push_and_dry_run_are_exclusive(repo, capsys):
    assert run_tag(repo, "--push") == 2
    assert "--push cannot be combined" in capsys.readouterr().err


def test_check_rejects_lightweight_tags(repo, capsys):
    sh("git", "tag", "v0.4.0-alpha.1", cwd=repo)  # lightweight
    assert release.main(["--repo", str(repo), "check", "v0.4.0-alpha.1", "--require-annotated"]) == 2
    assert "not an annotated tag" in capsys.readouterr().err
    sh("git", "tag", "-d", "v0.4.0-alpha.1", cwd=repo)
    sh("git", "tag", "-a", "v0.4.0-alpha.1", "-m", "release", cwd=repo)
    assert release.main(["--repo", str(repo), "check", "v0.4.0-alpha.1", "--require-annotated"]) == 0


def test_notes_and_info_commands(repo, capsys, tmp_path, monkeypatch):
    assert release.main(["--repo", str(repo), "notes", "v0.4.0-alpha.1"]) == 0
    assert "First alpha" in capsys.readouterr().out
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert release.main(["info", "v0.4.0-alpha.1", "--github-output"]) == 0
    text = out.read_text()
    assert "version=0.4.0-alpha.1" in text and "pep440=0.4.0a1" in text and "prerelease=true" in text
    assert release.main(["info", "v1.0.0", "--github-output"]) == 0
    assert "prerelease=false" in out.read_text()


def test_invalid_input_returns_exit_code_2(capsys):
    assert release.main(["info", "0.4.0"]) == 2
    assert release.main(["pep440", "garbage"]) == 2
    capsys.readouterr()
