import base64
import copy
import hashlib
import json
import subprocess
import httpx
import pytest
from app.webapp.release_transport import AzureReleaseTransport
from app.webapp.private_az import PrivateAz, TransportRefused

TENANT = "00000000-0000-0000-0000-000000000001"
SUB = "00000000-0000-0000-0000-000000000002"
CLIENT = "00000000-0000-0000-0000-000000000003"
OID = "00000000-0000-0000-0000-000000000004"
RESOURCE = f"/subscriptions/{SUB}/resourceGroups/mock/providers/Microsoft.Web/sites/mock-webapp"


def token(**overrides):
    data = dict(tid=TENANT, oid=OID, appid=CLIENT) | overrides
    value = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"e30.{value}.signature"


def adapter(http, bearer=None, **kwargs):
    return AzureReleaseTransport(http, lambda scope: bearer or token(), TENANT, SUB, CLIENT, OID, RESOURCE,
                                 "https://mock-webapp.azurewebsites.net", "https://mock-webapp-staging.azurewebsites.net", **kwargs)


def test_workflow_arm_adapter_keeps_credentials_in_memory_and_uses_regular_swap():
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path.endswith("/slots/staging/slotsswap")
        assert json.loads(request.content) == {"targetSlot": "production", "preserveVnet": True}
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud = adapter(http)
        az = PrivateAz(TENANT, SUB, CLIENT, run=cloud.run)
        assert az.call(["rest", "--method", "post", "--url", f"https://management.azure.com{RESOURCE}/slots/staging/slotsswap?api-version=2024-11-01", "--body", json.dumps({"targetSlot": "production", "preserveVnet": True})]) == {}
        assert CLIENT not in repr(cloud)
    assert len(requests) == 1


@pytest.mark.parametrize("claim", ["tid", "oid", "appid"])
def test_wrong_workload_token_stops_before_arm_operations(claim):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as http:
        cloud = adapter(http, token(**{claim: "wrong"}))
        with pytest.raises(TransportRefused): PrivateAz(TENANT, SUB, CLIENT, run=cloud.run).call(["rest", "--url", f"https://management.azure.com{RESOURCE}"])
    assert requests == []


def test_selected_scm_package_digest_is_measured_from_stored_bytes():
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith("packagename.txt"): return httpx.Response(200, text="selected.zip\n")
        assert request.url.path.endswith("selected.zip")
        assert request.url.host == "mock-webapp-staging.scm.azurewebsites.net"
        return httpx.Response(200, content=b"stored package bytes")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert adapter(http).selected_package_sha256("staging") == hashlib.sha256(b"stored package bytes").hexdigest()
    assert len(requests) == 2


@pytest.mark.parametrize("selected", ["../escape.zip", "/absolute.zip", "https://other.example.test/file.zip", "a.zip\nb.zip"])
def test_scm_selection_cannot_escape_or_redirect(selected):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, text=selected)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused): adapter(http).selected_package_sha256("staging")
    assert len(calls) == 1


def test_scm_refusal_is_unread_and_never_falls_back_to_basic_auth():
    from .test_http_client import Unreadable
    body = Unreadable()
    def handle(request):
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(401, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused): adapter(http).selected_package_sha256("staging")
    assert body.reads == 0


IDENTITY = f"/subscriptions/{SUB}/resourceGroups/mock-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock-webapp"


