"""Delete-only continuation cannot extend the immutable original ownership set."""
import importlib
from datetime import datetime,timezone,timedelta

import pytest


@pytest.fixture
def cleanup(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.cleanup")


def test_failed_gate_is_not_stop(cleanup):
    now = datetime(2026,10,16,8,20,tzinfo=timezone.utc)
    assert not cleanup.early_allowed({"failed_gate":True},"a"*40,"p8-1016x")
    assert not cleanup.early_allowed({"kind":"heartbeat","message_ref":"fake"},"a"*40,"p8-1016x")
    assert cleanup.early_allowed({"kind":"human-explicit-stop","message_ref":"real-owner-message",
        "source_sha":"a"*40,"session":"p8-1016x"},"a"*40,"p8-1016x")


def test_timeout_remaining_identity_only(cleanup):
    original = [{"address":"azurerm_resource_group.owned","id":"/subscriptions/s/resourceGroups/owned"},
        {"address":"azurerm_search_service.owned","id":"/subscriptions/s/resourceGroups/owned/providers/Microsoft.Search/searchServices/owned"}]
    remaining = [original[0]]
    plan = {"resource_changes":[{"mode":"managed","address":original[0]["address"],
        "change":{"actions":["delete"],"before":{"id":original[0]["id"]},"after":None}}]}
    cleanup.validate_remaining_delete(plan,original,remaining)
    for bad in [dict(plan,resource_changes=plan["resource_changes"]*2),
                {"resource_changes":[dict(plan["resource_changes"][0],change={"actions":["update"],"before":{"id":original[0]["id"]}})]},
                {"resource_changes":[dict(plan["resource_changes"][0],address=original[1]["address"])]}]:
        with pytest.raises(Exception): cleanup.validate_remaining_delete(bad,original,remaining)
    with pytest.raises(Exception): cleanup.validate_remaining_delete(plan,original,[dict(original[0],id="unowned")])


def test_retained_inventory_complete(cleanup):
    original = {"resources":[{"id":"retained-1","type":"full"}],"roleAssignments":[],
                "deletedVaults":[{"id":"protected","properties":{"scheduledPurgeDate":"original"}}]}
    assert cleanup.retained_unchanged(original,original)
    assert not cleanup.retained_unchanged(original,dict(original,resources=[]))
    assert not cleanup.retained_unchanged(original,dict(original,resources=original["resources"]+[{"id":"extra"}]))
    assert not cleanup.retained_unchanged(original,dict(original,deletedVaults=[]))


def test_orphan_provider_blocks_new_cleanup(cleanup,tmp_path):
    class Driver:
        def active_owned(self): return [{"kind":"provider","pid":43}]
    with pytest.raises(Exception,match="reserved"): cleanup.require_idle(Driver())


def test_timeout_continuation_uses_a_fresh_remaining_plan(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines.session import SessionAuthority,exclusive_json
    start = datetime(2026,10,16,8,tzinfo=timezone.utc)
    a = SessionAuthority("p8-1016x","a"*40,"b"*40,"c"*64,start.isoformat(),
        *[(start+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],"d"*64)
    rows = [{"address":"owned.first","id":"first"},{"address":"owned.second","id":"second"}]
    component = {"name":"foundation","resources":rows}
    manifest = {"components":[component]}
    baseline = {"retained":{"resources":[],"roleAssignments":[],"deletedVaults":[]}}
    exclusive_json(tmp_path/"baseline.json",baseline)
    monkeypatch.setattr(cleanup,"load",lambda directory:(a,{},manifest))
    class Driver:
        def __init__(self): self.rows=list(rows); self.plans=[]; self.applies=0
        def verify_owner(self): pass
        def active_owned(self): return []
        def state(self,component): return list(self.rows)
        def plan(self,component,attempt,remaining):
            self.plans.append((attempt,list(remaining)))
            return {"resource_changes":[{"mode":"managed","address":row["address"],
                "change":{"actions":["delete"],"before":{"id":row["id"]},"after":None}} for row in remaining]}
        def apply(self,component,attempt):
            self.applies += 1
            if self.applies == 1:
                self.rows.pop(0)
                raise TimeoutError("private original timeout")
            self.rows=[]
        def canonical_absence(self): return True
        def retained(self): return baseline["retained"]
    driver=Driver()
    first=cleanup.execute(tmp_path,start+timedelta(minutes=78),driver=driver)
    assert first["physical_pass"] is False
    second=cleanup.execute(tmp_path,start+timedelta(minutes=79),driver=driver)
    assert second["physical_pass"] is True
    assert driver.plans == [(1,rows),(2,[rows[1]])]
    assert (tmp_path/"cleanup-attempt-1.json").exists()


def test_no_apply_cleanup_does_not_touch_driver(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines.session import SessionAuthority
    now=datetime.now(timezone.utc)
    a=SessionAuthority("p8-1016x","a"*40,"b"*40,"c"*64,None,*[now.isoformat()]*4,"d"*64)
    monkeypatch.setattr(cleanup,"load",lambda directory:(a,{},{}))
    class Driver:
        def verify_owner(self): pytest.fail("preparation was interrupted")
    assert cleanup.execute(tmp_path,now,driver=Driver())["status"] == "no-apply"


def test_failed_gate_retains_resources_before_due(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines.session import SessionAuthority,exclusive_json
    now=datetime(2026,10,16,8,tzinfo=timezone.utc)
    a=SessionAuthority("p8-1016x","a"*40,"b"*40,"c"*64,now.isoformat(),
        *[(now+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],"d"*64)
    monkeypatch.setattr(cleanup,"load",lambda directory:(a,{},{}))
    exclusive_json(tmp_path/"stopped-gate.json",{"failed_gate":True})
    class Driver:
        def verify_owner(self): pytest.fail("failure is not early cleanup authority")
    assert cleanup.execute(tmp_path,now+timedelta(minutes=30),driver=Driver())["status"]=="pending"


def test_refresh_confirmed_absence_can_remove_only_original_state(cleanup):
    row={"address":"owned.first","id":"original"}
    drift={"mode":"managed","address":row["address"],"change":{"actions":["delete"],
        "before":{"id":row["id"]},"after":None}}
    cleanup.validate_remaining_delete({"resource_changes":[],"resource_drift":[drift]},[row],[row])
    with pytest.raises(Exception): cleanup.validate_remaining_delete({"resource_changes":[],
        "resource_drift":[dict(drift,address="unowned")]},[row],[row])


def test_read_only_drift_observation_does_not_authorize_update(cleanup):
    row={"address":"owned.first","id":"original"}
    delete={"mode":"managed","address":row["address"],"change":{"actions":["delete"],
        "before":{"id":row["id"]},"after":None}}
    drift=dict(delete,change={"actions":["update"],"before":{"id":"original","status":"Deleting"},
        "after":{"id":"original","status":"ScheduledForDelete"}})
    cleanup.validate_remaining_delete({"resource_changes":[delete],"resource_drift":[drift]},[row],[row])
    drift["change"]["after"]["id"]="unowned"
    with pytest.raises(Exception): cleanup.validate_remaining_delete({"resource_changes":[delete],"resource_drift":[drift]},[row],[row])


def test_unresponsive_provider_does_not_hide_an_unbounded_wait(cleanup,monkeypatch):
    owner=importlib.import_module("scripts.pipelines.owner")
    class Child:
        pid=333
        exited=False
        def __enter__(self): return self
        def __exit__(self,*args): self.exited=True
        def communicate(self,timeout): raise owner.subprocess.TimeoutExpired("owned",timeout)
    child=Child()
    monkeypatch.setattr(owner.subprocess,"Popen",lambda *args,**kw:child)
    monkeypatch.setattr(owner.os,"killpg",lambda *args:None,raising=False)
    driver=object.__new__(owner.OwnerDriver); driver.env={}; driver.record=lambda *args:None
    with pytest.raises(owner.subprocess.TimeoutExpired): driver.process(["owned"],seconds=1)
    assert child.exited is False


def test_owner_backend_cannot_inherit_ci_oidc_or_wrong_state(cleanup,tmp_path):
    owner=importlib.import_module("scripts.pipelines.owner")
    folder=tmp_path/".terraform"; folder.mkdir()
    import json
    backend={"type":"azurerm","config":{"use_oidc":False,"use_azuread_auth":True,
        "storage_account_name":"owned","container_name":"test-state","key":"index.tfstate"}}
    (folder/"terraform.tfstate").write_text(json.dumps({"backend":backend}))
    owner.verify_backend(tmp_path,{"type":"azurerm","storage_account_name":"owned","container_name":"test-state","key":"index.tfstate"})
    backend["config"]["use_oidc"]=True
    (folder/"terraform.tfstate").write_text(json.dumps({"backend":backend}))
    with pytest.raises(Exception): owner.verify_backend(tmp_path,{"type":"azurerm","storage_account_name":"owned","container_name":"test-state","key":"index.tfstate"})


def test_resume_adopts_proved_empty_index_after_state_storage_deleted(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines.session import SessionAuthority,exclusive_json
    start=datetime(2026,10,16,8,tzinfo=timezone.utc)
    a=SessionAuthority("p8-1016x","a"*40,"b"*40,"c"*64,start.isoformat(),
        *[(start+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],"d"*64)
    resource={"address":"owned.index","id":"index"}
    foundation={"address":"owned.group","id":"group"}
    manifest={"components":[{"name":"index-test","resources":[resource]},
        {"name":"foundation","resources":[foundation]}]}
    retained={"resources":[],"roleAssignments":[],"deletedVaults":[]}
    exclusive_json(tmp_path/"baseline.json",{"retained":retained})
    monkeypatch.setattr(cleanup,"load",lambda directory:(a,{},manifest))
    class Driver:
        def __init__(self): self.values={"index-test":[resource],"foundation":[foundation]}; self.failed=False
        def verify_owner(self): pass
        def active_owned(self): return []
        def state(self,component):
            if self.failed and component["name"]=="index-test": raise RuntimeError("state storage already removed")
            return self.values[component["name"]]
        def plan(self,component,attempt,remaining):
            return {"resource_changes":[{"address":r["address"],"mode":"managed","change":{
                "actions":["delete"],"before":{"id":r["id"]},"after":None}} for r in remaining]}
        def apply(self,component,attempt):
            if component["name"]=="foundation" and not self.failed:
                self.failed=True
                raise TimeoutError("group still deleting after storage removed")
            self.values[component["name"]]=[]
        def retained(self): return retained
        def canonical_absence(self): return True
    driver=Driver()
    assert not cleanup.execute(tmp_path,start+timedelta(minutes=78),driver=driver)["physical_pass"]
    assert cleanup.execute(tmp_path,start+timedelta(minutes=79),driver=driver)["physical_pass"]
