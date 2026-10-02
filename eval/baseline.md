This file holds the three-arm study of 2 October 2026 first, then the single-provider runs of
August as history, then how to run the study. The study's raw report is
`eval/reports/2c413fe9-….json`, which is git-ignored because it embeds answer and evidence text. The
section below is `python -m app.study_report 2c413fe9-76f1-4f5a-aae9-b7b2419642f0`'s output,
unedited.

# Three-arm study — 2 October 2026

- **Run id:** `2c413fe9-76f1-4f5a-aae9-b7b2419642f0`
- **Started:** 2026-10-02T03:25:38.360009+00:00
- **Sample:** 10 queries × 3 passes per arm: gq-001, gq-002, gq-014, gq-015, gq-022, gq-023, gq-029, gq-030, gq-036, gq-037
- **Cost of the run:** answering $1.5903, judging $1.1152, total $2.7055. Answering is what the arms reported over every outcome, rejected replies included, since they were billed. Judging is the harness's own cost, and is not charged to any arm.

## Arms

Each arm is a separate API process started with that provider alone.

| Arm | Expected provider | URL |
|---|---|---|
| A | `anthropic` | http://localhost:8081 |
| Z | `azure-openai` | http://localhost:8082 |
| O | `openai` | http://localhost:8083 |

Z authenticates as the owner (Azure CLI), not as the deployed managed identity.

## Method

- The unit of analysis is the query. Each query's delta is the mean of x − y over the passes where both arms have a value, so each arm's passes are averaged before the arms are compared.
- Each metric is scored only over the queries it applies to: groundedness over every query, citation recall and precision over the answerable ones, and the must_contain pass rate over those with something to contain. k is the queries with at least one pass where both arms have a value.
- The 95% interval is a bootstrap that resamples queries with their passes kept together: 10,000 resamples, seed 20260924, percentiles by nearest rank.
- The decision rule, pre-registered in spec §8, applies to the four quality metrics. A difference is declared only when the mean paired delta is ≤ −0.10 or ≥ +0.10 and its 95% interval excludes zero; an interval that touches zero does not exclude it. Anything else is inconclusive at its k queries × passes.
- The rule reads the mean and the interval bounds settled to 12 decimal places, so float error cannot flip a verdict at −0.10, +0.10 or 0.
- Groundedness is scored by a Claude Sonnet 5 judge, blinded to the arm.
- A filtered answer is scored as the fixed filtered reply the API returned in its place, on every metric that applies to its query, as any other answer is. Filtered answers are counted per arm under "Outcomes, filter events and models", so a delta that filter events may have driven can be told apart.
- Figures are the report's own, formatted: deltas, intervals and means to 3 decimal places, cost to 4, latency in whole milliseconds. A mean delta or an interval bound that 3 places would show as exactly 0, +0.10 or −0.10 without being it is shown to as many places as it takes to say which side it is on.

## Quality per arm

Descriptive. Each mean is over the arm's outcomes without errors, on the queries the metric applies to, where the outcome has a value for it; n is how many outcomes that is. The difference between two arms' means is not a comparison's delta, which is over the paired outcomes only. Judgements failed counts the answers put to the judge that came back with no score, because the judge failed or its reply was not a score in [0, 1]; they are left out of groundedness. Unanswerable handled is reported only, since two queries cannot support a conclusion.

| Arm | Groundedness | Judgements failed | Citation recall | Citation precision | must_contain pass rate | Unanswerable handled |
|---|---:|---:|---:|---:|---:|---:|
| A | 0.840 (n = 30) | 0 | 0.889 (n = 24) | 0.569 (n = 24) | 1.000 (n = 18) | 5 of 6 |
| Z | 0.783 (n = 30) | 0 | 0.646 (n = 24) | 0.585 (n = 24) | 0.889 (n = 18) | 4 of 6 |
| O | 0.860 (n = 30) | 0 | 0.674 (n = 24) | 0.576 (n = 24) | 1.000 (n = 18) | 2 of 6 |

## Outcomes, filter events and models

Recorded as legitimate differences between the arms, not explained away. No reply counts the errors on which the request got no reply, whose cost is unknown. Filtered counts the outcomes on which a content filter blocked the request, errors included. Models seen is each `model` the arm's outcomes without errors or filter events name, as the replies' metadata gives it. A filtered answer is left out because no response named its model.

