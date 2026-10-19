from test_pipelines_metadata_policy import POLICY
"""Fresh authority, durable reservations and the independent owner clock."""
from dataclasses import asdict
from datetime import datetime,timezone,timedelta
import importlib
import json

import pytest


@pytest.fixture
def session_module(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.session")


def authority(start="2026-10-16T08:00:00+00:00"):
    t = datetime.fromisoformat(start)
    return {"session":"p8-1016x","source_sha":"a"*40,"tree_sha":"b"*40,"release_digest":"c"*64,
        "apply_started_at":start,"queue_cutoff_at":(t+timedelta(minutes=75)).isoformat(),
        "cleanup_due_at":(t+timedelta(minutes=78)).isoformat(),
        "cleanup_start_by":(t+timedelta(minutes=80)).isoformat(),"closeout_by":(t+timedelta(minutes=120)).isoformat(),
        "manifest_sha256":"d"*64}


def test_deadlines_and_no_apply(session_module):
    raw = authority()
    a = session_module.SessionAuthority(**raw)
    assert a.validate() is None
    assert not a.cleanup_due(datetime.fromisoformat(raw["cleanup_due_at"])-timedelta(seconds=1))
    assert a.cleanup_due(datetime.fromisoformat(raw["cleanup_due_at"]))
    raw["apply_started_at"] = None
    assert not session_module.SessionAuthority(**raw).cleanup_due(datetime(2027,1,1,tzinfo=timezone.utc))
    raw = authority(); raw["closeout_by"] = "2026-10-16T11:00:00+00:00"
    with pytest.raises(Exception): session_module.SessionAuthority(**raw).validate()


def test_one_reservation_survives_restart(session_module,tmp_path):
    path = session_module.reserve_operation(tmp_path,"queries-test","a"*64)
    first = path.read_bytes()
    with pytest.raises(Exception): session_module.reserve_operation(tmp_path,"queries-test","a"*64)
    with pytest.raises(Exception): session_module.reserve_operation(tmp_path,"queries-test","b"*64)
    assert path.read_bytes() == first
    for i in range(3): session_module.reserve_budget(tmp_path,"metadata",3)
    with pytest.raises(Exception): session_module.reserve_budget(tmp_path,"metadata",3)


def test_duplicate_owner_lock_refuses(session_module,tmp_path):
    with session_module.operation_lock(tmp_path):
        with pytest.raises(Exception):
            with session_module.operation_lock(tmp_path): pytest.fail("second cleanup entered")


def test_watchdog_handoff_and_exit(session_module,tmp_path):
    watchdog = importlib.import_module("scripts.pipelines.watchdog")
    command = watchdog.wsl_command("C:/Users/Owner/Project With Spaces/.local/project8/p8-1016x")
    assert command[:3] == ["wsl.exe","--distribution","Ubuntu"]
    assert "--session" in command and not any("\\" in word for word in command[3:])
    expected = {"pid":123,"created":"fresh","command_line":"python exact-owned.py","sha256":"a"*64}
    assert watchdog.same_process(expected,dict(expected))
    assert not watchdog.same_process(expected,dict(expected,created="pid-reused"))
    assert not watchdog.same_process(expected,None)


def test_cutoff_preparation_is_early_durable_and_cannot_request_cleanup(session_module,tmp_path,monkeypatch):
    watchdog=importlib.import_module("scripts.pipelines.watchdog")
    quiescence=importlib.import_module("scripts.pipelines.quiescence")
    a=session_module.SessionAuthority(**authority())
    calls=[]
    monkeypatch.setattr(quiescence,"request",lambda *args:calls.append("request"))
    cutoff=session_module.instant(a.queue_cutoff_at)
    path=tmp_path/"quiescence-preparation.json"
    watchdog.cutoff_handoff(tmp_path,a,{"pipeline_id":2},cutoff-timedelta(minutes=10,seconds=1))
    assert not path.exists() and calls==[]
    watchdog.cutoff_handoff(tmp_path,a,{"pipeline_id":2},cutoff-timedelta(minutes=10))
    notice=json.loads(path.read_bytes())
    assert notice["session"]==a.session and notice["source_sha"]==a.source_sha
    assert notice["queue_cutoff_at"]==a.queue_cutoff_at and notice["cleanup_due_at"]==a.cleanup_due_at
    assert notice["acknowledge_by"]==(session_module.instant(a.cleanup_due_at)-timedelta(seconds=45)).isoformat()
    assert notice["actions"]==["prepare-owner-browser-restoration"] and calls==[]
    original=path.read_bytes()
    watchdog.cutoff_handoff(tmp_path,a,{"pipeline_id":2},cutoff-timedelta(minutes=1))
    assert path.read_bytes()==original and calls==[]
    assert not (tmp_path/"quiescence/request.json").exists()
    watchdog.cutoff_handoff(tmp_path,a,{"pipeline_id":2},cutoff)
    assert calls==["request"] and path.read_bytes()==original


def test_preparation_io_failure_cannot_prevent_original_cutoff_request(session_module,tmp_path,monkeypatch):
    watchdog=importlib.import_module("scripts.pipelines.watchdog")
    quiescence=importlib.import_module("scripts.pipelines.quiescence")
    a=session_module.SessionAuthority(**authority());calls=[]
    monkeypatch.setattr(quiescence,"request",lambda *args:calls.append("request"))
    def broken(*args): raise OSError("simulated notice write failure")
    monkeypatch.setattr(watchdog,"prepare_handoff",broken)
    watchdog.cutoff_handoff(tmp_path,a,{"pipeline_id":2},session_module.instant(a.queue_cutoff_at))
    assert calls==["request"]


def test_dispatch_reserves_all_queries_before_browser_queue(session_module,tmp_path,monkeypatch):
    raw=authority(); a=session_module.SessionAuthority(**raw)
    monkeypatch.setattr(session_module,"load",lambda directory:(a,{},{}))
    monkeypatch.setattr(session_module,"verify_source",lambda authority:None)
    now=datetime.fromisoformat(raw["apply_started_at"])+timedelta(minutes=20)
    session_module.reserve_dispatch(tmp_path,"release",now)
    assert (tmp_path/"intents/queries-test.json").exists() and (tmp_path/"intents/queries-prod.json").exists()
    with pytest.raises(Exception): session_module.reserve_dispatch(tmp_path,"release",now)
    with pytest.raises(Exception): session_module.reserve_dispatch(tmp_path,"negative",datetime.fromisoformat(raw["queue_cutoff_at"]))


def test_manifest_allows_nine_arm_plus_local_gate(session_module,tmp_path):
    source="/subscriptions/sub/resourceGroups/rg-p8-1016x-agents"
    a={"session":"p8-1016x","owner":{"subscription_id":"sub"},"search_host":"owned.search.windows.net",
        "owned_group_ids":[source,source.replace("agents","search"),source.replace("agents","identity")]}
    components=[{"name":name,"directory":"tf/"+name,"config_sha256":"a"*64,"resources":[]} for name in (
        "index-test","index-demo-prod","foundation","identity","agents")]
    components[-1]["resources"]=[{"address":"owned."+str(i),"id":source+"/providers/Owned/resources/"+str(i),
        "kind":"arm","api_version":"2024-01-01"} for i in range(9)]+[
        {"address":"terraform_data.pat_ready","id":"local","kind":"local"}]
    session_module.validate_manifest({"components":components},a,tmp_path)


def test_preapply_watchdog_adopts_actual_start_but_never_resets_it(session_module):
    watchdog=importlib.import_module("scripts.pipelines.watchdog")
    current=authority(); actual=session_module.SessionAuthority(**current)
    prepared=session_module.SessionAuthority(**dict(current,apply_started_at=None))
    assert watchdog.adopt_authority(prepared,actual)==actual
    moved=authority("2026-10-16T08:10:00+00:00")
    with pytest.raises(Exception): watchdog.adopt_authority(actual,session_module.SessionAuthority(**moved))


def test_human_receipt_binds_actual_scope_and_absence_inventory(session_module,tmp_path):
    import hashlib
    raw=authority()
    prefix="/subscriptions/sub/resourceGroups/rg-p8-1016x-"
    raw.update({"metadata_policy":dict(POLICY),"owner":{"subscription_id":"sub","tenant_id":"tenant","object_id":"owner"},
        "owner_ado_id":"11111111-2222-4333-8444-555555555555",
        "owned_group_ids":[prefix+name for name in ("search","identity","agents")],
        "principals":{},"scopes":{},"pipeline_id":2,"search_host":"owned.search.windows.net",
        "storage_host":"owned.blob.core.windows.net"})
    manifest={k:raw[k] for k in ("session","source_sha","tree_sha")}
    manifest["components"]=[{"name":name,"directory":"tf/"+name,"config_sha256":"a"*64,"resources":[]} for name in (
        "index-test","index-demo-prod","foundation","identity","agents")]
    baseline={k:raw[k] for k in ("session","source_sha","tree_sha")}
    baseline.update({"observed_at":"2026-10-16T07:59:00+00:00","canonical_absent":True,
        "absence":[{"id":group,"status":404} for group in raw["owned_group_ids"]],
        "retained":{"resources":[],"roleAssignments":[],"deletedVaults":[]}})
    receipt={k:raw[k] for k in ("session","source_sha","tree_sha","release_digest")}
    raw["manifest_sha256"]=hashlib.sha256(json.dumps(manifest).encode()).hexdigest()
    raw["baseline_sha256"]=hashlib.sha256(json.dumps(baseline).encode()).hexdigest()
    receipt.update({"kind":"human-live-approval","message_ref":"test-only-receipt",
        "scope_sha256":session_module.scope_digest(raw)})
    for filename,value,key in (("cleanup-manifest.json",manifest,"manifest_sha256"),
        ("baseline.json",baseline,"baseline_sha256"),("owner-approval-receipt.json",receipt,"approval_receipt_sha256")):
        data=json.dumps(value).encode(); (tmp_path/filename).write_bytes(data); raw[key]=hashlib.sha256(data).hexdigest()
    (tmp_path/"authority.json").write_text(json.dumps(raw))
    session_module.load(tmp_path)
    manifest["components"][2]["resources"]=[{"address":"azurerm_resource_group.extra","id":raw["owned_group_ids"][0],
        "kind":"arm","api_version":"2021-04-01"}]
    data=json.dumps(manifest).encode(); (tmp_path/"cleanup-manifest.json").write_bytes(data)
    changed=dict(raw,manifest_sha256=hashlib.sha256(data).hexdigest())
    (tmp_path/"authority.json").write_text(json.dumps(changed))
    with pytest.raises(Exception): session_module.load(tmp_path)
    manifest["components"][2]["resources"]=[]
    (tmp_path/"cleanup-manifest.json").write_bytes(json.dumps(manifest).encode())
    raw["owner"]["object_id"]="different-owner"
    (tmp_path/"authority.json").write_text(json.dumps(raw))
    with pytest.raises(Exception): session_module.load(tmp_path)


def test_watchdog_never_adopts_a_replacement_ownership_manifest(session_module):
    from dataclasses import replace
    watchdog=importlib.import_module("scripts.pipelines.watchdog")
    original=session_module.SessionAuthority(**authority())
    with pytest.raises(Exception): watchdog.adopt_authority(original,replace(original,manifest_sha256="e"*64))
