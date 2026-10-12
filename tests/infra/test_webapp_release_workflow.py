import copy
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_workflows as cw

PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def workflow():
    return {"on": {"workflow_dispatch": {}}, "permissions": {}, "concurrency": dict(cw.CONCURRENCY), "jobs": {
        "preflight": {"if": cw.MAIN_GUARD, "runs-on": "ubuntu-latest", "permissions": {"contents": "read", "actions": "read"}, "steps": [{"uses": PIN}]},
        "release": {"if": cw.MAIN_GUARD, "runs-on": "ubuntu-latest", "needs": "preflight", "environment": "azure", "permissions": {"id-token": "write", "contents": "read"}, "steps": [{"uses": PIN}]}}}


def test_exact_webapp_authority_shape_is_allowed():
    assert cw._check_workflow("webapp-release.yml", workflow()) == []


@pytest.mark.parametrize("kind", ["push", "pr", "guard", "preflight_permissions", "release_permissions", "pin", "concurrency", "preflight_azure", "extra_job"])
def test_webapp_workflow_cannot_widen_trigger_permissions_or_authority(kind):
    value = workflow()
    if kind == "push": value["on"]["push"] = {}
    elif kind == "pr": value["on"]["pull_request"] = {}
    elif kind == "guard": del value["jobs"]["release"]["if"]
    elif kind == "preflight_permissions": value["jobs"]["preflight"]["permissions"]["id-token"] = "write"
    elif kind == "release_permissions": value["jobs"]["release"]["permissions"]["actions"] = "read"
    elif kind == "pin": value["jobs"]["release"]["steps"][0]["uses"] = "actions/checkout@v7"
    elif kind == "concurrency": value["concurrency"]["cancel-in-progress"] = True
    elif kind == "preflight_azure": value["jobs"]["preflight"]["environment"] = "azure"
    else: value["jobs"]["extra"] = {"runs-on": "ubuntu-latest", "permissions": {}}
    assert cw._check_workflow("webapp-release.yml", value)


def test_repository_release_uses_same_run_verified_bundle_and_locked_install(repo_root):
    value = yaml.safe_load((repo_root / ".github/workflows/webapp-release.yml").read_text())
    jobs = value["jobs"]
    release = jobs["release"]
    assert release["needs"] == "preflight"
    install = next(step["run"] for step in release["steps"] if "requirements-gateway-check.txt" in step.get("run", ""))
    assert "--require-hashes --only-binary=:all: --no-deps" in install
    download = next(step for step in release["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["name"] == "webapp-verified-${{ github.run_id }}-${{ github.run_attempt }}"
    assert "github-token" not in download["with"] and "run-id" not in download["with"]
    assert not any("terraform apply" in step.get("run", "") or "terraform destroy" in step.get("run", "") for step in release["steps"])
