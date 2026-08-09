from app.metrics import (
    citation_precision,
    citation_recall,
    latency_percentiles,
    unanswerable_correct,
)


def test_citation_recall_all_expected_present():
    assert citation_recall(expected=["issue:4211"], actual=["issue:4211", "commit:abc"]) == 1.0


def test_citation_recall_half_present():
    assert citation_recall(expected=["issue:1", "issue:2"], actual=["issue:1"]) == 0.5


def test_citation_recall_no_expectation_is_perfect():
    # Unanswerable queries expect nothing, so recall is vacuously satisfied.
    assert citation_recall(expected=[], actual=[]) == 1.0
    assert citation_recall(expected=[], actual=["issue:9"]) == 1.0


def test_citation_precision_counts_only_expected_hits():
    assert citation_precision(expected=["issue:1"], actual=["issue:1", "issue:2"]) == 0.5


def test_citation_precision_with_no_citations_is_zero_when_some_were_expected():
    assert citation_precision(expected=["issue:1"], actual=[]) == 0.0


def test_citation_precision_with_no_citations_is_one_when_none_were_expected():
    assert citation_precision(expected=[], actual=[]) == 1.0


def test_latency_percentiles():
    p = latency_percentiles([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    assert p["p50"] == 500
    assert p["p95"] == 1000


def test_latency_percentiles_empty():
    p = latency_percentiles([])
    assert p == {"p50": 0, "p95": 0}


def test_unanswerable_correct_when_the_answer_declines():
    assert unanswerable_correct("The evidence does not contain information about that.") is True


def test_unanswerable_incorrect_when_the_answer_asserts():
    assert unanswerable_correct("The incident rate is 3.2 percent.") is False
