"""An early authentication refusal must leave a useful, token-free original."""
import importlib
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return tuple(importlib.import_module("scripts.pipelines." + name)
                 for name in ("identity", "run"))


@pytest.fixture
def principal():
    expected = {"subscription_id": "sub", "tenant_id": "tenant", "client_id": "client",
                "object_id": "object", "issuer": "https://issuer.example", "subject": "subject"}
    account = {"id": "sub", "tenantId": "tenant", "state": "Enabled",
               "user": {"type": "servicePrincipal", "name": "client"}}
    claims = {"federation": {"iss": expected["issuer"], "sub": "subject",
                            "aud": "api://AzureADTokenExchange", "exp": time.time() + 300},
              "access": {"tid": "tenant", "oid": "object", "appid": "client",
                         "exp": time.time() + 300}}
    return account, claims, expected


def test_principal_refusal_identifies_check_without_claim_values(modules, principal):
    identity, _ = modules
    account, claims, expected = principal
    claims["access"]["oid"] = "PRIVATE_WRONG_OBJECT"
    with pytest.raises(identity.DeliveryRefused) as caught:
        identity.verify_principal(account, claims, expected)
    assert caught.value.code == "identity"
    assert caught.value.check == "access-object"
    assert "PRIVATE_WRONG_OBJECT" not in str(caught.value)


def test_refresh_uri_refusal_identifies_check_without_job_token(modules, principal):
    identity, _ = modules
    expected = principal[2] | {"service_connection_id": "connection",
                              "oidc_request_origin": "https://dev.azure.com",
                              "oidc_path_prefix": "/owned/project/_apis/distributedtask/hubs/build/"}
    inherited = {"SYSTEM_ACCESSTOKEN": "PRIVATE_JOB_TOKEN",
                 "SYSTEM_OIDCREQUESTURI": "https://dev.azure.com/other/oidctoken",
                 "AZURESUBSCRIPTION_SERVICE_CONNECTION_ID": "connection"}
    with pytest.raises(identity.DeliveryRefused) as caught:
        identity.terraform_environment(expected, "PRIVATE_ASSERTION", inherited)
    assert caught.value.check == "refresh-endpoint"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("kind", ["identity", "unexpected"])
def test_main_publishes_safe_failure_even_before_runtime_assignment(modules, monkeypatch, tmp_path, capsys, kind):
    identity, run = modules
    monkeypatch.setattr(run, "context", lambda *args: ({"source_sha": "a" * 40}, {}))
    def fail(*args):
        if kind == "identity":
            raise identity.IdentityRefused("access-object")
        raise RuntimeError("PRIVATE_EXCEPTION_TOKEN")
    monkeypatch.setattr(run, "Runtime", fail)
    monkeypatch.setenv("idToken", "PRIVATE_ASSERTION")
    monkeypatch.setenv("SYSTEM_ACCESSTOKEN", "PRIVATE_JOB_TOKEN")
    public = tmp_path / "p8-publish"
    assert run.main(["build", "--authority", str(tmp_path / "authority.json"),
                     "--receipt-dir", str(public)]) == 2
    artifact = json.loads((public / "failure.json").read_bytes())
    assert artifact == {"kind": "stage-failure", "stage": "build",
                        "code": "identity" if kind == "identity" else "internal",
                        "check": "access-object" if kind == "identity" else "unclassified"}
    assert "PRIVATE" not in (public / "failure.json").read_text() + capsys.readouterr().out
    assert sorted(p.name for p in public.iterdir()) == ["failure.json"]


def test_guard_only_does_not_authenticate_or_publish_failure(modules, monkeypatch, tmp_path):
    _, run = modules
    monkeypatch.setattr(run, "context", lambda *args: ({}, {}))
    def unexpected(*args):
        raise AssertionError("guard must not authenticate")
    monkeypatch.setattr(run, "Runtime", unexpected)
    public = tmp_path / "p8-publish"
    assert run.main(["build", "--authority", str(tmp_path / "authority.json"),
                     "--receipt-dir", str(public), "--guard-only"]) == 0
    assert not public.exists()


