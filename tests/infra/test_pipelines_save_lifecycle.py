"""Observed Save shapes across real admission and restoration controllers.

Only Git inspection and the external browser/ARM transport are replaced. The
production validators, files, hashes, clocks and consumed intents stay real.
"""
import copy
import importlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from test_pipelines_diagnostic_preparation import (
    prepared, checkout_null_spec, bind_checkout_amendment, START,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'ado-save'


def captured(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def staged(prepared):
    m, root, time, write, observe, _, _, _, review = prepared
    spec, amendment, expected = checkout_null_spec(
        prepared, null_defaults={'repository.clean': 'true'})
    # Both response fragments are independently captured, not derived from the
    # expected controller output. Other readbacks use the bounded test fixture.
    expected['definition'].update(captured('static-rev64'))
    baseline = json.loads((root / 'baseline.json').read_bytes())
    baseline['readbacks']['definition'].update({k: v for k, v in captured('static-rev64').items()
                                               if k not in ('triggers', 'options')})
    spec['bindings']['baseline'] = write('baseline.json', baseline)
    amendment['baseline_sha256'] = spec['bindings']['baseline']['sha256']
    amendment['additions'] = {'options': captured('static-rev64')['options']}
    bind_checkout_amendment(prepared, spec, amendment, expected)
    m.stage(root, 'preparation-spec.json', 'static.json')
    m.draft(root)
    time[0] += timedelta(seconds=20)
    actual = json.loads((root / 'diagnostic-preparation/configuration.private.json').read_bytes())
    actual['definition'].update(captured('variables-rev65'))
    write('save-witness.json', {'definition_id': 2, 'started_at': START.isoformat(),
        'completed_at': time[0].isoformat(), 'revision': 65,
        'variables': json.loads((root / 'diagnostic-preparation/variables.private.json').read_bytes())})
    observe('configured.json', actual)
    return actual


def test_observed_variable_save_id_change_reaches_queue_and_restoration(prepared):
    m, root, time, _, observe, _, _, _, review = prepared
    actual = staged(prepared)
    original = (root / 'baseline.json').read_bytes()
    packet = m.finalize(root, 'configured.json', 'save-witness.json')
    assert packet['queue_before'] == '2026-10-19T05:05:20+00:00'
    review()
    observe('enable.json', actual)
    m.admit(root, 'enable', 'enable.json', 'review.json')
    time[0] += timedelta(seconds=15)
    actual['definition']['queueStatus'] = 'enabled'
    # A later regeneration is simulated, not claimed as observed on Azure.
    actual['definition']['triggers'][0]['schedules'][0]['scheduleJobId'] = 'simulated-enable-id'
    observe('queue.json', actual)
    m.admit(root, 'queue', 'queue.json', 'review.json')
    intent = (root / 'diagnostic-preparation/queue.intent.json').read_bytes()
    assert json.loads(intent)['uncertain_submission_consumes_attempt'] is True
    with pytest.raises(m.DeliveryRefused):
        m.admit(root, 'queue', 'queue.json', 'review.json')
    assert (root / 'diagnostic-preparation/queue.intent.json').read_bytes() == intent
    assert (root / 'baseline.json').read_bytes() == original
    q = importlib.import_module('scripts.pipelines.quiescence')
    before = captured('static-rev64')
    restored = captured('variables-rev65')
    q.require_restored_definition(before, restored, 2)
    assert before['triggers'][0]['schedules'][0]['scheduleJobId'] == 'b6672ffc-471a-4497-b8eb-55e1da2ac6b6'


@pytest.mark.parametrize('phase', ['finalize', 'enable', 'queue'])
@pytest.mark.parametrize('change', ['branch', 'hour', 'minute', 'days', 'zone', 'changes',
                                  'count', 'option', 'variable', 'secret', 'override',
                                  'source', 'client', 'checks', 'permission'])
def test_real_operational_drift_is_refused_at_each_save_boundary(prepared, phase, change):
    m, root, _, _, observe, _, _, _, review = prepared
    actual = staged(prepared)
    if phase != 'finalize':
        m.finalize(root, 'configured.json', 'save-witness.json')
        review()
    if phase == 'queue':
        observe('enable.json', actual)
        m.admit(root, 'enable', 'enable.json', 'review.json')
        actual['definition']['queueStatus'] = 'enabled'
    schedule = actual['definition']['triggers'][0]['schedules'][0]
    changes = {'branch': ('branchFilters', ['+refs/heads/main']), 'hour': ('startHours', 3),
               'minute': ('startMinutes', 1), 'days': ('daysToBuild', 127),
               'zone': ('timeZoneId', 'E. Australia Standard Time'),
               'changes': ('scheduleOnlyWithChanges', False)}
    if change in changes:
        key, value = changes[change]; schedule[key] = value
    elif change == 'count': actual['definition']['triggers'][0]['schedules'].append(copy.deepcopy(schedule))
    elif change == 'option': actual['definition']['options'][0]['definition']['id'] = 'foreign-option'
    elif change == 'variable': actual['definition']['variables']['P8_PREFLIGHT_SHA256']['value'] = 'c' * 64
    elif change == 'secret': actual['definition']['variables']['P8_PREFLIGHT_JSON']['isSecret'] = False
    elif change == 'override': actual['definition']['variables']['P8_PREFLIGHT_SOURCE_SHA']['allowOverride'] = True
    elif change == 'source': actual['definition']['variables']['P8_PREFLIGHT_SOURCE_SHA']['value'] = 'c' * 40
    elif change == 'client': actual['prod']['authorization']['parameters']['serviceprincipalid'] = 'foreign'
    elif change == 'checks': actual['prodChecks'] = {'value': [{'id': 999}]}
    elif change == 'permission': actual['prodPermission'] = {'allPipelines': {'authorized': True}}
    observe('changed.json', actual)
    with pytest.raises(m.DeliveryRefused):
        if phase == 'finalize': m.finalize(root, 'changed.json', 'save-witness.json')
        else: m.admit(root, phase, 'changed.json', 'review.json')
    assert not (root / 'diagnostic-preparation/queue.intent.json').exists()


@pytest.mark.parametrize('phase', ['stage', 'draft', 'finalize', 'enable', 'queue'])
def test_stop_at_each_prepaid_transition_prevents_later_admission(prepared, phase):
    m, root, _, write, observe, expected, _, _, review = prepared
    if phase in ('stage', 'draft'):
        if phase == 'draft':
            observe('static.json', expected)
            m.stage(root, 'preparation-spec.json', 'static.json')
    else:
        actual = staged(prepared)
        if phase in ('enable', 'queue'):
            m.finalize(root, 'configured.json', 'save-witness.json'); review()
        if phase == 'queue':
            observe('enable.json', actual)
            m.admit(root, 'enable', 'enable.json', 'review.json')
            actual['definition']['queueStatus'] = 'enabled'
        observe('next.json', actual)
    clock = (root / 'setup-clock.json').read_bytes()
    write('setup-stop.json', {'kind': 'test-stop'})
    with pytest.raises(m.DeliveryRefused):
        if phase == 'stage':
            observe('static.json', expected); m.stage(root, 'preparation-spec.json', 'static.json')
        elif phase == 'draft': m.draft(root)
        elif phase == 'finalize': m.finalize(root, 'configured.json', 'save-witness.json')
        else: m.admit(root, phase, 'next.json', 'review.json')
    assert not (root / 'diagnostic-preparation/queue.intent.json').exists()
    assert (root / 'setup-clock.json').read_bytes() == clock


def test_pre_save_capture_remains_strict_about_schedule_identity(prepared):
    m, root, _, _, observe, _, _, _, _ = prepared
    spec, amendment, expected = checkout_null_spec(prepared, null_defaults={'repository.clean': 'true'})
    bind_checkout_amendment(prepared, spec, amendment, expected)
    actual = copy.deepcopy(expected)
    actual['definition']['triggers'][0]['schedules'][0]['scheduleJobId'] = 'unexplained-before-save'
    observe('static.json', actual)
    with pytest.raises(m.DeliveryRefused): m.stage(root, 'preparation-spec.json', 'static.json')
    assert not (root / 'diagnostic-preparation/staged.json').exists()


@pytest.mark.parametrize('change', ['branchFilters', 'startHours', 'timeZoneId', 'daysToBuild',
                                  'scheduleOnlyWithChanges', 'option-id', 'extra-trigger'])
def test_restoration_does_not_ignore_operational_or_unrelated_id_changes(prepared, change):
    q = importlib.import_module('scripts.pipelines.quiescence')
    before, after = captured('static-rev64'), captured('variables-rev65')
    if change == 'option-id': after['options'][0]['definition']['id'] = 'different-option'
    elif change == 'extra-trigger': after['triggers'].append({'triggerType': 'continuousIntegration'})
    else: after['triggers'][0]['schedules'][0][change] = 'changed'
    with pytest.raises(q.DeliveryRefused): q.require_restored_definition(before, after, 2)
