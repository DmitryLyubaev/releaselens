from test_pipelines_metadata_policy import POLICY
"""Catch the real missing-owner and infeasible whole-attempt budget before billing."""
from datetime import datetime,timedelta,timezone
import copy
import hashlib
import importlib
import json

import pytest

OWNER="11111111-2222-4333-8444-555555555555"
OTHER="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

@pytest.fixture
def modules(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/'eval'))
    return importlib.import_module('scripts.pipelines.session')

def authority(profile='standard'):
    start=datetime(2026,10,17,4,tzinfo=timezone.utc)
    offsets=(75,78,80,120) if profile=='standard' else (210,213,215,270)
    return {'session':'p8-example','source_sha':'a'*40,'tree_sha':'b'*40,'release_digest':'c'*64,
        'manifest_sha256':'d'*64,'apply_started_at':start.isoformat(),'timing_profile':profile,
        **{key:(start+timedelta(minutes=n)).isoformat() for key,n in zip(
            ('queue_cutoff_at','cleanup_due_at','cleanup_start_by','closeout_by'),offsets)}}

def test_human_scope_binds_the_owner_ado_id_and_timing_profile(modules):
    raw={'metadata_policy':dict(POLICY),'owner':{},'owner_ado_id':OWNER,'owned_group_ids':[],'principals':{},'scopes':{},
        'pipeline_id':2,'storage_host':'owned.blob.core.windows.net','search_host':'owned.search.windows.net',
        'manifest_sha256':'a'*64,'baseline_sha256':'b'*64}
    digest=modules.scope_digest(raw)
    assert modules.scope_digest(dict(raw,owner_ado_id=OTHER))!=digest
    assert modules.scope_digest(dict(raw,timing_profile='provisioning'))!=digest

def test_provisioning_profile_requires_exact_approved_offsets(modules):
    a=modules.SessionAuthority(**authority('provisioning'))
    a.validate()
    raw=authority('provisioning');raw['closeout_by']='2026-10-17T08:01:00+00:00'
    with pytest.raises(Exception):modules.SessionAuthority(**raw).validate()

def test_started_watchdog_cannot_adopt_another_timing_profile(modules):
    watchdog=importlib.import_module('scripts.pipelines.watchdog')
    original=modules.SessionAuthority(**authority());changed=modules.SessionAuthority(**authority('provisioning'))
    with pytest.raises(modules.DeliveryRefused,match='authority'):
        watchdog.adopt_authority(original,changed)

def test_old_window_refuses_the_complete_budget_before_any_paid_launch(modules):
    readiness=importlib.import_module('scripts.pipelines.readiness')
    with pytest.raises(Exception,match='deadline'):
        readiness.require_execution_window(authority(),datetime(2026,10,17,4,tzinfo=timezone.utc),'clock')

def test_full_budget_boundary_and_remaining_phase_checks(modules):
    readiness=importlib.import_module('scripts.pipelines.readiness');raw=authority('provisioning')
    # 196 minutes includes the separate15-minute placeholder and12-minute uploads.
    readiness.require_execution_window(raw,datetime(2026,10,17,4,4,tzinfo=timezone.utc),'clock')
    with pytest.raises(Exception,match='deadline'):
        readiness.require_execution_window(raw,datetime(2026,10,17,4,4,1,tzinfo=timezone.utc),'clock')
    readiness.require_execution_window(raw,datetime(2026,10,17,4,9,tzinfo=timezone.utc),'foundation')
    with pytest.raises(Exception,match='deadline'):
        readiness.require_execution_window(raw,datetime(2026,10,17,4,9,1,tzinfo=timezone.utc),'foundation')

