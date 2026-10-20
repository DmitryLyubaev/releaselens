"""The actual job-authentication diagnostic must stay separate from paid delivery."""
import importlib
import json

import httpx
import pytest
import yaml

GUIDS = {"tenant": "00000000-0000-0000-0000-000000000001", "subscription": "00000000-0000-0000-0000-000000000002"}


@pytest.fixture
def probe(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    spec = importlib.util.find_spec("scripts.pipelines.identity_preflight")
    assert spec is not None, "credential-free preflight boundary is missing"
    return importlib.import_module("scripts.pipelines.identity_preflight")


@pytest.fixture
def delivery():
    """The seven plain settings and the run context, as the job passes them."""
    principal = {"issuer": "https://issuer.example", "subject": "subject",
                 "oidc_request_origin": "https://dev.azure.com",
                 "oidc_path_prefix": "/owned/project/_apis/distributedtask/hubs/build/"}
    principals = {role: principal | {"client_id": "client-" + role, "object_id": "object-" + role,
                                     "service_connection_id": "connection-" + role}
                  for role in ("test", "prod", "verify")}
    return {"BUILD_REASON": "Manual", "BUILD_SOURCEBRANCH": "refs/heads/main", "BUILD_BUILDID": "17",
            "P8_TENANT_ID": GUIDS["tenant"], "P8_SUBSCRIPTION_ID": GUIDS["subscription"],
            "P8_STORAGE_ACCOUNT": "owned", "P8_SEARCH_GROUP": "rg-p8-search",
            "P8_SEARCH_SERVICE": "owned-search", "P8_RELEASE_DIGEST": "b" * 64,
            "P8_PRINCIPALS_JSON": json.dumps(principals)}


def principals_of(env):
    return json.loads(env["P8_PRINCIPALS_JSON"])


def with_principals(env, principals):
    return env | {"P8_PRINCIPALS_JSON": json.dumps(principals)}


def test_preflight_reads_settings_not_packet(probe, delivery, monkeypatch):
    # The settings alone, with no packet or authority variable of any kind, authorise the run.
    assert not [key for key in delivery if key.startswith("P8_PREFLIGHT")]
    settings, run_id = probe.require_context(delivery)
    assert run_id == 17
    assert settings.principals["prod"]["client_id"] == "client-prod"
    assert settings.principals["test"]["tenant_id"] == GUIDS["tenant"]
    assert not hasattr(probe, "decode_authority") and not hasattr(probe, "verify_source")


def mutate(env, fault):
    principals = principals_of(env)
    if fault == "scheduled": return env | {"BUILD_REASON": "Schedule"}
    if fault == "pull-request": return env | {"BUILD_REASON": "PullRequest"}
    if fault == "other-branch": return env | {"BUILD_SOURCEBRANCH": "refs/heads/feature"}
    if fault == "zero-build": return env | {"BUILD_BUILDID": "0"}
    if fault == "non-numeric-build": return env | {"BUILD_BUILDID": "x"}
    if fault == "no-build": return {k: v for k, v in env.items() if k != "BUILD_BUILDID"}
    if fault == "no-settings": return {k: v for k, v in env.items() if k != "P8_RELEASE_DIGEST"}
    if fault == "bad-guid": return env | {"P8_TENANT_ID": "tenant"}
    if fault == "missing-role": del principals["prod"]
    if fault == "foreign-origin": principals["verify"]["oidc_request_origin"] = "https://example.invalid"
    if fault == "relative-prefix": principals["verify"]["oidc_path_prefix"] = "owned/project/"
    if fault == "shared-client": principals["prod"]["client_id"] = principals["test"]["client_id"]
    if fault == "shared-object": principals["verify"]["object_id"] = principals["test"]["object_id"]
    if fault == "shared-connection": principals["prod"]["service_connection_id"] = principals["verify"]["service_connection_id"]
    return with_principals(env, principals)


@pytest.mark.parametrize("fault", ["scheduled", "pull-request", "other-branch", "zero-build", "non-numeric-build",
                                   "no-build", "no-settings", "bad-guid", "missing-role", "foreign-origin",
                                   "relative-prefix", "shared-client", "shared-object", "shared-connection"])
def test_context_refuses_foreign_or_malformed_job_before_authentication(probe, delivery, fault):
    with pytest.raises(probe.DeliveryRefused): probe.require_context(mutate(delivery, fault))


def test_authentication_uses_same_boundary_then_exercises_actual_refresh(probe, delivery, monkeypatch):
    expected = principals_of(delivery)["test"]
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


def test_refreshed_assertion_with_wrong_subject_is_refused_without_its_value(probe, delivery, monkeypatch):
    expected = principals_of(delivery)["test"] | {"tenant_id": "tenant", "subscription_id": "sub"}
    account = {"id": "sub", "tenantId": "tenant", "state": "Enabled", "user": {"type": "servicePrincipal", "name": "client-test"}}
    access = {"tid": "tenant", "oid": "object-test", "appid": "client-test", "exp": 4102444800}
    good = {"iss": "https://issuer.example", "sub": "subject", "aud": "api://AzureADTokenExchange", "exp": 4102444800}
    monkeypatch.setattr(probe, "authenticate", lambda p: (account, {"federation": good, "access": access}, {
        "ARM_OIDC_REQUEST_URL": "https://dev.azure.com/owned/oidctoken", "ARM_OIDC_REQUEST_TOKEN": "PRIVATE_JOB_TOKEN"}))
    monkeypatch.setattr(probe, "jwt_claims", lambda token: good | {"sub": "PRIVATE_WRONG_SUBJECT"})
    handler = lambda request: httpx.Response(200, json={"oidcToken": "PRIVATE_REFRESHED_ASSERTION"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(probe.IdentityRefused) as caught: probe.check_identity(expected, client)
    assert caught.value.check == "federation-subject" and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("status,body", [(403, {"error": "PRIVATE_REMOTE_ERROR"}), (200, {}),
                                        (200, {"oidcToken": ""})])
def test_failed_refresh_is_fixed_code_and_never_success(probe, delivery, monkeypatch, status, body):
    monkeypatch.setattr(probe, "authenticate", lambda p: ({}, {"access": {}}, {
        "ARM_OIDC_REQUEST_URL": "https://dev.azure.com/owned/oidctoken",
        "ARM_OIDC_REQUEST_TOKEN": "PRIVATE_JOB_TOKEN"}))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))) as client:
        with pytest.raises(probe.DeliveryRefused) as caught:
            probe.check_identity(principals_of(delivery)["test"], client)
    assert "PRIVATE" not in str(caught.value)


