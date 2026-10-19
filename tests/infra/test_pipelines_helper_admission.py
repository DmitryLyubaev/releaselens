"""Tracked replacements for critical checks previously embedded in helpers."""
import copy
import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from test_pipelines_save_lifecycle import captured


@pytest.fixture
def helpers(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    return importlib.import_module('scripts.pipelines.definition')


def test_setup_restoration_uses_saved_definition_projection(helpers):
    before = {'definition': captured('static-rev64'), 'unfinishedBuilds': {'count': 0, 'value': []}}
    for name in ('variableGroups', 'groupPermission', 'groupChecks'):
        before[name] = {'value': []}
    for role in ('test', 'prod', 'verify'):
        before[role] = {'authorization': {'parameters': {'serviceprincipalid': 'old-' + role}}}
        before[role + 'Permission'] = {'value': []}
        before[role + 'Checks'] = {'value': []}
    after = copy.deepcopy(before)
    after['definition'] = captured('variables-rev65')
    saved = copy.deepcopy(before)
    helpers.require_setup_restored(before, after)
    assert before == saved
    after['definition']['options'][0]['enabled'] = True
    with pytest.raises(Exception): helpers.require_setup_restored(before, after)


@pytest.mark.parametrize('clients', ['original', 'new'])
def test_versioned_restoration_adapter_consumes_actual_proof_shape(helpers, monkeypatch, tmp_path, clients):
    from scripts.pipelines.helper_templates import admission
    before = {'definition': captured('static-rev64'), 'unfinishedBuilds': {'count': 0, 'value': []}}
    for name in ('variableGroups', 'groupPermission', 'groupChecks'): before[name] = {'value': []}
    for role in ('test', 'prod', 'verify'):
        before[role] = {'authorization': {'parameters': {'serviceprincipalid': 'old-' + role}}}
        before[role + 'Permission'] = {'value': []}; before[role + 'Checks'] = {'value': []}
    after = copy.deepcopy(before); after['definition'] = captured('variables-rev65')
    principals = {role: {'client_id': 'new-' + role} for role in ('test', 'prod', 'verify')}
    if clients == 'new':
        for role in principals: after[role]['authorization']['parameters']['serviceprincipalid'] = principals[role]['client_id']
    (tmp_path / 'identity-apply').mkdir()
    data = {'setup-ado-restoration.private.json': {'kind': 'actual-browser-setup-restoration-readbacks',
            'session': 'p8-offline', 'source_sha': 'a' * 40, 'at': datetime.now(timezone.utc).isoformat(), 'readbacks': after},
            'platform-operational-baseline.private.json': {'readbacks': before},
            'identity-apply/actual.private.json': {'identities': principals}}
    for name, value in data.items(): (tmp_path / name).write_text(json.dumps(value))
    context = SimpleNamespace(S=tmp_path, SESSION='p8-offline', SOURCE='a' * 40,
                              read=lambda p: json.loads(p.read_text()))
    assert admission.verify_restoration(context, clients)
    proof = data['setup-ado-restoration.private.json']; proof['at'] = '2020-01-01T00:00:00+00:00'
    (tmp_path / 'setup-ado-restoration.private.json').write_text(json.dumps(proof))
    with pytest.raises(Exception): admission.verify_restoration(context, clients)


@pytest.fixture
def paid(repo_root, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    return create_paid_owner(tmp_path, monkeypatch)


def create_paid_owner(tmp_path, monkeypatch, now=None):
    from scripts.pipelines import startup, session
    now = now or datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(startup, 'utc_now', lambda: now)
    def write(name, value):
        p = tmp_path / name; p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text(json.dumps(value)); return session.sha256_file(p)
    raw = {'session': 'p8-offline', 'source_sha': 'a' * 40,
           'apply_started_at': now.isoformat()}
    own = {'pid': 123, 'created': (now - timedelta(minutes=5)).isoformat(),
           'command_line': 'python keeper.py', 'sha256': 'b' * 64}
    authority_hash = write('authority.json', raw)
    clock_hash = write('setup-clock.json', {'session': 'p8-offline', 'S0': (now - timedelta(minutes=10)).isoformat(),
                       'ready_or_cleanup_due_at': (now + timedelta(minutes=50)).isoformat()})
    review_hash = write('reviews/paid-execution-packet.json', {'verdict': 'PASS'})
    write('first-billable-apply-intent.json', {'session': 'p8-offline', 'source_sha': 'a' * 40,
          'apply_started_at': now.isoformat(), 'authority_sha256': authority_hash,
          'paid_packet_review_sha256': review_hash, 'maximum': 1})
    write('paid-keeper-handoff-ack.json', {'session': 'p8-offline', 'same_native_process': True,
          'paid_authority_sha256': authority_hash, 'setup_clock_sha256': clock_hash, 'pid': 123})
    write('watchdog-owned.json', own)
    write('setup-keeper-owned.json', {'process': own})
    write('watchdog-heartbeat.json', {'pid': 123, 'session': 'p8-offline', 'source_sha': 'a' * 40,
          'at': (now - timedelta(seconds=35)).isoformat()})
    return startup, tmp_path, raw, own, now, write


@pytest.mark.parametrize('fault', ['none', 'slow-query', 'changed-process', 'cleanup', 'cleanup-during-query',
                                 'changed-authority', 'changed-review'])
def test_paid_helper_uses_current_owned_process_and_fresh_heartbeat(paid, monkeypatch, fault):
    startup, root, raw, own, now, write = paid
    # Import inside the test so the initial missing tracked adapter fails locally.
    from scripts.pipelines.helper_templates import admission
    def snapshot(pid):
        assert pid == 123
        if fault == 'slow-query': monkeypatch.setattr(startup, 'utc_now', lambda: now + timedelta(seconds=6))
        if fault == 'cleanup-during-query': write('cleanup-native.json', {'pid': 999})
        return dict(own, command_line='reused PID') if fault == 'changed-process' else dict(own)
    if fault == 'cleanup': write('cleanup-native.json', {'pid': 999})
    if fault == 'changed-authority': write('authority.json', dict(raw, source_sha='c' * 40))
    if fault == 'changed-review': write('reviews/paid-execution-packet.json', {'verdict': 'FAIL'})
    def verify():
        context = SimpleNamespace(S=root, SESSION='p8-offline', SOURCE='a' * 40)
        return admission.verify_paid_owner(context, raw, native_snapshot=snapshot)
    if fault == 'none': assert verify()['same_native_keeper']['pid'] == 123
    else:
        with pytest.raises(Exception): verify()
    assert not (root / 'verified.json').exists()


def test_final_dispatch_checks_keeper_after_slow_source_validation(paid, monkeypatch):
    startup, root, raw, own, now, _ = paid
    from scripts.pipelines import session
    from scripts.pipelines.helper_templates import admission
    from test_pipelines_session import authority
    a = session.SessionAuthority(**dict(authority(start=now.isoformat()), session='p8-offline'))
    monkeypatch.setattr(session, 'load', lambda _: (a, {}, {}))
    monkeypatch.setattr(session, 'verify_source', lambda _: None)
    monkeypatch.setattr(session, 'utc_now', lambda: now)
    session.reserve_dispatch(root, 'release', now)
    context = SimpleNamespace(S=root, SESSION='p8-offline', SOURCE='a' * 40)
    def slow_source(_): monkeypatch.setattr(startup, 'utc_now', lambda: now + timedelta(seconds=6))
    monkeypatch.setattr(session, 'verify_source', slow_source)
    with pytest.raises(session.DeliveryRefused, match='authority'):
        admission.final_dispatch(context, 'release', native_snapshot=lambda pid: dict(own))
