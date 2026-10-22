"""Static sanity checks for .github/workflows (no network, no actionlint needed)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
USES_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as boolean True
    return data


def ids():
    return [p.name for p in WORKFLOWS]


def test_workflows_exist():
    names = {p.name for p in WORKFLOWS}
    assert {"ci.yaml", "docker.yaml", "release.yaml"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=ids())
def test_valid_yaml_with_triggers_and_jobs(path):
    wf = load(path)
    assert wf.get("name")
    assert wf["on"], "no triggers"
    assert wf["jobs"]
    for name, job in wf["jobs"].items():
        assert "runs-on" in job or "uses" in job, name
        assert "steps" in job or "uses" in job, name


@pytest.mark.parametrize("path", WORKFLOWS, ids=ids())
def test_permissions_are_declared_and_least_privilege(path):
    wf = load(path)
    top = wf.get("permissions")
    for name, job in wf["jobs"].items():
        assert top is not None or "permissions" in job, f"{path.name}:{name} relies on default token permissions"
    assert top != "write-all" and top != {"contents": "write"}, "top-level permissions must be read-only or empty"
    for job in wf["jobs"].values():
        assert job.get("permissions") != "write-all"


@pytest.mark.parametrize("path", WORKFLOWS, ids=ids())
def test_no_dangerous_triggers(path):
    triggers = load(path)["on"]
    triggers = (
        triggers
        if isinstance(triggers, dict)
        else {t: None for t in ([triggers] if isinstance(triggers, str) else triggers)}
    )
    assert "pull_request_target" not in triggers
    assert "workflow_run" not in triggers


@pytest.mark.parametrize("path", WORKFLOWS, ids=ids())
def test_actions_are_not_floating(path):
    for ref in USES_RE.findall(path.read_text(encoding="utf-8")):
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        assert "@" in ref, f"{path.name}: {ref} has no version"
        _, version = ref.split("@", 1)
        assert version not in {"main", "master", "latest", "HEAD"}, f"{path.name}: {ref} is a floating reference"


@pytest.mark.parametrize("path", [p for p in WORKFLOWS if p.name != "ci.yaml"], ids=lambda p: p.name)
def test_release_related_actions_are_pinned_by_sha(path):
    """ci.yaml still uses major tags (Dependabot keeps them current); everything we added is SHA-pinned."""
    for ref in USES_RE.findall(path.read_text(encoding="utf-8")):
        if ref.startswith("./"):
            continue
        assert SHA_RE.match(ref.split("@", 1)[1]), f"{path.name}: {ref} is not pinned to a full commit SHA"


def test_no_expression_injection_in_run_steps():
    """Untrusted event data must not be interpolated into shell scripts."""
    bad = re.compile(
        r"\$\{\{\s*(github\.event\.(issue|pull_request|comment|head_commit|review)[^}]*|github\.head_ref|inputs\.[a-z_]+)\s*\}\}"
    )
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run", "")
                assert not bad.search(run), f"{path.name}:{name}: untrusted expression inside a run script"


def test_release_workflow_shape():
    wf = load(WORKFLOWS[[p.name for p in WORKFLOWS].index("release.yaml")])
    assert wf["on"]["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in wf["on"]
    jobs = wf["jobs"]
    assert {"validate", "test", "dist", "bundle", "docker", "publish"} <= set(jobs)
    publish = jobs["publish"]
    assert publish["if"] == "github.event_name == 'push'", "dry runs must never publish"
    assert publish.get("environment") == "release"
    assert set(publish["needs"]) >= {"validate", "dist", "bundle", "docker"}
    assert publish["permissions"]["attestations"] == "write" and publish["permissions"]["id-token"] == "write"
    text = (ROOT / ".github/workflows/release.yaml").read_text(encoding="utf-8")
    assert "--draft" in text and "--prerelease" in text and "--verify-tag" in text
    assert "attest-build-provenance" in text and "cyclonedx_py" in text and "SHA256SUMS" in text
    assert "--require-annotated" in text
    platforms = [m["platform"] for m in jobs["bundle"]["strategy"]["matrix"]["include"]]
    assert platforms == ["linux-x86_64", "windows-x86_64"]


def test_docker_workflow_does_not_publish():
    text = (ROOT / ".github/workflows/docker.yaml").read_text(encoding="utf-8")
    assert "push: false" in text and "packages: write" not in text


def test_only_the_release_job_can_write_contents():
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            perms = job.get("permissions")
            if isinstance(perms, dict) and perms.get("contents") == "write":
                assert (path.name, name) == ("release.yaml", "publish")
