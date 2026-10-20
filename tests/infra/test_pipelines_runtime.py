"""Behavior at the release boundary; external mutations are recorded fakes."""
import ast
from dataclasses import asdict,replace
import hashlib
import importlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

ROLES = ("test","prod","verify")
PRINCIPALS = {role:{"client_id":f"00000000-0000-0000-0000-00000000001{n}","object_id":f"00000000-0000-0000-0000-00000000002{n}",
    "issuer":"https://<ado-issuer>/00000000-0000-0000-0000-000000000003","subject":"sc://example/ReleaseLens/"+role,
    "service_connection_id":f"00000000-0000-0000-0000-00000000003{n}",
    "oidc_request_origin":"https://example.vstoken.visualstudio.com","oidc_path_prefix":"/"} for n,role in enumerate(ROLES)}
DIGEST = "0"*64
SETTINGS = {"P8_TENANT_ID":"00000000-0000-0000-0000-000000000001","P8_SUBSCRIPTION_ID":"00000000-0000-0000-0000-000000000002",
    "P8_STORAGE_ACCOUNT":"p8a1b2c3","P8_SEARCH_GROUP":"rg-p8-search","P8_SEARCH_SERVICE":"p8-search-a1b2c3",
    "P8_RELEASE_DIGEST":DIGEST,"P8_PRINCIPALS_JSON":json.dumps(PRINCIPALS)}
ENV = SETTINGS | {"BUILD_SOURCEBRANCH":"refs/heads/main","BUILD_REASON":"Manual","BUILD_SOURCEVERSION":"a"*40,"BUILD_BUILDID":"41"}
SEARCH_HOST = "p8-search-a1b2c3.search.windows.net"
STORAGE_HOST = "p8a1b2c3.blob.core.windows.net"
INDEX_BODY = {"@odata.etag":"\"0x1\"","name":"p8-demo-prod","fields":[{"name":"chunk_id","type":"Edm.String","key":True}]}
FINGERPRINT = {"document_count":41825,"schema_sha256":hashlib.sha256(json.dumps(INDEX_BODY,sort_keys=True,
    separators=(",",":"),ensure_ascii=False).encode()).hexdigest()}
CONTROLLER_ENV = ("P8_AUTHORITY_JSON","P8_AUTHORITY_SHA256","P8_SOURCE_SHA","SYSTEM_DEFINITIONID","SYSTEM_JOBATTEMPT","SYSTEM_STAGEATTEMPT")


