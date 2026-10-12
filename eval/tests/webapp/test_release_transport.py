import base64
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


def adapter(http, bearer=None):
    return AzureReleaseTransport(http, lambda scope: bearer or token(), TENANT, SUB, CLIENT, OID, RESOURCE,
                                 "https://mock-webapp.azurewebsites.net", "https://mock-webapp-staging.azurewebsites.net")


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
