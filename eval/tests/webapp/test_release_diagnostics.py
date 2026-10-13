import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from app.webapp import release_cli as cli
from app.webapp import release_transport as transport
from app.webapp.private_az import TransportRefused
from .test_release import ci, SHA, REPO
from .test_release_transport import TENANT, SUB, CLIENT, OID, RESOURCE, token


def prepared(tmp_path, mode):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        for name in ('ReleaseLens.Web.dll', 'ReleaseLens.Web.deps.json', 'ReleaseLens.Web.runtimeconfig.json'):
            archive.writestr(name, 'content')
    package = payload.getvalue()
    folder = tmp_path / 'artifacts/webapp-verified'
    folder.mkdir(parents=True)
    (folder / 'package.zip').write_bytes(package)
    (folder / 'verified.json').write_text(json.dumps(dict(current_sha=SHA, repository=REPO, mode=mode, fixture=False, execution_ci=ci(), ci=ci(), package_sha256=hashlib.sha256(package).hexdigest(), version=f'{SHA}.900.1.{mode}')))
    return dict(GITHUB_ACTIONS='true', GITHUB_REF='refs/heads/main', GITHUB_EVENT_NAME='workflow_dispatch', WEBAPP_MODE=mode, WEBAPP_FIXTURE='false', GITHUB_REPOSITORY=REPO, GITHUB_SHA=SHA, GITHUB_RUN_ID='900', GITHUB_RUN_ATTEMPT='1', AZURE_TENANT_ID='private-tenant', AZURE_CLIENT_ID='private-client', AZURE_SUBSCRIPTION_ID='private-sub', WEBAPP_DEPLOY_OBJECT_ID='private-object', WEBAPP_RESOURCE_ID='private-resource', WEBAPP_FRONTEND_AUDIENCE='api://private-audience', WEBAPP_MAIN_URL='https://private-main.example.test', WEBAPP_STAGING_URL='https://private-stage.example.test', WEBAPP_INTENDED_CONFIG='{}')


class Cloud:
    def __init__(self, *args): pass
    def configuration(self, intended): return intended
    def probe_scm(self, slot): return False
    def deploy(self, *args): raise AssertionError('diagnosis deployed')
    def run(self, *args, **kwargs): raise AssertionError('diagnosis swapped')
    def selected_package_sha256(self, *args): raise AssertionError('diagnosis read package bytes')


class Credential:
    def __init__(self, *args): pass
    def get_token(self, scope): return SimpleNamespace(token='private-token')


class Client:
    def __init__(self, *args): pass
    def health(self, token): return SimpleNamespace(http_status=503)
    def anonymous_health_status(self): return 401
    def ask(self, *args): raise AssertionError('diagnosis asked model')


def test_diagnose_collects_read_only_facts_without_a_receipt_or_package(tmp_path, capsys):
    env = prepared(tmp_path, 'diagnose')
    with patch.object(cli, 'ReadOnlyAzureReleaseTransport', Cloud, create=True), patch.object(cli, 'WebAppClient', Client):
        assert cli.main(['diagnose'], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path, credential_factory=Credential) == 0
    folder = tmp_path / 'artifacts/webapp-release'
    data = json.loads((folder / 'diagnostics.json').read_text())
    assert data['schema'] == 1 and data['source_sha'] == SHA and data['run_id'] == 900
    assert data['read_only'] is True and data['configuration_matches'] is True
    assert data['health_statuses'] == {'main': 503, 'staging': 503}
    assert data['deployment_calls'] == data['swap_calls'] == data['model_calls'] == 0
    assert not (folder / 'receipt.json').exists() and not (folder / 'package.zip').exists()
    assert 'private-' not in json.dumps(data)
    assert capsys.readouterr().out == 'status=ok\n'