@pytest.fixture
def run(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.run")


@pytest.fixture
def settings(run):
    return run.load_settings(SETTINGS)


@pytest.fixture
def env(monkeypatch,tmp_path):
    for key in CONTROLLER_ENV: monkeypatch.delenv(key,raising=False)
    for key,value in ENV.items(): monkeypatch.setenv(key,value)
    home=tmp_path/"home"; home.mkdir()
    monkeypatch.setenv("HOME",str(home)); monkeypatch.setenv("USERPROFILE",str(home))
    return home


@pytest.fixture
def processes(run,monkeypatch):
    """git and the offline pytest only; Terraform or az reaching subprocess fails the test."""
    state=SimpleNamespace(calls=[],head="a"*40,dirty=False,pytest=0,service=False)
    def fake(args,**kw):
        state.calls.append(list(args))
        if list(args[:2])==["git","rev-parse"]: return SimpleNamespace(returncode=0,stdout=state.head+"\n",stderr="")
        if list(args[:2])==["git","diff"]: return SimpleNamespace(returncode=1 if state.dirty else 0,stdout="",stderr="")
        if list(args[:4])==["az","search","service","show"]:
            return SimpleNamespace(returncode=0 if state.service else 3,stdout="",stderr="" if state.service else "(ResourceNotFound) private")
        if "pytest" in args: return SimpleNamespace(returncode=state.pytest,stdout="private",stderr="")
        pytest.fail(f"unexpected process {args[:2]}")
    monkeypatch.setattr(run.subprocess,"run",fake)
    return state


@pytest.fixture
def fake_search():
    """Index GET, count and search on one owned host. Search answers the S2 or negative candidate."""
    state=SimpleNamespace(requests=[],correct=229,ignore_filter=False,prod_bodies=[INDEX_BODY],prod_counts=[41825])
    def next_value(values): return values.pop(0) if len(values) > 1 else values[0]
    def server(request):
        path=request.url.path
        state.requests.append((request.method,path))
        if request.url.host != SEARCH_HOST: return httpx.Response(421)
        if path.endswith("/docs/$count"):
            return httpx.Response(200,text=str(next_value(state.prod_counts) if "/p8-demo-prod/" in path else 41825))
        if request.method == "GET" and path == "/indexes/p8-demo-prod":
            return httpx.Response(200,json=next_value(state.prod_bodies))
        if request.method == "POST" and path.endswith("/docs/search"):
            body=json.loads(request.content)
            if body.get("filter") is not None and not state.ignore_filter: return httpx.Response(200,json={"value":[]})
            i=int(body["search"].split()[-1])
            return httpx.Response(200,json={"value":[{"chunk_id":str(i),
                "artefact":f"a{i}" if i < state.correct else "wrong","@search.score":1.0}]})
        return httpx.Response(404)
    state.transport=httpx.MockTransport(server)
    def searched(index): return [r for r in state.requests if r[1] == f"/indexes/{index}/docs/search"]
    state.searched=searched
    return state


def make_bundle(run,root: Path,repo_root: Path,source_sha="c"*40):
    """A synthetic verified bundle: the 229 frozen questions, cached vectors and the repository's copies."""
    root.mkdir(parents=True)
    rows=[dict(qid=f"q{i:03}",question=f"Find synthetic change number {i}",target=f"a{i}",entity_type="commit") for i in range(229)]
    data="".join(json.dumps(row)+"\n" for row in rows).encode()
    (root/"questions.jsonl").write_bytes(data)
    (root/"questions.manifest.json").write_text(json.dumps({"sha256":hashlib.sha256(data).hexdigest()}))
    (root/"links.jsonl").write_text("")
    np.save(root/"queries.npy",np.ones((229,1536),dtype="float32"))
    (root/"s2.json").write_text(json.dumps({"top":50,"k":50,"filter":None}))
    (root/"negative.json").write_text(json.dumps({"top":50,"k":50,"filter":"artefact eq '__p8_no_match__'"}))
    (root/"schema.json").write_text("{}")
    for bundled,source in run.BUNDLED_COPIES: shutil.copyfile(repo_root/source,root/bundled)
    manifest=SimpleNamespace(source_sha=source_sha)
    return importlib.import_module("app.delivery_search.contracts").VerifiedBundle(root,manifest,DIGEST)


@pytest.fixture
def harness(run,env,processes,fake_store,fake_search,repo_root,tmp_path,monkeypatch):
    """Drives run.main end to end against the fakes: no socket, no az, no Terraform."""
    bundle=make_bundle(run,tmp_path/"bundle",repo_root)
    h=SimpleNamespace(store=fake_store,search=fake_search,processes=processes,bundle=bundle,home=env,principals=[],fetched=[])
    def authenticate(principal):
        h.principals.append(principal)
        return ({"id":"sub","tenantId":"tenant","state":"Enabled","user":{"type":"servicePrincipal","name":"client"}},
                {"federation":{"iss":"issuer","sub":"subject","aud":"aud","exp":99},
                 "access":{"tid":"tenant","oid":"object","appid":"client","exp":99}},{"TF_IN_AUTOMATION":"true"})
    def fetch(url,digest,destination,tokens,client,*,allowed_host):
        h.fetched.append((url,digest,allowed_host))
        return bundle
    def blob_store(host,tokens,client):
        assert host == STORAGE_HOST
        return fake_store
    monkeypatch.setattr(run,"authenticate",authenticate)
    monkeypatch.setattr(run,"fetch_bundle",fetch)
    monkeypatch.setattr(run,"BlobStore",blob_store)
    monkeypatch.setattr(run,"TokenSource",lambda scope,tenant:SimpleNamespace(token=lambda:"synthetic"))
    monkeypatch.setattr(run,"http_client",lambda:httpx.Client(transport=fake_search.transport))
    def main(stage,*,reason="Manual",build_id="41"):
        monkeypatch.setenv("BUILD_REASON",reason); monkeypatch.setenv("BUILD_BUILDID",build_id)
        h.publish=tmp_path/"publish"/(stage+"-"+build_id)
        return run.main([stage,"--receipt-dir",str(h.publish)])
    def point(release_run_id=41,release_digest=DIGEST,source_sha="e"*40):
        fake_store.write_pointer({"release_run_id":release_run_id,"release_digest":release_digest,"source_sha":source_sha})
        fake_store.pointer_writes.clear()
    def seed(stage,*,release_run_id=41,correct=229,**extra):
        """The gate evidence an earlier stage of the release wrote, from a different commit of main."""
        rows=[run.ArmResult(f"q{i:03}","S2",[run.Hit(i,f"a{i}" if i < correct else "wrong",1.0)],1.0,None) for i in range(229)]
        result=run.grade_rows(bundle,rows)
        output=tmp_path/"seed"/f"{stage}-{release_run_id}"
        run.write_gate(result,rows,output)
        receipt={"source_sha":"e"*40,"release_digest":DIGEST,"release_run_id":release_run_id,"stage":stage,
            "gate":asdict(result),"expected_negative":False,"acceptance_pass":result.passed}
        for name in ("results.json","results.xml"):
            data=(output/name).read_bytes()
            fake_store.put_once("evidence",f"runs/{release_run_id}/gates/{stage}/{name}",data)
            receipt[name+"_sha256"]=hashlib.sha256(data).hexdigest()
        fake_store.put_json(f"runs/{release_run_id}/receipts/{stage}.json",receipt|extra)
    def failure(): return json.loads((h.publish/"failure.json").read_bytes())
    def receipt(): return json.loads((h.publish/"gate"/"receipt.json").read_bytes())
    h.main,h.point,h.seed,h.failure,h.receipt=main,point,seed,failure,receipt
    return h


# The boundary: plain settings, main, a clean checkout, and no controller.

def test_context_requires_main_and_settings(run,env,processes,monkeypatch):
    settings,principal=run.context("build")
    assert settings == run.load_settings(SETTINGS) and principal == settings.principals["test"]
    assert run.context("plan-prod")[1] == settings.principals["prod"]
    monkeypatch.setenv("BUILD_SOURCEBRANCH","refs/heads/feature")
    with pytest.raises(run.DeliveryRefused) as caught: run.context("build")
    assert caught.value.code == "authority"
    monkeypatch.setenv("BUILD_SOURCEBRANCH","refs/heads/main")
    monkeypatch.delenv("P8_STORAGE_ACCOUNT")
    with pytest.raises(run.DeliveryRefused) as caught: run.context("build")
    assert caught.value.code == "settings"


def test_context_requires_clean_checkout_of_the_running_commit(run,env,processes):
    processes.dirty=True
    with pytest.raises(run.DeliveryRefused,match="^authority$"): run.context("build")
    processes.dirty,processes.head=False,"b"*40
    with pytest.raises(run.DeliveryRefused,match="^authority$"): run.context("build")


def test_no_authority_or_cutoff_variables_are_read(run,env,processes,monkeypatch):
    expected=run.context("test")
    for key in CONTROLLER_ENV: monkeypatch.setenv(key,"not-a-packet")
    assert run.context("test") == expected


def test_verify_requires_schedule_reason(run,env,processes,monkeypatch):
    monkeypatch.setenv("BUILD_REASON","Manual")
    with pytest.raises(run.DeliveryRefused) as caught: run.context("verify")
    assert caught.value.code == "authority"
    monkeypatch.setenv("BUILD_REASON","Schedule")
    assert run.context("verify")[1] == run.load_settings(SETTINGS).principals["verify"]


@pytest.mark.parametrize("stage,reason",[("build","Schedule"),("test","Schedule"),("prod","Schedule"),
    ("negative","Schedule"),("build","IndividualCI"),("verify","IndividualCI")])
def test_scheduled_run_only_verifies(run,env,processes,monkeypatch,stage,reason):
    monkeypatch.setenv("BUILD_REASON",reason)
    with pytest.raises(run.DeliveryRefused,match="^authority$"): run.context(stage)


def test_guard_only_never_authenticates(run,env,processes,monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(run,"authenticate",lambda *a:pytest.fail("guard authenticated"))
    assert run.main(["negative","--receipt-dir",str(tmp_path/"out"),"--guard-only"]) == 0
    assert capsys.readouterr().out.strip() == "status=pass code=source-guard"


@pytest.mark.parametrize("settings_present",[False,True])
def test_promote_negative_always_refuses(run,env,processes,monkeypatch,tmp_path,capsys,settings_present):
    if not settings_present:
        for key in (*ENV,*CONTROLLER_ENV): monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(run,"authenticate",lambda *a:pytest.fail("promote-negative authenticated"))
    monkeypatch.setattr(run,"http_client",lambda:pytest.fail("promote-negative opened a client"))
    for index,argv in enumerate(([],["--guard-only"])):
        published=tmp_path/str(index)
        assert run.main(["promote-negative","--receipt-dir",str(published),*argv]) == 2
        assert json.loads((published/"failure.json").read_bytes())["code"] == "authority"
    assert "status=refused code=authority" in capsys.readouterr().out
    assert processes.calls == []


def test_run_imports_no_controller_module(repo_root):
    tree=ast.parse((repo_root/"scripts/pipelines/run.py").read_text(encoding="utf-8"))
    imported={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    assert not imported & {"checks","authority_transport","readiness","metadata"}
    assert "StageBudget" not in (repo_root/"scripts/pipelines/run.py").read_text(encoding="utf-8")


# The frozen bundle.

def test_bundle_source_sha_may_differ_from_running_commit(run,harness):
    assert harness.bundle.manifest.source_sha != ENV["BUILD_SOURCEVERSION"]
    assert run.check_bundle(harness.bundle) == {"bundle_digest":DIGEST,"bundle_source_sha":"c"*40}
    assert harness.main("build") == 0
    recorded=json.loads((harness.publish/"bundle.json").read_bytes())
    assert recorded["source_sha"] == "a"*40 and recorded["bundle_source_sha"] == "c"*40
    assert harness.fetched == [(f"https://{STORAGE_HOST}/inputs/releases/{DIGEST}/release.tar.gz",DIGEST,STORAGE_HOST)]


@pytest.mark.parametrize("bundled",["scorer.py","requirements.lock","provider.lock.hcl"])
def test_bundle_copies_must_match_repository(run,repo_root,tmp_path,bundled):
    bundle=make_bundle(run,tmp_path/"bundle",repo_root)
    with (bundle.root/bundled).open("ab") as out: out.write(b"\n# changed\n")
    with pytest.raises(run.DeliveryRefused,match="^bundle$"): run.check_bundle(bundle)
    (bundle.root/bundled).unlink()
    with pytest.raises(run.DeliveryRefused,match="^bundle$"): run.check_bundle(bundle)


# The release pointer and the per-run namespace.

def test_release_stage_writes_pointer_once_in_build(harness):
    assert harness.main("build") == 0
    assert harness.store.pointer_writes == [{"release_run_id":41,"release_digest":DIGEST,"source_sha":"a"*40}]
    assert any("pytest" in call for call in harness.processes.calls)
    assert harness.principals == [PRINCIPALS["test"]|{"tenant_id":SETTINGS["P8_TENANT_ID"],"subscription_id":SETTINGS["P8_SUBSCRIPTION_ID"]}]
    assert harness.store.evidence("runs/41/identities/build.json")["build_id"] == 41
    assert (harness.home/"p8"/"41").is_dir()


@pytest.mark.parametrize("present,held",[(True,None),(False,"service")])
def test_build_refuses_while_a_service_or_its_state_remains(harness,present,held):
    harness.processes.service=present
    if held: harness.store.states["service"]={"version":4,"lineage":"00000000-0000-0000-0000-000000000009","serial":3,
        "resources":[{"mode":"managed","type":"azurerm_search_service","name":"search","instances":[{"attributes":{}}]}]}
    assert harness.main("build") == 2
    assert harness.failure()["code"] == "reserved"
    assert harness.store.pointer_writes == [] and harness.store.blobs == {} and harness.fetched == []
    assert not any("pytest" in call for call in harness.processes.calls)
    assert ["az","search","service","show","--name","p8-search-a1b2c3","--resource-group","rg-p8-search"] in harness.processes.calls


@pytest.mark.parametrize("held",[None,{"version":4,"lineage":"00000000-0000-0000-0000-000000000009","serial":9,"resources":[]}])
def test_build_proceeds_when_the_service_is_absent_and_its_state_empty(harness,held):
    harness.store.states["service"]=held
    assert harness.main("build") == 0
    assert harness.store.pointer_writes == [{"release_run_id":41,"release_digest":DIGEST,"source_sha":"a"*40}]


def test_build_without_passing_offline_tests_writes_no_pointer(harness):
    harness.processes.pytest=1
    assert harness.main("build") == 2
    assert harness.store.pointer_writes == [] and harness.failure()["code"] == "gate"


@pytest.mark.parametrize("pointer",[None,{"release_run_id":40},{"release_digest":"f"*64},{"source_sha":"b"*40}])
def test_later_release_stages_require_this_runs_pointer(harness,pointer):
    if pointer is not None: harness.point(**{"release_run_id":41,"source_sha":"a"*40}|pointer)
    assert harness.main("plan-test") == 2
    assert harness.failure()["code"] in ({"request"} if pointer is None else {"authority"})
    assert not [c for c in harness.processes.calls if c[0] == "terraform"]
    assert not [k for k in harness.store.blobs if k[1].startswith("runs/")]


# The negative run: red at the gate, prod proven unchanged.

def test_negative_expected_failure_exits_17_and_records_prod_unchanged(harness):
    harness.point(); harness.seed("prod")
    assert harness.main("negative",build_id="57") == 17
    receipt=harness.store.evidence("runs/41/receipts/negative.json")
    assert receipt == harness.receipt()
    assert receipt["prod_before"] == receipt["prod_after"] == FINGERPRINT
    assert receipt["acceptance_pass"] is True and receipt["expected_negative"] is True and receipt["gate"]["correct"] == 0
    assert (receipt["release_run_id"],receipt["source_sha"],receipt["bundle_source_sha"]) == (41,"a"*40,"c"*40)
    assert ("evidence","runs/41/gates/negative/results.json") in harness.store.blobs
    assert harness.principals[0]["client_id"] == PRINCIPALS["test"]["client_id"]
    assert harness.store.evidence("runs/41/identities/negative.json")["build_id"] == 57
    requests=harness.search.requests
    searches=[i for i,r in enumerate(requests) if r[1].endswith("/docs/search")]
    prod=[i for i,r in enumerate(requests) if r[1].startswith("/indexes/p8-demo-prod")]
    assert len(searches) == 229 and all(r[1] == "/indexes/p8-test/docs/search" for r in (requests[i] for i in searches))
    assert len(prod) == 4 and prod[:2] == [0,1] and min(prod[2:]) > max(searches)
    assert harness.store.pointer_writes == []


def test_negative_that_passes_gate_is_refused(harness):
    harness.point(); harness.seed("prod")
    harness.search.ignore_filter=True
    assert harness.main("negative",build_id="57") == 2
    receipt=harness.store.evidence("runs/41/receipts/negative.json")
    assert receipt["acceptance_pass"] is False and receipt["gate"]["correct"] >= 120
    assert harness.receipt() == receipt and harness.failure()["code"] == "gate"


@pytest.mark.parametrize("change",["schema","count"])
def test_negative_refuses_when_prod_fingerprint_changes(harness,change):
    harness.point(); harness.seed("prod")
    if change == "schema": harness.search.prod_bodies=[INDEX_BODY,INDEX_BODY|{"@odata.etag":"\"0x2\""}]
    else: harness.search.prod_counts=[41825,41824]
    assert harness.main("negative",build_id="57") == 2
    receipt=harness.store.evidence("runs/41/receipts/negative.json")
    assert receipt["prod_before"] == FINGERPRINT and receipt["prod_after"] != FINGERPRINT
    assert receipt["acceptance_pass"] is False and harness.failure()["code"] == "gate"


@pytest.mark.parametrize("pointer",[None,{"release_digest":"f"*64},{"release_run_id":0},{"release_run_id":"41"}])
def test_negative_refuses_without_the_current_release(harness,pointer):
    if pointer is not None: harness.point(**pointer)
    harness.seed("prod")
    assert harness.main("negative",build_id="57") == 2
    assert harness.search.requests == []


def test_negative_requires_passing_prod_gate_of_the_same_release(harness):
    harness.point(); harness.seed("prod",correct=119)
    assert harness.main("negative",build_id="57") == 2
    assert harness.failure()["code"] == "gate" and harness.search.requests == []


# The scheduled verify run.

def test_verify_scores_prod_after_the_release_and_negative(harness):
    harness.point(); harness.seed("prod")
    harness.seed("negative",correct=0,expected_negative=True,acceptance_pass=True,prod_before=FINGERPRINT,prod_after=FINGERPRINT,prod_unchanged=True)
    assert harness.main("verify",reason="Schedule",build_id="60") == 0
    assert len(harness.search.searched("p8-demo-prod")) == 229 and harness.search.searched("p8-test") == []
    assert harness.store.evidence("runs/41/receipts/verify.json")["acceptance_pass"] is True
    assert harness.principals[0]["client_id"] == PRINCIPALS["verify"]["client_id"]


@pytest.mark.parametrize("negative",[{"release_run_id":40},{"acceptance_pass":False},{"expected_negative":False},{"release_digest":"f"*64}])
def test_verify_refuses_receipt_from_other_release(harness,negative):
    harness.point(); harness.seed("prod")
    harness.store.put_json("runs/41/receipts/negative.json",{"source_sha":"e"*40,"release_digest":DIGEST,"release_run_id":41,
        "expected_negative":True,"acceptance_pass":True,"prod_unchanged":True}|negative)
    assert harness.main("verify",reason="Schedule",build_id="60") == 2
    assert harness.failure()["code"] == "gate" and harness.search.requests == []


def test_verify_refuses_without_negative_receipt(harness):
    harness.point(); harness.seed("prod")
    assert harness.main("verify",reason="Schedule",build_id="60") == 2
    assert harness.search.requests == []


def test_verify_never_runs_manually(harness):
    harness.point(); harness.seed("prod")
    assert harness.main("verify",reason="Manual",build_id="60") == 2
    assert harness.failure()["code"] == "authority" and harness.principals == []


# Public failure diagnostics.

@pytest.mark.parametrize("tamper",[False,True])
def test_stage_publishes_sanitized_request_diagnostic(run,settings,repo_root,monkeypatch,tmp_path,capsys,tamper):
    storage=importlib.import_module("scripts.pipelines.storage")
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(503,text="private response"))) as client:
        with pytest.raises(run.RequestRefused) as caught:
            storage.BlobStore(STORAGE_HOST,SimpleNamespace(token=lambda:"synthetic"),client).write_pointer({"release_run_id":41})
    error=caught.value
    error.args=("private exception detail",)
    if tamper:
        error.check="private operation"
        error.http_status="private status"
    monkeypatch.setattr(run,"context",lambda *args:(settings,settings.principals["test"]))
    class FailedRuntime:
        def __init__(self,*args): raise error
    monkeypatch.setattr(run,"Runtime",FailedRuntime)
    monkeypatch.setattr(run,"http_client",lambda:httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail("network"))))
    published=tmp_path/"build"
    assert run.main(["build","--receipt-dir",str(published)]) == 2
    assert json.loads((published/"failure.json").read_bytes()) == {
        "kind":"stage-failure","stage":"build","code":"request",
        "check":"unclassified" if tamper else "pointer-http","http_status":None if tamper else 503}
    public=(published/"failure.json").read_text()+capsys.readouterr().out
    assert "private" not in public


