"""The study's write-up: a saved run rendered as exactly the markdown that gets published.

Every figure comes from the stored report, and only formatting is applied: deltas, interval
bounds and means to 3 decimal places, or more where 3 would put a mean delta or a bound on one of
the rule's boundaries without its being there, cost to 4, latency in whole milliseconds. Nothing is
recomputed, so the published page cannot disagree with the record of the run it came from, and
a figure the report does not hold is not published.

Renders a real study run only. A dry run prices the study and carries no comparisons, so a
write-up of one would read as a study that did not happen. A report saved before the three-arm
harness has none of the fields this reads. Both are refused rather than rendered in part.

From eval/, `python -m app.study_report <run_id>` prints the write-up of reports/<run_id>.json.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .analysis import (
    _SETTLED_DECIMALS,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    DIFFERENCE_THRESHOLD,
    ArmSummary,
    Comparison,
)
from .models import RunReport

REPORTS = Path(__file__).resolve().parent.parent / "reports"

_METRIC_NAMES = {
    "groundedness": "Groundedness",
    "citation_recall": "Citation recall",
    "citation_precision": "Citation precision",
    "must_contain": "must_contain pass rate",
}

_NONE = "—"


def render_markdown(report: dict) -> str:
    """The write-up of one study run, from its stored JSON.

    Raises ValueError for a dry run, or for a report that is not a study run's.
    """
    run = _study_run(report)
    summaries = {summary.arm: summary for summary in run.arm_summaries}
    providers = {arm.name: arm.expected_provider for arm in run.arms}

    lines = [
        *_header(run),
        *_arms(run),
        *_method(run),
        *_quality(run.arm_summaries),
        *_outcomes(run.arm_summaries),
        *_latency_and_cost(run.arm_summaries),
    ]

    groups = _comparison_groups(run.comparisons)
    if not groups:
        lines += ["## Comparisons", "", "No comparisons were requested for this run.", ""]
    for group in groups:
        lines += _comparison(group, run.query_ids, summaries, providers)

    return "\n".join(lines).rstrip("\n") + "\n"


def _study_run(report: dict) -> RunReport:
    run_id = report.get("run_id", "(no run_id)")

    try:
        run = RunReport.model_validate(report)
    except ValidationError as error:
        missing = [
            str(detail["loc"][0]) for detail in error.errors()
            if detail["type"] == "missing" and len(detail["loc"]) == 1
        ]
        reason = (
            f"it has no {', '.join(missing)}, so it was saved before the three-arm harness"
            if missing else f"it does not match the study report's shape: {error}"
        )
        raise ValueError(f"report {run_id} is not a study run and cannot be rendered: {reason}") from error

    if run.dry_run:
        raise ValueError(
            f"report {run_id} is a dry run, which is not rendered: dry runs are for pricing the "
            "study, and carry no comparisons. Render the run it priced."
        )

    return run


def _header(run: RunReport) -> list[str]:
    started = datetime.fromisoformat(run.started_at)

    # A request with no reply records $0, though the arm may have gone on and billed for it.
    no_reply = sum(summary.no_reply_count for summary in run.arm_summaries)
    unseen = (
        f" The total excludes any spend on {no_reply} {'request' if no_reply == 1 else 'requests'} "
        "that got no reply, a timeout or a connection error, since no reply said what it cost."
        if no_reply else ""
    )

    return [
        f"# Three-arm study — {started.day} {started:%B %Y}",
        "",
        f"- **Run id:** `{run.run_id}`",
        f"- **Started:** {run.started_at}",
        f"- **Sample:** {_sample(len(run.query_ids), run.passes)} per arm: {', '.join(run.query_ids)}",
        f"- **Cost of the run:** answering {_usd(run.answering_cost_usd)}, "
        f"judging {_usd(run.judge_cost_usd)}, total {_usd(run.total_cost_usd)}. Answering is what "
        "the arms reported over every outcome, rejected replies included, since they were billed. "
        f"Judging is the harness's own cost, and is not charged to any arm.{unseen}",
        "",
    ]


def _arms(run: RunReport) -> list[str]:
    lines = [
        "## Arms",
        "",
        "Each arm is a separate API process started with that provider alone.",
        "",
        "| Arm | Expected provider | URL |",
        "|---|---|---|",
        *(f"| {arm.name} | `{arm.expected_provider}` | {arm.base_url} |" for arm in run.arms),
        "",
    ]

    # The report does not record the credential. The study runs Azure OpenAI locally through
    # AzureCliCredential (spec §8), and spec §8 requires the write-up to say so.
    azure = [arm.name for arm in run.arms if arm.expected_provider == "azure-openai"]
    lines += [
        f"{name} authenticates as the owner (Azure CLI), not as the deployed managed identity."
        for name in azure
    ]
    return [*lines, ""] if azure else lines


def _method(run: RunReport) -> list[str]:
    # A judgement that raised has no usage to price, so a run whose every judgement failed paid
    # nothing for judging and still put its answers to the judge.
    judged = (
        "Groundedness is scored by a Claude Sonnet 5 judge, blinded to the arm."
        if run.judge_cost_usd > 0 or any(s.judge_failure_count for s in run.arm_summaries)
        else "Nothing was judged in this run, so groundedness has no data."
    )
    threshold = f"{DIFFERENCE_THRESHOLD:.2f}"

    return [
        "## Method",
        "",
        "- The unit of analysis is the query. Each query's delta is the mean of x − y over the "
        "passes where both arms have a value, so each arm's passes are averaged before the arms "
        "are compared.",
        "- Each metric is scored only over the queries it applies to: groundedness over every "
        "query, citation recall and precision over the answerable ones, and the must_contain "
        "pass rate over those with something to contain. k is the queries with at least one "
        "pass where both arms have a value.",
        f"- The 95% interval is a bootstrap that resamples queries with their passes kept "
        f"together: {BOOTSTRAP_RESAMPLES:,} resamples, seed {BOOTSTRAP_SEED}, percentiles by "
        "nearest rank.",
        f"- The decision rule, pre-registered in spec §8, applies to the four quality metrics. A "
        f"difference is declared only when the mean paired delta is ≤ −{threshold} or "
        f"≥ +{threshold} and its 95% interval excludes zero; an interval that touches zero does "
        "not exclude it. Anything else is inconclusive at its k queries × passes.",
        f"- The rule reads the mean and the interval bounds settled to 12 decimal places, so "
        f"float error cannot flip a verdict at −{threshold}, +{threshold} or 0.",
        f"- {judged}",
        "- A filtered answer is scored as the fixed filtered reply the API returned in its place, "
        "on every metric that applies to its query, as any other answer is. Filtered answers are "
        "counted per arm under \"Outcomes, filter events and models\", so a delta that filter "
        "events may have driven can be told apart.",
        "- Figures are the report's own, formatted: deltas, intervals and means to 3 decimal "
        "places, cost to 4, latency in whole milliseconds. A mean delta or an interval bound that "
        "3 places would show as exactly 0, +0.10 or −0.10 without being it is shown to as many "
        "places as it takes to say which side it is on.",
        "",
    ]


def _quality(summaries: list[ArmSummary]) -> list[str]:
    return [
        "## Quality per arm",
        "",
        "Descriptive. Each mean is over the arm's outcomes without errors, on the queries the "
        "metric applies to, where the outcome has a value for it; n is how many outcomes that is. "
        "The difference between two arms' means is not a comparison's delta, which is over the "
        "paired outcomes only. Judgements failed counts the answers put to the judge that came "
        "back with no score, because the judge failed or its reply was not a score in [0, 1]; "
        "they are left out of groundedness. Unanswerable handled is reported only, since two "
        "queries cannot support a conclusion.",
        "",
        "| Arm | Groundedness | Judgements failed | Citation recall | Citation precision | must_contain pass rate | Unanswerable handled |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {s.arm} | {_mean(s.mean_groundedness, s.groundedness_count)} "
            f"| {s.judge_failure_count} "
            f"| {_mean(s.mean_citation_recall, s.citation_recall_count)} "
            f"| {_mean(s.mean_citation_precision, s.citation_precision_count)} "
            f"| {_mean(s.mean_must_contain, s.must_contain_count)} "
            f"| {s.unanswerable_handled} of {s.unanswerable_total} |"
            for s in summaries
        ),
        "",
    ]


def _outcomes(summaries: list[ArmSummary]) -> list[str]:
    return [
        "## Outcomes, filter events and models",
        "",
        "Recorded as legitimate differences between the arms, not explained away. No reply counts "
        "the errors on which the request got no reply, whose cost is unknown. Filtered counts "
        "the outcomes on which a content filter blocked the request, errors included. Models seen "
        "is each `model` the arm's outcomes without errors or filter events name, as the replies' "
        "metadata gives it. A filtered answer is left out because no response named its model.",
        "",
        "| Arm | Outcomes | Errors | No reply | Filtered | Models seen |",
        "|---|---:|---:|---:|---:|---|",
        *(
            f"| {s.arm} | {s.outcome_count} | {s.error_count} | {s.no_reply_count} | {s.filtered_count} "
            f"| {', '.join(f'`{model}`' for model in s.models_seen) or 'none'} |"
            for s in summaries
        ),
        "",
    ]


def _latency_and_cost(summaries: list[ArmSummary]) -> list[str]:
    return [
        "## Latency and cost per arm",
        "",
        "Descriptive, with no significance claim. Latency and mean cost per query are over the "
        "outcomes without errors. Answering cost is over all of the arm's outcomes, because a "
        "rejected reply was still billed.",
        "",
        "| Arm | p50 latency | p95 latency | Mean cost per query | Answering cost |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {s.arm} | {_ms(s.p50_latency_ms)} | {_ms(s.p95_latency_ms)} "
            f"| {_usd(s.mean_cost_usd_per_query)} | {_usd(s.total_cost_usd)} |"
            for s in summaries
        ),
        "",
    ]


def _comparison_groups(comparisons: list[Comparison]) -> list[list[Comparison]]:
    """The comparisons in report order, one group per requested pair of arms.

    A pair requested twice is two groups, not one with each metric listed twice.
    """
    groups: list[list[Comparison]] = []
    for comparison in comparisons:
        current = groups[-1] if groups else None
        if (
            current is None
            or (current[0].x, current[0].y) != (comparison.x, comparison.y)
            or any(seen.metric == comparison.metric for seen in current)
        ):
            groups.append([comparison])
        else:
            current.append(comparison)
    return groups


def _models_note(x: str, y: str, summaries: dict[str, ArmSummary], providers: dict[str, str]) -> str:
    """What the comparison's models say about the claim, as a sentence to follow its intro.

    Decided by the arms' providers, never by the model names their replies gave. Azure OpenAI
    against OpenAI is the claim's comparison: the same model, gpt-4.1-mini, through two auth paths.
    Any other pair compares two different models and cannot test the claim (spec §8).

    In the claim's comparison, model names that differ are recorded, as spec §8 asks, and do not
    stop it testing the claim. OpenAI may name the model without a date, or by another snapshot,
    where Azure names its 2025-04-14 deployment. Recorded only where both arms named a model: an
    arm that answered nothing has no name to differ by.
    """
    if {providers.get(x), providers.get(y)} != {"azure-openai", "openai"}:
        return " It compares two different models, and does not test the keyless claim."

    x_models, y_models = (summaries[arm].models_seen if arm in summaries else [] for arm in (x, y))
    if not x_models or not y_models or x_models == y_models:
        return ""

    azure, openai = (x, y) if providers[x] == "azure-openai" else (y, x)
    return (
        f" The replies named the model differently: {azure} (Azure OpenAI) as "
        f"{_models(summaries[azure])}, and {openai} (OpenAI) as {_models(summaries[openai])}. "
        "OpenAI's `gpt-4.1-mini` may not be the same snapshot as Azure's `2025-04-14`. This is "
        "recorded as a difference between the arms, and the comparison still tests the claim."
    )


def _comparison(
    group: list[Comparison], query_ids: list[str], summaries: dict[str, ArmSummary],
    providers: dict[str, str],
) -> list[str]:
    x, y = group[0].x, group[0].y
    names = [_METRIC_NAMES.get(c.metric, c.metric) for c in group]

    lines = [
        f"## {x} against {y}",
        "",
        f"Each delta is {x} − {y}. {x}'s models seen: {_models(summaries.get(x))}. "
        f"{y}'s: {_models(summaries.get(y))}.{_models_note(x, y, summaries, providers)}",
        "",
        "| Metric | Sample | Mean delta | 95% CI | Verdict |",
        "|---|---|---:|---|---|",
        *(
            f"| {name} | {_sample(c.k, c.passes)}, {c.pairs} pairs | {_ruled(c.mean_delta)} "
            f"| {_interval(c)} | {_verdict(c)} |"
            for name, c in zip(names, group)
        ),
        "",
    ]

    short = [(name, c) for name, c in zip(names, group) if c.k < c.nominal_k or c.pairs < c.nominal_pairs]
    if short:
        lines += [
            "Short of the size the study planned for. Pairs drop out where either arm errored, was "
            "not scored, or is missing:",
            "",
            *(
                f"- **{name}:** k = {c.k} of {c.nominal_k} queries, {c.pairs} of {c.nominal_pairs} "
                f"pairs. {_shortfall(c)}"
                for name, c in short
            ),
            "",
        ]

    # Every query of the run gets a row, so one that dropped out of every metric shows as such.
    extra = sorted({q for c in group for q in c.per_query_delta} - set(query_ids))
    lines += [
        f"### Per-query deltas, {x} − {y}",
        "",
        f"{_NONE} means the metric does not apply to the query, or no pass of it was paired.",
        "",
        f"| Query | {' | '.join(names)} |",
        f"|---|{'---:|' * len(group)}",
        *(
            f"| {query_id} | {' | '.join(_signed(c.per_query_delta.get(query_id)) for c in group)} |"
            for query_id in [*query_ids, *extra]
        ),
        "",
    ]
    return lines


def _shortfall(comparison: Comparison) -> str:
    """What a comparison's shortfall does to it. Small k is blamed only when k is what is short."""
    if comparison.k < comparison.nominal_k:
        return (
            "The interval is weaker than its nominal 95% at small k: at k ≤ 3 it equals the range "
            "of the per-query deltas."
        )
    return (
        "Every query in scope is compared, so k is at its planned size; the queries that lost a "
        "pass are averaged over fewer passes."
    )


