"""Owner preparation must finish before the short diagnostic submission window."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import json

import pytest


START = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)


def save_witness(root, time, write):
    write('save-witness.json', {'definition_id': 2, 'started_at': START.isoformat(),
          'completed_at': time[0].isoformat(), 'revision': 12,
          'variables': json.loads((root / 'diagnostic-preparation/variables.private.json').read_bytes())})


@pytest.fixture
def prepared(repo_root, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    spec = importlib.util.find_spec('scripts.pipelines.diagnostic_preparation')
    assert spec is not None, 'Missing staged owner diagnostic preparation workflow'
    module = importlib.import_module(spec.name)
    # Only Git/source inspection is replaced. All files, hashes, configuration,
    # timing, authority validation, locks and reservations are real.
    monkeypatch.setattr(module, 'verify_source', lambda authority: None)
    time = [START]
    monkeypatch.setattr(module, 'utc_now', lambda: time[0])

    def write(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return {'path': name, 'sha256': sha256(path.read_bytes()).hexdigest()}

    principal = {'subscription_id': 'subscription', 'tenant_id': 'tenant',
                 'issuer': 'https://issuer.example', 'subject': 'subject',
                 'oidc_request_origin': 'https://dev.azure.com',
                 'oidc_path_prefix': '/owned/project/_apis/distributedtask/hubs/build/'}
    authority = {'kind': 'identity-preflight', 'session': 'p8-offline', 'pipeline_id': 2,
                 'source_sha': 'a' * 40, 'tree_sha': 'b' * 40, 'approval_ref': 'human-approval',
                 'principals': {role: principal | {'client_id': 'client-' + role,
                              'object_id': 'object-' + role,
                              'service_connection_id': 'connection-' + role}
                                for role in ('test', 'prod', 'verify')}}
    expected = {'definition': {'id': 2, 'queueStatus': 'disabled', 'revision': 12,
                              'variables': {'unrelated': {'value': 'retained'}},
                              'triggers': []},
                'variableGroups': {'count': 1, 'value': [{'id': 1}]},
                'groupPermission': {'pipelines': [{'id': 2}]}, 'groupChecks': {'value': []},
                'unfinishedBuilds': {'count': 0, 'value': []}}
    for role in ('test', 'prod', 'verify'):
        expected[role] = {'id': 'connection-' + role, 'authorization': {'parameters': {
            'serviceprincipalid': 'client-' + role,
            'workloadIdentityFederationIssuer': 'https://issuer.example',
            'workloadIdentityFederationSubject': 'subject'}}}
        expected[role + 'Permission'] = {'pipelines': [{'id': 2}]}
        expected[role + 'Checks'] = {'value': []}
    bindings = {key: write(key + '.json', {'original': key})
                for key in ('identity', 'readers', 'rollback', 'rollback_review')}
    bindings['approval'] = write('approval.json', {key: authority[key] for key in
        ('session', 'source_sha', 'tree_sha')} | {'message_ref': 'human-approval'})
    bindings['baseline'] = write('baseline.json', {'readbacks': expected})
    clock = authority | {'S0': START.isoformat(), 'approval_sha256': bindings['approval']['sha256'],
                         'ready_or_cleanup_due_at': (START + timedelta(hours=1)).isoformat(),
                         'prepaid_closeout_by': (START + timedelta(hours=2)).isoformat(),
                         'outer_closeout_by': (START + timedelta(minutes=330)).isoformat()}
    config = {'kind': 'identity-diagnostic-preparation', 'authority_template': authority,
              'setup_clock': write('setup-clock.json', clock), 'expected_static': expected,
              'bindings': bindings}
    write('preparation-spec.json', config)

    def observation(name, readbacks):
        write(name, {'started_at': time[0].isoformat(), 'completed_at': time[0].isoformat(),
                     'readbacks': readbacks})

    def stage():
        observation('static.json', expected)
        module.stage(tmp_path, 'preparation-spec.json', 'static.json')

    def draft():
        module.draft(tmp_path)
        return json.loads((tmp_path / 'diagnostic-preparation/configuration.private.json').read_bytes())

    def finalize():
        observation('configured.json', draft())
        write('save-witness.json', {'definition_id': 2, 'started_at': time[0].isoformat(),
              'completed_at': time[0].isoformat(), 'revision': 12,
              'variables': json.loads((tmp_path / 'diagnostic-preparation/variables.private.json').read_bytes())})
        return module.finalize(tmp_path, 'configured.json', 'save-witness.json')

    def review():
        write('review.json', {'kind': 'independent-diagnostic-packet-review', 'verdict': 'PASS',
                            'critical': 0, 'important': 0, 'reviewer': 'independent-reviewer',
                            'packet_sha256': sha256((tmp_path / 'diagnostic-preparation/execution-packet.json').read_bytes()).hexdigest()})

    return module, tmp_path, time, write, observation, expected, stage, finalize, review


def test_slow_static_configuration_precedes_authority_and_submission_clocks(prepared):
    m, root, time, _, _, _, stage, finalize, _ = prepared
    time[0] += timedelta(minutes=10)
    stage()
    assert not (root / 'diagnostic-preparation/authority.json').exists()
    assert not (root / 'diagnostic-preparation/execution-packet.json').exists()
    packet = finalize()
    assert packet['queue_before'] == '2026-10-19T05:15:00+00:00'
    authority = json.loads((root / 'diagnostic-preparation/authority.json').read_bytes())
    assert authority['issued_at'] == '2026-10-19T05:10:00+00:00'
    assert authority['expires_at'] == '2026-10-19T05:40:00+00:00'


def test_slow_authority_variable_save_refuses_without_renewing_clock(prepared):
    m, root, time, write, observe, _, stage, _, _ = prepared
    stage()
    m.draft(root)
    original = (root / 'diagnostic-preparation/authority.json').read_bytes()
    time[0] += timedelta(minutes=6)
    save_witness(root, time, write)
    observe('configured.json', json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes()))
    with pytest.raises(m.DeliveryRefused, match='deadline'):
        m.finalize(root, 'configured.json', 'save-witness.json')
    assert (root / 'diagnostic-preparation/authority.json').read_bytes() == original
    assert not (root / 'diagnostic-preparation/execution-packet.json').exists()
    with pytest.raises(m.DeliveryRefused):
        m.draft(root)


@pytest.mark.parametrize('fault', ['enabled', 'missing-role', 'wrong-client', 'checks', 'active-build', 'authority-var'])
def test_final_packet_requires_actual_complete_disabled_configuration(prepared, fault):
    m, root, time, write, observe, _, stage, _, _ = prepared
    stage()
    m.draft(root)
    actual = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    if fault == 'enabled': actual['definition']['queueStatus'] = 'enabled'
    if fault == 'missing-role': del actual['prod']
    if fault == 'wrong-client': actual['test']['authorization']['parameters']['serviceprincipalid'] = 'foreign'
    if fault == 'checks': actual['prodChecks'] = {'value': [{'id': 99}]}
    if fault == 'active-build': actual['unfinishedBuilds'] = {'count': 1, 'value': [{'id': 1}]}
    if fault == 'authority-var': actual['definition']['variables']['P8_PREFLIGHT_SHA256']['value'] = 'c' * 64
    observe('configured.json', actual)
    save_witness(root, time, write)
    with pytest.raises(m.DeliveryRefused):
        m.finalize(root, 'configured.json', 'save-witness.json')
    assert not (root / 'diagnostic-preparation/execution-packet.json').exists()


def test_enable_reserves_single_dispatch_then_queue_consumes_it(prepared):
    m, root, _, _, observe, _, stage, finalize, review = prepared
    stage(); finalize(); review()
    expected = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    observe('before-enable.json', expected)
    m.admit(root, 'enable', 'before-enable.json', 'review.json')
    assert (root / 'diagnostic-preparation/dispatch.intent.json').exists()
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'enable', 'before-enable.json', 'review.json')
    expected['definition']['queueStatus'] = 'enabled'
    observe('before-queue.json', expected)
    m.admit(root, 'queue', 'before-queue.json', 'review.json')
    assert (root / 'diagnostic-preparation/queue.intent.json').exists()
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'queue', 'before-queue.json', 'review.json')


@pytest.mark.parametrize('phase', ['enable', 'queue'])
def test_slow_final_validation_cannot_reuse_an_early_clock(prepared, monkeypatch, phase):
    m, root, time, _, observe, _, stage, finalize, review = prepared
    stage(); finalize(); review()
    expected = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    if phase == 'queue':
        observe('enable.json', expected)
        m.admit(root, 'enable', 'enable.json', 'review.json')
        expected['definition']['queueStatus'] = 'enabled'
    observe('latest.json', expected)
    def slow_source(authority):
        time[0] += timedelta(minutes=3, seconds=1)
    monkeypatch.setattr(m, 'verify_source', slow_source)
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, phase, 'latest.json', 'review.json')
    assert not (root / 'diagnostic-preparation/queue.intent.json').exists()


@pytest.mark.parametrize('fault', ['stop', 'closed', 'changed-clock', 'changed-original', 'bad-review', 'stale-readback'])
def test_dispatch_refuses_changed_or_stopped_attempt_even_inside_window(prepared, fault):
    m, root, time, write, observe, _, stage, finalize, review = prepared
    stage(); finalize(); review()
    expected = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    observe('latest.json', expected)
    if fault == 'stop': write('setup-stop.json', {'stop': True})
    if fault == 'closed': write('closeout.json', {'closed': True})
    if fault == 'changed-clock': write('setup-clock.json', {'S0': 'reset'})
    if fault == 'changed-original': write('identity.json', {'changed': True})
    if fault == 'bad-review': write('review.json', {'verdict': 'PASS', 'packet_sha256': '0' * 64})
    if fault == 'stale-readback': time[0] += timedelta(minutes=2, seconds=1)
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'enable', 'latest.json', 'review.json')
    assert not (root / 'diagnostic-preparation/dispatch.intent.json').exists()


def test_setup_deadline_caps_authority_without_extending_original_clock(prepared):
    m, root, time, _, _, _, stage, finalize, _ = prepared
    clock = (root / 'setup-clock.json').read_bytes()
    time[0] += timedelta(minutes=34)
    stage(); packet = finalize()
    assert packet['queue_before'] == '2026-10-19T05:39:00+00:00'
    authority = json.loads((root / 'diagnostic-preparation/authority.json').read_bytes())
    assert authority['expires_at'] == '2026-10-19T06:00:00+00:00'
    assert (root / 'setup-clock.json').read_bytes() == clock


def test_partial_packet_intent_cannot_be_replayed(prepared):
    m, root, _, write, _, _, stage, _, _ = prepared
    stage()
    write('diagnostic-preparation/draft.intent.json', {'uncertain': True})
    with pytest.raises(m.DeliveryRefused): m.draft(root)
    assert not (root / 'diagnostic-preparation/authority.json').exists()


def test_cli_refuses_missing_inputs_without_echoing_private_data(prepared, capsys):
    m, root, _, write, _, _, _, _, _ = prepared
    write('PRIVATE-spec.json', {'PRIVATE': 'TOKEN'})
    assert m.main(['stage', '--session', str(root), '--spec', 'PRIVATE-spec.json',
                   '--readbacks', 'missing.json']) == 2
    assert capsys.readouterr().out == 'status=refused code=diagnostic-preparation\n'


def test_actual_platform_masks_authority_and_omits_false_flags(prepared):
    m, root, _, _, _, _, stage, finalize, _ = prepared
    stage(); finalize()
    actual = json.loads((root / 'configured.json').read_bytes())['readbacks']['definition']['variables']
    assert actual['P8_PREFLIGHT_JSON'] == {'value': None, 'isSecret': True}
    assert actual['P8_PREFLIGHT_SHA256'] == {'value': json.loads(
        (root / 'diagnostic-preparation/execution-packet.json').read_bytes())['authority_sha256']}
    assert actual['P8_PREFLIGHT_SOURCE_SHA'] == {'value': 'a' * 40}


def test_prior_four_minute_fifty_second_save_leaves_review_runway(prepared):
    m, root, time, write, observe, _, stage, _, _ = prepared
    stage(); m.draft(root)
    time[0] += timedelta(minutes=4, seconds=50)
    write('save-witness.json', {'definition_id': 2, 'started_at': START.isoformat(),
          'completed_at': time[0].isoformat(), 'revision': 12,
          'variables': json.loads((root / 'diagnostic-preparation/variables.private.json').read_bytes())})
    observe('configured.json', json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes()))
    packet = m.finalize(root, 'configured.json', 'save-witness.json')
    assert packet['queue_before'] == '2026-10-19T05:09:00+00:00'
    assert packet['expires_at'] == '2026-10-19T05:30:00+00:00'


def test_slow_readback_validation_is_rechecked_before_dispatch_intent(prepared, monkeypatch):
    m, root, time, _, observe, _, stage, finalize, review = prepared
    stage(); finalize(); review()
    expected = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    observe('latest.json', expected)
    original = m._observation
    def slow_read(*args):
        result = original(*args)
        time[0] += timedelta(minutes=3, seconds=1)
        return result
    monkeypatch.setattr(m, '_observation', slow_read)
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'enable', 'latest.json', 'review.json')
    assert not (root / 'diagnostic-preparation/dispatch.intent.json').exists()


@pytest.mark.parametrize('fault', ['prod-checks', 'permissions', 'variable-group', 'repository', 'tenant', 'schedule'])
def test_staging_cannot_bless_changed_protected_baseline(prepared, fault):
    m, root, _, write, observe, expected, stage, _, _ = prepared
    spec = json.loads((root / 'preparation-spec.json').read_bytes())
    if fault == 'prod-checks': spec['expected_static']['prodChecks'] = {'value': [{'id': 'removed-owner'}]}
    if fault == 'permissions': spec['expected_static']['testPermission'] = {'allPipelines': {'authorized': True}}
    if fault == 'variable-group': spec['expected_static']['variableGroups'] = {'count': 0, 'value': []}
    if fault == 'repository': spec['expected_static']['definition']['repository'] = {'id': 'foreign'}
    if fault == 'tenant': spec['expected_static']['test']['authorization']['parameters']['tenantid'] = 'foreign'
    if fault == 'schedule': spec['expected_static']['definition']['triggers'] = [{'triggerType': 'continuousIntegration'}]
    write('preparation-spec.json', spec)
    observe('changed.json', spec['expected_static'])
    with pytest.raises(m.DeliveryRefused):
        m.stage(root, 'preparation-spec.json', 'changed.json')
    assert not (root / 'diagnostic-preparation/staged.json').exists()


def test_slow_binding_hash_is_rechecked_before_finalization(prepared, monkeypatch):
    m, root, time, write, observe, _, stage, _, _ = prepared
    stage(); m.draft(root)
    save_witness(root, time, write)
    observe('configured.json', json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes()))
    original = m._binding
    def slow_binding(*args):
        result = original(*args)
        time[0] += timedelta(minutes=2, seconds=1)
        return result
    monkeypatch.setattr(m, '_binding', slow_binding)
    with pytest.raises(m.DeliveryRefused):
        m.finalize(root, 'configured.json', 'save-witness.json')
    assert not (root / 'diagnostic-preparation/execution-packet.json').exists()


def test_real_readback_envelope_and_revision_normalization(prepared):
    m, root, time, write, _, expected, _, _, _ = prepared
    write('static.json', {'at': time[0].isoformat(), 'completed_at': time[0].isoformat(), 'readbacks': expected})
    m.stage(root, 'preparation-spec.json', 'static.json')
    m.draft(root)
    saved = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    saved['definition']['revision'] = 13
    save_witness(root, time, write)
    witness = json.loads((root / 'save-witness.json').read_bytes()); witness['revision'] = 13
    write('save-witness.json', witness)
    write('final.json', {'at': time[0].isoformat(), 'completed_at': time[0].isoformat(), 'readbacks': saved})
    assert m.finalize(root, 'final.json', 'save-witness.json')['maximum_queues'] == 1


def test_queue_after_enabled_interval_is_refused_despite_fresh_readback(prepared):
    m, root, time, _, observe, _, stage, finalize, review = prepared
    stage(); finalize(); review()
    expected = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    observe('enable.json', expected)
    m.admit(root, 'enable', 'enable.json', 'review.json')
    time[0] += timedelta(seconds=121)
    expected['definition']['queueStatus'] = 'enabled'
    observe('queue.json', expected)
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'queue', 'queue.json', 'review.json')
    assert not (root / 'diagnostic-preparation/queue.intent.json').exists()


def test_actual_endpoint_and_definition_revision_dates_are_audit_only(prepared):
    m, root, time, write, observe, expected, _, _, _ = prepared
    baseline = json.loads((root / 'baseline.json').read_bytes())
    baseline['readbacks']['definition']['createdDate'] = '2026-10-19T02:44:02Z'
    for role in ('test', 'prod', 'verify'):
        endpoint = baseline['readbacks'][role]
        endpoint['modificationDate'] = '2026-10-19T02:44:02Z'
        endpoint['authorization']['parameters']['serviceprincipalid'] = 'old-client-' + role
        expected[role]['modificationDate'] = '2026-10-19T05:47:03Z'
    expected['definition']['createdDate'] = '2026-10-19T05:47:03Z'
    binding = write('baseline.json', baseline)
    spec = json.loads((root / 'preparation-spec.json').read_bytes())
    spec['bindings']['baseline'] = binding
    spec['expected_static'] = expected
    write('preparation-spec.json', spec)
    observe('static.json', expected)
    m.stage(root, 'preparation-spec.json', 'static.json')
    m.draft(root)
    saved = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    saved['definition']['createdDate'] = '2026-10-19T05:48:03Z'
    save_witness(root, time, write)
    observe('final.json', saved)
    assert m.finalize(root, 'final.json', 'save-witness.json')['maximum_queues'] == 1


@pytest.mark.parametrize('approved', [False, True])
def test_static_checkout_additions_need_exact_separately_bound_amendment(prepared, approved):
    m, root, _, write, observe, expected, _, _, _ = prepared
    baseline = json.loads((root / 'baseline.json').read_bytes())
    baseline['readbacks']['definition']['repository'] = {'id': 'same-repository', 'properties': {'fetchDepth': '1'}}
    baseline_binding = write('baseline.json', baseline)
    expected['definition']['repository'] = {'id': 'same-repository', 'clean': 'true', 'properties': {'fetchDepth': '1'}}
    spec = json.loads((root / 'preparation-spec.json').read_bytes())
    spec['bindings']['baseline'] = baseline_binding
    spec['expected_static'] = expected
    if approved:
        amendment = {'kind': 'human-approved-diagnostic-static-amendment', 'message_ref': 'human-approval',
                     'baseline_sha256': baseline_binding['sha256'],
                     'expected_static_sha256': sha256(json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                     'additions': {'repository.clean': 'true'}}
        spec['static_amendment'] = write('static-amendment.json', amendment)
    write('preparation-spec.json', spec)
    observe('static.json', expected)
    if approved:
        m.stage(root, 'preparation-spec.json', 'static.json')
        assert (root / 'diagnostic-preparation/staged.json').exists()
    else:
        with pytest.raises(m.DeliveryRefused): m.stage(root, 'preparation-spec.json', 'static.json')


def test_draft_uses_original_readback_age_not_stage_receipt_age(prepared):
    m, root, time, _, observe, expected, _, _, _ = prepared
    observe('static.json', expected)
    time[0] += timedelta(seconds=119)
    m.stage(root, 'preparation-spec.json', 'static.json')
    time[0] += timedelta(seconds=2)
    with pytest.raises(m.DeliveryRefused): m.draft(root)
    assert not (root / 'diagnostic-preparation/authority.json').exists()


def test_execution_packet_contains_exact_single_manual_diagnostic_submission(prepared):
    _, _, _, _, _, _, stage, finalize, _ = prepared
    stage(); packet = finalize()
    assert packet['queue'] == {'pipeline_id': 2, 'mode': 'identity-preflight',
                              'branch': 'refs/heads/main', 'sourceVersion': 'a' * 40, 'reason': 'Manual'}
    assert packet['enable_max_seconds'] == 120


def test_actual_static_get_may_omit_empty_variables_and_triggers(prepared):
    m, root, time, write, observe, expected, _, _, _ = prepared
    expected['definition'].pop('variables')
    expected['definition'].pop('triggers')
    spec = json.loads((root / 'preparation-spec.json').read_bytes())
    spec['expected_static'] = expected
    spec['bindings']['baseline'] = write('baseline.json', {'readbacks': expected})
    write('preparation-spec.json', spec)
    observe('static.json', expected)
    m.stage(root, 'preparation-spec.json', 'static.json')
    m.draft(root)
    saved = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    save_witness(root, time, write)
    observe('configured.json', saved)
    assert m.finalize(root, 'configured.json', 'save-witness.json')['maximum_queues'] == 1
