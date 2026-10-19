"""Owner-approved controller budget, isolated from Terraform provider traffic."""
import copy
import importlib
from types import SimpleNamespace

import httpx
import pytest


# Hand-derived approved policy; do not build expected values from production constants.
POLICY = {
    'version': 'controller-search-get-v2', 'total': 256, 'ordinary': 244,
    'physical': 12, 'cleanup_contingency': 9, 'readiness_reads_per_loop': 24,
    'counting_boundary': 'python-controller-and-owner-search-get',
    'provider_boundary': 'manifest-bound-two-index-lifecycles-and-original-command-deadlines',
}


@pytest.fixture
def modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    return SimpleNamespace(**{name: importlib.import_module('scripts.pipelines.' + name)
                             for name in ('storage', 'session', 'fingerprint')})


def test_full_readiness_envelope_is_retained_at_fingerprint_admission(modules):
    # A 101-used ledger leaves 86 fingerprint + 48 readiness + 9 cleanup slots.
    modules.fingerprint.fingerprint_capacity(101, 'before', policy=POLICY)
    modules.fingerprint.fingerprint_capacity(168, 'after', policy=POLICY)
    for used, phase in ((102, 'before'), (169, 'after')):
        with pytest.raises(ValueError):
            modules.fingerprint.fingerprint_capacity(used, phase, policy=POLICY)


def test_new_budget_requires_explicit_exact_policy_before_any_request(modules):
    def forbidden(_): pytest.fail('invalid authority sent HTTP')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        for policy in ({}, POLICY | {'total': 120}, POLICY | {'ordinary': 256},
                       POLICY | {'provider_boundary': 'uncounted'}, POLICY | {'extra': True}):
            with pytest.raises(modules.storage.DeliveryRefused, match='authority'):
                modules.storage.BlobStore('owned.blob.core.windows.net', None, client,
                                          metadata_policy=policy)
        legacy = modules.storage.BlobStore('owned.blob.core.windows.net', None, client)
        with pytest.raises(modules.storage.DeliveryRefused, match='authority'):
            legacy.reserve_budget('metadata', 256)


def test_235_workload_and_12_physical_slots_survive_restart(modules):
    occupied = set()
    requests = []
    def server(request):
        key = request.url.path
        if request.method == 'GET':
            assert key.endswith('/operations/metadata-workload-stopped.json')
            return httpx.Response(404, headers={'x-ms-error-code':'BlobNotFound'})
        requests.append(key)
        assert request.headers['if-none-match'] == '*'
        if key in occupied: return httpx.Response(412)
        occupied.add(key)
        return httpx.Response(201)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        def store():
            return modules.storage.BlobStore('owned.blob.core.windows.net',
                SimpleNamespace(token=lambda: 'offline'), client, metadata_policy=copy.deepcopy(POLICY))
        first = store()
        ordinary = [first.reserve_metadata() for _ in range(235)]
        assert ordinary[0].endswith('/000.json') and ordinary[-1].endswith('/234.json')
        with pytest.raises(modules.storage.DeliveryRefused, match='reserved'):
            first.reserve_budget('metadata', 256)
        physical = [first.reserve_metadata(physical=True) for _ in range(12)]
        assert physical[0].endswith('/244.json') and physical[-1].endswith('/255.json')
        restarted = store()
        for physical in (False, True):
            with pytest.raises(modules.storage.DeliveryRefused, match='reserved'):
                restarted.reserve_metadata(physical=physical)
        assert not any(path.endswith(f'/{i:03}.json') for path in requests for i in range(235, 244))


def test_generic_limits_are_not_widened_by_metadata_policy(modules):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('unexpected HTTP'))) as client:
        store = modules.storage.BlobStore('owned.blob.core.windows.net', None, client,
                                          metadata_policy=POLICY)
        for name, maximum, start in [('upload-test', 256, 0), ('metadata', 256, 235),
                                      ('metadata', 256, 243), ('metadata', 255, 244),
                                      ('metadata', 257, 244)]:
            with pytest.raises(modules.storage.DeliveryRefused, match='authority'):
                store.reserve_budget(name, maximum, start=start)


def test_local_metadata_cannot_resume_as_fresh_cloud_ledger(modules, tmp_path, monkeypatch):
    from datetime import datetime, timezone
    a = SimpleNamespace(apply_started_at='2026-10-19T00:00:00+00:00',
        queue_cutoff_at='2026-10-19T03:30:00+00:00', source_sha='a'*40, release_digest='b'*64)
    monkeypatch.setattr(modules.session, 'load', lambda _: (a, {}, {}))
    monkeypatch.setattr(modules.session, 'verify_source', lambda _: None)
    modules.session.reserve_budget(tmp_path, 'metadata', 244, metadata_policy=POLICY)
    with pytest.raises(modules.storage.DeliveryRefused, match='authority'):
        modules.session.reserve_dispatch(tmp_path, 'release', datetime(2026, 10, 19, 0, 20, tzinfo=timezone.utc))
    assert not (tmp_path / 'intents/dispatch-release.json').exists()


