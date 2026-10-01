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
from .models import Arm, CitedEvidence, GoldenQuery, QueryOutcome, RunReport, RunRequest

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


def arm_order(arms: list[Arm], pass_index: int) -> list[Arm]:
    """The order the arms answer each query in this pass: the list rotated left by the pass.

    Each arm takes each position once in every len(arms) passes. Whatever going first or
    last on a query does to an answer, such as meeting a cold database cache, then does not
    fall on the same arm every time. With three arms and three passes the order is A,Z,O,
    then Z,O,A, then O,A,Z.
    """
    shift = pass_index % len(arms)
    return arms[shift:] + arms[:shift]


def provider_error(arm: Arm, metadata: dict) -> str | None:
    """Why this reply cannot count as the arm's answer, or None when it can.

    An arm is a process started with one provider and nothing to fall back to, so a reply
    answered by any other provider, by more than one, or by none at all did not come from
    the configuration under test. Scored, it would be counted as that arm's quality.

    A content filter from the arm's own provider is the exception. That provider blocked the
    request rather than answering it, which is a result about the provider, recorded as
    filtered rather than as an error.

    Checks who answered only. An answer from the right provider can still be degraded, and
    that is the caller's to check.
    """
    providers = metadata.get("providers") or []
    if providers == [arm.expected_provider]:
        return None

    filtered = metadata.get("filtered")
    if filtered and filtered.get("provider") == arm.expected_provider:
        return None

    answered = ", ".join(providers)
    return f"arm {arm.name} expected {arm.expected_provider}; answered by {answered or 'none'}"


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


def _answered_by(metadata: dict) -> dict:
    """Who the reply says answered, as the outcome records it whether or not it is scored."""
    filtered = metadata.get("filtered")
    return {
        "provider": metadata.get("provider"),
        "providers": metadata.get("providers") or [],
        "model": metadata.get("model"),
        "filtered_stage": filtered.get("stage") if filtered else None,
    }


def _spend(metadata: dict | None) -> dict:
    """What the reply says answering cost, or nothing when there was no reply."""
    if metadata is None:
        return {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "cache_read_input_tokens": 0}
    return {
        "cost_usd": float(metadata["costUsd"]),
        "tokens_in": metadata["tokensIn"],
        "tokens_out": metadata["tokensOut"],
        "cache_read_input_tokens": metadata["cacheReadInputTokens"],
    }


def _unscored(
    query: GoldenQuery, arm: Arm, pass_index: int, latency_ms: float, error: str,
    metadata: dict | None = None,
) -> QueryOutcome:
    """An outcome with no score: the request failed, or its reply cannot count as the arm's.

    Every quality metric is zero or None whatever the reply held, so nothing downstream can
    count it as a quality result, and `error` says why. When there was a reply, who answered
    and what it cost are kept from it. The first is what the operator needs to see what went
    wrong. The second was billed whether or not the answer counts, so dropping it would
    understate what the run spent.

    Refuses an empty `error`. An empty message tells the operator nothing, and any check
    written as truthiness rather than `error is not None` reads it as no error at all. An
    httpx timeout's own message is empty, so recording it alone made a timeout look like an
    answer.
    """
    if not error:
        raise ValueError("an unscored outcome needs an error saying why it has no score")

    return QueryOutcome(
        id=query.id, arm=arm.name, pass_index=pass_index,
        category=query.category, question=query.question,
        answer="", citations=[], citation_recall=0.0, citation_precision=0.0,
        groundedness=None, groundedness_reason=None,
        must_contain_satisfied=False, must_not_contain_satisfied=False,
        unanswerable_handled=None,
        latency_ms=latency_ms,
        degraded=bool((metadata or {}).get("degraded", False)), unresolved_citation_markers=[],
        error=error,
        **_spend(metadata),
        **_answered_by(metadata or {}),
    )


async def _ask(
    client: httpx.AsyncClient, request: RunRequest, query: GoldenQuery, arm: Arm, pass_index: int,
) -> tuple[QueryOutcome, list[CitedEvidence] | None]:
    """Put one query to one arm, and score the reply if it is that arm's answer.

    Returns the evidence the answer cited beside the outcome, for the judge. It is None when
    the outcome carries an error, because there is then no answer to judge.
    """
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{arm.base_url}/query",
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
        latency_ms = (time.perf_counter() - started) * 1000
        # Named by its type as well: an httpx timeout's message is empty.
        return _unscored(query, arm, pass_index, latency_ms, f"{type(exc).__name__}: {exc}"), None

    latency_ms = (time.perf_counter() - started) * 1000
    metadata = body["metadata"]

    # A degraded answer is the retrieved evidence handed back verbatim because the provider
    # became unavailable. No model wrote it, so scoring it would measure retrieval and charge
    # the result to the arm's model. provider_error already catches a provider that never
    # answered; this catches one that answered at least once and then failed.
    error = provider_error(arm, metadata)
    if error is None and metadata["degraded"]:
        reason = metadata.get("degradedReason") or "no reason given"
        error = f"arm {arm.name} returned a degraded answer: {reason}"
    if error is not None:
        return _unscored(query, arm, pass_index, latency_ms, error, metadata), None

    answer = body["answer"]
    citations = citation_ids(body)

    outcome = QueryOutcome(
        id=query.id,
        arm=arm.name,
        pass_index=pass_index,
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
        **_spend(metadata),
        degraded=metadata["degraded"],
        unresolved_citation_markers=metadata["unresolvedCitationMarkers"],
        **_answered_by(metadata),
    )
    return outcome, cited_evidence(body)


