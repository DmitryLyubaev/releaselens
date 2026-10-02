"""The write-up: a saved run rendered as exactly the markdown that is published.

The run is built by hand and analysed by `score.analyse`, so every figure can be traced to the
lines that made it. Nothing here calls a model, Azure or the Worker.
"""

import pytest

from app.retrieval.arms import ArmResult, Hit
from app.retrieval.questions import Question
from app.retrieval.report import NO_CORRECTION, render
from app.retrieval.score import ARMS, analyse

_TYPES = [("commit", 62), ("issue", 81), ("pull_request", 151), ("release", 6)]


def _questions() -> list[Question]:
    types = [entity_type for entity_type, count in _TYPES for _ in range(count)]
    return [Question(f"q{n:03d}", f"question {n}", f"{entity_type}:{n}", entity_type)
            for n, entity_type in enumerate(types, start=1)]


# How many of the 300 each arm puts first: E3 clearly beats E1, S3 is level with S1 and S2.
_HITS = {"E1": 150, "E2": 170, "E3": 200, "S1": 150, "S2": 152, "S3": 155}


def _result(question: Question, arm: str, *, hit: bool, error: str | None = None) -> ArmResult:
    if error is not None:
        return ArmResult(question.qid, arm, [], 4.0, error)
    first = question.target if hit else "issue:0"
    hits = [Hit(1, first, 0.9), Hit(2, "commit:zzz", 0.6), Hit(3, question.target, 0.5)]
    return ArmResult(question.qid, arm, hits, 20.0 if arm in ("E1", "S1") else 300.0, None, 12)


def _run(*, s1_error: bool = True, changed_on_e2: int = 4) -> dict:
    questions = _questions()
    results, repeat = [], []
    for arm in ARMS:
        for index, question in enumerate(questions):
            error = "NpgsqlException: connection reset (not real)" if (
                s1_error and arm == "S1" and index == 299) else None
            results.append(_result(question, arm, hit=index < _HITS[arm], error=error))
            if index < 30:
                flipped = arm == "E2" and index < changed_on_e2
                repeat.append(_result(question, arm, hit=(index < _HITS[arm]) != flipped))
    return {
        "run_id": "20261002T101500Z",
        "started_at": "2026-10-02T10:15:00+00:00",
        "questions": {"file": "retrieval/questions.jsonl", "sha256": "0" * 64, "count": 300},
        "chunks": 41825,
        "deployments": {"E2": "embed-small-not-real", "E3": "embed-large-not-real",
                        "S2": "embed-small-not-real", "S3": "embed-small-not-real"},
        "search": {"index": "releaselens-chunks", "api_version": "2026-04-01"},
        "repeat_first": 30,
        "arm_failure": None,
        "analysis": analyse(questions, results, repeat, {}),
    }


@pytest.fixture(scope="module")
def page() -> str:
    return render(_run())


def test_render_states_no_correction_and_small_releases(page):
    assert ("The three comparisons are each made at 95%, with no correction for multiple "
            "comparisons.") in page
    assert NO_CORRECTION in page
    assert "too small to read" in page
    release_rows = [line for line in page.splitlines() if line.startswith("| release")]
    assert release_rows and all("too small to read" in row for row in release_rows)
    assert not any("too small to read" in line for line in page.splitlines()
                   if line.startswith(("| commit", "| issue", "| pull_request")))


def test_render_words_each_verdict_exactly(page):
    rows = {line.split(" | ")[1]: line for line in page.splitlines() if line.startswith("| C")}

    assert rows["E3 − E1"].endswith("| difference |")
    # S1 errored on one question, so C2 is over the 299 that survive on both arms.
    assert rows["S3 − S1"].endswith("| inconclusive at 299 questions |")
    assert rows["S3 − S2"].endswith("| inconclusive at 300 questions |")
    assert "| +0.167 |" in rows["E3 − E1"]


def test_render_reports_every_arm_with_the_latency_and_cost_caveats(page):
    for arm in ARMS:
        assert any(line.startswith(f"| {arm} |") for line in page.splitlines())
    assert "not alike" in page
    assert "retry" in page and "drop" in page
    assert "$0.133 per hour" in page
    assert "free plan" in page
    assert "2026-10-02" in page


def test_render_appendix_is_exploratory_with_no_interval(page):
    appendix = page.split("## Appendix", 1)[1].split("\n## ", 1)[0]

    assert "exploratory" in appendix.lower()
    pairs = [line for line in appendix.splitlines() if line.startswith("| ") and " − " in line]
    assert len(pairs) == 12
    assert "CI" not in appendix and "[" not in appendix


def test_render_caveats_determinism_and_drops(page):
    caveats = page.split("## Caveats", 1)[1]

    # E2 changed its top-1 on 4 of the 30 repeated, more than the 3 the spec allows.
    assert "E2: 4 of 30 top-1 results changed (more than 3: a caveat on E2)" in caveats
    assert "E1: 0 of 30 top-1 results changed" in caveats
    assert "S1: 1 question dropped" in caveats
    assert "C2 (S3 − S1): 1 pair dropped" in caveats


def test_render_is_exact_and_repeatable(page):
    assert render(_run()) == page
    assert page.endswith("\n") and not page.endswith("\n\n")
    assert page.startswith("# Retrieval benchmark — 2 October 2026\n")


def test_render_refuses_a_run_with_an_arm_failure():
    run = _run() | {"arm_failure": {"arm": "E3", "pass": "first", "error": "CredentialUnavailableError"},
                    "analysis": None}

    with pytest.raises(ValueError, match="E3"):
        render(run)


def test_render_refuses_a_run_with_no_analysis():
    with pytest.raises(ValueError, match="no analysis"):
        render(_run() | {"analysis": None})