@pytest.mark.parametrize('mutation', [None, {}, {'total': 120}, {'total': True}, {'version':'old'},
                                     {'counting_boundary':'all-search-http'}])
def test_runtime_rejects_missing_or_stale_policy_before_authentication(modules, tmp_path, monkeypatch, mutation):
    run=importlib.import_module('scripts.pipelines.run')
    def forbidden(*args): pytest.fail('invalid policy reached authentication')
    monkeypatch.setattr(run,'authenticate',forbidden)
    raw={} if mutation is None else {'metadata_policy':POLICY | mutation}
    if mutation=={}: raw['metadata_policy']={}
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(modules.storage.DeliveryRefused,match='authority'):
            run.Runtime(raw,{},'build',tmp_path,client)


def test_human_scope_rejects_policy_changes_instead_of_reusing_receipt(modules):
    raw={'metadata_policy':dict(POLICY), 'owner':{},
        'owner_ado_id':'11111111-2222-4333-8444-555555555555','owned_group_ids':[],
        'principals':{},'scopes':{},'pipeline_id':2,'storage_host':'owned.blob.core.windows.net',
        'search_host':'owned.search.windows.net','manifest_sha256':'a'*64,'baseline_sha256':'b'*64}
    modules.session.scope_digest(raw)
    raw['metadata_policy']['total']=120
    with pytest.raises(modules.storage.DeliveryRefused,match='authority'):
        modules.session.scope_digest(raw)


def test_all_256_slots_include_cleanup_only_after_a_bound_human_stop(modules,tmp_path):
    import json
    occupied={f'/evidence/budgets/metadata/{i:03}.json' for i in range(235)}
    stopped=[]
    def server(request):
        if request.method=='GET':
            return httpx.Response(200 if stopped else 404,headers={'x-ms-error-code':'BlobNotFound'})
        key=request.url.path
        if key.endswith('/operations/metadata-workload-stopped.json'):
            if stopped: return httpx.Response(412)
            stopped.append(json.loads(request.content));return httpx.Response(201)
        if key in occupied:return httpx.Response(412)
        occupied.add(key);return httpx.Response(201)
    a={'session':'p8-fixture','source_sha':'a'*40,'manifest_sha256':'b'*64,'metadata_policy':dict(POLICY)}
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store=modules.storage.BlobStore('owned.blob.core.windows.net',SimpleNamespace(token=lambda:'offline'),
            client,metadata_policy=POLICY)
        with pytest.raises(modules.storage.DeliveryRefused): store.reserve_cleanup_metadata(tmp_path,a)
        assert len(occupied)==235
        (tmp_path/'owner-stop-authority.json').write_text(json.dumps({'kind':'human-explicit-stop',
            'message_ref':'offline-human-fixture','session':a['session'],'source_sha':a['source_sha']}))
        (tmp_path/'quiescence').mkdir()
        (tmp_path/'quiescence/request.json').write_text(json.dumps({k:a[k] for k in (
            'session','source_sha','manifest_sha256')}|{'kind':'owner-browser-quiescence-request'}))
        cleanup=[store.reserve_cleanup_metadata(tmp_path,a) for _ in range(9)]
        assert cleanup[0].endswith('/235.json') and cleanup[-1].endswith('/243.json')
        physical=[store.reserve_metadata(physical=True) for _ in range(12)]
        assert physical[0].endswith('/244.json') and physical[-1].endswith('/255.json')
        assert len(occupied)==256 and len(stopped)==1
        with pytest.raises(modules.storage.DeliveryRefused): store.reserve_cleanup_metadata(tmp_path,a)
        restarted=modules.storage.BlobStore(store.host,store.tokens,client,metadata_policy=POLICY)
        with pytest.raises(modules.storage.DeliveryRefused,match='authority'): restarted.reserve_metadata()


