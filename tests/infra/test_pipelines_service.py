"""The per-run search service: create, status and idempotent teardown. az and Terraform are recorded fakes."""
import importlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import httpx
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
STACK_FILES = ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl")
LINEAGE = "00000000-0000-0000-0000-000000000009"
GATE = {"expected":229,"evaluated":229,"correct":200,"errors":0,"missing":0,"skipped":0,"passed":True}


def state(*resources,serial=3):
    return {"version":4,"lineage":LINEAGE,"serial":serial,"resources":[
        {"mode":"managed","type":kind,"name":name,"instances":[{"attributes":{}}]} for kind,name in resources]}


INDEX = state(("azapi_data_plane_resource","index"))
SERVICE = state(("azurerm_search_service","search"))
EMPTIED = state(serial=9)
SHOWN = {"resource_changes":[{"address":"azurerm_search_service.search","mode":"managed","type":"azurerm_search_service",
    "change":{"actions":["create"],"after":{"name":"p8-search-a1b2c3","resource_group_name":"rg-p8-search",
        "sku":"basic","replica_count":1,"partition_count":1}}}]}


@pytest.fixture
def service(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.service")


@pytest.fixture
def harness(service,fake_store,repo_root,tmp_path,monkeypatch):
    """Drives service.main against the fakes: no socket, no az, no Terraform."""
    run = importlib.import_module("scripts.pipelines.run")
    for key,value in ENV.items(): monkeypatch.setenv(key,value)
    # The service stack is Task 5's; a stand-in stack root holds both reviewed stacks and the bundled copies' sources.
    root = tmp_path/"repo"
    for stack in ("index","service"):
        (root/"infra/pipelines"/stack).mkdir(parents=True)
        for name in STACK_FILES: (root/"infra/pipelines"/stack/name).write_text(f"reviewed {stack}/{name}")
        (root/"infra/pipelines"/stack/"terraform.tfvars").write_text("unreviewed")
    bundle_root = tmp_path/"bundle"; bundle_root.mkdir()
    for bundled,source in run.BUNDLED_COPIES:
        (root/source).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(repo_root/source,root/source); shutil.copyfile(repo_root/source,bundle_root/bundled)
    (bundle_root/"schema.json").write_text("{}")
    monkeypatch.setattr(run,"ROOT",root)
    bundle = SimpleNamespace(root=bundle_root,digest=DIGEST,manifest=SimpleNamespace(source_sha="c"*40))
    h = SimpleNamespace(store=fake_store,calls=[],present=True,present_error=False,failing=set(),shown=SHOWN,
        output=SEARCH_HOST,principals=[],fetched=[],bundle=bundle)
    def process(args,**kw):
        args = list(args)
        h.calls.append(SimpleNamespace(argv=args,cwd=Path(kw["cwd"]) if kw.get("cwd") else None,env=kw.get("env")))
        if args[:2] == ["git","rev-parse"]: return SimpleNamespace(returncode=0,stdout="a"*40+"\n",stderr="")
        if args[:2] == ["git","diff"]: return SimpleNamespace(returncode=0,stdout="",stderr="")
        if args[:4] == ["az","search","service","show"]:
            if h.present_error: return SimpleNamespace(returncode=1,stdout="",stderr="(AuthorizationFailed) private detail")
            if h.present: return SimpleNamespace(returncode=0,stdout="{\"name\":\"private\"}",stderr="")
            return SimpleNamespace(returncode=3,stdout="",stderr="(ResourceNotFound) private detail")
        if args[0] == "terraform":
            where = Path(kw["cwd"]).name
            if (args[1],where) in h.failing: return SimpleNamespace(returncode=1,stdout="private",stderr="private")
            if args[1] == "show": return SimpleNamespace(returncode=0,stdout=json.dumps(h.shown),stderr="")
            if args[1] == "output": return SimpleNamespace(returncode=0,stdout=json.dumps(h.output),stderr="")
            if args[1] == "destroy" and where == "service": h.present = False
            return SimpleNamespace(returncode=0,stdout="private terraform output",stderr="private terraform error")
        pytest.fail(f"unexpected process {args[:2]}")
    monkeypatch.setattr(run.subprocess,"run",process)
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
    monkeypatch.setattr(service,"authenticate",authenticate)
    monkeypatch.setattr(service,"fetch_bundle",fetch)
    monkeypatch.setattr(service,"BlobStore",blob_store)
    monkeypatch.setattr(service,"TokenSource",lambda scope,tenant:SimpleNamespace(token=lambda:"synthetic"))
    monkeypatch.setattr(service,"http_client",lambda:httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail("network"))))
    def main(command,*,reason="Manual",build_id="41",reuse_home=False):
        monkeypatch.setenv("BUILD_REASON",reason); monkeypatch.setenv("BUILD_BUILDID",build_id)
        # Every job normally runs on a fresh hosted agent, with its own home directory.
        if not reuse_home: h.home = tmp_path/"home"/(command+"-"+build_id); h.home.mkdir(parents=True)
        monkeypatch.setenv("HOME",str(h.home)); monkeypatch.setenv("USERPROFILE",str(h.home))
        h.publish = tmp_path/"publish"/(command+"-"+build_id)
        return service.main([command,"--receipt-dir",str(h.publish)])
    def terraform(): return [c for c in h.calls if c.argv[0] == "terraform"]
    def verbs(): return [(c.argv[1] if c.argv[1] != "state" else "state-rm",c.cwd.name) for c in terraform()]
    def az(): return [c for c in h.calls if c.argv[0] == "az"]
    def failure(): return json.loads((h.publish/"failure.json").read_bytes())
    def receipts(release_run_id=41,prod=None,negative=None):
        fake_store.write_pointer({"release_run_id":release_run_id,"release_digest":DIGEST,"source_sha":"e"*40})
        fake_store.pointer_writes.clear()
        proof = {"source_sha":"e"*40,"release_digest":DIGEST,"release_run_id":release_run_id}
        if prod is not False:
            fake_store.put_json(f"runs/{release_run_id}/receipts/prod.json",proof|{"stage":"prod","gate":GATE,
                "expected_negative":False,"acceptance_pass":True}|(prod or {}))
        if negative is not False:
            fake_store.put_json(f"runs/{release_run_id}/receipts/negative.json",proof|{"stage":"negative",
                "gate":GATE|{"correct":0,"passed":False},"expected_negative":True,"acceptance_pass":True,
                "prod_unchanged":True}|(negative or {}))
    h.main,h.terraform,h.verbs,h.az,h.failure,h.receipts = main,terraform,verbs,az,failure,receipts
    return h