def configuration_fixture(actual_ids=None):
    intended = {slot: {
        "identity": {"type": "UserAssigned", "identity_ids": [IDENTITY]},
        "siteconfig": {"linuxFxVersion": "DOTNETCORE|10.0", "minTlsVersion": "1.3"},
        "appsettings": {"Web__Slot": slot, "Web__Model": "mock-model"},
        "auth": {"platform": {"enabled": True}, "identityProviders": {
            "azureActiveDirectory": {"enabled": True, "registration": {
                "clientId": CLIENT, "clientSecretSettingName": "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID"}},
            "google": {"enabled": False}, "customOpenIdConnectProviders": {}}},
        "publishing": {"scm": False, "ftp": False},
    } for slot in ("main", "staging")}
    intended["sticky"] = {"appSettingNames": ["Web__Slot"]}
    responses = {}
    for slot in ("main", "staging"):
        resource = RESOURCE + ("/slots/staging" if slot == "staging" else "")
        value = intended[slot]
        responses[("GET", resource)] = {"identity": {"type": "UserAssigned", "userAssignedIdentities": {
            identity: {"clientId": CLIENT, "principalId": OID} for identity in actual_ids or [IDENTITY]}}}
        for path, properties in (("/config/web", value["siteconfig"]), ("/config/authsettingsV2", value["auth"])):
            responses[("GET", resource + path)] = {"properties": copy.deepcopy(properties)}
        responses[("POST", resource + "/config/appsettings/list")] = {"properties": copy.deepcopy(value["appsettings"])}
        for kind in ("scm", "ftp"):
            responses[("GET", resource + "/basicPublishingCredentialsPolicies/" + kind)] = {"properties": {"allow": False}}
    responses[("GET", RESOURCE + "/config/slotConfigNames")] = {"properties": copy.deepcopy(intended["sticky"])}
    return intended, responses


def read_configuration(intended, responses):
    def handle(request):
        assert request.url.host == "management.azure.com"
        assert request.url.params["api-version"] == "2024-11-01"
        return httpx.Response(200, json=responses[(request.method, request.url.path)])
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        return adapter(http).configuration(intended)


@pytest.mark.parametrize("actual_id", [IDENTITY, IDENTITY.lower(), IDENTITY.upper()])
def test_configuration_accepts_identity_id_casing_only(actual_id):
    intended, responses = configuration_fixture([actual_id])
    actual = read_configuration(intended, responses)
    assert actual == intended


@pytest.mark.parametrize("declared_group,actual_group", [
    ("mock-mass", "mock-ma\u00df"),
    ("mock-bootstrap", "mock-boot\ufb06rap"),
    ("mock-k", "mock-\u212a"),
])
def test_configuration_rejects_unicode_substitutions_in_identity_id(declared_group, actual_group):
    declared_id = IDENTITY.replace("mock-bootstrap", declared_group)
    actual_id = IDENTITY.replace("mock-bootstrap", actual_group)
    intended, responses = configuration_fixture([actual_id])
    for slot in ("main", "staging"):
        intended[slot]["identity"]["identity_ids"] = [declared_id]
    assert read_configuration(intended, responses) != intended


@pytest.mark.parametrize("actual_ids", [[IDENTITY + "-other"], [IDENTITY, IDENTITY + "-other"], []])
def test_configuration_still_rejects_different_extra_or_missing_identity(actual_ids):
    intended, responses = configuration_fixture()
    for slot in ("main", "staging"):
        resource = RESOURCE + ("/slots/staging" if slot == "staging" else "")
        responses[("GET", resource)]["identity"]["userAssignedIdentities"] = {value: {} for value in actual_ids}
    assert read_configuration(intended, responses) != intended


@pytest.mark.parametrize("section,key,value", [
    ("identity", "type", "SystemAssigned, UserAssigned"),
    ("siteconfig", "minTlsVersion", "1.2"),
    ("siteconfig", "linuxFxVersion", "dotnetcore|10.0"),
    ("appsettings", "Web__Model", "MOCK-MODEL"),
])
def test_identity_casing_tolerance_does_not_normalize_other_configuration(section, key, value):
    intended, responses = configuration_fixture([IDENTITY.lower()])
    if section == "identity":
        responses[("GET", RESOURCE)]["identity"][key] = value
    else:
        method, path = ("POST", "/config/appsettings/list") if section == "appsettings" else ("GET", "/config/web")
        responses[(method, RESOURCE + path)]["properties"][key] = value
    assert read_configuration(intended, responses) != intended


def test_configuration_still_refuses_an_enabled_non_entra_provider():
    intended, responses = configuration_fixture()
    responses[("GET", RESOURCE + "/config/authsettingsV2")]["properties"]["identityProviders"]["google"]["enabled"] = True
    with pytest.raises(TransportRefused): read_configuration(intended, responses)


