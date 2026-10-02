"""Deterministic metrics. No model calls here — the LLM-judge lives in judge.py."""

from __future__ import annotations

import math
import re

_DECLINE_MARKERS = (
    "does not contain",
    "do not contain",
    "not in the evidence",
    "no evidence",
    "cannot be determined",
    "cannot determine",
    "is not available",
    "not answerable",
    "i don't have",
    "i do not have",
)

_ASSERTION_MARKERS = re.compile(r"\b\d+(\.\d+)?\s*(percent|%)|\bis\s+\d+")


def citation_recall(expected: list[str], actual: list[str]) -> float:
    """Fraction of the expected artefacts that were actually cited.

    A query with no expectation (unanswerable) is vacuously satisfied — the
    unanswerable_correct check is what scores those.
    """
    if not expected:
        return 1.0
    found = sum(1 for e in expected if e in actual)
    return found / len(expected)


def citation_precision(expected: list[str], actual: list[str]) -> float:
    """Fraction of the citations produced that were expected.

    Low precision means the answer is padded with loosely related evidence, which
    reads as thorough and is not.
    """
    if not actual:
        return 1.0 if not expected else 0.0
    if not expected:
        return 1.0
    hits = sum(1 for a in actual if a in expected)
    return hits / len(actual)


def retrieval_recall_at_k(expected: list[str], retrieved: list[str]) -> float:
    """Whether the retriever surfaced the expected artefacts at all, before synthesis.

    Separating this from citation recall says whether a miss was retrieval's fault
    or the model's.
    """
    return citation_recall(expected, retrieved)


def latency_percentiles(latencies_ms: list[float]) -> dict[str, float]:
    """Nearest-rank p50/p95 over the observed latencies.

    Both percentiles use the same nearest-rank method: ceil(fraction * n) - 1,
    clamped into range. An earlier draft used statistics.quantiles for p50 on
    lists longer than two items, which does not agree with nearest-rank p95 and
    gives 550 (not 500) on the ten-item fixture below — that mismatch is why this
    uses one consistent method for both instead.
    """
    if not latencies_ms:
        return {"p50": 0, "p95": 0}
    ordered = sorted(latencies_ms)
    n = len(ordered)
    return {
        "p50": ordered[min(n - 1, max(0, math.ceil(n * 0.50) - 1))],
        "p95": ordered[min(n - 1, max(0, math.ceil(n * 0.95) - 1))],
    }


def unanswerable_correct(answer: str) -> bool:
    """True when the answer declines rather than inventing something.

    This is the metric that matters most. A system scoring well everywhere else and
    failing here is confidently wrong, which is worse than being unable to answer.
    """
    lowered = answer.lower()
    declined = any(marker in lowered for marker in _DECLINE_MARKERS)
    asserted = bool(_ASSERTION_MARKERS.search(lowered))
    return declined and not asserted
