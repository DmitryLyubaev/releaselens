"""Bounded Search data-plane operations. Never retries a document or query."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np
import orjson

from .bundle import read_json, validate_inputs
from .contracts import DeliveryRefused, SearchTarget, VerifiedBundle, canonical, exclusive_json
from ..retrieval.arms import Hit
from ..retrieval.corpus import load_chunks

API_VERSION = "2024-07-01"
UPLOAD_CHECKS = frozenset({'upload-deadline', 'upload-requests', 'upload-size',
    'upload-response', 'upload-incomplete', 'upload-unclassified'})


class UploadRefused(DeliveryRefused):
    """An allowlisted diagnostic, never a remote response or corpus fragment."""
    def __init__(self, check):
        self.check = check if check in UPLOAD_CHECKS else 'upload-unclassified'
        super().__init__('upload')


def encode_document(document):
    vector = document.get("vector")
    if not isinstance(vector, np.ndarray):
        return canonical(document)
    if vector.dtype != np.dtype("float32") or vector.ndim != 1 or not np.isfinite(vector).all():
        raise UploadRefused("upload-response")
    # memmap is an ndarray subclass unsupported by orjson. This view is zero-copy;
    # native float32 encoding avoids Python floats and retains the frozen bits.
    return orjson.dumps(dict(document, vector=np.asarray(vector)),
                        option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_SORT_KEYS)


def document_batches(documents):
    """Encode each document once, including UTF-8 and envelope bytes in the cap."""
    prefix, suffix = b'{"value":[', b']}'
    encoded, keys, size = [], [], len(prefix) + len(suffix)
    limit = 12 * 1024 * 1024
    for document in documents:
        item = encode_document(document)
        if len(item) + len(prefix) + len(suffix) > limit:
            raise UploadRefused("upload-size")
        if encoded and (len(encoded) == 500 or size + 1 + len(item) > limit):
            yield keys, prefix + b','.join(encoded) + suffix
            encoded, keys, size = [], [], len(prefix) + len(suffix)
        size += len(item) + bool(encoded)
        encoded.append(item)
        keys.append(document["chunk_id"])
    if encoded:
        yield keys, prefix + b','.join(encoded) + suffix


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
    metrics = dict(kind="upload-metrics", bytes_started=0, http_seconds=0.0,
                   encoding_seconds=0.0, outcome="failed", check="upload-unclassified")

    def submit(keys_expected, payload):
        nonlocal requests, sent
        if time.monotonic() - started >= 240:
            raise UploadRefused("upload-deadline")
        if requests >= 200:
            raise UploadRefused("upload-requests")
        if len(payload) > 12 * 1024 * 1024:
            raise UploadRefused("upload-size")
        requests += 1
        metrics["bytes_started"] += len(payload)
        http_started = time.monotonic()
        try:
            rows = send(target, tokens, client, "POST", "docs/index", payload).json()["value"]
            if time.monotonic() - started >= 240:
                raise UploadRefused("upload-deadline")
            keys = [row["key"] for row in rows]
            if (len(keys) != len(keys_expected) or len(set(keys)) != len(keys)
                or set(keys) != set(keys_expected)
                or any(row["status"] is not True or row["statusCode"] not in (200, 201) for row in rows)):
                raise UploadRefused("upload-response")
        except (KeyError, ValueError, TypeError):
            raise UploadRefused("upload-response") from None
        finally:
            metrics["http_seconds"] += time.monotonic() - http_started
        sent += len(keys_expected)

    def documents():
        for row, chunk_id in enumerate(ids):
            chunk = chunks[chunk_id]
            yield {"@search.action": "upload", "chunk_id": str(chunk_id), "artefact": chunk.artefact,
                   "content": chunk.content, "vector": vectors[row]}

    # Encode the next bounded batch while the sole HTTP worker waits. No second
    # request starts until the previous response passed every check. On failure,
    # the prepared next batch is discarded and the reservation forbids replay.
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = None
            batches = iter(document_batches(documents()))
            while True:
                encoding_started = time.monotonic()
                try:
                    keys, payload = next(batches)
                except StopIteration:
                    break
                finally:
                    metrics["encoding_seconds"] += time.monotonic() - encoding_started
                if pending is not None:
                    pending.result()
                pending = worker.submit(submit, keys, payload)
            if pending is not None:
                pending.result()
        if sent != 41825:
            raise UploadRefused("upload-incomplete")
        metrics.update(outcome="uploaded", check=None)
    except UploadRefused as exc:
        metrics["check"] = exc.check
        raise
    finally:
        metrics.update(documents_confirmed=sent, requests_started=requests,
                       elapsed_seconds=time.monotonic() - started)
        for key in ("elapsed_seconds", "http_seconds", "encoding_seconds"):
            metrics[key] = round(metrics[key], 3)
        # Diagnostic failure cannot replace the original upload refusal. The
        # create-only sibling also cannot overwrite evidence on a reserved replay.
        with suppress(OSError, DeliveryRefused):
            exclusive_json(reservation.with_suffix(".metrics.json"), metrics)
    wait_for_count(target, tokens, client)
    return sent
