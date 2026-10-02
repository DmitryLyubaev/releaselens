"""Embedding the corpus and a question. No network and no Azure CLI.

Every request goes to an `httpx.MockTransport`, and `AzureCliCredential` is replaced by a fake,
so nothing here can reach Azure OpenAI or run `az`. Nothing really sleeps: the embedder's
`sleep` is a list that records what it was asked to wait.
"""

import json

import httpx
import numpy as np
import pytest
from azure.core.credentials import AccessToken

from app.retrieval import azure_auth
from app.retrieval.azure_auth import TokenSource
from app.retrieval.corpus import Chunk
from app.retrieval.embed import embed_corpus, embed_query

_BASE_URL = "https://example-account.openai.azure.com/openai/v1/"
_DEPLOYMENT = "embed-not-real"
_SCOPE = "https://ai.azure.com/.default"
_DIMENSIONS = 4


class _FakeCliCredential:
    """Stands in for `AzureCliCredential`: hands out a fake token and records what it was asked."""

    instances: list["_FakeCliCredential"] = []

    def __init__(self, *, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.requested: list[tuple[str, ...]] = []
        self.expires_on = 0
        _FakeCliCredential.instances.append(self)

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.requested.append(scopes)
        return AccessToken(f"fake-token-{len(self.requested)}", self.expires_on)


@pytest.fixture(autouse=True)
def fake_cli(monkeypatch):
    _FakeCliCredential.instances = []
    monkeypatch.setattr(azure_auth, "AzureCliCredential", _FakeCliCredential)


def _tokens() -> TokenSource:
    # Expiry far beyond the fixed clock, so one token serves the whole test.
    source = TokenSource(_SCOPE, "tenant-not-real", clock=lambda: 0.0)
    _FakeCliCredential.instances[-1].expires_on = 10**9
    return source


def _chunks(count: int) -> list[Chunk]:
    # Chunk ids that are neither sequential nor sorted, so the saved id order means something.
    return [
        Chunk((index * 7919) % 100_003 + 1, f"commit:{index:07x}", "commit", f"{index:07x}", 0, 10,
              f"chunk text {index}")
        for index in range(count)
    ]


def _vector(text: str) -> list[float]:
    number = int(text.rsplit(" ", 1)[-1])
    return [float(number), number + 0.5, -float(number), 1.0]


def _reply(request: httpx.Request) -> httpx.Response:
    inputs = json.loads(request.content)["input"]
    # Reversed on purpose: the service's `index`, not the order of `data`, says which input a
    # vector belongs to.
    data = [{"object": "embedding", "index": i, "embedding": _vector(text)} for i, text in enumerate(inputs)]
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": data[::-1],
            "model": _DEPLOYMENT,
            "usage": {"prompt_tokens": 3 * len(inputs), "total_tokens": 3 * len(inputs)},
        },
    )


class _Recorder:
    """A transport handler that records every request and answers through `respond`."""

    def __init__(self, respond=_reply) -> None:
        self.requests: list[httpx.Request] = []
        self.respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def first_inputs(self) -> list[str]:
        return [json.loads(request.content)["input"][0] for request in self.requests]


def _embed(chunks, out_dir, recorder, **kwargs):
    return embed_corpus(
        chunks,
        base_url=_BASE_URL,
        deployment=_DEPLOYMENT,
        tokens=_tokens(),
        out_dir=out_dir,
        client=recorder.client(),
        **kwargs,
    )


def test_embedding_saves_rows_in_chunk_order(tmp_path):
    chunks = _chunks(130)
    recorder = _Recorder()

    run = _embed(chunks, tmp_path, recorder, batch_size=64)

    assert len(recorder.requests) == 3
    assert [len(json.loads(r.content)["input"]) for r in recorder.requests] == [64, 64, 2]
    for request in recorder.requests:
        assert request.method == "POST"
        assert str(request.url) == _BASE_URL + "embeddings"
        assert json.loads(request.content)["model"] == _DEPLOYMENT

    vectors = np.load(run.vectors_path)
    assert vectors.dtype == np.float32
    assert vectors.shape == (130, _DIMENSIONS)
    assert np.array_equal(vectors, np.array([_vector(c.content) for c in chunks], dtype=np.float32))

    assert run.vectors_path == tmp_path / f"{_DEPLOYMENT}.npy"
    assert run.ids_path == tmp_path / f"{_DEPLOYMENT}.ids.json"
    assert json.loads(run.ids_path.read_text(encoding="utf-8")) == [c.chunk_id for c in chunks]
    assert (tmp_path / f"{_DEPLOYMENT}.progress.json").exists()
    assert (run.deployment, run.tokens_billed, run.batches) == (_DEPLOYMENT, 3 * 130, 3)


