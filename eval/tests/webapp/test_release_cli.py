import json
import httpx
import pytest
from app.gateway.freeze import DirtyTreeError
from app.webapp import release_cli as cli
from .test_release import source, ci, SHA, REPO
from .test_release import receipt


def test_release_guards_run_before_reading_credentials_or_opening_http(capsys):
    class Secrets:
        def __getitem__(self, key): raise AssertionError("Credentials read before guard")
        def get(self, key, default=None): raise AssertionError("Credentials read before guard")
    def refuse(): raise DirtyTreeError("uncommitted changes")
    assert cli.main(["stage"], env=Secrets(), clean_guard=refuse) == 1
    assert capsys.readouterr().out == "status=refused\n"


def test_source_api_projects_only_public_release_metadata():
    raw = source() | {"actor": {"email": "private-marker@example.test"}, "runtime": "private-marker"}
    def handle(request):
        if request.url.path.endswith("/jobs"): return httpx.Response(200, json={"jobs": raw["jobs"]})
        return httpx.Response(200, json=raw)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = cli.GitHubMetadata(http, REPO).source(123)
    assert "private-marker" not in json.dumps(result)
    assert result["head_sha"] == SHA


def test_preflight_ci_requires_the_actual_build_job_to_pass():
    raw = ci() | {"id": 456, "run_attempt": 1}
    def handle(request):
        if request.url.path.endswith("/jobs"): return httpx.Response(200, json={"jobs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}]})
        return httpx.Response(200, json={"workflow_runs": [raw]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(cli.TransportRefused): cli.GitHubMetadata(http, REPO).ci(SHA)


@pytest.mark.parametrize("mode,fixture", [("seed", "true"), ("promote", "true"), ("unknown", "false")])
def test_invalid_mode_fixture_combination_refuses_without_azure(mode, fixture, capsys, tmp_path):
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch", "WEBAPP_MODE": mode, "WEBAPP_FIXTURE": fixture}
    assert cli.main(["preflight"], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path) == 1
    assert capsys.readouterr().out == "status=refused\n"


def test_invalid_promotion_receipt_is_rejected_before_azure_credential_acquisition(tmp_path, capsys):
    import hashlib, io, zipfile
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        for name in ("ReleaseLens.Web.dll", "ReleaseLens.Web.deps.json", "ReleaseLens.Web.runtimeconfig.json"):
            archive.writestr(name, "content")
    value = package.getvalue(); digest = hashlib.sha256(value).hexdigest()
    bundle = tmp_path / "artifacts/webapp-verified"; bundle.mkdir(parents=True)
    (bundle / "package.zip").write_bytes(value)
    (bundle / "receipt.json").write_text(json.dumps(receipt() | {"package_sha256": digest, "kind": "fixture"}))
    (bundle / "verified.json").write_text(json.dumps(dict(current_sha=SHA, repository=REPO, mode="promote", fixture=False,
                                                              execution_ci=ci(), ci=ci(), source=source(), package_sha256=digest)))
    env = dict(GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main", GITHUB_EVENT_NAME="workflow_dispatch", WEBAPP_MODE="promote", WEBAPP_FIXTURE="false",
               GITHUB_REPOSITORY=REPO, GITHUB_SHA=SHA, GITHUB_RUN_ID="900", GITHUB_RUN_ATTEMPT="1", AZURE_TENANT_ID="placeholder", AZURE_CLIENT_ID="placeholder")
    acquisitions = []
    def credential(*args): acquisitions.append(True); raise AssertionError("Credential should not be acquired")
    assert cli.main(["promote"], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path, credential_factory=credential) == 1
    assert acquisitions == []
    assert capsys.readouterr().out == "status=refused\n"