@pytest.mark.parametrize('mode', ['seed', 'diagnose'])
def test_refusal_identifies_frontend_token_boundary_without_error_text(tmp_path, capsys, mode):
    env = prepared(tmp_path, mode)
    class SeedCloud(Cloud):
        def deploy(self, *args): pass
    class RefusedCredential(Credential):
        def get_token(self, scope):
            raise RuntimeError('AADSTS70021 private-token private-owner@example.test https://private-host.example.test')
    with patch.object(cli, 'AzureReleaseTransport', SeedCloud), patch.object(cli, 'ReadOnlyAzureReleaseTransport', Cloud), patch.object(cli, 'WebAppClient', Client):
        assert cli.main([mode], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path, credential_factory=RefusedCredential) == 1
    data = json.loads((tmp_path / 'artifacts/webapp-release/diagnostics.json').read_text())
    assert data['phase'] == 'frontend_token' and data['aad_error_code'] == 70021
    assert data['outcome'] == 'refused' and data['source_sha'] == SHA
    assert 'private-' not in json.dumps(data) and 'AADSTS' not in json.dumps(data)
    assert capsys.readouterr().out == 'status=refused\n'


@pytest.mark.parametrize('operation', ['deploy', 'swap', 'scm_post', 'settings_write'])
def test_read_only_transport_refuses_writes_before_token_or_http(operation):
    requests = []
    tokens = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as http:
        cloud = transport.ReadOnlyAzureReleaseTransport(http, lambda _: tokens.append(True) or token(), TENANT, SUB, CLIENT, OID, RESOURCE, 'https://mock-webapp.azurewebsites.net', 'https://mock-webapp-staging.azurewebsites.net')
        with pytest.raises(TransportRefused):
            if operation == 'deploy': cloud.deploy('main', b'zip')
            elif operation == 'swap': cloud._arm('POST', 'https://management.azure.com' + RESOURCE + '/slots/staging/slotsswap?api-version=2024-11-01', {'targetSlot': 'production'})
            elif operation == 'scm_post': cloud._read('POST', cloud._scm('main') + '/api/zipdeploy?isAsync=true')
            else: cloud._arm('PUT', 'https://management.azure.com' + RESOURCE + '/config/web?api-version=2024-11-01', {})
    assert requests == [] and tokens == []


@pytest.mark.parametrize('slot', ['', '/slots/staging'])
def test_read_only_transport_allows_the_existing_appsettings_list_read(slot):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'properties': {}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud = transport.ReadOnlyAzureReleaseTransport(http, lambda _: token(), TENANT, SUB, CLIENT, OID, RESOURCE, 'https://mock-webapp.azurewebsites.net', 'https://mock-webapp-staging.azurewebsites.net')
        assert cloud._arm('POST', 'https://management.azure.com' + RESOURCE + slot + '/config/appsettings/list?api-version=2024-11-01') == {'properties': {}}
    assert len(requests) == 1 and requests[0].method == 'POST'


def test_http_diagnostics_keep_refusal_body_unread_and_drop_headers_and_urls():
    from app.webapp.diagnostics import ReleaseDiagnostics
    from .test_http_client import Unreadable
    body = Unreadable()
    trace = ReleaseDiagnostics(SHA, 900, 1, 'diagnose')
    trace.hosts['mock-webapp.scm.azurewebsites.net'] = 'main'
    def handle(request):
        return httpx.Response(403, headers={'x-secret': 'private-marker'}, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle), event_hooks={'response': [trace.observe]}) as http:
        cloud = transport.ReadOnlyAzureReleaseTransport(http, lambda _: token(), TENANT, SUB, CLIENT, OID, RESOURCE, 'https://mock-webapp.azurewebsites.net', 'https://mock-webapp-staging.azurewebsites.net')
        with pytest.raises(TransportRefused): cloud.probe_scm('main')
    data = trace.result('collected')
    assert data['responses'] == [{'operation': 'deployment_status', 'slot': 'main', 'status': 403}]
    assert body.reads == 0
    assert all(value not in json.dumps(data) for value in ('private-marker', 'https://', TENANT, CLIENT, 'authorization'))


