from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator


class GoldenQuery(BaseModel):
    id: str
    question: str
    category: str
    expected_citations: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    notes: str | None = None


class CitedEvidence(BaseModel):
    """One artefact the answer cited, with the text the system actually showed the model.

    `id` is the same "type:key" identifier citation_recall and citation_precision are
    computed over, so the judge and the deterministic metrics are looking at one set of
    artefacts described two ways rather than at two lists that could drift.

    `text` holds every fragment of that artefact — an artefact split across chunks shares
    one marker, and a claim supported by its third chunk reads as unsupported to a judge
    shown only the first. Empty means the API returned the artefact with no attributable
    text, which the prompt says out loud rather than passing off as "no evidence".
    """

    marker: int
    id: str
    title: str
    url: str
    text: list[str] = Field(default_factory=list)


class QueryOutcome(BaseModel):
    id: str

    # Every arm answers every query, so `id` alone no longer names one outcome; the query,
    # the arm and the pass together do.
    arm: str
    pass_index: int

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

    # Kept apart from cost_usd, which is what the API reported for answering. Judging is the
    # harness's cost, not the arm's: folded in, it would be charged to every arm's cost per
    # query as if the system under test had spent it.
    judge_cost_usd: float = 0.0

    tokens_in: int
    tokens_out: int
    cache_read_input_tokens: int
    degraded: bool
    unresolved_citation_markers: list[str]

    # Who the reply says answered: `provider` and `model` as its metadata names them,
    # `providers` every provider that answered, and `filtered_stage` the stage at which a
    # content filter blocked the request, when one did.
    provider: str | None = None
    providers: list[str] = Field(default_factory=list)
    model: str | None = None
    filtered_stage: str | None = None

    error: str | None = None


class RunReport(BaseModel):
    run_id: str
    started_at: str
    query_count: int
    mean_citation_recall: float
    mean_citation_precision: float
    mean_groundedness: float | None

    # How many queries the groundedness mean is actually over. A judgement that could not be
    # parsed scores None rather than zero — deliberately, so a harness fault never masquerades
    # as a hallucinating system — but that leaves the mean computed over the survivors. One run
    # reported groundedness 1.000 from two scored queries out of five, which reads as a perfect
    # score and is not one. Report the denominator so it cannot.
    groundedness_scored_count: int
    unanswerable_accuracy: float | None
    must_contain_pass_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    total_cost_usd: float
    estimated_cost_usd_before_run: float
    outcomes: list[QueryOutcome]


class Arm(BaseModel):
    """One configuration under test: a separate ReleaseLens API process at its own URL.

    `expected_provider` is the provider that process was started with, as the reply's
    metadata names it. The arm is the process, not a parameter of the query: `/query`
    takes no provider, so the only way to put a question to a given provider is to send it
    to the process configured with that provider alone.
    """

    name: str
    base_url: str
    expected_provider: str


class RunRequest(BaseModel):
    # Each query is put to every arm, in this order.
    arms: list[Arm] = Field(min_length=1)
    passes: int = Field(default=1, ge=1)

    # Each pair (x, y) is reported as the delta x − y, by arm name.
    comparisons: list[tuple[str, str]] = Field(default_factory=list)

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

    @model_validator(mode="after")
    def _the_study_can_be_carried_out(self) -> Self:
        """Reject a study that is malformed before any query is sent, and so before any spend.

        Outcomes are tagged and comparisons are keyed by arm name, so two arms sharing a name
        would pool two processes' answers as if one had given them all. A comparison naming
        an arm that is not in the run, or one arm against itself, has no delta to report.
        Each would otherwise be discovered only after the sweep had been paid for.

        Checks the study's shape only. Whether each arm's URL answers, and from the
        provider it claims, is not known until a query is sent to it.
        """
        names: set[str] = set()
        for arm in self.arms:
            if arm.name in names:
                raise ValueError(f"duplicate arm name {arm.name!r}; each arm needs its own name")
            names.add(arm.name)

        for pair in self.comparisons:
            for name in pair:
                if name not in names:
                    raise ValueError(f"comparison {pair!r} names unknown arm {name!r}")
            if pair[0] == pair[1]:
                raise ValueError(f"comparison {pair!r} compares arm {pair[0]!r} with itself")

        return self