@pytest.mark.parametrize("enabled", [True, False])
def test_configuration_refuses_unexpected_custom_auth_provider(enabled):
    intended, responses = configuration_fixture()
    responses[("GET", RESOURCE + "/config/authsettingsV2")]["properties"]["identityProviders"]["customOpenIdConnectProviders"] = {
        "unexpected": {"enabled": enabled, "registration": {"clientId": "mock-custom"}}}
    with pytest.raises(TransportRefused): read_configuration(intended, responses)


@pytest.mark.parametrize("actual_map", [{}, None, "absent"])
def test_configuration_accepts_platform_empty_custom_provider_forms(actual_map):
    intended, responses = configuration_fixture()
    for slot in ("main", "staging"):
        resource = RESOURCE + ("/slots/staging" if slot == "staging" else "")
        providers = responses[("GET", resource + "/config/authsettingsV2")]["properties"]["identityProviders"]
        if actual_map == "absent": del providers["customOpenIdConnectProviders"]
        else: providers["customOpenIdConnectProviders"] = actual_map
    assert read_configuration(intended, responses) == intended


@pytest.fixture
def deployment_timer():
    class Timer:
        elapsed = 0
        def clock(self): return self.elapsed
        def sleep(self, seconds): self.elapsed += seconds
    return Timer()


def test_scm_pending_deployment_202_then_success_keeps_original_location(deployment_timer):
    from .test_http_client import Unreadable
    body = Unreadable()
    requests = []
    location = "https://mock-webapp.scm.azurewebsites.net/api/deployments/latest?deployer=ZipDeploy&time=mock"
    def handle(request):
        requests.append(request)
        if len(requests) == 1: return httpx.Response(202, headers={"Location": location})
        assert str(request.url) == location
        if len(requests) == 2:
            return httpx.Response(202, headers={"Location": "https://other.example.test/private"}, stream=body)
        return httpx.Response(200, json={"status": 4})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter(http, clock=deployment_timer.clock, sleep=deployment_timer.sleep).deploy("main", b"mock zip")
    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert deployment_timer.elapsed == 10
    assert body.reads == 0


def test_scm_pending_deployment_202_stops_at_existing_poll_bound(deployment_timer):
    from .test_http_client import Unreadable
    body = Unreadable()
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(202, headers={"Location": "/api/deployments/latest"})
        return httpx.Response(202, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused):
            adapter(http, clock=deployment_timer.clock, sleep=deployment_timer.sleep).deploy("main", b"mock zip")
    assert len(requests) == 31
    assert deployment_timer.elapsed == 300
    assert body.reads == 0


def test_scm_pending_deployment_then_403_stops_without_reading_body(deployment_timer):
    from .test_http_client import Unreadable
    body = Unreadable()
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(202, headers={"Location": "/api/deployments/latest"})
        return httpx.Response(202 if len(requests) == 2 else 403, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused):
            adapter(http, clock=deployment_timer.clock, sleep=deployment_timer.sleep).deploy("main", b"mock zip")
    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert deployment_timer.elapsed == 10
    assert body.reads == 0


@pytest.mark.parametrize('path,method', [('/config/slotConfigNames','GET'),('/config/appsettings/list','POST'),('/slots/staging/config/appsettings/list','POST')])
def test_owned_configuration_read_recovers_once_from_timeout(path, method, deployment_timer):
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests)==1: raise httpx.ReadTimeout('private-read-timeout')
        return httpx.Response(200,json={'properties':{}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert adapter(http,clock=deployment_timer.clock,sleep=deployment_timer.sleep)._arm(method,'https://management.azure.com'+RESOURCE+path+'?api-version=2024-11-01')=={'properties':{}}
    assert len(requests)==2 and all(request.extensions['timeout']['read']==30 for request in requests)
    assert deployment_timer.elapsed==1


def test_persistent_read_timeout_stops_after_one_retry(deployment_timer):
    requests=[]
    def handle(request):
        requests.append(request); raise httpx.ReadTimeout('private-read-timeout')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused): adapter(http,sleep=deployment_timer.sleep)._arm('GET','https://management.azure.com'+RESOURCE+'/config/slotConfigNames?api-version=2024-11-01')
    assert len(requests)==2 and deployment_timer.elapsed==1


@pytest.mark.parametrize('operation',['upload','swap'])
def test_ambiguous_write_timeout_is_never_replayed(operation,deployment_timer):
    requests=[]
    def handle(request):
        requests.append(request); raise httpx.ReadTimeout('private-write-timeout')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http,sleep=deployment_timer.sleep)
        with pytest.raises(TransportRefused):
            if operation=='upload': cloud.deploy('main',b'mock zip')
            else: cloud._arm('POST','https://management.azure.com'+RESOURCE+'/slots/staging/slotsswap?api-version=2024-11-01',{'targetSlot':'production'})
    assert len(requests)==1 and deployment_timer.elapsed==0


