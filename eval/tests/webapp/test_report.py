import pytest
from app.webapp.contracts import Observation, ContractRefused
from app.webapp import report
from app.webapp.checks import evaluate
from app.gateway.report import IdentifierError, write
from .test_checks import SHA, AT

def test_settings_without_teardown_cannot_pass_w6():
    result=evaluate('W6a',Observation('W6a',matches=dict.fromkeys('intended_settings_before settings_retained main_owner_signin staging_owner_signin'.split(),True)),commit=SHA,evidence='live',measured_at=AT)
    text=report.render([result],SHA)
    assert 'W6: not run' in text and '| W6a | pass | live |' in text
    assert '| W1 | not run | none |' in text

def test_cross_commit_result_is_refused():
    result=evaluate('W1',Observation('W1'),commit=SHA,evidence='live',measured_at=AT)
    with pytest.raises(ContractRefused): report.render([result],'b'*40)

@pytest.mark.parametrize('value',['app-private.azurewebsites.net','app-private.scm.azurewebsites.net','owner-private@example.test','00000000-0000-0000-0000-000000000001'])
def test_unsafe_artifact_writes_nothing_and_never_echoes_value(value,tmp_path):
    destination=tmp_path/'check.json'
    with pytest.raises(IdentifierError) as failure: write(destination,value)
    assert not destination.exists()
    assert value not in str(failure.value)

def test_rejected_output_path_never_echoes_private_filename(tmp_path):
    name='private-app.azurewebsites.net'
    with pytest.raises(IdentifierError) as failure: write(tmp_path/name,'00000000-0000-0000-0000-000000000001')
    assert name not in str(failure.value)
