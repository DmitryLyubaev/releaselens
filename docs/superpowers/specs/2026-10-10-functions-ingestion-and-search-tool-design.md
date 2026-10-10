# Azure Functions: ingestion and a search tool — design

**Status: specification, approved by the owner on 2026-10-10.** Nothing is built, applied or
measured. Every figure below is either read from a named source on a stated date, or labelled as
an estimate. Claims that could not be checked are labelled *unverified* and listed in §12. Changes
made after approval are in §13, dated, each on the owner's decision.

- Project 5 of the portfolio plan (phase 2), in ReleaseLens, `main` at `69e0c0a`
- Builds on project 1 (the keyless Azure OpenAI account and the bootstrap stack), project 2 (the
  AI Search index, its saved vectors and its per-session stack) and project 4 (the API Management
  gateway, its Entra app and `Gateway.Invoke`, and its monitoring)
- Sources: the research notes of 2026-10-10 in the private plan repository, which cite each
  Microsoft Learn page, package and issue they rely on; this document restates what it needs
- Agreed with the owner in brainstorming on 2026-10-10, section by section

---

## 1. Purpose

Show, measured, what serverless functions add to ReleaseLens, in two pieces:

- **Ingestion.** A new or changed GitHub artefact, dropped into Storage as a file, becomes
  searchable on its own: chunked, embedded and written to the AI Search index, with no key
  anywhere, no duplicate chunks when it is sent again, and broken input set aside in a poison
  queue instead of retried forever or lost.
- **A search tool published on the platform's gateway.** A read-only MCP tool, `search_corpus`,
  runs on Functions and is published behind project 4's API Management gateway from this
  repository, the way a product team publishes onto a platform team's gateway: Entra only, a
  per-caller limit, usage per caller, and no way round the gateway.

Every check has its pass rule written in §7 before anything live runs. A failed check is a
publishable result.