def test_hidden_transport_timeout_context_is_classified_without_message():
    from app.webapp.diagnostics import ReleaseDiagnostics
    trace = ReleaseDiagnostics(SHA, 900, 1, 'seed')
    trace.phase = 'scm_deploy'
    try:
        try: raise httpx.ReadTimeout('private-token https://private-host.example.test')
        except httpx.ReadTimeout: raise TransportRefused('release_refused') from None
    except TransportRefused as error:
        data = trace.result('refused', error)
    assert data['phase'] == 'scm_deploy' and data['error_category'] == 'timeout'
    assert 'private-' not in json.dumps(data)


def test_wrapped_aad_error_keeps_only_numeric_code_and_fixed_refusal_code():
    from app.webapp.diagnostics import ReleaseDiagnostics
    trace = ReleaseDiagnostics(SHA, 900, 1, 'diagnose')
    trace.phase = 'arm_token'
    try:
        try: raise RuntimeError('AADSTS70021 private-owner@example.test private-token')
        except RuntimeError: raise TransportRefused('account_refused') from None
    except TransportRefused as error:
        data = trace.result('refused', error)
    assert data['aad_error_code'] == 70021 and data['refusal_code'] == 'account_refused'
    assert 'private-' not in json.dumps(data)


def test_swap_attempt_survives_response_timeout_and_trace_truncation():
    from app.webapp.diagnostics import ReleaseDiagnostics
    trace = ReleaseDiagnostics(SHA, 900, 1, 'promote')
    attempts = []
    def handle(request):
        attempts.append(request)
        raise httpx.ReadTimeout('private-swap-result')
    with httpx.Client(transport=httpx.MockTransport(handle), event_hooks={'request': [trace.observe_request], 'response': [trace.observe]}) as http:
        cloud = transport.AzureReleaseTransport(http, lambda _: token(), TENANT, SUB, CLIENT, OID, RESOURCE, 'https://mock-webapp.azurewebsites.net', 'https://mock-webapp-staging.azurewebsites.net')
        with pytest.raises(TransportRefused):
            cloud._arm('POST', 'https://management.azure.com' + RESOURCE + '/slots/staging/slotsswap?api-version=2024-11-01', {'targetSlot': 'production'})
    assert len(attempts) == 1 and trace.result('refused')['swap_calls'] == 1
    for _ in range(45):
        trace.observe(httpx.Response(200, request=httpx.Request('GET', 'https://management.azure.com/operations/poll')))
    assert len(trace.responses) == 40 and trace.result('refused')['swap_calls'] == 1
    assert 'private-' not in json.dumps(trace.result('refused'))


def test_async_poll_projection_retains_only_owned_destination_and_fixed_reason():
    from app.webapp.diagnostics import ReleaseDiagnostics
    trace=ReleaseDiagnostics(SHA,900,1,'seed')
    trace.hosts['mock-webapp.scm.azurewebsites.net']='main'
    trace.observe(httpx.Response(202,headers={'Location':'https://private-host.example.test/private-token'},request=httpx.Request('POST','https://mock-webapp.scm.azurewebsites.net/api/zipdeploy?isAsync=true')))
    data=trace.result('refused')
    assert data['scm_polling']=={'source':'owned_latest','fallback_reason':'untrusted'}
    assert 'private-' not in json.dumps(data) and 'https://' not in json.dumps(data)


def run_http_diagnosis(tmp_path, http):
    env = prepared(tmp_path, 'diagnose') | dict(
        AZURE_TENANT_ID=TENANT, AZURE_CLIENT_ID=CLIENT,
        AZURE_SUBSCRIPTION_ID=SUB, WEBAPP_DEPLOY_OBJECT_ID=OID,
        WEBAPP_RESOURCE_ID=RESOURCE,
        WEBAPP_MAIN_URL='https://mock-webapp.azurewebsites.net',
        WEBAPP_STAGING_URL='https://mock-webapp-staging.azurewebsites.net')
    class ConfiguredCloud(transport.ReadOnlyAzureReleaseTransport):
        def configuration(self, intended): return intended
    class BoundCredential(Credential):
        def get_token(self, scope): return SimpleNamespace(token=token() if scope == transport.ARM_SCOPE else 'private-frontend-token')
    with patch.object(cli, 'ReadOnlyAzureReleaseTransport', ConfiguredCloud):
        code = cli.main(['diagnose'], env=env, http=http, clean_guard=lambda: None,
                        output_guard=lambda _: None, root=tmp_path, credential_factory=BoundCredential)
    return code, json.loads((tmp_path / 'artifacts/webapp-release/diagnostics.json').read_text())


