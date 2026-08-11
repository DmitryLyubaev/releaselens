from __future__ import annotations

from pydantic import BaseModel, Field


class GoldenQuery(BaseModel):
    id: str
    question: str
    category: str
    expected_citations: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    notes: str | None = None


class QueryOutcome(BaseModel):
    id: str
    category: str
    question: str
    answer: str
    citations: list[str]
    citation_recall: float
    citation_precision: float
    groundedness: float | None
    groundedness_reason: str | None
    must_contain_satisfied: bool
    must_not_contain_satisfied: bool
    unanswerable_handled: bool | None
    latency_ms: float
    cost_usd: float
    tokens_in: int
    tokens_out: int
    cache_read_input_tokens: int
    degraded: bool
    unresolved_citation_markers: list[str]
    error: str | None = None


class RunReport(BaseModel):
    run_id: str
    started_at: str
    query_count: int
    mean_citation_recall: float
    mean_citation_precision: float
    mean_groundedness: float | None
    unanswerable_accuracy: float | None
    must_contain_pass_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    total_cost_usd: float
    estimated_cost_usd_before_run: float
    outcomes: list[QueryOutcome]


class RunRequest(BaseModel):
    api_base_url: str = "http://host.docker.internal:8080"
    api_key: str
    k: int = 8
    judge: bool = True
    judge_model: str = "claude-sonnet-5"
    # Takes the first N queries in file order. The golden set is grouped by category with
    # the unanswerable entries last, so a limit small enough to be cheap is also small
    # enough to consist entirely of factual queries — and unanswerable accuracy, the
    # metric the set exists for, would silently report null. Prefer per_category.
    limit: int | None = None

    # Takes the first N of *each* category, so a partial run still reports every metric.
    # A sweep costs real money, so a representative subset is often the right sweep to
    # run, not a compromise on the full one.
    per_category: int | None = None

    dry_run: bool = False
