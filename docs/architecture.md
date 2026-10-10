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
     │  routes: /query · /health                                 │
     │          GET /evidence/tools · POST /evidence/tools/{name}│
     │  api-key auth → budget check → QueryAgent                 │
     │                                                           │
     │  QueryAgent: seed evidence + tool loop                    │
     │    retrieve: search_commits · get_issue · list_releases   │
     │              diff_between_releases · find_regressions     │
     │    compute:  count_evidence                               │
     │                                                           │
     │  FallbackChatProvider → Anthropic ─┐                      │
     │                       → OpenAI-wire ┴─► degraded 200      │
     └───────────────────────────┬───────────────────────────────┘
                                 │ OTLP
                                 ▼
                   Aspire Dashboard (local only; see Azure deployment)
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

### The text arm does not use its index, and cannot

`evidence_chunks_tsv_idx` is a GIN index on `content_tsv`. Under RLS it is never used. The
same `count(*)` over a rare term, against a 41,825-chunk corpus:

| | plan | rows examined | time |
|---|---|---|---|
| As a superuser, RLS not applied | GIN index scan, 5 heap blocks | 5 | 21 ms |
| Under `releaselens_app` | Seq Scan | 41,825 | 262 ms |
| Under `releaselens_app`, `enable_seqscan = off` | tenant btree, then filter | 41,825 | 110 ms |

Forcing sequential scans off does not reach the index — Postgres takes the tenant btree and
discards 41,820 rows rather than touch the GIN index. A composite `gin (tenant_id, content_tsv)`
via `btree_gin` was built and measured too, and changes nothing: still a Seq Scan.

The cause is that `ts_match_vq`, the function behind `tsvector @@ tsquery`, is not leakproof
(`pg_proc.proleakproof` is false). An RLS policy is a security qual, and Postgres will not
evaluate a non-leakproof qual ahead of one — an index condition is evaluated during the scan,
which would put the match ahead of the tenant check. So the `@@` predicate can only ever be a
post-filter here, and no index shape changes that.

The consequence is that every text retrieval scans the tenant's chunks: about 70 ms at the
current corpus size, growing linearly with it. This is a property of combining RLS with
full-text search, not of any one query, and it predates the retrieval code's current shape —
it was found while measuring something else. It is recorded rather than fixed: the documented
remedy is `ALTER FUNCTION ts_match_vq(tsvector, tsquery) LEAKPROOF`, which restores the index
by permitting the match to run before the tenant check, and that is a tenancy decision — the
isolation guarantee is the thing this project claims — not a performance tweak.

One thing follows from it directly. `RetrievalResult.TextMatchCount` reports how many chunks
the text query matched in total, so a caller can tell a full candidate pool from an exhausted
corpus. It is counted by `count(*) over ()` on the scan that ranks the pool, not by a second
query: since that scan reads every matching row regardless, counting costs almost nothing
(72 ms against 71 ms unscanned on a rare term) and the count is exact. An earlier version used
a separate CTE capped at 1001 rows, which cost a full duplicate scan — 142 ms against 72 ms —
and reported every figure above a thousand as the same thousand. The cap was protecting against
a cost the index would have made real; without the index there is nothing to protect.

Deliberately not a reranking model — that is a stated non-goal.

## Citations

A citation identifies an **artefact**, not a chunk. Both the seed-evidence path and the tool
path resolve markers through one function, so an entity split across several chunks is cited
once and all of its chunks carry the same `[E<n>]` label. Labels can therefore repeat within
the evidence block and are not necessarily in ascending order; the system prompt says so.

Because markers are issued densely — a new number only ever at the moment a citation is
appended — validating the model's answer is a range check: any `[E<n>]` outside `1..count` is
reported in `metadata.unresolvedCitationMarkers` rather than silently accepted. That check runs
against the full accumulated evidence pool, whose size is reported as
`metadata.accumulatedCitationCount`; only afterwards is the response's `citations` array
narrowed to the artefacts the answer actually cites. Because that array is a subset, each entry
carries its `marker` explicitly and a consumer must never infer one from array position.

