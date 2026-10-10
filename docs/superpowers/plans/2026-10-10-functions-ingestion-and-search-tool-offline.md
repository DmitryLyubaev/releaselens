# Azure Functions ingestion and search tool, plan 1 (offline), implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything project 5 needs that makes no Azure call: the two Function apps and their
shared code, the Worker's artefact export, the bootstrap and search-stack additions, the per-session
`infra/functions` stack with the tool's MCP API on project 4's gateway, the measurement harness, the
runbook for the live session (plan 2), and the tests.

**Architecture:**
- **Code:** `ReleaseLens.Functions.Common` (chunk keys, a bearer-token handler, an Azure OpenAI
  embeddings client, an AI Search REST client, the artefact JSON format); `ReleaseLens.Functions.Ingest`
  (a queue-triggered function: parse the Event Grid event, read the blob, chunk with
  `EvidenceChunker`, embed, upsert, delete stale); `ReleaseLens.Functions.Tool` (the MCP tool
  `search_corpus`). One test project covers all three.
- **Infrastructure:** the bootstrap gains the ingestion storage account, the Event Grid topic and
  subscription, two identities, the `releaselens-search-tool` app registration and their roles;
  `infra/search` gains two search roles; a new `infra/functions` stack holds the two Flex Consumption
  apps (azapi) and the tool's MCP API and policy on the gateway.
- **Harness:** `eval/app/functions/` runs I1–I4 and T1–T4 as the owner and writes the report.

**Tech Stack:**
- **C#, .NET 10:** the isolated worker (`Microsoft.Azure.Functions.Worker`), the queue and MCP
  extensions, `Azure.Storage.Blobs`, `Azure.Identity`; xUnit v3 tests with stubbed HTTP
- **Terraform 1.15.8:** azurerm `~> 5.6`, azapi `~> 2.13`, azuread `~> 3.0`; mocked `terraform test`; WSL only
- **Python 3.14:** `eval/` (httpx, azure-identity, pytest), `tests/infra` (pytest)

**Spec:** `docs/superpowers/specs/2026-10-10-functions-ingestion-and-search-tool-design.md`, approved
2026-10-10. Research notes (private, readable locally):
`E:\Development\portfolio-plan\handovers\project-05-functions-research-2026-10-10.md`.

## Global Constraints

- **The design is fixed (spec §3–§10).** Implement it as written. A conflict goes to the controller
  as a question, never a silent change.
- **Names (spec §3):** identities `id-releaselens-ingest`, `id-releaselens-tool`; app registration
  `releaselens-search-tool` (no secret, assignment required, one app role assigned to the gateway
  identity only); container `artefacts-in`; queues `ingest-events`, `ingest-events-poison`;
  resource group `rg-releaselens-functions`; index `releaselens-chunks`; embedding deployment
  `releaselens-embed-small` (1,536 dimensions); tool `search_corpus`.
- **Ingestion (spec §4):** event type `Microsoft.Storage.BlobCreated` only, subject in `artefacts-in`
  ending `.json`; delivery to the queue with the Event Grid topic's identity; `host.json`
  `maxDequeueCount` 3, `visibilityTimeout` 30 s; chunk key `<artefact, base64url without padding>-<chunk
  index>`; `mergeOrUpload`, then delete every key of that artefact not in the new set.
- **Tool (spec §5):** `search_corpus(query, top = 5)`, `top` at most 10; hybrid search with the
  semantic ranker; each hit's artefact ID, type, an excerpt of at most 500 characters, and score;
  `webhookAuthorizationLevel = "Anonymous"`.