def role(name): return PRINCIPALS[name]["client_id"]


# service_present: az answers present, absent, or unknown.

def test_service_present_classifies_the_cli_answer(service):
    settings = importlib.import_module("scripts.pipelines.settings").load_settings(SETTINGS)
    seen = []
    def answer(returncode,stderr=""):
        def run(args,**kw):
            seen.append(list(args))
            return SimpleNamespace(returncode=returncode,stdout="",stderr=stderr)
        return run
    assert service.service_present(settings,answer(0)) is True
    assert seen[0] == ["az","search","service","show","--name","p8-search-a1b2c3","--resource-group","rg-p8-search"]
    assert service.service_present(settings,answer(3,"ERROR: (ResourceNotFound) private")) is False
    assert service.service_present(settings,answer(3,"ERROR: (ResourceGroupNotFound) private")) is False
    for stderr in ("ERROR: (AuthorizationFailed) private","",None):
        with pytest.raises(service.DeliveryRefused,match="^request$"): service.service_present(settings,answer(1,stderr))
    def missing(args,**kw): raise FileNotFoundError("az")
    with pytest.raises(service.DeliveryRefused,match="^request$"): service.service_present(settings,missing)


# create-service: a new release never adopts or replaces a service.

def test_create_refuses_when_service_present(harness):
    assert harness.main("create-service") == 2
    assert harness.terraform() == [] and len(harness.az()) == 1
    assert harness.failure()["code"] == "reserved"


