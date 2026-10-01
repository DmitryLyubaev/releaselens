"""The study's statistics, over hand-built outcomes.

Each test builds the outcomes it needs, so every figure asserted here can be worked out by hand
from the lines above it. Nothing here calls a model or the API.
"""

import json
import math
from fractions import Fraction

import pytest

from app import analysis
from app.analysis import (
    BOOTSTRAP_SEED,
    QUALITY_METRICS,
    _nearest_rank_index,
    bootstrap_ci,
    compare,
    metric_value,
    paired_query_deltas,
    summarise,
    verdict,
)
from app.models import Arm, GoldenQuery, QueryOutcome, RunReport

_QUERIES = {
    query.id: query
    for query in (
        GoldenQuery(
            id="q-ans", question="Which release fixed the planner?", category="factual",
            expected_citations=["issue:1"], must_contain=["14111"],
        ),
        GoldenQuery(
            id="q-temporal", question="What changed first?", category="temporal",
            expected_citations=["issue:2", "commit:3"],
        ),
        GoldenQuery(id="q-unans", question="What is the incident rate?", category="unanswerable"),
    )
}

_Z = Arm(name="Z", base_url="http://arm-z.invalid", expected_provider="azure-openai")
_O = Arm(name="O", base_url="http://arm-o.invalid", expected_provider="openai")


def _factual(query_id: str) -> GoldenQuery:
    return GoldenQuery(id=query_id, question=f"question {query_id}", category="factual")


def _outcome(query_id: str, arm: str, pass_index: int, **fields) -> QueryOutcome:
    """An answered outcome scoring full marks on everything, unless `fields` says otherwise."""
    query = _QUERIES.get(query_id) or _factual(query_id)
    defaults = {
        "category": query.category,
        "question": query.question,
        "answer": "an answer [E1]",
        "citations": ["issue:1"],
        "citation_recall": 1.0,
        "citation_precision": 1.0,
        "groundedness": 1.0,
        "groundedness_reason": "supported",
        "must_contain_satisfied": True,
        "must_not_contain_satisfied": True,
        "unanswerable_handled": True if query.category == "unanswerable" else None,
        "latency_ms": 1000.0,
        "cost_usd": 0.01,
        "tokens_in": 1000,
        "tokens_out": 100,
        "cache_read_input_tokens": 0,
        "degraded": False,
        "unresolved_citation_markers": [],
        "model": "gpt-4.1-mini",
    }
    return QueryOutcome(id=query_id, arm=arm, pass_index=pass_index, **{**defaults, **fields})


def _by_metric(comparisons) -> dict:
    return {c.metric: c for c in comparisons}


def test_metric_scopes():
    # The runner gives an unanswerable query's citation metrics a vacuous 1.0 for citing nothing.
    unanswerable = _outcome("q-unans", "Z", 0, groundedness=0.5, citation_recall=1.0, citation_precision=1.0)
    assert metric_value(unanswerable, _QUERIES["q-unans"], "citation_recall") is None
    assert metric_value(unanswerable, _QUERIES["q-unans"], "citation_precision") is None
    assert metric_value(unanswerable, _QUERIES["q-unans"], "groundedness") == 0.5

    # all([]) is True, so a query with nothing to contain reads as passed unless it is scoped out.
    nothing_to_contain = _outcome("q-temporal", "Z", 0, must_contain_satisfied=True, citation_recall=0.5)
    assert metric_value(nothing_to_contain, _QUERIES["q-temporal"], "must_contain") is None
    assert metric_value(nothing_to_contain, _QUERIES["q-temporal"], "citation_recall") == 0.5

    answered = _outcome(
        "q-ans", "Z", 0,
        groundedness=0.5, citation_recall=0.5, citation_precision=0.25, must_contain_satisfied=False,
    )
    assert [metric_value(answered, _QUERIES["q-ans"], m) for m in QUALITY_METRICS] == [0.5, 0.5, 0.25, 0.0]

    unscored = _outcome("q-ans", "Z", 0, groundedness=None)
    assert metric_value(unscored, _QUERIES["q-ans"], "groundedness") is None

    # Full marks on every field, so only the error can be what makes each of them None. An empty
    # message is still an error: an httpx timeout once produced one.
    for error in ("arm Z expected azure-openai; answered by none", ""):
        errored = _outcome("q-ans", "Z", 0, error=error)
        assert [metric_value(errored, _QUERIES["q-ans"], m) for m in QUALITY_METRICS] == [None] * 4

    with pytest.raises(ValueError, match="unknown quality metric 'latency_ms'"):
        metric_value(answered, _QUERIES["q-ans"], "latency_ms")