def test_embedding_resumes_after_a_failed_batch(tmp_path):
    """Review Focus 1: a 429 then a dropped connection on batch 2 costs batch 1 nothing more."""
    chunks = _chunks(130)
    batch_two_first_input = chunks[64].content
    attempts_on_batch_two = 0

    def faulty(request: httpx.Request) -> httpx.Response:
        nonlocal attempts_on_batch_two
        if json.loads(request.content)["input"][0] != batch_two_first_input:
            return _reply(request)
        attempts_on_batch_two += 1
        if attempts_on_batch_two == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "429"}})
        raise httpx.ConnectError("connection dropped", request=request)

    failing = _Recorder(faulty)
    slept: list[float] = []
    with pytest.raises(httpx.ConnectError):
        _embed(chunks, tmp_path, failing, batch_size=64, sleep=slept.append)

    # Batch 1 once, then batch 2's first try and its 5 retries; batch 3 is never reached.
    assert failing.first_inputs() == [chunks[0].content] + [batch_two_first_input] * 6
    assert slept[0] == 0

    resumed = _Recorder()
    run = _embed(chunks, tmp_path, resumed, batch_size=64, sleep=slept.append)

    assert resumed.first_inputs() == [chunks[64].content, chunks[128].content]

    clean = _embed(chunks, tmp_path / "clean", _Recorder(), batch_size=64)
    assert np.array_equal(np.load(run.vectors_path), np.load(clean.vectors_path))
    assert json.loads(run.ids_path.read_text(encoding="utf-8")) == [c.chunk_id for c in chunks]
    # What the corpus cost, counted once per batch however many calls it took to finish.
    assert (run.tokens_billed, run.batches) == (clean.tokens_billed, clean.batches)


def test_429_honours_retry_after_then_succeeds(tmp_path):
    throttled_replies = 3

    def throttled(request: httpx.Request) -> httpx.Response:
        nonlocal throttled_replies
        if throttled_replies:
            throttled_replies -= 1
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"code": "429"}})
        return _reply(request)

    recorder = _Recorder(throttled)
    slept: list[float] = []
    run = _embed(_chunks(5), tmp_path, recorder, sleep=slept.append)

    assert slept == [7.0, 7.0, 7.0]
    assert len(recorder.requests) == 4
    assert run.batches == 1

    # Never more than 5 retries: a sixth 429 in a row is the caller's problem.
    always = _Recorder(lambda request: httpx.Response(429, headers={"Retry-After": "1"}))
    slept.clear()
    with pytest.raises(httpx.HTTPStatusError):
        _embed(_chunks(5), tmp_path / "always", always, sleep=slept.append)
    assert len(always.requests) == 6
    assert slept == [1.0] * 5


def test_no_key_header_is_ever_sent(tmp_path):
    recorder = _Recorder()
    _embed(_chunks(70), tmp_path, recorder, batch_size=64)
    vector, tokens = embed_query(
        "chunk text 3", base_url=_BASE_URL, deployment=_DEPLOYMENT, tokens=_tokens(),
        client=recorder.client(),
    )

    assert len(recorder.requests) == 3
    for request in recorder.requests:
        assert request.headers["Authorization"].startswith("Bearer fake-token-")
        assert "api-key" not in request.headers
    assert all(scopes == (_SCOPE,) for c in _FakeCliCredential.instances for scopes in c.requested)
    assert all(c.tenant_id == "tenant-not-real" for c in _FakeCliCredential.instances)

    assert vector.dtype == np.float32
    assert np.array_equal(vector, np.array(_vector("chunk text 3"), dtype=np.float32))
    assert tokens == 3


def test_token_source_caches_until_five_minutes_before_expiry():
    now = [1_000.0]
    source = TokenSource(_SCOPE, "tenant-not-real", clock=lambda: now[0])
    credential = _FakeCliCredential.instances[-1]
    credential.expires_on = 1_000 + 3_600

    assert source.token() == "fake-token-1"
    now[0] = 1_000 + 3_600 - 301
    assert source.token() == "fake-token-1"
    now[0] = 1_000 + 3_600 - 300
    credential.expires_on = 10**9
    assert source.token() == "fake-token-2"
    assert credential.requested == [(_SCOPE,), (_SCOPE,)]