A citation names an artefact; only its **text** says whether a claim about it is true. A
request may set `includeEvidence: true` to receive that text on each citation's `evidence`
field — every passage of the artefact the agent read, not the first, because an artefact
split across chunks shares one marker and a claim resting on its third chunk would read as
unsupported to anyone shown only its first. It is off by default and absent from the payload
when off: a chunk runs to roughly a thousand characters and an answer can cite twenty
artefacts, so the cost falls on the one consumer that needs it — the groundedness judge in
the eval harness, which was scoring 0.0 on sound answers because bare identifiers are
unfalsifiable. Nothing in `evidence` is truncated; an empty array means the artefact reached
the agent with no attributable text, which is a different fact from the field being absent.
The degraded path honours the flag too, and the flag changes nothing else about the
response — the same query with it on and off yields an identical citation list, so
`citation_recall` and `citation_precision` cannot move because of it.

A tool result carries its `ResultKind` — `Evidence` or `Computed`. `Computed` means the
result is a figure rather than an artefact: `count_evidence` is its only producer, and it
carries no citations because there is nothing to cite. Every other tool reports `Evidence`,
including `list_releases`, which runs an exhaustive query rather than a sampled one but still
returns citable release rows, with real citations attached. The kind is a property of what
the result is, not a hint the model alone was given — so the evidence API can publish it on
the response and an external consumer can honour it without having read the system prompt.

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

The stored checkpoint takes precedence over the configured `GitHub:SinceUtc`, which is what
makes a daily run cheap and resumable — and it means widening the window has no effect until
the checkpoint is cleared, so a backfill requires the worker's `reset-checkpoints` command
first. The reset clears the ETag as well as the cursor: a surviving ETag earns a 304 and the
pipeline skips the source, so the backfill would ingest nothing and still report success.

Chunking never slices between the halves of a surrogate pair. A lone surrogate is invalid
UTF-16 and Npgsql's encoder rejects it, which killed the first real ingest on an emoji in a
commit message.

## Azure deployment

