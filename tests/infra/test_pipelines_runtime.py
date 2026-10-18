"""Behavior at the release boundary; external mutations are recorded fakes."""
import importlib
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
import json

import httpx

import pytest


@pytest.fixture
def run(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.run")


def test_prod_does_not_apply_when_original_gate_refuses(run):
    calls = []
    runtime = object.__new__(run.Runtime)
    runtime.stage = "prod"
    def refuse(stage):
        calls.append(("gate",stage))
        raise run.DeliveryRefused("gate")
    runtime.prior_gate = refuse
    runtime.apply_and_load = lambda target: calls.append(("apply",target))
    with pytest.raises(run.DeliveryRefused): runtime.execute()
    assert calls == [("gate","test")]


def test_verify_cannot_reach_apply_or_load(run):
    calls = []
    runtime = object.__new__(run.Runtime)
    runtime.stage = "verify"
    runtime.proof = {"source_sha":"a"*40}
    runtime.bundle = SimpleNamespace(digest="b"*64)
    runtime.prior_gate = lambda stage: calls.append(("prior",stage))
    runtime.store = SimpleNamespace(json=lambda *args:{"source_sha":"a"*40,"release_digest":"b"*64,
        "expected_negative":True,"acceptance_pass":True})
    runtime.apply_and_load = lambda target: pytest.fail("verify mutated index")
    runtime.plan = lambda target: pytest.fail("verify planned mutation")
    runtime.measure = lambda target,**kw: calls.append(("measure",target,kw)) or True
    assert runtime.execute() is True
    assert calls == [("prior","prod"),("measure","demo-prod",{"negative":False})]


def test_index_workspace_copies_only_reviewed_configuration(run,tmp_path,monkeypatch):
    root = tmp_path/"repo"
    source = root/"infra/pipelines/index"
    source.mkdir(parents=True)
    for filename in ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"):
        (source/filename).write_text("reviewed")
    (source/"surprise.tf").write_text("unreviewed")
    runtime = object.__new__(run.Runtime)
    runtime.work = tmp_path/"work"
    runtime.work.mkdir()
    runtime.a = {"storage_account_name":"owned"}
    runtime.command = lambda *args: None
    monkeypatch.setattr(run,"ROOT",root)
    directory = runtime.terraform("test")
    assert {p.name for p in directory.iterdir()} == {
        "main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"}


def test_stage_budget_stops_before_another_request_or_process(run):
    now = datetime.now(timezone.utc)
    ticks = [100.0]
    budget = run.StageBudget("test",now+timedelta(hours=1),clock=lambda:ticks[0])
    assert budget.remaining(120) == 120
    ticks[0] += 721
    with pytest.raises(run.DeliveryRefused,match="deadline"): budget.remaining(120)
    with pytest.raises(run.DeliveryRefused,match="deadline"): budget.before_request(None)


def test_identity_original_keeps_claims_but_no_tokens(run):
    receipt=run.identity_receipt({"id":"sub","tenantId":"tenant","state":"Enabled",
        "user":{"type":"servicePrincipal","name":"client"},"accessToken":"NEVER"},
        {"federation":{"iss":"issuer","sub":"subject","aud":"aud","exp":99,"token":"NEVER"},
         "access":{"tid":"tenant","oid":"object","appid":"client","exp":99,"token":"NEVER"}})
    import json
    assert "NEVER" not in json.dumps(receipt)
    assert receipt["claims"]["access"]["oid"]=="object"


def test_positive_sequence_uses_prod_identity_for_prod_backend(run):
    calls=[]
    for stage in ("test","plan-prod","prod"):
        runtime=object.__new__(run.Runtime); runtime.stage=stage
        role=run.connection_role(stage,"Manual")
        runtime.apply_and_load=lambda target,role=role:calls.append((role,"apply",target))
        runtime.measure=lambda target:calls.append(("gate",target)) or True
        runtime.prior_gate=lambda stage:calls.append(("prior",stage))
        runtime.plan=lambda target,role=role:calls.append((role,"plan",target))
        assert runtime.execute()
    assert ("test","plan","demo-prod") not in calls
    assert calls.index(("gate","test")) < calls.index(("prod","plan","demo-prod")) < calls.index(("prod","apply","demo-prod"))


@pytest.fixture
def flow(run,tmp_path,repo_root,monkeypatch):
    calls=[]
    records={}
    class Store:
        def state(self,target): return None
        def get(self,container,key,**kw): return records[key]
        def json(self,container,key): return json.loads(records[key])
        def put_once(self,container,key,data):
            assert key not in records
            records[key]=data
        def put_json(self,key,value): self.put_once("evidence",key,run.canonical(value))
    bundle=SimpleNamespace(root=tmp_path/"inputs",digest="b"*64,manifest=SimpleNamespace(source_sha="a"*40))
    bundle.root.mkdir()
    def make(name):
        runtime=object.__new__(run.Runtime)
        runtime.a={"search_host":"owned.search.windows.net","storage_account_name":"owned",
            "session":"p8-1016x","manifest_sha256":"f"*64}
        runtime.bundle=bundle; runtime.work=tmp_path/name; runtime.work.mkdir()
        runtime.publish=tmp_path/(name+"-publish"); runtime.publish.mkdir()
        runtime.principal={"subscription_id":"sub","tenant_id":"tenant"}
        runtime.proof={"source_sha":"a"*40,"release_digest":"b"*64,"run_id":17}
        runtime.store=Store(); runtime.tf_env={}
        runtime.process=lambda *args,**kw:SimpleNamespace(returncode=0,stdout="private original",stderr="")
        runtime.search_tokens=SimpleNamespace(token=lambda:calls.append("token") or "synthetic")
        runtime.initialize=lambda *args:calls.append("init")
        runtime.client=httpx.Client(transport=httpx.MockTransport(lambda request:calls.append("request") or httpx.Response(404)))
        legacy=runtime.work/"legacy"; legacy.mkdir()
        (legacy/".terraform.lock.hcl").write_bytes((repo_root/"infra/pipelines/index/.terraform.lock.hcl").read_bytes())
        runtime.terraform=lambda target:calls.append("init") or legacy
        return runtime
    original=tmp_path/"saved"; original.mkdir()
    for name,data in (("plan.tfplan",b"original-plan"),("inputs.tfvars.json",b"{}"),
        (".terraform.lock.hcl",(repo_root/"infra/pipelines/index/.terraform.lock.hcl").read_bytes())):
        (original/name).write_bytes(data); records["plans/test/"+name]=data
    binding=run.bind_plan(original/"plan.tfplan",bundle,"test",None,original/"inputs.tfvars.json",original/".terraform.lock.hcl")
    records["plans/test/binding.json"]=run.canonical({"source_sha":"a"*40,"release_digest":"b"*64,"run_id":17,"binding":asdict(binding)})
    shown={"resource_changes":[{"mode":"managed","address":"azapi_data_plane_resource.index","type":"azapi_data_plane_resource",
        "change":{"actions":["create"],"after":{"type":"Microsoft.Search/searchServices/indexes@2024-07-01",
            "parent_id":"owned.search.windows.net","name":"p8-test"}}}]}
    records["plans/test/shown.json"]=run.canonical(shown)
    monkeypatch.setattr(run,"upload_bundle",lambda *args,**kw:calls.append("upload"))
    return make,calls,records,shown


def test_invalid_binding_never_initializes_remote_backend(run,flow):
    make,calls,records,shown=flow
    envelope=json.loads(records["plans/test/binding.json"])
    envelope["binding"]["target"]="demo-prod"
    records["plans/test/binding.json"]=run.canonical(envelope)
    with pytest.raises(run.DeliveryRefused): make("invalid").apply_and_load("test")
    assert calls==[]


def test_schema_request_never_authenticates_an_invalid_host(run,flow):
    make,calls,records,shown=flow
    runtime=make("wrong-host"); runtime.a["search_host"]="untrusted.example"
    with pytest.raises(run.DeliveryRefused): runtime.apply_and_load("test")
    assert calls==[]


def test_plan_and_apply_originals_survive_distinct_agents(run,flow):
    make,calls,records,shown=flow
    records.clear()
    producer=make("producer")
    def command(args,directory,name,seconds):
        if args[1]=="plan": (directory/"plan.tfplan").write_bytes(b"original-plan")
        return SimpleNamespace(stdout=json.dumps(shown),returncode=0)
    producer.command=command
    producer.plan("test")
    assert json.loads(records["plans/test/shown.json"])==shown
    consumer=make("consumer")
    consumer.apply_and_load("test")
    assert json.loads(records["applies/test/state-before.json"]) is None
    outcome=json.loads(records["applies/test/result.json"])
    assert outcome["returncode"]==0 and outcome["binding"]["target"]=="test"
    assert (consumer.publish/"test-apply/result.json").exists()


def test_apply_timeout_original_publishes_before_agent_exit(run,flow):
    make,calls,records,shown=flow
    runtime=make("timeout")
    def timeout(*args,**kw): raise TimeoutError("private original timeout")
    runtime.process=timeout
    with pytest.raises(TimeoutError): runtime.apply_and_load("test")
    original=json.loads(records["applies/test/result.json"])
    assert original["returncode"] is None and original["binding"]["target"]=="test"
    assert (runtime.publish/"test-apply/result.json").exists()


def test_upload_deadline_metrics_survive_in_published_stage_directory(run,flow,monkeypatch):
    make,calls,records,shown=flow
    runtime=make('upload-deadline')
    metrics={'kind':'upload-metrics','documents_confirmed':500,'requests_started':2,
        'bytes_started':1234,'encoding_seconds':2.0,'http_seconds':238.0,
        'elapsed_seconds':240.0,'outcome':'failed','check':'upload-deadline'}
    def refused(*args,reservation,**kw):
        run.exclusive_json(reservation.with_suffix('.metrics.json'),metrics)
        raise run.UploadRefused('upload-deadline')
    monkeypatch.setattr(run,'upload_bundle',refused)
    with pytest.raises(run.UploadRefused) as caught:
        runtime.apply_and_load('test')
    assert caught.value.check == 'upload-deadline'
    assert json.loads((runtime.publish/'upload-test.metrics.json').read_bytes()) == metrics


def test_expected_negative_requires_no_hits_not_merely_no_correct_answers(run):
    result=SimpleNamespace(passed=False,correct=0,evaluated=229,errors=0,missing=0,skipped=0)
    rows=[SimpleNamespace(hits=[]) for _ in range(229)]
    assert run.negative_passes(result,rows)
    rows[0].hits=[SimpleNamespace(artefact="incorrect")]
    assert not run.negative_passes(result,rows)