def test_create_refuses_when_service_state_not_empty(harness):
    harness.present = False
    harness.store.states["service"] = SERVICE
    assert harness.main("create-service") == 2
    assert harness.terraform() == [] and harness.failure()["code"] == "reserved"


def test_create_runs_init_plan_apply_in_order(harness,capsys):
    harness.present = False
    assert harness.main("create-service") == 0
    assert harness.verbs() == [("init","service"),("plan","service"),("show","service"),("apply","service"),("output","service")]
    init,plan,show,apply,output = harness.terraform()
    assert "-lockfile=readonly" in init.argv
    assert "-backend-config=container_name=service-state" in init.argv and "-backend-config=key=service.tfstate" in init.argv
    assert "-backend-config=storage_account_name=p8a1b2c3" in init.argv
    assert plan.argv == ["terraform","plan","-input=false","-out=service.tfplan","-var-file=inputs.tfvars.json"]
    assert apply.argv == ["terraform","apply","-input=false","service.tfplan"]
    assert init.cwd == harness.home/"p8"/"create-service"/"service" and all(c.cwd == init.cwd for c in harness.terraform())
    assert all(c.env == {"TF_IN_AUTOMATION":"true"} for c in harness.terraform())
    assert json.loads((init.cwd/"inputs.tfvars.json").read_bytes()) == {"search_service_name":"p8-search-a1b2c3",
        "resource_group_name":"rg-p8-search","location":"australiaeast",
        "subscription_id":SETTINGS["P8_SUBSCRIPTION_ID"],"tenant_id":SETTINGS["P8_TENANT_ID"]}
    assert {p.name for p in init.cwd.iterdir()} >= set(STACK_FILES) and not (init.cwd/"terraform.tfvars").exists()
    assert (init.cwd/"main.tf").read_text() == "reviewed service/main.tf"
    assert [p["client_id"] for p in harness.principals] == [role("test")]
    out = capsys.readouterr().out
    assert out.strip() == "status=pass code=stage" and "private" not in out


def test_create_accepts_the_state_a_teardown_leaves(harness):
    harness.present = False
    harness.store.states["service"] = EMPTIED
    assert harness.main("create-service") == 0
    assert ("apply","service") in harness.verbs()


@pytest.mark.parametrize("change",[{"actions":["delete","create"]},{"name":"other"},{"resource_group_name":"rg-other"},
    {"type":"azurerm_storage_account"},{"address":"azurerm_search_service.other"},{"sku":"standard"},{"sku":"free"},
    {"replica_count":2},{"partition_count":2},{"replica_count":None},{"partition_count":"1"},"no-sku","extra"])
def test_create_never_applies_a_plan_other_than_one_new_service(harness,change):
    harness.present = False
    row = json.loads(json.dumps(SHOWN["resource_changes"][0]))
    if change == "extra": harness.shown = {"resource_changes":[row,row]}
    else:
        if change == "no-sku": del row["change"]["after"]["sku"]
        elif "actions" in change: row["change"] |= change
        elif "type" in change or "address" in change: row |= change
        else: row["change"]["after"] |= change
        harness.shown = {"resource_changes":[row]}
    assert harness.main("create-service") == 2
    assert ("apply","service") not in harness.verbs() and harness.failure()["code"] == "plan"


def test_create_refuses_when_the_service_host_differs(harness):
    harness.present = False
    harness.output = "other.search.windows.net"
    assert harness.main("create-service") == 2
    assert harness.failure()["code"] == "target"


def test_schedule_never_creates(harness):
    harness.present = False
    assert harness.main("create-service",reason="Schedule") == 2
    assert harness.failure()["code"] == "authority" and harness.calls == [] and harness.principals == []


# status: absent, incomplete or ready; read-only, exit 0 for all three.

