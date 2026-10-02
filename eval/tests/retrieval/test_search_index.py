"""The AI Search index: its schema, its upload, and the S2 and S3 queries. No network, no Azure CLI.

Every request goes to an `httpx.MockTransport`, and `AzureCliCredential` is replaced by a fake,
so nothing here can reach AI Search or run `az`.
"""

import json

import httpx
import numpy as np
import pytest
from azure.core.credentials import AccessToken

from app.retrieval import azure_auth
from app.retrieval.arms import Hit
from app.retrieval.azure_auth import TokenSource
from app.retrieval.corpus import Chunk
from app.retrieval.search_index import (
    API_VERSION,
    INDEX_NAME,
    SCOPE,
    SearchError,
    create_index,
    search,
    upload,
)

_ENDPOINT = "https://search-not-real.search.windows.net"
_VECTOR = np.array([0.5, -0.25, 0.125, 1.0], dtype=np.float32)


class _FakeCliCredential:
    """Stands in for `AzureCliCredential`: hands out a fake token and records the scopes asked."""

    instances: list["_FakeCliCredential"] = []

    def __init__(self, *, tenant_id: str) -> None:
        self.requested: list[tuple[str, ...]] = []
        _FakeCliCredential.instances.append(self)

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.requested.append(scopes)
        return AccessToken(f"fake-token-{len(self.requested)}", 10**9)


@pytest.fixture(autouse=True)
def fake_cli(monkeypatch):
    _FakeCliCredential.instances = []
    monkeypatch.setattr(azure_auth, "AzureCliCredential", _FakeCliCredential)


def _tokens() -> TokenSource:
    return TokenSource(SCOPE, "tenant-not-real", clock=lambda: 0.0)


class _Recorder:
    """A transport handler that records every request and answers through `respond`."""

    def __init__(self, respond) -> None:
        self.requests: list[httpx.Request] = []
        self.respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


def _results(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"value": [
        {"@search.score": 0.031, "@search.rerankerScore": 2.75, "chunk_id": "17", "artefact": "commit:abc1234"},
        {"@search.score": 0.033, "@search.rerankerScore": 1.5, "chunk_id": "9", "artefact": "issue:12"},
    ]})


def _search(recorder: _Recorder, *, semantic: bool) -> list[Hit]:
    return search("Which release dropped the old parser?", _VECTOR, semantic=semantic,
                  endpoint=_ENDPOINT, tokens=_tokens(), client=recorder.client())


def test_search_sends_hybrid_and_semantic_bodies():
    recorder = _Recorder(_results)

    _search(recorder, semantic=False)
    _search(recorder, semantic=True)

    hybrid = {
        "search": "Which release dropped the old parser?",
        "vectorQueries": [{"kind": "vector", "vector": [0.5, -0.25, 0.125, 1.0], "fields": "vector", "k": 50}],
        "top": 50,
        "select": "chunk_id,artefact",
    }
    assert recorder.bodies() == [hybrid, {**hybrid, "queryType": "semantic", "semanticConfiguration": "default"}]
    for request in recorder.requests:
        assert request.method == "POST"
        assert str(request.url) == (f"{_ENDPOINT}/indexes/releaselens-chunks/docs/search"
                                    f"?api-version=2026-04-01")
        assert request.headers["Authorization"] == "Bearer fake-token-1"
        assert "api-key" not in request.headers
    assert SCOPE == "https://search.azure.com/.default"
    assert all(credential.requested == [(SCOPE,)] for credential in _FakeCliCredential.instances)


def test_semantic_uses_reranker_score():
    recorder = _Recorder(_results)

    assert _search(recorder, semantic=True) == [Hit(17, "commit:abc1234", 2.75), Hit(9, "issue:12", 1.5)]
    # Without the ranker, the fused score, in the order the service ranked them.
    assert _search(recorder, semantic=False) == [Hit(17, "commit:abc1234", 0.031), Hit(9, "issue:12", 0.033)]


def _partial(returned: str | None):
    def respond(request: httpx.Request) -> httpx.Response:
        body = {**_results(request).json(), "@search.semanticPartialResponseReason": "CapacityOverloaded"}
        if returned is not None:
            body["@search.semanticPartialResponseType"] = returned
        return httpx.Response(200, json=body)
    return respond


