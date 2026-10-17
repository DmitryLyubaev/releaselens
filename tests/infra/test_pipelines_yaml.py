"""Pipeline boundaries and runtime policy, without queueing or Azure credentials."""
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path

import httpx
import pytest
import yaml


@pytest.fixture
def modules(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return tuple(importlib.import_module("scripts.pipelines."+name) for name in ("checks","run","storage"))


def test_main_extends_and_authorizations(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    assert source["trigger"] == "none" and source["pr"] == "none"
    assert source["extends"]["template"] == "templates/search-delivery.yml"
    assert source["parameters"] == [{"name":"mode","type":"string","default":"release","values":["release","negative","identity-preflight"]}]
    assert source["schedules"][0]["cron"] == "0 9 * * *" and source["schedules"][0]["always"] is True
    template = yaml.safe_load((repo_root / "pipelines/templates/search-delivery.yml").read_bytes())
    text = json.dumps(template)
    assert "stepList" not in text and "persistCredentials\": true" not in text
    assert "P8Agent -equals ReleaseLens" in text and "refs/heads/main" in text
    assert "addSpnToEnvironment" in text and "visibleAzLogin" in text
    assert "ReleaseLens-P8" in text and "agents-demo.yml" not in text


def test_failed_or_missing_gate_blocks_prod(modules):
    _,run,_ = modules
    good = {"source_sha":"a"*40,"release_digest":"b"*64,"run_id":42,
            "gate":{"expected":229,"evaluated":229,"correct":120,"errors":0,"missing":0,"skipped":0,"passed":True}}
    expected = {"source_sha":"a"*40,"release_digest":"b"*64,"run_id":42}
    assert run.require_gate(good,expected) is None
    for field,value in [("correct",119),("evaluated",228),("errors",1),("skipped",1),("missing",1),("passed",False)]:
        bad = good | {"gate":good["gate"] | {field:value}}
        with pytest.raises(Exception): run.require_gate(bad,expected)
    for bad in [{},good | {"source_sha":"c"*40},good | {"release_digest":"d"*64},good | {"run_id":99}]:
        with pytest.raises(Exception): run.require_gate(bad,expected)


def test_exact_artifact_dependencies(modules):
    _,run,_ = modules
    assert run.dependencies("build") == ()
    assert run.dependencies("test") == ("test-plan",)
    assert run.dependencies("prod") == ("test-gate","prod-plan")
    assert run.dependencies("negative") == ("prod-gate",)
    assert run.dependencies("verify") == ("prod-gate",)
    with pytest.raises(Exception): run.dependencies("anything-else")


def test_negative_never_uses_prod_writer(modules):
    _,run,_ = modules
    assert run.connection_role("negative","Manual") == "test"
    assert run.connection_role("prod","Manual") == "prod"
    with pytest.raises(Exception): run.connection_role("negative","Schedule")


def test_schedule_read_only(modules,repo_root):
    _,run,_ = modules
    assert run.connection_role("verify","Schedule") == "verify"
    for stage in ["build","test","prod","negative"]:
        with pytest.raises(Exception): run.connection_role(stage,"Schedule")
    with pytest.raises(Exception): run.connection_role("verify","Manual")
    template = yaml.safe_load((repo_root / "pipelines/templates/search-delivery.yml").read_bytes())
    scheduled = template["stages"][0]["${{ if eq(variables['Build.Reason'], 'Schedule') }}"]
    rendered = json.dumps(scheduled)
    assert "ReleaseLens-P8-Verify" in rendered
    assert "ReleaseLens-P8-Test" not in rendered and "ReleaseLens-P8-Prod" not in rendered
    assert "app.delivery_search load" not in rendered and "terraform apply" not in rendered


def test_required_checks_snapshot(modules):
    checks,_,_ = modules
    raw = {"checks":[{"id":i,"type":{"id":f"type-{kind}"},"resource":{"id":"resource","type":"environment"},
                     "settings":{"policy":kind},"isDisabled":False,"version":1}
                    for i,kind in enumerate(["approval","branch","required-template","exclusive-lock"],1)],
           "pipeline_authorized":True,"pool_authorized":True,"private_project":True,"main_protected":True,
           "secrets_masked":True}
    expected = {"approval_ref":"reviewed-settings", "checks":[{
        "kind":kind,"id":i,"type_id":f"type-{kind}","resource":{"id":"resource","type":"environment"},
        "settings_sha256":hashlib.sha256(json.dumps({"policy":kind},sort_keys=True,separators=(",",":")).encode()).hexdigest(),
        "version":1} for i,kind in enumerate(["approval","branch","required-template","exclusive-lock"],1)]}
    checks.verify_checks(raw,expected)
    for flag in ["pipeline_authorized","pool_authorized","private_project","main_protected","secrets_masked"]:
        with pytest.raises(Exception): checks.verify_checks(raw | {flag:False},expected)
    for i in range(4):
        with pytest.raises(Exception): checks.verify_checks(raw | {"checks":raw["checks"][:i]+raw["checks"][i+1:]},expected)
    changed = dict(raw["checks"][0]); changed["settings"] = {"policy":"bypass"}
    with pytest.raises(Exception): checks.verify_checks(raw | {"checks":[changed]+raw["checks"][1:]},expected)


def test_blob_claim_and_state_absence_fail_closed(modules):
    _,_,storage = modules
    requests = []
    class Tokens:
        def token(self): return "synthetic"
    def server(request):
        requests.append(request)
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            return httpx.Response(412)
        return httpx.Response(404,headers={"x-ms-error-code":"ContainerNotFound"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store = storage.BlobStore("owned.blob.core.windows.net",Tokens(),client)
        with pytest.raises(Exception): store.put_once("evidence","claims/release.json",b"{}");
        with pytest.raises(Exception): store.state("test")
    assert len(requests) == 2


def test_missing_results_files_refuse(modules,tmp_path):
    _,run,_ = modules
    with pytest.raises(Exception): run.require_result_files(tmp_path)
    (tmp_path/"results.json").write_text("{}")
    with pytest.raises(Exception): run.require_result_files(tmp_path)


def test_metadata_budget_survives_new_agent(modules):
    _,_,storage=modules
    keys=set()
    class Tokens:
        def token(self): return "synthetic"
    def server(request):
        if request.url.path in keys: return httpx.Response(412)
        keys.add(request.url.path)
        return httpx.Response(201)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        for i in range(3):
            storage.BlobStore("owned.blob.core.windows.net",Tokens(),client).reserve_budget("metadata",3)
        with pytest.raises(Exception):
            storage.BlobStore("owned.blob.core.windows.net",Tokens(),client).reserve_budget("metadata",3)
    assert len(keys)==3