@pytest.mark.parametrize('path',['owner-stop-authority.json','quiescence/request.json'])
def test_terminal_cleanup_cannot_resume_dispatch(modules,tmp_path,monkeypatch,path):
    from datetime import datetime,timezone
    a=SimpleNamespace(apply_started_at='2026-10-19T00:00:00+00:00',
        queue_cutoff_at='2026-10-19T03:30:00+00:00',source_sha='a'*40,release_digest='b'*64)
    monkeypatch.setattr(modules.session,'load',lambda _: (a,{},{}))
    monkeypatch.setattr(modules.session,'verify_source',lambda _:None)
    target=tmp_path/path;target.parent.mkdir(exist_ok=True);target.write_text('{}')
    with pytest.raises(modules.storage.DeliveryRefused):
        modules.session.reserve_dispatch(tmp_path,'release',datetime(2026,10,19,0,20,tzinfo=timezone.utc))
    assert not (tmp_path/'intents/dispatch-release.json').exists()


@pytest.mark.parametrize('status',[403,500,'transport'])
def test_ambiguous_reservation_never_advances_to_another_slot(modules,status):
    puts=[]
    def server(request):
        if request.method=='GET':return httpx.Response(404,headers={'x-ms-error-code':'BlobNotFound'})
        puts.append(request.url.path)
        if status=='transport':raise httpx.ReadTimeout('offline timeout',request=request)
        return httpx.Response(status)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store=modules.storage.BlobStore('owned.blob.core.windows.net',SimpleNamespace(token=lambda:'offline'),
            client,metadata_policy=POLICY)
        for _ in range(2):
            with pytest.raises(modules.storage.DeliveryRefused):store.reserve_metadata()
    assert puts==['/evidence/budgets/metadata/000.json']*2


def test_policy_mutation_cannot_widen_stored_transport(modules):
    mutable=copy.deepcopy(POLICY)
    with httpx.Client() as client:
        store=modules.storage.BlobStore('owned.blob.core.windows.net',None,client,metadata_policy=mutable)
        mutable['total']=999
        with pytest.raises(modules.storage.DeliveryRefused):store.reserve_budget('metadata',999)


def test_six_readiness_loops_each_get_all_24_attempts(modules):
    from app.delivery_search.client import wait_for_count
    from app.delivery_search.contracts import SearchTarget
    reads=[];claims=[]
    def server(request):
        if request.url.host.endswith('blob.core.windows.net'):
            if request.method=='GET':return httpx.Response(404,headers={'x-ms-error-code':'BlobNotFound'})
            claims.append(request.url.path);return httpx.Response(201)
        reads.append(request.url.path)
        return httpx.Response(200,text='41825' if len(reads)%24==0 else '0')
    token=SimpleNamespace(token=lambda:'offline')
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store=modules.storage.BlobStore('owned.blob.core.windows.net',token,client,metadata_policy=POLICY)
        def before(request):
            if request.url.host=='owned.search.windows.net':store.reserve_metadata()
        client.event_hooks['request'].append(before)
        target=SearchTarget('https://owned.search.windows.net','p8-test','owned.search.windows.net')
        # Fixed checks, promotion and both43-read fingerprints occupy91 slots.
        for _ in range(91):store.reserve_metadata()
        for _ in range(6):assert wait_for_count(target,token,client,sleep=lambda _:None)==41825
        assert len(reads)==144 and len(claims)==235
        with pytest.raises(modules.storage.DeliveryRefused):store.reserve_metadata()


