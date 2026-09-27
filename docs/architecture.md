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

Both Terraform stacks are built and tested with mocked plans. **Nothing is deployed yet**, and
nothing below describes a deployment. The owner's runbook is
[infra/bootstrap/README.md](../infra/bootstrap/README.md). The decisions and the reasons for them
are in the [spec](superpowers/specs/2026-09-24-azure-openai-keyless-design.md), §4.8–§4.14. Every
Azure fact here carries its source and the date it was read. A fact with no source given comes
from that spec, which is dated 2026-09-24 and was amended on 2026-09-27.

```
GitHub Actions: environment "azure", whose only branch rule is main
  │  OIDC, through the federated credential: no app registration, no client secret
  ▼
┌─ rg-releaselens-bootstrap · long-lived · applied by the owner · lock CanNotDelete ──────────┐
│  strlstate<suffix>           shared keys off · tfstate-bootstrap (owner) · tfstate-app      │
│  id-releaselens-deploy       federated credential github-environment-azure (env azure)      │
│  id-releaselens-app          the identity the Container App runs as                         │
│  aoai-releaselens-<suffix>   kind AIServices · key authentication disabled                  │
│    └ releaselens-chat        gpt-4.1-mini 2025-04-14 · GlobalStandard · capacity 100        │
│  ag-releaselens-budget       emails for budget-releaselens-monthly (subscription scope)     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ rg-releaselens · created empty by bootstrap · contents deployed and destroyed by CI ───────┐
│  cae-releaselens             Container Apps environment, no Log Analytics workspace         │
│  ca-releaselens-api          runs as id-releaselens-app ──► Azure OpenAI, no key            │
│  psql-releaselens-<suffix>   password authentication · firewall rule allow-azure-services   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
Local runs: the API on the owner's machine, Postgres in Docker, Azure OpenAI through the
owner's own az login; the Anthropic and OpenAI keys only in the git-ignored .env.
```

The bootstrap stack is applied once by the owner, locally, and never destroyed. The app stack
holds only the Container App and Postgres. The app stack reads nothing from bootstrap. The owner
copies four bootstrap outputs into the GitHub environment's variables once, and the workflows
are to pass them to Terraform as `TF_VAR_*`:
- `APP_IDENTITY_ID`
- `APP_IDENTITY_CLIENT_ID`
- `AZURE_OPENAI_BASE_URL`
- `AZURE_OPENAI_DEPLOYMENT`

The container's `AZURE_CLIENT_ID` is always the app identity's client ID. The deploy and destroy
workflows are the next change, and are not in the repository yet.

### Identities and roles

Two user-assigned managed identities, and no Entra app registration:
- the **deploy identity**, `id-releaselens-deploy`, which the workflows sign in as
- the **app identity**, `id-releaselens-app`, which the Container App runs as

Both live in the bootstrap group. Contributor on `rg-releaselens` includes writing federated
credentials, so an identity in that group would let CI add a trust for itself outside the
environment gate.

The bootstrap stack makes every role assignment, and looks each role up by name:

| Identity | Role | Scope | Why |
|---|---|---|---|
| App identity | Cognitive Services OpenAI User | the Azure OpenAI account | inference. The role also grants the account's assistants, responses and file-read data plane |
| Owner | Cognitive Services OpenAI User | the Azure OpenAI account | local runs through `az login` |
| Owner | Storage Blob Data Contributor | `tfstate-bootstrap` and `tfstate-app` (two assignments) | the Owner role has no data actions. Without these, the owner could not migrate state or run the app stack locally |
| Deploy identity | Contributor | `rg-releaselens` only | create and destroy the app stack |
| Deploy identity | Managed Identity Operator | the app identity only | attach an identity from another resource group to the Container App |
| Deploy identity | Storage Blob Data Contributor | `tfstate-app` only | read and write the app stack's state, including its lock |

The owner is whoever applies bootstrap. The owner's assignments use the object ID of the
principal that is signed in.

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
  in `deploy.yml`, `destroy.yml` and the temporary `oidc-probe.yml`, and checks their triggers,
  permissions, concurrency and SHA-pinned actions.
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
  `terraform destroy`, which is why the destroy workflow is designed to check that the group is
  empty.
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

**Capacity 100, which is 100,000 tokens per minute, is an estimate, not a measurement.**
- It is inferred from the most expensive query of the 12 August run.
- Azure also counts each request's `max_tokens` (2048 here) against the per-minute quota.
- The first dry run's measured token counts will revise it.

Capacity caps how fast spend can grow, not how much. Sustained around the clock, 100,000 tokens a
minute is about 144 million tokens a day. At the Global Standard input rate that is about $58 a
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
| State storage account | bootstrap | a few cents a month (an estimate) |
| Managed identities, resource groups, budget | bootstrap | nothing |

The budget, `budget-releaselens-monthly`, is in the long-lived stack, so it survives every
app-stack destroy. It defaults to 50 a month, in the billing account's currency. It alerts at 50,
80 and 100 percent of actual spend and 100 percent of forecast spend, and enforces nothing.
Microsoft's
[budget tutorial](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
(dated 2025-06-26, read 2026-09-27) says a budget stops no consumption. It also says budgets are
evaluated every 24 hours, against cost data that is typically 8 to 24 hours old.

The nightly destroy is best effort: GitHub disables scheduled workflows in a public repository
after 60 days without activity, and scheduled runs can be delayed or dropped.

### Terraform state

Both stacks keep their state in one storage account, one container each:
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

### Not yet verified

Each of these waits for a real run:
- **Whether `Cognitive Services OpenAI User` grants inference on an `AIServices` account.** The
  first real call will show.
- **Which token scope the account accepts.** The app defaults to `https://ai.azure.com/.default`,
  and the scope is configurable. The first real token will show.
- **Whether GitHub-hosted runners pass the `allow-azure-services` firewall rule.** The first smoke
  test will show. If they do not, `smoke_runner_ip` opens the server to the runner alone.
- **Whether MSAL's own managed-identity retry is capped.** `AzureCredentialFactory` caps
  Azure.Core's retry at one fixed 200 ms retry. Whether MSAL adds retries of its own beneath that
  is unverified.
- **Whether CI's rights are enough.** These are to be established by the first CI apply:
  - that Contributor on one resource group is enough to create Postgres and the Container Apps
    resources
  - that azurerm makes no subscription-scope call at `init` for CI's identity
  - that Managed Identity Operator is enough to attach the app identity
- **That no Azure OpenAI key lands in Terraform state.** Runbook step R8 checks it.
- **That the quota of 5000 covers capacity 100.** The first bootstrap apply will show.
- **Whether the deployment charges anything while idle.** The first invoice will show.
