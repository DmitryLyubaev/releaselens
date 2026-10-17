"""A nonexistent state is safe only with exact, authenticated never-applied proof."""
import copy
import importlib
import json
from datetime import datetime,timedelta,timezone

import httpx
import pytest

@pytest.fixture
def case(repo_root,monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(repo_root/'eval'))
    owner=importlib.import_module('scripts.pipelines.owner')
    cwd=tmp_path/'tf/agents';(cwd/'.terraform').mkdir(parents=True)
    (cwd/'main.tf').write_text('# reviewed local configuration\n')
    (cwd/'.terraform/terraform.tfstate').write_text(json.dumps({'backend':{'type':'local','config':{'path':'terraform.tfstate'}}}))
    group='/subscriptions/sub/resourceGroups/rg-p7-p8-example'
    raw={'session':'p8-example','source_sha':'a'*40,'manifest_sha256':'b'*64,
        'owner_session_path':tmp_path.as_posix(),'owned_group_ids':[group]}
    component={'name':'agents','directory':'tf/agents','backend':{'type':'local','path':'terraform.tfstate'},
        'config_sha256':owner.config_digest(cwd),'resources':[{'address':'azurerm_resource_group.agent',
            'kind':'arm','id':group,'api_version':'2021-04-01'}]}
    driver=object.__new__(owner.OwnerDriver);driver.directory=tmp_path;driver.a=raw
    driver.manifest={'components':[component]};driver.logs=tmp_path/'owner-originals';driver.logs.mkdir();driver.counter=0
    driver.tokens=type('Tokens',(),{'token':lambda self:'synthetic-owner-token'})()
    calls=[];status=[404]
    def server(request):
        assert request.method=='GET' and request.url.host=='management.azure.com'
        assert request.url.path==group and request.headers['Authorization']=='Bearer synthetic-owner-token'
        calls.append(request.method)
        return httpx.Response(status[0],json={'error':{'code':'ResourceGroupNotFound'}})
    client=httpx.Client
    monkeypatch.setattr(owner.httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(server)))
    def terraform(args,cwd=None,seconds=120):
        if args==['terraform','show','-json']:return '{"format_version":"1.0"}'
        if args==['terraform','state','list']:raise owner.DeliveryRefused('plan')
        pytest.fail('unexpected process or resource mutation')
    driver.process=terraform
    return driver,component,raw,cwd,status,calls

def test_never_applied_agents_accept_actual_group_absence_without_manufacturing_state(case):
    driver,component,raw,cwd,status,calls=case
    assert driver.state(component)==[]
    files=list(driver.logs.glob('*-local-state-absence.json'));assert len(files)==1
    proof=json.loads(files[0].read_bytes())
    assert proof['group']['id']=='/subscriptions/sub/resourceGroups/rg-p7-p8-example'
    assert proof['group']['status']==404 and calls==['GET']
    assert not (cwd/'terraform.tfstate').exists() and not (cwd/'terraform.tfstate.backup').exists()
    evidence=importlib.import_module('scripts.pipelines.evidence')
    evidence.require_local_state_absence(proof,component,raw)

@pytest.mark.parametrize('status_code',[200,403,500])
def test_existing_denied_or_failed_group_is_not_local_state_absence(case,status_code):
    driver,component,raw,cwd,status,calls=case;status[0]=status_code
    with pytest.raises(Exception):driver.state(component)
    assert not list(driver.logs.glob('*-local-state-absence.json'))
    observation=json.loads(next(driver.logs.glob('*-local-state-group-observation.json')).read_bytes())
    assert observation['id']==raw['owned_group_ids'][0] and observation['status']==status_code
    assert json.loads(observation['body'])=={'error':{'code':'ResourceGroupNotFound'}}

INVALID_SHOW=[{}, {'version':4,'resources':[{'type':'azurerm_resource_group'}]},
    {'format_version':'1.0','planned_values':{}}, [], {'format_version':'2.0'},
    {'format_version':'1.0','values':[]}, {'format_version':'1.0','values':{'root_module':{'resources':{}}}},
    {'format_version':'1.0','values':{'root_module':{'child_modules':{}}}}]

@pytest.mark.parametrize('shown',INVALID_SHOW)
def test_malformed_or_non_show_schema_cannot_prove_local_absence(case,shown):
    driver,component,raw,cwd,status,calls=case
    assert driver.state(component)==[]
    proof=json.loads(next(driver.logs.glob('*-local-state-absence.json')).read_bytes())
    evidence=importlib.import_module('scripts.pipelines.evidence')
    with pytest.raises(Exception):evidence.require_local_state_absence(proof|{'terraform_show':shown},component,raw)

@pytest.mark.parametrize('shown',INVALID_SHOW)
def test_capture_refuses_malformed_or_non_show_schema_before_arm(case,shown):
    driver,component,raw,cwd,status,calls=case
    driver.process=lambda *args,**kwargs:json.dumps(shown)
    with pytest.raises(Exception):driver.state(component)
    assert not calls and not list(driver.logs.glob('*-local-state-absence.json'))

@pytest.mark.parametrize('existing',['state','backup','lock','apply-intent'])
def test_prior_state_backup_lock_or_apply_intent_never_uses_absence_shortcut(case,existing):
    driver,component,raw,cwd,status,calls=case
    target={'state':cwd/'terraform.tfstate','backup':cwd/'terraform.tfstate.backup',
        'lock':cwd/'.terraform.tfstate.lock.info','apply-intent':driver.directory/'apply-intents/agents.json'}[existing]
    target.parent.mkdir(exist_ok=True);target.write_text('{}')
    with pytest.raises(Exception):driver.state(component)
    assert not calls and not list(driver.logs.glob('*-local-state-absence.json'))

