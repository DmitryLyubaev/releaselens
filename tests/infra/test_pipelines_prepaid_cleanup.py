"""Prepaid rollback runs without any paid packet/clock or cloud transport."""
import copy
from datetime import datetime, timedelta, timezone
import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def rollback(repo_root, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    from app.delivery_search.contracts import exclusive_json, sha256_file
    now = datetime.now(timezone.utc)
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True); exclusive_json(path, value)
    def read(path): return json.loads(path.read_bytes())
    group = '/subscriptions/sub/resourceGroups/rg-p8-offline-identity'
    identities = {role: group + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/' + role
                  for role in ('test', 'prod', 'verify')}
    ids = {'azurerm_resource_group.identity': group}
    for role, rid in identities.items():
        ids['azurerm_user_assigned_identity.' + role] = rid
        ids['azurerm_federated_identity_credential.' + role] = rid + '/federatedIdentityCredentials/' + role
    grants = {role: {'identity_resource_id': rid, 'assignment_name': 'reader-' + role}
              for role, rid in identities.items()}
    reader_ids = {'azurerm_role_assignment.diagnostic_reader[' + json.dumps(role) + ']':
                  value['identity_resource_id'] + '/providers/Microsoft.Authorization/roleAssignments/' + value['assignment_name']
                  for role, value in grants.items()}
    proposal = {'groups': {'identity': group}, 'identity_create_resources': [
        {'address': address, 'id': rid} for address, rid in ids.items()]}
    write(tmp_path / 'resource-proposal.json', proposal)
    write(tmp_path / 'execution-approval.json', {'message_ref': 'offline-human-approved-conditional-stop'})
    write(tmp_path / 'setup-clock.json', {'session': 'p8-offline', 'source_sha': 'a' * 40,
        'approval_sha256': sha256_file(tmp_path / 'execution-approval.json'), 'S0': (now - timedelta(minutes=5)).isoformat(),
        'ready_or_cleanup_due_at': (now + timedelta(minutes=55)).isoformat(),
        'prepaid_closeout_by': (now + timedelta(minutes=115)).isoformat()})
    write(tmp_path / 'reviews/setup-runtime.json', {'verdict': 'PASS', 'critical': 0, 'important': 0,
        'files': {'resource-proposal.json': sha256_file(tmp_path / 'resource-proposal.json')}})
    for name, values in [('identity', ids), ('diagnostic-readers', reader_ids)]:
        write(tmp_path / name / 'terraform.tfstate', {'lineage': name, 'serial': 1, 'ids': values})
    write(tmp_path / 'diagnostic-readers/plan-binding.json', {'grants': grants})
    calls = []
    def command(out, label, argv, *, cwd, seconds=None):
        state = read(cwd / 'terraform.tfstate')
        if label.startswith('state-'): return json.dumps(state)
        if label == 'plan-delete': (cwd / 'cleanup.tfplan').write_bytes(b'exact-offline-delete-plan'); return ''
        if label == 'show-delete':
            return json.dumps({'resource_changes': [{'mode': 'managed', 'address': address,
                'change': {'actions': ['delete'], 'before': {'id': rid}, 'after': None}} for address, rid in state['ids'].items()]})
        assert label == 'apply-delete'
        calls.append(cwd.name)
        (cwd / 'terraform.tfstate').write_text(json.dumps(dict(state, serial=state['serial'] + 1, ids={})))
        write(out / 'apply-delete.command.private.json', {'argv': argv, 'exit_code': 0})
        return ''
    def cloud_get(out, label, url, token, expected=200):
        if expected == 404: return {'status': 404}
        if label == 'group-current-resources': return {'value': [{'id': rid} for rid in identities.values()]}
        if label.startswith('identity-fic-children'):
            rid = url.split('/federatedIdentityCredentials?')[0]
            return {'value': [{'id': rid + '/federatedIdentityCredentials/' + rid.rsplit('/', 1)[-1]}]}
        assert label.startswith('identity-direct-grants')
        return {'value': []}
    context = SimpleNamespace(S=tmp_path, P=repo_root, PROPOSAL=tmp_path, SESSION='p8-offline', SOURCE='a' * 40,
        TF='terraform', read=read, write=write, sha=sha256_file, now=lambda: now.isoformat(),
        verify_approval=lambda: read(tmp_path / 'execution-approval.json'), command=command,
        bounded_seconds=lambda seconds: seconds, owner_token=lambda out: ('offline-token', None),
        state_resources=lambda state: state['ids'], cloud_get=cloud_get, checkpoint=lambda *a, **kw: None)
    return context, calls, now


def stop(context, phase):
    context.write(context.S / 'failure-original.json', {'exit_code': 1})
    context.write(context.S / 'actual-failure.json', {'kind': 'actual-operation-failure', 'phase': phase,
        'session': context.SESSION, 'source_sha': context.SOURCE, 'original_path': 'failure-original.json',
        'original_sha256': context.sha(context.S / 'failure-original.json')})
    context.write(context.S / 'setup-stop.json', {'kind': 'approved-conditional-stop',
        'session': context.SESSION, 'source_sha': context.SOURCE, 'message_ref': context.verify_approval()['message_ref'],
        'observed_failed_or_blocked_phase': phase, 'actual_evidence_path': 'actual-failure.json',
        'actual_evidence_sha256': context.sha(context.S / 'actual-failure.json')})