def test_identity_original_keeps_claims_but_no_tokens(run):
    receipt=run.identity_receipt({"id":"sub","tenantId":"tenant","state":"Enabled",
        "user":{"type":"servicePrincipal","name":"client"},"accessToken":"NEVER"},
        {"federation":{"iss":"issuer","sub":"subject","aud":"aud","exp":99,"token":"NEVER"},
         "access":{"tid":"tenant","oid":"object","appid":"client","exp":99,"token":"NEVER"}})
    assert "NEVER" not in json.dumps(receipt)
    assert receipt["claims"]["access"]["oid"]=="object"


# The kept delivery core: stage order, gates, plans, apply and load.

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
    runtime.proof = {"source_sha":"a"*40,"release_digest":DIGEST,"release_run_id":41}
    runtime.prior_gate = lambda stage: calls.append(("prior",stage))
    runtime.store = SimpleNamespace(json=lambda container,key:calls.append(("read",key)) or {"source_sha":"e"*40,
        "release_digest":DIGEST,"release_run_id":41,"expected_negative":True,"acceptance_pass":True,"prod_unchanged":True})
    runtime.apply_and_load = lambda target: pytest.fail("verify mutated index")
    runtime.plan = lambda target: pytest.fail("verify planned mutation")
    runtime.measure = lambda target,**kw: calls.append(("measure",target,kw)) or True
    assert runtime.execute() is True
    assert calls == [("prior","prod"),("read","runs/41/receipts/negative.json"),("measure","demo-prod",{})]


