from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import httpx

from .golden import load_golden
from .judge import GroundednessJudge
from .metrics import (
    citation_precision,
    citation_recall,
    latency_percentiles,
    unanswerable_correct,
)
from .models import CitedEvidence, QueryOutcome, RunReport, RunRequest

# Sonnet 5 introductory input pricing, USD per million tokens. Reverts to 3.00 on
# 1 September 2026 — see ModelPricing in the .NET side for the authoritative table.
_JUDGE_INPUT_USD_PER_MTOK = 2.00


# A dry run answers one query per category for real before extrapolating. An agentic
# loop's cost cannot be known without running it: the tool calls it chooses, and the
# evidence they return, are what drive the token count. One per category rather than the
# first N, because cost varies enormously by category — a factual lookup resolves in one
# or two tool calls while a temporal or causal question sends the agent hunting. Pricing
# a mixed sweep from a handful of factual queries underestimates it several times over.
_DRY_RUN_PER_CATEGORY = 1


def citation_ids(body: dict) -> list[str]:
    """The "type:key" identifiers citation_recall and citation_precision score.

    Unchanged by the evidence work and deliberately kept separate from it: these two
    metrics are about WHICH artefacts were cited, and adding the text of those artefacts
    must not be able to move them. Whatever the API puts in `evidence` is invisible here.
    """
    return [f"{c['type']}:{c['key']}" for c in body["citations"]]


def cited_evidence(body: dict) -> list[CitedEvidence]:
    """The same artefacts, with the text the judge needs in order to check a claim.

    `evidence` is absent unless the request asked for it, and empty when the artefact
    reached the agent as an identifier with no attributable text. Both come through as an
    empty `text`, which the judge prompt reports out loud rather than passing off as
    evidence that says nothing.
    """
    return [
        CitedEvidence(
            marker=c["marker"],
            id=f"{c['type']}:{c['key']}",
            title=c["title"],
            url=c["url"],
            text=c.get("evidence") or [],
        )
        for c in body["citations"]
    ]


def _stratify(queries: list, per_category: int) -> list:
    """First N of each category, in the file's own category order."""
    taken: dict[str, int] = {}
    kept = []

    for query in queries:
        seen = taken.get(query.category, 0)
        if seen < per_category:
            taken[query.category] = seen + 1
            kept.append(query)

    return kept


