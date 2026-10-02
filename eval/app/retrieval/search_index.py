"""The AI Search index behind S2 and S3: its schema, its documents, and one hybrid query. Keyless.

Spec §4. The index holds every chunk with its saved `-small` vector, so the corpus is never
embedded twice. Every call carries a bearer token for the search scope: the service runs with
local authentication off, so no `api-key` header is sent, and no key is read anywhere.

This does not create the search service (`infra/search` does), and it retries nothing. Creating
the index and uploading are both idempotent, so a failed build is simply run again; a failed
query is that question's error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import numpy as np

from .arms import K, Hit
from .azure_auth import TokenSource
from .corpus import Chunk

INDEX_NAME = "releaselens-chunks"
API_VERSION = "2026-04-01"
SCOPE = "https://search.azure.com/.default"
DIMENSIONS = 1536
SEMANTIC_CONFIGURATION = "default"
_TIMEOUT_SECONDS = 120.0

# Every attribute is stated, because the service's defaults make a string field filterable,
# sortable and facetable, which nothing here uses.
SCHEMA = {
    "name": INDEX_NAME,
    "fields": [
        {"name": "chunk_id", "type": "Edm.String", "key": True, "searchable": False,
         "filterable": False, "sortable": False, "facetable": False, "retrievable": True},
        {"name": "artefact", "type": "Edm.String", "searchable": False,
         "filterable": True, "sortable": False, "facetable": False, "retrievable": True},
        # Retrievable, because the semantic ranker reads only retrievable fields.
        {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "en.lucene",
         "filterable": False, "sortable": False, "facetable": False, "retrievable": True},
        {"name": "vector", "type": "Collection(Edm.Single)", "searchable": True,
         "retrievable": False, "dimensions": DIMENSIONS, "vectorSearchProfile": "hnsw-cosine"},
    ],
    "vectorSearch": {
        # Only the metric is set: HNSW's other parameters keep the service's defaults.
        "algorithms": [{"name": "hnsw", "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}],
        "profiles": [{"name": "hnsw-cosine", "algorithm": "hnsw"}],
    },
    "semantic": {
        "configurations": [
            {"name": SEMANTIC_CONFIGURATION,
             "prioritizedFields": {"prioritizedContentFields": [{"fieldName": "content"}]}},
        ],
    },
}


class SearchError(Exception):
    """A reply from AI Search that was not a success. Its message keeps the reply's body verbatim."""


def new_client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT_SECONDS)


@contextmanager
def _client(client: httpx.Client | None) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    with new_client() as owned:
        yield owned


def _compact(vector: np.ndarray) -> list[float]:
    """A float32 vector as the shortest decimals that read back as the same float32 values.

    `tolist()` would write each value's float64 expansion, about 20 characters, and a batch of
    500 documents would then pass AI Search's 16 MB request limit; these come to about half.
    """
    return [float(text) for text in np.asarray(vector, dtype=np.float32).astype(str)]


def _send(client: httpx.Client, method: str, endpoint: str, path: str, body: dict,
          tokens: TokenSource) -> httpx.Response:
    response = client.request(
        method,
        f"{endpoint.rstrip('/')}/{path}",
        params={"api-version": API_VERSION},
        headers={"Authorization": f"Bearer {tokens.token()}", "Content-Type": "application/json"},
        content=json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"),
    )
    if not response.is_success:
        raise SearchError(f"HTTP {response.status_code}: {response.text}")
    return response


def create_index(endpoint: str, tokens: TokenSource, client: httpx.Client | None = None) -> None:
    """Create the index with spec §4's schema, or update it in place when it already exists."""
    with _client(client) as http:
        _send(http, "PUT", endpoint, f"indexes/{INDEX_NAME}", SCHEMA, tokens)


def upload(
    chunks: list[Chunk],
    vectors_path: Path,
    ids_path: Path,
    endpoint: str,
    tokens: TokenSource,
    client: httpx.Client | None = None,
    batch: int = 500,
) -> int:
    """Upload every chunk with its saved `-small` vector, `batch` documents a request; the count sent.

    The saved vectors must cover exactly the chunks given, at 1,536 dimensions; anything else is
    refused before the first request. A document the service rejects fails the upload, naming it.
    """
    vectors = np.load(vectors_path)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    if vectors.ndim != 2 or vectors.shape[1] != DIMENSIONS or len(ids) != len(vectors):
        raise ValueError(f"{vectors_path} holds {vectors.shape} for {len(ids)} chunk ids; "
                         f"the index needs one {DIMENSIONS}-dimension row per id")
    if len(set(ids)) != len(ids) or set(ids) != set(by_id) or len(by_id) != len(chunks):
        raise ValueError(f"{ids_path} does not hold exactly the {len(chunks)} chunks given, each once")

    with _client(client) as http:
        for start in range(0, len(ids), batch):
            documents = [
                {"@search.action": "upload", "chunk_id": str(chunk_id), "artefact": by_id[chunk_id].artefact,
                 "content": by_id[chunk_id].content, "vector": _compact(vectors[row])}
                for row, chunk_id in enumerate(ids[start:start + batch], start)
            ]
            response = _send(http, "POST", endpoint, f"indexes/{INDEX_NAME}/docs/index",
                             {"value": documents}, tokens)
            # A 207 is a success with some documents refused, each with its own status.
            failed = [item for item in response.json()["value"] if not item["status"]]
            if failed:
                raise SearchError(f"HTTP {response.status_code}: {len(failed)} of {len(documents)} documents "
                                  f"refused, the first {failed[0]['key']}: {failed[0].get('errorMessage')}")
    return len(ids)


def search(
    question: str,
    vector: np.ndarray,
    *,
    semantic: bool,
    endpoint: str,
    tokens: TokenSource,
    client: httpx.Client | None = None,
) -> list[Hit]:
    """One hybrid query, keyword plus vector, for the top 50 chunks, in the service's order.

    With `semantic`, the ranker reorders them and each hit's score is its reranker score;
    without, it is the fused hybrid score. A query the ranker did not rank raises, though the
    service answers it with 200: its results are S2's, and would be scored as S3's.
    """
    body = {
        "search": question,
        "vectorQueries": [{"kind": "vector", "vector": _compact(vector), "fields": "vector", "k": K}],
        "top": K,
        "select": "chunk_id,artefact",
    }
    if semantic:
        body |= {"queryType": "semantic", "semanticConfiguration": SEMANTIC_CONFIGURATION}

    with _client(client) as http:
        reply = _send(http, "POST", endpoint, f"indexes/{INDEX_NAME}/docs/search", body, tokens).json()

    partial = reply.get("@search.semanticPartialResponseReason")
    if semantic and partial is not None:
        raise SearchError(f"the semantic ranker did not rank this query: {partial}, returning "
                          f"{reply.get('@search.semanticPartialResponseType')}")
    score = "@search.rerankerScore" if semantic else "@search.score"
    return [Hit(int(document["chunk_id"]), document["artefact"], float(document[score]))
            for document in reply["value"]]