def status_line(out):
    lines = [line for line in out.splitlines() if line.startswith("##vso[")]
    assert len(lines) == 1
    prefix = "##vso[task.setvariable variable=P8Status;isOutput=true]"
    assert lines[0].startswith(prefix)
    return lines[0][len(prefix):]


def run_status(harness,capsys):
    before = dict(harness.store.blobs)
    assert harness.main("status",reason="Schedule",build_id="60") == 0
    assert harness.store.blobs == before and harness.store.pointer_writes == [] and harness.terraform() == []
    assert [p["client_id"] for p in harness.principals] == [role("verify")]
    out = capsys.readouterr().out
    assert "private" not in out
    return status_line(out)


def test_status_absent(harness,capsys):
    harness.present = False
    harness.receipts()
    assert run_status(harness,capsys) == "absent"


def test_status_ready(harness,capsys):
    harness.receipts()
    assert run_status(harness,capsys) == "ready"


def test_status_incomplete_when_prod_receipt_missing(harness,capsys):
    harness.receipts(prod=False,negative=False)
    assert run_status(harness,capsys) == "incomplete"


def test_status_incomplete_when_negative_missing(harness,capsys):
    harness.receipts(negative=False)
    assert run_status(harness,capsys) == "incomplete"


def test_status_incomplete_when_pointer_absent(harness,capsys):
    assert run_status(harness,capsys) == "incomplete"


@pytest.mark.parametrize("prod,negative",[({"gate":GATE|{"correct":119}},None),({"release_run_id":40},None),
    ({"release_digest":"f"*64},None),(None,{"acceptance_pass":False}),(None,{"expected_negative":False}),
    (None,{"release_run_id":40})])
def test_status_incomplete_when_evidence_does_not_match(harness,capsys,prod,negative):
    harness.receipts(prod=prod,negative=negative)
    assert run_status(harness,capsys) == "incomplete"


def test_status_incomplete_when_pointer_names_another_release(harness,capsys):
    harness.receipts()
    harness.store.write_pointer({"release_run_id":41,"release_digest":"f"*64,"source_sha":"e"*40})
    harness.store.pointer_writes.clear()
    assert run_status(harness,capsys) == "incomplete"


def test_status_refuses_when_presence_is_unknown(harness,capsys):
    harness.present_error = True
    harness.receipts()
    assert harness.main("status",reason="Schedule",build_id="60") == 2
    out = capsys.readouterr().out
    assert "##vso" not in out and "private" not in out and out.strip() == "status=refused code=request"
    assert harness.failure() == {"kind":"stage-failure","stage":"status","code":"request","check":"unclassified"}


def test_status_is_the_scheduled_runs_alone(harness,capsys):
    assert harness.main("status",reason="Manual") == 2
    assert harness.failure()["code"] == "authority" and harness.calls == []
    assert "##vso" not in capsys.readouterr().out


# Teardown: idempotent, each identity its own state, the service last.

def test_teardown_prod_destroys_only_demo_prod(harness):
    harness.store.states["demo-prod"] = INDEX
    harness.store.states["test"] = INDEX
    harness.store.states["service"] = SERVICE
    assert harness.main("teardown-prod") == 0
    assert harness.verbs() == [("init","index-demo-prod"),("destroy","index-demo-prod")]
    init,destroy = harness.terraform()
    assert "-backend-config=container_name=prod-state" in init.argv and "-lockfile=readonly" in init.argv
    everything = " ".join(" ".join(c.argv) for c in harness.calls)
    assert "test-state" not in everything and "service-state" not in everything
    assert destroy.argv == ["terraform","destroy","-auto-approve","-input=false","-var-file=inputs.tfvars.json"]
    assert json.loads((destroy.cwd/"inputs.tfvars.json").read_bytes()) == {"subscription_id":SETTINGS["P8_SUBSCRIPTION_ID"],
        "tenant_id":SETTINGS["P8_TENANT_ID"],"target":"demo-prod","search_host":SEARCH_HOST,
        "schema_file":str(harness.bundle.root/"schema.json")}
    assert harness.fetched == [(f"https://{STORAGE_HOST}/inputs/releases/{DIGEST}/release.tar.gz",DIGEST,STORAGE_HOST)]
    assert [p["client_id"] for p in harness.principals] == [role("prod")]
    assert harness.present is True


