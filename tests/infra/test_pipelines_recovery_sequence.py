"""Composed offline controller replay; not a receipt of an Azure acceptance run.

Real diagnostic admission, paid ownership, authority parsing, runtime plan/apply,
query scoring, cross-stage receipts, dispatch, restoration and cleanup execute.
ADO/ARM/Terraform and bulk ingestion are scripted external boundaries. Separate
upload tests exercise real encoding/batching. No model, credential or network.
"""
import copy
from datetime import datetime, timedelta
import hashlib
import importlib
import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from test_pipelines_diagnostic_preparation import prepared
from test_pipelines_save_lifecycle import staged, captured
from test_pipelines_helper_admission import create_paid_owner
from test_pipelines_metadata_policy import POLICY


@pytest.mark.parametrize('fail_at', [None, 'identity', 'reader-removal', 'paid-admission',
                                    'foundation', 'agents', 'test', 'prod', 'negative', 'verify'])
def test_replay_contracts_and_owned_cleanup_after_each_mutation(prepared, monkeypatch, repo_root, fail_at):
    m, root, clock, _, observe, _, _, _, review = prepared
    from scripts.pipelines import (session, startup, paid_admission, quiescence, cleanup,
                                   run, identity, definition, evidence)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0]
    for module in (run, cleanup, quiescence, session): monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(session, 'utc_now', lambda: clock[0])
    monkeypatch.setattr(quiescence, 'verify_source', lambda _: None)
    monkeypatch.setattr(session, 'verify_source', lambda _: None)
    monkeypatch.setattr(identity, 'time', SimpleNamespace(time=lambda: clock[0].timestamp()))
    actual = staged(prepared)
    m.finalize(root, 'configured.json', 'save-witness.json'); review()
    observe('enable.json', actual); m.admit(root, 'enable', 'enable.json', 'review.json')
    actual['definition']['queueStatus'] = 'enabled'
    observe('queue.json', actual); m.admit(root, 'queue', 'queue.json', 'review.json')
    clock[0] += timedelta(minutes=1)
    baseline = json.loads((root / 'baseline.json').read_bytes())['readbacks']
    definition.require_setup_restored(baseline, copy.deepcopy(baseline))
    paid_root = root / 'paid'; paid_root.mkdir()
    _, _, raw, keeper, _, write = create_paid_owner(paid_root, monkeypatch, now=clock[0])
    monkeypatch.setattr(startup, 'utc_now', lambda: clock[0])
    subscription = '/subscriptions/sub/resourceGroups/rg-p8-offline-'
    groups = [subscription + name for name in ('search', 'identity', 'agents')]
    raw.update(tree_sha='b' * 40, release_digest='c' * 64, timing_profile='provisioning',
               metadata_policy=dict(POLICY), owner={'subscription_id': 'sub'},
               owner_ado_id='11111111-2222-4333-8444-555555555555',
               owned_group_ids=groups,
               principals=json.loads((root / 'diagnostic-preparation/authority.json').read_bytes())['principals'],
               scopes={}, pipeline_id=2,
               search_host='owned.search.windows.net', storage_host='owned.blob.core.windows.net')
    for key, minutes in zip(('queue_cutoff_at', 'cleanup_due_at', 'cleanup_start_by', 'closeout_by'), (210, 213, 215, 270)):
        raw[key] = (clock[0] + timedelta(minutes=minutes)).isoformat()
    rows = {}
    components = []
    for name in ('index-test', 'index-demo-prod', 'foundation', 'identity', 'agents'):
        if name.startswith('index-'):
            row = {'address': 'azapi_data_plane_resource.index', 'kind': 'index', 'host': raw['search_host'],
                   'name': 'p8-' + name[6:], 'id': raw['search_host'] + '/indexes/' + name[6:]}
        else:
            row = {'address': 'azurerm_resource_group.' + name, 'kind': 'arm', 'api_version': '2021-04-01',
                   'id': groups[{'foundation': 0, 'identity': 1, 'agents': 2}[name]]}
        rows[name] = []
        components.append({'name': name, 'directory': 'tf/' + name, 'config_sha256': 'a' * 64, 'resources': [row]})
    manifest = {k: raw[k] for k in ('session', 'source_sha', 'tree_sha')}; manifest['components'] = components
    retained = {'resources': [{'id': 'untouched'}], 'roleAssignments': [], 'deletedVaults': []}
    base = {k: raw[k] for k in ('session', 'source_sha', 'tree_sha')}
    base.update(observed_at=(clock[0] - timedelta(seconds=1)).isoformat(), canonical_absent=True,
                absence=[{'id': group, 'status': 404} for group in groups], retained=retained)
    raw['manifest_sha256'] = write('cleanup-manifest.json', manifest)
    raw['baseline_sha256'] = write('baseline.json', base)
    receipt = {k: raw[k] for k in ('session', 'source_sha', 'tree_sha', 'release_digest')}
    receipt.update(kind='human-live-approval', message_ref='offline-fixture-only', scope_sha256=session.scope_digest(raw))
    raw['approval_receipt_sha256'] = write('owner-approval-receipt.json', receipt)
    authority_hash = write('authority.json', raw)
    intent = session.read(paid_root / 'first-billable-apply-intent.json'); intent['authority_sha256'] = authority_hash
    write('first-billable-apply-intent.json', intent)
    ack = session.read(paid_root / 'paid-keeper-handoff-ack.json'); ack['paid_authority_sha256'] = authority_hash
    write('paid-keeper-handoff-ack.json', ack)
    a, _, _ = session.load(paid_root)  # Real scope, receipt, manifest and absence validation.
    paid_clock = (paid_root / 'authority.json').read_bytes()
    records, states, indexes, calls = {}, {}, {}, []
    class Store:
        def state(self, target): return states.get(target)
        def get(self, container, key, **kw): return records[key]
        def json(self, container, key): return json.loads(records[key])
        def put_once(self, container, key, data):
            assert key not in records; records[key] = data
        def put_json(self, key, value): self.put_once('evidence', key, run.canonical(value))
    inputs = root / 'query-inputs'; inputs.mkdir()
    questions = [{'qid': 'q%03d' % i, 'question': 'Find synthetic change number %d' % i,
                  'target': 'a%d' % i, 'entity_type': 'commit'} for i in range(229)]
    question_data = ''.join(json.dumps(q) + '\n' for q in questions).encode()
    (inputs / 'questions.jsonl').write_bytes(question_data)
    (inputs / 'questions.manifest.json').write_text(json.dumps({'sha256': hashlib.sha256(question_data).hexdigest()}))
    (inputs / 'links.jsonl').write_text('')
    vectors = np.zeros((229, 1536), dtype='float32'); vectors[:, 0] = 1
    np.save(inputs / 'queries.npy', vectors)
    for name, value in [('s2.json', {'top': 50, 'k': 50, 'filter': None}),
                        ('negative.json', {'top': 50, 'k': 50, 'filter': "artefact eq '__p8_no_match__'"})]:
        (inputs / name).write_text(json.dumps(value))
    bundle = SimpleNamespace(root=inputs, digest='c' * 64, manifest=SimpleNamespace(source_sha='a' * 40))
    query_count = []
    def search(request):
        target = 'test' if '/p8-test/' in request.url.path else 'demo-prod'
        if request.url.path.endswith('/docs/$count'): return httpx.Response(200, text=str(indexes[target]))
        if request.url.path.endswith('/docs/search'):
            body = json.loads(request.content); i = int(body['search'].split()[-1]); query_count.append(i)
            return httpx.Response(200, json={'value': [] if body.get('filter') or i >= 120 else
                                  [{'chunk_id': i, 'artefact': 'a%d' % i, '@search.score': 1.0}]})
        return httpx.Response(404)
    def upload(bundle, target, tokens, client, *, reservation):
        run.exclusive_json(reservation, {'offline-bulk-boundary': True})
        indexes['test' if target.index == 'p8-test' else 'demo-prod'] = 41825
    monkeypatch.setattr(run, 'upload_bundle', upload)
    def runtime(stage, run_id):
        r = object.__new__(run.Runtime); r.stage = stage; r.run_id = run_id
        r.work = root / ('runtime-' + stage); r.work.mkdir()
        r.publish = root / ('publish-' + stage); r.publish.mkdir()
        r.a = raw | {'storage_account_name': 'owned'}; r.bundle = bundle
        r.principal = {'subscription_id': 'sub', 'tenant_id': 'tenant'}
        r.proof = {'source_sha': 'a' * 40, 'release_digest': 'c' * 64, 'run_id': run_id}
        r.store = Store(); r.tf_env = {}; r.search_tokens = SimpleNamespace(token=lambda: 'offline-only')
        r.client = httpx.Client(transport=httpx.MockTransport(search))
        def process(args, **kw):
            cwd = kw['cwd']; out = ''
            if args[0] == 'terraform':
                target = 'test' if cwd.name == 'index-test' else 'demo-prod'
                if args[1] == 'plan': (cwd / 'plan.tfplan').write_bytes(('offline-' + target).encode())
                if args[1] == 'show': out = json.dumps({'resource_changes': [{'mode': 'managed',
                    'address': 'azapi_data_plane_resource.index', 'type': 'azapi_data_plane_resource',
                    'change': {'actions': ['create'], 'after': {'type': 'Microsoft.Search/searchServices/indexes@2024-07-01',
                       'parent_id': raw['search_host'], 'name': 'p8-' + target}}}]})
                if args[1] == 'apply':
                    calls.append('apply-' + target); indexes[target] = 0
                    states[target] = {'serial': 1, 'lineage': target}
                    rows['index-' + target] = copy.deepcopy(next(c['resources'] for c in components if c['name'] == 'index-' + target))
            return SimpleNamespace(returncode=0, stdout=out, stderr='')
        r.process = process; r.claim_release()
        return r
    stages, runtimes = [], []
    def measured_grades(directory):
        # The complete original-registry grader is covered separately. This
        # composed transport scenario delegates the two dispatch prerequisites
        # to the real runtime validators of the actual preceding gate files.
        try:
            runtimes[-1].prior_gate('test'); runtimes[-1].prior_gate('prod')
            return {'P8-4': {'status': 'PASS'}, 'P8-5': {'status': 'PASS'}}
        except (KeyError, session.DeliveryRefused):
            return {'P8-4': {'status': 'FAIL'}, 'P8-5': {'status': 'FAIL'}}
    monkeypatch.setattr(evidence, 'grade', measured_grades)
    try:
        for role in ('test', 'prod', 'verify'):
            principal = raw['principals'][role]
            expiry = clock[0].timestamp() + 300
            claims = {'federation': {'iss': principal['issuer'], 'sub': principal['subject'],
                                    'aud': 'api://AzureADTokenExchange', 'exp': expiry},
                      'access': {'tid': principal['tenant_id'], 'oid': principal['object_id'],
                                 'appid': principal['client_id'], 'exp': expiry}}
            # Authentication transport is synthetic; the exact principal validator executes.
            identity.verify_principal({'id': principal['subscription_id'], 'tenantId': principal['tenant_id'],
                                      'state': 'Enabled', 'user': {'type': 'servicePrincipal', 'name': principal['client_id']}},
                                      claims, principal)
        stages.append('identity')
        if fail_at == 'identity': raise RuntimeError('injected')
        definition.require_setup_restored(baseline, copy.deepcopy(baseline))
        stages.append('reader-removal')
        if fail_at == 'reader-removal': raise RuntimeError('injected')
        paid_admission.verify_paid_owner(paid_root, raw, session=a.session, source_sha=a.source_sha,
                                          native_snapshot=lambda pid: dict(keeper))
        stages.append('paid-admission')
        if fail_at == 'paid-admission': raise RuntimeError('injected')
        for name in ('foundation', 'agents'):
            session.reserve_operation(paid_root, 'create-' + name, a.manifest_sha256)
            rows[name] = copy.deepcopy(next(c['resources'] for c in components if c['name'] == name))
            stages.append(name)
            if fail_at == name: raise RuntimeError('injected')
        for mode, run_id, runtime_stages in [('release', 11, ('build', 'test', 'plan-prod', 'prod')),
                                             ('negative', 12, ('negative',)), ('scheduled', 13, ('verify',))]:
            session.reserve_dispatch(paid_root, mode, clock[0])
            quiescence.register_build(paid_root, mode, {'id': run_id, 'definition': {'id': 2}, 'sourceVersion': a.source_sha,
                'sourceBranch': 'refs/heads/main', 'reason': 'schedule' if mode == 'scheduled' else 'manual', 'queueTime': clock[0].isoformat()})
            monkeypatch.setenv('BUILD_REASON', 'Schedule' if mode == 'scheduled' else 'Manual')
            for stage in runtime_stages:
                r = runtime(stage, run_id)
                with r.client: result = r.execute()
                runtimes.append(r)
                assert result is (stage != 'negative')
                stages.append(stage)
                if fail_at == stage: raise RuntimeError('injected')
    except RuntimeError as exc:
        assert str(exc) == 'injected'
    finally:
        # The clock is advanced to the immutable due time, not reset. Due cleanup
        # must remain possible even if workload evidence or browser restoration is missing.
        clock[0] = datetime.fromisoformat(a.cleanup_due_at)
        quiescence.require_restored_definition(captured('static-rev64'), captured('variables-rev65'), 2)
        class Driver:
            def active_owned(self): return []
            def verify_owner(self): pass
            def state(self, component): return copy.deepcopy(rows[component['name']])
            def plan(self, component, attempt, remaining):
                return {'resource_changes': [{'mode': 'managed', 'address': row['address'],
                    'change': {'actions': ['delete'], 'before': {'id': row['id']}, 'after': None}} for row in remaining]}
            def apply(self, component, attempt): rows[component['name']] = []
            def retained(self): return retained
            def canonical_absence(self): return not any(rows.values())
        result = cleanup.execute(paid_root, clock[0], driver=Driver())
        assert result['physical_pass'] is True and result['retained_unchanged'] is True
        assert (paid_root / 'authority.json').read_bytes() == paid_clock
        assert cleanup.execute(paid_root, clock[0], driver=Driver()) == result
        assert len(list((paid_root / 'intents').glob('cleanup-*.json'))) == 1
        with pytest.raises(session.DeliveryRefused): session.reserve_dispatch(paid_root, 'release', clock[0])
    if fail_at is None:
        assert stages[-7:] == ['agents', 'build', 'test', 'plan-prod', 'prod', 'negative', 'verify']
        assert len(query_count) == 916
        assert calls == ['apply-test', 'apply-demo-prod']
    else: assert stages[-1] == fail_at