def _sample(k: int, passes: int) -> str:
    return f"{k} {'query' if k == 1 else 'queries'} × {passes} {'pass' if passes == 1 else 'passes'}"


def _verdict(comparison: Comparison) -> str:
    if comparison.verdict == "no data":
        return "no data"
    return f"{comparison.verdict} at {_sample(comparison.k, comparison.passes)}"


def _signed(value: float | None) -> str:
    # Rounded first so that a residue such as -1e-17 on a delta of zero prints as +0.000.
    return _NONE if value is None else f"{round(value, 3) + 0.0:+.3f}"


def _ruled(value: float | None) -> str:
    """A mean or an interval bound, as the decision rule reads it.

    To 3 places, unless that lands exactly on one of the rule's boundaries, 0, +0.10 or −0.10,
    without the value being on it. A bound of 0.0004 would print as +0.000 beside "difference",
    and a mean of 0.0996 as +0.100 beside "inconclusive", and the page would contradict itself.
    Such a value is printed at the 12 places it is settled to, trailing zeros trimmed, which is
    enough to show which side of the boundary it is on.
    """
    if value is None:
        return _NONE
    rounded = round(value, 3) + 0.0
    if rounded in (0.0, DIFFERENCE_THRESHOLD, -DIFFERENCE_THRESHOLD) and value != rounded:
        return f"{value:+.{_SETTLED_DECIMALS}f}".rstrip("0")
    return _signed(value)