def test_passes_are_averaged_before_pairing():
    outcomes = [
        *(_outcome("q-ans", "Z", p, groundedness=g) for p, g in enumerate([1.0, 1.0, 0.0])),
        *(_outcome("q-ans", "O", p, groundedness=0.5) for p in range(3)),
    ]

    assert paired_query_deltas(outcomes, _QUERIES, "Z", "O", "groundedness") == {
        "q-ans": pytest.approx(2 / 3 - 0.5)
    }

    # x − y, not y − x.
    assert paired_query_deltas(outcomes, _QUERIES, "O", "Z", "groundedness") == {
        "q-ans": pytest.approx(0.5 - 2 / 3)
    }


def _two_queries_three_passes(errored: tuple[str, str, int] | None = None) -> list[QueryOutcome]:
    """Z recalls everything and O half, on two answerable queries over three passes.

    The outcome named by `errored` carries an error, with the zeros the runner gives an outcome
    that has no answer.
    """
    outcomes = []
    for query_id in ("q-ans", "q-temporal"):
        for pass_index in range(3):
            for arm, recall in (("Z", 1.0), ("O", 0.5)):
                if (query_id, arm, pass_index) == errored:
                    outcomes.append(_outcome(
                        query_id, arm, pass_index, error="HTTPStatusError: 503",
                        citation_recall=0.0, citation_precision=0.0, groundedness=None,
                        must_contain_satisfied=False,
                    ))
                else:
                    outcomes.append(_outcome(query_id, arm, pass_index, citation_recall=recall))
    return outcomes


def test_pairs_with_an_errored_side_are_dropped_and_counted():
    clean = _by_metric(compare(_two_queries_three_passes(), _QUERIES, "Z", "O", passes=3))
    broken = _by_metric(compare(_two_queries_three_passes(("q-ans", "Z", 1)), _QUERIES, "Z", "O", passes=3))

    # must_contain applies to q-ans alone, so its pair count is that query's: 2 of 3 passes.
    assert (clean["must_contain"].k, clean["must_contain"].pairs) == (1, 3)
    assert (broken["must_contain"].k, broken["must_contain"].pairs) == (1, 2)

    # Over both queries, the error removes one pair and no query.
    assert (clean["citation_recall"].k, clean["citation_recall"].pairs) == (2, 6)
    assert (broken["citation_recall"].k, broken["citation_recall"].pairs) == (2, 5)

    # The error is not scored as a quality loss: q-ans keeps the delta of the passes that answered.
    assert broken["citation_recall"].per_query_delta == {"q-ans": 0.5, "q-temporal": 0.5}
    assert broken["citation_recall"].mean_delta == 0.5


def test_unscored_groundedness_drops_the_pair_from_groundedness_only():
    outcomes = [
        _outcome("q-ans", "Z", 0),
        # The judge's reply could not be parsed. The answer itself is there, and its citations count.
        _outcome("q-ans", "Z", 1, groundedness=None, groundedness_reason="judge output unparseable"),
        _outcome("q-ans", "Z", 2),
        *(_outcome("q-ans", "O", p, citation_recall=0.5) for p in range(3)),
    ]

    pairs = {c.metric: (c.k, c.pairs) for c in compare(outcomes, _QUERIES, "Z", "O", passes=3)}

    assert pairs == {
        "groundedness": (1, 2),
        "citation_recall": (1, 3),
        "citation_precision": (1, 3),
        "must_contain": (1, 3),
    }


