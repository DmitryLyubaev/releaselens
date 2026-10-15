"""Owner-only single batch reservation. Delivery never invokes this module."""
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import numpy as np

from .bundle import validate_queries
from .contracts import DeliveryRefused, canonical, exclusive_json, sha256_file
from ..retrieval.questions import load_frozen


def prepare_query_vectors(inputs: Path, authority: Path, embed: Callable[[str], np.ndarray]) -> Path:
    output = inputs / "queries.npy"
    if output.exists() and (inputs / "queries.json").exists():
        try:
            validate_queries(inputs)
            return output
        except (ValueError, OSError, KeyError, TypeError):
            raise DeliveryRefused("inputs") from None
    try:
        approval = json.loads(authority.read_text(encoding="utf-8"))
        if (approval.get("operation"), approval.get("approved"), approval.get("model"),
            approval.get("version"), approval.get("dimensions"), approval.get("max_questions"),
            approval.get("deployment_verified")) != (
                "prepare-query-vectors", True, "text-embedding-3-small", "1", 1536, 229, True):
            raise DeliveryRefused("authority")
        if not approval.get("approval_ref") or approval.get("question_set_sha256") != sha256_file(inputs / "questions.jsonl"):
            raise DeliveryRefused("authority")
        exclusive_json(inputs / "prepare.intent.json", approval)
        questions = load_frozen(inputs / "questions.jsonl")
        if len(questions) != 229 or len({q.qid for q in questions}) != 229:
            raise DeliveryRefused("inputs")
        vectors, records = [], []
        with (inputs / "prepare.journal.jsonl").open("xb") as journal:
            def append(value):
                journal.write(canonical(value) + b"\n")
                journal.flush()
                os.fsync(journal.fileno())
            for q in questions:
                record = {"qid": q.qid, "question_sha256": hashlib.sha256(q.question.encode()).hexdigest()}
                append(record | {"status": "reserved"})
                vector = np.asarray(embed(q.question), dtype=np.float32)
                if vector.shape != (1536,) or not np.isfinite(vector).all() or not vector.any():
                    raise DeliveryRefused("inputs")
                append(record | {"status": "returned", "vector": vector.tolist()})
                records.append(record)
                vectors.append(vector)
        with output.open("xb") as file:
            np.save(file, np.asarray(vectors, dtype=np.float32), allow_pickle=False)
        exclusive_json(inputs / "queries.json", {"model": "text-embedding-3-small", "version": "1",
                                                "dimensions": 1536, "questions": records})
        validate_queries(inputs)
        return output
    except DeliveryRefused:
        raise
    except Exception:
        # The original intent/journal remains. Never expose a credential-bearing client error.
        raise DeliveryRefused("prepare") from None