def test_diagnose_retains_each_swallowed_failure_and_anonymous_reachability(tmp_path, capsys):
    from .test_http_client import Unreadable
    bodies, requests = [], []
    def handle(request):
        requests.append(request)
        assert request.method == 'GET' and request.extensions['timeout']['read'] == 10
        main = request.url.host in {'mock-webapp.azurewebsites.net', 'mock-webapp.scm.azurewebsites.net'}
        if '.scm.' in request.url.host:
            assert request.url.path == '/api/deployments/latest'
            if not main: return httpx.Response(200, json={})
            status = 404
        else:
            assert request.url.path == '/health'
            if request.headers.get('Authorization'):
                if main: raise httpx.ReadTimeout('private-host private-token')
                status = 503
            else:
                assert 'Cookie' not in request.headers
                if not main: raise httpx.ConnectError('private-dns private-owner@example.test')
                status = 401
        body = Unreadable(); bodies.append(body)
        return httpx.Response(status, stream=body, headers={'Location': 'https://private-host.test', 'x-secret': 'private-token'})
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        code, data = run_http_diagnosis(tmp_path, http)
    assert code == 0 and capsys.readouterr().out == 'status=ok\n'
    assert data.get('probes') == [
        dict(operation='health', slot='main', status=None, error_category='timeout', timeout_kind='read', refusal_code='network', aad_error_code=None),
        dict(operation='health', slot='staging', status=503, error_category=None, timeout_kind=None, refusal_code=None, aad_error_code=None),
        dict(operation='anonymous_health', slot='main', status=401, error_category=None, timeout_kind=None, refusal_code=None, aad_error_code=None),
        dict(operation='anonymous_health', slot='staging', status=None, error_category='network', timeout_kind=None, refusal_code='network', aad_error_code=None),
        dict(operation='deployment_status', slot='main', status=404, error_category='transport_refused', timeout_kind=None, refusal_code='release_refused', aad_error_code=None),
        dict(operation='deployment_status', slot='staging', status=200, error_category=None, timeout_kind=None, refusal_code=None, aad_error_code=None)]
    assert data['health_statuses'] == {'main': None, 'staging': 503}
    assert data['scm_readable'] == {'main': False, 'staging': True}
    assert data['anonymous_health_statuses'] == {'main': 401, 'staging': None}
    assert len(requests) == 6 and all(body.reads == 0 for body in bodies)
    assert data['deployment_calls'] == data['swap_calls'] == data['model_calls'] == 0
    assert 'private-' not in json.dumps(data) and 'https://' not in json.dumps(data)
    assert not (tmp_path / 'artifacts/webapp-release/receipt.json').exists()


@pytest.mark.parametrize('failure,category,kind', [
    (httpx.ConnectTimeout, 'timeout', 'connect'), (httpx.ReadTimeout, 'timeout', 'read'),
    (httpx.WriteTimeout, 'timeout', 'write'), (httpx.PoolTimeout, 'timeout', 'pool'),
    (httpx.TimeoutException, 'timeout', 'other'), (httpx.ConnectError, 'network', None),
    (httpx.RemoteProtocolError, 'protocol', None), (OSError, 'network', None)])
