"""Scoring: chunks collapsed to artefacts, the per-question figures, the three comparisons at
five points, the determinism check and the cost per 1,000 queries.

Pure functions over hand-built results: nothing here calls a model, Azure or the Worker.
"""

import pytest

from app.analysis import verdict
from app.retrieval.arms import NO_QUERY_VECTOR, ArmResult, Hit
from app.retrieval.questions import Question
from app.retrieval.score import (
    ARMS,
    COMPARISONS,
    RATES,
    SEED,
    THRESHOLD,
    QuestionScore,
    analyse,
    collapse,
    compare_top1,
    cost_per_1000,
    determinism,
    score,
)

_NO_LINKS: dict[str, frozenset[str]] = {}


def _hits(*artefacts_and_scores: tuple[str, float]) -> list[Hit]:
    return [Hit(chunk_id, artefact, value)
            for chunk_id, (artefact, value) in enumerate(artefacts_and_scores, start=1)]


def _result(*artefacts_and_scores, arm="E2", qid="q001", error=None, tokens=0, ms=10.0) -> ArmResult:
    hits = [] if error is not None else _hits(*artefacts_and_scores)
    return ArmResult(qid, arm, hits, ms, error, tokens)


def test_the_constants_are_the_pre_registered_ones():
    assert (THRESHOLD, SEED) == (0.05, 20261002)
    assert COMPARISONS == (("E3", "E1"), ("S3", "S1"), ("S3", "S2"))
    assert ARMS == ("E1", "E2", "E3", "S1", "S2", "S3")


def test_collapse_keeps_first_appearance():
    hits = _hits(("issue:7", 0.9), ("commit:abc", 0.8), ("issue:7", 0.85), ("pull_request:3", 0.7),
                 ("commit:abc", 0.95))

    assert collapse(hits) == [("issue:7", 0.9), ("commit:abc", 0.8), ("pull_request:3", 0.7)]
    assert collapse([]) == []


def test_a_hit_scores_top1_and_its_reciprocal_rank():
    hit = score(_result(("issue:7", 0.9), ("issue:7", 0.8), ("commit:abc", 0.5)), "issue:7", _NO_LINKS)
    second = score(_result(("issue:7", 0.9), ("issue:7", 0.8), ("commit:abc", 0.5)), "commit:abc", _NO_LINKS)

    assert hit == QuestionScore(top1=1, rr=1.0, lenient_top1=1, margin=pytest.approx(0.4))
    # Two chunks of issue:7 come first, but collapsed it is one artefact, so the target is 2nd.
    assert second == QuestionScore(top1=0, rr=0.5, lenient_top1=0, margin=None)


def test_target_outside_the_list_scores_zero():
    """Review Focus 4: a target beyond the 50 chunks is a miss, not an error."""
    result = _result(*[(f"issue:{n}", 1.0 - n / 100) for n in range(50)])

    scored = score(result, "issue:999", _NO_LINKS)

    assert scored == QuestionScore(top1=0, rr=0.0, lenient_top1=0, margin=None)
    assert score(_result(), "issue:999", _NO_LINKS) == QuestionScore(0, 0.0, 0, None)


def test_only_the_first_50_chunks_count():
    result = _result(*[(f"issue:{n}", 1.0 - n / 100) for n in range(50)], ("issue:999", 0.1))

    assert score(result, "issue:999", _NO_LINKS).rr == 0.0


def test_lenient_accepts_the_merge_commit_both_ways():
    links = {"pull_request:42": frozenset({"commit:abc1234"}),
             "commit:abc1234": frozenset({"pull_request:42"})}

    commit_first = _result(("commit:abc1234", 0.9), ("pull_request:42", 0.8))
    pull_request_first = _result(("pull_request:42", 0.9), ("commit:abc1234", 0.8))
    unrelated_first = _result(("commit:fff0000", 0.9), ("pull_request:42", 0.8))

    on_pull_request = score(commit_first, "pull_request:42", links)
    on_commit = score(pull_request_first, "commit:abc1234", links)

    assert (on_pull_request.top1, on_pull_request.lenient_top1) == (0, 1)
    assert (on_commit.top1, on_commit.lenient_top1) == (0, 1)
    assert score(unrelated_first, "pull_request:42", links).lenient_top1 == 0
    # Lenient never helps an artefact with no link.
    assert score(commit_first, "pull_request:42", _NO_LINKS).lenient_top1 == 0