def test_wrong_group_or_existing_resources_cannot_be_regraded_as_absence(case):
    driver,component,raw,cwd,status,calls=case
    assert driver.state(component)==[]
    proof=json.loads(next(driver.logs.glob('*-local-state-absence.json')).read_bytes())
    evidence=importlib.import_module('scripts.pipelines.evidence')
    for change in ({'source_sha':'c'*40},{'session':'p8-other'},
        {'group':{'id':'/subscriptions/sub/resourceGroups/unrelated','status':404}},
        {'group':{'id':proof['group']['id'],'status':403}},
        {'terraform_show':{'values':{'root_module':{'resources':[{'address':'owned.resource'}]}}}}):
        with pytest.raises(Exception):evidence.require_local_state_absence(proof|change,component,raw)
    remote=copy.deepcopy(component);remote['backend']={'type':'azurerm'}
    with pytest.raises(Exception):evidence.require_local_state_absence(proof,remote,raw)


@pytest.mark.parametrize('change',['none','backup','apply-intent','late-browser-ack',
    'retry-after-unquiesced-delete','retry-after-no-delete','missing-first-attempt',
    'duplicate-first-attempt','foreign-first-attempt'])
def test_closeout_grader_consumes_absence_and_preserves_prior_quiescence(case,monkeypatch,change):
    driver,component,raw,cwd,status,calls=case
    assert driver.state(component)==[]
    proof=json.loads(next(driver.logs.glob('*-local-state-absence.json')).read_bytes())
    evidence=importlib.import_module('scripts.pipelines.evidence')
    session=importlib.import_module('scripts.pipelines.session')
    root=driver.directory;now=datetime.now(timezone.utc)
    a=session.SessionAuthority('p8-example','a'*40,'c'*40,'d'*64,now.isoformat(),
        *[(now+timedelta(minutes=n)).isoformat() for n in (75,78,80,120)],'b'*64)
    raw.update(source_sha=a.source_sha,cleanup_start_by=a.cleanup_start_by,closeout_by=a.closeout_by,pipeline_id=2)
    manifest={'components':[component]}
    monkeypatch.setattr(evidence,'load',lambda directory:(a,raw,manifest))
    retained={'resources':[],'roleAssignments':[],'deletedVaults':[]}
    cleanup={'attempt':1,'session':a.session,'source_sha':a.source_sha,'manifest_sha256':a.manifest_sha256,
        'physical_pass':True,'canonical_absence':True,'empty_states':True,'quiescence_before':True,
        'cleanup_started_at':now.isoformat(),'verified_at':now.isoformat()}
    (root/'authority.json').write_text('{}')
    (root/'baseline.json').write_text(json.dumps({'retained':retained}))
    (root/'retained-after-1.json').write_text(json.dumps(retained))
    if change=='late-browser-ack':cleanup['quiescence_before']=False
    (root/'cleanup-attempt-1.json').write_text(json.dumps(cleanup))
    first_attempt=1
    if change.startswith('retry-') or change=='missing-first-attempt':
        earlier=dict(cleanup,physical_pass=False,quiescence_before=False)
        (root/'cleanup-attempt-1.json').write_text(json.dumps(earlier))
        cleanup=dict(cleanup,attempt=2)
        (root/'cleanup-attempt-2.json').write_text(json.dumps(cleanup))
        (root/'retained-after-2.json').write_text(json.dumps(retained))
        if change=='retry-after-no-delete':first_attempt=2
    if change=='missing-first-attempt':(root/'cleanup-attempt-1.json').unlink()
    if change=='duplicate-first-attempt':(root/'cleanup-attempt-2.json').write_text(json.dumps(cleanup))
    if change=='foreign-first-attempt':
        (root/'cleanup-attempt-1.json').write_text(json.dumps(dict(cleanup,session='p8-foreign')))
    (root/'cleanup-result.json').write_text(json.dumps(cleanup))
    definition={'id':2,'queueStatus':'disabled','variables':{}}
    original={'cleanup-first-delete':{'session':a.session,'source_sha':a.source_sha,'at':now.isoformat(),'attempt':first_attempt},
        'group-absence-0':{'id':raw['owned_group_ids'][0],'status':404},'empty-state-absence-agents':proof,
        'pat-revocation':{'kind':'human-pat-revoked','session':a.session,'message_ref':'unit-test-only','at':now.isoformat()},
        'helper-exit':{'session':a.session,'active_owned':[],'helpers':[
            {'name':name,'expected':{'pid':123,'created':'unit-test-only','command_line':'unit-test-only','sha256':'f'*64},'actual':None}
            for name in ('watchdog-owned.json','cleanup-native.json')]},
        'definition-original':definition,'definition-disabled':definition,'queue-after':{'count':0,'value':[]}}
    registry={'kind':'actual','session':a.session,'source_sha':a.source_sha,'files':{}}
    for key,value in original.items():
        file=root/(key+'.json');file.write_text(json.dumps(value))
        registry['files'][key]={'kind':'actual','session':a.session,'source_sha':a.source_sha,'channel':'runtime',
            'path':file.name,'sha256':evidence.sha256_file(file),'captured_at':now.isoformat()}
    (root/'originals.json').write_text(json.dumps(registry))
    if change=='backup':(cwd/'terraform.tfstate.backup').write_text('{}')
    if change=='apply-intent':
        (root/'apply-intents').mkdir();(root/'apply-intents/agents.json').write_text('{}')
    assert evidence.grade(root)['P8-8']['status']==('PASS' if change in ('none','retry-after-no-delete') else 'FAIL')