def test_bootstrap_is_reproducible_and_brackets_the_mean():
    deltas = [0.5, -0.25, 0.1, 0.3, 0.0, 0.6, -0.1, 0.2, 1 / 3, -1 / 6]

    first = bootstrap_ci(deltas)
    assert bootstrap_ci(deltas) == first
    # The seed is what fixes it, not a resampling that never varies.
    assert bootstrap_ci(deltas, seed=BOOTSTRAP_SEED + 1) != first

    mean = math.fsum(deltas) / len(deltas)
    assert first[0] <= mean <= first[1]
    assert first[0] < first[1]

    # (d, d) to the 12 places the bounds are settled to, which is exactly (d, d) for 0.1: the case
    # float arithmetic gets wrong, since (0.1 + 0.1 + 0.1) / 3 is 0.10000000000000002.
    for d in (0.1, -0.25, 0.0, 2 / 3):
        for k in (1, 3, 10):
            assert bootstrap_ci([d] * k) == (round(d, 12), round(d, 12))


@pytest.mark.parametrize(
    ("p", "n", "index"),
    [
        (Fraction(1, 40), 10_000, 249),
        (Fraction(39, 40), 10_000, 9749),
        (Fraction(1, 40), 40, 0),
        (Fraction(39, 40), 40, 38),
        (Fraction(1, 40), 1, 0),
        (Fraction(39, 40), 1, 0),
    ],
)
def test_percentile_index_is_nearest_rank(p, n, index):
    """ceil(p × n) − 1, clamped to [0, n − 1]."""
    assert _nearest_rank_index(p, n) == index


@pytest.mark.parametrize(
    ("mean_delta", "ci", "expected"),
    [
        (-0.10, (-0.2, -0.01), "difference"),
        (0.09, (0.01, 0.2), "inconclusive"),
        (0.25, (-0.05, 0.5), "inconclusive"),
        # A CI touching zero does not exclude it.
        (0.10, (0.0, 0.2), "inconclusive"),
        (0.10, (0.01, 0.2), "difference"),
        (-0.25, (-0.5, 0.0), "inconclusive"),
    ],
)
def test_verdict_rule(mean_delta, ci, expected):
    assert verdict(mean_delta, ci) == expected


def _sixths(m: int) -> tuple[list[float], list[float]]:
    """Z's and O's groundedness over three passes, scored 0, 0.5 or 1, for a query delta of m/6."""
    z, o = [], []
    for _ in range(3):
        halves = max(-2, min(2, m))
        m -= halves
        z.append(1.0 if halves >= 0 else 1.0 + halves / 2)
        o.append(1.0 - halves / 2 if halves >= 0 else 1.0)
    return z, o