| Arm | Outcomes | Errors | No reply | Filtered | Models seen |
|---|---:|---:|---:|---:|---|
| A | 30 | 0 | 0 | 0 | `claude-sonnet-5` |
| Z | 30 | 0 | 0 | 0 | `gpt-4.1-mini-2025-04-14` |
| O | 30 | 0 | 0 | 0 | `gpt-4.1-mini-2025-04-14` |

## Latency and cost per arm

Descriptive, with no significance claim. Latency and mean cost per query are over the outcomes without errors. Answering cost is over all of the arm's outcomes, because a rejected reply was still billed.

| Arm | p50 latency | p95 latency | Mean cost per query | Answering cost |
|---|---:|---:|---:|---:|
| A | 5,406 ms | 32,434 ms | $0.0488 | $1.4650 |
| Z | 3,049 ms | 9,101 ms | $0.0021 | $0.0631 |
| O | 2,821 ms | 6,432 ms | $0.0021 | $0.0623 |

## Z against O

Each delta is Z − O. Z's models seen: `gpt-4.1-mini-2025-04-14`. O's: `gpt-4.1-mini-2025-04-14`.

| Metric | Sample | Mean delta | 95% CI | Verdict |
|---|---|---:|---|---|
| Groundedness | 10 queries × 3 passes, 30 pairs | -0.077 | [-0.147, -0.017] | inconclusive at 10 queries × 3 passes |
| Citation recall | 8 queries × 3 passes, 24 pairs | -0.028 | [-0.083, +0.000] | inconclusive at 8 queries × 3 passes |
| Citation precision | 8 queries × 3 passes, 24 pairs | +0.008 | [-0.013, +0.038] | inconclusive at 8 queries × 3 passes |
| must_contain pass rate | 6 queries × 3 passes, 18 pairs | -0.111 | [-0.333, +0.000] | inconclusive at 6 queries × 3 passes |

### Per-query deltas, Z − O

— means the metric does not apply to the query, or no pass of it was paired.

| Query | Groundedness | Citation recall | Citation precision | must_contain pass rate |
|---|---:|---:|---:|---:|
| gq-001 | +0.000 | +0.000 | +0.000 | +0.000 |
| gq-002 | +0.000 | +0.000 | +0.000 | +0.000 |
| gq-014 | -0.167 | +0.000 | +0.000 | — |
| gq-015 | -0.300 | +0.000 | +0.101 | — |
| gq-022 | -0.200 | -0.222 | -0.033 | -0.667 |
| gq-023 | +0.000 | +0.000 | +0.000 | +0.000 |
| gq-029 | -0.100 | +0.000 | +0.000 | +0.000 |
| gq-030 | +0.000 | +0.000 | +0.000 | +0.000 |
| gq-036 | +0.000 | — | — | — |
| gq-037 | +0.000 | — | — | — |

## Z against A

Each delta is Z − A. Z's models seen: `gpt-4.1-mini-2025-04-14`. A's: `claude-sonnet-5`. It compares two different models, and does not test the keyless claim.

| Metric | Sample | Mean delta | 95% CI | Verdict |
|---|---|---:|---|---|
| Groundedness | 10 queries × 3 passes, 30 pairs | -0.057 | [-0.243, +0.130] | inconclusive at 10 queries × 3 passes |
| Citation recall | 8 queries × 3 passes, 24 pairs | -0.243 | [-0.556, +0.028] | inconclusive at 8 queries × 3 passes |
| Citation precision | 8 queries × 3 passes, 24 pairs | +0.016 | [-0.178, +0.203] | inconclusive at 8 queries × 3 passes |
| must_contain pass rate | 6 queries × 3 passes, 18 pairs | -0.111 | [-0.333, +0.000] | inconclusive at 6 queries × 3 passes |

### Per-query deltas, Z − A

— means the metric does not apply to the query, or no pass of it was paired.

| Query | Groundedness | Citation recall | Citation precision | must_contain pass rate |
|---|---:|---:|---:|---:|
| gq-001 | +0.000 | +0.000 | +0.000 | +0.000 |
| gq-002 | +0.367 | -1.000 | -0.528 | +0.000 |
| gq-014 | -0.333 | -1.000 | -0.097 | — |
| gq-015 | -0.667 | +0.167 | +0.236 | — |
| gq-022 | -0.167 | -0.111 | +0.022 | -0.667 |
| gq-023 | +0.200 | +0.000 | +0.000 | +0.000 |
| gq-029 | -0.300 | +0.000 | +0.495 | +0.000 |
| gq-030 | +0.333 | +0.000 | +0.000 | +0.000 |
| gq-036 | +0.000 | — | — | — |
| gq-037 | +0.000 | — | — | — |

