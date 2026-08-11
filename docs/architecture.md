# Architecture

The README carries the argument. This carries the detail.

## Components

```
GitHub REST                    ┌─────────────────────────────────────┐
     │  ETag conditional       │  Worker (.NET 10)                   │
     ▼  requests               │  migrate · create-tenant            │
IEvidenceSource ───────────────┤  ingest  · issue-key                │
                               └──────────────┬──────────────────────┘
                                              │ chunk → embed
                          BGE-small-en-v1.5   │ (ONNX, in-process, 384-d)
                                              ▼
                    ┌──────────────────────────────────────────────┐
                    │  PostgreSQL 17 + pgvector                    │
                    │  evidence tables · evidence_chunks           │
                    │  embeddings (HNSW) · row-level security      │
                    └──────────────┬───────────────────────────────┘
                                   │ hybrid retrieval
                                   ▼
     ┌───────────────────────────────────────────────────────────┐
     │  API (ASP.NET Core minimal)                               │
     │  api-key auth → budget check → QueryAgent                 │
     │                                                           │
     │  QueryAgent: seed evidence + tool loop                    │
     │    search_commits · get_issue                             │
     │    diff_between_releases · find_regressions               │
     │                                                           │
     │  FallbackChatProvider → Anthropic ─┐                      │
     │                       → OpenAI-wire ┴─► degraded 200      │
     └───────────────────────────┬───────────────────────────────┘
                                 │ OTLP
                                 ▼
                   Aspire Dashboard (local) / Azure Monitor
```

The evaluation harness (Python, FastAPI) sits outside this, calling the API's `/query` like
any other client. It is never deployed.

## Schema

Seven migrations. Evidence is one table per artefact type, all keyed by `(tenant_id, …)`:

| Table | Key | Notes |
|---|---|---|
| `tenants` | `tenant_id` | slug, display name, daily token budget |
| `api_keys` | `api_key_id` | SHA-256 of the key; the key itself is shown once |
| `commits` | `(tenant_id, sha)` | |
| `files_changed` | `(tenant_id, sha, path)` | FK to `commits`, trigram index on `path` |
| `issues` | `(tenant_id, number)` | GIN index on `labels` |
| `pull_requests` | `(tenant_id, number)` | indexed on `merged_at` and `merge_commit_sha` |
| `releases` | `(tenant_id, tag)` | |
| `evidence_chunks` | `chunk_id` | `content_tsv` generated column for full-text |
| `embeddings` | `chunk_id` | `vector(384)`, HNSW index |
| `embedding_dead_letter` | `dead_letter_id` | chunks that failed to embed, with attempts, last error and next attempt time |
| `ingest_checkpoints` | `(tenant_id, entity_type)` | cursor, ETag, items seen |
| `token_usage` | `(tenant_id, usage_date)` | drives the daily budget |

`evidence_chunks` and `embeddings` are separate tables. That costs a join on retrieval and
buys re-embedding with a different model without touching chunk rows.

Foreign keys between chunks and embeddings are composite — `(tenant_id, chunk_id)` — because
a single-column FK let one tenant write an embedding pointing at another tenant's chunk. That
was found by trying it against a live database, not by reading the schema.

### Row-level security

Every evidence table has `FORCE ROW LEVEL SECURITY` and a policy keyed on a per-transaction
setting. `FORCE` alone is not enough: Postgres exempts superusers and `BYPASSRLS` roles
unconditionally, so the policies never fire against a container's bootstrap superuser. Opening
a tenant scope therefore does both:

```sql
set local role releaselens_app;                                  -- drop the bypass
select set_config('releaselens.tenant_id', $1, true);            -- scope to this transaction
```

`SET LOCAL` is transaction-scoped, so a pooled connection cannot leak either setting to its
next borrower.

## Retrieval

Both arms run as CTEs in one round trip, then blend:

```
score = α · vector_score + (1 − α) · text_score_norm        α = 0.6
```

- `vector_score` is `1 − cosine_distance`, from the HNSW index.
- `text_score` is `ts_rank_cd` over `content_tsv` via `websearch_to_tsquery`.
- `text_score_norm` divides by the **pool maximum against a fixed floor of zero**, not true
  min-max. Rescaling against the empirical minimum would zero out the weakest genuine text
  match whenever every candidate matches, penalising exactly the single-arm hits the full
  outer join exists to keep.
- A `full outer join` keeps chunks found by only one arm.

`hnsw.iterative_scan = 'relaxed_order'` is set for the transaction. The HNSW index has no
tenant awareness, so under RLS the walk finds candidates first and the tenant filter is a
post-filter; without iterative scan, a tenant whose vectors are sparse relative to the whole
index gets back fewer rows than its corpus actually contains. `relaxed_order` is sufficient
because the vector CTE is a candidate pool that the blend re-ranks, not a final ordering.

Deliberately not a reranking model — that is a stated non-goal.

## Citations

A citation identifies an **artefact**, not a chunk. Both the seed-evidence path and the tool
path resolve markers through one function, so an entity split across several chunks is cited
once and all of its chunks carry the same `[E<n>]` label. Labels can therefore repeat within
the evidence block and are not necessarily in ascending order; the system prompt says so.

Because markers are issued densely — a new number only ever at the moment a citation is
appended — validating the model's answer is a range check: any `[E<n>]` outside `1..count` is
reported in `metadata.unresolvedCitationMarkers` rather than silently accepted.

## Designed failure modes

Each of these has a test.

| Condition | Behaviour |
|---|---|
| Source returns `304 Not Modified` | Ingest nothing, keep the checkpoint where it was |
| A chunk fails to embed | Write it to `embedding_dead_letter` with the reason; the run continues and reports the count |
| Retrieval finds fewer than *k* chunks | Say so in `RetrievalResult.Note`, surfaced in response metadata, rather than truncating silently |
| Primary chat provider unavailable | Fall back to the secondary provider |
| Both providers unavailable | Return the retrieved evidence unsynthesised, HTTP 200 with `degraded: true` and a reason — not a 500 |
| Daily token budget exhausted | HTTP 429, checked *before* the model call and inserted-then-locked so two concurrent requests cannot both pass |

The last row is the one that needed care: a naive read-then-check is a time-of-check /
time-of-use race, so the usage row is inserted and then selected `FOR UPDATE`.

## Ingestion notes

`pulls` and `releases` take no `since` parameter, so they are always read in full and their
checkpoints track only items seen. `issues` and `commits` do take `since` — but GitHub filters
issues on *updated* time, so an issues checkpoint must track `updated_at` or it falls
permanently behind the true high-water mark.

Chunking never slices between the halves of a surrogate pair. A lone surrogate is invalid
UTF-16 and Npgsql's encoder rejects it, which killed the first real ingest on an emoji in a
commit message.