- **Gateway policy (spec §5.2), inbound in order:** `validate-azure-ad-token` (tenant, the gateway
  app's audiences, `roles` contains `Gateway.Invoke`, token to `jwt`); `rate-limit-by-key` 20 calls a
  minute keyed on `oid`; `emit-metric` `Tool Calls` with a `Caller` dimension; then
  `authentication-managed-identity` for the `releaselens-search-tool` audience. No policy reads a
  request or response body; payload logging 0.
- **Keyless:** shared keys off on the new storage account; identity-based host storage; no secret
  on any app registration; no key in any setting, output or log.
- **Identifiers:** no tenant, client or object ID, hostname, account name or email in any public
  file. Tests use the repository's placeholder GUIDs (`00000000-…`, `11111111-…`) and `example.com`.
- **Role assignments:** in `infra/bootstrap/roles.tf`, except the two search roles, which go in
  `infra/search` under its existing exception.
- **No network in tests.** Stubbed HTTP handlers (C#), `httpx.MockTransport` (Python), mocked
  providers (Terraform). No test sleeps for real.
- **Terraform runs only in WSL**, on a clean copy without `terraform.tfvars`, with
  `TF_DATA_DIR=$HOME/tfdata/check-<stack>`. Never `plan` or `apply` against Azure in a task.
- **Commits:** repo-local identity, never `--author`, never `git config --global`; every message
  ends `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; branch
  `feat/functions-ingestion-tool`; nothing is pushed without the owner's yes.

## Rulings made while planning

- **`artefact` is already filterable** in project 2's index schema (`eval/app/retrieval/search_index.py`):
  spec §2 and §3.2 are wrong on this, so no schema change is made. Cost if wrong: none.
- **`Functions.Common` has its own bearer-token handler** rather than referencing `ReleaseLens.Llm`
  for `EntraTokenHandler`: `ReleaseLens.Llm` pulls in the ONNX runtime, `ReleaseLens.Embedding` and
  `ReleaseLens.Storage` (Npgsql), which would bloat both Function packages and their cold start.
  The handler is small (spec §3.4 named `EntraTokenHandler`). Cost if wrong: about 40 duplicated lines.
- **The artefact JSON writes `authorEmail` as null.** Chunking never uses it, and the export should
  not copy contributors' emails into Storage. Cost if wrong: none.
- **The harness speaks MCP's Streamable HTTP with httpx directly** (JSON-RPC `initialize`,
  `notifications/initialized`, `tools/list`, `tools/call`) rather than adding the `mcp` SDK, whose
  2.x pulls in a second HTTP stack into a pinned environment. Real-client interoperability is shown
  by the Claude Code demo. Cost if wrong: the harness misses an SDK-only quirk the demo would show.
- **The Function project SDK is `Microsoft.Azure.Functions.Worker.Sdk`**, as Microsoft's .NET 10 MCP
  sample uses (the research found the docs naming `Azure.Functions.Sdk`); if the build demands the
  other, the implementer switches and reports it. Cost if wrong: a package swap.

## Review Focus

1. **A changed artefact whose old chunks span more than one page of the lookup.** The stale-key
   lookup must page until it has every key, or old chunks survive. (Task 1
   `KeysForArtefact_PagesUntilEveryKeyIsRead`)
2. **An artefact ID holding a quote or other OData-significant character.** The lookup filter must
   escape it, not fail or match the wrong artefact. (Task 1 `KeysForArtefact_EscapesQuotesInTheFilter`)
3. **An event for a blob that has gone by the time it is processed** (deleted or replaced). The
   function must fail the attempt so the queue retries and, if it stays gone, poisons it, never
   report success with nothing ingested. (Task 2 `Ingest_ABlobThatIsGone_FailsTheAttempt`)
4. **An empty, whitespace-only or very long query to the tool.** An empty one is a tool error with a
   fixed message; a long one is cut before embedding, not sent whole. (Task 3
   `Search_EmptyQuery_IsAToolError`, `Search_AVeryLongQuery_IsCutBeforeEmbedding`)
5. **A bootstrap change that disturbs what runs today.** The existing account, deployments,
   identities, Entra app and monitoring are unchanged. (Task 5 `existing_resources_unchanged`)

---

### Task 1: `ReleaseLens.Functions.Common` and the test project

**Files:**
- Create: `src/ReleaseLens.Functions.Common/` (`ReleaseLens.Functions.Common.csproj`, `ChunkKeys.cs`,
  `BearerTokenHandler.cs`, `EmbeddingsClient.cs`, `SearchIndexClient.cs`, `IndexChunk.cs`,
  `ArtefactJson.cs`)
- Create: `tests/ReleaseLens.Functions.Tests/` (project and the tests below)
- Modify: `ReleaseLens.sln`, `Directory.Packages.props`

**Interfaces:**
- Produces:
  - `static class ChunkKeys { string For(string artefact, int index); IReadOnlyList<string> Stale(IEnumerable<string> existing, IReadOnlySet<string> fresh); }`
  - `sealed class BearerTokenHandler(TokenCredential credential, string scope, TimeProvider time) : DelegatingHandler` — attaches `Bearer`, caches until 5 minutes before expiry
  - `sealed class EmbeddingsClient(HttpClient http, string deployment)` with
    `Task<IReadOnlyList<float[]>> EmbedAsync(IReadOnlyList<string> inputs, CancellationToken ct)`; base
    address the account's v1 URL (`…/openai/v1/`), `POST embeddings` with `{model, input}`, results ordered by `index`, each 1,536 long or it throws
  - `sealed record IndexChunk(string ChunkId, string Artefact, string Content, float[] Vector)`
  - `sealed record SearchHit(string ChunkId, string Artefact, string Content, double Score)`
  - `sealed class SearchIndexClient(HttpClient http, string indexName)` (base address the search
    endpoint, API version `2026-04-01`, the same as project 2) with
    `Task UpsertAsync(IReadOnlyList<IndexChunk>, CancellationToken)` (`mergeOrUpload`),
    `Task DeleteAsync(IReadOnlyList<string> keys, CancellationToken)`,
    `Task<IReadOnlyList<string>> KeysForArtefactAsync(string artefact, CancellationToken)`,
    `Task<IReadOnlyList<SearchHit>> HybridSemanticAsync(string query, float[] vector, int top, CancellationToken)`
    (keyword plus vector `k = top`, `queryType: semantic`, configuration `default`, score the reranker score)
  - `static class ArtefactJson { IEvidenceRecord Parse(string json); string Serialize(IEvidenceRecord record); }` —
    camelCase, an `entityType` discriminator (the wire names `commit`, `issue`, `pull_request`,
    `release`), `authorEmail` always written as null; `InvalidArtefactException` on bad input
  - Scopes as constants: `OpenAiScope = "https://ai.azure.com/.default"`, `SearchScope = "https://search.azure.com/.default"`

- [ ] **Step 1: Write the failing tests** (stub handler as in `tests/ReleaseLens.Llm.Tests`):
  - `ChunkKeys_ForIsBase64UrlWithoutPaddingThenTheIndex`: `For("release:python-1.44.1", 3)` equals the
    base64url of the UTF-8 bytes, no `=`, then `-3`; only `[A-Za-z0-9_-]` characters
  - `ChunkKeys_StaleIsEveryExistingKeyNotFresh`, including project 2's numeric keys (`"17"`)
  - `BearerTokenHandler_AttachesTheTokenForItsScope`, `…_ReusesUntilFiveMinutesBeforeExpiry`
  - `Embeddings_PostsTheDeploymentAndInputs_ReturnsVectorsInIndexOrder` (the reply out of order)
  - `Embeddings_AVectorThatIsNot1536Long_Throws`
  - `Search_UpsertSendsMergeOrUploadWithEveryField`, `Search_DeleteSendsDeleteActionsByKey`
  - `KeysForArtefact_FiltersOnTheArtefactAndSelectsOnlyTheKey`
  - `KeysForArtefact_PagesUntilEveryKeyIsRead` (Review Focus 1): two pages via `@odata.nextLink` or
    `skip`, all keys returned
  - `KeysForArtefact_EscapesQuotesInTheFilter` (Review Focus 2): `issue:it's` becomes `'issue:it''s'`
  - `HybridSemantic_SendsKeywordVectorAndSemanticAndReturnsRerankerScores`
  - `Search_AFailureStatus_ThrowsWithTheStatusAndNoBody` (no backend text in the exception message)
  - `ArtefactJson_RoundTripsEachEntityType`, `ArtefactJson_WritesAuthorEmailAsNull`,
    `ArtefactJson_RejectsBadJsonUnknownTypeAndMissingFields` (each an `InvalidArtefactException`)
- [ ] **Step 2: Run, expect FAIL.** `dotnet test tests/ReleaseLens.Functions.Tests`
- [ ] **Step 3: Implement.** `Functions.Common` references `ReleaseLens.Core` only (plus
  `Azure.Identity`, `Microsoft.Extensions.Http`); add any new package versions to
  `Directory.Packages.props` with a comment that says why, as the file's existing entries do.
- [ ] **Step 4: Run, expect PASS**, and the whole `dotnet test` (the Docker-based suites may fail
  locally for want of Docker only; say so).
- [ ] **Step 5: Commit** `feat(functions): the shared code for both apps: chunk keys, keyless embeddings and search clients, the artefact format`

---

### Task 2: The ingest app

**Files:**
- Create: `src/ReleaseLens.Functions.Ingest/` (`.csproj`, `Program.cs`, `host.json`, `ArtefactEvent.cs`,
  `IngestArtefact.cs`, `IngestFunction.cs`)
- Test: `tests/ReleaseLens.Functions.Tests/Ingest*.cs`

**Interfaces:**
- Consumes: Task 1's types.
- Produces:
  - `sealed record BlobRef(string Container, string BlobName)`;
    `static class ArtefactEvent { BlobRef Parse(string queueMessage); }` — accepts the event as plain
    JSON or base64 of it; a single event object or a one-element array; requires `eventType`
    `Microsoft.Storage.BlobCreated` and a subject under `/blobServices/default/containers/artefacts-in/blobs/`;
    anything else throws `InvalidArtefactException`
  - `sealed class IngestArtefact(EmbeddingsClient embeddings, SearchIndexClient index)` with
    `Task<IngestResult> IngestAsync(IEvidenceRecord record, CancellationToken ct)` where
    `sealed record IngestResult(string Artefact, int Chunks, int Deleted)`
  - `IngestFunction`: `[Function("ingest")]`, `[QueueTrigger("ingest-events", Connection = "IngestQueue")] string message`;
    reads the blob with `BlobServiceClient` (the managed identity), then `ArtefactJson.Parse`, then
    `IngestAsync`; logs counts and durations only
- `host.json`: `extensions.queues.maxDequeueCount = 3`, `visibilityTimeout = "00:00:30"`,
  `messageEncoding = "none"` (the parser handles base64 itself).

- [ ] **Step 1: Write the failing tests:**
  - `ArtefactEvent_ParsesPlainJson`, `ArtefactEvent_ParsesBase64Json`, `ArtefactEvent_ParsesAOneEventArray`
  - `ArtefactEvent_RejectsAnotherEventTypeAnotherContainerAndJunk`
  - `Ingest_ChunksWithTheAppsOptions_EmbedsOnce_UpsertsEveryChunkWithItsKey` (a fixture issue; the
    chunk count equals `EvidenceChunker` with `ChunkOptions.Default`; one embeddings call)
  - `Ingest_DeletesEveryOldKeyOfTheArtefact_IncludingNumericOnes`
  - `Ingest_SendingTheSameArtefactTwice_WritesTheSameKeysAndDeletesNothing`
  - `Ingest_AnEmbeddingOr429Failure_PropagatesSoTheQueueRetries`
  - `Ingest_ABlobThatIsGone_FailsTheAttempt` (Review Focus 3): a 404 on the blob read throws
  - `Ingest_TheLogHoldsNoArtefactText` (a captured logger sees no content or title)
  - `HostJson_HasThreeDequeuesAThirtySecondVisibilityAndNoEncoding`
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** The function's work lives in `IngestArtefact` and `ArtefactEvent` so the
  tests need no Functions host. `Program.cs` builds the clients from the app settings Task 8 names
  with `ManagedIdentityCredential(AZURE_CLIENT_ID)`.
- [ ] **Step 4: Run, expect PASS**, and `dotnet build` of the whole solution.
- [ ] **Step 5: Commit** `feat(functions): the ingest app: an artefact's event becomes searchable chunks, keyless, with stale chunks removed`

---

### Task 3: The tool app

**Files:**
- Create: `src/ReleaseLens.Functions.Tool/` (`.csproj`, `Program.cs`, `host.json`, `CorpusSearch.cs`,
  `SearchCorpusTool.cs`)
- Test: `tests/ReleaseLens.Functions.Tests/Tool*.cs`

**Interfaces:**
- Consumes: Task 1's `EmbeddingsClient`, `SearchIndexClient`, `SearchHit`.
- Produces:
  - `sealed record ToolHit(string Artefact, string Type, string Excerpt, double Score)`
  - `sealed class CorpusSearch(EmbeddingsClient embeddings, SearchIndexClient index)` with
    `Task<IReadOnlyList<ToolHit>> SearchAsync(string query, int? top, CancellationToken ct)`; `top`
    defaults to 5 and is clamped to 1–10; `Type` is the artefact's prefix before `:`; `Excerpt` at most
    500 characters; a query is cut to 2,000 characters before embedding; an empty or whitespace query
    throws `ToolInputException("query must not be empty")`
  - `SearchCorpusTool`: the MCP tool `search_corpus` (the extension's tool-trigger attributes, version
    1.6.0; read its README for the attribute names), returning the hits as JSON; any search or
    embedding failure becomes the fixed tool error `"search failed"`
- `host.json`: `extensions.mcp.system.webhookAuthorizationLevel = "Anonymous"`; the server's name
  `releaselens-search` and a one-line instruction.

- [ ] **Step 1: Write the failing tests:**
  - `Search_DefaultsToFiveAndClampsTopToTen`, `Search_TopBelowOneBecomesOne`
  - `Search_MapsTypeAndCutsTheExcerptAt500`
  - `Search_EmptyQuery_IsAToolError` (Review Focus 4)
  - `Search_AVeryLongQuery_IsCutBeforeEmbedding` (Review Focus 4): the embeddings request holds 2,000 characters
  - `Tool_ASearchFailure_IsTheFixedToolError_WithNoBackendText`
  - `HostJson_TurnsTheMcpExtensionKeyOff`
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**, the logic in `CorpusSearch`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(functions): the tool app: search_corpus, read-only, over hybrid search with the semantic ranker`

---

### Task 4: The Worker's `export-artefacts`

**Files:**
- Modify: `src/ReleaseLens.Worker/Program.cs` (a new case beside `export-corpus`; usage text),
  `src/ReleaseLens.Worker/ReleaseLens.Worker.csproj` (reference `ReleaseLens.Functions.Common`)
- Modify, if missing: `src/ReleaseLens.Storage/Repositories/EvidenceRepository.cs` (a
  `GetPullRequestAsync` beside `GetCommitAsync`, `GetIssueAsync`, `GetReleaseAsync`)
- Test: in `tests/ReleaseLens.Storage.Tests` (Postgres fixture) for any repository method added;
  `tests/ReleaseLens.Functions.Tests` for the key parsing

**Interfaces:**
- Consumes: `ArtefactJson.Serialize`, `EvidenceRepository.Get*Async`.
- Produces: `dotnet run --project src/ReleaseLens.Worker -- export-artefacts <dir> <key>...`, keys as
  `issue:123`, `pull_request:45`, `commit:<sha>`, `release:<tag>`; one file per key named
  `<base64url of the key>.json`; an unknown key is an error naming it; exit 1 if any key is not found.
  Pure parsing lives in `ArtefactKeys.Parse(string) -> EvidenceKey` in `Functions.Common`.

- [ ] **Step 1: Write the failing tests:** `ArtefactKeys_ParsesEachType`, `ArtefactKeys_RejectsJunk`;
  and, if added, `GetPullRequest_ReturnsTheStoredPullRequest` in the Storage tests.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS** (Storage tests need Docker; if unavailable here, say so — CI runs them).
- [ ] **Step 5: Commit** `feat(worker): export-artefacts writes real artefacts as the ingest app's JSON`

---

### Task 5: Bootstrap: the ingestion storage, Event Grid, identities, the tool's app registration, roles

**Files:**
- Create: `infra/bootstrap/ingestion.tf` (the storage account `strlingest<existing suffix>`, shared keys
  off, the containers `artefacts-in`, `deadletter-events`, `deploy-ingest`, `deploy-tool`, the two
  queues; the Event Grid system topic with a system-assigned identity and its subscription),
  `infra/bootstrap/search_tool_app.tf` (the `releaselens-search-tool` registration, as project 4's
  `gateway_app.tf`: identifier URI, one app role `Tool.Invoke`, no secret, assignment required)
- Modify: `identities.tf` (two identities), `roles.tf` (the roles below), `versions.tf`
  (`Microsoft.EventGrid` and `Microsoft.Web` in `resource_providers_to_register`), `state.tf` (the
  container `tfstate-functions`, for Task 8's backend), `outputs.tf`
- Test: `infra/bootstrap/tests/ingestion.tftest.hcl`, `tests/search_tool_app.tftest.hcl` (new);
  `tests/roles.tftest.hcl`; `tests/infra/test_terraform_static.py`

**Interfaces:**
- Produces outputs (read by Tasks 6 and 7): `ingest_identity_id`, `ingest_identity_client_id`,
  `ingest_identity_principal_id`, `tool_identity_id`, `tool_identity_client_id`,
  `tool_identity_principal_id`, `ingest_storage_account_name`, `ingest_storage_blob_endpoint`,
  `ingest_storage_queue_endpoint`, `search_tool_app_client_id`, `search_tool_app_identifier_uri`.
- **The subscription:** `included_event_types = ["Microsoft.Storage.BlobCreated"]`;
  `subject_begins_with = "/blobServices/default/containers/artefacts-in/"`, `subject_ends_with = ".json"`;
  `storage_queue_endpoint` the `ingest-events` queue with `delivery_identity` the system identity; a
  `storage_blob_dead_letter_destination` on `deadletter-events` with `dead_letter_identity` the system identity.
- **The roles** (read the research notes §7 for the host-storage set and confirm each name against
  Azure's built-in roles):
  - ingest: Storage Blob Data Reader on `artefacts-in`; Storage Queue Data Contributor on each queue;
    Cognitive Services OpenAI User on the account; its host storage (blob, queue and table data roles
    on the account, as §7 lists for a Flex app's identity-based host storage)
  - tool: Cognitive Services OpenAI User; its host storage
  - the Event Grid topic's identity: Storage Queue Data Message Sender on `ingest-events`; Storage
    Blob Data Contributor on `deadletter-events`
  - the owner: Storage Blob Data Contributor on `artefacts-in`, `deploy-ingest` and `deploy-tool`, and
    on the state container `tfstate-functions` (`owner_state_functions`, as `owner_state_gateway`)
  - `Tool.Invoke` assigned to the gateway identity (`azuread_app_role_assignment`, in `roles.tf`)

- [ ] **Step 1: Write the failing tests:** the account (keys off, TLS 1.2, no public blob access); the
  four containers private; the two queues; the topic's identity; the subscription's filter, endpoint and
  both identities; the two identities; the registration (no secret, assignment required, one role);
  every role's name, principal and scope (distinct mock IDs, as `roles.tftest.hcl` does);
  `existing_resources_unchanged` (Review Focus 5): the account, its deployments, the three existing
  identities, the gateway app and monitoring as before; static: providers 12, the new role-assignment
  count all in `roles.tf`, no shared-key or SAS output.
- [ ] **Step 2: Run, expect FAIL** (WSL, clean copy).
- [ ] **Step 3: Implement**, in the stack's style.
- [ ] **Step 4: Run, expect PASS**, then update every count of providers, role assignments and
  identities in `README.md`, `docs/architecture.md` and `infra/bootstrap/README.md`.
- [ ] **Step 5: Commit** `feat(infra): keyless ingestion storage and Event Grid delivery, the two Function identities, and the tool's app registration`

---

### Task 6: The search stack's two roles

**Files:**
- Modify: `infra/search/main.tf` (two `azurerm_role_assignment`s), a new `infra/search/bootstrap.tf`
  (`terraform_remote_state` of the bootstrap, as `infra/gateway/bootstrap.tf`), `variables.tf`
  (`tfstate_storage_account`), `tests/search.tftest.hcl`, `README.md`; `tests/infra/test_terraform_static.py`
  (the search stack's role-assignment count becomes 4)

**Interfaces:**
- Consumes: Task 5's `ingest_identity_principal_id`, `tool_identity_principal_id`.

- [ ] **Step 1: Write the failing tests:** Search Index Data Contributor for the ingest principal and
  Search Index Data Reader for the tool principal, both scoped to the service; the owner's two roles
  unchanged.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS** (WSL), and `python -m pytest tests/infra -q`.
- [ ] **Step 5: Commit** `feat(infra): the search stack grants the ingest identity write and the tool identity read`

---

### Task 7: The tool's MCP API policy, and its checks

**Files:**
- Create: `infra/functions/policies/mcp-api.xml`
- Create: `tests/infra/test_functions_policies.py`

**Interfaces:**
- Produces the named values the policy uses, which Task 8 creates exactly: `tool-tenant-id`,
  `tool-gateway-app-client-id`, `tool-app-audience`, `tool-gateway-identity-client-id`.

- [ ] **Step 1: Write the failing tests** (parse with `xml.etree`):
  - `test_inbound_order`: `validate-azure-ad-token`, `rate-limit-by-key`, `emit-metric`,
    `authentication-managed-identity`
  - `test_token_validation`: tenant, audiences `api://{{tool-gateway-app-client-id}}` and
    `{{tool-gateway-app-client-id}}`, required claim `roles` containing `Gateway.Invoke`, output `jwt`
  - `test_rate_limit`: `calls="20"`, `renewal-period="60"`, counter key the `oid` from `jwt`
  - `test_metric`: name `Tool Calls`, one dimension `Caller` from `oid`
  - `test_backend_credential`: resource `{{tool-app-audience}}`, client ID `{{tool-gateway-identity-client-id}}`
  - `test_no_body_is_read`: no `context.Request.Body` or `context.Response.Body` anywhere
  - `test_on_error_body_is_fixed`, `test_no_literal_identifiers`
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Write the file**, with short comments saying why (spec §5.2).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(infra): the search tool's gateway policy: who, how often, then the gateway's own credential`

---

### Task 8: The `infra/functions` stack

**Files:**
- Create in `infra/functions`: `versions.tf`, `backend.tf` (container `tfstate-functions`, from Task 5,
  key `functions.tfstate`),
  `variables.tf`, `remote_state.tf` (bootstrap, search, gateway), `main.tf` (resource group, the Flex
  plan, the two apps via azapi `Microsoft.Web/sites` and their `authsettingsV2`), `mcp_api.tf` (the MCP
  API on the gateway at `2025-09-01-preview`, its policy from `policies/mcp-api.xml`, the four named
  values), `outputs.tf`, `terraform.tfvars.example`, `README.md`, `.terraform.lock.hcl`
- Test: `infra/functions/tests/functions.tftest.hcl`; `tests/infra/test_terraform_static.py`;
  `.github/workflows/ci.yml` (a "Check infra/functions" step; its lock file in the cache key)

**Interfaces:**
- Consumes: Task 5's outputs, the search stack's endpoint, the gateway stack's service ID, identity
  client ID and base host, Task 7's file and named values.
- Produces outputs: `tool_mcp_url` (the gateway's MCP endpoint for the tool), `tool_app_url` (the app's
  own `/runtime/webhooks/mcp`, for T3), `ingest_app_name`, `tool_app_name`.
- **App settings** (both, unless noted): `AzureWebJobsStorage__accountName`,
  `AzureWebJobsStorage__credential = "managedidentity"`, `AzureWebJobsStorage__clientId`,
  `AZURE_CLIENT_ID`, `OPENAI_BASE_URL`, `OPENAI_EMBEDDING_DEPLOYMENT = "releaselens-embed-small"`,
  `SEARCH_ENDPOINT`, `SEARCH_INDEX = "releaselens-chunks"`, `APPLICATIONINSIGHTS_CONNECTION_STRING`
  (sensitive), `APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=<id>;Authorization=AAD"`;
  ingest only: `IngestQueue__queueServiceUri`, `IngestQueue__credential`, `IngestQueue__clientId`,
  `INGEST_BLOB_ENDPOINT`. No `AzureWebJobsStorage` connection string.
- **The apps:** runtime `dotnet-isolated` `10.0`, instance memory 2,048 MB, no always-ready instances,
  deployment storage the `deploy-ingest`/`deploy-tool` container with user-assigned-identity
  authentication; `authsettingsV2`: require authentication, unauthenticated action 401, the Entra
  provider with allowed audience `search_tool_app_identifier_uri` and allowed applications the gateway
  identity's client ID.

- [ ] **Step 1: Write the failing tests** (mock providers and remote state as `infra/gateway`'s tests
  do): the group; both apps' kind, runtime and version, memory, identity, deployment container and its
  auth; every app setting above and **no** `AzureWebJobsStorage` key; authentication required with the
  audience and allowed application; the MCP API's type `mcp`, its backend `serviceUrl` to the tool app,
  its policy equal to the file; the four named values not secret; outputs. Static: registers no
  providers; no role assignments; not in `rg-releaselens`; no key or connection string except the
  sensitive Application Insights one.
- [ ] **Step 2: Run, expect FAIL** (WSL). Check the azapi bodies against the `Microsoft.Web/sites` and
  `Microsoft.ApiManagement/service/apis@2025-09-01-preview` schemas, and the research notes' Bicep
  shape; report differences.
- [ ] **Step 3: Implement.** No `prevent_destroy`.
- [ ] **Step 4: Run, expect PASS**, plus `python -m pytest tests/infra -q` and
  `python scripts/check_workflows.py .github/workflows`.
- [ ] **Step 5: Commit** `feat(infra): a per-session functions stack: two keyless Flex apps, and the tool published on the gateway`

---

### Task 9: The harness

**Files:**
- Create in `eval/app/functions/`: `__init__.py`, `__main__.py`, `mcp_http.py`, `ingest_checks.py`,
  `tool_checks.py`, `report.py`
- Test: `eval/tests/functions/` (with an `__init__.py`)

**Interfaces:**
- Consumes: `app.retrieval.azure_auth.TokenSource`, `app.retrieval.search_index` (the direct query for
  T1), `app.gateway.report.write` (the identifier refusal).
- Produces:
  - `mcp_http.McpSession(http: httpx.Client, url: str, token: str | None)` with `initialize() -> dict`,
    `list_tools() -> list[str]`, `call(name: str, arguments: dict) -> dict`, speaking JSON-RPC over
    Streamable HTTP (accepting `application/json` or a single `text/event-stream` message), carrying
    `Mcp-Session-Id` once the server sets it
  - `ingest_checks`: `i1_new_searchable(...)`, `i2_changed_replaces(...)`, `i3_duplicate_harmless(...)`,
    `i4_broken_to_poison(...)`, each returning `app.gateway.checks.CheckResult`; polling with an
    injectable clock and sleep; the poison queue read with the queue REST API and the owner's token
    (scope `https://storage.azure.com/.default`), peeking only
  - `tool_checks`: `t1_same_search(...)`, `t2_gateway_refuses(...)`, `t3_no_bypass(...)`,
    `t4_rate_limit(...)` (30 concurrent calls)
  - CLI: `upload`, `ingest-checks`, `tool-checks`, `report`, run as `python -m app.functions <command>`;
    every file written goes through `app.gateway.report.write`
- Pass rules exactly as spec §7: I1 120 s, I4 10 minutes, T1 ten questions top-5 in order, T4 at least one 429 with `Retry-After`.

- [ ] **Step 1: Write the failing tests** on fixtures: each check passes and fails on its rule; the MCP
  session sends the bearer header, handles both reply content types, keeps the session ID, and maps a
  tool error; uploads are named uniquely per session; no token, URL or hostname in any written file.
- [ ] **Step 2: Run, expect FAIL.** From `eval/`, `.venv/Scripts/python.exe -m pytest tests/functions -q`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS**, and the whole eval suite.
- [ ] **Step 5: Commit** `feat(eval): the Functions harness: I1–I4, T1–T4 and a report that refuses identifiers`

---

### Task 10: The runbook and the README

**Files:**
- Create: `docs/runbook-functions.md`
- Modify: `README.md` (a short "Azure Functions" section), `docs/architecture.md` (the ingestion path
  and the tool behind the gateway)

- [ ] **Step 1: Write the runbook** from spec §11, one section per step, each marked **Ask first**, in
  `docs/runbook-gateway.md`'s style (its rules section, its `acctok` and `tfout` helpers, the CA bundle,
  WSL for Terraform, the findings scratch file, the clock rules), with: the bulk load holding back I1's
  five artefacts; the code deploy with the owner's sign-in; the smoke test; the Claude Code
  `headersHelper` set-up; project 4's follow-up checks; a findings table for every spec §12 item; the
  destroy order and its checks.
- [ ] **Step 2: Check** every §11 step and §12 item appears; `python -m pytest tests/infra -q` passes
  (the doc scan).
- [ ] **Step 3: Commit** `docs: the Functions runbook, and where the two apps fit in the architecture`