def test_teardown_test_destroys_index_then_service(harness):
    harness.store.states["test"] = INDEX
    harness.store.states["service"] = SERVICE
    assert harness.main("teardown-test",reason="Schedule",build_id="61") == 0
    assert harness.verbs() == [("init","index-test"),("destroy","index-test"),("init","service"),("destroy","service")]
    init_index,destroy_index,init_service,destroy_service = harness.terraform()
    assert "-backend-config=container_name=test-state" in init_index.argv
    assert "-backend-config=container_name=service-state" in init_service.argv
    assert "prod-state" not in " ".join(" ".join(c.argv) for c in harness.calls)
    assert json.loads((destroy_service.cwd/"inputs.tfvars.json").read_bytes())["search_service_name"] == "p8-search-a1b2c3"
    assert json.loads((destroy_index.cwd/"inputs.tfvars.json").read_bytes())["target"] == "test"
    # Presence is read once before the destroys, and the service is confirmed gone after them.
    shows = [i for i,c in enumerate(harness.calls) if c.argv[0] == "az"]
    destroyed = next(i for i,c in enumerate(harness.calls) if c is destroy_service)
    assert len(shows) == 2 and shows[0] < destroyed < shows[1]
    assert [p["client_id"] for p in harness.principals] == [role("test")]


@pytest.mark.parametrize("command",["teardown-prod","teardown-test"])
@pytest.mark.parametrize("held",[None,EMPTIED])
def test_teardown_is_noop_when_states_empty(harness,command,held):
    harness.present = False
    for target in ("test","demo-prod","service"): harness.store.states[target] = held
    assert harness.main(command) == 0
    assert harness.terraform() == [] and harness.fetched == []


@pytest.mark.parametrize("command,target",[("teardown-prod","demo-prod"),("teardown-test","test")])
def test_teardown_clears_index_state_when_service_absent(harness,command,target):
    harness.present = False
    harness.store.states[target] = INDEX
    assert harness.main(command) == 0
    assert harness.verbs() == [("init","index-"+target),("state-rm","index-"+target)]
    assert harness.terraform()[-1].argv == ["terraform","state","rm","azapi_data_plane_resource.index"]
    assert not [c for c in harness.terraform() if c.argv[1] == "destroy"] and harness.fetched == []
    # Only Terraform changes state; nothing outside evidence is ever written.
    assert all(container == "evidence" for container,key in harness.store.blobs)


def test_teardown_test_destroys_service_even_when_index_destroy_fails(harness):
    harness.store.states["test"] = INDEX
    harness.store.states["service"] = SERVICE
    harness.failing.add(("destroy","index-test"))
    assert harness.main("teardown-test") == 2
    # Once the service is confirmed gone, the stale index state is cleared in the same run; it still ends red.
    assert harness.verbs() == [("init","index-test"),("destroy","index-test"),("init","service"),("destroy","service"),
        ("state-rm","index-test")]
    shows = [i for i,c in enumerate(harness.calls) if c.argv[0] == "az"]
    assert len(shows) == 2 and shows[1] < len(harness.calls)-1 and harness.present is False
    assert harness.terraform()[-1].argv == ["terraform","state","rm","azapi_data_plane_resource.index"]
    assert harness.failure()["code"] == "cleanup"
    receipt = harness.store.evidence("teardowns/41/teardown-test.json")
    assert receipt["actions"] == {"test":"failed","service":"destroyed","test-state":"state-removed"}
    assert receipt["outcome"] == "failed"


