# Golden query schema

Each entry:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable identifier. Never reuse or renumber — results are compared across runs by id. |
| `question` | yes | The question, exactly as a user would type it. |
| `category` | yes | One of: `factual`, `temporal`, `causal`, `aggregation`, `unanswerable`. |
| `expected_citations` | yes | Artefacts a correct answer must cite, as `type:key` (e.g. `issue:14111`). Empty for `unanswerable`. |
| `must_contain` | no | Substrings a correct answer must contain — exact identifiers only, never prose. |
| `must_not_contain` | no | Substrings that indicate a wrong answer. |
| `notes` | no | Why this query is in the set. |

`type` is the wire name of the entity: `commit`, `issue`, `pull_request`, `release`.
The key is the sha, the issue number, the pull request number, or the release tag
respectively — matching `EntityType.ToWireName()` and `EvidenceKey` in
`ReleaseLens.Core`, because the harness compares `f"{type}:{key}"` strings verbatim.

## Categories

- **factual** — one artefact answers it. Tests basic retrieval.
- **temporal** — needs date reasoning across releases. Tests `diff_between_releases`.
- **causal** — links a defect to its fix. Tests `find_regressions` and multi-hop retrieval.
- **aggregation** — counts or summarises across many artefacts. Tests whether the model
  fabricates numbers when retrieval is incomplete.
- **unanswerable** — the evidence genuinely does not contain the answer. **The correct
  response is to say so.** This category is the point of the set: a system that scores well
  on the first four and fabricates an answer here is worse than useless.

At least 6 of the 30–50 queries must be `unanswerable`.

## Corpus coverage, and why the set is weighted the way it is

The set was built against the first real ingest of `microsoft/semantic-kernel`. That
ingest ran with a smoke window of 2026-06-01, and the window only constrains two of the
four evidence types — GitHub's `pulls` and `releases` endpoints take no `since`
parameter, so those arrived complete.

| Table | Rows | Coverage at the time this set was written |
|---|---|---|
| `pull_requests` | 7,119 | Complete, all-time (#1 on 2023-02-27 → 2026-08-06) |
| `releases` | 276 | Complete, all-time (2023-04-25 → 2026-08-06) |
| `issues` | 163 | Only those updated on or after 2026-06-01 |
| `commits` | 60 | Only 2026-06-03 → 2026-08-06 |

A full 2024-01-01 ingest will run later and add roughly two and a half more years of
commits and issues. **A golden set that quietly goes wrong then is worse than no golden
set**, so three rules govern every entry here:

1. **`unanswerable` means unanswerable by nature, never by accident of the current
   slice.** Legitimate grounds: subjective judgement, forward-looking claims, and facts
   that live outside repository evidence entirely (operational telemetry, revenue,
   package download counts, internal deliberation, customer lists). Illegitimate: any
   question that is only unanswerable because the artefact has not been ingested yet.
   `gq-038` is phrased against "the most recent release in the evidence" rather than a
   named tag precisely so that it stays forward-looking as history grows.
2. **No aggregation hardcodes a count that the full ingest will change.** The count
   aggregations are over pull requests and releases, which are already complete. The one
   issue aggregation (`gq-035`) is scoped to June 2026: every issue created in that month
   necessarily has an update timestamp on or after 2026-06-01, so it was captured by the
   updated-since filter and the count of 14 is already final. There are no commit
   aggregations at all.
3. **`factual`, `temporal` and `causal` entries lean on pull requests and releases.**
   Issues appear only where a specific issue-to-fix chain is genuinely evidenced, not to
   fill a quota — 163 issues is a thin base for causal reasoning. The two
   `diff_between_releases` entries (`gq-014`, `gq-015`) both use release pairs whose
   windows fall entirely inside the ingested commit range, so their commit sets are
   already final too.

Every id, tag, issue number, pull request number, sha and username in `queries.yaml` was
checked against the database before the entry was written. Nothing was written from
memory of what the repository contains.

### Composition

| Category | Count |
|---|---|
| `factual` | 13 |
| `temporal` | 8 |
| `causal` | 7 |
| `aggregation` | 7 |
| `unanswerable` | 8 |
| **Total** | **43** |

### A note on `must_contain` for counts

`must_contain` is an exact substring test, so it can only express "the answer states this
value". Two aggregation entries (`gq-033`, `gq-035`) have no `must_contain`: their true
answers are beyond what a bounded retrieval can support, and the correct behaviour is to
say so rather than to produce a number. Asserting the number there would score an honest
decline as a failure, which is the opposite of what this set is for. Those entries are
scored by the hedging check and the groundedness judge instead.

## Harness

The week-3 fork is resolved in favour of **keeping the Python FastAPI eval service**,
implemented in `eval/` as Task 24. The `dotnet` CLI fallback would have been about half
the work and forfeited only the Python-web-frameworks gap, but that gap is the whole
reason the option existed: closing it is worth the extra few hours, and the golden set
itself is identical either way, so nothing in `queries.yaml` depends on the choice. The
service loads this file through `eval/app/golden.py`, scores each answer with the
deterministic metrics in `eval/app/metrics.py`, and adds an LLM groundedness judge on
top. No `src/ReleaseLens.Eval` project exists or should be created.