All four Terraform stacks are built and tested with mocked plans, and the deploy, destroy and
gateway-check workflows are built. **The app stack was deployed and destroyed twice on 1 October 2026**; the
[README's record](../README.md#two-stack-deployment-1-october-2026) has the runs. Apart from
the verified items below, this section describes what the code configures. What the checks after the bootstrap apply showed is under
[Verified by the bootstrap apply](#verified-by-the-bootstrap-apply). The owner's runbook is
[infra/bootstrap/README.md](../infra/bootstrap/README.md). The
decisions and the reasons for them are in the
[spec](superpowers/specs/2026-09-24-azure-openai-keyless-design.md), §4.8–§4.14. Every Azure fact
here carries its source and the date it was read. A fact with no source given comes from that
spec, which is dated 2026-09-24 and was amended on 2026-09-27 and 2026-09-30.

```
GitHub Actions: environment "azure", whose only branch rule is main
  │  OIDC, through the federated credential: no app registration, no client secret
  ▼
┌─ rg-releaselens-bootstrap · long-lived · applied by the owner · lock CanNotDelete ──────────┐
│  strlstate<suffix>           shared keys off · tfstate-bootstrap (owner) · tfstate-app      │
│                              · tfstate-search (owner) · tfstate-gateway (owner)             │
│  id-releaselens-deploy       federated credential github-environment-azure (env azure)      │
│  id-releaselens-app          the identity the Container App runs as                         │
│  aoai-releaselens-<suffix>   kind AIServices · key authentication disabled                  │
│    ├ releaselens-chat        gpt-4.1-mini 2025-04-14 · GlobalStandard · capacity 300        │
│    ├ releaselens-embed-small text-embedding-3-small 1 · GlobalStandard · capacity 350       │
│    └ releaselens-embed-large text-embedding-3-large 1 · GlobalStandard · capacity 350       │
│  ag-releaselens-budget       emails for budget-releaselens-monthly (subscription scope)     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ rg-releaselens · created empty by bootstrap · contents deployed and destroyed by CI ───────┐
│  cae-releaselens             Container Apps environment, no Log Analytics workspace         │
│  ca-releaselens-api          runs as id-releaselens-app ──► Azure OpenAI, no key            │
│  psql-releaselens-<suffix>   password authentication · firewall rule allow-azure-services   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
Local runs: the API on the owner's machine, Postgres in Docker, Azure OpenAI through the
owner's own az login; the Anthropic and OpenAI keys only on the owner's machine: in the
environment, entered at a masked prompt, or in the git-ignored .env, never in a tracked file.
```

The bootstrap stack is applied once by the owner, locally, and never destroyed. The app stack
holds only the Container App and Postgres. The embedding deployments serve only the retrieval
benchmark, whose AI Search service is in a third stack, [`infra/search`](../infra/search/README.md):
the owner applies it in its own group, `rg-releaselens-search`, for one measurement session, and
destroys it at the end. A fourth stack, [`infra/gateway`](../infra/gateway/README.md), is the AI
gateway's, applied and destroyed the same way in `rg-releaselens-gateway` (see
[The AI gateway path](#the-ai-gateway-path)). The app stack reads nothing from bootstrap. The owner
copies four bootstrap outputs into the GitHub environment once, and the workflows pass them to
Terraform as `TF_VAR_*`:
- `APP_IDENTITY_ID`, a variable
- `APP_IDENTITY_CLIENT_ID`, a variable
- `AZURE_OPENAI_BASE_URL`, a secret since 2026-10-01 (see below)
- `AZURE_OPENAI_DEPLOYMENT`, a variable

The container's `AZURE_CLIENT_ID` is always the app identity's client ID.

### The workflows

[`deploy.yml`](../.github/workflows/deploy.yml), [`destroy.yml`](../.github/workflows/destroy.yml)
and [`gateway-check.yml`](../.github/workflows/gateway-check.yml) are the only workflows that name
the environment `azure`. The first two ran on 1 October 2026. `gateway-check.yml` has not run.
- **`deploy.yml`** is dispatched by hand. Its `preflight` job, with only `actions: read`, refuses
  to continue while `destroy.yml` is disabled. It also resolves the image tag `sha-<commit>` to
  its digest with an anonymous GHCR call. The `deploy` job applies the app stack with the image
  pinned to that digest, then runs the smoke test.
- **`destroy.yml`** is dispatched by hand, and runs nightly at 14:00 UTC. It destroys the app
  stack, then lists `rg-releaselens`, following ARM's paging, and fails, naming each resource, if
  anything is left. The listing only reads, so it also runs after a failed or cancelled destroy,
  and names what is still billing.
  - The destroy is retried only on azurerm issue
    [#33433](https://github.com/hashicorp/terraform-provider-azurerm/issues/33433), open when
    read on 2026-10-01. Deleting a Container App or its environment succeeds in Azure, but
    Terraform stops on a polling error. The next run drops the deleted resource from state, so
    the workflow makes up to three attempts: one for each of those two resources, then a clean
    pass. Any other error fails the step at once.
- **`gateway-check.yml`** is dispatched by hand, from `main` only (the job checks the ref as well
  as the environment's branch rule), and only during a gateway session. It signs in as the deploy
  identity, makes one call through the gateway and prints `status=<code>`, with `total_tokens=<n>`
  after it on a 200. Nothing else is printed, so the public log names no gateway. Its two
  secrets, `GATEWAY_BASE_URL` and `GATEWAY_SCOPE`, exist in the environment only for the session
  and are deleted after it. It installs a short, hash-locked list of requirements, because the job
  holds an OIDC token.
- **All three** have top-level `permissions: {}`, and give `id-token: write` and `contents: read`
  only to the job with the environment. They pin every action to a commit SHA, and share the
  concurrency group `releaselens-azure`, where runs queue and none is cancelled. In the deploy and
  destroy workflows, Terraform signs in through `ARM_USE_OIDC`, with no `azure/login` step.

**What the environment holds.** No credential: every value is an identifier.
- **Four secrets,** which GitHub masks in the public run logs: `AZURE_TENANT_ID`,
  `AZURE_SUBSCRIPTION_ID`, `TFSTATE_STORAGE_ACCOUNT` and `AZURE_OPENAI_BASE_URL`. GitHub masks
  a secret only where its whole value appears, so Terraform's apply and destroy output passes
  through a filter that hides the subscription ID even when Terraform truncates it.
- **Four variables,** which the logs show: `AZURE_CLIENT_ID`, `APP_IDENTITY_ID`,
  `APP_IDENTITY_CLIENT_ID` and `AZURE_OPENAI_DEPLOYMENT`. `APP_IDENTITY_ID` contains the
  subscription ID, so it shows with that segment masked. `SMOKE_OPEN_RUNNER_IP` is added by hand,
  only if the smoke test needs it.
- **Two more secrets, only during a gateway session:** `GATEWAY_BASE_URL` and `GATEWAY_SCOPE`,
  for `gateway-check.yml`. The owner sets them before the check and deletes them in the session's
  last step.

The tenant and subscription IDs became secrets on 2026-09-30, and the other two on 2026-10-01,
both by the owner's decision. The reason for the second pair, checked on 2026-09-30:
- a request with an invalid token to the state storage account's blob endpoint gets a 401 whose
  `WWW-Authenticate` header names the tenant ID
- the storage account shares its random suffix with the Azure OpenAI account, and both name
  patterns are in this repository, so the base URL would give the storage account's name away
- the Azure OpenAI endpoint's own 401 names no tenant, in its headers or its body

Other routes to the tenant ID have not been ruled out.

**The smoke test.** The deployed database has no schema and no tenant: the API runs no
migrations, and the Worker is not in the image. So the deploy job provisions both with the Worker
CLI (`migrate`, `create-tenant`, `issue-key`), run on the runner against the deployed Postgres.
- It registers the connection string, its password and the issued key with `::add-mask::`
  before anything else can print them, and never echoes `issue-key`'s output.
- `scripts/deploy_tools.py smoke` then calls `/query` once, retrying a cold start for up to five
  minutes.
- It passes only if the answer came from Azure OpenAI alone, is not degraded, and costs more
  than $0. The cost must also equal, to six decimal places, what the Worker's `price` command
  recomputes from the answer's own token counts at the app's configured rate.

Whether GitHub-hosted runners pass Postgres's `allow-azure-services` rule is unverified. If they
do not, setting the environment variable `SMOKE_OPEN_RUNNER_IP` to `true` makes the deploy job
add a firewall rule for its own public IP, read from `api.ipify.org`, before the smoke test. A
final step removes the rule again. It runs whenever the step that added the rule ran, even if
the smoke test failed.

### The AI gateway path

The app's default path is direct: the Container App, or a local API, calls the Azure OpenAI
account with a token for `https://ai.azure.com`. During a gateway session there is a second path,
through API Management. Gateway mode is configuration, not a new code path: `AzureOpenAi:BaseUrl`
is `https://<gateway-host>/openai/v1/` and `AzureOpenAi:TokenScope` is
`api://<gateway-app-client-id>/.default`. The stack is [`infra/gateway`](../infra/gateway/README.md),
and the design is the [gateway spec](superpowers/specs/2026-10-06-apim-ai-gateway-design.md).
**Nothing on this path has been applied or measured yet.**

```
Direct (the default)
  caller ──► token for https://ai.azure.com ──► aoai-releaselens-<suffix>  (australiaeast)
             the account's own RBAC is the check

Through the gateway (only during a session; the endpoint is public, protected by the token alone)
  caller ──► token for api://<gateway-app-client-id> ──► apim-releaselens-<suffix> (Basic v2)
               1 validate the token: audience, and the role Gateway.Invoke ──► 401 if not
               2 llm-token-limit: 10,000 a minute, 50,000 a day, per caller (the token's oid)
                                  ──► 429 with Retry-After, or 403, before any model is called
               3 llm-emit-token-metric ──► appi-releaselens (counts only; no bodies)
               4 swap the credential: the gateway identity's token, not the caller's
               5 backend pool aoai-pool
                   ├ aoai-primary    priority 1  ──► australiaeast account
                   └ aoai-secondary  priority 2  ──► Southeast Asia account
                 each backend has a circuit breaker on one 429; the model's 429 is re-sent once
                 and the pool picks the secondary; both tripped is a 503
```

What sits where:
- **Bootstrap (long-lived, nothing bills by the hour):** the Southeast Asia account and its two
  deployments, `releaselens-chat-failover-test` on the first account (capacity 1, so it
  throttles on purpose), `id-releaselens-gateway`, the Entra app `releaselens-ai-gateway` with its
  role `Gateway.Invoke` (assigned to the owner and the two existing identities; assignment is
  required), `log-releaselens` and `appi-releaselens`, and the state container `tfstate-gateway`.
  Every role assignment is in `infra/bootstrap/roles.tf`.
- **The gateway stack (per session):** `rg-releaselens-gateway` and API Management, with the API,
  its two revisions, the backends, the logger and the policies in `infra/gateway/policies/`.
  It creates no role assignment and no key.
- **The harness:** `eval/app/gateway/`, with the result rule and the region signal frozen before
  any measured run. `gateway-check.yml` is check B3 only: a second caller served while the first
  is over its daily budget.
- **Direct stays the default,** and a production setup would remove direct access so that all
  traffic goes through the gateway. This repository does not.

### Identities and roles

Five user-assigned managed identities, and two Entra app registrations, which no one signs in as:
- the **deploy identity**, `id-releaselens-deploy`, which the workflows sign in as
- the **app identity**, `id-releaselens-app`, which the Container App runs as
- the **gateway identity**, `id-releaselens-gateway`, which the AI gateway's API Management signs in as to call the models and to publish metrics
- the **ingest identity**, `id-releaselens-ingest`, which the ingest Function app runs as
- the **tool identity**, `id-releaselens-tool`, which the search tool's Function app runs as
- the **gateway app**, `releaselens-ai-gateway`, an app registration with one app role, `Gateway.Invoke`, and one delegated scope, `access_as_user`. It holds no secret and no certificate. Its service principal requires an assignment, so only an identity that holds the role can get a token for the gateway
- the **search tool app**, `releaselens-search-tool`, the tool app's audience: one app role, `Tool.Invoke`, for applications only, no delegated scope, no secret and no certificate. Its service principal requires an assignment, and only the gateway identity holds the role

All five identities live in the bootstrap group. Contributor on `rg-releaselens` includes writing federated
credentials, so an identity in that group would let CI add a trust for itself outside the
environment gate.

The bootstrap stack makes every Azure role assignment but two, and looks each role up by name:

| Identity | Role | Scope | Why |
|---|---|---|---|
| App identity | Cognitive Services OpenAI User | the Azure OpenAI account | inference. The role also grants the account's assistants, responses and file-read data plane |
| Owner | Cognitive Services OpenAI User | the Azure OpenAI account | local runs through `az login` |
| Owner | Storage Blob Data Contributor | `tfstate-bootstrap`, `tfstate-app`, `tfstate-search`, `tfstate-gateway` and `tfstate-functions` (five assignments) | the Owner role has no data actions. Without these, the owner could not migrate state or run the app or search stack locally |
| Owner | Storage Blob Data Contributor | `artefacts-in`, `deploy-ingest` and `deploy-tool` (three assignments) | the demo's uploads, and deploying both Function apps' packages with the owner's sign-in |
| Gateway identity | Cognitive Services OpenAI User | each of the two Azure OpenAI accounts (two assignments) | API Management calls the models as this identity, so no key exists |
| Gateway identity | Monitoring Metrics Publisher | Application Insights only | the gateway publishes its token metric with Entra ID, since local authentication is off |
| Ingest identity | Storage Blob Data Reader | `artefacts-in` only | read the artefact a queue message names |
| Ingest identity | Storage Queue Data Contributor | `ingest-events` and `ingest-events-poison` (two assignments) | the queue trigger receives and deletes; the runtime writes the poison message after the third failure |
| Ingest identity | Cognitive Services OpenAI User | the australiaeast Azure OpenAI account | embed chunks |
| Ingest identity | Storage Blob Data Owner, Storage Table Data Contributor | the ingestion account (two assignments) | identity-based host storage. No queue role on the account: the app's only queues are its own two |
| Tool identity | Cognitive Services OpenAI User | the australiaeast Azure OpenAI account | embed the query |
| Tool identity | Storage Blob Data Owner, Storage Queue Data Contributor, Storage Table Data Contributor | the ingestion account (three assignments) | identity-based host storage; the MCP extension uses queues |
| Event Grid topic's identity | Storage Queue Data Message Sender | `ingest-events` only | deliver blob events to the queue with no key |
| Event Grid topic's identity | Storage Blob Data Contributor | `deadletter-events` only | dead-letter events Event Grid cannot deliver |
| Deploy identity | Contributor | `rg-releaselens` only | create and destroy the app stack |
| Deploy identity | Managed Identity Operator | the app identity only | attach an identity from another resource group to the Container App |
| Deploy identity | Storage Blob Data Contributor | `tfstate-app` only | read and write the app stack's state, including its lock |

The owner is whoever applies bootstrap. The owner's assignments use the object ID of the
principal that is signed in.

The two apps' roles are assigned in the same stack, as four Entra app role assignments that are
not Azure roles, and are in addition to the table above:

| Principal | App role | Resource | Why |
|---|---|---|---|
| Owner | `Gateway.Invoke` | the gateway's service principal | local runs and the harness, through `az login` |
| App identity | `Gateway.Invoke` | the gateway's service principal | the deployed API calls the gateway |
| Deploy identity | `Gateway.Invoke` | the gateway's service principal | CI's check calls the gateway |
| Gateway identity | `Tool.Invoke` | the search tool's service principal | API Management calls the search tool as this identity, and nothing else may |

The other two are in the search stack, which only the owner applies, and which gives the owner
`Search Service Contributor` and `Search Index Data Contributor` on its search service, and on
nothing else. The rule that bootstrap holds every assignment keeps role-assignment rights away
from CI, and CI never runs that stack.

The app identity, not a system-assigned one, holds the role on the account. A system-assigned
identity would not exist until CI created the app, so CI would need the right to write role
assignments. Every deploy would then also wait for a new assignment to propagate. The
user-assigned identity is created and granted once.

The federated credential has one subject, for the environment `azure`, and no branch-type
credential exists. A job without `environment: azure` therefore cannot get an Azure token from
any branch. The environment's branch rule is the only thing that binds the token to `main`, so
two rules always hold:
- **No workflow triggered by `pull_request_target`, `workflow_run` or `issue_comment` names the
  environment.** `scripts/check_workflows.py` enforces this in CI. It allows the environment only
  in `deploy.yml`, `destroy.yml` and `gateway-check.yml`, and checks their triggers, permissions, concurrency and
  SHA-pinned actions. The temporary `oidc-probe.yml`, which the bootstrap runbook ran in R7 and
  R11, was then removed, and taken out of that allowlist.
- **The repository stays public.** On GitHub Free, a private repository's environment protection
  rules are ignored.

### What CI can do

CI signs in as the deploy identity. It holds no subscription-scope right and cannot write role
assignments. It cannot read the bootstrap state, and it has no role on the Azure OpenAI account,
so it cannot turn key authentication back on. It *can* do four things:
- **Run code as the app identity**, through Contributor on `rg-releaselens` and Managed Identity
  Operator on the app identity.
  - That identity can call the model and the account's data plane directly, outside `/query`, so
    no tenant token budget limits it.
  - Capacity caps the rate of that spend, not its total.
  - The budget alerts and stops nothing.
- **Read the Postgres password,** which is in the app stack's state.
- **Create any billable resource in `rg-releaselens`.** Deploy authority, the right to push to
  `main`, is therefore spending authority. Resources created outside Terraform survive
  `terraform destroy`, which is why the destroy workflow checks that the group is empty.
- **Rewrite the app stack's state.** The owner therefore runs that stack locally only through a
  reviewed, interactive plan, never `-auto-approve`. A planned deletion of anything other than the
  Container Apps resources or Postgres is treated as tampering, and recovered from an earlier blob
  version.

### Deployment type: Global Standard, and why it changed

The spec first chose a regional Standard deployment, which keeps inference in the account's
geography. A read-only check on 2026-09-27 (`az cognitiveservices usage list`) found this
subscription's quota:
- `OpenAI.Standard.gpt4.1-mini`: 0 in `australiaeast`, and 0 in eastus2, swedencentral,
  japaneast and westus3
- `OpenAI.GlobalStandard.gpt4.1-mini`: 5000

The owner chose Global Standard over asking Microsoft for regional quota, and the spec was
amended the same day.

**What that means for the data.** Microsoft's
[deployment types page](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types)
(dated 2026-08-06, read 2026-09-27) says two things:
- data stored at rest remains in the designated Azure geography, here Australia, because the
  account is in `australiaeast`
- for Global types, inferencing data "may be processed in any Azure region"

The prompts carry public GitHub data: the commits, issues, pull requests and releases of a public
repository.

**Rates**, from the Azure Retail Prices API for `australiaeast`, in USD per million tokens:

| Deployment type | Input | Cached input | Output | Read |
|---|---:|---:|---:|---|
| Global Standard (used) | 0.40 | 0.10 | 1.60 | 2026-09-24, and again 2026-09-27, unchanged |
| Standard (regional) | 0.44 | 0.11 | 1.76 | 2026-09-24 |

`ModelPricing` keeps the regional row, so switching back would be a configuration change.

**The model and the pin.** The deployment is `gpt-4.1-mini` version `2025-04-14`, pinned with
`NoAutoUpgrade`, because azurerm's default would upgrade it automatically.
- Keeping this model means no payload change, and it is the model OpenAI sells directly, which
  the planned evaluation compares against.
- `az cognitiveservices model list` (2026-09-27) lists it for `australiaeast` with a
  `GlobalStandard` SKU.
- Microsoft's retirement schedule (read 2026-09-24) lists it as Legacy, retiring 2027-04-14. The
  deployment then stops working, on purpose.
- Its Global Standard successors, such as the GPT-5 family, need payload changes. Review this in
  February 2027.

**Capacity 300, which is 300,000 tokens per minute, is revised from the dry run's measurement**
(2026-10-02, spec §4.8). It was 100, an estimate inferred from the most expensive query of the
12 August run.
- The dry run (2026-10-02, five queries per arm, once) measured Z's largest query (gq-022) at
  46,422 tokens, cached input included. Z's typical in-study use is about 49,000 tokens a
  minute: its five queries total 72,554 tokens over 89.5 s of A-Z-O rounds, about 48,600, a
  five-query sample.
- Azure also counts each request's `max_tokens` (2048 here) against the per-minute quota.
  gq-023 was not in the dry run and was not measured; it is assumed to be similar in size to
  gq-022, as the other causal query in the selection. If it is, the adjacent pair could
  together pass 100,000 tokens in one minute.
- 300 is within the subscription's Global Standard quota of 5,000.

Capacity caps how fast spend can grow, not how much. Sustained around the clock, 300,000 tokens a
minute is about 432 million tokens a day. At the Global Standard input rate that is about $173 a
day. That figure is arithmetic, not a measurement.

**Kind `AIServices`, not `OpenAI`.** Microsoft automatically upgrades eligible long-lived
`OpenAI`-kind accounts to `AIServices`. azurerm cannot set the opt-out, so a later bootstrap plan
would try to roll the kind back. The `AIServices` kind keeps the
`https://<subdomain>.openai.azure.com/openai/v1/` endpoint.

**Rejected:**
- **Data Zone Standard.** The APAC data zone spans several countries, so it does not keep data in
  Australia.
- **Provisioned.** It bills by the hour.

### What bills

| Resource | Stack | Bills |
|---|---|---|
| Postgres Flexible Server `B_Standard_B1ms` | app | by the hour while it exists |
| Container Apps (consumption, scales to zero) | app | per use |
| Azure OpenAI Global Standard deployment | bootstrap | per token. The Retail Prices API lists only per-token meters for it (read 2026-09-24 and 2026-09-27). The first invoice will confirm whether it charges anything while idle |
| Two embedding deployments, Global Standard | bootstrap | per token, and nothing idle (benchmark spec §6.1) |
| A second Azure OpenAI account in Southeast Asia, with `releaselens-chat` and `releaselens-chat-failover-test` deployments, and a tiny `releaselens-chat-failover-test` on the first account, all Global Standard | bootstrap | per token, and nothing idle (gateway spec §3.1) |
| API Management, Basic v2 | gateway | by the hour while it exists, about US$0.21 (US$0.20548, Retail Prices API, 2026-10-06), so about US$5 a day if forgotten; destroyed at the end of every gateway session (gateway spec §3.2, §7.5) |
| AI Search service, Basic | search | by the hour while it exists, US$3.19 a day; destroyed after each benchmark session (benchmark spec §6.4, §8) |
| State storage account | bootstrap | a few cents a month (an estimate) |
| The AI gateway's Entra app, Log Analytics workspace (30 days, 0.1 GB a day cap) and Application Insights | bootstrap | nothing idle; Log Analytics ingestion is billed per GB and capped (gateway spec §3.1) |
| Managed identities, resource groups, budget | bootstrap | nothing |

The budget, `budget-releaselens-monthly`, is in the long-lived stack, so it survives every
app-stack destroy. It is scoped to the subscription and defaults to 50 a month. That amount is in
the billing currency: for this project's subscription the budget reports its spend in AUD (the
Consumption budgets API's `currentSpend.unit`, read 2026-09-30, after runbook step R5). It alerts at 50, 80 and 100 percent of actual spend and 100
percent of forecast spend, and enforces nothing.
Microsoft's
[budget tutorial](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
(dated 2025-06-26, read 2026-09-27) says a budget stops no consumption. It also says budgets are
evaluated every 24 hours, against cost data that is typically 8 to 24 hours old.

The nightly destroy is best effort: GitHub disables scheduled workflows in a public repository
after 60 days without activity, and scheduled runs can be delayed or dropped.

### Terraform state

Every stack keeps its state in one storage account, one container each:
- the account has shared keys off, local users off, OAuth by default, public access to nested
  items off, and TLS 1.2
- the backend authenticates through Entra ID
- blob versioning is on, with 7 days of blob and container soft delete
- a lifecycle rule deletes a version 90 days after its content was written

That rule means the recovery windows differ between the stacks:
- The app state is rewritten on every deploy and destroy, so recent versions are there to restore
  after tampering.
- The bootstrap state changes rarely. If it is overwritten or deleted after it has sat unchanged
  for 90 days, the previous version can be deleted at once, and the practical recovery window is
  then only the 7 days of soft delete.

The account's keys exist, with shared-key access disabled. They sit in the bootstrap state, which
only the owner can read. That is why bootstrap's local state, before it is migrated, is
git-ignored.

### Logs

There is no Log Analytics workspace. A Container Apps environment authenticates to a workspace
with the workspace's shared key, which would put an Azure key into the design. Creating a
workspace also lists deleted workspaces at subscription scope, which CI has no right to do.

Logs stream instead:

```bash
az containerapp logs show --name ca-releaselens-api --resource-group rg-releaselens --follow
```

The app stack sets no OpenTelemetry endpoint, so in Azure, traces and metrics go nowhere. Azure
Monitor with keyless authentication is later work.

### Verified by the bootstrap apply

The checks after the full bootstrap apply on 2026-09-30 (runbook step R8) showed these:
- **That no Azure OpenAI key lands in Terraform state.** In the state, the account's primary and
  secondary access keys are both empty strings.
- **That the quota of 5000 covers capacity 100.** The deployment `releaselens-chat` provisioned
  successfully at Global Standard, capacity 100.
- **That key authentication is off.** The account reports `disableLocalAuth: true`.

### Verified by the first deploys

The deploys and destroys on 1 October 2026 (runbook steps R14 and R15) showed these:
- **`Cognitive Services OpenAI User` grants inference on an `AIServices` account.** The app
  identity, which holds only that role, got the smoke test's question answered.
- **The account accepts the app's default token scope,** `https://ai.azure.com/.default`. The
  app stack sets no other scope, so no other was tried.
- **GitHub-hosted runners pass the `allow-azure-services` firewall rule.** Both smoke tests
  reached Postgres without `SMOKE_OPEN_RUNNER_IP`.
- **CI's rights are enough.** With no subscription-scope role, `init`, apply and destroy
  succeeded:
  - Contributor on one resource group created Postgres and the Container Apps resources.
  - Managed Identity Operator attached the app identity.
- **Destroying Container Apps resources trips azurerm issue
  [#33433](https://github.com/hashicorp/terraform-provider-azurerm/issues/33433).** Azure deletes them, but the provider reports a failure.
  The destroy workflow retries on that error only.

### Not yet verified

- **Whether MSAL's own managed-identity retry is capped.** `AzureCredentialFactory` caps
  Azure.Core's retry at one fixed 200 ms retry. Whether MSAL adds retries of its own beneath that
  is unverified.
- **Whether the deployment charges anything while idle.** The first invoice will show.
