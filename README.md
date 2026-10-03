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

### Retrieval benchmark on Azure AI Search, 3 October 2026

**The question:** how much better could ReleaseLens's retrieval be, measured against what it does
today? Six arms searched the same corpus of 41,825 chunks, restored from 12 August, with the same
questions. The design and the decision rule were fixed in advance, in
[the spec](docs/superpowers/specs/2026-10-02-azure-ai-search-benchmark-design.md). Its dated
amendments in §13 record every change made after approval. The full write-up, with method,
per-type figures, determinism and costs, is in
**[eval/baseline.md](eval/baseline.md#retrieval-benchmark--3-october-2026)**.

| Arm | What it is |
|---|---|
| E1 | BGE-small, the local model ReleaseLens embeds with: exact cosine search |
| E2 | Azure OpenAI `text-embedding-3-small`: exact cosine search |
| E3 | Azure OpenAI `text-embedding-3-large`: exact cosine search |
| S1 | ReleaseLens today: the app's own hybrid retriever |
| S2 | Azure AI Search hybrid: keyword plus `-small` vectors |
| S3 | S2 with AI Search's semantic ranker |

**The questions.**
- **Writing:** Claude Sonnet 5 wrote one question per artefact, from a seeded, stratified sample.
  Each question has exactly one right answer: its pull request, commit or issue.
- **Checking:** an independent reviewer (Claude Opus 5.5 subagents, at the owner's direction)
  audited all 300, searching the whole corpus for a second answer, and kept the **229** with
  exactly one.
- **Releases are not measured.** Every release question had a pull request or another release
  note that answered it as well.
- **Routine version bumps are not measured either.** They were left out of the sample, being
  near-identical to each other.

**The three comparisons.** Each is x − y in top-1 accuracy, paired by question. A difference is
declared only when the paired difference is at least ±0.05 *and* its 95% bootstrap interval
excludes zero.

| # | Comparison | n | Mean difference in top-1 | 95% CI | Verdict |
|---|---|---:|---:|---|---|
| C1 | E3 − E1: OpenAI's large model against the local BGE, both exact | 229 | +0.271 | [+0.201, +0.341] | difference |
| C2 | S3 − S1: AI Search with the semantic ranker against ReleaseLens today | 229 | +0.262 | [+0.192, +0.332] | difference |
| C3 | S3 − S2: the semantic ranker's own effect | 229 | +0.105 | [+0.026, +0.179] | difference |

The three are each made at 95%, with no correction for making three.

| Arm | Top-1 | MRR | p50 / p95 latency | Cost per 1,000 queries |
|---|---:|---:|---:|---:|
| E1 | 0.376 | 0.494 | 199 / 270 ms | $0, local |
| E2 | 0.485 | 0.613 | 340 / 911 ms | $0.000544 |
| E3 | 0.646 | 0.739 | 132 / 149 ms | $0.003538 |
| S1 | 0.367 | 0.483 | 147 / 258 ms | $0, local |
| S2 | 0.524 | 0.637 | 652 / 1,273 ms | $0.000544 |
| S3 | 0.629 | 0.739 | 667 / 1,229 ms | $0.000544 |

**How to read the per-arm figures.**
- **They are descriptive,** with no significance claim, n = 229 each.
- **Latency is not a contest** between the local arms and the network arms.
- **Fixed costs are outside the table.** The search service costs $0.133 an hour on Basic, and
  embedding the corpus once cost $1.57.
- **Nothing errored.** No arm errored on any question, and repeating the first 30 questions
  changed no arm's top-1 result.
- **Keyless throughout.** AI Search ran with key authentication off. Every call to it and to
  Azure OpenAI carried the owner's Entra token, through the Azure CLI.

**What this does not show.**
- **It measures retrieval only,** not the quality of any answer built on it.
- **ReleaseLens's search is unchanged.** S1 is what it runs today.
- **The service was short-lived.** It was created for the session and destroyed straight after.
- **A possible shared bias.** The question writer and the auditor are both Anthropic models. No
  arm uses an Anthropic model.

### Three-arm study, 2 October 2026

The claim under test: going keyless on Azure changed how requests are authenticated, not what is
sent. The study puts the same ten golden queries (two per category) to three arms, on three
passes each, against the corpus of 41,825 chunks restored from 12 August. One Claude Sonnet 5
judge, blinded to the arm, scores groundedness. The design and the decision rule were fixed in
advance, in spec §8. The full write-up, with method, per-arm figures and every per-query delta:
**[eval/baseline.md](eval/baseline.md#three-arm-study--2-october-2026)**.

| Arm | Provider | Model the replies named | Auth |
|---|---|---|---|
| A | Anthropic | `claude-sonnet-5` | key |
| Z | Azure OpenAI, Global Standard | `gpt-4.1-mini-2025-04-14` | the owner's Entra identity, through the Azure CLI |
| O | OpenAI | `gpt-4.1-mini-2025-04-14` | key |

Z authenticates as the owner, not as the managed identity the deployed app uses.

**Z against O, the same model through two auth paths.** Each delta is Z − O. A difference is
declared only when the mean paired delta is at most −0.10 or at least +0.10 *and* its 95%
interval excludes zero.

| Metric | Sample | Mean delta | 95% CI | Verdict |
|---|---|---:|---|---|
| Groundedness | 10 queries × 3 passes, 30 pairs | -0.077 | [-0.147, -0.017] | inconclusive at 10 queries × 3 passes |
| Citation recall | 8 queries × 3 passes, 24 pairs | -0.028 | [-0.083, +0.000] | inconclusive at 8 queries × 3 passes |
| Citation precision | 8 queries × 3 passes, 24 pairs | +0.008 | [-0.013, +0.038] | inconclusive at 8 queries × 3 passes |
| must_contain pass rate | 6 queries × 3 passes, 18 pairs | -0.111 | [-0.333, +0.000] | inconclusive at 6 queries × 3 passes |

All four are inconclusive. That does not show the two paths give the same answers; it shows
this sample cannot tell. Groundedness is the closest: its interval excludes zero, so Z's answers
scored lower than O's in this sample, but by 0.077, less than the 0.10 the rule requires. Most
of that comes from three queries (gq-014, gq-015 and gq-022; see the per-query deltas). The
`must_contain` delta comes from one query, gq-022.

Z against A compares two different models and does not test the keyless claim. All four of its
metrics are inconclusive too.

| Arm | Groundedness | Citation recall | p50 / p95 latency | Mean cost per query |
|---|---:|---:|---:|---:|
| A | 0.840 (n = 30) | 0.889 (n = 24) | 5,406 / 32,434 ms | $0.0488 |
| Z | 0.783 (n = 30) | 0.646 (n = 24) | 3,049 / 9,101 ms | $0.0021 |
| O | 0.860 (n = 30) | 0.674 (n = 24) | 2,821 / 6,432 ms | $0.0021 |

Per-arm figures are descriptive, with no significance claim. The run had no errors, no content
filter events and no failed judgements, and cost $2.7055: $1.5903 answering and $1.1152 judging.

### Single-provider runs, 11–12 August 2026

These are kept as history. They measured Claude Sonnet 5 alone, before the three-arm harness.

Measured 12 August 2026, against a corpus of 41,825 chunks. Full
write-up, including method, caveats and the previous run for comparison:
**[eval/baseline.md](eval/baseline.md#evaluation--12-august-2026)**.

| Metric | 11 Aug | 12 Aug |
|---|---|---|
| Golden query set | 43 queries | 43 queries · 5 categories |
| **Queries in this run** | 5 | **5 — one per category** |
| **Groundedness (LLM-judge)** | not measured | **0.940** (scored 5 of 5) |
| Citation recall | 1.000 | **1.000** |
| Citation precision (answerable) | 0.090 | **0.588** |
| must_contain pass rate | 1.000 | **1.000** |
| **Unanswerable handled correctly** | 1 of 1 | **1 of 1** |
| p50 / p95 latency | 15,876 / 58,028 ms | 20,863 / **22,647** ms |
| Total cost, 5 queries | $0.9200 | **$0.2367** |
| Worst single query | $0.7202 | **$0.0581** |

**Read the sample size before the results.** Five queries, one per category. Real
measurements rather than estimates, but five of them, and no rate should be derived from
them. Five is what the remaining API balance allowed; the 11 August run priced a full
43-query sweep at $8.01 and that reasoning is recorded rather than left to be guessed at.

Three things this table is deliberately not claiming:

- **Precision is 0.588, not the 0.669 the harness reports.** Recall and precision both return
  a vacuous 1.0 for queries expecting no citations, which is every `unanswerable` entry. The
  figure above is over the four answerable queries only.
- **"1 of 1" is not 100%.** One correct refusal.
- **The precision columns are not comparable with each other.** Until the citation-filtering fix, the response
  returned every artefact any tool had touched, so precision measured how many rows a tool
  returned rather than anything about the answer. Most of that rise is the measurement
  becoming correct, not the system improving.

The row that matters most is the unanswerable one. Eight of the 43 queries have no answer in
the evidence, and the correct response is to say so. A system that scores well on the other
four categories and invents an answer here is confidently wrong, which is worse than being
unable to answer.

### The loop this is here to demonstrate

The 11 August run found cost spanning **77× across categories**: *"list every Java release
tag"* cost $0.72, took 58 seconds and pushed 337,570 input tokens — 78% of the whole run.
Nothing in the system computed anything; every tool returned evidence chunks, so an
aggregation question could only be answered by retrieving toward completeness.

That measurement justified `count_evidence` and `list_releases`. The same query now costs
**$0.0581 and runs in 21.7 seconds**, and p95 across the run fell from 58.0s to 22.6s. The
tools exist because a number said they should, and a later number says whether they worked.

The same run also showed the harness measuring itself rather than the system in two places,
both since fixed: citations were not filtered to the ones the answer used, and the
groundedness judge had never received anything but bare identifiers — it was being asked
whether claims were supported by the string `issue:14111`, correctly answering that it could
not tell, and scoring zero for it.

**Recall did not fall, and it was expected to.** Filtering citations to those the answer used
should have exposed queries where retrieval found the right artefact and the model wrote
around it. It stayed at 1.000.

### What the judge caught once it could see

With real evidence, its findings are specific enough to act on. On gq-014 it flagged that a
*"'19 commits' figure is anomalously cited to [E4][E5]"* — markers that do not establish it.
On gq-022, that *"dotnet-1.79.0 shipped the fix"* rests on an inference the cited evidence
does not carry. Both are quiet over-claiming, and neither is visible from recall, precision
or `must_contain`, all three of which are perfect on those queries.

## What it does that a tutorial RAG does not

**The model gets tools, not just chunks.** `search_commits`, `get_issue`,
`diff_between_releases` and `find_regressions` retrieve evidence by sampling it.
`count_evidence` computes a figure over the whole corpus instead of sampling — the one tool
that returns no citation, because a count is not an artefact. `list_releases` also runs an
exhaustive query rather than a sampled one, but what it returns are citable release rows, so
it is retrieval too. It runs its own follow-up searches when the first pass is not enough.
Agentic retrieval, not single-shot.

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

### Evidence API

Alongside `/query`, which answers a question, ReleaseLens publishes the evidence tools
themselves so an external agent can retrieve evidence and compose its own answer.

```
GET  /evidence/tools         the six tools, with descriptions and JSON schemas
POST /evidence/tools/{name}  run one, under the tenant the API key belongs to
```

A `POST /evidence/tools/{name}` response carries `content` (the tool's rendered text),
`isError` (a recoverable tool failure — an unknown tag, a malformed argument — not an HTTP
error; the route itself still returns 200), `citations` (the artefacts the result names) and
`excerpts` (the attributable text behind each citation, keyed to it by `(type, key)`).

It also carries the two fields that make a result's *limits* machine-readable, so a caller
never has to parse the prose to recover them:

- **`bounds`** — on an `evidence` result: `returned`, `matched` and `truncated`. `matched` is
  how many of the matching population exist under the same filters in the whole corpus;
  `returned` is how many of that population this response contains. `matched` is exact, never
  an estimate, and never less than `returned`. `truncated` is simply `matched > returned`.
  Null on a `computed` result and on a tool error, which are not pages of results.
  The unit and the population are per tool: `find_regressions`, `list_releases`,
  `diff_between_releases` and `get_issue` count artefacts; `search_commits` counts *chunks
  matched by the full-text arm*, because that is the only population it holds a corpus-wide
  count for — chunks the vector arm alone retrieved are returned as context but are not part
  of that ratio. See the remarks on `ToolResultBounds` for why the two numbers must share one
  population and what goes wrong when they do not.
- **`coverage`** — on a `computed` result: `earliest`, `latest` and `completeForWindow`. The
  corpus does not span all of history, so a count can be true of the evidence and false of the
  repository; `completeForWindow` is how a caller tells those apart. Null on an `evidence`
  result.

The two are mutually exclusive by construction — `bounds` belongs to a page of artefacts,
`coverage` to a figure — and the MCP server in the sibling `releaselens-mcp` repository binds
directly to them. They exist because an external agent has no system prompt telling it not to
count the rows it was handed.

A result also declares whether it is `evidence` — a citable artefact, with citations — or
`computed`: a figure produced by a bounded aggregate, carrying no citations because a
computed figure is not an artefact. `count_evidence` is the only tool that returns
`computed`; every other tool, including `list_releases` (which runs an exhaustive query
rather than a sampled one, but still returns citable rows), reports `evidence` with real
citations. That distinction is a property of the response, not a convention a caller has to
know.

Unlike `/query`, where excerpt text is opt-in via `includeEvidence` — kept off by default
there because a chunk runs to roughly a thousand characters across as many as twenty
citations, a cost documented in `Contracts.cs` — this endpoint always returns `excerpts`. An
evidence API exists so a caller can read the text behind a citation, so withholding it by
default here would defeat the endpoint's purpose; the payload-size trade-off `/query` makes
does not apply to a surface whose only job is handing evidence to an external caller.

Tenant is derived from the API key. No schema published by `/evidence/tools` accepts a
tenant parameter, and no route or body field offers one, so a caller has no way to ask for
another tenant's evidence.

**Unproven:** these endpoints exist and are tested, but nothing has yet been built on top of
them, and no measurement of an external agent using them has been taken.

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
| Telemetry | OpenTelemetry → Aspire Dashboard locally. In Azure, logs stream with `az containerapp logs show`; there is no Log Analytics workspace |
| Infrastructure | Terraform in three stacks: a long-lived bootstrap stack, an app stack (Container Apps scale-to-zero, Postgres Flexible Server), and a short-lived search stack for the retrieval benchmark. A manual OIDC deploy and a nightly destroy in GitHub Actions; GitHub holds no credentials. Azure OpenAI with key authentication disabled, on Global Standard. Budget alerts |
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
{ "Chat": { "Providers": ["openai"] }, "OpenAi": { "BaseUrl": "http://localhost:11434/v1/", "Model": "llama3.1", "Unpriced": true } }
```

Embeddings were already local. With this, nothing leaves the machine.

## Testing

```bash
dotnet test
```

256 tests. Unit tests for chunking, normalisation and the provider adapters against mocked
transports; integration tests against real PostgreSQL via Testcontainers, including the
row-level security proofs.

```bash
cd eval && python -m pytest
```

31 tests over the metrics, the judge, the runner and the golden set — structure, uniqueness,
category validity, and the sampling that keeps a partial sweep representative. The judge and
runner tests drive a fake Anthropic client, so the suite never spends money.

The golden query set itself is the regression corpus and runs on demand, because it costs
money.

## Deployment

**The app stack was deployed and destroyed twice on 1 October 2026,** through the deploy and
destroy workflows. The runs are recorded in
[Two-stack deployment, 1 October 2026](#two-stack-deployment-1-october-2026). Between runs,
nothing is deployed. The rest of this section is about what the code configures, and each
Azure fact carries its source and date. The
detail is in [docs/architecture.md](docs/architecture.md#azure-deployment).

There are two Terraform stacks for the app, and a third for the retrieval benchmark:

| Stack | Applied by | Holds |
|---|---|---|
| [`infra/bootstrap`](infra/bootstrap/README.md) | the owner, locally, once; never destroyed | the Terraform state storage; the deploy and app identities; the one federated credential; the Azure OpenAI account and its three deployments, the chat model and two embedding models; the budget; the empty app resource group; every role assignment except the search stack's two |
| [`infra/terraform`](infra/terraform/README.md) | GitHub Actions through OIDC, every session | the Container App and Postgres, and nothing else |
| [`infra/search`](infra/search/README.md) | the owner, locally, for one benchmark session, then destroyed | its own resource group, one keyless AI Search service on Basic, and the owner's two roles on it |

**Bootstrap once.** The owner follows the runbook in
[infra/bootstrap/README.md](infra/bootstrap/README.md). The budget comes first: the first apply
creates only the budget, its action group and the resource group that holds the action group.
So the budget exists before anything that can bill. Bootstrap also registers the resource
providers every stack uses. One of them is `Microsoft.App`, which the 12 August apply below found
missing on a fresh subscription.

**Then deploy and destroy through the workflows**,
[`deploy.yml`](.github/workflows/deploy.yml) and [`destroy.yml`](.github/workflows/destroy.yml):
- **`deploy.yml`** runs only when dispatched by hand, from `main`, under the GitHub environment
  `azure`. It refuses to run while the destroy workflow is disabled. It pins the image to the
  digest of `sha-<commit>` and applies the app stack. Then it runs a smoke test, which passes
  only if all of these hold:
  - the deployed app answers through Azure OpenAI, as its managed identity
  - the answer is not degraded
  - the cost is above $0, and matches the cost recomputed from the answer's own token counts

  The smoke test provisions its own tenant and API key with the Worker, against the deployed
  database. It masks the connection string and the key in the run log before anything else can
  print them.
- **`destroy.yml`** runs when dispatched, and nightly at 14:00 UTC (midnight AEST). After
  `terraform destroy`, it lists what is left in `rg-releaselens` and fails if anything is. A
  resource created outside Terraform survives a destroy, so this check is needed. The check also
  runs after a failed or cancelled destroy, and names what is still billing.
- **The nightly destroy is best effort, not a guarantee.** GitHub disables scheduled workflows in
  a public repository after 60 days without activity, and scheduled runs can be delayed or
  dropped. The budget alert is the backstop, and it stops nothing.

**What this design claims:**
- **Key authentication is disabled** on the Azure OpenAI account (`local_auth_enabled = false`),
  and no key is used. The app authenticates as its managed identity, and local runs authenticate
  as the owner through `az login`.
  - The account's keys still exist, but none of them is in Terraform state. Runbook step R8
    checked that on 2026-09-30, after the full bootstrap apply: in the state, the account's
    primary and secondary access keys are both empty strings.
  - CI has no role on the account, so it cannot turn key authentication back on. Only the owner
    can.
- **GitHub holds no credentials, only identifiers.** Four of them are stored as environment
  secrets so that public run logs mask them: the tenant and subscription IDs, the state storage
  account's name and the Azure OpenAI base URL.
  - The last two became secrets on 2026-10-01, the owner's decision. A request with an invalid
    token to the storage account's blob endpoint gets a 401 whose `WWW-Authenticate` header names
    the tenant ID (checked 2026-09-30). The storage account shares its random suffix with the
    Azure OpenAI account, and both name patterns are in this repository, so the base URL would
    give the storage account's name away. The Azure OpenAI endpoint's own 401 names no tenant.
    Other routes to the tenant ID have not been ruled out.
  - The rest are variables of the environment `azure`: the deploy identity's client ID, the app
    identity's resource ID and client ID, the deployment name, and `SMOKE_OPEN_RUNNER_IP`, which
    is set only if the smoke test needs it. The variables appear unmasked in public run logs, on
    purpose. The exception is `APP_IDENTITY_ID`: it contains the subscription ID, so it shows
    with its subscription segment masked.
  - GitHub masks a secret only where its whole value appears. So Terraform's apply and destroy
    output in the workflows passes through a filter that hides the subscription ID even when
    Terraform truncates it.
- **CI holds no role-assignment rights and no subscription-scope rights.** What it *can* do is
  listed under [What this is not](#what-this-is-not).
- **Data at rest stays in the Australia geography.** The account is in `australiaeast`.
  Inference runs on Global Standard. Microsoft's
  [deployment types page](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types)
  (dated 2026-08-06, read 2026-09-27) says that for Global types, inference "may be processed in
  any Azure region". The prompts carry public GitHub data.

**What bills.** Postgres bills by the hour while it exists, which is why the app stack is
destroyed after every session. The Container App scales to zero. The Azure OpenAI deployment
bills per token; the rates, and what else bills, are in
[docs/architecture.md](docs/architecture.md#what-bills).

**The budget alerts; it does not cap spend.** It is a subscription-scope budget, and defaults to
50 a month. That amount is in the subscription's billing currency, whatever the variable's name
says. For this project's subscription the budget reports its spend in AUD (the Consumption
budgets API's `currentSpend.unit`, read 2026-09-30). It alerts at 50, 80 and 100 percent of actual spend and at 100 percent of
forecast spend. Microsoft's
[budget tutorial](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
(dated 2025-06-26, read 2026-09-27) says two things about budgets:
- a budget stops no consumption
- it is evaluated every 24 hours, against cost data that is typically 8 to 24 hours old

**The model retires.** Microsoft's retirement schedule (read 2026-09-24) lists `gpt-4.1-mini`
2025-04-14 as Legacy, retiring 2027-04-14. The deployment is pinned (`NoAutoUpgrade`), so it
stops working on that date instead of moving to a model the app was not tested with. Its
successors need payload changes. Review this in February 2027.

### Two-stack deployment, 1 October 2026

The owner applied the bootstrap stack on 30 September 2026. On 1 October the app stack was
deployed and destroyed twice through the workflows, signed in through OIDC as the deploy
identity. This records only what those runs showed.

- **The OIDC boundary** (30 September, runbook step R11). A job in the environment `azure`
  exchanged its GitHub token for an Azure token, and a job without the environment was refused
  (`AADSTS700213`). That run has since been deleted, because its log showed the tenant ID. The
  probe workflow that printed it no longer exists.
- **First deploy** ([run 36778651088](https://github.com/DmitryLyubaev/releaselens/actions/runs/36778651088), commit `542258e`): preflight, apply and
  the smoke test passed.
  - Azure OpenAI answered the smoke test's question, with model `gpt-4.1-mini-2025-04-14`, not
    degraded. It cost **US$0.0012420**: 2,345 uncached input tokens, 2,176 cached input tokens
    and 54 output tokens. The Worker's `price` command recomputed the same cost, to 6 decimal
    places.
  - The runner reached Postgres through `allow-azure-services`, so the runner rule was not
    needed.
- **The first destroy stopped on a provider bug**, azurerm issue [#33433](https://github.com/hashicorp/terraform-provider-azurerm/issues/33433). Azure deleted
  the Container App, but the provider reported a failure and stopped.
  - A second run deleted Postgres, hit the same bug on the Container Apps environment, which
    Azure had also deleted, and the empty-group check passed.
  - Both logs showed the first 24 characters of the subscription ID. Terraform truncates IDs in
    its progress lines, and GitHub masks only whole values. Both runs were deleted.
    [PR #16](https://github.com/DmitryLyubaev/releaselens/pull/16) added the output filter and
    the retry.
- **Second deploy** ([run 36794105021](https://github.com/DmitryLyubaev/releaselens/actions/runs/36794105021), commit `7029367`): passed. It cost
  **US$0.0012452**, for 2,345 uncached input, 2,176 cached input and 56 output tokens. Again,
  the runner rule was not needed.
- **Second destroy** ([run 36795849202](https://github.com/DmitryLyubaev/releaselens/actions/runs/36795849202)): passed. The retry for #33433 fired
  twice, the third attempt finished cleanly, and the empty-group check passed. Neither of the
  last two logs shows any part of the subscription or tenant ID.

What the runs did **not** establish:
- **Answer quality.** The smoke tenant has no evidence, so the question was answered without any
  of the corpus. The runs prove the path from the API, through the managed identity, to Azure
  OpenAI and back, not answer quality.
- **The idle cost.** What the deployment costs while idle waits for the invoice.

### Single-stack deployment, 12 August 2026

This is a record of the earlier, single-stack design, kept as history. That stack had a Key
Vault and a Log Analytics workspace; the current design uses neither.

Applied to a real Azure subscription on 12 August 2026: **16 resources, roughly 12 minutes**,
and then destroyed. The deployed API answered `/health` with HTTP 200 in **7.6 seconds cold**
-- that figure is the `min_replicas = 0` trade-off made concrete, since the first request pays
for the container to start. Subsequent requests returned immediately.

Three things that only an apply could establish, and which `plan` and `validate` both passed
without noticing:

- the `Microsoft.App` registration above, which is why it is documented here at all
- that Azure can pull the image from a public GHCR package -- the Container App has no
  registry credentials by design, so a private package cannot be deployed without adding them
- that the SKUs, region and quota in this configuration are actually satisfiable

What the run did **not** establish: `/health` returns a literal, so it exercises neither Key
Vault nor the database, and the deployed database was empty -- no migrations, no corpus. The
deployment is proven; an end-to-end demo over real evidence is a separate exercise.

## What this is not

**It has not been run at scale.** Tenant isolation is designed and tested; it has not been
exercised under concurrent load or with a large number of tenants.

**Postgres has a password.** The deployed database uses a generated server-administrator
password, not Microsoft Entra authentication.
- The password is in three places: the app stack's current Terraform state, the earlier blob
  versions of that state, and the Container App's secret. Only the owner and the deploy identity
  can read the state and its versions.
- It is generated when the database is created and deleted with it, so it lives until the next
  destroy. That is normally one session. A second deploy without a destroy keeps the same
  password, and the nightly destroy is best effort.
- Earlier versions of the app state still hold it for up to 90 days after they were written,
  plus 7 days of soft delete. Once the server is gone, it opens nothing.
- Two of the project's own rules are not met, and this says so rather than hiding it:
  - A secret that cannot be avoided is not kept in Key Vault with an expiry date. The password is
    generated by Terraform, so a Key Vault would only hold a copy of a value that is already in
    state.
  - Postgres is not keyless, although Azure allows Entra authentication for it.
- Entra authentication for Postgres is later work.

**Postgres is open to Azure services.** Its firewall rule `allow-azure-services` (`0.0.0.0`)
admits any Azure-hosted client in any tenant, not only this subscription. The password is the
only control.
- GitHub-hosted runners pass that rule: both deploys on 1 October 2026 reached Postgres without
  the runner rule. If that changes, setting `SMOKE_OPEN_RUNNER_IP` to `true` makes the deploy
  workflow add a rule for the runner's IP alone before the smoke test, and remove it afterwards.
- Private networking is later work.

**CI can do four things, because deploying needs them:**
- **Run code as the app identity.** It holds Contributor on `rg-releaselens` and Managed Identity
  Operator on the app identity.
  - That identity can call the model and the account's data plane directly, outside `/query`,
    where no tenant token budget applies.
  - The deployment's capacity (300,000 tokens per minute, from the dry run's measurement) caps
    how fast that spend can grow, not how much it can total.
  - The budget alerts, and stops nothing.
- **Read the Postgres password,** which is in the app stack's state.
- **Create any billable resource in `rg-releaselens`.** So the right to push to `main` is also
  spending authority. Anything created outside Terraform survives a destroy, which is why the
  destroy workflow fails if the group is not empty afterwards.
- **Rewrite the app stack's state.** So the owner runs that stack locally only through a
  reviewed, interactive plan, never `-auto-approve`. Any planned deletion of something other than
  the Container Apps resources or Postgres is treated as tampering, and recovered from an earlier
  version of the state.

CI cannot assign roles, act at subscription scope, or read the bootstrap state. It holds no role
on the Azure OpenAI account, so it cannot change the account's settings, including key
authentication.

**The evaluation is a starting point.** Five queries, one per category, measured on 12 August
2026, with groundedness scored on all five. No rate should be derived from five. The harness, the
golden set and the method are the durable parts; the numbers are early data points.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
