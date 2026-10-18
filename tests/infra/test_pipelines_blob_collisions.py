"""Azure's documented create-only collision must not consume the next budget slot."""
import importlib
import json

import httpx
import pytest


@pytest.fixture
def storage(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.storage")


class Tokens:
    def token(self):
        return "synthetic"


def test_metadata_budget_advances_after_blob_already_exists(storage):
    blobs = {}
    requests = []

    def server(request):
        requests.append(request)
        assert request.method == "PUT"
        assert request.headers["if-none-match"] == "*"
        key = request.url.path
        if key in blobs:
            return httpx.Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})
        blobs[key] = request.content
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        first = storage.BlobStore("owned.blob.core.windows.net", Tokens(), client)
        assert first.reserve_budget("metadata", 3) == "budgets/metadata/000.json"
        # A distinct stage or agent starts at slot zero again.
        second = storage.BlobStore("owned.blob.core.windows.net", Tokens(), client)
        assert second.reserve_budget("metadata", 3) == "budgets/metadata/001.json"

    assert [r.url.path for r in requests] == [
        "/evidence/budgets/metadata/000.json",
        "/evidence/budgets/metadata/000.json",
        "/evidence/budgets/metadata/001.json",
    ]
    assert {key: json.loads(value) for key, value in blobs.items()} == {
        "/evidence/budgets/metadata/000.json": {"budget": "metadata", "slot": 0},
        "/evidence/budgets/metadata/001.json": {"budget": "metadata", "slot": 1},
    }


def test_documented_collisions_cannot_exceed_budget(storage):
    paths = []

    def server(request):
        paths.append(request.url.path)
        return httpx.Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore("owned.blob.core.windows.net", Tokens(), client).reserve_budget("metadata", 3)
    assert caught.value.code == "reserved"
    assert paths == ["/evidence/budgets/metadata/000.json", "/evidence/budgets/metadata/001.json",
                     "/evidence/budgets/metadata/002.json"]


def test_duplicate_release_claim_still_refuses_without_retry_or_overwrite(storage):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore("owned.blob.core.windows.net", Tokens(), client).put_json(
                "claims/release.json", {"source_sha": "b" * 40})
    assert caught.value.code == "reserved"
    assert len(requests) == 1 and requests[0].headers["if-none-match"] == "*"


@pytest.mark.parametrize("error", [None, "LeaseAlreadyPresent", "ContainerBeingDeleted", "unknown"])
def test_other_conflicts_fail_without_allocating_another_slot(storage, error):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(409, headers={} if error is None else {"x-ms-error-code": error})

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore("owned.blob.core.windows.net", Tokens(), client).reserve_budget("metadata", 3)
    assert caught.value.code == "request" and len(requests) == 1


def test_unexpected_metadata_status_is_classified_without_response_body(storage):
    def server(request):
        return httpx.Response(503,text='private remote content')
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore("owned.blob.core.windows.net", Tokens(), client).reserve_budget("metadata",3)
    assert getattr(caught.value,'check',None)=='metadata-budget-http'
    assert getattr(caught.value,'http_status',None)==503
    assert 'private remote content' not in str(caught.value)


def test_metadata_timeout_is_classified_without_exception_text(storage):
    def server(request):
        raise httpx.ReadTimeout('private connection detail',request=request)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore("owned.blob.core.windows.net", Tokens(), client).reserve_budget("metadata",3)
    assert getattr(caught.value,'check',None)=='metadata-budget-transport'
    assert getattr(caught.value,'http_status','missing') is None
    assert 'private connection detail' not in str(caught.value)


@pytest.mark.parametrize('tamper',[False,True])
def test_stage_publishes_sanitized_request_diagnostic(storage,repo_root,monkeypatch,tmp_path,capsys,tamper):
    run=importlib.import_module('scripts.pipelines.run')
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(503,text='private response'))) as client:
        with pytest.raises(storage.DeliveryRefused) as caught:
            storage.BlobStore('owned.blob.core.windows.net',Tokens(),client).reserve_budget('metadata',3)
    error=caught.value
    error.args=('private exception detail',)
    if tamper:
        error.check='private operation'
        error.http_status='private status'
    monkeypatch.setattr(run,'context',lambda *args:({},{}))
    class FailedRuntime:
        def __init__(self,*args):raise error
    monkeypatch.setattr(run,'Runtime',FailedRuntime)
    published=tmp_path/'test'
    assert run.main(['test','--authority',str(tmp_path/'unused.json'),'--receipt-dir',str(published)])==2
    assert json.loads((published/'failure.json').read_bytes())=={
        'kind':'stage-failure','stage':'test','code':'request',
        'check':'unclassified' if tamper else 'metadata-budget-http',
        'http_status':None if tamper else 503}
    public=(published/'failure.json').read_text()+capsys.readouterr().out
    assert 'private' not in public