async def run_eval(request: RunRequest) -> RunReport:
    queries = load_golden()
    full_query_count = len(queries)

    if request.per_category:
        queries = _stratify(queries, request.per_category)
    elif request.limit:
        queries = queries[: request.limit]

    # A dry run is a *sample*, not a simulation. It really does call the API and really
    # does spend money — just on a few queries instead of all of them — and it skips the
    # judge, whose cost is estimated for free with count_tokens. Calling it free would be
    # a lie that costs whoever believed it the price of a whole sweep.
    if request.dry_run:
        queries = _stratify(queries, _DRY_RUN_PER_CATEGORY)

    outcomes: list[QueryOutcome] = []

    # Held beside the outcomes rather than on them. The evidence for one query runs to tens
    # of kilobytes and every outcome is written to reports/<run_id>.json; folding it in
    # would turn a metrics report into an evidence dump nobody can read. The judge's reason
    # for each score still lands on the outcome, which is what a reader of the report needs.
    evidence_by_query: dict[str, list[CitedEvidence]] = {}

    async with httpx.AsyncClient(timeout=180.0) as client:
        for query in queries:
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{request.api_base_url}/query",
                    headers={"X-Api-Key": request.api_key},
                    json={
                        "question": query.question,
                        "k": request.k,
                        # The judge scores whether each claim is supported by the evidence
                        # cited, which is unanswerable from an identifier. Opt-in on the
                        # API, so the harness has to ask; production responses do not carry
                        # it and are not paying for this.
                        "includeEvidence": True,
                    },
                )
                response.raise_for_status()
                body = response.json()
            except Exception as exc:  # noqa: BLE001 — a failed query is a data point, not a crash
                outcomes.append(
                    QueryOutcome(
                        id=query.id, category=query.category, question=query.question,
                        answer="", citations=[], citation_recall=0.0, citation_precision=0.0,
                        groundedness=None, groundedness_reason=None,
                        must_contain_satisfied=False, must_not_contain_satisfied=False,
                        unanswerable_handled=None,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        cost_usd=0.0, tokens_in=0, tokens_out=0, cache_read_input_tokens=0,
                        degraded=False, unresolved_citation_markers=[], error=str(exc),
                    )
                )
                continue

            latency_ms = (time.perf_counter() - started) * 1000
            answer = body["answer"]
            metadata = body["metadata"]
            citations = citation_ids(body)
            evidence_by_query[query.id] = cited_evidence(body)

            outcomes.append(
                QueryOutcome(
                    id=query.id,
                    category=query.category,
                    question=query.question,
                    answer=answer,
                    citations=citations,
                    citation_recall=citation_recall(query.expected_citations, citations),
                    citation_precision=citation_precision(query.expected_citations, citations),
                    groundedness=None,
                    groundedness_reason=None,
                    must_contain_satisfied=all(s in answer for s in query.must_contain),
                    must_not_contain_satisfied=not any(s in answer for s in query.must_not_contain),
                    unanswerable_handled=(
                        unanswerable_correct(answer) if query.category == "unanswerable" else None
                    ),
                    latency_ms=latency_ms,
                    cost_usd=float(metadata["costUsd"]),
                    tokens_in=metadata["tokensIn"],
                    tokens_out=metadata["tokensOut"],
                    cache_read_input_tokens=metadata["cacheReadInputTokens"],
                    degraded=metadata["degraded"],
                    unresolved_citation_markers=metadata["unresolvedCitationMarkers"],
                )
            )

    estimated_judge_cost = 0.0

    if request.judge:
        judge = GroundednessJudge(request.judge_model)
        scorable = [
            (o.question, o.answer, evidence_by_query.get(o.id, []))
            for o in outcomes
            if not o.error
        ]

        # Free, and it prices the sweep before any money is spent on it.
        estimated_tokens = await judge.estimate_cost(scorable)
        estimated_judge_cost = estimated_tokens / 1_000_000 * _JUDGE_INPUT_USD_PER_MTOK

        # On a dry run, scale what the sample actually cost up to the whole set, so the
        # number the operator reads is the price of the run they are about to authorise
        # rather than the price of the three queries that just ran.
        if request.dry_run and outcomes:
            agent_cost_so_far = sum(o.cost_usd for o in outcomes)
            per_query = (agent_cost_so_far + estimated_judge_cost) / len(outcomes)
            estimated_judge_cost = per_query * full_query_count

        if not request.dry_run:
            for outcome in outcomes:
                if outcome.error:
                    continue
                score, reason = await judge.score(
                    outcome.question, outcome.answer, evidence_by_query.get(outcome.id, []))
                outcome.groundedness = None if score < 0 else score
                outcome.groundedness_reason = reason

    scored = [o for o in outcomes if o.groundedness is not None]
    unanswerable = [o for o in outcomes if o.unanswerable_handled is not None]
    latencies = latency_percentiles([o.latency_ms for o in outcomes])

    return RunReport(
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(UTC).isoformat(),
        query_count=len(outcomes),
        mean_citation_recall=_mean([o.citation_recall for o in outcomes]),
        mean_citation_precision=_mean([o.citation_precision for o in outcomes]),
        mean_groundedness=_mean([o.groundedness for o in scored]) if scored else None,
        unanswerable_accuracy=(
            sum(1 for o in unanswerable if o.unanswerable_handled) / len(unanswerable)
            if unanswerable else None
        ),
        must_contain_pass_rate=_mean([1.0 if o.must_contain_satisfied else 0.0 for o in outcomes]),
        p50_latency_ms=latencies["p50"],
        p95_latency_ms=latencies["p95"],
        total_cost_usd=sum(o.cost_usd for o in outcomes),
        estimated_cost_usd_before_run=estimated_judge_cost,
        outcomes=outcomes,
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