@pytest.mark.parametrize(
    ("scores", "boundary", "expected"),
    [
        # A judge's scores whose mean delta is exactly -0.10, which floats sum to -0.09999999999999999.
        (
            [
                ([0.5, 0.5, 0.9], [0.9, 1.0, 0.25]),
                ([0.0, 0.5, 0.75], [0.5, 0.9, 0.25]),
                ([0.8, 0.25, 0.9], [0.75, 0.25, 0.75]),
                ([0.25, 0.8, 0.5], [1.0, 0.25, 1.0]),
                ([0.5, 0.0, 0.8], [0.75, 0.9, 0.0]),
            ],
            ("mean_delta", -0.1),
            "difference",
        ),
        # Ten queries whose interval's lower bound is exactly 0, which floats put at +2.8e-18.
        ([_sixths(m) for m in (4, 1, 0, 3, 0, -2, 0, 0, 2, 4)], ("ci_low", 0.0), "inconclusive"),
        # And an upper bound of exactly 0, which floats put at -2.8e-18.
        ([_sixths(m) for m in (-3, 0, 1, 1, 0, -3, -2, -2, -3, 1)], ("ci_high", 0.0), "inconclusive"),
    ],
    ids=["mean-at-the-threshold", "lower-bound-at-zero", "upper-bound-at-zero"],
)
def test_the_rule_reads_exact_values_at_its_boundaries(monkeypatch, scores, boundary, expected):
    """Float residue must not decide the verdict where the rule draws its lines.

    Each case is one this study can produce, found by search: groundedness over three passes,
    where the true value sits exactly on a boundary of the rule and float arithmetic lands just
    on the wrong side of it.
    """
    queries = {f"q-{n}": _factual(f"q-{n}") for n in range(len(scores))}
    outcomes = [
        _outcome(query_id, arm, pass_index, groundedness=score)
        for query_id, (z, o) in zip(queries, scores)
        for arm, arm_scores in (("Z", z), ("O", o))
        for pass_index, score in enumerate(arm_scores)
    ]

    def groundedness():
        return _by_metric(compare(outcomes, queries, "Z", "O", passes=3))["groundedness"]

    field, value = boundary
    assert getattr(groundedness(), field) == value
    assert groundedness().verdict == expected

    # The case really is on the edge: left unsettled, the residue flips the verdict.
    monkeypatch.setattr(analysis, "_settled", lambda v: v)
    assert getattr(groundedness(), field) != value
    assert groundedness().verdict != expected


def test_degenerate_inputs():
    # k = 1: a single query, so every resample is that query and the interval is a point.
    single = [
        *(_outcome("q-ans", "Z", p, groundedness=1.0) for p in range(3)),
        *(_outcome("q-ans", "O", p, groundedness=0.5) for p in range(3)),
    ]
    grounded = _by_metric(compare(single, _QUERIES, "Z", "O", passes=3))["groundedness"]
    assert (grounded.k, grounded.mean_delta, grounded.ci_low, grounded.ci_high) == (1, 0.5, 0.5, 0.5)
    assert grounded.verdict == "difference"
    assert compare(single, _QUERIES, "Z", "O", passes=3) == compare(single, _QUERIES, "Z", "O", passes=3)

    # Zero variance across queries: identical answers on both arms.
    same = [_outcome(q, arm, p) for q in _QUERIES for arm in ("Z", "O") for p in range(3)]
    for comparison in compare(same, _QUERIES, "Z", "O", passes=3):
        assert (comparison.mean_delta, comparison.ci_low, comparison.ci_high) == (0.0, 0.0, 0.0)
        assert comparison.verdict == "inconclusive"

    # k = 0: every one of O's queries failed, so no pair survives on any metric.
    nothing = [
        *(_outcome(q, "Z", p) for q in _QUERIES for p in range(3)),
        *(_outcome(q, "O", p, error="ConnectError: connection refused") for q in _QUERIES for p in range(3)),
    ]
    empty = compare(nothing, _QUERIES, "Z", "O", passes=3)
    assert [c.metric for c in empty] == list(QUALITY_METRICS)
    for comparison in empty:
        assert (comparison.k, comparison.pairs, comparison.passes) == (0, 0, 3)
        assert (comparison.mean_delta, comparison.ci_low, comparison.ci_high) == (None, None, None)
        assert comparison.verdict == "no data"
        assert comparison.per_query_delta == {}

    # An arm with no answer at all has no mean, latency or cost per query to report, rather than 0.
    failed = summarise(nothing, _QUERIES, _O)
    assert (failed.outcome_count, failed.error_count) == (9, 9)
    assert (failed.mean_groundedness, failed.groundedness_count) == (None, 0)
    assert (failed.p50_latency_ms, failed.p95_latency_ms, failed.mean_cost_usd_per_query) == (None, None, None)

    report = RunReport(
        run_id="run", started_at="2026-10-02T00:00:00+00:00", passes=3, query_ids=list(_QUERIES),
        arms=[_Z, _O],
        arm_summaries=[summarise(nothing, _QUERIES, _Z), failed],
        comparisons=[*compare(single, _QUERIES, "Z", "O", passes=3), *empty],
        answering_cost_usd=0.09, judge_cost_usd=0.0, total_cost_usd=0.09,
        estimated_cost_usd_before_run=None, outcomes=nothing,
    )

    dumped = report.model_dump_json()
    assert "NaN" not in dumped and "Infinity" not in dumped
    # Pydantic writes a NaN as null, so the JSON alone could hide one. Check the numbers themselves.
    assert all(math.isfinite(n) for n in _numbers(report.model_dump()))
    assert json.loads(dumped)["comparisons"][-1]["verdict"] == "no data"


