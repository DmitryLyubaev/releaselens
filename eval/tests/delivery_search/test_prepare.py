import hashlib
import json

import numpy as np
import pytest

from app.delivery_search.prepare import prepare_query_vectors
from app.delivery_search.contracts import DeliveryRefused


def authority(root):
    p = root / "authority.json"
    p.write_text(json.dumps({"operation": "prepare-query-vectors", "approved": True,
        "approval_ref": "synthetic-owner-receipt", "question_set_sha256": hashlib.sha256(
            (root / "questions.jsonl").read_bytes()).hexdigest(), "model": "text-embedding-3-small",
        "version": "1", "dimensions": 1536, "max_questions": 229,
        "deployment_verified": True}))
    return p


def test_prepare_reservation(prepare_inputs):
    calls = []
    def embed(text):
        calls.append(text)
        raise RuntimeError("private remote error")
    receipt = authority(prepare_inputs)
    for _ in range(2):
        with pytest.raises(DeliveryRefused): prepare_query_vectors(prepare_inputs, receipt, embed)
    assert len(calls) == 1
    assert (prepare_inputs / "prepare.intent.json").exists()


def test_prepare_exact_batch_and_cached_reuse(prepare_inputs):
    calls = []
    def embed(text):
        calls.append(text)
        return np.ones(1536, dtype=np.float32)
    receipt = authority(prepare_inputs)
    path = prepare_query_vectors(prepare_inputs, receipt, embed)
    assert len(calls) == 229 and np.load(path).shape == (229, 1536)
    prepare_query_vectors(prepare_inputs, receipt, lambda text: pytest.fail("cached run must not embed"))
    assert len((prepare_inputs / "prepare.journal.jsonl").read_text().splitlines()) == 458


def test_unapproved_preparation_no_calls(prepare_inputs):
    receipt = authority(prepare_inputs)
    value = json.loads(receipt.read_text()); value["approved"] = False
    receipt.write_text(json.dumps(value))
    with pytest.raises(DeliveryRefused):
        prepare_query_vectors(prepare_inputs, receipt, lambda _: pytest.fail("unapproved call"))
