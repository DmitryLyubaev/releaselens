"""The actual job-authentication diagnostic must stay separate from paid delivery."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import json

import httpx
import pytest
import yaml


@pytest.fixture
def probe(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    spec = importlib.util.find_spec("scripts.pipelines.identity_preflight")
    assert spec is not None, "credential-free preflight boundary is missing"
    return importlib.import_module("scripts.pipelines.identity_preflight")


@pytest.fixture
def packet():
    now = datetime.now(timezone.utc)
    principal = {"subscription_id": "subscription", "tenant_id": "tenant", "client_id": "client",
                 "object_id": "object", "issuer": "https://issuer.example", "subject": "subject",
                 "service_connection_id": "connection", "oidc_request_origin": "https://dev.azure.com",
                 "oidc_path_prefix": "/owned/project/_apis/distributedtask/hubs/build/"}
    authority = {"kind": "identity-preflight", "session": "p8-probe", "source_sha": "a" * 40,
                 "tree_sha": "b" * 40, "pipeline_id": 2, "approval_ref": "actual-owner-approval",
                 "issued_at": now.isoformat(), "expires_at": (now + timedelta(minutes=25)).isoformat(),
                 "principals": {role: principal | {"client_id": "client-" + role,
                              "object_id": "object-" + role, "service_connection_id": "connection-" + role}
                                for role in ("test", "prod", "verify")}}
    env = {"BUILD_REASON": "Manual", "BUILD_SOURCEBRANCH": "refs/heads/main",
           "BUILD_SOURCEVERSION": "a" * 40, "P8_PREFLIGHT_SOURCE_SHA": "a" * 40,
           "SYSTEM_DEFINITIONID": "2", "SYSTEM_JOBATTEMPT": "1", "SYSTEM_STAGEATTEMPT": "1",
           "BUILD_BUILDID": "17"}
    env["P8_PREFLIGHT_SHA256"] = sha256(json.dumps(authority, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return authority, env, now


@pytest.mark.parametrize("fault", [None, "foreign-source", "other-pipeline", "retry", "scheduled",
                                   "expired", "no-approval", "paid-authority", "missing-role", "changed-packet"])
def test_context_refuses_unapproved_or_foreign_job_before_authentication(probe, packet, fault):
    authority, env, now = packet
    if fault == "foreign-source": env["BUILD_SOURCEVERSION"] = "c" * 40
    if fault == "other-pipeline": env["SYSTEM_DEFINITIONID"] = "1"
    if fault == "retry": env["SYSTEM_JOBATTEMPT"] = "2"
    if fault == "scheduled": env["BUILD_REASON"] = "Schedule"
    if fault == "expired": now += timedelta(minutes=30)
    if fault == "no-approval": authority["approval_ref"] = ""
    if fault == "paid-authority": authority["kind"] = "live-delivery"
    if fault == "missing-role": del authority["principals"]["prod"]
    if fault == "changed-packet": authority["session"] = "p8-changed"
    if fault:
        with pytest.raises(probe.DeliveryRefused): probe.require_context(authority, env, now)
    else:
        assert probe.require_context(authority, env, now) == 17


def test_authentication_uses_same_boundary_then_exercises_actual_refresh(probe, packet, monkeypatch):
    authority, _, _ = packet
    expected = authority["principals"]["test"]
    uri = "https://dev.azure.com/owned/project/_apis/distributedtask/hubs/build/plans/plan/jobs/job/oidctoken"
    calls = []
    monkeypatch.setattr(probe, "authenticate", lambda p: calls.append("same-runtime-authentication") or (
        {"id": "subscription"}, {"access": {"not": "serialized"}},
        {"ARM_OIDC_REQUEST_URL": uri, "ARM_OIDC_REQUEST_TOKEN": "PRIVATE_JOB_TOKEN"}))
    monkeypatch.setattr(probe, "jwt_claims", lambda token: calls.append("decode-refreshed-assertion") or {"synthetic": True})
    monkeypatch.setattr(probe, "verify_principal", lambda account, claims, principal: calls.append("reverify-refreshed-identity"))
    def server(request):
        assert request.method == "POST" and str(request.url).startswith(uri + "?")
        assert request.url.params["serviceConnectionId"] == "connection-test"
        assert request.headers["authorization"] == "Bearer PRIVATE_JOB_TOKEN"
        calls.append("refresh-request")
        return httpx.Response(200, json={"oidcToken": "PRIVATE_REFRESHED_ASSERTION"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        result = probe.check_identity(expected, client)
    assert calls == ["same-runtime-authentication", "refresh-request", "decode-refreshed-assertion", "reverify-refreshed-identity"]
    assert result == {"principal_verified": True, "refresh_environment_verified": True,
                      "refresh_request_verified": True, "refreshed_principal_verified": True}
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("status,body", [(403, {"error": "PRIVATE_REMOTE_ERROR"}), (200, {}),
                                        (200, {"oidcToken": ""})])
def test_failed_refresh_is_fixed_code_and_never_success(probe, packet, monkeypatch, status, body):
    monkeypatch.setattr(probe, "authenticate", lambda p: ({}, {"access": {}}, {
        "ARM_OIDC_REQUEST_URL": "https://dev.azure.com/owned/oidctoken",
        "ARM_OIDC_REQUEST_TOKEN": "PRIVATE_JOB_TOKEN"}))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))) as client:
        with pytest.raises(probe.DeliveryRefused) as caught:
            probe.check_identity(packet[0]["principals"]["test"], client)
    assert "PRIVATE" not in str(caught.value)


def test_preflight_yaml_uses_free_hosted_job_without_live_dispatch(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    assert "identity-preflight" in source["parameters"][0]["values"]
    template = yaml.safe_load((repo_root / "pipelines/templates/search-delivery.yml").read_bytes())
    manual = template["stages"][1]["${{ if ne(variables['Build.Reason'], 'Schedule') }}"]
    preflight = next(v["${{ if eq(parameters.mode, 'identity-preflight') }}"] for v in manual
                     if "${{ if eq(parameters.mode, 'identity-preflight') }}" in v)
    job = preflight[0]["jobs"][0]
    assert job["pool"] == {"vmImage": "ubuntu-24.04"} and job["timeoutInMinutes"] == 20
    text = json.dumps(preflight)
    assert "--guard-only" in text and "--receipt-dir" in text
    assert all(word not in text for word in ("terraform apply", "scripts.pipelines.run build", "ReleaseLens-P7", "P8_AUTHORITY_JSON"))
    tasks = [step for step in job["steps"] if step.get("template") == "identity-preflight-task.yml"]
    assert [step["parameters"]["role"] for step in tasks] == ["test", "prod", "verify"]
    assert any(step.get("task") == "PublishPipelineArtifact@1" and step.get("condition") == "always()"
               for step in job["steps"])
    setup = next(step["bash"] for step in job["steps"] if "--guard-only" in step.get("bash", ""))
    assert setup.index('test "$BUILD_SOURCEBRANCH" = refs/heads/main') < setup.index("python -m venv")
    assert setup.index('test "$BUILD_SOURCEVERSION" = "$P8_PREFLIGHT_SOURCE_SHA"') < setup.index("python -m venv")


def configure_main(probe, packet, monkeypatch):
    authority, env, _ = packet
    for key, value in env.items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv("P8_PREFLIGHT_JSON", json.dumps(authority))
    monkeypatch.setattr(probe, "verify_source", lambda a: None)


def test_main_guard_only_has_no_authentication_or_refresh(probe, packet, monkeypatch, tmp_path):
    configure_main(probe, packet, monkeypatch)
    monkeypatch.setattr(probe, "check_identity", lambda *args: pytest.fail("guard must not authenticate"))
    assert probe.main(["all", "--guard-only", "--receipt-dir", str(tmp_path / "publish")]) == 0
    assert not (tmp_path / "publish").exists()


@pytest.mark.parametrize("fault", ["missing", "wrong-source", "failed", None])
def test_all_requires_three_actual_matching_receipts(probe, packet, monkeypatch, tmp_path, fault):
    configure_main(probe, packet, monkeypatch)
    authority, env, _ = packet
    for role in ("test", "prod", "verify"):
        if role == "prod" and fault == "missing": continue
        receipt = {"kind": "identity-preflight-result", "session": authority["session"],
                   "source_sha": authority["source_sha"], "tree_sha": authority["tree_sha"],
                   "run_id": 17, "authority_sha256": env["P8_PREFLIGHT_SHA256"],
                   "role": role, "checks": dict(probe.CHECKS), "verified": True}
        if role == "prod" and fault == "wrong-source": receipt["source_sha"] = "c" * 40
        if role == "prod" and fault == "failed": receipt["verified"] = False
        (tmp_path / (role + "-identity.json")).write_text(json.dumps(receipt))
    assert probe.main(["all", "--receipt-dir", str(tmp_path)]) == (0 if fault is None else 2)


def test_main_failure_never_publishes_exception_or_credentials(probe, packet, monkeypatch, tmp_path, capsys):
    configure_main(probe, packet, monkeypatch)
    monkeypatch.setenv("idToken", "PRIVATE_ASSERTION")
    monkeypatch.setenv("SYSTEM_ACCESSTOKEN", "PRIVATE_JOB_TOKEN")
    def fail(*args): raise RuntimeError("PRIVATE_REMOTE_ERROR")
    monkeypatch.setattr(probe, "check_identity", fail)
    assert probe.main(["test", "--receipt-dir", str(tmp_path)]) == 2
    raw = (tmp_path / "test-failure.json").read_text()
    assert json.loads(raw) == {"kind": "identity-preflight-failure", "role": "test",
                               "code": "internal", "check": "unclassified"}
    assert "PRIVATE" not in raw + capsys.readouterr().out


def test_main_success_keeps_only_bound_safe_proof(probe, packet, monkeypatch, tmp_path):
    configure_main(probe, packet, monkeypatch)
    monkeypatch.setattr(probe, "check_identity", lambda *args: dict(probe.CHECKS))
    assert probe.main(["test", "--receipt-dir", str(tmp_path)]) == 0
    receipt = json.loads((tmp_path / "test-identity.json").read_bytes())
    assert receipt["role"] == "test" and receipt["run_id"] == 17 and receipt["verified"] is True
    assert receipt["source_sha"] == "a" * 40 and receipt["checks"] == probe.CHECKS


def test_fixed_hosted_tasks_preserve_connections_and_secret_boundaries(repo_root):
    task = yaml.safe_load((repo_root / "pipelines/templates/identity-preflight-task.yml").read_bytes())
    assert task["parameters"][0]["values"] == ["test", "prod", "verify"]
    azure = task["steps"][0]
    assert azure["task"] == "AzureCLI@2" and azure["inputs"]["addSpnToEnvironment"] is True
    assert azure["inputs"]["visibleAzLogin"] is False and "P8IdentityPreflightReady" in azure["condition"]
    assert {azure["inputs"]["${{ if eq(parameters.role, '" + role + "') }}"]["azureSubscription"]
            for role in ("test", "prod", "verify")} == {"ReleaseLens-P8-Test", "ReleaseLens-P8-Prod", "ReleaseLens-P8-Verify"}
    assert azure["env"]["SYSTEM_ACCESSTOKEN"] == "$(System.AccessToken)"
    assert azure["env"]["SYSTEM_OIDCREQUESTURI"] == "$(System.OidcRequestUri)"