---

# Evaluation — 12 August 2026

The current measured state of ReleaseLens against its golden query set. Every number came
from a real run; nothing is estimated. The August 11 baseline is kept at the end, because a
project that only reports its latest numbers is not showing you a measurement, it is showing
you a claim.

- **Code state:** after the judge fixes described under "What changed between the two runs"
- **Run id:** `64b3ee8b-1dbd-41f2-9030-8d7dba313554`
- **Started:** 2026-08-12T02:25:01Z
- **Answering model:** Claude Sonnet 5, `k=8` seed chunks · **Judge:** Claude Sonnet 5
- **Corpus:** 41,825 chunks — 2,921 commits, 3,805 issues, 7,121 pull requests, 276 releases
- **Raw report:** `eval/reports/64b3ee8b-….json` (git-ignored — it embeds answer and evidence text)

> This file names no commit hashes. The repository's history was rewritten after these runs,
> so the hashes they were taken at no longer exist here; changes are named by what they did
> instead. The run ids above are the durable identifiers.

## Method

Five queries, one per category, selected by the harness's `per_category` sampler. The golden
set holds 43; five is what the remaining API balance allowed, and sampling one per category
rather than the first five matters because the set is grouped by category with the eight
`unanswerable` entries last — any prefix sample would have been entirely `factual`.

**Every figure below is N=5, one observation per category.** Real measurements, but five of
them. No rate should be derived from them.

## Results

| Metric | 11 Aug | **12 Aug** | |
|---|---|---|---|
| Groundedness (LLM-judge) | not measured | **0.940** | scored 5 of 5 |
| Citation recall | 1.000 | **1.000** | |
| Citation precision (answerable) | 0.090 | **0.588** | |
| must_contain pass rate | 1.000 | **1.000** | |
| Unanswerable handled correctly | 1 of 1 | **1 of 1** | |
| Total cost, 5 queries | $0.9200 | **$0.2367** | agent-side |
| Worst single query | $0.7202 | **$0.0581** | gq-029 |
| p50 latency | 15,876 ms | 20,863 ms | |
| p95 latency | 58,028 ms | **22,647 ms** | |

### Per query

| id | category | groundedness | cost | latency | citations | precision |
|---|---|---:|---:|---:|---:|---:|
| gq-001 | factual | 1.0 | $0.0051 | 5,206 ms | 1 | 1.00 |
| gq-014 | temporal | 0.9 | $0.0342 | 20,863 ms | 21 | 0.10 |
| gq-022 | causal | 0.8 | $0.1290 | 22,647 ms | 4 | 0.75 |
| gq-029 | aggregation | 1.0 | $0.0581 | 21,708 ms | 8 | 0.50 |
| gq-036 | unanswerable | 1.0 | $0.0103 | 4,757 ms | 5 | 1.00 |

## How to read these

**Precision is 0.588, not the 0.669 the harness reports.** `citation_recall` and
`citation_precision` both return a vacuous 1.0 for a query expecting no citations, which is
every `unanswerable` entry. With one of five unanswerable, that pulls the reported mean up.
The figure above is over the four answerable queries.

**Groundedness is a mean over 5 of 5**, and the report now carries that denominator. It
matters: an earlier attempt today reported groundedness 1.000 from *two* scored queries out
of five, which reads as perfect and was not. An unparseable judgement scores `None`, never
zero — a harness fault must not masquerade as a hallucinating system — but that silently
shrinks the denominator, so the denominator now travels with the number.

**"1 of 1" is not 100%.** One correct refusal.

**The 12 Aug precision figure is not comparable with the 11 Aug one.** Until citations were
filtered to the artefacts the answer actually cites, the response carried every artefact any
tool had touched, so precision measured how many rows a tool returned rather than anything
about the answer. The rise from 0.090 is mostly the measurement becoming correct, not the
system improving.

## What changed between the two runs, and what it bought

Four defects were found and fixed, each by measuring rather than by reasoning:

**Aggregation cost — `count_evidence` and `list_releases`.** The 11 Aug run
showed cost spanning 77× across categories, with *"list every Java release tag"* at $0.72,
58 seconds and 337,570 input tokens — 78% of the whole run. Nothing computed; every tool
returned evidence chunks, so an aggregation could only be answered by retrieving toward
completeness. That query now costs **$0.0581 and runs in 21.7 seconds**, and p95 across the
run fell from 58.0s to 22.6s.

