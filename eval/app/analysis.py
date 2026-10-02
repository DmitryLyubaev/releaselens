"""The study's statistics: paired per-query deltas, a bootstrap interval and the decision rule.

Pre-registered in spec §8 before any data existed, and implemented as written. The unit of
analysis is the query. Each arm's passes of a query are averaged first, the arms are paired by
query, and the 95% interval resamples queries with their passes kept together. Each metric is
scored only over the queries it applies to.

A function of the outcomes and the golden set alone: no model calls and no I/O.
"""

from __future__ import annotations

import math
import random
from fractions import Fraction
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .metrics import latency_percentiles

# models.py builds RunReport out of the two models below, so importing it here at runtime would
# be circular. Nothing here needs its classes at runtime, only their names for the annotations.
if TYPE_CHECKING:
    from .models import Arm, GoldenQuery, QueryOutcome

QUALITY_METRICS = ("groundedness", "citation_recall", "citation_precision", "must_contain")
DIFFERENCE_THRESHOLD = 0.10
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260924

# The 2.5th and 97.5th percentiles, as exact fractions so the rank is not left to float rounding.
_CI_LOWER = Fraction(1, 40)
_CI_UPPER = Fraction(39, 40)

_SETTLED_DECIMALS = 12


class Comparison(BaseModel):
    """Arm x against arm y on one quality metric, as the decision rule reads it.

    k and pairs are what the comparison is really over, and nominal_k and nominal_pairs what it
    would be over had every pass of every query in scope been paired. A comparison short of its
    nominal size has a weaker interval than the study planned for: at k of 3 or fewer, the 95%
    interval is in effect the range of the per-query deltas.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)

    metric: str
    x: str
    y: str

    k: int
    """Queries with at least one pass where both arms have a value for the metric."""

    pairs: int
    """The (query, pass) pairs the deltas are over. Its shortfall against nominal_pairs is how
    many pairs dropped out, because one side errored, was not scored, or is missing."""

    nominal_k: int
    """The run's queries in this metric's scope: every query for groundedness, the answerable
    ones for the citation metrics, and those with a `must_contain` for must_contain."""

    nominal_pairs: int
    """nominal_k × passes."""

    passes: int
    """The passes each arm answered each query, as `k queries × passes` is worded."""

    mean_delta: float | None
    """The mean over queries of each query's mean x − y. None when k is 0."""

    ci_low: float | None
    """The 95% bootstrap interval around mean_delta, from bootstrap_ci. None when k is 0."""

    ci_high: float | None
    """The interval's upper bound. None when k is 0."""

    verdict: str
    """"difference", "inconclusive" or "no data" (k is 0)."""

    per_query_delta: dict[str, float]
    """Each query's mean x − y over its kept pairs, published beside the mean (spec §8)."""


