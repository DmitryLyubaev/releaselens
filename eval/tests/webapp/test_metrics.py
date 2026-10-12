from datetime import datetime, timezone, timedelta
import httpx
import pytest
from app.webapp.metrics import owner_matches, query_window
from app.webapp.private_az import TransportRefused
from .test_http_client import Unreadable

START = datetime(2026,10,12,1,tzinfo=timezone.utc)
END = START + timedelta(seconds=30)

def test_only_owner_rows_inside_the_controlled_window_prove_identity():
    rows = [{'Caller':'owner-private','timestamp':'2026-10-12T01:00:05Z','total':12},
            {'Caller':'other','timestamp':'2026-10-12T01:00:06Z','total':8},
            {'Caller':'owner-private','timestamp':'2026-10-12T00:59:59Z','total':40}]
    assert owner_matches(rows,'owner-private',START,END) == (True,1)
    assert owner_matches(rows[1:],'owner-private',START,END) == (False,0)
    assert owner_matches([dict(rows[0],total=True)],'owner-private',START,END) == (False,0)

def test_query_has_both_bounds_and_filters_the_gateway_metric():
    import json
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'tables':[{'columns':[{'name':x} for x in ['timestamp','Caller','total']], 'rows':[['2026-10-12T01:00:05Z','owner-private',12]]}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert len(query_window(http,'private-app','private-bearer',START,END)) == 1
    q=calls[0]['query']
    assert 'timestamp >= datetime(2026-10-12T01:00:00Z)' in q
    assert 'timestamp <= datetime(2026-10-12T01:00:30Z)' in q
    assert 'Total Tokens' in q and 'azure-openai-v1' in q
    assert calls[0]['timespan'] == '2026-10-12T01:00:00Z/2026-10-12T01:00:30Z'

def test_refused_metric_body_is_not_read():
    body=Unreadable()
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(403,stream=body))) as http:
        with pytest.raises(TransportRefused): query_window(http,'private-app','private-bearer',START,END)
    assert body.reads==0

@pytest.mark.parametrize('end',[START,START+timedelta(minutes=16),START-timedelta(seconds=1)])
def test_invalid_window_makes_no_request(end):
    with httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError('network')))) as http:
        with pytest.raises(TransportRefused): query_window(http,'private-app','private-bearer',START,end)
