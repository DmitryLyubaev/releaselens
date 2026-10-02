"""Scoring the arms, and the three pre-registered comparisons (spec §5).

Each arm's ranked chunks are collapsed to artefacts by first appearance, and scored against the
question's single target: top-1, reciprocal rank, lenient top-1 and the within-arm margin. The
comparisons C1 = E3 − E1, C2 = S3 − S1 and C3 = S3 − S2 pair top-1 by question, and are decided
by plan 3's bootstrap and rule at this benchmark's own threshold of five points. Everything else
is descriptive.

An errored question is not scored, never a miss: it leaves that arm's pairs, and the drops are
counted. A function of the results, the questions and the links alone: no I/O and no calls.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

from .. import analysis
from ..metrics import latency_percentiles
from .arms import K, NO_QUERY_VECTOR, ArmResult, Hit
from .questions import Question

THRESHOLD = 0.05
SEED = 20261002
COMPARISONS = (("E3", "E1"), ("S3", "S1"), ("S3", "S2"))
ARMS = ("E1", "E2", "E3", "S1", "S2", "S3")
LOCAL_ARMS = ("E1", "S1")
# More top-1 changes than this among the repeated questions is a caveat on the arm (spec §5.2).
DETERMINISM_LIMIT = 3

# Spec §8's rates, read on their date. E1 and S1 run locally and have no per-query price.
RATES = {
    "date": "2026-10-02",
    "source": "the Azure Retail Prices API, Australia East, in USD",
    "per_million_tokens": {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13},
    "ranker_per_request": 0.0,
    "ranker_plan": ("the free plan, whose monthly allowance of requests is free, and which refuses "
                    "requests beyond it rather than billing them"),
    "search_per_hour": 0.133,
    "search_tier": "Basic",
}
ARM_MODELS = {"E2": "text-embedding-3-small", "E3": "text-embedding-3-large",
              "S2": "text-embedding-3-small", "S3": "text-embedding-3-small"}


@dataclass(frozen=True)
class QuestionScore:
    top1: int
    rr: float
    lenient_top1: int
    margin: float | None


@dataclass(frozen=True)
class Comparison:
    """x − y in top-1 accuracy over the questions both arms scored.

    `dropped` is how many questions either arm answered that are not among the n, because one
    side errored or is missing. The mean and the bounds are settled to 12 places, as the rule
    reads them; `verdict` is "difference" or "inconclusive".
    """

    x: str
    y: str
    n: int
    dropped: int
    mean: float
    ci_low: float
    ci_high: float
    verdict: str


def collapse(hits: list[Hit]) -> list[tuple[str, float]]:
    """The artefacts in order of first appearance, each with the score of its first chunk."""
    seen: dict[str, float] = {}
    for hit in hits:
        seen.setdefault(hit.artefact, hit.score)
    return list(seen.items())


def score(result: ArmResult, target: str, links: Mapping[str, frozenset[str]]) -> QuestionScore | None:
    """One question on one arm (spec §5.1), or None when the arm errored on it.

    Only the first 50 chunks count, so a target beyond them, or a list with no hits, scores top-1
    0 and reciprocal rank 0. Lenient top-1 also accepts an artefact `links` ties to the target: a
    pull request's merge commit, or a commit's pull request. The margin is the first artefact's
    score less the next artefact's, in the arm's own units, so it is never compared across arms;
    it is None on a miss, and when no other artefact came back.
    """
    if result.error is not None:
        return None
    ranked = collapse(result.hits[:K])
    artefacts = [artefact for artefact, _ in ranked]
    first = artefacts[0] if artefacts else None
    top1 = int(first == target)
    return QuestionScore(
        top1=top1,
        rr=1 / (artefacts.index(target) + 1) if target in artefacts else 0.0,
        lenient_top1=int(top1 == 1 or (first is not None and first in links.get(target, frozenset()))),
        margin=ranked[0][1] - ranked[1][1] if top1 and len(ranked) > 1 else None,
    )


def compare_top1(scores: Mapping[str, Mapping[str, QuestionScore | None]], x: str, y: str) -> Comparison:
    """x − y in top-1, paired by question, decided by the rule at five points (spec §5.4).

    Keeps the qids both arms scored, in qid order, so the bootstrap draws the same resamples
    every time. Refuses a pair of arms with no question in common, which has no mean.
    """
    xs, ys = scores[x], scores[y]
    answered = sorted(set(xs) | set(ys))
    kept = [qid for qid in answered if xs.get(qid) is not None and ys.get(qid) is not None]
    if not kept:
        raise ValueError(f"no question was scored on both {x} and {y}")

    deltas = [float(xs[qid].top1 - ys[qid].top1) for qid in kept]
    mean = analysis.settled(math.fsum(deltas) / len(deltas))
    ci_low, ci_high = analysis.bootstrap_ci(deltas, seed=SEED)
    return Comparison(x, y, len(kept), len(answered) - len(kept), mean, ci_low, ci_high,
                      analysis.verdict(mean, (ci_low, ci_high), threshold=THRESHOLD))


def _top_artefact(result: ArmResult) -> str | None:
    ranked = collapse(result.hits[:K])
    return ranked[0][0] if ranked else None


def _repeat_pairs(first: Iterable[ArmResult], repeat: Iterable[ArmResult]) -> dict[str, tuple[int, int]]:
    """Per arm, how many repeated questions could be compared, and how many changed top-1."""
    before = {(result.arm, result.qid): result for result in first}
    counts: dict[str, tuple[int, int]] = {}
    for again in repeat:
        compared, changed = counts.get(again.arm, (0, 0))
        original = before.get((again.arm, again.qid))
        if original is None:
            raise ValueError(f"{again.arm} repeated {again.qid}, which its first pass did not answer")
        # An errored side has no top-1 to compare, so it is neither a change nor a match.
        if original.error is None and again.error is None:
            compared += 1
            changed += _top_artefact(original) != _top_artefact(again)
        counts[again.arm] = (compared, changed)
    return counts


def determinism(first: list[ArmResult], repeat: list[ArmResult]) -> dict[str, int]:
    """How many of each arm's repeated questions put a different artefact first (spec §5.2).

    A pair where either pass errored is left out, not counted as a change.
    """
    return {arm: changed for arm, (_, changed) in _repeat_pairs(first, repeat).items()}


def cost_per_1000(arm: str, results: list[ArmResult], rates: Mapping = RATES) -> float:
    """What 1,000 of this arm's queries cost at `rates`, from its measured query tokens (spec §5.7).

    Over every row of the arm, errored ones included, since an embedding billed before a failure
    was still paid for. S2 and S3 carry E2's tokens for the question they search with. S3 adds
    the ranker's price for each search it sent. E1 and S1 are local, and cost nothing per query.
    AI Search's hourly charge is fixed whatever the queries, so it is not in this figure.
    """
    own = [result for result in results if result.arm == arm]
    if not own:
        raise ValueError(f"there are no {arm} results to price")
    model = ARM_MODELS.get(arm)
    total = 0.0
    if model is not None:
        total += sum(result.query_tokens for result in own) * rates["per_million_tokens"][model] / 1_000_000
    if arm == "S3":
        total += sum(1 for result in own if result.error != NO_QUERY_VECTOR) * rates["ranker_per_request"]
    return 1000 * total / len(own)


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _figures(scored: list[QuestionScore]) -> dict:
    return {
        "n": len(scored),
        "top1": _mean([s.top1 for s in scored]),
        "mrr": _mean([s.rr for s in scored]),
        "lenient_top1": _mean([s.lenient_top1 for s in scored]),
    }


def analyse(questions: list[Question], results: list[ArmResult], repeat: list[ArmResult],
            links: Mapping[str, frozenset[str]], rates: Mapping = RATES) -> dict:
    """Everything the write-up reports, as plain JSON values, from one run's rows.

    Every arm must have answered every question exactly once, an error being an answer; anything
    else is a run that did not finish, and is refused. `repeat` is each arm's second pass over
    the first questions. The figures per arm and per type are over the questions the arm scored;
    latency is over its rows without errors, so a failure's time is not an answer's.
    """
    targets = {question.qid: question.target for question in questions}
    by_arm: dict[str, list[ArmResult]] = {arm: [] for arm in ARMS}
    for result in results:
        by_arm.setdefault(result.arm, []).append(result)
    for arm, rows in by_arm.items():
        qids = [row.qid for row in rows]
        if arm not in ARMS or len(qids) != len(set(qids)) or set(qids) != set(targets):
            raise ValueError(f"{arm} answered {len(set(qids))} distinct of the {len(targets)} questions, "
                             f"in {len(qids)} rows; a run is analysed only when every arm answered each once")

    scores = {arm: {row.qid: score(row, targets[row.qid], links) for row in rows} for arm, rows in by_arm.items()}

    arms = {}
    for arm, rows in by_arm.items():
        scored = [s for s in scores[arm].values() if s is not None]
        margins = [s.margin for s in scored if s.margin is not None]
        answered = [row.ms for row in rows if row.error is None]
        latency = latency_percentiles(answered) if answered else None
        arms[arm] = _figures(scored) | {
            "errors": len(rows) - len(scored),
            "median_margin": statistics.median(margins) if margins else None,
            "margin_n": len(margins),
            "p50_ms": latency["p50"] if latency else None,
            "p95_ms": latency["p95"] if latency else None,
            "query_tokens": sum(row.query_tokens for row in rows),
            "cost_per_1000": cost_per_1000(arm, rows, rates),
        }

    exploratory = []
    for index, y in enumerate(ARMS):
        for x in ARMS[index + 1:]:
            if (x, y) in COMPARISONS:
                continue
            kept = [qid for qid in sorted(targets)
                    if scores[x][qid] is not None and scores[y][qid] is not None]
            difference = _mean([scores[x][qid].top1 - scores[y][qid].top1 for qid in kept])
            exploratory.append({"x": x, "y": y, "n": len(kept),
                                "difference": None if difference is None else analysis.settled(difference)})

    types = {question.qid: question.entity_type for question in questions}
    by_type = {
        entity_type: {"questions": sum(1 for t in types.values() if t == entity_type)} | {
            arm: _figures([s for qid, s in scores[arm].items() if types[qid] == entity_type and s is not None])
            for arm in ARMS
        }
        for entity_type in sorted(set(types.values()))
    }

    pairs = _repeat_pairs(results, repeat)
    return {
        "questions": len(questions),
        "threshold": THRESHOLD,
        "seed": SEED,
        "resamples": analysis.BOOTSTRAP_RESAMPLES,
        "comparisons": [asdict(compare_top1(scores, x, y)) for x, y in COMPARISONS],
        "arms": arms,
        "by_type": by_type,
        "exploratory": exploratory,
        "determinism": {
            arm: {"compared": compared, "changed": changed, "caveat": changed > DETERMINISM_LIMIT}
            for arm, (compared, changed) in ((arm, pairs.get(arm, (0, 0))) for arm in ARMS)
        },
        "rates": dict(rates),
    }
