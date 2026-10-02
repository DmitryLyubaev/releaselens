"""The benchmark's write-up: a saved run rendered as exactly the markdown that is published.

Spec §5.3–§5.7, and nothing more: the three pre-registered verdicts, the line saying no
correction was made for running three, each arm's descriptive figures with the latency and cost
caveats, the breakdown by artefact type, the caveats on determinism and dropped pairs, and every
other pair of arms in an exploratory appendix with no interval.

Every figure is the run's own, from the analysis `run-arms` saved, and only formatting is
applied: accuracies, means and bounds to 3 places (or more, where 3 would put a mean or a bound
on one of the rule's boundaries without its being there), margins to 4, cost per 1,000 queries
to 6, latency in whole milliseconds. Nothing is recomputed, so the page cannot disagree with the
record of the run; the one figure it works out is what embedding the corpus cost, the run's
recorded tokens at the analysis's own rates. A run that did not finish is refused, never
rendered in part.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..analysis import SETTLED_DECIMALS
from .score import ARMS, DETERMINISM_LIMIT, LOCAL_ARMS, THRESHOLD

NO_CORRECTION = "The three comparisons are each made at 95%, with no correction for multiple comparisons."

_NONE = "—"
_TOO_SMALL = "release"

_DESCRIPTIONS = {
    "E1": "BGE-small, local: exact cosine over the stored 384-dimension vectors, the question "
          "embedded with BGE's query prefix",
    "E2": "`text-embedding-3-small`: exact cosine over 1,536-dimension vectors of the corpus",
    "E3": "`text-embedding-3-large`: exact cosine over 3,072-dimension vectors of the corpus",
    "S1": "ReleaseLens today: the app's `HybridRetriever`, run by the Worker's `retrieve`",
    "S2": "AI Search hybrid: keyword (English analyzer) plus vector (`-small`, HNSW), fused by the "
          "service",
    "S3": "S2's query with the semantic ranker (`queryType: semantic`), which reorders its top 50",
}

def render(run: dict) -> str:
    """The write-up of one finished run, from its saved JSON.

    Raises ValueError for a run with an arm failure, or with no analysis.
    """
    analysis = _finished(run)
    lines = [
        *_header(run),
        *_arms(),
        *_method(analysis),
        *_comparisons(analysis),
        *_per_arm(analysis),
        *_latency_and_cost(run, analysis),
        *_by_type(analysis),
        *_caveats(run, analysis),
        *_appendix(analysis),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _finished(run: dict) -> dict:
    run_id = run.get("run_id", "(no run_id)")
    failure = run.get("arm_failure")
    if failure is not None:
        raise ValueError(
            f"run {run_id} did not finish: {failure['arm']} failed on its {failure['pass']} pass "
            f"({failure['error']}), so it is not rendered. Run run-arms again."
        )
    if run.get("analysis") is None:
        reason = f": {run['analysis_error']}" if run.get("analysis_error") else ""
        raise ValueError(f"run {run_id} has no analysis, so it is not rendered{reason}")
    return run["analysis"]


def _header(run: dict) -> list[str]:
    started = datetime.fromisoformat(run["started_at"])
    questions = run["questions"]
    deployments = run["deployments"]
    return [
        f"# Retrieval benchmark — {started.day} {started:%B %Y}",
        "",
        f"- **Run id:** `{run['run_id']}`",
        f"- **Started:** {run['started_at']}",
        f"- **Questions:** {questions['count']:,}, frozen in `{questions['file']}` with SHA-256 "
        f"`{questions['sha256']}`. Each has one artefact as its single right answer.",
        f"- **Corpus:** {run['chunks']:,} chunks, the same for every arm. Each arm returns its top 50 "
        "chunks per question.",
        f"- **Embedding deployments:** `{deployments['E2']}` for E2, S2 and S3, and "
        f"`{deployments['E3']}` for E3, called keyless as the owner through the Azure CLI.",
        f"- **AI Search:** the index `{run['search']['index']}`, REST API `{run['search']['api_version']}`, "
        "keyless: local authentication is off, and every call carries an Entra token.",
        "",
    ]


def _arms() -> list[str]:
    return [
        "## The arms",
        "",
        "| Arm | What it is |",
        "|---|---|",
        *(f"| {arm} | {_DESCRIPTIONS[arm]} |" for arm in ARMS),
        "",
        "The E arms search exactly, so no approximate index blurs the comparison of models. The S arms "
        "run as each system really runs, with its own approximate index.",
        "",
    ]


def _method(analysis: dict) -> list[str]:
    threshold = f"{THRESHOLD:.2f}"
    return [
        "## Method",
        "",
        "- Each arm's ranked chunks are collapsed to artefacts by first appearance. Top-1 is 1 when "
        "the first artefact is the target. Reciprocal rank is 1 over the target's rank among the "
        "collapsed artefacts, and 0 when it is not within the 50 chunks. MRR is its mean.",
        "- Lenient top-1 also counts a pull request's merge commit as right for a pull-request "
        "target, and the reverse.",
        "- The margin is the arm's score for its first artefact less its score for the next "
        "artefact, on top-1 hits only. It is within the arm, in the arm's own units, and is not "
        "compared across arms.",
        "- A question an arm errored on is dropped from that arm's figures and from every pair it "
        "is in. It is never scored as a miss.",
        f"- The comparisons pair top-1 by question. The 95% interval is a bootstrap that resamples "
        f"questions: {analysis['resamples']:,} resamples, seed {analysis['seed']}, percentiles by "
        "nearest rank.",
        f"- The decision rule was pre-registered before any data existed. A difference is declared "
        f"only when the paired difference is ≤ −{threshold} or ≥ +{threshold} and its 95% interval "
        "excludes zero; an interval that touches zero does not exclude it. Anything else is "
        "inconclusive at its number of questions.",
        f"- The rule reads the mean and the bounds settled to {SETTLED_DECIMALS} decimal places, so "
        f"float error cannot flip a verdict at −{threshold}, +{threshold} or 0.",
        "",
    ]


def _comparisons(analysis: dict) -> list[str]:
    return [
        "## The pre-registered comparisons",
        "",
        "Each is x − y in top-1 accuracy, over the n questions both arms scored.",
        "",
        "| # | Comparison | n | Dropped | Mean difference in top-1 | 95% CI | Verdict |",
        "|---|---|---:|---:|---:|---|---|",
        *(
            f"| C{number} | {c['x']} − {c['y']} | {c['n']} | {c['dropped']} | {_ruled(c['mean'])} "
            f"| [{_ruled(c['ci_low'])}, {_ruled(c['ci_high'])}] | {_verdict(c)} |"
            for number, c in enumerate(analysis["comparisons"], start=1)
        ),
        "",
        f"{NO_CORRECTION} Each has at most about a 5% chance of declaring a difference that is not "
        "there, so the three together have a greater chance than that of declaring at least one.",
        "",
    ]


def _per_arm(analysis: dict) -> list[str]:
    arms = analysis["arms"]
    return [
        "## Per arm",
        "",
        "Descriptive, with no verdict. Top-1, MRR, lenient top-1 and the margin are over the n "
        "questions the arm scored; dropped is the questions it errored on. The median margin is in "
        "each arm's own units, with its n of top-1 hits.",
        "",
        "| Arm | n | Dropped | Top-1 | MRR | Lenient top-1 | Median margin | p50 latency | p95 latency "
        "| Cost per 1,000 queries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {arm} | {a['n']} | {a['errors']} | {_figure(a['top1'])} | {_figure(a['mrr'])} "
            f"| {_figure(a['lenient_top1'])} | {_margin(a['median_margin'], a['margin_n'])} "
            f"| {_ms(a['p50_ms'])} | {_ms(a['p95_ms'])} | {_cost(arm, a['cost_per_1000'])} |"
            for arm, a in ((arm, arms[arm]) for arm in ARMS)
        ),
        "",
    ]


def _latency_and_cost(run: dict, analysis: dict) -> list[str]:
    rates = analysis["rates"]
    per_million = rates["per_million_tokens"]
    local = " and ".join(LOCAL_ARMS)
    return [
        "**Latency.**",
        "",
        "- It is measured per question by the harness, end to end on each arm, embedding the "
        "question included, over the rows without errors.",
        f"- The local arms ({local}) and the network arms (E2, E3, S2 and S3) are not alike, so their "
        "latencies are not a contest. The local arms run in one process beside the database; the "
        "network arms make HTTPS calls to Azure.",
        "- S2 and S3 add E2's time to embed the question, since they search with E2's vector.",
        f"- {local} open a database connection for each question, inside its time, and q001 pays for "
        "a cold one.",
        "- Each arm's dropped questions are in the table. The arms do not retry alike: E2 and E3 retry "
        "a throttled or failed embedding call inside the question's time, so a retried question is "
        "kept and is slower, while S2 and S3 never retry a search, so a failed search drops the "
        "question from that arm, and from its latency.",
        "",
        "**Cost.**",
        "",
        f"- Cost per 1,000 queries is priced from each arm's measured query tokens, at the rates read "
        f"on {rates['date']} from {rates['source']}: `text-embedding-3-small` at "
        f"${per_million['text-embedding-3-small']:.2f} and `text-embedding-3-large` at "
        f"${per_million['text-embedding-3-large']:.2f} per 1M tokens.",
        "- For E2 and E3 it is per question asked, over every row, errored ones included, since an "
        "embedding that a failure followed was still billed. For S2 and S3 it is per search sent, "
        "leaving out any question E2 gave no vector for, which was never searched.",
        "- S2 and S3 include E2's tokens for embedding the question.",
        f"- Embedding the corpus is a one-time cost, not a cost per query, billed at the rates above: "
        f"{_corpus_cost(run['corpus_tokens'], per_million)}.",
        f"- S3 adds the semantic ranker at ${rates['ranker_per_request']:g} per request, on "
        f"{rates['ranker_plan']}.",
        f"- {local} run locally, with no per-query charge.",
        f"- AI Search {rates['search_tier']} costs ${rates['search_per_hour']:.3f} per hour of service. "
        "It is a fixed cost whatever the number of queries, so it is reported here and not in the "
        "cost per 1,000 queries.",
        "",
    ]


def _by_type(analysis: dict) -> list[str]:
    by_type = analysis["by_type"]
    lines = [
        "## By artefact type",
        "",
        "Descriptive, with no verdict. Each figure has its n, the questions of that type the arm "
        f"scored. Releases are labelled too small to read: n = {by_type.get(_TOO_SMALL, {}).get('questions', 0)} "
        "supports no reading.",
        "",
    ]
    for key, title in (("top1", "Top-1"), ("mrr", "MRR"), ("lenient_top1", "Lenient top-1")):
        lines += [
            f"### {title}",
            "",
            f"| Type | Questions | {' | '.join(ARMS)} |",
            f"|---|---:|{'---:|' * len(ARMS)}",
            *(
                f"| {_type_label(entity_type)} | {figures['questions']} | "
                + " | ".join(_counted(figures[arm][key], figures[arm]["n"]) for arm in ARMS)
                + " |"
                for entity_type, figures in by_type.items()
            ),
            "",
        ]
    return lines


def _caveats(run: dict, analysis: dict) -> list[str]:
    repeated = run["repeat_first"]
    determinism = analysis["determinism"]
    arms = analysis["arms"]
    return [
        "## Caveats",
        "",
        "**Determinism.**",
        "",
        f"The first {repeated} questions were run a second time on every arm. E1's and S1's second "
        f"pass is a second full Worker run, of which the first {repeated} questions are compared. "
        "S2's and S3's second pass searches with E2's first-pass vectors, so it embeds nothing again. "
        f"More than {DETERMINISM_LIMIT} changes on an arm is a caveat on that arm. A question that "
        "errored on either pass is not compared.",
        "",
        *(f"- {_determinism(arm, determinism[arm], repeated)}" for arm in ARMS),
        "",
        "**Pairs dropped.**",
        "",
        "A question an arm errored on is dropped from that arm, and from each comparison with it.",
        "",
        *(f"- {arm}: {_count(arms[arm]['errors'], 'question')} dropped." for arm in ARMS),
        *(
            f"- C{number} ({c['x']} − {c['y']}): {_count(c['dropped'], 'pair')} dropped, leaving {c['n']}."
            for number, c in enumerate(analysis["comparisons"], start=1)
        ),
        "",
    ]


def _appendix(analysis: dict) -> list[str]:
    return [
        "## Appendix: exploratory pairs",
        "",
        "Exploratory: every other pair of arms, as a plain difference in top-1 accuracy over the "
        "questions both arms scored. These have no interval and no verdict, and support no claim.",
        "",
        "| Pair | n | Difference in top-1 |",
        "|---|---:|---:|",
        *(f"| {e['x']} − {e['y']} | {e['n']} | {_signed(e['difference'])} |" for e in analysis["exploratory"]),
        "",
    ]


def _corpus_cost(corpus_tokens: Mapping, per_million: Mapping) -> str:
    return "; ".join(
        f"`{model}` {figures['tokens']:,} tokens, ${figures['tokens'] * per_million[model] / 1_000_000:.6f}"
        for model, figures in corpus_tokens.items()
    )


def _verdict(comparison: Mapping) -> str:
    if comparison["verdict"] == "difference":
        return "difference"
    return f"inconclusive at {_count(comparison['n'], 'question')}"


def _determinism(arm: str, counts: Mapping, repeated: int) -> str:
    text = f"{arm}: {counts['changed']} of {counts['compared']} top-1 results changed"
    if counts["caveat"]:
        text += f" (more than {DETERMINISM_LIMIT}: a caveat on {arm})"
    if counts["compared"] < repeated:
        text += f"; {repeated - counts['compared']} of the {repeated} were not compared, because a pass errored"
    return text + "."


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _type_label(entity_type: str) -> str:
    return f"{entity_type} (too small to read)" if entity_type == _TOO_SMALL else entity_type


def _minus(text: str) -> str:
    # The text's own minus sign, U+2212, so a figure reads as the method's "−0.05" does.
    return text.replace("-", "−")


def _signed(value: float | None) -> str:
    # Rounded first, so that a residue such as -1e-17 on a difference of zero prints as +0.000.
    return _NONE if value is None else _minus(f"{round(value, 3) + 0.0:+.3f}")


def _ruled(value: float | None) -> str:
    """A mean or a bound as the rule reads it: to 3 places, unless that lands exactly on 0, +0.05
    or −0.05 without the value being on it, when it is shown to the 12 places it is settled to,
    trailing zeros trimmed, so the page never puts a figure on the wrong side of the line."""
    if value is None:
        return _NONE
    rounded = round(value, 3) + 0.0
    if rounded in (0.0, THRESHOLD, -THRESHOLD) and value != rounded:
        return _minus(f"{value:+.{SETTLED_DECIMALS}f}".rstrip("0"))
    return _signed(value)


def _figure(value: float | None) -> str:
    return _NONE if value is None else f"{value:.3f}"


def _counted(value: float | None, n: int) -> str:
    return f"{_figure(value)} (n = {n})"


def _margin(value: float | None, n: int) -> str:
    return f"{_NONE if value is None else f'{value:.4f}'} (n = {n})"


def _ms(value: float | None) -> str:
    return _NONE if value is None else f"{value:,.0f} ms"


def _cost(arm: str, value: float) -> str:
    return "$0, local" if arm in LOCAL_ARMS else f"${value:.6f}"