def test_diagnose_projects_wrapped_scm_exception_types_without_messages(tmp_path, failure, category, kind):
    def handle(request): raise failure('AADSTS70021 private-token private-owner@example.test https://private-host.test')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        code, data = run_http_diagnosis(tmp_path, http)
    assert code == 0
    probes = data.get('probes', [])
    assert len(probes) == 6
    assert all(item['status'] is None and item['error_category'] == category and item['timeout_kind'] == kind and item['aad_error_code'] == 70021 for item in probes)
    assert 'private-' not in json.dumps(data)


def test_response_status_survives_a_later_body_timeout_and_anonymous_has_no_session(tmp_path):
    class TimeoutBody(httpx.SyncByteStream):
        def __iter__(self): raise httpx.ReadTimeout('private-body'); yield b''
    def handle(request):
        if '.scm.' in request.url.host: return httpx.Response(200, json={})
        if request.headers.get('Authorization'):
            return httpx.Response(200, stream=TimeoutBody(), headers={'Set-Cookie': 'AppServiceAuthSession=private-session; Path=/'})
        assert 'Cookie' not in request.headers and 'Authorization' not in request.headers
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(handle), cookies={'shared': 'private-shared'}, headers={'Authorization': 'private-default'}) as http:
        code, data = run_http_diagnosis(tmp_path, http)
    assert code == 0
    assert data.get('probes', [])[:2] == [
        dict(operation='health', slot=slot, status=200, error_category='timeout', timeout_kind='read', refusal_code='network', aad_error_code=None) for slot in ('main', 'staging')]
    assert data['health_statuses'] == {'main': None, 'staging': None}
    assert data['anonymous_health_statuses'] == {'main': 401, 'staging': 401}
    assert 'private-' not in json.dumps(data)


def test_anonymous_diagnostic_ignores_malformed_redirect_and_success_body(tmp_path):
    from .test_http_client import Unreadable
    bodies = []
    def handle(request):
        if '.scm.' in request.url.host: return httpx.Response(200, json={})
        body = Unreadable(); bodies.append(body)
        return httpx.Response(503 if request.headers.get('Authorization') else 200,
                              headers={'Location': 'https://[private-malformed'}, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        code, data = run_http_diagnosis(tmp_path, http)
    assert code == 0 and data['anonymous_health_statuses'] == {'main': 200, 'staging': 200}
    assert len(data['probes']) == 6 and all(body.reads == 0 for body in bodies)


def test_probe_labels_and_count_are_closed_before_the_read():
    from app.webapp.diagnostics import ReleaseDiagnostics
    trace = ReleaseDiagnostics(SHA, 900, 1, 'diagnose')
    reads = []
    for operation, slot in [('private-url', 'main'), ('health', 'private-owner')]:
        with pytest.raises(ValueError): trace.collect_probe(operation, slot, lambda: reads.append(True))
    assert reads == [] and trace.result('collected')['probes'] == []
    for _ in range(6): assert trace.collect_probe('health', 'main', lambda: 401) == 401
    with pytest.raises(ValueError): trace.collect_probe('health', 'main', lambda: reads.append(True))
    assert reads == [] and len(trace.result('collected')['probes']) == 6


def test_unexpected_probe_exception_refuses_job_but_preserves_closed_partial_evidence(tmp_path):
    class UnexpectedClient(Client):
        def health(self, token): raise RuntimeError('private-owner https://private-host.test AADSTS70021')
    env = prepared(tmp_path, 'diagnose')
    with patch.object(cli, 'ReadOnlyAzureReleaseTransport', Cloud), patch.object(cli, 'WebAppClient', UnexpectedClient):
        code = cli.main(['diagnose'], env=env, clean_guard=lambda: None, output_guard=lambda _: None,
                        root=tmp_path, credential_factory=Credential)
    data = json.loads((tmp_path / 'artifacts/webapp-release/diagnostics.json').read_text())
    assert code == 1 and data['outcome'] == 'refused'
    assert data['probes'] == [dict(operation='health', slot='main', status=None,
                                  error_category='other', timeout_kind=None, refusal_code=None, aad_error_code=70021)]
    assert 'private-' not in json.dumps(data)
