from dataclasses import replace
import json

import httpx
import pytest

from app.delivery_search.client import upload_bundle, wait_for_count
from app.delivery_search.contracts import DeliveryRefused, SearchTarget, ReleaseManifest, VerifiedBundle


class Tokens:
    def token(self): return "synthetic"


@pytest.mark.parametrize("endpoint,host,index", [
    ("https://other.search.windows.net", "owned.search.windows.net", "p8-test"),
    ("http://owned.search.windows.net", "owned.search.windows.net", "p8-test"),
    ("https://owned.search.windows.net/?secret=token", "owned.search.windows.net", "p8-test"),
    ("https://owned.search.windows.net", "owned.search.windows.net", "old-index")])
def test_untrusted_endpoint_refuses_before_auth(endpoint, host, index):
    with pytest.raises(DeliveryRefused): SearchTarget(endpoint, index, host)


@pytest.mark.parametrize("mode", ["partial", "wrong_keys", "duplicate_keys", "redirect"])
def test_loader_partial_success(complete_inputs, tmp_path, mode):
    manifest = ReleaseManifest(1,"a"*40,(),"b"*64,"text-embedding-3-small","1",1536,229,41825,120,"c"*64)
    bundle = VerifiedBundle(complete_inputs, manifest, "d"*64)
    calls = []
    def server(request):
        calls.append(request)
        data = json.loads(request.content)["value"]
        assert len(data) <= 500 and len(request.content) <= 12*1024*1024
        rows = [{"key":r["chunk_id"],"status":True,"statusCode":201,"errorMessage":None} for r in data]
        if mode == "partial": rows[0]["status"] = False
        if mode == "wrong_keys": rows[0]["key"] = "unknown"
        if mode == "duplicate_keys": rows[0]["key"] = rows[1]["key"]
        if mode == "redirect": return httpx.Response(302, headers={"location":"https://evil.example"})
        return httpx.Response(207, json={"value":rows})
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server), follow_redirects=True) as client:
        with pytest.raises(DeliveryRefused):
            upload_bundle(bundle, target, Tokens(), client, reservation=tmp_path / "upload.json")
    assert len(calls) == 1
    assert (tmp_path / "upload.json").exists()
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no replay"))) as client:
        with pytest.raises(DeliveryRefused):
            upload_bundle(bundle, target, Tokens(), client, reservation=tmp_path / "upload.json")


def test_delayed_count_is_bounded():
    calls, sleeps = [], []
    def server(request):
        calls.append(request)
        return httpx.Response(200, text="41824")
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(DeliveryRefused): wait_for_count(target, Tokens(), client, sleep=sleeps.append)
    assert len(calls) == 24 and len(sleeps) == 23 and sum(sleeps) == 115


def test_count_exact_success():
    calls = []
    def server(request):
        calls.append(request)
        return httpx.Response(200, text="\ufeff41825")
    target = SearchTarget("https://owned.search.windows.net", "p8-demo-prod", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        assert wait_for_count(target, Tokens(), client) == 41825
    assert len(calls) == 1
