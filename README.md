# ReleaseLens

Natural-language questions over a repository's commits, issues, pull requests and releases,
answered with citations back to the specific artefacts.

Indexed repository: [`microsoft/semantic-kernel`](https://github.com/microsoft/semantic-kernel).

```
Question ──► hybrid retrieval (Postgres full-text + pgvector)
         ──► agent loop with tools ──► synthesis with citations
         ──► answer + retrieval metadata
```

## Why this exists

Plenty of systems answer questions over documents. Fewer can say how they know the answer is
right. This one ships with a golden query set, a metrics harness and a written baseline — and
the numbers below come from a run that happened, with its limits stated rather than smoothed
over.

## Evaluation

Measured 11 August 2026 at commit `3e25a6d`. Full write-up, including method and caveats:
**[eval/baseline.md](eval/baseline.md)**.

> **These numbers are historical, not current.** Since they were taken, the corpus has grown
> from 22,288 chunks to 41,825 and two aggregate tools have been added specifically to fix
> what this run exposed. The measurements below are exactly what was observed at that commit,
> and they are left unedited rather than quietly refreshed — but they describe a system that
> has since changed underneath them. A re-measure is pending.

| Metric | Result |
|---|---|
| Golden query set | 43 queries · 5 categories |
| **Queries in this run** | **5 — one per category** |
| Citation recall | 1.000 |
| Citation precision (answerable queries) | 0.090 |
| must_contain pass rate | 1.000 |
| **Unanswerable handled correctly** | **1 of 1** |
| Groundedness (LLM-judge) | not measured yet |
| p50 / p95 latency | 15,876 ms / 58,028 ms |
| Cost per query | $0.009 – $0.720 |

**Read the sample size before the results.** Five queries, one per category. They are real
measurements rather than estimates, but they are five measurements, and a rate cannot be
derived from them. The run cost $0.92 and priced a full 43-query sweep at $8.01, which was
more than the remaining API balance — the reasoning is recorded in the baseline rather than
left for a reader to guess at.

Three things that table is deliberately not claiming:

- **Groundedness has no number.** The run was a pricing run, which skips the LLM-judge. The
  harness implements the judge; it has not yet been paid to run.
- **Precision is 0.090, not the 0.272 the harness reports.** Recall and precision both return
  a vacuous 1.0 for queries that expect no citations, which is every `unanswerable` entry. The
  figure above is over the four answerable queries only.
- **"1 of 1" is not 100%.** It is one correct refusal.

The row that matters most is the unanswerable one. Eight of the 43 queries have no answer in
the evidence, and the correct response is to say so. A system that scores well on the other
four categories and invents an answer here is confidently wrong, which is worse than being
unable to answer.

### What the baseline found

**Cost spans 77× across categories.** One query — *"List every Java release tag ever
published"* — cost $0.72, took 58 seconds and pushed 337,570 input tokens, which is 78% of the
whole run. At the time there was no counting tool, so an aggregation question could only be
answered by retrieving its way to completeness. Prompt caching served just 6.6% of input
tokens because each iteration moves the cache boundary.

This is the finding that justified `count_evidence` and `list_releases`, which answer that
class of question with one bounded aggregate instead of a retrieval loop. The tools exist
because a measurement said they should, not because they seemed like a good idea — and
whether they actually moved the number is a question for the next run, not a claim for this
one.

**Recall 1.000 against precision 0.090** means retrieval finds the right evidence and the
model then cites nearly everything it saw — 48 artefacts on the query above. Headroom, not a
wall: precision can rise without recall falling.

## What it does that a tutorial RAG does not

**The model gets tools, not just chunks.** `search_commits`, `get_issue`,
`diff_between_releases` and `find_regressions` retrieve evidence; `count_evidence` and
`list_releases` compute over the whole corpus instead of sampling it. It runs its own
follow-up searches when the first pass is not enough. Agentic retrieval, not single-shot.

**Counting is a tool, not a guess.** A retrieval system asked "how many releases shipped in
2024" can only count what it happened to retrieve, and a sample counted is a fabrication with
a number attached. `count_evidence` runs one bounded aggregate and reports the predicate it
applied. It returns no citation, because a computed figure is not an artefact and inventing a
marker for it would be the same dishonesty in a different costume.

**Computed answers carry their own coverage.** The corpus does not span all of history —
commits begin in January 2024. So a count can be true of the evidence and false of the
repository. Both aggregate tools return the date range actually held alongside the number, and
the system prompt requires the answer to say so when the window exceeds it. A confident zero
is the worst output this system could produce.

**Citations are produced by the tools, not parsed out of prose.** Every citation corresponds
to an artefact a tool actually returned, so the model cannot cite something it never saw. A
citation identifies an artefact rather than a chunk, so an entity split across several chunks
is cited once. Answers referencing a marker outside the evidence range are flagged in the
response metadata — across the baseline run, none were.

**Embeddings never leave the machine.** BGE-small-en-v1.5 runs in-process on ONNX Runtime.
384 dimensions, no embedding API, no per-token cost, and no evidence transmitted in order to
index it.

**It can run with zero egress.** The OpenAI adapter takes a configurable base URL, so pointing
it at Ollama, LM Studio or vLLM makes the whole pipeline local. For enterprise evidence that
cannot leave the network, that is the difference between a usable tool and one that is out of
the question.

**Tenant isolation is enforced by the database.** Postgres row-level security on every
evidence table, with the tenant set per transaction and the connecting role's bypass dropped
for the life of it. A query that forgets its `WHERE` clause returns nothing rather than
someone else's data. There are tests that prove it, including one that shows a cross-tenant
foreign key is rejected.

**Failure modes are designed, not discovered.** Provider down → fall back. Both down → return
the retrieved evidence unsynthesised rather than a 500. Retrieval finds fewer than *k* → say
so in the metadata rather than truncate silently. Budget exhausted → 429.

## Corpus

Counts from the running database, 12 August 2026.

| | Count | Earliest | Latest |
|---|---|---|---|
| Commits | 2,921 | 2024-01-02 | 2026-08-10 |
| Issues | 3,805 | 2023-03-21 | 2026-08-09 |
| Pull requests | 7,121 | 2023-02-27 | 2026-08-10 |
| Releases | 276 | 2023-04-25 | 2026-08-06 |
| Files changed | 34,561 | | |
| Chunks | 41,825 | | |
| Embeddings | 41,825 | | |
| Embedding dead letters | 0 | | |

**Coverage starts in 2024, and the system knows it.** `GitHub:SinceUtc` is 2024-01-01, so
commits begin there. Pull requests and releases reach further back because GitHub's `pulls`
and `releases` endpoints take no `since` parameter and therefore always arrive complete. The
aggregate tools report these bounds with every result rather than letting a count that stops
at the ingest boundary pass for a count of the repository.

**A checkpoint beats configuration, which is worth knowing before a backfill.** Widening
`SinceUtc` does nothing on its own: the stored cursor wins, and the run reports success having
ingested almost nothing. That is correct for daily incremental runs and a trap for a backfill,
so `reset-checkpoints` exists to clear the cursor *and* the ETag — a surviving ETag earns a
304 and skips the source entirely.

## Architecture

See [docs/architecture.md](docs/architecture.md).

| Component | Technology |
|---|---|
| Ingestion | .NET 10 worker, GitHub REST behind `IEvidenceSource`, ETag conditional requests, checkpointed and resumable |
| Embedding | BGE-small-en-v1.5 on ONNX Runtime, in-process, 384 dimensions |
| Store | PostgreSQL 17 + pgvector, HNSW index, row-level security |
| Retrieval | Full-text + vector, blended `α·vector + (1−α)·text` with α = 0.6 |
| Agent | Tool-calling loop over Anthropic or any OpenAI-wire-format endpoint |
| API | ASP.NET Core minimal API, API-key auth, per-tenant daily token budget |
| Telemetry | OpenTelemetry → Aspire Dashboard locally, Azure Monitor in cloud |
| Infrastructure | Terraform: Container Apps scale-to-zero, Postgres Flexible Server, Key Vault, budget alerts |
| Evaluation | Python FastAPI harness, golden query set, LLM-judge groundedness |

## Running it

Prerequisites: .NET SDK 10, Docker, a GitHub token with public read access, and an Anthropic
or OpenAI key — or Ollama, if you would rather nothing left the machine.

```bash
docker compose up -d
pwsh scripts/fetch-model.ps1
```

```bash
export RELEASELENS_DB="Host=127.0.0.1;Port=5433;Database=releaselens;Username=releaselens;Password=releaselens_dev_only"
export GITHUB_TOKEN="..."
export ANTHROPIC_API_KEY="..."
```

```bash
dotnet run --project src/ReleaseLens.Worker -- migrate
dotnet run --project src/ReleaseLens.Worker -- create-tenant
dotnet run --project src/ReleaseLens.Worker -- ingest
dotnet run --project src/ReleaseLens.Worker -- issue-key semantic-kernel local
dotnet run --project src/ReleaseLens.Api
```

```bash
dotnet run --project src/ReleaseLens.Worker -- reset-checkpoints semantic-kernel commit issue
```

Needed only when widening the ingest window. The stored checkpoint wins over `GitHub:SinceUtc`,
so moving `SinceUtc` further back does nothing on its own — the next run resumes from where the
last one stopped and quietly returns almost nothing. This clears the cursor and the ETag for the
named entity types, or all four if none are named. It deletes no evidence; re-ingestion upserts.

The API key is printed once and stored only as a SHA-256 hash — it cannot be recovered, only
reissued. `launchSettings.json` chooses the port in development, so use the URL the API
prints rather than assuming 8080.

A full ingest of `microsoft/semantic-kernel` takes a little over an hour, most of it in the
in-process embedder. It is checkpointed, so interrupting it is safe.

### Fully local, no egress

```bash
ollama serve
ollama pull llama3.1
```

```json
{ "OpenAi": { "BaseUrl": "http://localhost:11434/v1/", "Model": "llama3.1" } }
```

Embeddings were already local. With this, nothing leaves the machine.

## Testing

```bash
dotnet test
```

199 tests. Unit tests for chunking, normalisation and the provider adapters against mocked
transports; integration tests against real PostgreSQL via Testcontainers, including the
row-level security proofs.

```bash
cd eval && python -m pytest
```

17 tests over the metrics and the golden set — structure, uniqueness, category validity and
the sampling that keeps a partial sweep representative.

The golden query set itself is the regression corpus and runs on demand, because it costs
money.

## Deployment

```bash
cd infra/terraform
terraform apply -target=azurerm_consumption_budget_subscription.this
terraform apply
```

The budget goes first, deliberately.

**Azure has no hard spending cap on pay-as-you-go subscriptions.** What Terraform deploys here
is a budget with alerts at 50/80/100 percent plus a forecast alert. The control that actually
works is `terraform destroy` when you stop working, and the Container App scales to zero so an
idle deployment costs nothing but the database.

This has been planned and written but not applied to a live subscription.

## What this is not

**It has not been run at scale.** Tenant isolation is designed and tested; it has not been
exercised under concurrent load or with a large number of tenants.

**The evaluation is a starting point.** Five queries, no groundedness score. The harness, the
golden set and the method are the durable parts; the numbers are the first data point they
produced.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