def test_positive_sequence_uses_prod_identity_for_prod_backend(run):
    calls=[]
    for stage in ("plan-test","test","plan-prod","prod"):
        runtime=object.__new__(run.Runtime); runtime.stage=stage
        role=run.role_for(stage,"Manual")
        runtime.apply_and_load=lambda target,role=role:calls.append((role,"apply",target))
        runtime.measure=lambda target:calls.append(("gate",target)) or True
        runtime.prior_gate=lambda stage:calls.append(("prior",stage))
        runtime.plan=lambda target,role=role:calls.append((role,"plan",target))
        assert runtime.execute()
    assert ("test","plan","demo-prod") not in calls and ("prod","plan","test") not in calls
    assert calls.index(("test","plan","test")) < calls.index(("test","apply","test")) < calls.index(("gate","test"))
    assert calls.index(("gate","test")) < calls.index(("prod","plan","demo-prod")) < calls.index(("prod","apply","demo-prod"))


def test_release_wide_checks_compare_release_not_commit(run):
    gate={"expected":229,"evaluated":229,"correct":120,"errors":0,"missing":0,"skipped":0,"passed":True}
    expected={"source_sha":"a"*40,"release_digest":DIGEST,"release_run_id":41}
    run.require_gate({"source_sha":"e"*40,"release_digest":DIGEST,"release_run_id":41,"gate":gate},expected)
    for changed in ({"release_digest":"f"*64},{"release_run_id":40}):
        with pytest.raises(run.DeliveryRefused,match="^gate$"):
            run.require_gate(expected|changed|{"gate":gate},expected)
    with pytest.raises(run.DeliveryRefused,match="^gate$"):
        run.require_gate(expected|{"gate":gate|{"correct":119}},expected)


