"""The study's write-up, rendered from hand-built reports.

Each report is built from the models and then dumped to JSON, because that is what
render_markdown is given: the stored report, not the objects that wrote it. Nothing here calls a
model or the API.
"""

import json

import pytest

from app import study_report
from app.analysis import QUALITY_METRICS, ArmSummary, Comparison
from app.models import Arm, RunReport
from app.study_report import render_markdown

_QUERY_IDS = [
    "gq-001", "gq-002", "gq-014", "gq-015", "gq-022", "gq-023", "gq-029", "gq-030", "gq-036", "gq-037",
]

# Each metric's queries, as compare() scopes them for this selection (spec §8).
_SCOPE = {
    "groundedness": _QUERY_IDS,
    "citation_recall": _QUERY_IDS[:8],
    "citation_precision": _QUERY_IDS[:8],
    "must_contain": ["gq-001", "gq-002", "gq-022", "gq-023", "gq-029", "gq-030"],
}

_ARMS = [
    Arm(name="A", base_url="http://localhost:8081", expected_provider="anthropic"),
    Arm(name="Z", base_url="http://localhost:8082", expected_provider="azure-openai"),
    Arm(name="O", base_url="http://localhost:8083", expected_provider="openai"),
]


def _summary(arm: str, **fields) -> ArmSummary:
    defaults = {
        "outcome_count": 30,
        "error_count": 0,
        "filtered_count": 0,
        "mean_groundedness": 0.9,
        "groundedness_count": 30,
        "judge_failure_count": 0,
        "no_reply_count": 0,
        "mean_citation_recall": 0.8,
        "citation_recall_count": 24,
        "mean_citation_precision": 0.6,
        "citation_precision_count": 24,
        "mean_must_contain": 1.0,
        "must_contain_count": 18,
        "unanswerable_handled": 6,
        "unanswerable_total": 6,
        "p50_latency_ms": 10_000.0,
        "p95_latency_ms": 20_000.0,
        "mean_cost_usd_per_query": 0.02,
        "total_cost_usd": 0.6,
        "models_seen": ["gpt-4.1-mini-2025-04-14"],
    }
    return ArmSummary(arm=arm, **{**defaults, **fields})


def _comparison(metric: str, x: str, y: str, **fields) -> Comparison:
    """x against y over every query in the metric's scope on all 3 passes, unless `fields` says
    otherwise."""
    scope = _SCOPE[metric]
    defaults = {
        "k": len(scope),
        "pairs": len(scope) * 3,
        "nominal_k": len(scope),
        "nominal_pairs": len(scope) * 3,
        "passes": 3,
        "mean_delta": 0.0,
        "ci_low": -0.05,
        "ci_high": 0.05,
        "verdict": "inconclusive",
        "per_query_delta": {query_id: 0.0 for query_id in scope},
    }
    return Comparison(metric=metric, x=x, y=y, **{**defaults, **fields})


def _report(comparisons: list[Comparison] | None = None, summaries: list[ArmSummary] | None = None, **fields) -> dict:
    """A real study run as stored: Z against O, then Z against A, unless told otherwise. `fields`
    replaces top-level values in the stored JSON."""
    report = RunReport(
        run_id="study-run",
        started_at="2026-10-02T03:04:05.678901+00:00",
        dry_run=False,
        passes=3,
        query_ids=_QUERY_IDS,
        arms=_ARMS,
        arm_summaries=summaries or [_summary(arm.name) for arm in _ARMS],
        comparisons=comparisons if comparisons is not None else [
            _comparison(metric, x, y) for x, y in (("Z", "O"), ("Z", "A")) for metric in QUALITY_METRICS
        ],
        answering_cost_usd=1.8,
        judge_cost_usd=1.44,
        total_cost_usd=3.24,
        estimated_cost_usd_before_run=None,
        outcomes=[],
    )
    return {**report.model_dump(mode="json"), **fields}


def _sections(markdown: str) -> dict[str, str]:
    """Each `## ` section's text by its heading. The text before the first one is under ""."""
    sections = {"": ""}
    heading = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            heading = line.removeprefix("## ")
            sections[heading] = ""
        else:
            sections[heading] += line + "\n"
    return sections


