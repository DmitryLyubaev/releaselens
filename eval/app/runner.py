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
from .models import QueryOutcome, RunReport, RunRequest

# Sonnet 5 introductory input pricing, USD per million tokens. Reverts to 3.00 on
# 1 September 2026 — see ModelPricing in the .NET side for the authoritative table.
_JUDGE_INPUT_USD_PER_MTOK = 2.00


async def run_eval(request: RunRequest) -> RunReport:
    queries = load_golden()
    if request.limit:
        queries = queries[: request.limit]

    outcomes: list[QueryOutcome] = []

    async with httpx.AsyncClient(timeout=180.0) as client:
        for query in queries:
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{request.api_base_url}/query",
                    headers={"X-Api-Key": request.api_key},
                    json={"question": query.question, "k": request.k},
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
            citations = [f"{c['type']}:{c['key']}" for c in body["citations"]]

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
        scorable = [(o.question, o.answer, o.citations) for o in outcomes if not o.error]

        # Free, and it prices the sweep before any money is spent on it.
        estimated_tokens = await judge.estimate_cost(scorable)
        estimated_judge_cost = estimated_tokens / 1_000_000 * _JUDGE_INPUT_USD_PER_MTOK

        if not request.dry_run:
            for outcome in outcomes:
                if outcome.error:
                    continue
                score, reason = await judge.score(outcome.question, outcome.answer, outcome.citations)
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