class ArmSummary(BaseModel):
    """One arm's figures, reported descriptively and carrying no significance claim.

    Each `mean_<metric>` is over the arm's outcomes without errors, on the queries the metric
    applies to, where the outcome has a value for it, and `<metric>_count` is how many outcomes
    that is. The mean is None when its count is 0. The count is there because a judgement that
    cannot be parsed scores None, not zero, which leaves the mean over the survivors: one run
    reported groundedness 1.000 from two scored queries out of five, which reads as a perfect
    score and is not one.

    The difference between two arms' means is not the comparison's delta, which is over only
    the pairs where both arms have a value.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)

    arm: str

    outcome_count: int
    """Every outcome of the arm: each query on each pass, errors included."""

    error_count: int
    filtered_count: int
    """Outcomes on which a content filter blocked the request, errors included."""

    mean_groundedness: float | None
    groundedness_count: int
    judge_failure_count: int
    """Outcomes without errors that were put to the judge and came back with no score: the judge
    raised, its reply could not be parsed, or its score was not a number in [0, 1]. Each is left
    out of groundedness_count, and its pairs out of the groundedness comparisons."""

    mean_citation_recall: float | None
    citation_recall_count: int
    mean_citation_precision: float | None
    citation_precision_count: int
    mean_must_contain: float | None
    """The `must_contain` pass rate."""
    must_contain_count: int

    unanswerable_handled: int
    """How many of the unanswerable_total outcomes declined to answer, as they should."""

    unanswerable_total: int
    """Outcomes without errors on unanswerable queries. Reported only, since two queries cannot
    support a conclusion (spec §8)."""

    p50_latency_ms: float | None
    """From latency_percentiles, over the outcomes without errors, whose latency is the time to
    an answer and not the time to a failure. None when there is none."""

    p95_latency_ms: float | None
    """Over the same outcomes as p50_latency_ms."""

    mean_cost_usd_per_query: float | None
    """What answering cost, averaged over the outcomes without errors. None when there is none."""

    total_cost_usd: float
    """What answering cost, summed over ALL the arm's outcomes. A rejected reply was billed
    whether or not it counts, so it is money spent."""

    models_seen: list[str]
    """Each `model` the outcomes without errors name, sorted. A rejected reply's model belongs to
    the provider that answered instead, which its error already names."""


def metric_value(outcome: QueryOutcome, query: GoldenQuery, metric: str) -> float | None:
    """This outcome's score on one quality metric, or None where it has none to give.

    None means the metric does not apply to the query, or the outcome has no value for it.
    Neither is a zero, and neither is a pass. An unanswerable query has no right citation to
    recall, and the runner's vacuous 1.0 for citing nothing would inflate both citation metrics;
    a query with no `must_contain` has nothing to pass. An outcome with an error has no answer to
    score, so it is None on every metric, and never a quality loss charged to its arm.
    """
    if metric not in QUALITY_METRICS:
        raise ValueError(f"unknown quality metric {metric!r}")

    # Never truthiness: an httpx timeout once recorded an error of "".
    if outcome.error is not None or not _in_scope(query, metric):
        return None

    if metric == "groundedness":
        return outcome.groundedness
    if metric == "must_contain":
        return 1.0 if outcome.must_contain_satisfied else 0.0
    return getattr(outcome, metric)


def _in_scope(query: GoldenQuery, metric: str) -> bool:
    """Whether the metric applies to the query at all (spec §8): groundedness to every query,
    the citation metrics to the answerable ones, and `must_contain` to those with something to
    contain."""
    if metric in ("citation_recall", "citation_precision"):
        return query.category != "unanswerable"
    if metric == "must_contain":
        return bool(query.must_contain)
    return True


def _kept_differences(
    outcomes: list[QueryOutcome], queries: dict[str, GoldenQuery], x: str, y: str, metric: str,
) -> dict[str, list[float]]:
    """Each query's x − y on every pass where both arms have a value, in run order."""
    y_by_pass = {(o.id, o.pass_index): o for o in outcomes if o.arm == y}

    kept: dict[str, list[float]] = {}
    for x_outcome in outcomes:
        if x_outcome.arm != x:
            continue
        y_outcome = y_by_pass.get((x_outcome.id, x_outcome.pass_index))
        if y_outcome is None:
            continue

        query = queries[x_outcome.id]
        x_value = metric_value(x_outcome, query, metric)
        y_value = metric_value(y_outcome, query, metric)
        if x_value is None or y_value is None:
            continue

        kept.setdefault(x_outcome.id, []).append(x_value - y_value)

    return kept


def _means(kept: dict[str, list[float]]) -> dict[str, float]:
    return {query_id: math.fsum(diffs) / len(diffs) for query_id, diffs in kept.items()}


def paired_query_deltas(
    outcomes: list[QueryOutcome], queries: dict[str, GoldenQuery], x: str, y: str, metric: str,
) -> dict[str, float]:
    """Each query's mean x − y, over the passes where both arms have a value.

    Pairs x's and y's outcomes by query and pass, and keeps a pair only when both values are not
    None. The mean of the kept differences is the mean of x's kept passes less the mean of y's,
    so the passes are averaged per query before the arms are compared, as spec §8 has it. A
    query with no kept pair is left out, not scored as no difference.
    """
    return _means(_kept_differences(outcomes, queries, x, y, metric))


def _nearest_rank_index(p: Fraction, n: int) -> int:
    """The index of fraction p of n sorted values: ceil(p × n) − 1, clamped to [0, n − 1]."""
    return min(n - 1, max(0, math.ceil(p * n) - 1))


def _settled(value: float) -> float:
    """`value` without the residue float arithmetic leaves, so the rule reads the number itself.

    The scores are ratios of small counts (citations found over citations expected, cited over
    citations given), 0 or 1, or a judge's short decimal, so every mean the study produces is a
    fraction with a small denominator. Float arithmetic leaves residue near 1e-16 on them, and
    the rule's two boundaries are where that would decide a verdict: a mean of exactly 0.10 can
    come out as 0.09999999999999999 and fail `>= 0.10`, and a bound of exactly 0 can come out as
    2.8e-18 and pass `> 0`. Both happen on scores this study can produce (see the analysis
    tests). Rounding to 12 places removes the residue and nothing else, because two different
    values those fractions can take lie far further apart than 1e-12.

    Adding 0.0 turns the -0.0 that rounding a tiny negative leaves into 0.0.
    """
    return round(value, _SETTLED_DECIMALS) + 0.0