def test_preflight_yaml_uses_free_hosted_job_without_live_dispatch(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    assert "identity-preflight" in source["parameters"][0]["values"]
    template = yaml.safe_load((repo_root / "pipelines/templates/search-delivery.yml").read_bytes())
    manual = template["stages"][1]["${{ if ne(variables['Build.Reason'], 'Schedule') }}"]
    stage = next(v["${{ if eq(parameters.mode, 'identity-preflight') }}"] for v in manual
                 if "${{ if eq(parameters.mode, 'identity-preflight') }}" in v)
    job = stage[0]["jobs"][0]
    assert job["pool"] == {"vmImage": "ubuntu-24.04"} and job["timeoutInMinutes"] == 10
    text = json.dumps(stage)
    assert "--guard-only" in text and "--receipt-dir" in text
    assert "P8_PREFLIGHT" not in text and "P8_AUTHORITY" not in text
    assert all(word not in text for word in ("terraform apply", "scripts.pipelines.run build", "ReleaseLens-P7"))
    tasks = [step for step in job["steps"] if step.get("template") == "identity-preflight-task.yml"]
    assert [step["parameters"]["role"] for step in tasks] == ["test", "prod", "verify"]
    assert any(step.get("task") == "PublishPipelineArtifact@1" and step.get("condition") == "always()"
               for step in job["steps"])
    guard = next(step for step in job["steps"] if "--guard-only" in step.get("bash", ""))
    required = {"BUILD_REASON", "BUILD_SOURCEBRANCH", "BUILD_BUILDID", "P8_TENANT_ID", "P8_SUBSCRIPTION_ID",
                "P8_STORAGE_ACCOUNT", "P8_SEARCH_GROUP", "P8_SEARCH_SERVICE", "P8_RELEASE_DIGEST", "P8_PRINCIPALS_JSON"}
    final = next(step for step in job["steps"] if "identity_preflight all --receipt-dir" in step.get("bash", ""))
    task = yaml.safe_load((repo_root / "pipelines/templates/identity-preflight-task.yml").read_bytes())
    for env in (guard["env"], final["env"], task["steps"][0]["env"]):
        assert required <= set(env) and not [k for k in env if k.startswith("P8_PREFLIGHT")]


def configure_main(monkeypatch, env):
    for key, value in env.items(): monkeypatch.setenv(key, value)


def test_main_guard_only_has_no_authentication_or_refresh(probe, delivery, monkeypatch, tmp_path):
    configure_main(monkeypatch, delivery)
    monkeypatch.setattr(probe, "check_identity", lambda *args: pytest.fail("guard must not authenticate"))
    assert probe.main(["all", "--guard-only", "--receipt-dir", str(tmp_path / "publish")]) == 0
    assert not (tmp_path / "publish").exists()


def test_main_guard_refuses_a_malformed_job_and_publishes_only_the_fixed_code(probe, delivery, monkeypatch, tmp_path, capsys):
    configure_main(monkeypatch, delivery | {"BUILD_SOURCEBRANCH": "refs/heads/feature", "P8_PRINCIPALS_JSON": "PRIVATE"})
    assert probe.main(["all", "--guard-only", "--receipt-dir", str(tmp_path)]) == 2
    assert json.loads((tmp_path / "all-failure.json").read_bytes()) == {
        "kind": "identity-preflight-failure", "role": "all", "code": "authority", "check": "unclassified"}
    assert "PRIVATE" not in (tmp_path / "all-failure.json").read_text() + capsys.readouterr().out


def write_receipts(probe, tmp_path, fault):
    for role in ("test", "prod", "verify"):
        if role == "prod" and fault == "missing": continue
        receipt = {"kind": "identity-preflight-result", "run_id": 17, "role": role,
                   "checks": dict(probe.CHECKS), "verified": True}
        if role == "prod" and fault == "other-run": receipt["run_id"] = 16
        if role == "prod" and fault == "failed": receipt["verified"] = False
        if role == "prod" and fault == "wrong-role": receipt["role"] = "test"
        if role == "prod" and fault == "partial-checks": receipt["checks"] = dict(probe.CHECKS) | {"refresh_request_verified": False}
        (tmp_path / (role + "-identity.json")).write_text(json.dumps(receipt))
        if role == "prod" and fault == "corrupt": (tmp_path / (role + "-identity.json")).write_text("{")


@pytest.mark.parametrize("fault", ["missing", "other-run", "failed", "wrong-role", "partial-checks", "corrupt", None])
def test_all_requires_three_role_receipts(probe, delivery, monkeypatch, tmp_path, fault):
    configure_main(monkeypatch, delivery)
    write_receipts(probe, tmp_path, fault)
    assert probe.main(["all", "--receipt-dir", str(tmp_path)]) == (0 if fault is None else 2)


def test_all_does_not_authenticate_or_call_the_refresh_endpoint(probe, delivery, monkeypatch, tmp_path):
    configure_main(monkeypatch, delivery)
    write_receipts(probe, tmp_path, None)
    monkeypatch.setattr(probe, "check_identity", lambda *args: pytest.fail("all only reads receipts"))
    assert probe.main(["all", "--receipt-dir", str(tmp_path)]) == 0


def test_main_failure_never_publishes_exception_or_credentials(probe, delivery, monkeypatch, tmp_path, capsys):
    configure_main(monkeypatch, delivery)
    monkeypatch.setenv("idToken", "PRIVATE_ASSERTION")
    monkeypatch.setenv("SYSTEM_ACCESSTOKEN", "PRIVATE_JOB_TOKEN")
    def fail(*args): raise RuntimeError("PRIVATE_REMOTE_ERROR")
    monkeypatch.setattr(probe, "check_identity", fail)
    assert probe.main(["test", "--receipt-dir", str(tmp_path)]) == 2
    raw = (tmp_path / "test-failure.json").read_text()
    assert json.loads(raw) == {"kind": "identity-preflight-failure", "role": "test",
                               "code": "internal", "check": "unclassified"}
    assert "PRIVATE" not in raw + capsys.readouterr().out


@pytest.mark.parametrize("check", ["refresh-endpoint", "federation-subject", "secret-present"])
def test_main_failure_keeps_the_fixed_identity_check(probe, delivery, monkeypatch, tmp_path, check):
    configure_main(monkeypatch, delivery)
    def fail(*args): raise probe.IdentityRefused(check)
    monkeypatch.setattr(probe, "check_identity", fail)
    assert probe.main(["prod", "--receipt-dir", str(tmp_path)]) == 2
    assert json.loads((tmp_path / "prod-failure.json").read_bytes()) == {
        "kind": "identity-preflight-failure", "role": "prod", "code": "identity", "check": check}


@pytest.mark.parametrize("role", ["test", "prod", "verify"])
def test_each_role_checks_its_own_principal_even_though_the_run_is_manual(probe, delivery, monkeypatch, tmp_path, role):
    configure_main(monkeypatch, delivery)
    seen = []
    monkeypatch.setattr(probe, "check_identity", lambda principal, client: seen.append(principal) or dict(probe.CHECKS))
    assert probe.main([role, "--receipt-dir", str(tmp_path)]) == 0
    assert [p["client_id"] for p in seen] == ["client-" + role]
    assert seen[0]["tenant_id"] == GUIDS["tenant"] and seen[0]["subscription_id"] == GUIDS["subscription"]


def test_main_success_keeps_only_bound_safe_proof(probe, delivery, monkeypatch, tmp_path):
    configure_main(monkeypatch, delivery)
    monkeypatch.setattr(probe, "check_identity", lambda *args: dict(probe.CHECKS))
    assert probe.main(["test", "--receipt-dir", str(tmp_path)]) == 0
    receipt = json.loads((tmp_path / "test-identity.json").read_bytes())
    assert set(receipt) == {"kind", "run_id", "role", "at", "checks", "verified"}
    assert receipt["role"] == "test" and receipt["run_id"] == 17 and receipt["verified"] is True
    assert receipt["checks"] == probe.CHECKS


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