def _rows(section: str) -> list[list[str]]:
    """The cells of each table row in a section, header rows included and separator rows left out."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("|") and not set(line.strip()) <= set("|-: ")
    ]


def _row(section: str, first_cell: str) -> list[str]:
    matches = [row for row in _rows(section) if row[0] == first_cell]
    assert len(matches) == 1, f"expected one row for {first_cell!r}, found {len(matches)}"
    return matches[0]


def test_inconclusive_is_worded_with_its_sample_size():
    comparisons = [
        _comparison(
            "citation_recall", "Z", "O", k=8, pairs=24, passes=3,
            mean_delta=0.0333333, ci_low=-0.0204, ci_high=0.0871, verdict="inconclusive",
        ),
        _comparison("groundedness", "Z", "O", mean_delta=-0.15, ci_low=-0.25, ci_high=-0.05, verdict="difference"),
        _comparison(
            "must_contain", "Z", "O", k=0, pairs=0,
            mean_delta=None, ci_low=None, ci_high=None, verdict="no data", per_query_delta={},
        ),
    ]

    section = _sections(render_markdown(_report(comparisons)))["Z against O"]

    # Deltas and interval bounds to 3 places, signed; the verdict carries the sample it was reached at.
    assert _row(section, "Citation recall") == [
        "Citation recall", "8 queries × 3 passes, 24 pairs", "+0.033", "[-0.020, +0.087]",
        "inconclusive at 8 queries × 3 passes",
    ]
    assert _row(section, "Groundedness")[2:] == [
        "-0.150", "[-0.250, -0.050]", "difference at 10 queries × 3 passes",
    ]
    # Nothing to compare is shown as nothing, never as a zero that reads as no difference.
    assert _row(section, "must_contain pass rate")[1:] == [
        "0 queries × 3 passes, 0 pairs", "—", "—", "no data",
    ]


def test_every_per_query_delta_is_published():
    pairs = [("Z", "O"), ("Z", "A")]

    def delta(pair: int, metric: int, query: int) -> float:
        # Distinct for every pair, metric and query, so each cell can only have come from its own.
        return round((-1) ** query * (0.4 * pair + 0.1 * metric + 0.007 * query + 0.001), 6)

    comparisons = [
        _comparison(metric, x, y, per_query_delta={
            query_id: delta(p, m, _QUERY_IDS.index(query_id)) for query_id in _SCOPE[metric]
        })
        for p, (x, y) in enumerate(pairs)
        for m, metric in enumerate(QUALITY_METRICS)
    ]

    sections = _sections(render_markdown(_report(comparisons)))

    for comparison in comparisons:
        section = sections[f"{comparison.x} against {comparison.y}"]
        header = _row(section, "Query")
        column = header.index({
            "groundedness": "Groundedness",
            "citation_recall": "Citation recall",
            "citation_precision": "Citation precision",
            "must_contain": "must_contain pass rate",
        }[comparison.metric])

        for query_id in _QUERY_IDS:
            cell = _row(section, query_id)[column]
            if query_id in comparison.per_query_delta:
                assert cell == f"{comparison.per_query_delta[query_id]:+.3f}"
            else:
                # Out of the metric's scope: there is no delta to publish, and none is made up.
                assert cell == "—"


def test_descriptive_figures_carry_no_verdict():
    summaries = [
        _summary("A", p50_latency_ms=20_862.6, p95_latency_ms=22_647.4, mean_cost_usd_per_query=0.07004, total_cost_usd=2.1012),
        # Every outcome failed: no answer, so no latency or cost per query to report.
        _summary("Z", p50_latency_ms=None, p95_latency_ms=None, mean_cost_usd_per_query=None, total_cost_usd=0.0),
        _summary("O"),
    ]

    section = _sections(render_markdown(_report(summaries=summaries)))["Latency and cost per arm"]

    header, *rows = _rows(section)
    assert header == ["Arm", "p50 latency", "p95 latency", "Mean cost per query", "Answering cost"]
    assert "verdict" not in section.lower()
    assert "inconclusive" not in section and "difference" not in section

    # Latency in whole milliseconds, cost to 4 places.
    assert rows == [
        ["A", "20,863 ms", "22,647 ms", "$0.0700", "$2.1012"],
        ["Z", "—", "—", "—", "$0.0000"],
        ["O", "10,000 ms", "20,000 ms", "$0.0200", "$0.6000"],
    ]


def test_the_header_states_date_sample_and_auth():
    markdown = render_markdown(_report())
    sections = _sections(markdown)

    assert "2026-10-02" in sections[""]
    assert "10 queries × 3 passes" in sections[""]

    assert _rows(sections["Arms"]) == [
        ["Arm", "Expected provider", "URL"],
        ["A", "`anthropic`", "http://localhost:8081"],
        ["Z", "`azure-openai`", "http://localhost:8082"],
        ["O", "`openai`", "http://localhost:8083"],
    ]
    assert "Z authenticates as the owner (Azure CLI), not as the deployed managed identity" in markdown

    # The sample is the run's own, not the study's design written in.
    smaller = render_markdown(_report(query_ids=_QUERY_IDS[:4], passes=2))
    assert "4 queries × 2 passes" in _sections(smaller)[""]


def test_models_seen_and_filter_events_are_listed():
    summaries = [
        _summary("A", models_seen=["claude-sonnet-5"]),
        _summary("Z", error_count=1, filtered_count=2),
        _summary("O", models_seen=["gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"]),
    ]

    section = _sections(render_markdown(_report(summaries=summaries)))["Outcomes, filter events and models"]

    assert _rows(section) == [
        ["Arm", "Outcomes", "Errors", "No reply", "Filtered", "Models seen"],
        ["A", "30", "0", "0", "0", "`claude-sonnet-5`"],
        ["Z", "30", "1", "0", "2", "`gpt-4.1-mini-2025-04-14`"],
        ["O", "30", "0", "0", "0", "`gpt-4.1-mini`, `gpt-4.1-mini-2025-04-14`"],
    ]


def test_failed_judgements_are_stated_per_arm():
    summaries = [
        _summary("A"),
        _summary("Z", groundedness_count=28, judge_failure_count=2),
        _summary("O"),
    ]

    section = _sections(render_markdown(_report(summaries=summaries)))["Quality per arm"]

    assert _row(section, "Arm")[1:3] == ["Groundedness", "Judgements failed"]
    assert _row(section, "Z")[1:3] == ["0.900 (n = 28)", "2"]
    assert _row(section, "A")[1:3] == ["0.900 (n = 30)", "0"]
    assert "came back with no score" in section


def test_a_run_whose_every_judgement_failed_does_not_say_nothing_was_judged():
    summaries = [_summary(arm.name, mean_groundedness=None, groundedness_count=0, judge_failure_count=30) for arm in _ARMS]

    method = _sections(render_markdown(_report(summaries=summaries, judge_cost_usd=0.0, total_cost_usd=1.8)))["Method"]

    assert "blinded to the arm" in method
    assert "Nothing was judged" not in method


def test_a_comparison_short_of_its_nominal_size_says_so():
    comparisons = [
        _comparison("groundedness", "Z", "O"),
        _comparison("citation_recall", "Z", "O", k=7, pairs=19),
        # Every query kept, one pass of one lost.
        _comparison("citation_precision", "Z", "O", pairs=23),
        _comparison("must_contain", "Z", "O", k=3, pairs=9),
    ]

    section = _sections(render_markdown(_report(comparisons)))["Z against O"]
    caveats = {
        line.split("**")[1].removesuffix(":"): line
        for line in section.splitlines()
        if line.startswith("- **")
    }

    # A comparison at its full size has nothing to caveat.
    assert set(caveats) == {"Citation recall", "Citation precision", "must_contain pass rate"}
    assert "k = 7 of 8 queries, 19 of 24 pairs" in caveats["Citation recall"]
    assert "k = 8 of 8 queries, 23 of 24 pairs" in caveats["Citation precision"]
    assert "k = 3 of 6 queries, 9 of 18 pairs" in caveats["must_contain pass rate"]

    # Short of queries: the interval is over fewer queries than planned, and small k is to blame.
    for name in ("Citation recall", "must_contain pass rate"):
        assert "weaker than its nominal 95%" in caveats[name]
        assert "at k ≤ 3 it equals the range of the per-query deltas" in caveats[name]

    # Short of pairs only: every query is in, so k is not what is short.
    pairs_only = caveats["Citation precision"]
    assert "small k" not in pairs_only and "k ≤ 3" not in pairs_only
    assert "Every query in scope is compared" in pairs_only
    assert "fewer passes" in pairs_only


@pytest.mark.parametrize(
    ("mean_delta", "ci", "verdict", "printed"),
    [
        # A bound of +0.000 at 3 places, beside a verdict whose interval excludes zero.
        (0.15, (0.0004, 0.2), "difference", ("+0.150", "[+0.0004, +0.200]")),
        (-0.15, (-0.2, -0.0004), "difference", ("-0.150", "[-0.200, -0.0004]")),
        # A mean of +0.100 at 3 places, beside an inconclusive verdict.
        (0.0996, (0.05, 0.15), "inconclusive", ("+0.0996", "[+0.050, +0.150]")),
        (-0.0995, (-0.15, -0.05), "inconclusive", ("-0.0995", "[-0.150, -0.050]")),
        (0.1004, (0.05, 0.15), "difference", ("+0.1004", "[+0.050, +0.150]")),
        # On a boundary exactly, so 3 places are already the whole truth.
        (0.1, (0.05, 0.15), "difference", ("+0.100", "[+0.050, +0.150]")),
        (0.15, (0.0, 0.2), "inconclusive", ("+0.150", "[+0.000, +0.200]")),
    ],
    ids=["low-bound-above-zero", "high-bound-below-zero", "mean-under-0.10", "mean-over-minus-0.10",
         "mean-over-0.10", "mean-exactly-0.10", "bound-exactly-zero"],
)
def test_a_figure_rounding_onto_a_boundary_shows_which_side_it_is_on(mean_delta, ci, verdict, printed):
    """At 3 places, 0.0004 and 0.0996 print as +0.000 and +0.100, and the page says an interval
    touching zero, or a mean short of 0.10, is inconclusive."""
    comparison = _comparison(
        "citation_precision", "Z", "O", mean_delta=mean_delta, ci_low=ci[0], ci_high=ci[1], verdict=verdict,
    )

    section = _sections(render_markdown(_report([comparison])))["Z against O"]

    assert tuple(_row(section, "Citation precision")[2:4]) == printed


def test_per_query_deltas_stay_at_3_places():
    """Only the figures the rule reads, the mean and the bounds, show more places."""
    section = _sections(render_markdown(_report([
        _comparison("citation_precision", "Z", "O", per_query_delta={"gq-001": 0.0996, "gq-002": 0.0004}),
    ])))["Z against O"]

    assert _row(section, "gq-001")[1] == "+0.100"
    assert _row(section, "gq-002")[1] == "+0.000"


_TWO_MODELS = "compares two different models, and does not test the keyless claim"
_SNAPSHOT = "may not be the same snapshot as Azure's `2025-04-14`"


def _sections_with(a: list[str], z: list[str], o: list[str]) -> dict[str, str]:
    summaries = [_summary("A", models_seen=a), _summary("Z", models_seen=z), _summary("O", models_seen=o)]
    return _sections(render_markdown(_report(summaries=summaries)))


def test_the_claims_comparison_with_one_model_name_says_neither():
    section = _sections_with(["claude-sonnet-5"], ["gpt-4.1-mini-2025-04-14"], ["gpt-4.1-mini-2025-04-14"])["Z against O"]

    assert _TWO_MODELS not in section
    assert "snapshot" not in section


@pytest.mark.parametrize(
    "o_models",
    [["gpt-4.1-mini"], ["gpt-4.1-mini-2025-08-01"], ["gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"]],
    ids=["undated", "another-snapshot", "a-mix-across-passes"],
)
def test_the_claims_comparison_records_a_snapshot_difference_and_still_tests_the_claim(o_models):
    """Z against O is the same model through two auth paths, whatever name OpenAI's reply gives it.
    A different name is a difference spec §8 asks to be recorded, not a reason the comparison
    stops testing the claim."""
    section = _sections_with(["claude-sonnet-5"], ["gpt-4.1-mini-2025-04-14"], o_models)["Z against O"]

    assert _TWO_MODELS not in section
    assert _SNAPSHOT in section
    sentence = next(line for line in section.splitlines() if _SNAPSHOT in line)
    assert "`gpt-4.1-mini-2025-04-14`" in sentence
    assert all(f"`{model}`" in sentence for model in o_models)


@pytest.mark.parametrize(
    ("a_models", "z_models"),
    [
        (["claude-sonnet-5"], ["gpt-4.1-mini-2025-04-14"]),
        # Keyed on the providers, not the names: no names, or the same names, change nothing.
        ([], ["gpt-4.1-mini-2025-04-14"]),
        (["gpt-4.1-mini-2025-04-14"], ["gpt-4.1-mini-2025-04-14"]),
    ],
    ids=["different-names", "no-names", "same-names"],
)
def test_a_comparison_outside_the_claim_says_it_compares_two_models(a_models, z_models):
    """Z against A compares gpt-4.1-mini with Claude Sonnet 5, so it cannot test whether Azure
    without a key matches OpenAI with one (spec §8)."""
    section = _sections_with(a_models, z_models, ["gpt-4.1-mini-2025-04-14"])["Z against A"]

    assert _TWO_MODELS in section
    assert "snapshot" not in section


def test_the_method_says_filtered_answers_are_scored():
    method = _sections(render_markdown(_report()))["Method"]

    assert "A filtered answer is scored as the fixed filtered reply" in method
    assert "counted per arm" in method


def test_a_dry_run_is_refused():
    dry_run = _report(
        comparisons=[], dry_run=True, estimated_cost_usd_before_run=4.2,
        estimate_note="The judge's output share is an allowance of 512 tokens per judgement, not a measurement.",
    )

    with pytest.raises(ValueError, match="pricing"):
        render_markdown(dry_run)


def test_a_report_from_before_the_study_is_refused():
    # The shape every report had before the three-arm harness: one pooled mean per metric.
    pooled = {
        "run_id": "64b3ee8b", "started_at": "2026-08-12T02:25:01+00:00", "query_count": 5,
        "mean_citation_recall": 1.0, "mean_citation_precision": 0.669, "mean_groundedness": 0.94,
        "unanswerable_accuracy": 1.0, "must_contain_pass_rate": 1.0, "p50_latency_ms": 20863.0,
        "p95_latency_ms": 22647.0, "total_cost_usd": 0.2367, "estimated_cost_usd_before_run": None,
        "outcomes": [],
    }

    with pytest.raises(ValueError, match="64b3ee8b.*arms"):
        render_markdown(pooled)


def test_the_method_states_the_rule_and_how_it_reads_values():
    method = _sections(render_markdown(_report()))["Method"]

    assert "settled to 12 decimal places" in method
    assert "so float error cannot flip a verdict at −0.10, +0.10 or 0" in method
    assert "blinded to the arm" in method

    # A run that paid for no judgement does not claim groundedness was judged.
    unjudged = _sections(render_markdown(_report(judge_cost_usd=0.0, total_cost_usd=1.8)))["Method"]
    assert "blinded to the arm" not in unjudged
    assert "Nothing was judged in this run" in unjudged


def test_the_run_cost_shows_answering_judging_and_total():
    header = _sections(render_markdown(_report(answering_cost_usd=1.2344, judge_cost_usd=0.5, total_cost_usd=1.7344)))[""]

    assert "answering $1.2344" in header
    assert "judging $0.5000" in header
    assert "total $1.7344" in header


def test_the_run_cost_says_what_it_leaves_out():
    """A request that got no reply records $0, though the arm may have billed for it."""
    summaries = [_summary("A"), _summary("Z", error_count=3, no_reply_count=2), _summary("O", error_count=1, no_reply_count=1)]

    sections = _sections(render_markdown(_report(summaries=summaries)))

    assert "The total excludes any spend on 3 requests that got no reply" in sections[""]
    assert _row(sections["Outcomes, filter events and models"], "Arm") == [
        "Arm", "Outcomes", "Errors", "No reply", "Filtered", "Models seen",
    ]
    assert _row(sections["Outcomes, filter events and models"], "Z")[1:4] == ["30", "3", "2"]

    one = _sections(render_markdown(_report(summaries=[_summary("A", error_count=1, no_reply_count=1), _summary("Z"), _summary("O")])))
    assert "The total excludes any spend on 1 request that got no reply" in one[""]

    # Every request got a reply, so nothing is left out and nothing is said.
    assert "no reply" not in _sections(render_markdown(_report()))[""]


def test_the_command_prints_a_saved_run(tmp_path, monkeypatch, capsys):
    report = _report()
    (tmp_path / "study-run.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "dry-run.json").write_text(json.dumps(_report(comparisons=[], dry_run=True)), encoding="utf-8")
    monkeypatch.setattr(study_report, "REPORTS", tmp_path)

    assert study_report.main(["study-run"]) == 0
    assert capsys.readouterr().out == render_markdown(report)

    assert study_report.main(["dry-run"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "pricing" in printed.err

    assert study_report.main(["no-such-run"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "no-such-run" in printed.err
