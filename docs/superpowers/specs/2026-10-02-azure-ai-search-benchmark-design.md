# Retrieval benchmark on Azure AI Search — design

**Status: specification, approved by the owner on 2026-10-02.** Nothing is built or measured. Every figure below
is either read from a named source on a stated date, or labelled as an estimate. Claims that
could not be checked are labelled *unverified* and listed in §10.

- Project 2 of the portfolio plan (phase 1), in ReleaseLens, `main` at `59b3ce4`
- Builds on project 1:
  - the keyless Azure OpenAI account and bootstrap stack
  - the evaluation harness in `eval/`
  - the restored corpus of 12 August 2026
  - the pre-registered decision-rule machinery from plan 3
- Brief: the phase 1 plan is private; this document restates everything it needs from it

---

## 1. Purpose

Measure which retrieval puts the right artefact first, for single-target questions about the
semantic-kernel corpus that ReleaseLens holds. The comparison is between:
- the local BGE-small model the app uses today
- Azure OpenAI's `text-embedding-3-small` and `text-embedding-3-large`
- Azure AI Search hybrid search, with and without its semantic ranker
- ReleaseLens's own hybrid search, as the baseline

The design and the decision rule are fixed here, before any data exists. "The local model wins"
and "inconclusive" are both acceptable, publishable answers.

**Why it matters.** An honest, checkable measurement of a managed Azure search service against
a free local alternative, on a real corpus, with cost per query. It is not a production feature:
ReleaseLens's own search is not changed by this project.

---

## 2. What exists today

Read from the repository on 2026-10-02.

- **Corpus.** The restored 12 August corpus, a local Postgres database `releaselens_eval`:
  - 41,825 chunks over 2,921 commits, 3,805 issues, 7,121 pull requests and 276 releases
  - 10,343,825 tokens by the stored `token_count`, an average of 247 per chunk, at most 320
  - each chunk carries `entity_type` and `entity_key`, which together identify its artefact
    as `type:key`
- **Links between artefacts.** The only link the schema records is `pull_requests.merge_commit_sha`,
  which ties a merged pull request to its merge commit. Issue-to-pull-request links exist only
  as mentions in text.
- **Local embedder.** `OnnxEmbedder`, BGE-small-en-v1.5, 384 dimensions, CLS pooling, with BGE's
  query prefix applied to queries. The vectors are stored in `embeddings.embedding vector(384)`
  with an HNSW index.
- **ReleaseLens search.** `HybridRetriever` (`src/ReleaseLens.Storage/Retrieval/HybridRetriever.cs`):
  a vector pool (pgvector, cosine, approximate) and a full-text pool (`ts_rank_cd` over
  `content_tsv`), joined and ordered by a blended score.
- **Harness.** The `eval/` package from plan 3:
  - seeded paired bootstrap and the decision rule, settled to 12 places (`analysis.py`)
  - a renderer that publishes a report verbatim (`study_report.py`)
  - fakes for every network call in its tests
- **Azure.** The keyless `AIServices` account from project 1, the bootstrap Terraform stack, the
  state storage account, and the A$10 monthly budget alert.

---

## 3. The question set

**3.1 Targets are artefacts.** Each question is written from one artefact and has that artefact
as its single correct answer, identified as `type:key`. Retrieval returns chunks. Each arm's
ranked chunks are collapsed to a ranked list of artefacts, by first appearance. A top-1 hit means
the first artefact is the target.

**3.2 Sample.**
- **Size and seed:** 300 artefacts, drawn with the fixed seed `20261002` from the restored corpus.
- **Stratified by type,** in proportion to the distinct artefacts (`entity_type`, `entity_key`)
  in `evidence_chunks`. The sampler computes the exact split, by largest remainder, so the total is
  300. With the evidence tables' counts (14,123 artefacts) the split would be 62 commits, 81
  issues, 151 pull requests and 6 releases; the chunk table's distinct counts may differ slightly.
- **Too thin to ask about:** an artefact is skipped when its chunks' `token_count` totals fewer
  than 40. The next draw from the same seeded stream replaces it.
- **The text Claude sees:** an artefact with several chunks is given to Claude as its chunks in
  order, truncated to 1,500 tokens.

**3.3 Writing.** One call to Claude Sonnet 5 per artefact. It is told to:
- write one natural question a developer might ask that this artefact alone answers
- ask about what is distinctive to it, such as a number, author or date, where its text shows
  them
- not copy the artefact's distinctive phrasing
- return JSON: `{"question": ..., "why_unique": ...}`

The prompt text, the model name and the generation date are frozen with the set.

