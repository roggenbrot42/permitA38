from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_issue_forms_are_valid():
    forms = sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    names = {f.name for f in forms}
    assert {"bug_report.yml", "feature_request.yml", "alpha_feedback.yml", "tax_rule_change.yml", "config.yml"} <= names
    for f in forms:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if f.name == "config.yml":
            assert data["blank_issues_enabled"] is False
            assert any("security/advisories" in c["url"] for c in data["contact_links"])
        else:
            assert data["name"] and data["body"]
            ids = [b["id"] for b in data["body"] if "id" in b]
            assert len(ids) == len(set(ids)), f"duplicate ids in {f.name}"


def test_community_files_exist():
    for name in (
        "SUPPORT.md",
        "CODE_OF_CONDUCT.md",
        "RELEASING.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "THIRD_PARTY_NOTICES.md",
        ".github/CODEOWNERS",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/dependabot.yml",
    ):
        assert (ROOT / name).is_file(), name
    assert "@roggenbrot42" in (ROOT / ".github/CODEOWNERS").read_text()
    pr = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    assert "signed off" in pr and "AI assistance" in pr and "Tax or legal rules" in pr


def test_dependabot_covers_ecosystems():
    data = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {u["package-ecosystem"] for u in data["updates"]} == {"pip", "github-actions", "docker"}


def test_roadmap_has_maintainer_todo_and_alpha1_facts():
    text = (ROOT / "docs/roadmap.md").read_text(encoding="utf-8")
    assert "MAINTAINER TODO: Review and finalise the release assignment of each roadmap item" in text
    assert text.count("(confirm intended meaning") >= 2  # GoBD and GDPR items
    assert "TOTP" in text.split("## v0.4.0-alpha.2")[0]  # TOTP is done in alpha.1, not planned later