def test_margin_is_within_arm_and_none_on_a_miss():
    # The first artefact's score less the next different artefact's, in the arm's own units.
    reranked = score(_result(("issue:7", 3.2), ("issue:7", 3.1), ("issue:8", 2.0), arm="S3"),
                     "issue:7", _NO_LINKS)
    cosine = score(_result(("issue:7", 0.61), ("issue:8", 0.60)), "issue:7", _NO_LINKS)
    miss = score(_result(("issue:8", 0.61), ("issue:7", 0.60)), "issue:7", _NO_LINKS)
    # A lenient hit is still a miss on top-1, so it has no margin either.
    lenient = score(_result(("commit:abc1234", 0.9), ("pull_request:42", 0.8)), "pull_request:42",
                    {"pull_request:42": frozenset({"commit:abc1234"})})
    alone = score(_result(("issue:7", 0.9), ("issue:7", 0.8)), "issue:7", _NO_LINKS)

    assert reranked.margin == pytest.approx(1.2)
    assert cosine.margin == pytest.approx(0.01)
    assert miss.margin is None
    assert lenient.margin is None
    # No different artefact came back at all, so there is nothing to measure a margin against.
    assert alone.margin is None


def test_an_errored_result_is_not_scored():
    assert score(_result(error="SearchError: HTTP 503: busy (not real)"), "issue:7", _NO_LINKS) is None
    # An error of "" is still an error: never truthiness.
    assert score(_result(error=""), "issue:7", _NO_LINKS) is None


def _scores(hits: dict[str, int]) -> dict[str, QuestionScore | None]:
    return {qid: QuestionScore(top1, float(top1), top1, None) for qid, top1 in hits.items()}


def test_errored_pairs_are_dropped_and_counted():
    """Review Focus 2, the scoring half: an errored question leaves that arm's pairs, counted."""
    qids = [f"q{n:03d}" for n in range(1, 301)]
    s3 = _scores({qid: 1 if n % 2 else 0 for n, qid in enumerate(qids)})
    s3["q007"] = None
    s3["q123"] = None
    s2 = _scores({qid: 0 for qid in qids})

    comparison = compare_top1({"S3": s3, "S2": s2}, "S3", "S2")

    assert (comparison.x, comparison.y) == ("S3", "S2")
    assert (comparison.n, comparison.dropped) == (298, 2)
    # q007 (index 6, a 0) and q123 (index 122, a 0) are out, leaving 150 hits over 298.
    assert comparison.mean == round(150 / 298, 12)
    assert comparison.ci_low <= comparison.mean <= comparison.ci_high
    assert comparison.verdict == "difference"


def test_a_question_missing_from_one_arm_is_dropped_too():
    x = _scores({"q001": 1, "q002": 1, "q003": 0})
    y = _scores({"q001": 0, "q003": 0})

    comparison = compare_top1({"X": x, "Y": y}, "X", "Y")

    assert (comparison.n, comparison.dropped) == (2, 1)


def test_comparison_is_reproducible_with_the_pre_registered_seed():
    x = _scores({f"q{n:03d}": n % 3 == 0 for n in range(1, 61)})
    y = _scores({f"q{n:03d}": n % 4 == 0 for n in range(1, 61)})

    first = compare_top1({"X": x, "Y": y}, "X", "Y")
    again = compare_top1({"X": x, "Y": y}, "X", "Y")

    assert first == again


def test_no_question_scored_on_both_arms_is_refused():
    with pytest.raises(ValueError, match="no question was scored on both X and Y"):
        compare_top1({"X": {"q001": None}, "Y": _scores({"q001": 1})}, "X", "Y")


@pytest.mark.parametrize(
    ("mean", "ci", "expected"),
    [
        (-0.05, (-0.09, -0.01), "difference"),
        (0.049, (0.01, 0.09), "inconclusive"),
        (0.20, (-0.01, 0.4), "inconclusive"),
        (0.05, (0.0, 0.1), "inconclusive"),
    ],
)
def test_rule_at_five_points(mean, ci, expected):
    assert verdict(mean, ci, threshold=THRESHOLD) == expected


def test_a_mean_of_exactly_five_points_is_a_difference():
    """15 of 300 more hits is exactly +0.05, which the rule must read as at the threshold."""
    qids = [f"q{n:03d}" for n in range(1, 301)]
    x = _scores({qid: 1 if n < 15 else 0 for n, qid in enumerate(qids)})
    y = _scores({qid: 0 for qid in qids})

    comparison = compare_top1({"X": x, "Y": y}, "X", "Y")

    assert comparison.mean == 0.05
    assert comparison.ci_low > 0
    assert comparison.verdict == "difference"


def test_determinism_counts_changed_top1():
    first = [
        _result(("issue:1", 0.9), arm="E2", qid="q001"),
        _result(("issue:2", 0.9), arm="E2", qid="q002"),
        _result(("issue:3", 0.9), ("issue:4", 0.8), arm="E2", qid="q003"),
        _result(("issue:1", 0.9), arm="S3", qid="q001"),
        _result(arm="S3", qid="q002", error="SearchError: HTTP 503 (not real)"),
    ]
    repeat = [
        # Same top-1 artefact from a different chunk, and a lower score: not a change.
        ArmResult("q001", "E2", [Hit(99, "issue:1", 0.7)], 10.0, None),
        _result(("issue:9", 0.9), ("issue:2", 0.8), arm="E2", qid="q002"),
        _result(("issue:4", 0.9), ("issue:3", 0.8), arm="E2", qid="q003"),
        _result(("issue:1", 0.9), arm="S3", qid="q001"),
        # An errored side has no top-1 to compare, so it is not counted as a change.
        _result(("issue:5", 0.9), arm="S3", qid="q002"),
    ]

    assert determinism(first, repeat) == {"E2": 2, "S3": 0}