No arm uses an Anthropic model, so the writer is independent of every contender.

**3.4 Mechanical checks.** Each is fixed here and applied before any arm runs. A question is
rejected if it:
- **copies its target:** more than 50% of its content words, meaning lower-cased words of 4 or
  more letters that are not in a fixed English stop-word list, appear in the target's text
- contains the target's key (a SHA prefix of 7 or more characters, `#<number>`, the tag) or its
  URL
- is shorter than 6 words or longer than 40
- is not valid JSON output

A rejected artefact gets one rewrite. If that fails too, the next seeded draw of the same type
replaces it. The final count is exactly 300, with the same per-type counts.

**3.5 The owner's spot-check.**
- 30 questions are drawn with a fixed seed and shown with their targets' text.
- The owner marks each one: fine, ambiguous (more than one right answer) or wrong.
- **The pass mark:** if more than 3 of the 30 are ambiguous or wrong, the prompt or the checks
  are changed, the set is regenerated, and a fresh sample of 30 is checked.
- The marks are saved with the set.

**3.6 Freezing.** The final set is committed to the repository before any arm runs, at
`eval/retrieval/questions.jsonl`. The commit also carries a manifest: the seed, the prompt, the
model, the date, the rejection counts and the spot-check marks. Every measurement reads only that
file.

---

## 4. The arms

All six search the same 41,825 chunks with the same chunk text. Each returns its top 50 chunks
per question.

| Arm | What it is | How it searches |
|---|---|---|
| E1 | BGE-small, local | exact cosine nearest neighbours over the stored 384-dimension vectors; the query is embedded locally with BGE's query prefix |
| E2 | `text-embedding-3-small` | exact cosine over 1,536-dimension vectors of the corpus |
| E3 | `text-embedding-3-large` | exact cosine over 3,072-dimension vectors (full size, no dimension reduction) |
| S1 | ReleaseLens today | the app's real `HybridRetriever`, run by a new Worker `retrieve` command over the question file in one process |
| S2 | AI Search hybrid | keyword (English analyzer) plus vector (`-small`, HNSW) in one query; AI Search fuses the two rankings |
| S3 | AI Search hybrid with the semantic ranker | S2's query with `queryType: semantic`; the ranker reorders S2's top 50 |

**Why the E arms use exact search.** The embedding arms isolate the model: exact search means no
approximate index can blur the comparison. The S arms run as each system really runs, with its
own approximate index.

**Corpus vectors for E2 and E3** are computed once per model and saved locally. They are
git-ignored, and they are reused for S2's index, so the corpus is never embedded twice.

**S2's embedding is fixed in advance.** S2 and S3 use `-small`, because it is the cheaper model a
real deployment would choose. This is fixed here, so the result cannot be steered by picking the
better embedding afterwards.

**The AI Search index:**

| Field | Kind | Notes |
|---|---|---|
| `chunk_id` | key | |
| `artefact` | filterable string | `type:key` |
| `content` | searchable | English Lucene analyzer |
| `vector` | 1,536 dimensions | cosine, HNSW, default parameters |

It has one semantic configuration, with `content` as the content field.

---

## 5. Scoring and the decision rule

**5.1 Per question and arm:**
- **top-1:** 1 if the first artefact is the target, else 0
- **reciprocal rank:** 1/rank of the target among the collapsed artefacts, or 0 if it is not
  within the 50 chunks
- **lenient top-1:** also counts a pull request's merge commit as correct for a pull-request
  target, and the reverse, using `merge_commit_sha`
- **margin, within the arm only:** the arm's score for its first artefact minus its score for the
  best different artefact, when top-1 is a hit

**5.2 One pass, with a determinism check.** Each arm answers all 300 questions once. The first 30
questions are then run again on every arm, and the report states how many top-1 results changed
per arm. A change in more than 3 of the 30 on any arm is reported as a caveat on that arm.

**5.3 The pre-registered comparisons.** These are the only ones with a verdict:

| # | Comparison | The question it answers |
|---|---|---|
| C1 | E3 − E1 | does OpenAI's best embedding model beat the free local one? |
| C2 | S3 − S1 | does Azure's full managed search beat what ReleaseLens has? |
| C3 | S3 − S2 | what does the semantic ranker add? |

**5.4 The decision rule.**
- For each comparison: the paired difference in top-1 accuracy over the same 300 questions.
- **The interval:** its 95% interval from a bootstrap that resamples questions, with 10,000
  resamples and seed `20261002`, nearest-rank percentiles, and values settled to 12 decimal places
  (the plan 3 machinery).