def test_owner_id_must_match_the_actual_pinned_approval(modules):
    readiness=importlib.import_module('scripts.pipelines.readiness')
    rows=[];expected=[]
    for i,kind in enumerate(('branch','required-template','approval','exclusive-lock'),1):
        settings={'approvers':[{'id':OWNER}]} if kind=='approval' else {'setting':kind}
        resource={'id':str(i),'type':'environment'}
        rows.append({'id':i,'type':{'id':kind},'version':'1','resource':resource,'settings':settings})
        expected.append({'id':i,'kind':kind,'type_id':kind,'version':'1','resource':resource,
            'settings_sha256':hashlib.sha256(json.dumps(settings,sort_keys=True,separators=(',',':')).encode()).hexdigest()})
    snapshot={'checks':rows,**{k:True for k in ('pipeline_authorized','pool_authorized','private_project','main_protected','secrets_masked')}}
    raw={'owner_ado_id':OWNER,'checks_expected':{'approval_ref':'actual-owner-approval','checks':expected}}
    readiness.require_owner_binding(raw,snapshot)
    with pytest.raises(Exception,match='authority'):
        readiness.require_owner_binding(dict(raw,owner_ado_id=OTHER),snapshot)
    changed=copy.deepcopy(snapshot);changed['checks'][2]['settings']['approvers'].append({'id':OTHER})
    with pytest.raises(Exception):readiness.require_owner_binding(raw,changed)


@pytest.mark.parametrize('mode',['valid','missing-owner','wrong-owner','insufficient-window','changed-snapshot'])
@pytest.mark.parametrize('component_name',['foundation','identity'])
def test_owner_reservation_cannot_precede_readiness_checks(modules,monkeypatch,tmp_path,mode,component_name):
    from types import SimpleNamespace
    ownership=importlib.import_module('scripts.pipelines.ownership')
    rows=[];expected=[]
    for i,kind in enumerate(('branch','required-template','approval','exclusive-lock'),1):
        settings={'approvers':[{'id':OWNER}]} if kind=='approval' else {'setting':kind}
        resource={'id':str(i),'type':'environment'}
        rows.append({'id':i,'type':{'id':kind},'version':'1','resource':resource,'settings':settings})
        expected.append({'id':i,'kind':kind,'type_id':kind,'version':'1','resource':resource,
            'settings_sha256':hashlib.sha256(json.dumps(settings,sort_keys=True,separators=(',',':')).encode()).hexdigest()})
    snapshot={'checks':rows,**{k:True for k in ('pipeline_authorized','pool_authorized','private_project','main_protected','secrets_masked')}}
    profile='standard' if mode=='insufficient-window' else 'provisioning'
    raw=authority(profile);start=datetime.now(timezone.utc)-timedelta(minutes=1)
    raw.update(owner_ado_id=OWNER,checks_expected={'approval_ref':'actual-owner-approval','checks':expected},apply_started_at=start.isoformat())
    offsets=(75,78,80,120) if profile=='standard' else (210,213,215,270)
    for key,n in zip(('queue_cutoff_at','cleanup_due_at','cleanup_start_by','closeout_by'),offsets):raw[key]=(start+timedelta(minutes=n)).isoformat()
    if mode=='missing-owner':raw.pop('owner_ado_id')
    if mode=='wrong-owner':raw['owner_ado_id']=OTHER
    snapshot_bytes=json.dumps(snapshot).encode();raw['checks_snapshot_sha256']=hashlib.sha256(snapshot_bytes).hexdigest()
    (tmp_path/'checks-snapshot.json').write_bytes(snapshot_bytes+(b'\n' if mode=='changed-snapshot' else b''))
    binary=tmp_path/'create.tfplan';binary.write_bytes(b'unit-test-only saved plan')
    shown=tmp_path/'create.json';shown.write_text(json.dumps({'resource_changes':[{'mode':'managed',
        'address':'azurerm_resource_group.delivery','change':{'actions':['create']}}]}))
    component={'name':component_name,'resources':[{'address':'azurerm_resource_group.delivery'}],
        'create_plan':{'kind':'owner','binary':binary.name,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'shown':shown.name,'shown_sha256':hashlib.sha256(shown.read_bytes()).hexdigest()}}
    a=SimpleNamespace(session=raw['session'],source_sha=raw['source_sha'],manifest_sha256=raw['manifest_sha256'])
    monkeypatch.setattr(modules,'load',lambda directory:(a,raw,{'components':[component]}))
    monkeypatch.setattr(modules,'verify_source',lambda authority:None)
    if mode=='valid' or (mode=='insufficient-window' and component_name=='identity'):
        ownership.reserve_owner_apply(tmp_path,component_name)
        assert (tmp_path/'apply-intents'/f'{component_name}.json').exists()
    else:
        with pytest.raises(Exception):ownership.reserve_owner_apply(tmp_path,component_name)
        assert not (tmp_path/'apply-intents'/f'{component_name}.json').exists()