@pytest.mark.parametrize("returned", ["baseResults", None])
def test_a_partial_semantic_response_is_an_error(returned):
    # AI Search's default is to answer 200 with unranked results when the ranker cannot run;
    # scored as S3, they would be S2's results under S3's name.
    with pytest.raises(SearchError, match="CapacityOverloaded"):
        _search(_Recorder(_partial(returned)), semantic=True)


def test_a_partial_response_that_was_reranked_is_kept():
    hits = _search(_Recorder(_partial("rerankedResults")), semantic=True)

    assert hits == [Hit(17, "commit:abc1234", 2.75), Hit(9, "issue:12", 1.5)]


def test_a_semantic_hit_without_a_reranker_score_is_an_error():
    def unscored(request: httpx.Request) -> httpx.Response:
        body = _results(request).json()
        body["value"][1]["@search.rerankerScore"] = None
        return httpx.Response(200, json=body)

    with pytest.raises(SearchError, match="1 semantic hits have no reranker score, the first chunk 9"):
        _search(_Recorder(unscored), semantic=True)


def test_a_failed_reply_keeps_its_body_verbatim():
    body = '{"error":{"code":"Forbidden","message":"Semantic ranker free quota exceeded (not real)."}}'
    recorder = _Recorder(lambda request: httpx.Response(403, text=body))

    with pytest.raises(SearchError) as raised:
        _search(recorder, semantic=True)

    assert str(raised.value) == f"HTTP 403: {body}"


def test_create_index_schema():
    recorder = _Recorder(lambda request: httpx.Response(201, json={"name": INDEX_NAME}))

    create_index(_ENDPOINT, _tokens(), client=recorder.client())

    (request,) = recorder.requests
    assert request.method == "PUT"
    assert str(request.url) == f"{_ENDPOINT}/indexes/releaselens-chunks?api-version={API_VERSION}"
    assert request.headers["Authorization"] == "Bearer fake-token-1"
    assert "api-key" not in request.headers
    assert json.loads(request.content) == {
        "name": "releaselens-chunks",
        "fields": [
            {"name": "chunk_id", "type": "Edm.String", "key": True, "searchable": False,
             "filterable": False, "sortable": False, "facetable": False, "retrievable": True},
            {"name": "artefact", "type": "Edm.String", "searchable": False,
             "filterable": True, "sortable": False, "facetable": False, "retrievable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "en.lucene",
             "filterable": False, "sortable": False, "facetable": False, "retrievable": True},
            {"name": "vector", "type": "Collection(Edm.Single)", "searchable": True,
             "retrievable": False, "dimensions": 1536, "vectorSearchProfile": "hnsw-cosine"},
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw", "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}],
            "profiles": [{"name": "hnsw-cosine", "algorithm": "hnsw"}],
        },
        "semantic": {
            "configurations": [
                {"name": "default", "prioritizedFields": {"prioritizedContentFields": [{"fieldName": "content"}]}},
            ],
        },
    }


def _chunks(count: int) -> list[Chunk]:
    # Chunk ids neither sequential nor sorted, so a document's vector must be found by its id.
    return [Chunk((index * 7919) % 100_003 + 1, f"commit:{index:07x}", "commit", f"{index:07x}", 0, 10,
                  f"chunk text {index}") for index in range(count)]


def _save(tmp_path, chunks: list[Chunk], *, dimensions: int = 1536, seed: int = 7):
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(len(chunks), dimensions)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    # Saved in reverse chunk order: the rows follow the ids file, not the chunk list.
    ids = [chunk.chunk_id for chunk in chunks][::-1]
    vectors_path, ids_path = tmp_path / "small.npy", tmp_path / "small.ids.json"
    np.save(vectors_path, vectors)
    ids_path.write_text(json.dumps(ids), encoding="utf-8")
    return vectors_path, ids_path, dict(zip(ids, vectors))


def _indexed(request: httpx.Request) -> httpx.Response:
    documents = json.loads(request.content)["value"]
    return httpx.Response(200, json={"value": [
        {"key": document["chunk_id"], "status": True, "errorMessage": None, "statusCode": 201}
        for document in documents]})