def _price_of_the_run(
    request: RunRequest, sample: list[QueryOutcome], selection_size: int, judge_cost_usd: float,
) -> tuple[float | None, str | None]:
    """What the run this dry run precedes will cost, scaled up from what the sample cost.

    That run puts every query in the selection to every arm on every pass. Each arm is priced
    from its own sample, because the arms run different models at different rates. The judge
    is priced for every answer that run gets, from every arm, at the sample's mean per answer.

    Returns the estimate and None, or None and a note naming each arm whose sample had errors
    and how many. Any error refuses the estimate. Cost varies enormously by category, so a
    mean over the categories that did answer is not the arm's cost, and an arm that answered
    nothing has no cost to scale up. Either would understate the run the operator is about
    to authorise.
    """
    holes = []
    for arm in request.arms:
        drawn = [o for o in sample if o.arm == arm.name]
        errors = sum(1 for o in drawn if o.error is not None)
        if errors:
            holes.append(f"arm {arm.name} had {errors} of {len(drawn)} errors")

    if holes:
        return None, "no estimate: " + "; ".join(holes)

    answers_per_arm = selection_size * request.passes

    estimate = sum(
        _mean([o.cost_usd for o in sample if o.arm == arm.name]) * answers_per_arm
        for arm in request.arms
    )
    if sample:
        estimate += judge_cost_usd / len(sample) * answers_per_arm * len(request.arms)

    return estimate, None


async def run_eval(request: RunRequest) -> RunReport:
    selection = load_golden()

    if request.per_category:
        selection = _stratify(selection, request.per_category)
    elif request.limit:
        selection = selection[: request.limit]

    # A dry run is a *sample*, not a simulation. It really does call the API and really
    # does spend money — just on one query per category, once, instead of every query on
    # every pass — and it skips the judge, whose cost is estimated for free with
    # count_tokens. Calling it free would be a lie that costs whoever believed it the price
    # of a whole sweep.
    if request.dry_run:
        queries, passes = _stratify(selection, _DRY_RUN_PER_CATEGORY), 1
    else:
        queries, passes = selection, request.passes

    outcomes: list[QueryOutcome] = []

    # Held beside the outcomes rather than on them. The evidence for one query runs to tens
    # of kilobytes and every outcome is written to reports/<run_id>.json; folding it in
    # would turn a metrics report into an evidence dump nobody can read. The judge's reason
    # for each score still lands on the outcome, which is what a reader of the report needs.
    # Keyed by query, arm and pass: each pass is a fresh answer citing evidence of its own,
    # and each answer is judged against the evidence it cited, never another arm's or
    # another pass's.
    evidence_by_answer: dict[tuple[str, str, int], list[CitedEvidence]] = {}

    async with httpx.AsyncClient(timeout=180.0) as client:
        for pass_index in range(passes):
            for query in queries:
                for arm in arm_order(request.arms, pass_index):
                    outcome, evidence = await _ask(client, request, query, arm, pass_index)
                    outcomes.append(outcome)
                    if evidence is not None:
                        evidence_by_answer[query.id, arm.name, pass_index] = evidence

    estimated_judge_cost = 0.0

    if request.judge:
        judge = GroundednessJudge(request.judge_model)
        scorable = [
            (o.question, o.answer, evidence_by_answer.get((o.id, o.arm, o.pass_index), []))
            for o in outcomes
            if o.error is None
        ]

        # Free, and it prices the sweep before any money is spent on it.
        estimated_tokens = await judge.estimate_cost(scorable)
        estimated_judge_cost = estimated_tokens / 1_000_000 * _JUDGE_INPUT_USD_PER_MTOK

        if not request.dry_run:
            for outcome in outcomes:
                if outcome.error is not None:
                    continue
                score, reason = await judge.score(
                    outcome.question, outcome.answer,
                    evidence_by_answer.get((outcome.id, outcome.arm, outcome.pass_index), []))
                outcome.groundedness = None if score < 0 else score
                outcome.groundedness_reason = reason

    # On a dry run, the number the operator reads is the price of the run they are about to
    # authorise, not the price of the sample that just ran.
    estimated_cost: float | None = estimated_judge_cost
    estimate_note: str | None = None
    if request.dry_run:
        estimated_cost, estimate_note = _price_of_the_run(
            request, outcomes, len(selection), estimated_judge_cost)

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
        groundedness_scored_count=len(scored),
        unanswerable_accuracy=(
            sum(1 for o in unanswerable if o.unanswerable_handled) / len(unanswerable)
            if unanswerable else None
        ),
        must_contain_pass_rate=_mean([1.0 if o.must_contain_satisfied else 0.0 for o in outcomes]),
        p50_latency_ms=latencies["p50"],
        p95_latency_ms=latencies["p95"],
        total_cost_usd=sum(o.cost_usd for o in outcomes),
        estimated_cost_usd_before_run=estimated_cost,
        estimate_note=estimate_note,
        outcomes=outcomes,
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
