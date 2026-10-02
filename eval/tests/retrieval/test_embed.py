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
from app.retrieval import embed as embed_module
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


def _partial_run(out_dir, chunks):
    """Batch 1 of 3 saved and recorded, then a 400 on batch 2 ends the run."""

    def fails_batch_two(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["input"][0] == chunks[64].content:
            return httpx.Response(400, json={"error": {"code": "BadRequest"}})
        return _reply(request)

    with pytest.raises(httpx.HTTPStatusError):
        _embed(chunks, out_dir, _Recorder(fails_batch_two), batch_size=64)
    progress = json.loads((out_dir / f"{_DEPLOYMENT}.progress.json").read_text(encoding="utf-8"))
    assert list(progress["done"]) == ["0"]


def _files(out_dir) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(out_dir.iterdir())}


def _assert_refused(chunks, out_dir, **kwargs) -> None:
    """The resume raises before any request, and leaves every file as it was."""
    before = _files(out_dir)
    recorder = _Recorder()
    with pytest.raises(ValueError):
        _embed(chunks, out_dir, recorder, **kwargs)
    assert recorder.requests == []
    assert _files(out_dir) == before


def test_resume_refuses_a_different_chunk_list(tmp_path):
    chunks = _chunks(130)
    _partial_run(tmp_path, chunks)

    _assert_refused(chunks[:100], tmp_path, batch_size=64)
    _assert_refused(chunks[::-1], tmp_path, batch_size=64)


@pytest.mark.parametrize("change", ["deployment", "batch_size"])
def test_resume_refuses_another_deployment_or_batch_size(tmp_path, change):
    chunks = _chunks(130)
    _partial_run(tmp_path, chunks)

    if change == "deployment":
        # The files are named for the deployment, so only a copied or renamed progress file can
        # name another one.
        progress_path = tmp_path / f"{_DEPLOYMENT}.progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress["deployment"] = "another-embed-not-real"
        progress_path.write_text(json.dumps(progress), encoding="utf-8")
        _assert_refused(chunks, tmp_path, batch_size=64)
    else:
        _assert_refused(chunks, tmp_path, batch_size=32)


def test_resume_refuses_progress_without_its_vectors(tmp_path):
    chunks = _chunks(130)
    _partial_run(tmp_path, chunks)
    (tmp_path / f"{_DEPLOYMENT}.npy").unlink()

    _assert_refused(chunks, tmp_path, batch_size=64)


@pytest.mark.parametrize(
    ("finished", "replacement"),
    [
        (False, np.zeros((130, _DIMENSIONS + 1), dtype=np.float32)),
        (False, np.zeros((130, _DIMENSIONS), dtype=np.float64)),
        (False, np.zeros((129, _DIMENSIONS), dtype=np.float32)),
        (True, np.zeros((130, _DIMENSIONS + 1), dtype=np.float32)),
    ],
    ids=["partial-dimensions", "partial-dtype", "partial-rows", "finished-dimensions"],
)
def test_resume_refuses_vectors_that_do_not_match_the_record(tmp_path, finished, replacement):
    chunks = _chunks(130)
    if finished:
        _embed(chunks, tmp_path, _Recorder(), batch_size=64)
    else:
        _partial_run(tmp_path, chunks)
    np.save(tmp_path / f"{_DEPLOYMENT}.npy", replacement)

    _assert_refused(chunks, tmp_path, batch_size=64)


def test_a_batch_flushed_but_not_recorded_is_sent_again(tmp_path, monkeypatch):
    """The one window that pays twice: rows flushed, then a crash before the batch's record."""
    chunks = _chunks(130)
    real_write_json = embed_module._write_json

    def crash_on_the_last_record(path, value):
        if path.name.endswith(".progress.json") and "2" in value["done"]:
            raise RuntimeError("crashed before the record")
        real_write_json(path, value)

    monkeypatch.setattr(embed_module, "_write_json", crash_on_the_last_record)
    with pytest.raises(RuntimeError):
        _embed(chunks, tmp_path, _Recorder(), batch_size=64)
    monkeypatch.setattr(embed_module, "_write_json", real_write_json)

    # Batch 3's rows are in the file, but the record says only batches 1 and 2 are saved.
    expected = np.array([_vector(c.content) for c in chunks], dtype=np.float32)
    assert np.array_equal(np.load(tmp_path / f"{_DEPLOYMENT}.npy")[128:], expected[128:])
    progress = json.loads((tmp_path / f"{_DEPLOYMENT}.progress.json").read_text(encoding="utf-8"))
    assert sorted(progress["done"]) == ["0", "1"]

    resumed = _Recorder()
    run = _embed(chunks, tmp_path, resumed, batch_size=64)

    assert resumed.first_inputs() == [chunks[128].content]
    clean = _embed(chunks, tmp_path / "clean", _Recorder(), batch_size=64)
    assert np.array_equal(np.load(run.vectors_path), np.load(clean.vectors_path))
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