def test_index_workspace_copies_only_reviewed_configuration(run,settings,tmp_path,monkeypatch):
    root = tmp_path/"repo"
    source = root/"infra/pipelines/index"
    source.mkdir(parents=True)
    for filename in ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"):
        (source/filename).write_text("reviewed")
    (source/"surprise.tf").write_text("unreviewed")
    runtime = object.__new__(run.Runtime)
    runtime.work = tmp_path/"work"
    runtime.work.mkdir()
    runtime.settings = settings
    runtime.command = lambda *args: None
    monkeypatch.setattr(run,"ROOT",root)
    directory = runtime.terraform("test")
    assert {p.name for p in directory.iterdir()} == {
        "main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"}


@pytest.mark.parametrize("target,container,key",[("test","test-state","index.tfstate"),
    ("demo-prod","prod-state","index.tfstate"),("service","service-state","service.tfstate")])
def test_init_names_each_targets_own_backend(run,target,container,key):
    args=run.init_args("p8a1b2c3",target)
    assert args[:2] == ["terraform","init"] and "-lockfile=readonly" in args
    assert [a for a in args if a.startswith("-backend-config=")] == ["-backend-config=storage_account_name=p8a1b2c3",
        "-backend-config=container_name="+container,"-backend-config=key="+key]
    with pytest.raises(run.DeliveryRefused): run.init_args("p8a1b2c3","verify")


