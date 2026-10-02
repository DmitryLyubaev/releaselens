"""The arms: exact cosine for E2 and E3, AI Search for S2 and S3, the Worker's file for E1 and S1.

No network: the embedder is a plain function, every search request goes to an
`httpx.MockTransport`, and the tokens are a stub, so nothing here can reach Azure.
"""

import json

import httpx
import numpy as np
import pytest

from app.retrieval.arms import (
    ArmResult,
    Hit,
    exact_cosine,
    read_worker_output,
    run_embedding_arm,
    run_search_arm,
)
from app.retrieval.questions import Question

_ENDPOINT = "https://search-not-real.search.windows.net"


class _Tokens:
    def token(self) -> str:
        return "fake-token"


def _corpus(rows: int, dimensions: int, *, seed: int = 20261002):
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(rows, dimensions)).astype(np.float32)
    # Chunk ids neither sequential nor sorted, so a hit's id must come from its row.
    ids = [int(chunk_id) for chunk_id in rng.permutation(np.arange(1, rows + 1) * 13)]
    artefacts = {chunk_id: f"commit:{chunk_id:07x}" for chunk_id in ids}
    return rng, vectors, ids, artefacts


def _questions(count: int) -> list[Question]:
    return [Question(f"q{index:03d}", f"question {index}", f"commit:{index:07x}", "commit")
            for index in range(count)]


def test_exact_cosine_matches_brute_force():
    rng, vectors, ids, artefacts = _corpus(2_000, 64)
    query = rng.normal(size=64).astype(np.float32)

    hits = exact_cosine(vectors, ids, artefacts, query, k=50)

    full = (vectors.astype(np.float64) @ query.astype(np.float64)) / (
        np.linalg.norm(vectors.astype(np.float64), axis=1) * np.linalg.norm(query.astype(np.float64)))
    expected = np.argsort(-full)[:50]
    assert [hit.chunk_id for hit in hits] == [ids[row] for row in expected]
    assert [hit.score for hit in hits] == pytest.approx([full[row] for row in expected], abs=1e-6)
    assert [hit.artefact for hit in hits] == [artefacts[ids[row]] for row in expected]
    assert all(isinstance(hit.score, float) for hit in hits)


def _save(tmp_path, vectors: np.ndarray, ids: list[int]):
    vectors_path, ids_path = tmp_path / "large.npy", tmp_path / "large.ids.json"
    np.save(vectors_path, vectors)
    ids_path.write_text(json.dumps(ids), encoding="utf-8")
    return vectors_path, ids_path


def test_embedding_arm_searches_the_saved_vectors_and_keeps_each_query_vector(tmp_path):
    rng, vectors, ids, artefacts = _corpus(500, 32)
    vectors_path, ids_path = _save(tmp_path, vectors, ids)
    queries = {f"question {index}": rng.normal(size=32).astype(np.float32) for index in range(3)}

    def embed(text: str):
        if text == "question 1":
            raise httpx.ConnectError("connection dropped (not real)")
        if text == "question 3":
            return np.ones(31, dtype=np.float32), 7
        return queries[text], 7

    kept: dict[str, np.ndarray] = {}
    results = run_embedding_arm("E3", _questions(4), vectors_path=vectors_path, ids_path=ids_path,
                                artefacts=artefacts, embed=embed, query_vectors=kept)

    assert [(result.qid, result.arm) for result in results] == [("q000", "E3"), ("q001", "E3"), ("q002", "E3"),
                                                                ("q003", "E3")]
    for result, text in ((results[0], "question 0"), (results[2], "question 2")):
        expected = exact_cosine(vectors, ids, artefacts, queries[text])
        assert [hit.chunk_id for hit in result.hits] == [hit.chunk_id for hit in expected]
        assert [hit.score for hit in result.hits] == pytest.approx([hit.score for hit in expected], abs=1e-6)
        assert (result.error, result.query_tokens) == (None, 7)
        assert result.ms > 0
    assert results[1].hits == [] and results[1].query_tokens == 0
    assert results[1].error == "ConnectError: connection dropped (not real)"
    # Embedded, and billed, but not searchable: the tokens still count.
    assert results[3].hits == [] and results[3].error.startswith("ValueError") and results[3].query_tokens == 7
    assert set(kept) == {"q000", "q002"}
    assert np.array_equal(kept["q002"], queries["question 2"])