**Not in this project:** the Durable Functions benchmark runner from the phase 2 plan. It is a
later, optional add-on (owner's decision, 2026-10-10).

---

## 2. What exists today

Read from the repository on 2026-10-10.

- **The corpus.** GitHub artefacts (commits, issues, pull requests and releases) in Postgres, as
  evidence records. `EvidenceChunker` (`src/ReleaseLens.Core/Chunking/`) turns each record into
  chunks with a self-describing header. The restored corpus has 41,825 chunks.
- **The index.** Project 2's `releaselens-chunks` (`eval/app/retrieval/search_index.py`): key
  `chunk_id` (project 2's numeric chunk IDs), `artefact`, `content`, a 1,536-dimension `vector`
  from `text-embedding-3-small`, and a semantic configuration. `artefact` is **not** filterable.
  The saved vectors are in `eval/retrieval-data/` (git-ignored), so the corpus is never embedded
  twice. Project 2 measured hybrid search with the semantic ranker best, and fully repeatable.
- **The search stack.** `infra/search`: Basic, one replica, keys off, created and destroyed per
  session by the owner, with the owner's two search roles.
- **The bootstrap.** The keyless Azure OpenAI account with `releaselens-embed-small`; the
  identities; every role assignment in `roles.tf`; the Entra app `releaselens-ai-gateway` with
  `Gateway.Invoke`; `appi-releaselens` with Entra ingestion; the state storage account.
- **The gateway stack.** `infra/gateway`: API Management Basic v2, per session, with its
  user-assigned identity and named values; its state is readable by another stack.
- **The app's Azure OpenAI code** is chat only: `EntraTokenHandler` and `EntraTokenCache`
  (`src/ReleaseLens.Llm/Providers/Azure/`) attach Entra tokens. No C# embeddings client exists.

---

## 3. Architecture

Two Function apps, each with its own identity (approach A, owner's choice):

| | Ingest app | Tool app |
|---|---|---|
| Trigger | a Storage queue | the Functions MCP extension (HTTP) |
| Does | chunk, embed, upsert, delete stale | embed a query, search |
| Identity | `id-releaselens-ingest` | `id-releaselens-tool` |
| Index access | Search Index Data **Contributor** | Search Index Data **Reader** |
| Reachable from | nothing (no HTTP function) | the gateway's identity only |

### 3.1 Bootstrap additions (long-lived; nothing bills by the hour)

- **A storage account for ingestion,** shared keys off, holding: the blob container
  `artefacts-in`; the queues `ingest-events` and `ingest-events-poison`; and, for each app, its
  host storage and a deployment-package container.
- **An Event Grid system topic** on that account (system-assigned identity), with one
  subscription: event type `Microsoft.Storage.BlobCreated` only, subject beginning with the
  `artefacts-in` container and ending in `.json`, delivered to the `ingest-events` queue **with
  the topic's identity** (no key), and a dead-letter container for events Event Grid itself
  cannot deliver. An upload while no session is running waits in the queue.
- **Two user-assigned identities,** `id-releaselens-ingest` and `id-releaselens-tool`, here so
  their roles exist and have propagated before a session.
- **An Entra app registration `releaselens-search-tool`:** an identifier URI (the tool app's
  audience), no secret or certificate, `app_role_assignment_required = true`, and one app role
  assigned to project 4's gateway identity only.
- **Roles,** all in `roles.tf`, none on the search service (which exists only in a session):
  - ingest: read blobs in `artefacts-in`; process and delete messages in both queues; Azure
    OpenAI User on the account; its host-storage roles
  - tool: Azure OpenAI User; its host-storage roles
  - the Event Grid topic's identity: Storage Queue Data Message Sender on `ingest-events`, and
    blob write on the dead-letter container
  - the owner: write to `artefacts-in` (the demo uploads) and to both deployment containers

### 3.2 Search stack additions (per session)

`infra/search` gains Search Index Data Contributor for the ingest identity and Search Index Data
Reader for the tool identity, under the stack's existing exception (the owner applies it, and it
already grants the owner's search roles). The index schema gains `"filterable": true` on
`artefact` (§4.4); search results do not change, and the index is rebuilt every session.

### 3.3 The functions stack (`infra/functions`, per session, owner-applied)

Reads the bootstrap's, the search stack's and the gateway's state.

- **Two Flex Consumption apps,** .NET 10 isolated worker, each with its user-assigned identity,
  created with **azapi** (`Microsoft.Web/sites`), because `azurerm_function_app_flex_consumption`
  injects an `AzureWebJobsStorage` connection string and breaks accounts with shared keys off
  (azurerm issues #29693, #33211, #30732; fix PR #29099 open, research notes §2g). Host storage is
  identity-based (`AzureWebJobsStorage__accountName`, `__credential`, `__clientId`). No
  always-ready instances.
- **App Service authentication (Entra) on every route of both apps:** require authentication,
  return 401, allowed audience the `releaselens-search-tool` identifier URI, allowed applications
  the gateway identity. The ingest app has no HTTP function; the setting keeps the runtime's own
  key-authenticated endpoints unreachable too.
- **The tool's MCP API on the gateway:** `azapi_resource` `Microsoft.ApiManagement/service/apis`
  at `2025-09-01-preview`, type `mcp`, pass-through to the tool app's `/runtime/webhooks/mcp`
  over Streamable HTTP, with the policy of §5.2.
- **App settings:** the account's v1 base URL, the embedding deployment, the search endpoint and
  index name, the queue name. No secret.

### 3.4 Code

- `src/ReleaseLens.Functions.Ingest` — the queue-triggered function.
- `src/ReleaseLens.Functions.Tool` — the MCP tool.
- `src/ReleaseLens.Functions.Common` — what both use: an Azure OpenAI embeddings client (over
  `EntraTokenHandler`), an AI Search client (upsert, delete, lookup, hybrid-plus-semantic
  search), and the chunk-key rule. Both apps reference `ReleaseLens.Core` for `EvidenceChunker`.
- `ReleaseLens.Worker` gains `export-artefacts <dir> <key>...`: real artefacts from the restored
  corpus as evidence-record JSON files.
- `eval/app/functions/` — the harness: uploads, the checks of §7, the Python MCP client, the
  report.
- `docs/runbook-functions.md` — the live session.

---

## 4. Ingestion

### 4.1 The input

Each blob in `artefacts-in` is one artefact as JSON: an evidence record (commit, issue, pull
request or release) in the shape `ReleaseLens.Core` defines, with an `entityType` discriminator.
Names end in `.json`; the harness names its uploads uniquely per session.

### 4.2 The flow

1. Event Grid delivers the `BlobCreated` event to `ingest-events`.
2. The ingest function (`QueueTrigger("ingest-events")`) parses the event. Whether Event Grid
   writes plain or base64-encoded JSON into the queue is *unverified* (§12): the parser accepts
   both, and a test pins each.
3. It checks the event names a blob in `artefacts-in`, downloads it with its identity, and
   parses and validates the evidence record.
4. It chunks the record with `EvidenceChunker`, with the app's own `ChunkOptions`.
5. It embeds all the chunks in one call to `releaselens-embed-small` (1,536 dimensions), keyless.
6. It upserts the chunks (`mergeOrUpload`) with deterministic keys (§4.3), then deletes stale
   keys (§4.4).

### 4.3 Deterministic keys

A chunk's key is `<artefact, base64url without padding>-<chunk index>`. AI Search keys allow only
letters, digits, `_`, `-` and `=`, and artefact IDs hold `:` and `.`. Sending the same artefact
again writes the same keys, and Event Grid's at-least-once delivery is harmless.

### 4.4 Stale chunks

After the upsert, the function looks up every chunk whose `artefact` equals this artefact and
deletes each key not in the new set. That covers an artefact that comes back shorter, and one that
is already in the bulk-loaded corpus under project 2's numeric keys. It needs `artefact`
filterable (§3.2).

### 4.5 Failures

`host.json`: `maxDequeueCount` 3, `visibilityTimeout` 30 s.

- Broken input (bad JSON, unknown `entityType`, missing fields, a blob outside `artefacts-in`)
  fails every try and lands in `ingest-events-poison` after 3.
- Transient errors (an Azure OpenAI 429, AI Search busy) get the same 3 tries, spaced by the
  visibility timeout.
- Nothing waits indefinitely inside the function, nothing is retried forever, and nothing is
  dropped without a trace.

### 4.6 Telemetry

To `appi-releaselens`, with Entra ingestion: counts and durations only. No artefact content, no
query text.

**Out of scope:** deleted blobs; bulk ingestion through the function (the corpus is bulk-loaded
from saved vectors).

---

## 5. The search tool

### 5.1 The tool

The Functions MCP extension (stable NuGet 1.6.0, research notes §3a), one tool:

- `search_corpus(query: string, top: int = 5)`, `top` at most 10
- embeds the query with `releaselens-embed-small`, then runs hybrid search with the semantic
  ranker (project 2's best arm), within the ranker's free monthly allowance
- returns, for each hit, the artefact ID, its type, a chunk excerpt of at most 500 characters and
  the score
- read-only: its identity has Search Index Data Reader and Azure OpenAI User, nothing else
- `host.json`: `extensions.mcp.system.webhookAuthorizationLevel = "Anonymous"`, so the
  extension's `mcp_extension` key is off; App Service authentication (§3.3) guards it instead

### 5.2 The call path

1. **The caller** (the Python test client or Claude Code) sends an Entra token for the gateway's
   audience with `Gateway.Invoke`, as in project 4.
2. **The gateway's MCP API policy, inbound in order:**
   1. `validate-azure-ad-token`: tenant, the gateway app's audiences, `roles` contains
      `Gateway.Invoke`; the token to the variable `jwt`
   2. `rate-limit-by-key`: 20 calls a minute, counter key the token's `oid`; over it, the
      gateway's own 429 with `Retry-After`
   3. `emit-metric`: `Tool Calls`, with a `Caller` dimension (the `oid`)
   4. `authentication-managed-identity`: the gateway identity's token for the
      `releaselens-search-tool` audience, replacing the caller's
   
   No policy reads the request or response body, and payload logging is 0, so Streamable HTTP
   passes through. `llm-token-limit` does not apply to MCP traffic (research notes §4).
3. **The tool app** accepts only that token (§3.3).

### 5.3 The Claude Code demo

`claude mcp add --transport http` with a `headersHelper` script that calls `az` for a fresh
gateway token each time, so no token is written into Claude Code's configuration. The owner asks a
question about the corpus and Claude calls `search_corpus`. Descriptive, not a check.

---

## 6. Failure handling

| What happens | What is seen |
|---|---|
| Broken artefact file | 3 tries, then `ingest-events-poison` |
| Azure OpenAI 429 or AI Search error during ingestion | that try fails; the queue retries; 3 in all |
| No session running | events wait in `ingest-events` (7-day message lifetime) |
| Search or embedding error in the tool | an MCP tool error with a fixed message, no backend detail |
| No token, wrong audience, no `Gateway.Invoke` | 401 from the gateway |
| Over 20 calls a minute | the gateway's 429 with `Retry-After` |
| Direct call to the tool app | 401 from App Service authentication |

---

## 7. The measured checks

Run by `eval/app/functions/` as the owner through `az`, after the bulk load. Pass rules are fixed
here, before any live run.

**Ingestion**
- **I1, a new artefact becomes searchable.** Five artefacts are held back from the bulk load,
  then uploaded. Passes if each one's chunks are in the index, with the expected chunk count,
  within 120 seconds of its upload. Upload-to-searchable times are reported (median, maximum).
- **I2, a changed artefact replaces itself.** One artefact in the bulk-loaded corpus is uploaded
  shortened, so it yields fewer chunks. Passes if the index then holds exactly its new chunks,
  with the new text, and none of its old keys (the numeric ones included).
- **I3, duplicates are harmless.** The same unchanged file is uploaded twice. Passes if that
  artefact's chunk count and keys are identical after both.
- **I4, broken input goes to the poison queue.** One malformed file is uploaded. Passes if a
  message for it reaches `ingest-events-poison` within 10 minutes, and the index gains nothing
  from it.

**The tool**
- **T1, the tool runs the same search as project 2.** `search_corpus` is in the tool list, and
  for ten fixed questions from project 2's audited set, sent through the gateway, the tool's top
  5 artefacts match a direct hybrid-plus-semantic query of the same index, in order.
- **T2, the gateway refuses strangers.** A call with no token and a call with a wrong-audience
  token both get 401 from the gateway.
- **T3, no way round the gateway.** A call straight to the tool app is refused by App Service
  authentication, with no token and with the owner's gateway token (the owner is not assigned to
  `releaselens-search-tool`, so cannot get a token for the tool app's audience).
- **T4, the per-caller limit holds.** A burst of 30 calls at once gets at least one gateway 429
  with `Retry-After`, and the `Tool Calls` metric shows the calls under the owner's label.

**In the same session:** project 4's follow-ups are verified (B1's burst re-measured as a dated
amendment to project 4's report; the custom-metrics property after Terraform; revision 2's token
metric), and the Claude Code demo is recorded as a short identifier-free excerpt.

**Cost:** about US$1 a session, estimated: the gateway about US$0.21 an hour and AI Search about
US$0.10 an hour for about 2 hours; embeddings cents; Flex Consumption effectively nothing at this
volume (US$0.000037 per GB-second after a 100,000 GB-second free grant, research notes §2b).

---

## 8. Security and privacy

- **Keyless:** storage shared keys off; Event Grid delivers with its identity; both apps use
  identity-based host storage; the MCP extension's key off; no secret on either app registration;
  every model and search call uses a managed identity.
- **Least access:** the tool cannot write the index; the ingest app has no HTTP function; the tool
  app answers only the gateway's identity.
- **No content logged:** counts and durations only.
- **No identifiers in public files:** the harness reuses project 4's writer, which refuses GUIDs
  and Azure hostnames; the doc scan covers the new runbook and report.
- **The owner applies every stack.** CI gets no new rights.

---

## 9. Teardown and cost

- **Per session:** the functions, search and gateway stacks are destroyed, then checked: their
  resource groups gone, API Management purged, the search service deleted.
- **What stays** costs pennies a month: the storage account, the Event Grid topic (within its
  free operations), the identities, the app registrations.
- **Test uploads stay:** uniquely named per session, and tiny.

---

## 10. Testing in CI, with no Azure calls

- **C# unit tests:** the event parser (plain and base64); the key rule; the stale-key set; the
  ingest function end to end against stubbed HTTP (chunk, embed, upsert, delete); the poison path
  (exceptions propagate); the embeddings and search clients; the MCP tool against a stubbed
  search, including `top` capped at 10 and the 500-character excerpt; `export-artefacts`.
- **Mocked `terraform test`:** the bootstrap additions (keys off; the subscription's filter and
  identity delivery; identities; the registration; the roles); the search stack's two roles; the
  functions stack (the apps' runtime and identity storage; authentication required; the MCP
  setting; the MCP API and its policy on the gateway).
- **Static checks:** the MCP policy's order and its `oid` key; no body read; no keys; the doc scan.
- **Harness tests** for I1–I4 and T1–T4 on fixtures, and the MCP client's bearer header.
- **CI** builds and tests the new projects in the existing jobs, and adds `infra/functions` to the
  Terraform job.

---

## 11. Delivery

**Plan 1, offline.** Everything in §3 to §10 that needs no Azure call, as one PR.

**Plan 2, the live session,** by `docs/runbook-functions.md`, each step on the owner's yes:
1. Apply the bootstrap (it creates the `releaselens-search-tool` registration and adds project
   4's azapi update).
2. Apply the gateway, and verify project 4's follow-ups.
3. Apply search, and bulk-load the index from the saved vectors, holding back I1's five
   artefacts.
4. Apply `infra/functions`, and deploy both packages with the owner's sign-in.
5. Smoke test.
6. Run I1–I4 and T1–T4.
7. The Claude Code demo.
8. Publish the report; destroy and check.

---

## 12. Unverified, to settle in plan 2

1. Flex Consumption is offered in australiaeast, and a .NET 10 isolated app starts there.
2. An azapi-created Flex app with identity-based host storage starts on an account with shared
   keys off; code deploys with the owner's Entra sign-in and basic authentication off.
3. Event Grid delivers to the queue with the topic's identity; the message encoding (plain or
   base64).
4. The poison write needs no role beyond the ingest identity's queue roles.
5. Role propagation for the two identities on a newly created search service is within a
   session.
6. The MCP extension with the anonymous webhook level plus App Service authentication accepts the
   gateway identity's token and refuses anything else.
7. The gateway's MCP pass-through reaches `/runtime/webhooks/mcp`; `initialize`, `tools/list`
   and `tools/call` work through Basic v2 with the `mcp` 2.3 SDK.
8. `validate-azure-ad-token` and `rate-limit-by-key` on `oid` work on the MCP API as on project
   4's API (token-bucket bursts considered).
9. Claude Code connects with `headersHelper` and does not attempt OAuth discovery on a 401.
10. The first `search_corpus` call after idle starts within Claude Code's MCP timeout without an
    always-ready instance.
11. Destroying and recreating the functions stack leaves the Event Grid subscription working and
    queued messages draining.
12. `emit-metric` records `Tool Calls` with the `Caller` dimension (project 4 found that custom
    metrics carry no namespace dimension).

---

## 13. Changes after approval

- **2026-10-10, §5.3: the Claude Code command.** The runbook adds the tool with
  `claude mcp add-json --scope local`, not `claude mcp add --transport http`. `claude mcp add`
  has no flag for a `headersHelper` (Claude Code's MCP documentation, read 2026-10-10), which only
  a JSON configuration sets. The local scope keeps the gateway's URL in the owner's own
  configuration and out of the repository, where a project-scope `.mcp.json` would commit it.
- **2026-10-10, §3.1: where the deployment packages live.** Each app's deployment container is in
  its own host storage account (`strlingesthost<suffix>` and `strltoolhost<suffix>`), not in the
  ingestion account. The host needs Storage Blob Data Owner on its whole account, so in a shared
  account each app could write the other's package, and `artefacts-in`.
- **2026-10-10, §11: holding back I1's five artefacts.** `build-index` has no option to hold
  artefacts back, so the runbook bulk-loads from a filtered copy of the saved index data, in
  `eval/reports/functions-bulk` (git-ignored): the chunks and their saved vectors without the five.
  Nothing is embedded again.
- **2026-10-10, §2 and §3.2: the index schema is not changed.** Project 2's schema
  (`eval/app/retrieval/search_index.py`) already declares `artefact` filterable, so §2's "not
  filterable" and §3.2's added `"filterable": true` were both wrong, and no schema change was made.
  The filter by artefact (§4.4) and search results are as designed.