def test_teardown_test_keeps_the_index_state_while_the_service_remains(harness):
    harness.store.states["test"] = INDEX
    harness.failing.add(("destroy","index-test"))
    assert harness.main("teardown-test") == 2
    assert harness.verbs() == [("init","index-test"),("destroy","index-test")] and harness.present is True


def test_both_failed_destroys_stay_in_the_private_traceback(harness,capsys):
    harness.store.states["test"] = INDEX
    harness.store.states["service"] = SERVICE
    harness.failing |= {("destroy","index-test"),("destroy","service")}
    assert harness.main("teardown-test") == 2
    assert harness.verbs() == [("init","index-test"),("destroy","index-test"),("init","service"),("destroy","service")]
    private = (harness.publish.parent/"failure-private.txt").read_text(encoding="utf-8")
    assert "direct cause" in private and ", in teardown_index" in private and ", in teardown_service" in private
    assert private.index(", in teardown_index") < private.index("direct cause") < private.index(", in teardown_service")
    assert harness.failure() == {"kind":"stage-failure","stage":"teardown-test","code":"cleanup","check":"unclassified"}
    assert "private" not in capsys.readouterr().out


def test_lifecycle_commands_in_one_home_never_collide(harness):
    harness.present = False
    assert harness.main("create-service") == 0
    harness.present = True
    harness.store.states["service"] = SERVICE
    assert harness.main("teardown-prod",reuse_home=True) == 0
    assert harness.main("teardown-test",reuse_home=True) == 0
    services = {c.cwd for c in harness.terraform() if c.cwd.name == "service"}
    assert services == {harness.home/"p8"/"create-service"/"service",harness.home/"p8"/"teardown-test"/"service"}


def test_teardown_test_refuses_when_the_service_outlives_its_state(harness):
    assert harness.main("teardown-test") == 2
    assert harness.terraform() == [] and harness.failure()["code"] == "cleanup"


def test_teardown_receipts_never_collide_and_never_block(harness,capsys):
    harness.present = False
    harness.store.states["demo-prod"] = INDEX
    assert harness.main("teardown-prod",build_id="61") == 0
    receipt = harness.store.evidence("teardowns/61/teardown-prod.json")
    assert receipt["actions"] == {"demo-prod":"state-removed"} and receipt["outcome"] == "pass"
    assert receipt["service_present"] is False and receipt["build_id"] == 61
    assert json.loads((harness.publish/"teardown.json").read_bytes()) == receipt
    assert harness.main("teardown-prod",build_id="62") == 0
    assert ("evidence","teardowns/62/teardown-prod.json") in harness.store.blobs
    # A failed evidence write is published, and never stops the teardown or turns it red.
    contracts = importlib.import_module("app.delivery_search.contracts")
    def refused(*args): raise contracts.RequestRefused("blob-create-http",403)
    harness.store.put_once = refused
    before = len(harness.terraform())
    assert harness.main("teardown-prod",build_id="63") == 0
    assert len(harness.terraform()) == before+2
    assert json.loads((harness.publish/"evidence-failure.json").read_bytes()) == {
        "kind":"evidence-failure","stage":"teardown-prod","code":"request"}
    assert "private" not in capsys.readouterr().out


def test_teardown_refuses_without_known_presence(harness):
    harness.present_error = True
    harness.store.states["demo-prod"] = INDEX
    assert harness.main("teardown-prod") == 2
    assert harness.terraform() == [] and harness.failure()["code"] == "request"


@pytest.mark.parametrize("reason",["IndividualCI","PullRequest",""])
def test_commands_refuse_other_build_reasons(harness,reason):
    for command in ("create-service","status","teardown-prod","teardown-test"):
        assert harness.main(command,reason=reason) == 2
        assert harness.failure()["code"] == "authority"
    assert harness.calls == []


def test_commands_require_main(harness,monkeypatch):
    monkeypatch.setenv("BUILD_SOURCEBRANCH","refs/heads/feature")
    assert harness.main("teardown-test") == 2
    assert harness.failure()["code"] == "authority" and harness.principals == []
