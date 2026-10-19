from test_pipelines_metadata_policy import POLICY
"""Direct absence must follow a real bound 200, never a delete-intent timestamp."""
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
AUTH={'metadata_policy':dict(POLICY),'session':'p8-offline','source_sha':'a'*40,'manifest_sha256':'b'*64,
      'search_host':'p8observer.search.windows.net'}
MANIFEST={'components':[{'name':'index-test','resources':[{'kind':'index','host':AUTH['search_host'],'name':'p8-test'}]}]}
NOW=datetime(2026,10,19,tzinfo=timezone.utc)

from scripts.pipelines.physical_observation import observe_test_index as observe


def client(status):
    def handle(request):
        assert str(request.url)=='https://p8observer.search.windows.net/indexes/p8-test?api-version=2024-07-01'
        assert request.method=='GET' and request.headers['Authorization']=='Bearer synthetic-test-only'
        return httpx.Response(status,json={'name':'p8-test'} if status==200 else {'error':{'code':'IndexNotFound'}})
    return httpx.Client(transport=httpx.MockTransport(handle))

def reserve():return 'budgets/metadata/244.json'


@pytest.mark.parametrize('claim',['budgets/metadata/108.json','budgets/metadata/235.json',
    'budgets/metadata/243.json','budgets/metadata/256.json','foreign'])
def test_physical_callback_cannot_use_workload_or_contingency_slots(tmp_path,claim):
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('unreserved GET'))) as c:
        from app.delivery_search.contracts import DeliveryRefused
        with pytest.raises(DeliveryRefused):
            observe(tmp_path,AUTH,MANIFEST,c,'offline',lambda:claim,NOW,cleanup=False)

def test_absence_retains_actual_observation_and_prior_presence_hash(tmp_path):
    with client(200) as c:observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=False)
    with client(404) as c:proof=observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=True)
    assert proof['status']==404 and proof['at']=='2026-10-19T00:00:00+00:00'
    assert proof['previously_present'] is True and len(proof['prior_presence_sha256'])==64
    assert proof['first_delete_timestamp_not_inferred'] is True
    assert json.loads((tmp_path/'independent-test-index-first-absence.json').read_bytes())==proof

def test_404_without_actual_prior_200_cannot_claim_removal(tmp_path):
    with client(404) as c:proof=observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=True)
    assert proof['previously_present'] is False
    assert not (tmp_path/'independent-test-index-first-absence.json').exists()

@pytest.mark.parametrize('change',[{'search_host':'foreign.search.windows.net'},{'session':'other'},{'source_sha':'c'*40}])
def test_wrong_target_or_prior_binding_cannot_be_used(tmp_path,change):
    with client(200) as c:observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=False)
    with client(404) as c,pytest.raises((ValueError,AssertionError)):
        observe(tmp_path,AUTH|change,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=True)

def test_budget_exhaustion_prevents_network(tmp_path):
    def exhausted():raise ValueError('budget exhausted')
    def forbidden(_):pytest.fail('network request without budget')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c,pytest.raises(ValueError):
        observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',exhausted,NOW,cleanup=False)

def test_observer_never_exceeds_twelve_total_reads(tmp_path):
    with client(200) as c:
        for _ in range(12):observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=False)
    def forbidden(_):pytest.fail('thirteenth observer request')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c,pytest.raises((ValueError,AssertionError)):
        observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=False)

def test_auth_failure_never_becomes_absence(tmp_path):
    with client(200) as c:observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=False)
    with client(403) as c,pytest.raises((ValueError,AssertionError)):
        observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,NOW,cleanup=True)
    assert not (tmp_path/'independent-test-index-first-absence.json').exists()

def test_observation_time_is_after_response_not_intent(tmp_path):
    clock=iter([NOW,NOW+timedelta(seconds=9)])
    with client(200) as c:
        proof=observe(tmp_path,AUTH,MANIFEST,c,'synthetic-test-only',reserve,lambda:next(clock),cleanup=False)
    assert proof['at']=='2026-10-19T00:00:09+00:00'