@pytest.fixture
def captures(modules,tmp_path):
    import hashlib,json
    from datetime import datetime,timedelta,timezone
    from urllib.parse import urlencode
    now=datetime.now(timezone.utc)
    a={'session':'p8-fixture','source_sha':'a'*40,'release_digest':'b'*64,'manifest_sha256':'c'*64,
       'storage_host':'owned.blob.core.windows.net','search_host':'owned.search.windows.net','metadata_policy':dict(POLICY),'pipeline_id':2}
    ids=sorted(str(i) for i in range(41825))
    schema={'name':'p8-demo-prod','fields':[
        {'name':'chunk_id','key':True,'sortable':True,'retrievable':True},
        {'name':'artefact','retrievable':True},{'name':'content','retrievable':True},
        {'name':'vector','retrievable':False}]}
    rows=[{'chunk_id':i,'artefact':'fixture','content':'fixture'} for i in ids]
    def capture(phase):
        folder=tmp_path/phase;folder.mkdir()
        values=[schema]+[{'value':rows[i:i+1000]} for i in range(0,41825,1000)]
        values[1]['@odata.count']=41825
        observations=[]
        for i,value in enumerate(values):
            label='schema' if i==0 else 'documents-'+str(i-1).zfill(2)
            body=json.dumps(value).encode();(folder/(label+'.json')).write_bytes(body)
            params={'api-version':'2024-07-01',**({} if i==0 else modules.fingerprint.page_parameters(i-1))}
            observations.append({'method':'GET','status':200,'file':label+'.json',
                'url':'https://owned.search.windows.net/indexes/p8-demo-prod'+('' if i==0 else '/docs')+'?'+urlencode(params),
                'sha256':hashlib.sha256(body).hexdigest(),
                'metadata_budget_claim':f'budgets/metadata/{i+(0 if phase=="before" else 100):03}.json',
                'at':(now-timedelta(seconds=120 if phase=='before' else 1)).isoformat()})
        state={'version':4,'lineage':'offline-lineage','serial':1,'resources':[{'fixture':True}]}
        (folder/'state.json').write_text(json.dumps(state))
        return folder,observations,state
    registry={'kind':'actual','session':a['session'],'source_sha':a['source_sha'],'files':{}}
    for key,value in {
        'negative-build':{'id':17,'definition':{'id':2},'sourceVersion':a['source_sha'],
            'sourceBranch':'refs/heads/main','status':'completed','result':'failed','reason':'manual',
            'queueTime':(now-timedelta(seconds=90)).isoformat(),'finishTime':(now-timedelta(seconds=30)).isoformat()},
        'gate-negative-receipt':{'source_sha':a['source_sha'],'release_digest':a['release_digest'],
            'run_id':17,'stage':'negative','expected_negative':True,'acceptance_pass':True},
    }.items():
        path=tmp_path/(key+'.json');path.write_text(json.dumps(value))
        registry['files'][key]={'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'session':a['session'],'source_sha':a['source_sha'],'kind':'actual','channel':'runtime',
            'captured_at':(now-timedelta(seconds=20)).isoformat()}
    (tmp_path/'originals.json').write_text(json.dumps(registry))
    from scripts.pipelines.metadata import validate_policy
    ledger=SimpleNamespace(host=a['storage_host'], metadata_policy=validate_policy(POLICY),
        json=lambda container,key:{'budget':'metadata','slot':int(key.rsplit('/',1)[1][:3])})
    return a,set(ids),capture,ledger


def test_only_complete_chronological_fingerprints_open_post_negative_cleanup(modules,tmp_path,captures):
    from scripts.pipelines.metadata import require_cleanup_contingency
    a,ids,capture,ledger=captures
    for phase in ('before','after'):
        folder,observations,state=capture(phase)
        modules.fingerprint.complete_fingerprint(tmp_path,a,phase,folder,observations,ids,state,store=ledger)
    require_cleanup_contingency(tmp_path,a)
    # Mutation after completion invalidates the phase gate, including a restart.
    (tmp_path/'after/documents-41.json').write_text('{"value":[]}')
    with pytest.raises(modules.storage.DeliveryRefused):require_cleanup_contingency(tmp_path,a)


@pytest.mark.parametrize('damage',['interrupted','unclaimed','failed','before-negative','wrong-policy','reused-claims'])
def test_incomplete_or_unbound_second_fingerprint_never_opens_contingency(modules,tmp_path,captures,damage):
    from datetime import datetime,timedelta,timezone
    a,ids,capture,ledger=captures
    folder,observations,state=capture('before')
    modules.fingerprint.complete_fingerprint(tmp_path,a,'before',folder,observations,ids,state,store=ledger)
    folder,observations,state=capture('after')
    if damage=='interrupted':observations.pop()
    elif damage=='unclaimed':observations[-1]['metadata_budget_claim']='budgets/metadata/244.json'
    elif damage=='failed':observations[-1]['status']=403
    elif damage=='before-negative':
        for row in observations:row['at']=(datetime.now(timezone.utc)-timedelta(seconds=100)).isoformat()
    elif damage=='reused-claims':
        for i,row in enumerate(observations):row['metadata_budget_claim']=f'budgets/metadata/{i:03}.json'
    else:a=a|{'metadata_policy':POLICY|{'total':120}}
    with pytest.raises(modules.storage.DeliveryRefused):
        modules.fingerprint.complete_fingerprint(tmp_path,a,'after',folder,observations,ids,state,store=ledger)
    assert not (tmp_path/'fingerprint-after-complete.json').exists()


def test_stop_marker_blocks_direct_apply_runner_before_subprocess(modules,monkeypatch):
    run=importlib.import_module('scripts.pipelines.run')
    runtime=object.__new__(run.Runtime)
    def stopped():raise modules.storage.DeliveryRefused('authority')
    runtime.store=SimpleNamespace(require_workload_open=stopped)
    monkeypatch.setattr(run.subprocess,'run',lambda *a,**kw:pytest.fail('stopped apply reached process'))
    with pytest.raises(modules.storage.DeliveryRefused,match='authority'):
        runtime.process(['terraform','apply','plan.tfplan'])
