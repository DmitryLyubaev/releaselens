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
The study has not been run, and nothing in this section is a result.

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
`/mnt/e/Projects/ReleaseLens`. Keys are read from the git-ignored `.env` straight into the
environment of the process that needs them, so none is typed, printed or written to a file.

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

Point the Worker at the restored database, bring its schema up to date, and issue the harness a
key. The key goes straight into this window's environment and is never shown. It is stored only
as a hash, so it cannot be recovered, only reissued. The check prints `True` or `False`, never
the key; go on only on `True`.

```powershell
$env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=releaselens_dev_only"
dotnet run --project src/ReleaseLens.Worker -- migrate
$env:EVAL_API_KEY = (dotnet run --project src/ReleaseLens.Worker -- issue-key semantic-kernel eval)
$env:EVAL_API_KEY -cmatch '^rl_[0-9a-f]{48}$'
```

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
$env:ANTHROPIC_API_KEY = (Select-String -Path .env -Pattern '^ANTHROPIC_API_KEY=(.*)$').Matches[0].Groups[1].Value.Trim().Trim('"')
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
$env:OPENAI_API_KEY = (Select-String -Path .env -Pattern '^OPENAI_API_KEY=(.*)$').Matches[0].Groups[1].Value.Trim().Trim('"')
dotnet run --project src/ReleaseLens.Api -c Release --no-build --no-launch-profile --urls http://localhost:8083
```

Each arm must answer `/health`:

```powershell
8081, 8082, 8083 | ForEach-Object { "$_ " + (Invoke-RestMethod "http://localhost:$_/health").status }
```

## 3. Start the harness

In a fourth window, from `eval/`. The judge needs `ANTHROPIC_API_KEY`, for its token counts and
its scoring:

```powershell
$env:ANTHROPIC_API_KEY = (Select-String -Path ..\.env -Pattern '^ANTHROPIC_API_KEY=(.*)$').Matches[0].Groups[1].Value.Trim().Trim('"')
.venv/Scripts/fastapi run app/main.py --port 8000
```

## 4. Price it, then run it

Both spend money, and each needs the owner's approval first. The dry run answers one query per
category on each arm, once, and prices the whole run. Spec §8 estimates the dry run at about
$0.55, and the run at about $5, which could be half or double that. From the window that holds
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
$report = Invoke-RestMethod -Method Post -Uri http://localhost:8000/eval/run -ContentType application/json -Body ($study | ConvertTo-Json -Depth 4)
$report | Select-Object run_id, estimated_cost_usd_before_run, estimate_note
```

For the run itself, set `$study.dry_run = $false` and send it again. Each report is also saved
as `eval/reports/<run_id>.json`, which is git-ignored. From `eval/`, this prints a run's
write-up exactly as it is published. It refuses a dry run, which prices the study and carries
no verdicts:

```powershell
.venv/Scripts/python -m app.study_report <run_id>
```
