# Baseline evaluation — 11 August 2026

The first measured run of ReleaseLens against its golden query set. Every number here
came from a real run; nothing is estimated or carried over from a previous version.

- **Commit:** `3e25a6d`
- **Run id:** `6bf37544-4cb6-4b5f-b51c-ce5f8edc82a6`
- **Started:** 2026-08-11T05:58:56Z
- **Answering model:** Claude Sonnet 5 via the Anthropic provider, `k=8` seed chunks
- **Raw report:** `eval/reports/6bf37544-4cb6-4b5f-b51c-ce5f8edc82a6.json` (git-ignored —
  it embeds full answer text)

## Method, and why it is five queries and not forty-three

The golden set holds 43 queries. This run used **five — one per category**, selected by
the harness's `per_category` sampler.

That was a budget decision, taken with the numbers in hand rather than as a guess. The
run itself priced a full sweep at **$8.01**, against a remaining platform balance of about
$3.40. A full sweep was not affordable, and a partial sweep large enough to be interesting
would have consumed most of the balance for numbers that a later, cheaper system could
produce again.

Sampling one per category rather than the first five matters. The golden set is grouped by
category with the eight `unanswerable` entries last, so any prefix-based sample cheap
enough to run would have consisted entirely of `factual` queries — and unanswerable
accuracy, the metric the set exists for, would have reported null without complaining.

**Treat every figure below as N=5, one observation per category.** They are real
measurements, not estimates, but they are five measurements.

## Corpus under test

Ingested from `microsoft/semantic-kernel`:

| evidence | rows | coverage |
|---|---|---|
| pull requests | 7,119 | complete, all-time (2023-02-27 → 2026-08-06) |
| releases | 276 | complete, all-time |
| issues | 163 | only since 2026-06-01 |
| commits | 60 | only since 2026-06-03 |
| chunks / embeddings | 22,288 / 22,288 | — |

GitHub's `pulls` and `releases` endpoints take no `since` parameter, so those arrived
complete even though the ingest window was set to June 2026. Issues and commits are the
smoke window only. Queries lean on the complete half of the corpus for that reason.

## Results

| metric | value | reading |
|---|---|---|
| citation recall | **1.000** | every expected artefact was cited, in all five categories |
| citation precision (reported mean) | 0.272 | inflated — see below |
| **citation precision (answerable only)** | **0.090** | the honest figure |
| must_contain pass rate | **1.000** | every required identifier appeared |
| unanswerable accuracy | **1.000** | 1 of 1 — declined rather than inventing |
| groundedness | **not measured** | see below |
| p50 latency | 15,876 ms | |
| p95 latency | 58,028 ms | one query, see below |
| total cost | $0.92 | for five queries |

### Three caveats that change how these read

**Precision 0.272 is not the real number.** `citation_recall` and `citation_precision`
both return a vacuous 1.0 when a query expects no citations, which is every `unanswerable`
entry — by design, since `unanswerable_correct` is what scores those. With one of five
queries unanswerable, that vacuous 1.0 pulls the reported mean up. **Over the four
answerable queries precision is 0.090.** Recall is unaffected: all four answerable queries
genuinely scored 1.0.

**Groundedness was not measured at all.** The run was executed in `dry_run` mode, which
skips the LLM-judge to price a sweep before committing to it. `mean_groundedness` is
`null`. There is no groundedness baseline yet, and the harness should not be described as
having produced one.

**Unanswerable accuracy is 1 of 1.** A single correct refusal. It is the right sign and it
is not a rate.

`unanswerable_correct` is a keyword heuristic — it looks for a decline marker and the
absence of a numeric assertion — so it can in principle be satisfied by an answer that
hedges its way past the check. It was not, here. The actual answer to *"What is the
production incident rate of Semantic Kernel deployments at Microsoft?"* opens:

> The evidence provided does not contain any information about production incident rates
> for Semantic Kernel deployments at Microsoft.

and then names what the retrieved evidence *is* about — dependency bumps, a telemetry
proposal, a versioning ADR — rather than stretching any of it into an answer. That is the
behaviour the category exists to test, and on this query the heuristic and the reality
agree. On a larger sample they may not always; the heuristic is a screen, not a judge.

## Per-query detail

| id | category | cost | latency | citations | precision | input tokens |
|---|---|---:|---:|---:|---:|---:|
| gq-036 | unanswerable | $0.0093 | 4,980 ms | 6 | — | 3,446 |
| gq-001 | factual | $0.0117 | 8,928 ms | 8 | 0.125 | 1,987 |
| gq-014 | temporal | $0.0266 | 15,876 ms | 26 | 0.077 | 8,232 |
| gq-022 | causal | $0.1522 | 25,227 ms | 40 | 0.075 | 69,844 |
| gq-029 | aggregation | $0.7202 | 58,028 ms | 48 | 0.083 | 337,570 |

No query errored, none ran degraded, and no answer contained an unresolved `[E<n>]`
marker — the model never cited evidence it had not been shown.

## The two findings worth acting on

### Cost is uneven by a factor of 77, and one query dominates

`gq-029` — *"List every Java release tag ever published in this repository"* — cost $0.72,
took 58 seconds, and pushed **337,570 input tokens**. It alone is 78% of the run's cost.

The cause is structural rather than accidental. There is no counting or enumeration tool,
so an aggregation question can only be answered by retrieving its way to completeness: the
agent searches, sees a partial list, searches again, and accumulates the whole corpus in
context. Prompt caching barely helps — **27,594 of 421,079 input tokens were served from
cache, a 6.6% hit rate** — because each iteration appends new evidence and moves the cache
boundary.

This is the single lever on both cost and p95 latency. It is why a full sweep prices at $8
rather than the cents the plan originally assumed.

### The system over-cites

Precision of 0.090 on answerable queries means roughly one citation in eleven was one the
question actually needed. The agent cites essentially everything it retrieves — 48
artefacts on `gq-029`, 40 on `gq-022`.

This is not the duplicate-citation defect fixed in `fc03c62`; that one is gone, and these
citations are distinct artefacts. It is a judgement problem: nothing asks the model to cite
only what it used. Recall being a clean 1.000 says retrieval is finding the right evidence,
so there is real headroom here — precision can rise without recall falling.

**Any precision figure recorded before `fc03c62` is not comparable with these.** That
commit changed the denominator by collapsing per-chunk citations into per-artefact ones.

## What this baseline can and cannot support

**It can** support claims that retrieval finds the right evidence (recall 1.000 across five
categories), that required identifiers appear (1.000), that the system declined an
unanswerable question rather than inventing an answer, and that cost and latency are
dominated by aggregation-style questions.

**It cannot** support any claim about groundedness, any rate framed as a percentage of the
golden set, or any comparison against a previous version — this is the first measurement
there has been.

## Reproducing this

With Postgres up, the API running, and `ANTHROPIC_API_KEY` set:

```bash
curl -sS -X POST http://localhost:8000/eval/run -H "Content-Type: application/json" -d '{"api_base_url":"http://127.0.0.1:5274","api_key":"<key>","per_category":1,"dry_run":true}'
```

Drop `dry_run` to score groundedness as well; that adds a judge call per answered query.
Raise `per_category`, or omit it entirely for all 43 — at roughly $8 for the full set on
the numbers above.