def test_determinism_refuses_a_repeat_with_no_first_pass():
    with pytest.raises(ValueError, match="q002"):
        determinism([_result(arm="E2", qid="q001")], [_result(arm="E2", qid="q002")])


def test_cost_per_1000_prices_the_measured_query_tokens():
    assert RATES["date"] == "2026-10-02"
    assert RATES["per_million_tokens"] == {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13}
    assert RATES["ranker_per_request"] == 0.0
    assert "free" in RATES["ranker_plan"]
    assert RATES["search_per_hour"] == 0.133

    e3 = [_result(arm="E3", qid=f"q{n:03d}", tokens=20) for n in range(1, 5)]
    # An errored row was still billed for its embedding, so its tokens count.
    e3.append(_result(arm="E3", qid="q005", tokens=20, error="HTTPStatusError: 500 (not real)"))
    s3 = [_result(arm="S3", qid=f"q{n:03d}", tokens=10) for n in range(1, 4)]
    s3.append(_result(arm="S3", qid="q004", error=NO_QUERY_VECTOR))

    assert cost_per_1000("E3", e3, RATES) == pytest.approx(1000 * 20 * 0.13 / 1e6)
    assert cost_per_1000("S3", s3, RATES) == pytest.approx(1000 * (30 / 4) * 0.02 / 1e6)
    assert cost_per_1000("E1", [_result(arm="E1")], RATES) == 0.0

    # The ranker's price per request applies to each S3 search actually sent.
    priced = RATES | {"ranker_per_request": 0.001}
    assert cost_per_1000("S3", s3, priced) == pytest.approx(1000 * (30 * 0.02 / 1e6 + 3 * 0.001) / 4)
    assert cost_per_1000("S2", [_result(arm="S2", tokens=10)], priced) == pytest.approx(1000 * 10 * 0.02 / 1e6)


def _questions(count: int) -> list[Question]:
    types = ["commit", "issue", "pull_request", "release"]
    return [Question(f"q{n:03d}", f"question {n}", f"{types[n % 4]}:{n}", types[n % 4])
            for n in range(1, count + 1)]


def _run(questions: list[Question], *, errored: dict[str, set[str]] | None = None):
    """Every arm answers every question; arm i hits the target on questions whose number is not
    a multiple of i + 2, so each arm has its own accuracy."""
    errored = errored or {}
    results = []
    for index, arm in enumerate(ARMS):
        for question in questions:
            number = int(question.qid[1:])
            if question.qid in errored.get(arm, set()):
                results.append(ArmResult(question.qid, arm, [], 5.0, "boom (not real)", 3))
                continue
            first = question.target if number % (index + 2) else "issue:0"
            hits = [Hit(1, first, 0.9), Hit(2, "commit:zzz", 0.5), Hit(3, question.target, 0.4)]
            results.append(ArmResult(question.qid, arm, hits, float(number + index), None, 3))
    return results


def test_analyse_summarises_every_arm_comparison_and_type():
    questions = _questions(12)
    results = _run(questions, errored={"S3": {"q002"}})
    repeat = [result for result in results if result.qid in ("q001", "q002", "q003")]

    analysis = analyse(questions, results, repeat, _NO_LINKS)

    assert [c["x"] + "-" + c["y"] for c in analysis["comparisons"]] == ["E3-E1", "S3-S1", "S3-S2"]
    assert analysis["comparisons"][1]["dropped"] == 1
    assert analysis["arms"]["S3"]["errors"] == 1
    assert analysis["arms"]["S3"]["n"] == 11
    # E1 (index 0) misses on the even questions: 6 of 12.
    assert analysis["arms"]["E1"]["top1"] == 0.5
    assert analysis["arms"]["E1"]["mrr"] == pytest.approx((6 + 6 / 3) / 12)
    # Latency over the rows without errors only: 6, then 8 to 17 ms, with q002's 5 ms left out.
    assert analysis["arms"]["S3"]["p50_ms"] == 12.0
    assert analysis["arms"]["E1"]["p50_ms"] == 6.0
    assert len(analysis["exploratory"]) == 12
    assert not {(e["x"], e["y"]) for e in analysis["exploratory"]} & set(COMPARISONS)
    assert analysis["by_type"]["release"]["E1"]["n"] == 3
    assert analysis["determinism"]["S3"] == {"compared": 2, "changed": 0, "caveat": False}
    assert analysis["questions"] == 12


def test_analyse_refuses_an_arm_that_did_not_answer_every_question():
    questions = _questions(4)
    results = [result for result in _run(questions) if (result.arm, result.qid) != ("E2", "q003")]

    with pytest.raises(ValueError, match="E2"):
        analyse(questions, results, [], _NO_LINKS)
