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


@pytest.mark.parametrize('due,verified', [(False, False), (False, True), (True, False)])
def test_early_cleanup_waits_for_restoration_but_due_cleanup_does_not(cleanup,tmp_path,monkeypatch,due,verified):
    from scripts.pipelines.session import SessionAuthority,exclusive_json
    from scripts.pipelines import evidence
    start = datetime.now(timezone.utc)
    a = SessionAuthority('p8-1018d','a'*40,'b'*40,'c'*64,start.isoformat(),
        *[(start+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],'d'*64)
    row = {'address':'owned.group','id':'owned-group'}
    manifest = {'components':[{'name':'foundation','resources':[row]}]}
    retained = {'resources':[], 'roleAssignments':[], 'deletedVaults':[]}
    exclusive_json(tmp_path/'baseline.json', {'retained':retained})
    exclusive_json(tmp_path/'owner-stop-authority.json', {'kind':'human-explicit-stop',
        'source_sha':a.source_sha, 'session':a.session, 'message_ref':'actual-owner-stop'})
    monkeypatch.setattr(cleanup,'load',lambda directory:(a,{},manifest))
    monkeypatch.setattr(evidence,'grade',lambda directory:{'P8-1':{'status':'FAIL'}})
    monkeypatch.setattr(cleanup,'quiescence_before',lambda *args:verified)
    class Driver:
        applied = False
        def active_owned(self): return []
        def verify_owner(self): pass
        def state(self,component): return [] if self.applied else [row]
        def plan(self,component,attempt,remaining):
            return {'resource_changes':[{'mode':'managed','address':row['address'],
                'change':{'actions':['delete'],'before':{'id':row['id']},'after':None}}]}
        def apply(self,component,attempt): self.applied = True
        def canonical_absence(self): return self.applied
        def retained(self): return retained
    driver = Driver()
    result = cleanup.execute(tmp_path,start+timedelta(minutes=78 if due else 30),driver=driver)
    if not due and not verified:
        assert result['status'] == 'pending' and not result['physical_pass']
        assert not driver.applied
        assert not list((tmp_path/'intents').glob('cleanup-*.json'))
    else:
        assert result['physical_pass'] and driver.applied
        assert result['quiescence_before'] is verified


def test_early_native_dispatch_does_not_consume_helper_reservation_without_ack(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines import watchdog,quiescence
    from types import SimpleNamespace
    due = [False]
    a = SimpleNamespace(cleanup_due=lambda now:due[0])
    monkeypatch.setattr(watchdog,'load',lambda directory:(a,{},{}))
    monkeypatch.setattr(quiescence,'request_closeout',lambda directory,now:None)
    monkeypatch.setattr(quiescence,'status',lambda *args:{'verified':False})
    monkeypatch.setattr(watchdog.subprocess,'Popen',lambda *args,**kw:pytest.fail('cleanup helper launched before restoration'))
    with pytest.raises(Exception,match='cleanup'):
        watchdog.dispatch_cleanup(tmp_path)
    assert not (tmp_path/'cleanup-launch-intent.json').exists()
    assert not (tmp_path/'cleanup-native.json').exists()
    # The early refusal must leave the original due-time launch available.
    due[0] = True
    child = SimpleNamespace(pid=42)
    monkeypatch.setattr(watchdog,'wsl_command',lambda session:['owned-native-cleanup',session])
    monkeypatch.setattr(watchdog.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    monkeypatch.setattr(watchdog.subprocess,'Popen',lambda *args,**kw:child)
    monkeypatch.setattr(watchdog,'process_snapshot',lambda pid:{'pid':pid})
    assert watchdog.dispatch_cleanup(tmp_path) is child
    assert (tmp_path/'cleanup-native.json').exists()


@pytest.mark.parametrize('suffix', ['',
    '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/owned-id',
    '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/owned-id/federatedIdentityCredentials/test'])
def test_owned_identity_arm_grammar_case_is_not_a_new_resource(cleanup, suffix):
    actual = '/subscriptions/sub/resourceGroups/owned' + suffix
    pinned = actual.replace('resourceGroups', 'resourcegroups').replace('Microsoft.ManagedIdentity',
        'microsoft.managedidentity').replace('userAssignedIdentities', 'userassignedidentities').replace(
        'federatedIdentityCredentials', 'federatedidentitycredentials')
    original = [{'address': 'owned.identity', 'id': pinned}]
    remaining = [{'address': 'owned.identity', 'id': actual}]
    plan = {'resource_changes': [{'mode': 'managed', 'address': 'owned.identity',
        'change': {'actions': ['delete'], 'before': {'id': actual}, 'after': None}}]}
    cleanup.validate_remaining_delete(plan, original, remaining)


@pytest.mark.parametrize('old,new', [('sub','other-sub'), ('owned','other'),
    ('owned-id','OWNED-ID'), ('Microsoft.ManagedIdentity','Microsoft.Search'),
    ('userAssignedIdentities','searchServices'), ('test','TEST')])
def test_identity_case_comparison_keeps_names_and_scope_exact(cleanup, old, new):
    pinned = '/subscriptions/sub/resourceGroups/owned/providers/Microsoft.ManagedIdentity/userAssignedIdentities/owned-id/federatedIdentityCredentials/test'
    actual = pinned.replace(old, new)
    row = {'address': 'owned.identity', 'id': pinned}
    current = dict(row, id=actual)
    plan = {'resource_changes': [{'mode': 'managed', 'address': row['address'],
        'change': {'actions': ['delete'], 'before': {'id': actual}, 'after': None}}]}
    with pytest.raises(Exception, match='plan'):
        cleanup.validate_remaining_delete(plan, [row], [current])


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


def test_cleanup_executes_owned_identity_with_mixed_grammar_case(cleanup,tmp_path,monkeypatch):
    from scripts.pipelines.session import SessionAuthority,exclusive_json
    start = datetime(2026,10,18,8,tzinfo=timezone.utc)
    a = SessionAuthority('p8-1018c','a'*40,'b'*40,'c'*64,start.isoformat(),
        *[(start+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],'d'*64)
    pinned = '/subscriptions/sub/resourcegroups/owned/providers/microsoft.managedidentity/userassignedidentities/owned-id'
    actual = '/subscriptions/sub/resourceGroups/owned/providers/Microsoft.ManagedIdentity/userAssignedIdentities/owned-id'
    component = {'name':'identity','resources':[{'address':'owned.identity','id':pinned}]}
    retained = {'resources':[],'roleAssignments':[],'deletedVaults':[]}
    exclusive_json(tmp_path/'baseline.json',{'retained':retained})
    monkeypatch.setattr(cleanup,'load',lambda directory:(a,{}, {'components':[component]}))
    class Driver:
        applied = False
        def verify_owner(self): pass
        def active_owned(self): return []
        def state(self,component):
            return [] if self.applied else [{'address':'owned.identity','id':actual}]
        def plan(self,component,attempt,remaining):
            assert remaining == [{'address':'owned.identity','id':actual}]
            return {'resource_changes':[{'mode':'managed','address':'owned.identity',
                'change':{'actions':['delete'],'before':{'id':pinned},'after':None}}]}
        def apply(self,component,attempt): self.applied = True
        def retained(self): return retained
        def canonical_absence(self): return self.applied
    driver = Driver()
    assert cleanup.execute(tmp_path,start+timedelta(minutes=78),driver=driver)['physical_pass']
    assert driver.applied
    assert component['resources'][0]['id'] == pinned


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


def test_never_created_index_uses_authenticated_absence_not_cli_failure(cleanup):
    owner=importlib.import_module("scripts.pipelines.owner")
    driver=object.__new__(owner.OwnerDriver)
    driver.index_state_absence=lambda component:True
    driver.component_dir=lambda component:pytest.fail("absence must be checked before backend init metadata")
    driver.process=lambda *args,**kw:pytest.fail("state list exits 1 for a nonexistent state")
    assert driver.state({"name":"index-test"})==[]


def test_index_absence_requires_both_backend_and_index_proof(cleanup,tmp_path,monkeypatch):
    owner=importlib.import_module("scripts.pipelines.owner")
    import httpx
    records=[]
    statuses=[404,404]
    def server(request):
        if request.method=="PUT": return httpx.Response(201)
        status=statuses[0] if request.url.host.endswith("blob.core.windows.net") else statuses[1]
        return httpx.Response(status,headers={"x-ms-error-code":"BlobNotFound"})
    new_client=httpx.Client
    monkeypatch.setattr(owner.httpx,"Client",lambda **kw:new_client(transport=httpx.MockTransport(server)))
    monkeypatch.setattr(owner,"TokenSource",lambda *args: type("Tokens",(),{"token":lambda self:"synthetic"})())
    driver=object.__new__(owner.OwnerDriver)
    driver.a={"storage_host":"owned.blob.core.windows.net","search_host":"owned.search.windows.net",
        "owner":{"tenant_id":"tenant"},"session":"p8-1016x","source_sha":"a"*40}
    driver.directory=tmp_path; driver.record=lambda name,value:records.append((name,value))
    assert driver.index_state_absence({"name":"index-test"})
    assert records[-1][1]["index"]["status"]==404
    driver._absent_indices={}; statuses[1]=200
    with pytest.raises(Exception): driver.index_state_absence({"name":"index-test"})


def test_missing_nested_role_is_not_absence_of_its_search_parent(cleanup,tmp_path,monkeypatch):
    owner=importlib.import_module("scripts.pipelines.owner")
    import httpx
    new_client=httpx.Client
    def server(request):
        if request.method=="PUT": return httpx.Response(201)
        if request.url.host=="management.azure.com":
            return httpx.Response(404 if "roleAssignments" in request.url.path else 200)
        if request.url.host.endswith("blob.core.windows.net"): return httpx.Response(404,headers={"x-ms-error-code":"BlobNotFound"})
        return httpx.Response(200,json={"name":"p8-test"})
    monkeypatch.setattr(owner.httpx,"Client",lambda **kw:new_client(transport=httpx.MockTransport(server)))
    token=type("Tokens",(),{"token":lambda self:"synthetic"})()
    monkeypatch.setattr(owner,"TokenSource",lambda *args:token)
    driver=object.__new__(owner.OwnerDriver); driver.directory=tmp_path; driver.tokens=token
    driver.a={"storage_host":"owned.blob.core.windows.net","search_host":"owned.search.windows.net",
        "owner":{"tenant_id":"tenant"},"session":"p8-1016x","source_sha":"a"*40}
    parent="/subscriptions/s/resourceGroups/owned/providers/Microsoft.Search/searchServices/owned"
    driver.manifest={"components":[{"name":"foundation","resources":[{"id":value,"kind":"arm","api_version":"2024-01-01"}
        for value in (parent,parent+"/providers/Microsoft.Authorization/roleAssignments/nested")]}]}
    driver.record=lambda *args:None
    with pytest.raises(Exception): driver.index_state_absence({"name":"index-test"})
