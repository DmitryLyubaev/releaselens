"""Plain private Blob store: create-only evidence, per-run namespaces, one overwritable pointer."""
import importlib
import json

import httpx
import pytest

HOST = "owned.blob.core.windows.net"


@pytest.fixture
def storage(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.storage")


class Tokens:
    def token(self):
        return "synthetic"


def store(storage, server):
    client = httpx.Client(transport=httpx.MockTransport(server))
    return storage.BlobStore(HOST, Tokens(), client), client


def test_run_keys_are_namespaced(storage):
    assert storage.run_key(41, "receipts/test.json") == "runs/41/receipts/test.json"
    assert storage.run_key(41, "a.json") != storage.run_key(42, "a.json")
    assert storage.run_key(4, "1/a.json") != storage.run_key(41, "a.json")


def test_put_once_refuses_existing_key(storage):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})

    blobs, client = store(storage, server)
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.put_json("runs/41/claims/release.json", {"source_sha": "b" * 40})
    assert caught.value.code == "reserved"
    assert len(requests) == 1 and requests[0].headers["if-none-match"] == "*"
    assert requests[0].url.path == "/evidence/runs/41/claims/release.json"


def test_put_once_treats_precondition_failure_as_existing(storage):
    blobs, client = store(storage, lambda request: httpx.Response(412))
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.put_json("runs/41/a.json", {})
    assert caught.value.code == "reserved"


@pytest.mark.parametrize("error", [None, "LeaseAlreadyPresent", "ContainerBeingDeleted"])
def test_other_conflicts_are_request_failures(storage, error):
    headers = {} if error is None else {"x-ms-error-code": error}
    blobs, client = store(storage, lambda request: httpx.Response(409, headers=headers))
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.put_json("runs/41/a.json", {})
    assert caught.value.code == "request"


def test_put_once_only_writes_evidence(storage):
    blobs, client = store(storage, lambda request: pytest.fail("no request expected"))
    with client:
        for container in ("inputs", "test-state", "prod-state", "service-state"):
            with pytest.raises(storage.DeliveryRefused) as caught:
                blobs.put_once(container, "k.json", b"{}")
            assert caught.value.code == "authority"


def test_unexpected_status_and_transport_errors_are_classified_without_detail(storage):
    blobs, client = store(storage, lambda request: httpx.Response(503, text="private remote content"))
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.put_json("runs/41/a.json", {})
    assert caught.value.code == "request" and caught.value.http_status == 503
    assert "private remote content" not in str(caught.value)

    def timeout(request):
        raise httpx.ReadTimeout("private connection detail", request=request)

    blobs, client = store(storage, timeout)
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.put_json("runs/41/a.json", {})
    assert caught.value.code == "request" and "private connection detail" not in str(caught.value)


def test_pointer_overwrites_without_if_none_match(storage):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(201)

    blobs, client = store(storage, server)
    with client:
        blobs.write_pointer({"release_run_id": 41})
        blobs.write_pointer({"release_run_id": 42})
    assert len(requests) == 2
    for request in requests:
        assert request.method == "PUT"
        assert request.url.path == "/evidence/current.json"
        assert "if-none-match" not in request.headers and "if-match" not in request.headers
        assert request.headers["x-ms-blob-type"] == "BlockBlob"
    assert json.loads(requests[1].content) == {"release_run_id": 42}


def test_pointer_write_failure_is_refused(storage):
    blobs, client = store(storage, lambda request: httpx.Response(403, text="private"))
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.write_pointer({"release_run_id": 41})
    assert caught.value.code == "request" and "private" not in str(caught.value)


def test_pointer_http_failure_carries_label_and_status(storage):
    blobs, client = store(storage, lambda request: httpx.Response(503, text="private"))
    with client, pytest.raises(storage.RequestRefused) as caught:
        blobs.write_pointer({"release_run_id": 41})
    assert (caught.value.code, caught.value.check, caught.value.http_status) == ("request", "pointer-http", 503)


def test_pointer_transport_failure_carries_label(storage):
    def server(request):
        raise httpx.ConnectError("private transport detail")

    blobs, client = store(storage, server)
    with client, pytest.raises(storage.RequestRefused) as caught:
        blobs.write_pointer({"release_run_id": 41})
    assert (caught.value.check, caught.value.http_status) == ("pointer-transport", None)
    assert "private" not in str(caught.value) and caught.value.__cause__ is None


def test_pointer_round_trips(storage):
    seen = []

    def server(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"release_run_id": 41})

    blobs, client = store(storage, server)
    with client:
        assert blobs.read_pointer() == {"release_run_id": 41}
    assert seen == ["/evidence/current.json"]


def test_containers_allowlist_includes_service_state(storage):
    blobs, client = store(storage, lambda request: httpx.Response(200, content=b"x"))
    with client:
        assert blobs._url("service-state", "service.tfstate") == f"https://{HOST}/service-state/service.tfstate"
        for container in ("inputs", "evidence", "test-state", "prod-state"):
            blobs._url(container, "k")
        with pytest.raises(storage.DeliveryRefused):
            blobs._url("other-state", "k")


def test_service_state_read_returns_none_on_404(storage):
    seen = []

    def server(request):
        seen.append(request.url.path)
        return httpx.Response(404, headers={"x-ms-error-code": "BlobNotFound"})

    blobs, client = store(storage, server)
    with client:
        assert blobs.state("service") is None
    assert seen == ["/service-state/service.tfstate"]


@pytest.mark.parametrize("target,path", [("test", "/test-state/index.tfstate"), ("demo-prod", "/prod-state/index.tfstate")])
def test_index_state_paths_are_unchanged(storage, target, path):
    seen = []

    def server(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"lineage": "l", "serial": 3})

    blobs, client = store(storage, server)
    with client:
        assert blobs.state(target) == {"lineage": "l", "serial": 3}
    assert seen == [path]


def test_state_empty_placeholder_is_none_and_other_failures_refuse(storage):
    blobs, client = store(storage, lambda request: httpx.Response(200, headers={"content-length": "0"}))
    with client:
        assert blobs.state("service") is None
    for response in (httpx.Response(404, headers={"x-ms-error-code": "ContainerNotFound"}),
                     httpx.Response(500), httpx.Response(200, json={"serial": 1}), httpx.Response(200, content=b"not json")):
        blobs, client = store(storage, lambda request, r=response: r)
        with client, pytest.raises(storage.DeliveryRefused) as caught:
            blobs.state("service")
        assert caught.value.code == "plan"


def test_state_unknown_target_is_refused(storage):
    blobs, client = store(storage, lambda request: pytest.fail("no request expected"))
    with client, pytest.raises(storage.DeliveryRefused) as caught:
        blobs.state("prod")
    assert caught.value.code == "target"


def test_no_metadata_budget_api(storage):
    for name in ("reserve_metadata", "require_workload_open", "reserve_budget", "reserve_cleanup_metadata", "_reserve_range"):
        assert not hasattr(storage.BlobStore, name)
    assert not hasattr(storage, "METADATA_TOTAL") and not hasattr(storage, "METADATA_OPERATIONS")
    with pytest.raises(TypeError):
        storage.BlobStore(HOST, Tokens(), httpx.Client(), metadata_policy=None)