def test_embedding_arm_refuses_mismatched_files_before_embedding(tmp_path):
    _, vectors, ids, artefacts = _corpus(20, 8)
    vectors_path, ids_path = _save(tmp_path, vectors, ids[:19])
    calls = []

    with pytest.raises(ValueError):
        run_embedding_arm("E2", _questions(2), vectors_path=vectors_path, ids_path=ids_path,
                          artefacts=artefacts, embed=lambda text: calls.append(text))

    assert calls == []


def test_an_errored_question_is_dropped_not_missed():
    # Review Focus 2, the arm half: each failure is that question's error, with no hits, never
    # an empty list scored as a miss, and the questions after it still run.
    billing = '{"error":{"code":"Forbidden","message":"Semantic ranker free quota used up (not real)."}}'
    replies = iter([
        httpx.Response(503, text="Service Unavailable"),
        httpx.ReadTimeout("timed out (not real)"),
        httpx.Response(403, text=billing),
        httpx.Response(200, json={"value": [
            {"@search.score": 0.03, "@search.rerankerScore": 3.1, "chunk_id": "26", "artefact": "issue:7"}]}),
    ])

    def respond(request: httpx.Request) -> httpx.Response:
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    questions = _questions(5)
    vectors = {question.qid: np.ones(4, dtype=np.float32) for question in questions[:4]}
    client = httpx.Client(transport=httpx.MockTransport(respond))

    results = run_search_arm("S3", questions, query_vectors=vectors, endpoint=_ENDPOINT, tokens=_Tokens(),
                             client=client)

    assert [result.qid for result in results] == ["q000", "q001", "q002", "q003", "q004"]
    assert all(result.arm == "S3" and result.query_tokens == 0 for result in results)
    failed = results[:3] + results[4:]
    assert all(result.hits == [] and result.error for result in failed)
    assert results[0].error == "SearchError: HTTP 503: Service Unavailable"
    assert results[1].error == "ReadTimeout: timed out (not real)"
    assert results[2].error == f"SearchError: HTTP 403: {billing}"
    # E2 did not embed this question, so S2 and S3 have nothing to search with.
    assert results[4].error == "no query vector: E2 did not embed this question"
    assert results[3] == ArmResult("q003", "S3", [Hit(26, "issue:7", 3.1)], results[3].ms, None)


def test_read_worker_output(tmp_path):
    path = tmp_path / "s1.jsonl"
    lines = [
        {"qid": "q000", "arm": "S1", "hits": [{"chunkId": 41, "artefact": "pull_request:12", "score": 0.82},
                                             {"chunkId": 5, "artefact": "commit:abc1234", "score": 0.5}],
         "ms": 12.5, "error": None},
        {"qid": "q001", "arm": "S1", "hits": [], "ms": 3.25,
         "error": "NpgsqlException: Exception while reading from stream"},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n\n", encoding="utf-8")

    assert read_worker_output(path) == [
        ArmResult("q000", "S1", [Hit(41, "pull_request:12", 0.82), Hit(5, "commit:abc1234", 0.5)], 12.5, None),
        ArmResult("q001", "S1", [], 3.25, "NpgsqlException: Exception while reading from stream"),
    ]


def test_read_worker_output_refuses_another_arm(tmp_path):
    path = tmp_path / "e2.jsonl"
    path.write_text(json.dumps({"qid": "q000", "arm": "E2", "hits": [], "ms": 1.0, "error": None}) + "\n",
                    encoding="utf-8")

    with pytest.raises(ValueError, match="E2"):
        read_worker_output(path)