def test_read_http_refusal_remains_unread_without_retry(deployment_timer):
    from .test_http_client import Unreadable
    body=Unreadable(); requests=[]
    def handle(request):
        requests.append(request); return httpx.Response(403,stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused): adapter(http,sleep=deployment_timer.sleep)._arm('GET','https://management.azure.com'+RESOURCE+'/config/slotConfigNames?api-version=2024-11-01')
    assert len(requests)==1 and body.reads==0 and deployment_timer.elapsed==0


def test_token_acquisition_timeout_is_not_a_read_retry(deployment_timer):
    calls=[]; requests=[]
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as http:
        cloud=adapter(http,sleep=deployment_timer.sleep)
        def bearer(_):
            calls.append(True); raise httpx.ReadTimeout('private-token-acquisition')
        cloud._token=bearer
        with pytest.raises(TransportRefused): cloud._arm('GET','https://management.azure.com'+RESOURCE+'/config/slotConfigNames?api-version=2024-11-01')
    assert len(calls)==1 and requests==[] and deployment_timer.elapsed==0


@pytest.mark.parametrize('method,location,body',[('GET','scm',None),('POST','other',None),('POST','settings',{})])
def test_retry_scope_does_not_expand_to_other_operations(method,location,body,deployment_timer):
    requests=[]
    def handle(request):
        requests.append(request); raise httpx.ReadTimeout('private-timeout')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        cloud=adapter(http,sleep=deployment_timer.sleep)
        url=cloud._scm('main')+'/api/deployments/latest' if location=='scm' else 'https://management.azure.com'+RESOURCE+('/config/appsettings/list' if location=='settings' else '/config/authsettingsV2')
        with pytest.raises(TransportRefused): cloud._read(method,url,body=body)
    assert len(requests)==1 and deployment_timer.elapsed==0 and requests[0].extensions['timeout']['read']==10


def test_retry_url_classification_preserves_closed_invalid_url_refusal():
    requests=[]
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as http:
        with pytest.raises(TransportRefused,match='release_refused'):
            adapter(http)._read('GET','https://[private-invalid')
    assert requests==[]


@pytest.mark.parametrize('location',[None,'https://other.example.test/private','http://mock-webapp.scm.azurewebsites.net/api/deployments/latest','https://mock-webapp.scm.azurewebsites.net/not-status','https://private-user:private-token@mock-webapp.scm.azurewebsites.net/api/deployments/latest','https://[private-invalid'])
def test_accepted_upload_with_unusable_location_polls_only_owned_latest(location,deployment_timer):
    from .test_http_client import Unreadable
    body=Unreadable(); requests=[]
    def handle(request):
        requests.append(request)
        if len(requests)==1: return httpx.Response(202,headers={} if location is None else {'Location':location},stream=body)
        assert str(request.url)=='https://mock-webapp.scm.azurewebsites.net/api/deployments/latest'
        return httpx.Response(200,json={'status':4})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter(http,clock=deployment_timer.clock,sleep=deployment_timer.sleep).deploy('main',b'mock zip')
    assert [r.method for r in requests]==['POST','GET'] and body.reads==0


def test_fallback_status_failure_never_reuploads_or_reads_refusal_body(deployment_timer):
    from .test_http_client import Unreadable
    body=Unreadable(); requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(202) if len(requests)==1 else httpx.Response(403,stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(TransportRefused): adapter(http,clock=deployment_timer.clock,sleep=deployment_timer.sleep).deploy('main',b'mock zip')
    assert [r.method for r in requests]==['POST','GET'] and body.reads==0
