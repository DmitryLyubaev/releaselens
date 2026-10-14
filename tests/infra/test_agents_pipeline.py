"""Manual trusted-source workload and credential-free image workflow contracts."""
import json
import re
import yaml


def load(repo, path):
    return yaml.safe_load((repo / path).read_text())


def test_manual_main_source_and_capability_guards(repo_root):
    p = load(repo_root, "pipelines/agents-demo.yml")
    assert p["trigger"] == "none" and p["pr"] == "none"
    assert "schedules" not in p
    job, = p["jobs"]
    assert job["timeoutInMinutes"] == 12
    assert job["pool"] == {"name": "$(P7_POOL)", "demands": ["P7Agent -equals ReleaseLens"]}
    first = job["steps"][0]
    assert 'refs/heads/main' in first["bash"]
    assert 'BUILD_SOURCEVERSION' in first["bash"] and 'P7_SOURCE_SHA' in first["bash"]
    checkout = next(s for s in job["steps"] if s.get("checkout"))
    assert checkout == {"checkout": "self", "clean": True, "persistCredentials": False}


def test_exact_docker_free_projects_and_results(repo_root):
    job, = load(repo_root, "pipelines/agents-demo.yml")["jobs"]
    steps = job["steps"]
    workload = next(s["bash"] for s in steps if s.get("displayName") == "Build and test Core and Web")
    assert "ReleaseLens.Core.Tests" in workload and "ReleaseLens.Web.Tests" in workload
    assert "dotnet test \"$project\"" in workload
    assert "--no-build" in workload and "--no-restore" in workload
    assert "docker" not in workload.lower()
    publisher = next(s for s in steps if s.get("task") == "PublishTestResults@2")
    assert publisher["condition"] == "always()"
    assert publisher["inputs"]["failTaskOnMissingResultsFile"] is True
    assert publisher["inputs"]["failTaskOnFailedTests"] is True
    negative = next(s for s in steps if "exit 17" in s.get("bash", ""))
    assert "controlledFailure" in negative["condition"] and "succeeded()" in negative["condition"]
    assert "container" not in job


def test_fresh_workspace_and_allowlisted_artifact(repo_root):
    job, = load(repo_root, "pipelines/agents-demo.yml")["jobs"]
    assert ".p7-claimed" in job["steps"][0]["bash"]
    assert "test ! -e" in job["steps"][0]["bash"]
    artifact = next(s for s in job["steps"] if s.get("task") == "PublishPipelineArtifact@1")
    assert artifact["inputs"]["artifact"] == "p7-results"
    assert artifact["inputs"]["targetPath"] == "$(Agent.TempDirectory)/p7-results"
    assert artifact["condition"] == "always()"


def test_image_workflow_never_uses_azure_and_only_main_publishes(repo_root):
    w = load(repo_root, ".github/workflows/agent-image.yml")
    assert set(w.get("on", w.get(True))) == {"pull_request", "workflow_dispatch"}
    assert w["permissions"] == {"contents": "read"}
    publish = w["jobs"]["publish"]
    assert publish["needs"] == "validate"
    assert "workflow_dispatch" in publish["if"] and "refs/heads/main" in publish["if"]
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    for job in w["jobs"].values():
        assert "environment" not in job
        assert "id-token" not in job.get("permissions", {})
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
    validate = json.dumps(w["jobs"]["validate"])
    assert "build/agents" in validate and "smoke.sh" in validate
    smoke = (repo_root / "build/agents/smoke.sh").read_text()
    assert "docker run" in smoke
    assert "ReleaseLens.Core.Tests" in smoke and "ReleaseLens.Web.Tests" in smoke
    assert "--push" not in validate


def test_image_manifest_matches_dockerfile(repo_root):
    manifest = json.loads((repo_root / "build/agents/versions.json").read_text())
    docker = (repo_root / "build/agents/Dockerfile").read_text()
    assert f"FROM {manifest['base']}" in docker
    assert manifest["platform"] == "linux/amd64"
    assert "USER 10001:10001" in docker
    assert "sha256sum" in docker and "agent_sha256" in docker


def test_ci_validates_agents_without_removing_existing_stacks(repo_root):
    ci = load(repo_root, ".github/workflows/ci.yml")
    job = ci["jobs"]["terraform-plan"]
    stacks = {s.get("working-directory") for s in job["steps"]}
    assert {"infra/bootstrap", "infra/terraform", "infra/search", "infra/gateway", "infra/functions", "infra/webapp", "infra/agents"} <= stacks
    assert "infra/agents/.terraform.lock.hcl" in json.dumps(job)


def test_runbook_has_private_setup_and_bounded_scoped_cleanup(repo_root):
    text = (repo_root / "docs/runbook-agents.md").read_text()
    for required in ("personal Microsoft account", "private project", "owner-only", "Agent Pools",
                     "60 minutes", "30 minutes", "45 minutes", "pat_revoked", "group_exists",
                     "retained_before", "retained_after", "no Key Vault purge", "public push"):
        assert required in text


def test_published_image_is_smoke_tested_before_push(repo_root):
    steps = load(repo_root, ".github/workflows/agent-image.yml")["jobs"]["publish"]["steps"]
    build = next(s for s in steps if 'docker/build-push-action@' in s.get('uses', ''))
    assert build['with']['push'] is False and build['with']['load'] is True
    smoke = next(i for i, s in enumerate(steps) if 'smoke.sh' in s.get('run', ''))
    push = next(i for i, s in enumerate(steps) if 'docker push' in s.get('run', ''))
    assert smoke < push
