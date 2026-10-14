import json
from unittest.mock import patch
import httpx
import pytest
from app.webapp import release_cli as cli
from app.webapp.private_az import TransportRefused
from .test_release_transport import adapter,RESOURCE,token
from .test_release_diagnostics import Cloud,Client,Credential,prepared


@pytest.mark.parametrize('slot',['main','staging'])
def test_cold_kudu_requires_explicit_warmup_and_same_worker_for_manifest(slot):
    calls=[];warm=[False]
    instance='a'*64
    def handle(request):
        calls.append(request)
        if request.url.host=='management.azure.com':
            assert request.url.path==RESOURCE+('/slots/staging' if slot=='staging' else '')+'/instances'
            return httpx.Response(200,json={'value':[{'name':instance}]})
        assert request.method=='GET'
        assert request.url.host=='mock-webapp'+('-staging' if slot=='staging' else '')+'.scm.azurewebsites.net'
        assert f'ARRAffinity={instance}' in request.headers['cookie']
        assert f'ARRAffinitySameSite={instance}' in request.headers['cookie']
        if request.url.path=='/api/deployments':
            assert request.url.query==b'warmup=true'
            warm[0]=True
            return httpx.Response(200,json=[])
        assert warm[0]
        assert request.url.path=='/api/vfs/data/SitePackages/packagename.txt'
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http)
        cloud.warm_scm(slot)
        assert cloud.selected_package_present(slot) is False
    assert len(calls)==3


def test_seed_checks_kudu_readiness_before_manifest_or_upload(tmp_path):
    order=[]
    class WarmFirst(Cloud):
        def warm_scm(self,slot):order.append('warm')
        def selected_package_present(self,slot):
            order.append('manifest')
            raise TransportRefused('release_refused')
    with patch.object(cli,'AzureReleaseTransport',WarmFirst),patch.object(cli,'WebAppClient',Client):
        assert cli.main(['seed'],env=prepared(tmp_path,'seed'),clean_guard=lambda:None,
                        output_guard=lambda _:None,root=tmp_path,credential_factory=Credential)==1
    assert order==['warm','manifest']
    data=json.loads((tmp_path/'artifacts/webapp-release/diagnostics.json').read_text())
    assert data['deployment_calls']==data['swap_calls']==data['model_calls']==0


def test_failed_kudu_preparation_stops_before_manifest_and_upload(tmp_path):
    class NoWarm(Cloud):
        def warm_scm(self,slot):raise httpx.ReadTimeout('private-host private-cookie')
        def selected_package_present(self,slot):raise AssertionError('manifest reached')
    with patch.object(cli,'AzureReleaseTransport',NoWarm),patch.object(cli,'WebAppClient',Client):
        assert cli.main(['seed'],env=prepared(tmp_path,'seed'),clean_guard=lambda:None,
                        output_guard=lambda _:None,root=tmp_path,credential_factory=Credential)==1
    data=json.loads((tmp_path/'artifacts/webapp-release/diagnostics.json').read_text())
    assert data['phase']=='scm_warmup' and data['timeout_kind']=='read'
    assert data['deployment_calls']==data['swap_calls']==data['model_calls']==0
    assert 'private-' not in json.dumps(data)


@pytest.mark.parametrize('status',[301,302,401,403,404,500])
def test_warmup_refuses_non_ready_response_without_body_or_redirect(status):
    from .test_http_client import Unreadable
    body=Unreadable();calls=[]
    def handle(request):
        calls.append(request)
        if request.url.host=='management.azure.com':
            return httpx.Response(200,json={'value':[{'name':'a'*64}]})
        return httpx.Response(status,stream=body,headers={'Location':'https://other.example.test/'})
    with httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=True) as http:
        cloud=adapter(http)
        with pytest.raises(TransportRefused):cloud.warm_scm('main')
        assert not cloud._scm_affinity
    assert len(calls)==2 and body.reads==0


@pytest.mark.parametrize('value',[[],[{}],[{'name':'a; private-cookie=x'}],
    [{'name':'https://other.example.test/'}],[{'name':True}],
    [{'name':'a'},{'name':'a'}],[{'name':'a'}]*17])
def test_warmup_requires_a_bounded_valid_owned_instance_list(value):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'value':value})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused):adapter(http).warm_scm('main')
    assert len(calls)==1


@pytest.mark.parametrize('first',[502,503,504,'timeout'])
def test_warmup_retries_only_owned_read_before_any_upload(first):
    from .test_http_client import Unreadable
    body=Unreadable();calls=[];sleeps=[]
    def handle(request):
        calls.append(request)
        if request.url.host=='management.azure.com':
            return httpx.Response(200,json={'value':[{'name':'a'*64}]})
        assert request.method=='GET' and request.url.path=='/api/deployments'
        if len(calls)==2:
            if first=='timeout':raise httpx.ReadTimeout('private-host',request=request)
            return httpx.Response(first,stream=body)
        return httpx.Response(200,stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter(http,clock=lambda:0,sleep=sleeps.append).warm_scm('staging')
    assert len(calls)==3 and sleeps==[1] and body.reads==0


def test_warmup_budget_reduces_final_request_and_never_accepts_late_response():
    elapsed=[0];budgets=[]
    def handle(request):
        if request.url.host=='management.azure.com':
            return httpx.Response(200,json={'value':[{'name':'a'*64}]})
        budgets.append(request.extensions['timeout']['read'])
        elapsed[0]+=80 if len(budgets)<3 else 20
        if len(budgets)<3:raise httpx.ReadTimeout('private-host',request=request)
        return httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http,clock=lambda:elapsed[0],sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
        with pytest.raises(TransportRefused):cloud.warm_scm('main')
        assert not cloud._scm_affinity
    assert budgets==[60,60,18]


def test_warmup_rechecks_workload_identity_before_each_retry():
    calls=[];tokens=[]
    def acquire(_):
        tokens.append(True)
        return token() if len(tokens)<3 else token(oid='wrong')
    def handle(request):
        calls.append(request)
        if request.url.host=='management.azure.com':
            return httpx.Response(200,json={'value':[{'name':'a'*64}]})
        raise httpx.ReadTimeout('private-host',request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http,clock=lambda:0,sleep=lambda _:None);cloud._token=acquire
        with pytest.raises(TransportRefused,match='account_refused'):cloud.warm_scm('main')
    assert len(calls)==2 and len(tokens)==3


def test_warmup_affinity_does_not_escape_to_arm_or_other_slot():
    calls=[]
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/instances'):
            return httpx.Response(200,json={'value':[{'name':'a'*64}]})
        return httpx.Response(200,json={})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http)
        cloud.warm_scm('main')
        cloud._read('GET','https://management.azure.com'+RESOURCE+'?api-version=2024-11-01')
        cloud._read('GET',cloud._scm('staging')+'/api/deployments/latest')
        cloud._read('POST',cloud._scm('main')+'/api/zipdeploy?isAsync=true',body=b'package')
    assert 'cookie' not in calls[2].headers and 'cookie' not in calls[3].headers
    assert 'ARRAffinity='+'a'*64 in calls[4].headers['cookie']
    assert calls[4].method=='POST' and sum(r.method=='POST' for r in calls)==1