- **The rule:** a difference is declared only when the paired difference is at most −0.05 or at
  least +0.05 **and** its interval excludes zero. Anything else is reported as
  "inconclusive at 300 questions".
- **On multiple comparisons:** three comparisons are each made at 95%, with no correction for
  multiple comparisons, and the write-up says so.

**5.5 Reported with no verdict:**
- **For each arm:**
  - top-1 accuracy
  - MRR
  - lenient top-1
  - the median within-arm margin
  - latency p50 and p95
  - cost per 1,000 queries
- **By artefact type,** each figure with its n. Releases (n = 6) are labelled too small to read.
- **Every other pair of arms,** as plain differences in an appendix, labelled exploratory, with no
  interval.

**5.6 Latency** is measured per question by the harness, end to end per arm, including embedding
the question. Local arms (E1, S1) and network arms (E2, E3, S2, S3) are not alike, and the
write-up says so.

**5.7 Cost per 1,000 queries** is computed from each arm's measured query tokens at the rates in
§8, plus the ranker's per-request price for S3. AI Search's hourly charge is a fixed cost whatever
the number of queries, so it is reported separately as cost per hour of service.

---

## 6. Azure

**6.1 Embedding deployments, in the bootstrap stack,** on the existing `AIServices` account:

| Deployment | Model | Version | Type | Capacity |
|---|---|---|---|---|
| `releaselens-embed-small` | `text-embedding-3-small` | 1 | Global Standard | 350 |
| `releaselens-embed-large` | `text-embedding-3-large` | 1 | Global Standard | 350 |

- **Version:** pinned, with automatic upgrades off.
- **Capacity:** 350 is 350,000 tokens a minute. Embedding the 10.3M-token corpus at that rate
  takes about 30 minutes per model (an estimate). The subscription's Global Standard quota for
  each model is 1,000, read with `az cognitiveservices usage list` on 2026-10-02.
- **Lifecycle:** both versions are GA, and retire 2028-02-09, per `az cognitiveservices model list`
  on 2026-10-02.
- **Cost:** they bill per token, and cost nothing idle.
- **Where data is processed:** Global Standard processes data in any Azure region, as the chat
  deployment does. The README already states this.

**6.2 The search stack, `infra/search`,** a new short-lived Terraform stack the owner applies and
destroys from WSL:
- **What it holds:** its own resource group, `rg-releaselens-search`, and one search service on
  `basic` in `australiaeast`, with one replica and one partition.
- **Keyless:** `local_authentication_enabled = false`, so only Entra authentication works.
- **The owner's roles, on the service only:**
  - `Search Service Contributor`, to manage the index
  - `Search Index Data Contributor`, to load and query

  No other principal gets access, and CI gets none.
- **The semantic ranker plan is `free`** (§6.3).
- **State:** a new container `tfstate-search` in the existing state storage account. The bootstrap
  stack creates it, and grants the owner `Storage Blob Data Contributor` on it.
- **A separate resource group, deliberately.** In `rg-releaselens`, the nightly destroy's
  empty-group check would find the service and fail.
- **Role assignments live in this stack.** The rule that every role assignment lives in bootstrap
  exists so CI never holds role-assignment rights. This stack is applied only by the owner, who
  holds those rights already. The bootstrap tests that count its own role assignments are
  unaffected.

**6.3 The semantic ranker, on the free plan.**
- Microsoft's semantic ranker billing page (ms.date 2026-06-16, read 2026-10-02): every service
  starts on the free plan, which "provides a monthly free request allowance".
- After that allowance, "semantic ranker requests return a billing error". So the free plan
  refuses rather than bills.
- The Azure pricing page (read 2026-10-02) gives the allowance as "First 1k requests free per
  month".
- A run needs about 330 ranked queries: 300, plus 30 repeats.
- The region page (ms.date 2026-08-24, read 2026-10-02) lists the semantic ranker in Australia
  East. That region is not marked as too busy for new services.

**6.4 Teardown.**
- Every session ends with `terraform destroy` of `infra/search`, then a check that
  `rg-releaselens-search` no longer exists.
- Every session starts with the same check, so a forgotten service from an earlier session is
  caught.
- The budget alert is the backstop.
- A forgotten Basic service costs US$3.19 a day (§8).

---

## 7. Components