**Citations were not filtered to what the answer used.** See above.

**The judge had never worked.** It received bare identifiers —
`['issue:14111']` — and was asked whether every claim was supported by them. It correctly
reported it could not tell and scored 0.0. The defect stayed hidden because every earlier run
was a dry run, which skips the judge. It now receives the text of each cited artefact, opt-in
so production responses do not carry it.

**Recall did not fall, and I expected it to.** Filtering citations to those the answer used
should, in principle, have exposed queries where retrieval found the right artefact and the
model wrote around it — the old number credited the model for the retriever's work. It stayed
at 1.000, so on these five the model really was citing what mattered.

## What the judge actually caught

Now that it has evidence, its reasons are specific enough to act on:

- **gq-014 (0.9)** — *"the '19 commits' figure is anomalously cited to [E4][E5]"*. The answer
  attached a count to markers that do not establish it.
- **gq-022 (0.8)** — the PR details are fully supported, but *"the claim that dotnet-1.79.0
  'shipped the fix'"* rests on an inference the cited evidence does not carry.

Both are real, both are the kind of quiet over-claiming this metric exists to find, and
neither would be visible from recall, precision or `must_contain` — all three of which are
perfect on those queries.

## Known limits of this measurement

- **N=5.** One observation per category.
- **Run-to-run variance is real.** An intermediate run today put gq-029 at $0.0322 against
  $0.0581 here — the agent's tool-call count varies. Treat single-query costs as indicative.
- **Cost is agent-side only.** `total_cost_usd` sums the answering calls. The judge is priced
  separately by `estimated_cost_usd_before_run`.
- **Coverage starts in 2024** for commits. The aggregate tools report the corpus bounds with
  every result, but a question about 2023 commits is answerable only about the corpus.
- **The system prompt has changed since this run.** It told the model that `list_releases`
  results "carry no evidence marker and need none", which was false — that tool returns real
  release artefacts, and sixteen of the forty-three golden queries expect a release citation.
  The claim has been corrected. On `gq-029`, the only release query in this run, the model
  cited all four expected releases and scored recall 1.000, so it appears to have ignored the
  instruction — but a future run is not strictly comparable with this one on that axis.

- **`search_commits` could not say when its page was full, during this run.** `HybridRetriever`
  emitted a note only when *fewer* than *k* chunks came back, so the case where the candidate
  pool was full carried no signal at all. It has since been fixed: retrieval now reports how
  many chunks the text query matched in total, and says so when that exceeds the pool. The
  numbers above were measured before that, so an answer here that reads as confidently
  complete may have been drawn from a pool that was quietly full.

## Reproducing

With Postgres up, the API running, and `ANTHROPIC_API_KEY` set:

```bash
curl -sS -X POST http://localhost:8000/eval/run -H "Content-Type: application/json" -d '{"arms":[{"name":"A","base_url":"http://127.0.0.1:5274","expected_provider":"anthropic"}],"api_key":"<key>","per_category":1}'
```

