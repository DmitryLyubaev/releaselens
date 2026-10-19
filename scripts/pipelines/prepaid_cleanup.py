"""Finite prepaid rollback shared by fresh setup helpers.

No paid authority is loaded or created. The reviewed context supplies bounded
Terraform/ARM transport, source/approval bindings and create-only evidence I/O.
Cleanup retains the original clock, removes Readers before identities, refuses
ambiguous consumed deletes and never adopts a transferred paid owner.
"""
import json
from datetime import datetime, timezone, timedelta

from app.delivery_search.contracts import DeliveryRefused
from .helper_templates import admission

class SetupRollback:

    def __init__(self, context):
        self.context = context

    def require_review(self, name):
        c = self.context
        v = c.read(c.S / 'reviews' / name)
        if not (v['verdict'] == 'PASS' and (not v['critical']) and (not v['important'])):
            raise DeliveryRefused('authority')
        if not all((c.sha(c.S / p) == h for p, h in v['files'].items())):
            raise DeliveryRefused('authority')
        return v

    def current_state(self, directory, out, label):
        c = self.context
        return json.loads(c.command(out, label, [c.TF, 'state', 'pull'], cwd=directory))

    def clock(self):
        c = self.context
        v = c.read(c.S / 'setup-clock.json')
        if not (v['session'] == c.SESSION and v['source_sha'] == c.SOURCE):
            raise DeliveryRefused('authority')
        if not v['approval_sha256'] == c.sha(c.S / 'execution-approval.json'):
            raise DeliveryRefused('authority')
        return v

    def conditional_stop(self):
        c = self.context
        p = c.S / 'setup-stop.json'
        if not p.exists():
            return False
        v = c.read(p)
        a = c.verify_approval()
        if not (v['kind'] == 'approved-conditional-stop' and v['session'] == c.SESSION and (v['source_sha'] == c.SOURCE)):
            raise DeliveryRefused('authority')
        if not (v['message_ref'] == a['message_ref'] and v['observed_failed_or_blocked_phase']):
            raise DeliveryRefused('authority')
        path = (c.S / v['actual_evidence_path']).resolve()
        if not (path.is_relative_to(c.S.resolve()) and path != c.S.resolve()):
            raise DeliveryRefused('authority')
        if not c.sha(path) == v['actual_evidence_sha256']:
            raise DeliveryRefused('authority')
        evidence = c.read(path)
        if not (evidence['kind'] == 'actual-operation-failure' and evidence['phase'] == v['observed_failed_or_blocked_phase']):
            raise DeliveryRefused('authority')
        if not (evidence['session'] == c.SESSION and evidence['source_sha'] == c.SOURCE):
            raise DeliveryRefused('authority')
        original = (c.S / evidence['original_path']).resolve()
        if not (original.is_relative_to(c.S.resolve()) and c.sha(original) == evidence['original_sha256']):
            raise DeliveryRefused('authority')
        observed = c.read(original)
        if not (type(observed.get('exit_code')) is int and observed['exit_code'] != 0 or observed.get('outcome') in ('timeout', 'blocked', 'failed')):
            raise DeliveryRefused('authority')
        return True

    def cleanup_admission(self, which, out=None):
        c = self.context
        self.require_prepaid_owner()
        v = self.clock()
        due = datetime.now(timezone.utc) >= datetime.fromisoformat(v['ready_or_cleanup_due_at'])
        try:
            abort = self.conditional_stop()
        except Exception:
            if not due:
                raise
            abort = False
        continuing = which == 'readers' and (not abort) and (not due)
        if continuing:
            self.require_review('diagnostic-result.json')
        if not (continuing or abort or due):
            raise DeliveryRefused('authority')
        try:
            restored = admission.verify_restoration(c, 'new' if continuing else 'original')
        except Exception:
            if not due:
                raise
            restored = False
        return {'at': c.now(), 'which': which, 'continuation': continuing, 'approved_conditional_stop': abort, 'original_setup_due': due, 'ADO_restoration_verified': restored, 'missing_due_restoration_does_not_extend_clock': True}

    def require_prepaid_owner(self):
        c = self.context
        if (c.S / 'first-billable-apply-intent.json').exists():
            raise DeliveryRefused('authority')
        if (c.S / 'paid-keeper-handoff-ack.json').exists():
            raise DeliveryRefused('authority')

    def delete_invocations(self, component):
        c = self.context
        originals = sorted(component.glob('preparation-*/apply-delete.command.private.json'))
        if not len(originals) <= 1:
            raise DeliveryRefused('authority')
        if originals:
            record = c.read(originals[0])
            if not (record['argv'][1] == 'apply' and record['argv'][-1] == 'cleanup.tfplan'):
                raise DeliveryRefused('authority')
            return 1
        return None if (component / 'delete.intent.json').exists() else 0

    def cleanup(self, which):
        from .session import operation_lock
        with operation_lock(self.context.S / 'setup-operations'):
            return self._cleanup(which)

    def cleanup_all(self):
        """One locked prepaid rollback: Readers first, then their identities."""
        from .session import operation_lock
        with operation_lock(self.context.S / 'setup-operations'):
            # Identity cleanup requires a stop or the original due time, even
            # when Reader removal alone was admitted as diagnostic continuation.
            self.cleanup_admission('identity')
            self._cleanup('readers')
            self._cleanup('identity')

    def _cleanup(self, which):
        c = self.context
        from scripts.pipelines.cleanup import validate_remaining_delete
        if not which in ('readers', 'identity'):
            raise DeliveryRefused('authority')
        directory = c.S / ('diagnostic-readers' if which == 'readers' else 'identity')
        component = c.S / ('setup-cleanup-' + which)
        if (component / 'complete.json').exists():
            previous = c.read(component / 'complete.json')
            if not (previous['empty_state'] is True and c.sha(directory / 'terraform.tfstate') == previous['state_sha256']):
                raise DeliveryRefused('authority')
            return
        self.require_review('setup-runtime.json')
        v = self.clock()
        admission = self.cleanup_admission(which)
        c.BLOCKING_DEADLINE = datetime.fromisoformat(v['prepaid_closeout_by'])
        c.bounded_seconds(1)
        component.mkdir(exist_ok=True)
        number = 1
        while (component / ('preparation-' + str(number))).exists():
            number += 1
        out = component / ('preparation-' + str(number))
        out.mkdir()
        c.write(out / 'admission.json', admission)
        if which == 'identity':
            p = c.S / 'diagnostic-readers/terraform.tfstate'
            if not (not p.exists() or not c.state_resources(c.read(p))):
                raise DeliveryRefused('authority')
        state_path = directory / 'terraform.tfstate'
        token, _ = c.owner_token(out)
        rp = c.read(c.PROPOSAL / 'resource-proposal.json')
        if not state_path.exists():
            if not not (c.S / ('reader-apply' if which == 'readers' else 'identity-apply') / 'apply.intent.json').exists():
                raise DeliveryRefused('authority')
            if which == 'identity':
                c.cloud_get(out, 'never-created-group-404', rp['groups']['identity'] + '?api-version=2021-04-01', token, expected=404)
            c.write(out / 'never-created.json', {'at': c.now(), 'which': which, 'state_absent': True, 'apply_intent_absent': True})
            return
        before = self.current_state(directory, out, 'state-before')
        ids = c.state_resources(before)
        if which == 'identity':
            expected = {x['address']: x['id'] for x in rp['identity_create_resources']}
        else:
            b = c.read(directory / 'plan-binding.json')
            expected = {'azurerm_role_assignment.diagnostic_reader[' + json.dumps(role) + ']': g['identity_resource_id'] + '/providers/Microsoft.Authorization/roleAssignments/' + g['assignment_name'] for role, g in b['grants'].items()}
        if not all((k in expected and rid.lower() == expected[k].lower() for k, rid in ids.items())):
            raise DeliveryRefused('authority')
        if which == 'identity' and rp['groups']['identity'].lower() in {rid.lower() for rid in ids.values()}:
            inventory = c.cloud_get(out, 'group-current-resources', rp['groups']['identity'] + '/resources?api-version=2021-04-01', token)
            allowed = {rid.lower() for a, rid in ids.items() if a.startswith('azurerm_user_assigned_identity.')}
            if not (not inventory.get('nextLink') and {row['id'].lower() for row in inventory['value']} == allowed):
                raise DeliveryRefused('authority')
            for n, rid in enumerate(sorted(allowed)):
                fics = c.cloud_get(out, 'identity-fic-children-' + str(n), rid + '/federatedIdentityCredentials?api-version=2023-01-31', token)
                expected_fics = {value.lower() for a, value in ids.items() if a.startswith('azurerm_federated_identity_credential.') and value.lower().startswith(rid + '/')}
                if not (not fics.get('nextLink') and {row['id'].lower() for row in fics['value']} == expected_fics):
                    raise DeliveryRefused('authority')
                grants = c.cloud_get(out, 'identity-direct-grants-' + str(n), rid + '/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01', token)
                if not (not grants.get('nextLink') and (not any((row['properties']['scope'].lower() == rid for row in grants['value'])))):
                    raise DeliveryRefused('authority')
        original = [{'address': k, 'id': ids.get(k, rid)} for k, rid in expected.items()]
        remaining = [{'address': k, 'id': rid} for k, rid in ids.items()]
        if remaining:
            if not not (component / 'delete.intent.json').exists():
                raise DeliveryRefused('authority')
            state_hash = c.sha(state_path)
            c.command(out, 'plan-delete', [c.TF, 'plan', '-destroy', '-input=false', '-no-color', '-refresh=true', '-parallelism=1', '-var-file=inputs.tfvars.json', '-out=cleanup.tfplan'], cwd=directory, seconds=240)
            shown = json.loads(c.command(out, 'show-delete', [c.TF, 'show', '-json', 'cleanup.tfplan'], cwd=directory))
            c.write(out / 'delete.shown.private.json', shown)
            validate_remaining_delete(shown, original, remaining)
            if not c.sha(state_path) == state_hash:
                raise DeliveryRefused('authority')
            self.require_prepaid_owner()
            c.bounded_seconds(1)
            c.write(component / 'delete.intent.json', {'at': c.now(), 'plan_sha256': c.sha(directory / 'cleanup.tfplan'), 'state_sha256': state_hash, 'allowed_resources': remaining, 'max_invocations': 1})
            self.require_prepaid_owner()
            c.bounded_seconds(1)
            c.command(out, 'apply-delete', [c.TF, 'apply', '-input=false', '-no-color', '-parallelism=1', 'cleanup.tfplan'], cwd=directory, seconds=900)
        after = self.current_state(directory, out, 'state-after')
        if not (not c.state_resources(after) and after['lineage'] == before['lineage']):
            raise DeliveryRefused('authority')
        for n, rid in enumerate(expected.values()):
            api = '2022-04-01' if which == 'readers' else '2021-04-01' if rid == rp['groups']['identity'] else '2023-01-31'
            c.cloud_get(out, 'absent-' + str(n), rid + '?api-version=' + api, token, expected=404)
        if not datetime.now(timezone.utc) < c.BLOCKING_DEADLINE:
            raise DeliveryRefused('authority')
        c.write(component / 'complete.json', {'at': c.now(), 'which': which, 'empty_state': True, 'state_sha256': c.sha(state_path), 'state_lineage': after['lineage'], 'state_serial': after['serial'], 'exact404': len(expected), 'actual_delete_apply_invocations': self.delete_invocations(component), 'setup_clock_sha256': c.sha(c.S / 'setup-clock.json')})
        c.checkpoint('setup-cleanup-' + which + '-complete', **{which + '_resources_current': 0})