def test_upload_refusal_publishes_fixed_check_not_remote_text(modules, monkeypatch, tmp_path, capsys):
    from app.delivery_search import client
    _, run = modules
    refusal = getattr(client, 'UploadRefused', None)
    assert refusal is not None, 'upload failure diagnostics are missing'
    monkeypatch.setattr(run, 'context', lambda *args: ({}, {}))
    def fail(*args):
        raise refusal('upload-deadline')
    monkeypatch.setattr(run, 'Runtime', fail)
    public = tmp_path / 'publish'
    assert run.main(['test', '--authority', str(tmp_path / 'authority.json'),
        '--receipt-dir', str(public)]) == 2
    assert json.loads((public / 'failure.json').read_bytes()) == {
        'kind': 'stage-failure', 'stage': 'test', 'code': 'upload', 'check': 'upload-deadline'}
    assert str(refusal('PRIVATE_RESPONSE_TOKEN')) == 'upload'
    assert refusal('PRIVATE_RESPONSE_TOKEN').check == 'upload-unclassified'
    assert 'PRIVATE' not in (public / 'failure.json').read_text() + capsys.readouterr().out


@pytest.mark.parametrize("args,check", [
    (["account", "show", "--output", "json"], "cli-account"),
    (["account", "get-access-token", "--output", "json"], "cli-access-token"),
])
@pytest.mark.parametrize("failure", ["exit", "invalid-json", "timeout"])
def test_cli_failure_has_safe_operation_check(modules, monkeypatch, args, check, failure):
    identity, run = modules
    def cli(*a, **kw):
        if failure == "timeout":
            raise run.subprocess.TimeoutExpired("PRIVATE_COMMAND", 30, output="PRIVATE_TOKEN")
        return SimpleNamespace(returncode=1 if failure == "exit" else 0,
                               stdout="PRIVATE_TOKEN", stderr="PRIVATE_CLOUD_ERROR")
    monkeypatch.setattr(run.subprocess, "run", cli)
    with pytest.raises(identity.DeliveryRefused) as caught:
        run.Runtime.az_json(args)
    assert caught.value.check == check
    assert "PRIVATE" not in str(caught.value)


def token(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "synthetic." + payload + ".signature"


@pytest.mark.parametrize("wrong_uri", [False, True])
def test_actual_constructor_authenticates_before_storage(modules, principal, monkeypatch, tmp_path, wrong_uri):
    identity, run = modules
    account, claims, expected = principal
    expected |= {"service_connection_id": "connection", "oidc_request_origin": "https://dev.azure.com",
                 "oidc_path_prefix": "/owned/project/_apis/distributedtask/hubs/build/"}
    source = "a" * 40
    authority = {"session": "p8-unit", "source_sha": source, "release_digest": "b" * 64,
                 "storage_host": "owned.blob.core.windows.net",
                 "queue_cutoff_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(run.Path, "home", lambda: tmp_path)
    for key, value in {"BUILD_BUILDID": "7", "BUILD_REASON": "Manual", "idToken": token(claims["federation"]),
                       "SYSTEM_ACCESSTOKEN": "PRIVATE_JOB_TOKEN",
                       "AZURESUBSCRIPTION_SERVICE_CONNECTION_ID": "connection",
                       "SYSTEM_OIDCREQUESTURI": "https://dev.azure.com/" + ("wrong/" if wrong_uri else "owned/project/_apis/distributedtask/hubs/build/plans/p/jobs/j/") + "oidctoken"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("servicePrincipalKey", raising=False)
    calls = []
    def cli(args):
        calls.append(args[:2])
        return account if args[1] == "show" else {"accessToken": token(claims["access"])}
    monkeypatch.setattr(run.Runtime, "az_json", staticmethod(cli))
    class ReachedStorage(Exception):
        pass
    def storage(*args):
        raise ReachedStorage()
    monkeypatch.setattr(run, "BlobStore", storage)
    public = tmp_path / "p8-publish"
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("no network allowed"))) as client:
        with pytest.raises(identity.IdentityRefused if wrong_uri else ReachedStorage) as caught:
            run.Runtime(authority, expected, "build", public, client)
    assert calls == [["account", "show"], ["account", "get-access-token"]]
    if wrong_uri:
        assert caught.value.check == "refresh-endpoint"
        assert not (public / "identity.json").exists()
    else:
        receipt = json.loads((public / "identity.json").read_bytes())
        assert receipt["verified"] is True and receipt["source_sha"] == source and receipt["run_id"] == 7
        assert "PRIVATE" not in (public / "identity.json").read_text()