The harness now takes a list of arms, one API process each, in place of the single API URL
this run was made with. One arm answered by Anthropic is this run's configuration; a reply from
any other provider is recorded as an error, not scored. Raise `per_category`, or omit it for
all 43. Add `"dry_run":true` to price a sweep from one query per category before committing
to it. The three-arm study is under
[Running the three-arm study](#running-the-three-arm-study).

---

# Previous: baseline of 11 August 2026

The first measured run, run id `6bf37544-…`. Kept for comparison.

| Metric | Value |
|---|---|
| Queries | 5, one per category |
| Citation recall | 1.000 |
| Citation precision (answerable) | 0.090 |
| must_contain pass rate | 1.000 |
| Unanswerable handled correctly | 1 of 1 |
| Groundedness | not measured — the run was a dry run, which skips the judge |
| p50 / p95 latency | 15,876 ms / 58,028 ms |
| Total cost | $0.9200 |

Per query: gq-036 $0.0093 · gq-001 $0.0117 · gq-014 $0.0266 · gq-022 $0.1522 ·
**gq-029 $0.7202, 58,028 ms, 48 citations, 337,570 input tokens**.

That last row is why the aggregate tools exist. It also priced a full 43-query sweep at
$8.01, against a remaining balance near $3.40 — which is why both runs are five queries and
not forty-three, recorded here so a later reader does not read the sample size as
carelessness.

Corpus at the time: 22,288 chunks, with issues and commits covering only a June 2026 window.
The 12 August run is against 41,825 chunks with commits back to January 2024, so the two runs
are not measuring retrieval over the same corpus.

---

# Running the three-arm study

How to run the study pre-registered in spec §8
(`docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md`). This is the method only.
Its results are in "Three-arm study — 2 October 2026" at the top of this file.

The same ten golden queries (`per_category=2`) go to three arms on each of three passes, and one
judge scores every answer without being told which arm wrote it. Each arm is a separate local
API process started with a single provider, and all three read the same restored corpus.

| Arm | Port | Provider (`expected_provider`) | Model | Auth |
|---|---|---|---|---|
| A | 8081 | `anthropic` | Claude Sonnet 5 | key |
| Z | 8082 | `azure-openai` | gpt-4.1-mini 2025-04-14 (Global Standard) | the owner's Entra identity, through the Azure CLI |
| O | 8083 | `openai` | gpt-4.1-mini | key |

Z against O is the comparison that tests the claim: the same model through two auth paths. Z
against A is reported as well, but it compares two different models.

The commands are PowerShell, run from the repository root unless a step says otherwise. Docker
runs inside WSL (Ubuntu) on this machine and is not on the Windows path, so each `docker`
command goes through `wsl.exe`, which starts in the same folder as
`/mnt/e/Projects/ReleaseLens`.

Keys reach only the environment of the window that needs them, and none is printed or written to
a file. On this machine `ANTHROPIC_API_KEY` is a Windows user environment variable, which every
new window inherits. Each window that needs a key checks that it is set, without printing it,
and asks for it at a masked prompt when it is not (`Read-Host -MaskInput`, PowerShell 7).

If a git-ignored `.env` exists in the repository root, a window can read its key from there
instead of the check, as here for the O arm; from `eval/` the path is `..\.env`:

```powershell
$env:OPENAI_API_KEY = (Select-String -Path .env -Pattern '^OPENAI_API_KEY=(.*)$').Matches[0].Groups[1].Value.Trim().Trim('"')
```

## 1. Restore the corpus

The corpus is the one every published figure was measured against: the owner's local backup
`releaselens-corpus-2026-08-12.dump`, kept outside the repository in the sibling folder
`ReleaseLens-backup`, beside its `RESTORE.md`. It goes into a database of its own,
`releaselens_eval`, which leaves the working database alone.

```powershell
wsl.exe -d Ubuntu --exec docker compose up -d postgres
wsl.exe -d Ubuntu --exec docker exec -e PGPASSWORD=releaselens_dev_only releaselens-postgres psql -U releaselens -d postgres -c "create database releaselens_eval"
wsl.exe -d Ubuntu --exec docker cp ../ReleaseLens-backup/releaselens-corpus-2026-08-12.dump releaselens-postgres:/tmp/corpus.dump
wsl.exe -d Ubuntu --exec docker exec -e PGPASSWORD=releaselens_dev_only releaselens-postgres pg_restore -U releaselens -d releaselens_eval --no-owner /tmp/corpus.dump
```

`pg_dump` carries no roles. On a fresh volume the role `releaselens_app` does not exist, and
`pg_restore` reports errors on the grants to it. If it does, create the role and re-apply the
grants by running migration 007 by hand, which is idempotent:

```powershell
wsl.exe -d Ubuntu --exec docker cp src/ReleaseLens.Storage/Migrations/007_app_role.sql releaselens-postgres:/tmp/007_app_role.sql
wsl.exe -d Ubuntu --exec docker exec -e PGPASSWORD=releaselens_dev_only releaselens-postgres psql -U releaselens -d releaselens_eval -f /tmp/007_app_role.sql
```

Point the Worker at the restored database, bring its schema up to date, raise the tenant's daily
token budget, and issue the harness a key. The key goes straight into this window's environment
and is never shown. It is stored only as a hash, so it cannot be recovered, only reissued. The
check prints `True` or `False`, never the key; go on only on `True`.

```powershell
$env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=releaselens_dev_only"
dotnet run --project src/ReleaseLens.Worker -- migrate
$env:RELEASELENS_Tenant__Slug = "semantic-kernel"
$env:RELEASELENS_Tenant__DailyTokenBudget = "20000000"
dotnet run --project src/ReleaseLens.Worker -- create-tenant
$env:EVAL_API_KEY = (dotnet run --project src/ReleaseLens.Worker -- issue-key semantic-kernel eval)
$env:EVAL_API_KEY -cmatch '^rl_[0-9a-f]{48}$'
```

The budget is raised because one tenant serves all three arms. They share one database and one
key, so they share the tenant's daily token budget, and `/query` refuses every arm with HTTP 429
once the tenant's tokens for the UTC day reach it. The restored tenant row keeps the budget it
was created with, most likely `create-tenant`'s default of 2,000,000. The study needs about 3.5
million: an estimate from the 12 August token counts and spec §8's cost per query, not a
measurement. Cut off partway, the study would publish a fraction of its pairs, and the money
already spent would buy a study that has to be run again.

`create-tenant` is an upsert on the slug. On the restored `semantic-kernel` tenant it keeps the
tenant and its evidence, sets the budget to 20,000,000, and rewrites the tenant's names from the
Worker's settings, which are the names it already has. It changes `releaselens_eval` only,
because `RELEASELENS_DB` points there. Step 4 checks the budget again before the paid run.

Keep this window open: it holds `EVAL_API_KEY`, and the study's request is sent from it.

Check the row counts against `RESTORE.md`:

```powershell
wsl.exe -d Ubuntu --exec docker exec -e PGPASSWORD=releaselens_dev_only releaselens-postgres psql -U releaselens -d releaselens_eval -c "select 'evidence_chunks' as table_name, count(*) from evidence_chunks union all select 'embeddings', count(*) from embeddings union all select 'files_changed', count(*) from files_changed union all select 'pull_requests', count(*) from pull_requests union all select 'issues', count(*) from issues union all select 'commits', count(*) from commits union all select 'releases', count(*) from releases"
```

| Table | Rows |
|---|---:|
| evidence_chunks | 41,825 |
| embeddings | 41,825 |
| files_changed | 34,561 |
| pull_requests | 7,121 |
| issues | 3,805 |
| commits | 2,921 |
| releases | 276 |

## 2. Start the three arms

Build once:

```powershell
dotnet build -c Release
```

Then start each arm in a window of its own, from the repository root. Each window sets
`RELEASELENS_DB` as in step 1, and only its own arm's settings. Each process must start: one
that stops at startup says why, and a model with no price is one such reason.

**A, on port 8081:**

```powershell
$env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=releaselens_dev_only"
$env:Chat__Providers__0 = "anthropic"
if ($env:ANTHROPIC_API_KEY) { "ANTHROPIC_API_KEY is set" } else { $env:ANTHROPIC_API_KEY = Read-Host -MaskInput "ANTHROPIC_API_KEY" }
dotnet run --project src/ReleaseLens.Api -c Release --no-build --no-launch-profile --urls http://localhost:8081
```

**Z, on port 8082.** Sign in first with `az login`, as the owner's personal account, the one
holding the Cognitive Services OpenAI User role on the Azure OpenAI account. Stop if a work
account is signed in. Behind a TLS-inspecting proxy that breaks `az`, give this window alone a
CA bundle that includes the proxy's root: the API calls `az` whenever it needs a token, and `az`
inherits the window's environment.

```powershell
$env:REQUESTS_CA_BUNDLE = "<path to a CA bundle that includes the proxy's root>"   # only behind TLS inspection
az login
az account show --query user.name -o tsv
```

```powershell
$env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=releaselens_dev_only"
$env:Chat__Providers__0 = "azure-openai"
$env:AzureOpenAi__BaseUrl = "<the bootstrap stack's azure_openai_base_url output>"
$env:AzureOpenAi__Deployment = "releaselens-chat"
$env:AzureOpenAi__Credential = "AzureCli"
$env:AzureOpenAi__TenantId = "<the bootstrap stack's tenant_id output>"
dotnet run --project src/ReleaseLens.Api -c Release --no-build --no-launch-profile --urls http://localhost:8082
```

Z holds no key. It authenticates as the owner through the Azure CLI, not as the managed identity
the deployed app uses. `releaselens-chat` is the bootstrap stack's `azure_openai_deployment`
output.

**O, on port 8083:**

```powershell
$env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=releaselens_dev_only"
$env:Chat__Providers__0 = "openai"
$env:OpenAi__Model = "gpt-4.1-mini"
if ($env:OPENAI_API_KEY) { "OPENAI_API_KEY is set" } else { $env:OPENAI_API_KEY = Read-Host -MaskInput "OPENAI_API_KEY" }
dotnet run --project src/ReleaseLens.Api -c Release --no-build --no-launch-profile --urls http://localhost:8083
```

Each arm must answer `/health`:

```powershell
8081, 8082, 8083 | ForEach-Object { "$_ " + (Invoke-RestMethod "http://localhost:$_/health").status }
```

## 3. Start the harness

In a fourth window, from `eval/`. The judge needs `ANTHROPIC_API_KEY`, for its token counts and
its scoring. A run that asks for the judge fails before it sends any query when the key is
missing.

```powershell
if ($env:ANTHROPIC_API_KEY) { "ANTHROPIC_API_KEY is set" } else { $env:ANTHROPIC_API_KEY = Read-Host -MaskInput "ANTHROPIC_API_KEY" }
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`app.main:app` imports the harness as the package `app`, which its relative imports need.
`fastapi run app/main.py` fails with "attempted relative import with no known parent package",
because `eval/app/` has no `__init__.py`.

`--host 127.0.0.1` keeps the harness on this machine. This window holds the Anthropic key:
bound to every interface, as `fastapi run` binds by default, the harness would let anyone on the
network who could reach port 8000 point it at an arm of their own and spend the key on
judgements.

## 4. Price it, then run it

Both spend money, and each needs the owner's approval first. The dry run answers one query per
category on each arm, once, and prices the whole run. Spec §8 estimates the dry run at about
$0.55, and the run at about $5, which could be half or double that.

Each request is one call that returns only when the sweep has finished: minutes for the dry run,
and tens of minutes for the run. `-TimeoutSec 0` makes PowerShell wait as long as that takes,
rather than give up while the harness is still spending. From the window that holds
`EVAL_API_KEY`:

```powershell
$study = @{
    arms = @(
        @{ name = "A"; base_url = "http://localhost:8081"; expected_provider = "anthropic" }
        @{ name = "Z"; base_url = "http://localhost:8082"; expected_provider = "azure-openai" }
        @{ name = "O"; base_url = "http://localhost:8083"; expected_provider = "openai" }
    )
    comparisons = @(@("Z", "O"), @("Z", "A"))
    per_category = 2; passes = 3; judge = $true; dry_run = $true
    api_key = $env:EVAL_API_KEY
}
$report = Invoke-RestMethod -Method Post -Uri http://localhost:8000/eval/run -ContentType application/json -Body ($study | ConvertTo-Json -Depth 4) -TimeoutSec 0
$report | Select-Object run_id, estimated_cost_usd_before_run, estimate_note
```

**If the call fails, do not send it again.** A client that gives up, through a timeout, Ctrl+C
or a closed window, ends only the client. The harness carries on, and when the sweep finishes it
still saves the report as `eval/reports/<run_id>.json`, which is git-ignored; the harness's
window logs `POST /eval/run` at that moment. Sending the request again would start a second paid
run beside the first, drawing on the same token budget. Check `eval/reports/` first, and take
the report from there once it appears:

```powershell
Get-ChildItem eval/reports/*.json | Sort-Object LastWriteTime | Select-Object -Last 3 Name, LastWriteTime
$report = Get-Content eval/reports/<run_id>.json -Raw | ConvertFrom-Json
```

### Before the run: the dry run's checks

Each must pass before the owner is asked to approve the run. From the dry run's `$report`:

```powershell
$report.outcomes | Group-Object arm | ForEach-Object {
    $budget = $_.Group | ForEach-Object { $_.tokens_in + $_.tokens_out }
    $all = $_.Group | ForEach-Object { $_.tokens_in + $_.cache_read_input_tokens + $_.tokens_out }
    [pscustomobject]@{
        arm = $_.Name
        mean_budget_tokens = [math]::Round(($budget | Measure-Object -Average).Average)
        max_tokens_with_cached = ($all | Measure-Object -Maximum).Maximum
        fastest_ms = [math]::Round(($_.Group | Measure-Object latency_ms -Minimum).Minimum)
        errors = @($_.Group | Where-Object { $null -ne $_.error }).Count
    }
} | Format-Table
$report.outcomes | Where-Object { $null -ne $_.error } | Select-Object arm, id, error
$rounds = $report.outcomes | Group-Object id, pass_index | ForEach-Object {
    $z = $_.Group | Where-Object arm -eq "Z"
    $z_tokens = $z.tokens_in + $z.cache_read_input_tokens + $z.tokens_out
    $round_ms = ($_.Group | Measure-Object latency_ms -Sum).Sum
    [pscustomobject]@{ id = $z.id; z_tokens = $z_tokens; round_ms = $round_ms; z_tpm = [math]::Round($z_tokens * 60000 / $round_ms) }
}
$rounds | Sort-Object z_tpm -Descending | Format-Table id, z_tokens, @{ n = "round_ms"; e = { [math]::Round($_.round_ms) } }, z_tpm
$fastest_round_ms = ($rounds | Measure-Object round_ms -Minimum).Minimum
$z_paired_tpm = ($rounds | Measure-Object z_tpm -Maximum).Maximum
$reservation_bound_tpm = [math]::Round(2048 * 6 * 60000 / $fastest_round_ms)
[pscustomobject]@{
    z_paired_tpm = $z_paired_tpm
    reservation_bound_tpm = $reservation_bound_tpm
    capacity_check_tpm = $z_paired_tpm + $reservation_bound_tpm
    unreachable_bound_tpm = [math]::Round(($rounds | Measure-Object z_tokens -Maximum).Maximum * 60000 / $fastest_round_ms)
}
```

1. **Each arm's tokens per query, and no errors.** Record `mean_budget_tokens` for each arm.
   Every arm must show 0 errors, and in particular no wrong-provider error, which reads
   `arm Z expected azure-openai; answered by …`. A dry run with any error also refuses to
   estimate. Fix the arm and run the dry run again.
2. **The capacity check (spec §4.8).** Z's tokens a minute, at their highest, must stay under
   the deployment's 300,000 tokens per minute. In the study Z answers once in each A-Z-O round,
   not back to back, so each of Z's queries is paired with its own round: the A, Z and O
   latencies for the same query, summed. The last block above does this:
   - **`z_tpm`, for each query:** Z's tokens for it, cached input included, × 60000 / its own
     `round_ms`. `z_paired_tpm` is the largest.
   - **`reservation_bound_tpm`:** Azure also counts each request's `max_tokens` (2,048) against
     the minute. Z makes no more requests a minute than the most rounds a minute holds,
     60000 / the fastest `round_ms`, × the agent's 6 iterations at most (`Agent:MaxIterations`).
     So this is 2,048 × 6 × 60000 / the fastest `round_ms`, an upper bound, not a measurement.
   - **`capacity_check_tpm`:** the two added. This is the figure that must stay under 300,000.
   - **`unreachable_bound_tpm`:** Z's largest query × 60000 / the fastest `round_ms`, as if the
     largest query ran in every round at the fastest round's pace. A large query does not run
     that fast, so this cannot occur: it is a pessimistic bound for the record, not the check.

   Adjacent heavy queries can still peak above the typical rate, since two in a row can fall in
   the same minute; spec §4.8 has the dry run's figures. The check covers only the dry run's
   sample of one query per category, so it does not cover gq-023, the other half of the heavy
   causal pair; that is why the capacity has headroom above the check. Revise the price estimate
   if the check fails, and report both numbers to the owner.
3. **The tenant budget (step 1).** Read the budget and what today has used, in UTC as `/query`
   counts it. On the dry run's UTC day, `used_today` already holds the dry run's own tokens.

   ```powershell
   wsl.exe -d Ubuntu --exec docker exec -e PGPASSWORD=releaselens_dev_only releaselens-postgres psql -U releaselens -d releaselens_eval -c "select t.daily_token_budget, coalesce(u.tokens_in + u.tokens_out, 0) as used_today from tenants t left join token_usage u on u.tenant_id = t.tenant_id and u.usage_date = (now() at time zone 'utc')::date where t.slug = 'semantic-kernel'"
   ```

   `daily_token_budget` must read 20,000,000. `used_today`, plus 30 times the sum of the three
   arms' `mean_budget_tokens`, must stay well under it: each arm answers 10 queries on 3 passes,
   and the run's tokens could be double the dry run's. If it would not fit, wait for the next UTC
   day or raise the budget again as in step 1.
4. **The judge's allowance.** The estimate allows each judgement 512 output tokens, which is an
   allowance and not a measurement. The judge may write up to its cap of 2,048. Over the run's 90
   judgements, at Sonnet 5's $15 per million output tokens, the worst case is
   90 × 2,048 × $15/M ≈ $2.76 of judge output, against the 90 × 512 × $15/M ≈ $0.69 the estimate
   includes. Tell the owner the worst case, about $2.07 above the estimate.

For the run itself, set `$study.dry_run = $false` and send it the same way, with
`-TimeoutSec 0`, and with the same rule: if the call fails, check `eval/reports/` before
anything else. From `eval/`, this prints a run's write-up exactly as it is published. It refuses
a dry run, which prices the study and carries no verdicts:

```powershell
.venv/Scripts/python -m app.study_report <run_id>
```