def bootstrap_ci(
    deltas: list[float], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """The 95% bootstrap interval for the mean of the per-query deltas.

    Draws `resamples` samples of len(deltas) deltas with replacement, using random.Random(seed),
    so the same deltas always give the same interval. Each delta is one query with its passes
    already averaged, so resampling deltas resamples queries with their passes kept together.

    The bounds are the 2.5th and 97.5th percentiles of the sorted resampled means, by nearest
    rank: for fraction p of n means, the index ceil(p × n) − 1, clamped to [0, n − 1]. With
    10,000 resamples that is index 249 and index 9749. Each bound is settled (see _settled), so
    constant deltas d give (d, d) to 12 places, and exactly (d, d) for a d such as 0.1 that
    float arithmetic would otherwise return as 0.10000000000000002.

    Refuses an empty list, which has no mean to put an interval around.
    """
    if not deltas:
        raise ValueError("a bootstrap needs at least one delta")

    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(math.fsum(rng.choices(deltas, k=n)) / n for _ in range(resamples))

    return (
        _settled(means[_nearest_rank_index(_CI_LOWER, resamples)]),
        _settled(means[_nearest_rank_index(_CI_UPPER, resamples)]),
    )


def verdict(mean_delta: float, ci: tuple[float, float]) -> str:
    """The pre-registered decision rule (spec §8).

    A difference is declared only when the mean paired delta is at least 0.10 either way AND the
    95% interval excludes zero. An interval that touches zero does not exclude it. Anything else
    is inconclusive, which the write-up words as "inconclusive at k queries × passes".
    """
    if abs(mean_delta) >= DIFFERENCE_THRESHOLD and (ci[0] > 0 or ci[1] < 0):
        return "difference"
    return "inconclusive"


def compare(
    outcomes: list[QueryOutcome], queries: dict[str, GoldenQuery], x: str, y: str, passes: int,
) -> list[Comparison]:
    """x against y on each quality metric, in QUALITY_METRICS order.

    `queries` is the run's queries by id, which each metric's nominal size is counted from.

    With no query left to compare (k = 0), the verdict is "no data" and the mean and interval
    are None, never a NaN or a zero that would read as no difference.
    """
    comparisons = []

    for metric in QUALITY_METRICS:
        kept = _kept_differences(outcomes, queries, x, y, metric)
        per_query = _means(kept)
        nominal_k = sum(1 for query in queries.values() if _in_scope(query, metric))
        common = {
            "metric": metric, "x": x, "y": y, "passes": passes, "per_query_delta": per_query,
            "nominal_k": nominal_k, "nominal_pairs": nominal_k * passes,
        }

        if not per_query:
            comparisons.append(Comparison(
                **common, k=0, pairs=0,
                mean_delta=None, ci_low=None, ci_high=None, verdict="no data",
            ))
            continue

        deltas = list(per_query.values())
        mean_delta = _settled(math.fsum(deltas) / len(deltas))
        ci = bootstrap_ci(deltas)

        comparisons.append(Comparison(
            **common, k=len(per_query), pairs=sum(len(d) for d in kept.values()),
            mean_delta=mean_delta, ci_low=ci[0], ci_high=ci[1], verdict=verdict(mean_delta, ci),
        ))

    return comparisons


def _mean_or_none(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def summarise(
    outcomes: list[QueryOutcome], queries: dict[str, GoldenQuery], arm: Arm,
) -> ArmSummary:
    """One arm's descriptive figures, each over the outcomes its field says.

    Each quality metric is scoped as metric_value scopes it. Latency, cost per query and the
    models seen are over the outcomes without errors; the total cost is over every outcome.
    """
    own = [o for o in outcomes if o.arm == arm.name]
    answered = [o for o in own if o.error is None]

    scores = {
        metric: [
            value for o in answered
            if (value := metric_value(o, queries[o.id], metric)) is not None
        ]
        for metric in QUALITY_METRICS
    }

    unanswerable = [o for o in answered if queries[o.id].category == "unanswerable"]
    latencies = latency_percentiles([o.latency_ms for o in answered]) if answered else None

    return ArmSummary(
        arm=arm.name,
        outcome_count=len(own),
        error_count=len(own) - len(answered),
        filtered_count=sum(1 for o in own if o.filtered_stage is not None),
        **{f"mean_{metric}": _mean_or_none(values) for metric, values in scores.items()},
        **{f"{metric}_count": len(values) for metric, values in scores.items()},
        # The runner gives every answer it judges a reason, so a reason with no score is a judgement
        # that failed, and no reason is an answer never put to the judge.
        judge_failure_count=sum(
            1 for o in answered if o.groundedness is None and o.groundedness_reason is not None
        ),
        unanswerable_handled=sum(1 for o in unanswerable if o.unanswerable_handled is True),
        unanswerable_total=len(unanswerable),
        p50_latency_ms=latencies["p50"] if latencies else None,
        p95_latency_ms=latencies["p95"] if latencies else None,
        mean_cost_usd_per_query=_mean_or_none([o.cost_usd for o in answered]),
        total_cost_usd=math.fsum(o.cost_usd for o in own),
        models_seen=sorted({o.model for o in answered if o.model is not None}),
    )