@pytest.fixture
def flow(run,settings,fake_store,tmp_path,repo_root,monkeypatch):
    calls=[]
    records=fake_store.blobs
    bundle=SimpleNamespace(root=tmp_path/"inputs",digest=DIGEST,manifest=SimpleNamespace(source_sha="c"*40))
    bundle.root.mkdir()
    def make(name):
        runtime=object.__new__(run.Runtime)
        runtime.settings=replace(settings,search_service_name="owned")
        runtime.stage="test"
        runtime.bundle=bundle; runtime.work=tmp_path/name; runtime.work.mkdir()
        runtime.publish=tmp_path/(name+"-publish"); runtime.publish.mkdir()
        runtime.principal=settings.principals["test"]
        runtime.proof={"source_sha":"a"*40,"release_digest":DIGEST,"release_run_id":41}
        runtime.store=fake_store; runtime.tf_env={}
        runtime.process=lambda *args,**kw:SimpleNamespace(returncode=0,stdout="private original",stderr="")
        runtime.search_tokens=SimpleNamespace(token=lambda:calls.append("token") or "synthetic")
        runtime.initialize=lambda *args:calls.append("init")
        runtime.client=httpx.Client(transport=httpx.MockTransport(lambda request:calls.append("request") or (
            httpx.Response(200,text="0") if request.url.path.endswith('/docs/$count') else httpx.Response(404))))
        legacy=runtime.work/"legacy"; legacy.mkdir()
        (legacy/".terraform.lock.hcl").write_bytes((repo_root/"infra/pipelines/index/.terraform.lock.hcl").read_bytes())
        runtime.terraform=lambda target:calls.append("init") or legacy
        return runtime
    def key(name): return ("evidence","runs/41/"+name)
    original=tmp_path/"saved"; original.mkdir()
    for name,data in (("plan.tfplan",b"original-plan"),("inputs.tfvars.json",b"{}"),
        (".terraform.lock.hcl",(repo_root/"infra/pipelines/index/.terraform.lock.hcl").read_bytes())):
        (original/name).write_bytes(data); records[key("plans/test/"+name)]=data
    binding=run.bind_plan(original/"plan.tfplan",bundle,"test",None,original/"inputs.tfvars.json",original/".terraform.lock.hcl")
    records[key("plans/test/binding.json")]=run.canonical({"source_sha":"a"*40,"release_digest":DIGEST,"release_run_id":41,"binding":asdict(binding)})
    shown={"resource_changes":[{"mode":"managed","address":"azapi_data_plane_resource.index","type":"azapi_data_plane_resource",
        "change":{"actions":["create"],"after":{"type":"Microsoft.Search/searchServices/indexes@2024-07-01",
            "parent_id":"owned.search.windows.net","name":"p8-test"}}}]}
    records[key("plans/test/shown.json")]=run.canonical(shown)
    monkeypatch.setattr(run,"upload_bundle",lambda *args,**kw:calls.append("upload"))
    return make,calls,records,shown,key


