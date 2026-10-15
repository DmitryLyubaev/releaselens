"""Bounded Search data-plane operations. Never retries a document or query."""
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np

from .bundle import read_json, validate_inputs
from .contracts import DeliveryRefused, SearchTarget, VerifiedBundle, canonical, exclusive_json
from ..retrieval.arms import Hit
from ..retrieval.corpus import load_chunks
from ..retrieval.search_index import _compact

API_VERSION = "2024-07-01"


def send(target: SearchTarget, tokens, client: httpx.Client, method: str, operation: str,
         body: bytes | None = None) -> httpx.Response:
    # SearchTarget is immutable and validates both exact authority hostname and logical index.
    try:
        response = client.request(method, f"{target.endpoint.rstrip('/')}/indexes/{target.index}/{operation}",
            params={"api-version": API_VERSION}, headers={"Authorization": f"Bearer {tokens.token()}",
            "Content-Type": "application/json"}, content=body, follow_redirects=False, timeout=20)
        if response.status_code not in (200, 201, 207):
            raise DeliveryRefused("request")
        return response
    except httpx.HTTPError:
        raise DeliveryRefused("request") from None


def search_once(target: SearchTarget, question: str, vector: np.ndarray, config: dict,
                tokens, client: httpx.Client) -> list[Hit]:
    body = {"search": question, "vectorQueries": [{"kind": "vector", "vector": vector.tolist(),
            "fields": "vector", "k": 50}], "top": 50, "select": "chunk_id,artefact"}
    if config["filter"] is not None:
        body["filter"] = config["filter"]
    try:
        rows = send(target, tokens, client, "POST", "docs/search", canonical(body)).json()["value"]
        if not isinstance(rows, list) or len(rows) > 50:
            raise DeliveryRefused("request")
        hits = []
        for row in rows:
            hit = Hit(int(row["chunk_id"]), row["artefact"], float(row["@search.score"]))
            if not isinstance(hit.artefact, str) or not math.isfinite(hit.score):
                raise DeliveryRefused("request")
            hits.append(hit)
        return hits
    except (KeyError, ValueError, TypeError, OverflowError):
        raise DeliveryRefused("request") from None


def wait_for_count(target: SearchTarget, tokens, client: httpx.Client, *, sleep=None) -> int:
    sleep = time.sleep if sleep is None else sleep
    started = time.monotonic()
    for attempt in range(24):
        try:
            count = int(send(target, tokens, client, "GET", "docs/$count").text.lstrip("\ufeff").strip())
        except ValueError:
            raise DeliveryRefused("count") from None
        if count == 41825:
            return count
        if count > 41825 or time.monotonic() - started >= 115:
            break
        if attempt < 23:
            sleep(5)
    raise DeliveryRefused("count")


def upload_bundle(bundle: VerifiedBundle, target: SearchTarget, tokens, client: httpx.Client,
                  *, reservation: Path) -> int:
    validate_inputs(bundle.root)
    exclusive_json(reservation, {"bundle": bundle.digest, "index": target.index, "host": target.allowed_host})
    chunks = {c.chunk_id: c for c in load_chunks(bundle.root / "chunks.jsonl")}
    ids = read_json(bundle.root / "corpus.ids.json")
    vectors = np.load(bundle.root / "corpus.npy", allow_pickle=False, mmap_mode="r")
    started, requests, sent = time.monotonic(), 0, 0

    def submit(documents):
        nonlocal requests, sent
        if requests >= 200 or time.monotonic() - started >= 240:
            raise DeliveryRefused("upload")
        payload = canonical({"value": documents})
        if len(payload) > 12 * 1024 * 1024:
            raise DeliveryRefused("upload")
        requests += 1
        try:
            rows = send(target, tokens, client, "POST", "docs/index", payload).json()["value"]
            keys = [row["key"] for row in rows]
            if (len(keys) != len(documents) or len(set(keys)) != len(keys)
                or set(keys) != {d["chunk_id"] for d in documents}
                or any(row["status"] is not True or row["statusCode"] not in (200, 201) for row in rows)):
                raise DeliveryRefused("upload")
        except (KeyError, ValueError, TypeError):
            raise DeliveryRefused("upload") from None
        sent += len(documents)

    batch, size = [], len(b'{"value":[]}')
    for row, chunk_id in enumerate(ids):
        chunk = chunks[chunk_id]
        document = {"@search.action": "upload", "chunk_id": str(chunk_id), "artefact": chunk.artefact,
                    "content": chunk.content, "vector": _compact(vectors[row])}
        item_size = len(canonical(document)) + 1
        if item_size + 12 > 12 * 1024 * 1024:
            raise DeliveryRefused("upload")
        if batch and (len(batch) == 500 or size + item_size > 12 * 1024 * 1024):
            submit(batch)
            batch, size = [], len(b'{"value":[]}')
        batch.append(document)
        size += item_size
    if batch:
        submit(batch)
    if sent != 41825:
        raise DeliveryRefused("upload")
    wait_for_count(target, tokens, client)
    return sent
