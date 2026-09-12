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

- **`search_commits` still cannot say when its page is full.** `HybridRetriever` emits a note
  only when *fewer* than *k* chunks come back, so the genuinely-capped case carries no signal.
  Known, unfixed, and the largest remaining defect of that shape.

## Reproducing

With Postgres up, the API running, and `ANTHROPIC_API_KEY` set:

```bash
curl -sS -X POST http://localhost:8000/eval/run -H "Content-Type: application/json" -d '{"api_base_url":"http://127.0.0.1:5274","api_key":"<key>","per_category":1}'
```

Raise `per_category`, or omit it for all 43. Add `"dry_run":true` to price a sweep from one
query per category before committing to it.

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