| Unit | Where | Responsibility |
|---|---|---|
| Sample and question writer | `eval/app/retrieval/questions.py` | seeded stratified sample, the Claude call, mechanical checks, rewrite and replacement, the spot-check sheet, freezing |
| Corpus embedder | `eval/app/retrieval/embed.py` | embeds every chunk with one Azure model in batches, keyless, and saves vectors plus chunk ids locally; resumable |
| Arms | `eval/app/retrieval/arms.py` | one client per arm, each returning ranked chunks with scores and its own timing |
| Index builder | `eval/app/retrieval/index.py` | creates the AI Search index and uploads chunks with the saved `-small` vectors |
| Scoring and report | `eval/app/retrieval/score.py`, `report.py` | collapse to artefacts, top-1, MRR, lenient, margin, cost, the C1–C3 comparisons through `analysis.py`'s bootstrap and rule, the renderer |
| Worker `retrieve` | `src/ReleaseLens.Worker` | reads the frozen question file, runs `HybridRetriever` for each question with k = 50, writes ranked chunk ids, scores and per-question milliseconds as JSON lines |
| Bootstrap changes | `infra/bootstrap` | the two deployments, the `tfstate-search` container and the owner's role on it |
| Search stack | `infra/search` | the resource group, the service and the owner's two roles |

**Authentication.** Azure calls from Python use the owner's identity through the Azure CLI, as in
plan 3. Embeddings use the `https://ai.azure.com/.default` scope the app already uses. Search
uses `https://search.azure.com/.default`.

---

## 8. Cost

Rates read on 2026-10-02 from the Azure Retail Prices API (Australia East, USD) and the
Anthropic rates in `ModelPricing`.

| Item | Rate | Estimate |
|---|---|---|
| Embedding the corpus, `-small` | $0.02 per 1M tokens (Global Standard) | $0.21 |
| Embedding the corpus, `-large` | $0.13 per 1M tokens (Global Standard) | $1.34 |
| Embedding about 660 questions, both models | the same rates | under $0.01 |
| Writing the questions with Claude Sonnet 5 | $3 / $15 per 1M tokens | about $0.60–1.00 |
| AI Search Basic | $0.133 per hour | about $1–2 over 8–15 hours of sessions |
| Semantic ranker | free plan, about 330 requests | $0 |
| **Total** | | **about US$3.50–4.50** |

The token count for the corpus is the stored BGE `token_count`. OpenAI's tokenizer may count
somewhat differently (*unverified*). The run records the actual tokens billed.

---

## 9. Runbook, owner-gated

Each step marked **Ask first** waits for the owner's yes.

1. **Apply the bootstrap change** (§6.1, plus the state container). **Ask first.** No cost until
   used.
2. **Generate the questions,** about $1 on Anthropic. **Ask first.** Then the owner's spot-check
   (§3.5). Then freeze, commit, and push through a pull request. **Ask first.**
3. **Embed the corpus with both models,** about $1.55 on Azure. **Ask first.**
4. **One measurement session.** **Ask first.** About $1–2.
   1. Check that no search group exists.
   2. Apply `infra/search` and build the index.
   3. Run all six arms and the 30-question repeat.
   4. Destroy, and confirm `rg-releaselens-search` is gone.
5. **Render the report and publish it** in `eval/` and the README. **Ask first.** Then update the
   tracker. **Ask first.**

---

## 10. To verify during implementation

| Item | How |
|---|---|
| The exact azurerm 5.x argument names: `local_authentication_enabled`, `semantic_search_sku`, and the role names | the provider schema, through `terraform providers schema` in WSL |
| That AI Search accepts Entra tokens for index operations with keys disabled, using the two roles | the first real index creation |
| OpenAI's token count for the corpus against the stored BGE count | the embedding run's usage |
| The free ranker plan's allowance on a Basic service in Australia East | the run's ranker requests; a billing error would show the limit |
| That embedding 10.3M tokens at capacity 350 finishes without throttling | the embedding run |

---

## 11. What the README may claim afterwards

- The three comparisons' verdicts, worded exactly as the renderer gives them, with date and
  sample size.
- The per-arm figures, as descriptive.
- That AI Search ran keyless.

**It may not claim:**
- that one system is better without a declared difference
- anything about answer quality, since this measures retrieval only
- that ReleaseLens's search changed

---

## 12. Risks

- **Ambiguous questions:** a question with two right answers penalises an arm unfairly.
  Mitigated by the writing instruction, the lenient score, and the owner's spot-check with its
  stop rule.
- **Writer bias:** a writer model shapes the wording. Claude is not a contender. The bias is
  stated, not removed.
- **A forgotten search service:** $3.19 a day. Mitigated by the start and end checks, and the
  budget alert.
- **Small releases stratum:** n = 6, labelled too small to read.
- **Local against network latency:** not comparable as a contest; reported descriptively.
