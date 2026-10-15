"""Synthetic complete frozen inputs; no original corpus or Azure access."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest


def put_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def questions_at(root):
    rows = [dict(qid=f"q{i:03}", question=f"Find synthetic change number {i}",
                 target=f"a{i}", entity_type="commit") for i in range(229)]
    data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (root / "questions.jsonl").write_bytes(data)
    put_json(root / "questions.manifest.json", {"sha256": hashlib.sha256(data).hexdigest()})
    return rows


@pytest.fixture(scope="module")
def complete_inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("frozen")
    rows = questions_at(root)
    with (root / "chunks.jsonl").open("w", encoding="utf-8") as out:
        for i in range(41825):
            out.write(json.dumps(dict(chunkId=i, artefact=f"a{i % 229}", entityType="commit",
                entityKey=str(i), chunkIndex=0, tokenCount=5, content="Synthetic document")) + "\n")
    (root / "links.jsonl").write_text("", encoding="utf-8")
    vectors = np.lib.format.open_memmap(root / "corpus.npy", mode="w+", dtype="float32",
                                       shape=(41825, 1536))
    vectors[:, 0] = 1
    vectors.flush()
    del vectors
    put_json(root / "corpus.ids.json", list(range(41825)))
    query = np.zeros((229, 1536), dtype="float32")
    query[:, 0] = 1
    np.save(root / "queries.npy", query)
    put_json(root / "queries.json", {"model": "text-embedding-3-small", "version": "1",
        "dimensions": 1536, "questions": [{"qid": r["qid"],
        "question_sha256": hashlib.sha256(r["question"].encode()).hexdigest()} for r in rows]})
    from app.retrieval.search_index import SCHEMA
    put_json(root / "schema.json", SCHEMA)
    put_json(root / "s2.json", {"top": 50, "k": 50, "filter": None})
    put_json(root / "negative.json", {"top": 50, "k": 50, "filter": "artefact eq '__p8_no_match__'"})
    for filename in ["scorer.py", "requirements.lock", "provider.lock.hcl"]:
        (root / filename).write_text("synthetic source lock fixture\n", encoding="utf-8")
    return root


@pytest.fixture
def prepare_inputs(tmp_path):
    questions_at(tmp_path)
    return tmp_path
