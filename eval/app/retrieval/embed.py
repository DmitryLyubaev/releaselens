"""Embedding the corpus, and a question, with an Azure OpenAI deployment. Keyless.

The corpus run is resumable. Each batch's vectors are written into a memory-mapped `.npy`
and flushed, and only then is the batch recorded as done. A run that fails partway (a 429
past its retries, a dropped connection) is started again with the same `out_dir`, and it sends
only the batches not recorded, so no batch already saved is paid for twice. A crash between
the flush and the record costs that one batch again; nothing else does.

This does not choose the deployment or price the run: the caller names the deployment, and
reads what it cost from `tokens_billed`.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from .azure_auth import TokenSource
from .corpus import Chunk

MAX_RETRIES = 5
_RETRIED_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS = 60.0
_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class EmbeddingRun:
    """The saved vectors of one deployment's corpus run, and what they cost.

    `tokens_billed` and `batches` cover every batch saved in `out_dir`, across the calls it took
    to finish, so a resumed run reports what the whole corpus cost, each batch counted once.
    """

    deployment: str
    vectors_path: Path
    ids_path: Path
    tokens_billed: int
    batches: int


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, _MAX_BACKOFF_SECONDS)


def _post_embeddings(
    client: httpx.Client,
    *,
    base_url: str,
    deployment: str,
    inputs: list[str],
    tokens: TokenSource,
    sleep: Callable[[float], None],
) -> tuple[np.ndarray, int]:
    """One embeddings call, retried at most `MAX_RETRIES` times; the vectors and prompt tokens.

    A throttle or a server error waits as long as `Retry-After` says, or backs off without it;
    a dropped connection or a timeout backs off. Anything else, or the last failure, raises.
    """
    for attempt in range(MAX_RETRIES + 1):
        final = attempt == MAX_RETRIES
        try:
            response = client.post(
                f"{base_url}embeddings",
                json={"model": deployment, "input": inputs},
                headers={"Authorization": f"Bearer {tokens.token()}"},
            )
        except httpx.TransportError:
            if final:
                raise
            sleep(_backoff(attempt))
            continue

        if response.status_code in _RETRIED_STATUSES and not final:
            wait = _retry_after(response)
            sleep(_backoff(attempt) if wait is None else wait)
            continue

        response.raise_for_status()
        return _parse(response.json(), len(inputs))

    raise AssertionError("unreachable: the final attempt returns or raises")


def _parse(body: dict, expected: int) -> tuple[np.ndarray, int]:
    data = sorted(body["data"], key=lambda item: item["index"])
    if [item["index"] for item in data] != list(range(expected)):
        raise ValueError(f"expected {expected} embeddings indexed 0..{expected - 1}, got {len(data)}")
    vectors = np.asarray([item["embedding"] for item in data], dtype=np.float32)
    return vectors, int(body["usage"]["prompt_tokens"])


def _write_json(path: Path, value) -> None:
    # Written whole and then swapped in, so a crash never leaves half a progress file.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def _resume(progress_path: Path, ids_path: Path, vectors_path: Path, *, deployment: str,
            ids: list[int], batch_size: int) -> dict | None:
    """The saved progress, or None when there is none. Refuses progress made for another run.

    A run is only resumed into the same deployment, chunk order and batch size, because a batch
    number means nothing otherwise; the files are never overwritten to start again.
    """
    if not progress_path.exists():
        return None
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    saved_ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.exists() else None
    if progress["deployment"] != deployment or progress["batch_size"] != batch_size or saved_ids != ids:
        raise ValueError(
            f"{progress_path} was saved for another deployment, chunk order or batch size; "
            "move the deployment's files out of the way to embed afresh"
        )
    if progress["done"] and not vectors_path.exists():
        raise ValueError(f"{progress_path} records saved batches, but {vectors_path} is missing")
    return progress


def embed_corpus(
    chunks: list[Chunk],
    *,
    base_url: str,
    deployment: str,
    tokens: TokenSource,
    out_dir: Path,
    batch_size: int = 64,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> EmbeddingRun:
    """Embed every chunk's content, saving one float32 row per chunk in `chunks` order.

    Saves `{deployment}.npy`, `{deployment}.ids.json` (the chunk ids, row by row) and
    `{deployment}.progress.json` (the batches done, and each one's prompt tokens) in `out_dir`.
    Called again with the same `out_dir`, it resumes from the first batch not saved.
    """
    if not chunks:
        raise ValueError("there are no chunks to embed")

    out_dir.mkdir(parents=True, exist_ok=True)
    vectors_path = out_dir / f"{deployment}.npy"
    ids_path = out_dir / f"{deployment}.ids.json"
    progress_path = out_dir / f"{deployment}.progress.json"
    ids = [chunk.chunk_id for chunk in chunks]

    progress = _resume(progress_path, ids_path, vectors_path, deployment=deployment, ids=ids,
                       batch_size=batch_size)
    if progress is None:
        _write_json(ids_path, ids)
        progress = {"deployment": deployment, "batch_size": batch_size, "chunks": len(chunks),
                    "dimensions": None, "done": {}}
        _write_json(progress_path, progress)

    done: dict[str, int] = progress["done"]
    batch_count = math.ceil(len(chunks) / batch_size)
    owned = client is None
    client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)
    vectors = None
    try:
        for batch in range(batch_count):
            if str(batch) in done:
                continue
            start = batch * batch_size
            members = chunks[start:start + batch_size]
            rows, used = _post_embeddings(
                client, base_url=base_url, deployment=deployment,
                inputs=[chunk.content for chunk in members], tokens=tokens, sleep=sleep,
            )

            if vectors is None:
                if done:
                    vectors = np.lib.format.open_memmap(vectors_path, mode="r+")
                else:
                    vectors = np.lib.format.open_memmap(
                        vectors_path, mode="w+", dtype=np.float32, shape=(len(chunks), rows.shape[1]))
                    progress["dimensions"] = rows.shape[1]
            if vectors.shape != (len(chunks), rows.shape[1]):
                raise ValueError(f"{vectors_path} holds {vectors.shape}, but this batch has "
                                 f"{rows.shape[1]} dimensions for {len(chunks)} chunks")

            vectors[start:start + len(members)] = rows
            vectors.flush()
            done[str(batch)] = used
            _write_json(progress_path, progress)
    finally:
        # Drops the mapping, so the file can be opened again, on Windows too, even while a
        # caller still holds the traceback of a failure.
        del vectors
        if owned:
            client.close()

    return EmbeddingRun(deployment, vectors_path, ids_path, sum(done.values()), len(done))


def embed_query(
    text: str,
    *,
    base_url: str,
    deployment: str,
    tokens: TokenSource,
    client: httpx.Client | None = None,
) -> tuple[np.ndarray, int]:
    """One text's float32 vector, and the prompt tokens it was billed. Retried as a batch is."""
    owned = client is None
    client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)
    try:
        rows, used = _post_embeddings(client, base_url=base_url, deployment=deployment,
                                      inputs=[text], tokens=tokens, sleep=time.sleep)
    finally:
        if owned:
            client.close()
    return rows[0], used