def _interval(comparison: Comparison) -> str:
    if comparison.ci_low is None or comparison.ci_high is None:
        return _NONE
    return f"[{_ruled(comparison.ci_low)}, {_ruled(comparison.ci_high)}]"


def _mean(value: float | None, count: int) -> str:
    return f"{_NONE if value is None else f'{value:.3f}'} (n = {count})"


def _usd(value: float | None) -> str:
    return _NONE if value is None else f"${value:,.4f}"


def _ms(value: float | None) -> str:
    return _NONE if value is None else f"{value:,.0f} ms"


def _models(summary: ArmSummary | None) -> str:
    if summary is None or not summary.models_seen:
        return "none"
    return ", ".join(f"`{model}`" for model in summary.models_seen)


def main(argv: list[str] | None = None) -> int:
    """Print the write-up of reports/<run_id>.json. Returns the exit status."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m app.study_report <run_id>", file=sys.stderr)
        return 2

    path = REPORTS / f"{args[0]}.json"
    if not path.exists():
        print(f"no run {args[0]}: {path} does not exist", file=sys.stderr)
        return 1

    try:
        markdown = render_markdown(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    # The write-up goes into the repository's UTF-8 markdown. Redirected on Windows, stdout would
    # otherwise use the ANSI code page, which cannot encode the ≤ and − it contains.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