def test_upload_batches_and_fields(tmp_path):
    chunks = _chunks(1001)
    vectors_path, ids_path, saved = _save(tmp_path, chunks)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    recorder = _Recorder(_indexed)

    uploaded = upload(chunks, vectors_path, ids_path, _ENDPOINT, _tokens(), client=recorder.client(), batch=500)

    assert uploaded == 1001
    assert [len(body["value"]) for body in recorder.bodies()] == [500, 500, 1]
    seen = []
    for request, body in zip(recorder.requests, recorder.bodies()):
        assert request.method == "POST"
        assert str(request.url) == f"{_ENDPOINT}/indexes/releaselens-chunks/docs/index?api-version={API_VERSION}"
        assert request.headers["Authorization"] == "Bearer fake-token-1"
        assert "api-key" not in request.headers
        for document in body["value"]:
            assert set(document) == {"@search.action", "chunk_id", "artefact", "content", "vector"}
            assert document["@search.action"] == "upload"
            assert isinstance(document["chunk_id"], str)
            chunk = by_id[int(document["chunk_id"])]
            assert (document["artefact"], document["content"]) == (chunk.artefact, chunk.content)
            assert len(document["vector"]) == 1536
            # Written as short decimals, and still exactly the saved float32 values.
            assert np.array_equal(np.asarray(document["vector"], dtype=np.float32), saved[chunk.chunk_id])
            seen.append(chunk.chunk_id)
    assert sorted(seen) == sorted(by_id)


def test_a_full_batch_fits_the_request_limit(tmp_path):
    # AI Search refuses a request over 16 MB. 500 documents of 1,536 float32 values, written as
    # their float64 expansions, come to about 17 MB before any content. The corpus's chunks are
    # at most about 1,150 characters (320 tokens at 3.6 characters each), so this is generous.
    chunks = [Chunk(chunk.chunk_id, chunk.artefact, chunk.entity_type, chunk.entity_key, 0, 320, "x" * 1_500)
              for chunk in _chunks(500)]
    vectors_path, ids_path, _ = _save(tmp_path, chunks)
    recorder = _Recorder(_indexed)

    upload(chunks, vectors_path, ids_path, _ENDPOINT, _tokens(), client=recorder.client())

    (request,) = recorder.requests
    assert len(request.content) < 16_000_000


@pytest.mark.parametrize("change", ["a chunk without a vector", "a vector without a chunk", "the wrong width",
                                    "an all-zero row"])
def test_upload_refuses_vectors_that_do_not_match_before_sending(tmp_path, change):
    chunks = _chunks(10)
    if change == "a chunk without a vector":
        vectors_path, ids_path, _ = _save(tmp_path, chunks[:9])
    elif change == "a vector without a chunk":
        vectors_path, ids_path, _ = _save(tmp_path, chunks)
        chunks = chunks[:9]
    elif change == "the wrong width":
        vectors_path, ids_path, _ = _save(tmp_path, chunks, dimensions=3072)
    else:
        # A row an unfinished embedding run never wrote: the memory-mapped file starts as zeros.
        vectors_path, ids_path, _ = _save(tmp_path, chunks)
        saved = np.load(vectors_path)
        saved[6] = 0
        np.save(vectors_path, saved)
    recorder = _Recorder(_indexed)

    with pytest.raises(ValueError):
        upload(chunks, vectors_path, ids_path, _ENDPOINT, _tokens(), client=recorder.client())

    assert recorder.requests == []


def test_upload_refuses_a_document_the_service_rejected(tmp_path):
    chunks = _chunks(3)
    vectors_path, ids_path, _ = _save(tmp_path, chunks)

    def one_failed(request: httpx.Request) -> httpx.Response:
        documents = json.loads(request.content)["value"]
        return httpx.Response(207, json={"value": [
            {"key": document["chunk_id"], "status": index != 1,
             "errorMessage": "Document is too large (not real)." if index == 1 else None,
             "statusCode": 400 if index == 1 else 201}
            for index, document in enumerate(documents)]})

    with pytest.raises(SearchError, match="Document is too large"):
        upload(chunks, vectors_path, ids_path, _ENDPOINT, _tokens(), client=_Recorder(one_failed).client())
