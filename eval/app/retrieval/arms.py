"""The benchmark's arms: each one's top 50 chunks per question, with scores and its own timing.

Spec §4. E2 and E3 are exact cosine over the saved corpus vectors of `-small` and `-large`, so
no approximate index can blur the comparison of models. S2 and S3 query the AI Search index as
that system really runs, reusing E2's question vectors, so they pay for no embedding. E1 and S1
run in the Worker's `retrieve` command, and are read here from its JSON lines.

An arm that fails on one question records that question's `error`, with no hits, and moves on:
scoring drops the question from that arm's pairs, and never counts it as a miss. This does not
score anything, and it does not decide which questions to run.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from .azure_auth import TokenSource
from .questions import Question

K = 50
EMBEDDING_ARMS = ("E2", "E3")
SEARCH_ARMS = ("S2", "S3")
WORKER_ARMS = ("E1", "S1")
NO_QUERY_VECTOR = "no query vector: E2 did not embed this question"


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    artefact: str
    score: float


@dataclass
class ArmResult:
    """One question on one arm. `hits` is empty exactly when `error` says why.

    `ms` times what the arm did for the question; `query_tokens` is what embedding the question
    was billed, and stays 0 for an arm that embeds nothing it pays for.
    """

    qid: str
    arm: str
    hits: list[Hit]
    ms: float
    error: str | None
    query_tokens: int = 0


def _describe(error: Exception) -> str:
    # The Worker's form for E1 and S1, so every arm's errors read alike.
    return f"{type(error).__name__}: {error}"


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _unit(matrix: np.ndarray, *, in_place: bool = False) -> np.ndarray:
    # einsum, not linalg.norm, so `-large`'s half-gigabyte corpus is not squared into a copy.
    norms = np.sqrt(np.einsum("...i,...i->...", matrix, matrix))[..., np.newaxis]
    if np.any(norms == 0):
        raise ValueError("a zero vector has no cosine")
    return np.divide(matrix, norms, out=matrix if in_place else None)


def _top(unit_vectors: np.ndarray, ids: list[int], artefacts: Mapping[int, str], unit_query: np.ndarray,
         k: int) -> list[Hit]:
    scores = unit_vectors @ unit_query
    k = min(k, len(scores))
    best = np.argpartition(-scores, k - 1)[:k]
    # Best first; an exact tie goes to the earlier row, so a rerun ranks it the same way.
    ranked = best[np.lexsort((best, -scores[best]))]
    return [Hit(ids[row], artefacts[ids[row]], float(scores[row])) for row in ranked]


def exact_cosine(vectors: np.ndarray, ids: list[int], artefacts: Mapping[int, str], query: np.ndarray,
                 k: int = K) -> list[Hit]:
    """The `k` rows nearest `query` by cosine, best first, over every row: no index, no approximation."""
    if len(ids) != len(vectors):
        raise ValueError(f"{len(ids)} chunk ids for {len(vectors)} vectors")
    return _top(_unit(vectors), ids, artefacts, _unit(query), k)


def run_embedding_arm(
    arm: str,
    questions: list[Question],
    *,
    vectors_path: Path,
    ids_path: Path,
    artefacts: Mapping[int, str],
    embed: Callable[[str], tuple[np.ndarray, int]],
    query_vectors: dict[str, np.ndarray] | None = None,
) -> list[ArmResult]:
    """E2 or E3: embed each question with `embed`, then exact cosine over the saved corpus vectors.

    A question's `ms` covers its embedding call and the search. The corpus is normalised once,
    before any question, because a deployed system would store it so. When `query_vectors` is
    given, each question embedded is stored in it under its qid, so S2 and S3 can reuse E2's.
    """
    if arm not in EMBEDDING_ARMS:
        raise ValueError(f"{arm} is not an embedding arm; use one of {EMBEDDING_ARMS}")
    vectors = np.load(vectors_path)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    if vectors.ndim != 2 or len(ids) != len(vectors):
        raise ValueError(f"{ids_path} holds {len(ids)} chunk ids, but {vectors_path} holds {vectors.shape}")
    unknown = [chunk_id for chunk_id in ids if chunk_id not in artefacts]
    if unknown:
        raise ValueError(f"{len(unknown)} saved chunk ids are not in the corpus, the first {unknown[0]}")
    unit = _unit(vectors, in_place=True)

    results = []
    for question in questions:
        start = time.perf_counter()
        used = 0
        try:
            vector, used = embed(question.question)
            hits = _top(unit, ids, artefacts, _unit(np.asarray(vector, dtype=np.float32)), K)
        except Exception as error:
            # An embedding that was billed and then failed to search still counts its tokens.
            results.append(ArmResult(question.qid, arm, [], _elapsed_ms(start), _describe(error), used))
            continue
        results.append(ArmResult(question.qid, arm, hits, _elapsed_ms(start), None, used))
        if query_vectors is not None:
            query_vectors[question.qid] = np.asarray(vector, dtype=np.float32)
    return results


def run_search_arm(
    arm: str,
    questions: list[Question],
    *,
    query_vectors: Mapping[str, np.ndarray],
    endpoint: str,
    tokens: TokenSource,
    client: httpx.Client | None = None,
) -> list[ArmResult]:
    """S2 (hybrid) or S3 (hybrid with the semantic ranker), with E2's vector for each question.

    A question's `ms` covers the search call only: its vector was embedded, and timed, by E2. Any
    reply that is not a success, and any exception, is that question's `error`, with the reply's
    body verbatim, so a ranker billing error says what it was. Nothing is retried.
    """
    # Imported here because search_index builds this module's Hits.
    from .search_index import new_client, search

    if arm not in SEARCH_ARMS:
        raise ValueError(f"{arm} is not a search arm; use one of {SEARCH_ARMS}")
    semantic = arm == "S3"
    owned = client is None
    client = client or new_client()
    results = []
    try:
        for question in questions:
            vector = query_vectors.get(question.qid)
            if vector is None:
                results.append(ArmResult(question.qid, arm, [], 0.0, NO_QUERY_VECTOR))
                continue
            start = time.perf_counter()
            try:
                hits = search(question.question, vector, semantic=semantic, endpoint=endpoint, tokens=tokens,
                              client=client)
            except Exception as error:
                results.append(ArmResult(question.qid, arm, [], _elapsed_ms(start), _describe(error)))
                continue
            results.append(ArmResult(question.qid, arm, hits, _elapsed_ms(start), None))
    finally:
        if owned:
            client.close()
    return results


def read_worker_output(path: Path) -> list[ArmResult]:
    """E1's or S1's results, from the Worker `retrieve` command's JSON lines, in the file's order."""
    results = []
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["arm"] not in WORKER_ARMS:
                raise ValueError(f"{path} holds arm {row['arm']}; the Worker writes only {WORKER_ARMS}")
            hits = [Hit(hit["chunkId"], hit["artefact"], float(hit["score"])) for hit in row["hits"]]
            results.append(ArmResult(row["qid"], row["arm"], hits, float(row["ms"]), row["error"]))
    return results
