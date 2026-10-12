import pytest
from app.webapp.contracts import Observation, ContractRefused
from app.webapp.checks import evaluate, offline_edges

SHA = 'a' * 40
AT = '2026-10-12T01:00:00Z'
KEYS = {
 'W1': 'token_usable ask_succeeded answer_nonempty owner_metric_match',
 'W2': 'anonymous_main_refused anonymous_staging_refused forged_headers_refused deployment_health_only offline_edges_passed',
 'W3': 'initial_lifetime_elapsed expiry_advanced owner_audience_match ask_succeeded answer_nonempty',
 'W4': 'baseline_healthy staging_healthy approval_recorded swap_succeeded main_candidate staging_baseline slot_labels_retained',
 'W5': 'fixture_health_503 stage_failed promotion_rejected no_swap main_unchanged',
 'W6a': 'intended_settings_before settings_retained main_owner_signin staging_owner_signin',
 'W6b': 'webapp_group_absent gateway_group_absent plan_site_slot_absent deleted_apim_absent bootstrap_intact',
}

@pytest.mark.parametrize('check', KEYS)
def test_all_conditions_and_correct_evidence_are_required(check):
    matches = dict.fromkeys(KEYS[check].split(), True)
    kind = 'mixed' if check == 'W2' else 'workflow' if check in {'W4','W5'} else 'live'
    assert evaluate(check, Observation(check, matches=matches), commit=SHA, evidence=kind, measured_at=AT).verdict == 'pass'
    for name in matches:
        for bad in (False, None):
            changed = dict(matches)
            if bad is None: changed.pop(name)
            else: changed[name] = bad
            assert evaluate(check, Observation(check, matches=changed), commit=SHA, evidence=kind, measured_at=AT).verdict == 'fail'
    assert evaluate(check, Observation(check, matches=matches), commit=SHA, evidence='offline', measured_at=AT).verdict == 'fail'

def test_mismatched_check_is_refused():
    with pytest.raises(ContractRefused): evaluate('W1', Observation('W3'), commit=SHA, evidence='live', measured_at=AT)

def test_unrelated_green_ci_is_not_offline_edge_evidence():
    from .test_release import ci, REPO
    metadata = ci()
    assert not offline_edges(metadata, SHA, REPO)
    metadata['jobs'][0]['steps'] = [{'name':'Webapp offline token and route edges','conclusion':'success','status':'completed'}]
    assert offline_edges(metadata, SHA, REPO)
    metadata['jobs'][0]['steps'][0]['conclusion'] = 'skipped'
    assert not offline_edges(metadata, SHA, REPO)