@pytest.mark.parametrize('phase', ['identity-apply', 'reader-apply', 'static-save', 'variable-save', 'finalize', 'enable', 'queue', 'authentication'])
def test_prepaid_stop_removes_readers_before_identities_without_paid_authority(rollback, monkeypatch, phase):
    context, calls, _ = rollback
    module = importlib.import_module('scripts.pipelines.prepaid_cleanup')
    from scripts.pipelines.helper_templates import admission
    monkeypatch.setattr(admission, 'verify_restoration', lambda ctx, clients: True)
    stop(context, phase)
    original_clock = (context.S / 'setup-clock.json').read_bytes()
    owner = module.SetupRollback(context)
    owner.cleanup_all()
    assert calls == ['diagnostic-readers', 'identity']
    for name in ('diagnostic-readers', 'identity'):
        assert context.read(context.S / name / 'terraform.tfstate')['ids'] == {}
    assert context.read(context.S / 'setup-cleanup-readers/complete.json')['exact404'] == 3
    assert context.read(context.S / 'setup-cleanup-identity/complete.json')['exact404'] == 7
    owner.cleanup('readers'); owner.cleanup('identity')
    assert calls == ['diagnostic-readers', 'identity']
    assert (context.S / 'setup-clock.json').read_bytes() == original_clock
    assert not any((context.S / n).exists() for n in ('authority.json', 'execution-packet.json', 'paid-clock.json'))


@pytest.mark.parametrize('fault', ['paid-intent', 'ambiguous-delete', 'missing-state', 'restoration'])
def test_ambiguous_or_transferred_prepaid_ownership_never_replays_delete(rollback, monkeypatch, fault):
    context, calls, _ = rollback
    module = importlib.import_module('scripts.pipelines.prepaid_cleanup')
    from scripts.pipelines.helper_templates import admission
    def restored(*args):
        if fault == 'restoration': raise ValueError('browser unavailable')
        return True
    monkeypatch.setattr(admission, 'verify_restoration', restored)
    stop(context, 'finalize')
    if fault == 'paid-intent': context.write(context.S / 'first-billable-apply-intent.json', {})
    if fault == 'ambiguous-delete': context.write(context.S / 'setup-cleanup-readers/delete.intent.json', {})
    if fault == 'missing-state':
        # Use a never-created separate path, keeping the original state preserved.
        (context.S / 'diagnostic-readers/terraform.tfstate').rename(context.S / 'readers-state-original.json')
        context.write(context.S / 'reader-apply/apply.intent.json', {})
    with pytest.raises(Exception): module.SetupRollback(context).cleanup('readers')
    assert calls == []


def test_original_due_time_allows_cleanup_when_browser_is_unavailable(rollback, monkeypatch):
    context, calls, now = rollback
    from scripts.pipelines import prepaid_cleanup
    from scripts.pipelines.helper_templates import admission
    class Due(datetime):
        @classmethod
        def now(cls, tz=None): return now + timedelta(minutes=56)
    monkeypatch.setattr(prepaid_cleanup, 'datetime', Due)
    def unavailable(*args): raise ValueError('browser unavailable')
    monkeypatch.setattr(admission, 'verify_restoration', unavailable)
    original = (context.S / 'setup-clock.json').read_bytes()
    prepaid_cleanup.SetupRollback(context).cleanup_all()
    assert calls == ['diagnostic-readers', 'identity']
    assert (context.S / 'setup-clock.json').read_bytes() == original


def test_cleanup_cannot_enter_while_another_setup_owner_holds_lock(rollback):
    context, calls, _ = rollback
    from scripts.pipelines import prepaid_cleanup, session
    from scripts.pipelines.helper_templates import admission
    stop(context, 'finalize')
    with admission.setup_operations(context):
        with pytest.raises(session.DeliveryRefused, match='reserved'):
            prepaid_cleanup.SetupRollback(context).cleanup('readers')
    assert calls == []


def test_paid_transfer_during_delete_planning_refuses_before_consuming_apply(rollback, monkeypatch):
    context, calls, _ = rollback
    from scripts.pipelines import prepaid_cleanup
    from scripts.pipelines.helper_templates import admission
    monkeypatch.setattr(admission, 'verify_restoration', lambda *args: True)
    stop(context, 'finalize')
    original_command = context.command
    def command(out, label, argv, **kwargs):
        result = original_command(out, label, argv, **kwargs)
        if label == 'plan-delete': context.write(context.S / 'paid-keeper-handoff-ack.json', {})
        return result
    context.command = command
    with pytest.raises(Exception): prepaid_cleanup.SetupRollback(context).cleanup('readers')
    assert calls == []
    assert not (context.S / 'setup-cleanup-readers/delete.intent.json').exists()