def _numbers(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in _numbers(v)]
    return []


def test_arm_summary_scopes_each_figure():
    gpt = "gpt-4.1-mini-2025-04-14"
    outcomes = [
        _outcome("q-ans", "Z", 0, groundedness=1.0, citation_precision=0.5, latency_ms=100.0, cost_usd=0.01, model=gpt),
        _outcome(
            "q-ans", "Z", 1, groundedness=None, citation_recall=0.5, must_contain_satisfied=False,
            latency_ms=300.0, cost_usd=0.03, model=gpt,
        ),
        # A reply from the wrong provider: billed, and none of its figures count.
        _outcome(
            "q-ans", "Z", 2, error="arm Z expected azure-openai; answered by anthropic",
            latency_ms=180_000.0, cost_usd=0.02, model="claude-sonnet-5",
        ),
        # Nothing to contain: left out of must_contain, not passed.
        _outcome(
            "q-temporal", "Z", 0, groundedness=0.5, citation_recall=0.0, citation_precision=0.0,
            latency_ms=200.0, cost_usd=0.02, model="gpt-4.1-mini",
        ),
        # Blocked by Z's own filter: recorded as filtered, and still Z's answer.
        _outcome(
            "q-unans", "Z", 0, filtered_stage="prompt", unanswerable_handled=False,
            latency_ms=50.0, cost_usd=0.0, model=gpt,
        ),
        _outcome("q-unans", "Z", 1, unanswerable_handled=True, latency_ms=400.0, cost_usd=0.04, model=gpt),
        _outcome(
            "q-unans", "Z", 2, error="ConnectError: connection refused", unanswerable_handled=None,
            latency_ms=5.0, cost_usd=0.0, model=None,
        ),
        # Another arm's outcome, which is not Z's to summarise.
        _outcome("q-ans", "O", 0, latency_ms=9_999.0, cost_usd=1.0, model="gpt-4.1-mini"),
    ]

    summary = summarise(outcomes, _QUERIES, _Z)

    assert summary.arm == "Z"
    assert (summary.outcome_count, summary.error_count, summary.filtered_count) == (7, 2, 1)

    # Over the outcomes without errors where each metric applies and has a value.
    assert (summary.mean_groundedness, summary.groundedness_count) == (pytest.approx(3.5 / 4), 4)
    assert (summary.mean_citation_recall, summary.citation_recall_count) == (pytest.approx(0.5), 3)
    assert (summary.mean_citation_precision, summary.citation_precision_count) == (pytest.approx(0.5), 3)
    assert (summary.mean_must_contain, summary.must_contain_count) == (pytest.approx(0.5), 2)

    assert (summary.unanswerable_handled, summary.unanswerable_total) == (1, 2)

    # Latency of the answers, not of the failures: [50, 100, 200, 300, 400].
    assert (summary.p50_latency_ms, summary.p95_latency_ms) == (200.0, 400.0)

    # The mean is over the five answers; the total is everything the arm was billed, rejected reply included.
    assert summary.mean_cost_usd_per_query == pytest.approx(0.10 / 5)
    assert summary.total_cost_usd == pytest.approx(0.12)

    assert summary.models_seen == ["gpt-4.1-mini", gpt]