def test_plan_inputs_come_from_settings(run,flow):
    make,calls,records,shown,key=flow
    records.clear()
    runtime=make("plan-inputs")
    def command(args,directory,name,seconds):
        if args[1]=="plan": (directory/"plan.tfplan").write_bytes(b"original-plan")
        return SimpleNamespace(stdout=json.dumps(shown),returncode=0)
    runtime.command=command
    runtime.plan("test")
    assert json.loads(records[key("plans/test/inputs.tfvars.json")]) == {"subscription_id":SETTINGS["P8_SUBSCRIPTION_ID"],
        "tenant_id":SETTINGS["P8_TENANT_ID"],"target":"test","search_host":"owned.search.windows.net",
        "schema_file":str(runtime.bundle.root/"schema.json")}
    assert json.loads(records[key("plans/test/binding.json")])["release_run_id"] == 41
    assert all(k[1].startswith("runs/41/plans/test/") for k in records)


def test_invalid_binding_never_initializes_remote_backend(run,flow):
    make,calls,records,shown,key=flow
    envelope=json.loads(records[key("plans/test/binding.json")])
    envelope["binding"]["target"]="demo-prod"
    records[key("plans/test/binding.json")]=run.canonical(envelope)
    with pytest.raises(run.DeliveryRefused): make("invalid").apply_and_load("test")
    assert calls==[]


def test_plan_from_another_release_run_is_refused(run,flow):
    make,calls,records,shown,key=flow
    envelope=json.loads(records[key("plans/test/binding.json")])
    records[key("plans/test/binding.json")]=run.canonical(envelope|{"release_run_id":40})
    with pytest.raises(run.DeliveryRefused,match="^plan$"): make("other-run").apply_and_load("test")
    assert calls==[]


def test_schema_request_never_authenticates_an_invalid_host(run,flow):
    make,calls,records,shown,key=flow
    runtime=make("wrong-host"); runtime.settings=replace(runtime.settings,search_service_name="untrusted_example")
    with pytest.raises(run.DeliveryRefused): runtime.apply_and_load("test")
    assert calls==[]


def test_plan_and_apply_originals_survive_distinct_agents(run,flow):
    make,calls,records,shown,key=flow
    records.clear()
    producer=make("producer")
    def command(args,directory,name,seconds):
        if args[1]=="plan": (directory/"plan.tfplan").write_bytes(b"original-plan")
        return SimpleNamespace(stdout=json.dumps(shown),returncode=0)
    producer.command=command
    producer.plan("test")
    assert json.loads(records[key("plans/test/shown.json")])==shown
    consumer=make("consumer")
    consumer.apply_and_load("test")
    assert json.loads(records[key("applies/test/state-before.json")]) is None
    outcome=json.loads(records[key("applies/test/result.json")])
    assert outcome["returncode"]==0 and outcome["binding"]["target"]=="test"
    assert json.loads(records[key("applies/test/intent.json")])["release_run_id"]==41
    assert (consumer.publish/"test-apply/result.json").exists()


def test_apply_timeout_original_publishes_before_agent_exit(run,flow):
    make,calls,records,shown,key=flow
    runtime=make("timeout")
    def timeout(*args,**kw): raise TimeoutError("private original timeout")
    runtime.process=timeout
    with pytest.raises(TimeoutError): runtime.apply_and_load("test")
    original=json.loads(records[key("applies/test/result.json")])
    assert original["returncode"] is None and original["binding"]["target"]=="test"
    assert (runtime.publish/"test-apply/result.json").exists()


def test_upload_deadline_metrics_survive_in_published_stage_directory(run,flow,monkeypatch):
    make,calls,records,shown,key=flow
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


@pytest.mark.parametrize('count', ['0', '\ufeff0'])
def test_new_index_count_access_is_verified_before_bulk_upload(run,flow,monkeypatch,count):
    make,calls,records,shown,key=flow
    runtime=make('empty-count')
    events=[]
    def server(request):
        if request.url.path.endswith('/docs/$count'):
            events.append('count')
            return httpx.Response(200,text=count)
        return httpx.Response(404)
    runtime.client.close()
    runtime.client=httpx.Client(transport=httpx.MockTransport(server))
    def upload(*args,**kwargs):
        assert events==['count']
        events.append('upload')
    monkeypatch.setattr(run,'upload_bundle',upload)
    with runtime.client:
        runtime.apply_and_load('test')
    assert events==['count','upload']


@pytest.mark.parametrize('status,body', [(403,'private response'),(200,'1'),(200,'invalid')])
def test_failed_count_preflight_preserves_apply_result_and_never_uploads(run,flow,monkeypatch,status,body):
    make,calls,records,shown,key=flow
    runtime=make('bad-count')
    runtime.client.close()
    runtime.client=httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(status,text=body) if request.url.path.endswith('/docs/$count') else httpx.Response(404)))
    monkeypatch.setattr(run,'upload_bundle',lambda *a,**kw:pytest.fail('bulk upload started without count readiness'))
    with runtime.client,pytest.raises(run.DeliveryRefused):
        runtime.apply_and_load('test')
    assert json.loads(records[key('applies/test/result.json')])['returncode']==0


def test_expected_negative_requires_no_hits_not_merely_no_correct_answers(run):
    result=SimpleNamespace(passed=False,correct=0,evaluated=229,errors=0,missing=0,skipped=0)
    rows=[SimpleNamespace(hits=[]) for _ in range(229)]
    assert run.negative_passes(result,rows)
    rows[0].hits=[SimpleNamespace(artefact="incorrect")]
    assert not run.negative_passes(result,rows)
