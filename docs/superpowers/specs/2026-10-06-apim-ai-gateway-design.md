# API Management as the AI gateway — design

**Status: specification, approved by the owner on 2026-10-06.** Nothing is built, applied or measured.
Every figure below is either read from a named source on a stated date, or labelled as an
estimate. Claims that could not be checked are labelled *unverified* and listed in §12. Changes
made after approval are in §13, dated, each on the owner's decision.

- Project 4 of the portfolio plan, in ReleaseLens, `main` at `216423a`
- Builds on project 1 (the keyless Azure OpenAI account, the bootstrap stack and the provider's
  bounded 429 wait) and on project 2's pattern for a stack that exists only during a session
- Sources: the research notes of 2026-10-06 in the private plan repository, which cite each
  Microsoft Learn page and provider release they rely on; this document restates what it needs
- Agreed with the owner in brainstorming on 2026-10-06, section by section

---

## 1. Purpose

Put Azure API Management in front of ReleaseLens's model calls and show, with a small measured
test, what a gateway adds over calling the model directly:

- **Failover.** When the primary deployment throttles, requests are served by a second account
  in another region, without the client doing anything.
- **Budgets per caller.** Each Entra identity has its own token budget, enforced before a model
  is called, and one caller using up its budget does not block another.
- **Usage per caller.** Token usage is recorded per identity, with no prompts logged.
- **Keyless both ways.** Callers present Entra tokens; the gateway calls the models with its
  own managed identity; no key exists anywhere.

"Failover held" and "failover did not hold" are both publishable results. The rule that decides
between them is fixed in §7 before any live run.

**Why it matters.** A gateway is the standard enterprise answer to shared model quota, and the
portfolio's earlier projects call models directly. This one shows the gateway's policies as code,
measured, with its limits stated.

**Not a production change.** Direct mode stays the app's default, and the gateway exists only
during a session (§3.2).

---

## 2. What exists today

Read from the repository on 2026-10-06.

- **Provider.** `AzureOpenAiChatProvider` (`src/ReleaseLens.Llm/Providers/Azure/`):
  - calls Azure's v1 endpoint, `<base>/chat/completions`, with the deployment name as `model`
    in the body and no `api-version` parameter
  - authenticates through `EntraTokenHandler` with a token for `AzureOpenAi:TokenScope`; no key
    header is ever set. The scope is already configurable, and its comment names an API
    Management gateway as the reason
  - on a 429, waits once if `retry-after-ms` (or, failing that, `retry-after` in whole seconds)
    fits what is left of the query's 3,000 ms wait budget (`AgentModels.RateLimitWaitBudgetMs`),
    then sends once more
  - `ProviderHttp` maps 401, 403, 408, 429 and every 5xx to `ProviderUnavailableException`, so
    the provider chain moves on
- **Credentials.** `AzureCredentialFactory`: `AzureCli` with the tenant pinned, or
  `ManagedIdentity` with a user-assigned client ID. `DefaultAzureCredential` is deliberately not
  an option.
- **Bootstrap stack** (`infra/bootstrap`, applied by the owner, long-lived, locked):
  - the `AIServices` account in australiaeast, key authentication off, with `releaselens-chat`
    (gpt-4.1-mini 2025-04-14, Global Standard, capacity 300) and the two embedding deployments
  - identities `id-releaselens-deploy` (federated credential for the GitHub environment `azure`)
    and `id-releaselens-app`
  - every role assignment in the design (`roles.tf`), including the owner's and the app's
    Cognitive Services OpenAI User on the account
  - the state storage account, with a container per stack
  - the budget alert (USD 50 a month)
- **App stack** (`infra/terraform`): the API as a Container App, which sets `AzureOpenAi__BaseUrl`.
- **Search stack** (`infra/search`): project 2's per-session stack in its own resource group,
  applied and destroyed by the owner. The gateway stack follows this pattern.
- **Harness.** `eval/`, Python, with `httpx` and `azure-identity` already pinned.

---

## 3. Architecture

### 3.1 Bootstrap additions (long-lived, nothing bills by the hour)

All in `infra/bootstrap`, applied by the owner.

- **Second account.** `AIServices`, in `var.failover_location` (default `southeastasia`), key
  authentication off, custom subdomain from the existing suffix. Australia Southeast is not used:
  it does not offer Global Standard for this model (research §7).
- **Deployments.** The same two names on both accounts, all gpt-4.1-mini `2025-04-14`, Global
  Standard, `NoAutoUpgrade`:

  | Deployment | australiaeast | Southeast Asia |
  |---|---:|---:|
  | `releaselens-chat` | 300 (existing) | 100 |
  | `releaselens-chat-failover-test` | **1** (1,000 tokens a minute) | 100 |

  The request names the deployment in its body, so one backend pool serves both and nothing is
  re-applied between runs. Only the australiaeast failover-test deployment is tiny, so it
  throttles on purpose. Global Standard deployments bill per token and cost nothing idle.
- **Gateway identity.** `id-releaselens-gateway`, user-assigned. It is created here, not in the
  per-session stack, so its role assignments exist and have propagated before a session starts,
  and the gateway stack needs no rights to assign roles. Roles:
  - Cognitive Services OpenAI User on **both** accounts
  - Monitoring Metrics Publisher on the Application Insights resource
- **Entra app registration** `releaselens-ai-gateway`, through the `azuread` provider (new to
  this stack):
  - identifier URI `api://<its client id>`, access tokens version 2
  - one app role, `Gateway.Invoke`, allowed for users and applications
  - one delegated scope, `access_as_user`, with the Azure CLI pre-authorised on it (its client
    ID from `azuread_application_published_app_ids`, never a literal), so `az` can get a token
    for the gateway
  - a service principal with `app_role_assignment_required = true`: only an assigned identity
    can get a token at all
  - no secrets and no certificates
  - `Gateway.Invoke` assigned to the owner, `id-releaselens-app` and `id-releaselens-deploy`
- **Monitoring.** Log Analytics `log-releaselens` (30-day retention, a daily ingestion cap of
  0.1 GB) and workspace-based Application Insights `appi-releaselens`, both with local
  authentication off. They outlive the gateway sessions, so usage from every session stays in
  one place.
- **State.** A container `tfstate-gateway`, with the owner's data role on it.

The owner's and the app's direct roles on the australiaeast account stay. The Southeast Asia
account grants a role to the gateway identity only.

### 3.2 The gateway stack (`infra/gateway`, per session)

Applied by the owner at the start of a session and destroyed at its end, like `infra/search`. It
reads the bootstrap's outputs through `terraform_remote_state` (the owner has the data role on
that container), so no identifier is copied by hand.

- **Resource group** `rg-releaselens-gateway`, its own, so the nightly destroy's empty-group
  check on `rg-releaselens` never finds it.
- **API Management** `apim-releaselens-<suffix>`, `BasicV2_1`, australiaeast, with the gateway
  identity attached. The suffix is a `random_string` of the stack, new each session, so APIM's
  48-hour hold on a deleted name never blocks the next session.
  - Provider features: `purge_soft_delete_on_destroy = true`, `recover_soft_deleted = false`,
    so a destroy purges and a re-create never brings back an old configuration.
- **Named values** (not secret: they are identifiers, kept out of the repository by coming from
  the bootstrap's state): the tenant ID, the gateway app's client ID, the gateway identity's
  client ID.
- **The API.** Display name `Azure OpenAI (v1)`, path `openai`, `subscription_required = false`,
  one operation, `POST /v1/chat/completions`. It is in a version set (segment scheme) with one
  version, `v1`, so a later breaking change has a home. No product and no subscription exist.
- **Backends** (azapi, `Microsoft.ApiManagement/service/backends@2024-05-01`; azurerm has no
  pool type, PR #31144 is open):
  - `aoai-primary` and `aoai-secondary`, type Single, URL `https://<account>.openai.azure.com/openai`,
    each with one circuit-breaker rule, the official lab's values: `count = 1`, `interval = PT5M`,
    status codes 429–429, `tripDuration = PT1M`, `acceptRetryAfter = true`
  - `aoai-pool`, type Pool: `aoai-primary` priority 1, `aoai-secondary` priority 2
- **Logger** to `appi-releaselens` with `identity_client_id` set to the gateway identity, so
  ingestion uses Entra. The API's Application Insights diagnostic logs no request or response
  body (0 bytes), at 100% sampling, with `metrics = true` set through `azapi_update_resource`
  (azurerm has no argument for it, PR #28499 is open).
- **Policies** as XML files under `infra/gateway/policies/`, loaded with `file()` (§4).
- **Revision 2** of `v1`, non-current, with the change in §4.3; made current only after it is
  called at `;rev=2` in the smoke test.

### 3.3 Callers

| Caller | Where | Credential | Used for |
|---|---|---|---|
| Owner | locally | `az`, tenant pinned | the measured test; a local ReleaseLens API in gateway mode |
| `id-releaselens-deploy` | GitHub Actions, environment `azure` | its existing federated credential | check B3 only (§7.3) |
| `id-releaselens-app` | the Container App | its managed identity | can be switched to the gateway; not in the measured evidence |

The app identity is not measured because that needs the app stack deployed during a gateway
session. The local API run as the owner covers the same code path.

---

## 4. One request, end to end

### 4.1 Inbound, in order

1. **Check the caller.** `validate-azure-ad-token` with the tenant from a named value, audiences
   `api://<client id>` and `<client id>` (the two forms of the audience), and the required claim
   `roles` containing `Gateway.Invoke`. The validated token goes to a variable, `jwt`. Any
   failure returns 401, and no model is called.
2. **Apply the budget.** `llm-token-limit`, `counter-key` the token's `oid`,
   `estimate-prompt-tokens="true"`, `tokens-per-minute = var.tokens_per_minute` (default
   10,000) and `token-quota = var.tokens_per_day` (default 50,000) with
   `token-quota-period="Daily"`. Over the minute budget returns 429 with `Retry-After`; over the
   daily budget returns 403. Prompt tokens are estimated before forwarding, so a call over budget
   never reaches a model. Every caller has the same limits and its own counter. The defaults are
   low on purpose: nothing real calls the gateway, and low limits make the budget checks quick
   and cheap.
3. **Record usage.** `llm-emit-token-metric`, namespace `releaselens-gateway`, with three
   dimensions: `API ID`, `Caller` (the `oid`) and `Deployment` (the body's `model`). Three
   callers and two deployments sit well under APIM's cap of 100 values per dimension.
4. **Swap the credential.** `authentication-managed-identity`, resource
   `https://cognitiveservices.azure.com`, client ID the gateway identity's. It replaces the
   caller's `Authorization` header, so the caller's token never reaches a model and callers need
   no rights on the models.
5. **Route.** `set-backend-service backend-id="aoai-pool"`.

### 4.2 Backend, outbound and errors

- **Backend.** `retry` with `condition="@(context.Response.StatusCode == 429)"`, `count="1"`,
  `interval="0"`, `first-fast-retry="true"`, around `forward-request buffer-request-body="true"`.
  The 429 that trips the primary's breaker is re-sent, and the pool now picks the secondary. The
  gateway's own 429 (step 2) is never retried: it happens before the backend section.
- **Both backends tripped.** APIM returns 503.
- **Outbound.** The body is unchanged. Azure OpenAI's `x-ms-region` response header names the
  region that answered (*unverified*, §12; the smoke test settles which signal the rule uses,
  §7.4).
- **On error.** A short fixed JSON body with the status code and no backend detail, so no
  account hostname leaks.
- **Content filter.** A model's 400 for a filtered prompt passes through unchanged, so the app's
  existing filter handling still works.

### 4.3 Revision 2

A non-breaking change: the outbound section removes backend response headers a caller does not
need, keeping `x-ms-region`, `retry-after`, `retry-after-ms` and the content headers. It is
called at `/openai;rev=2/v1/chat/completions` in the smoke test, then made current with an
`azurerm_api_management_api_release`.

### 4.4 Global Standard, stated plainly

A Global Standard deployment may process a prompt in any Azure region, though it is anchored to
its account's region for availability (research §7). "Answered by Southeast Asia" in this
document means the Southeast Asia account served the request, not that the prompt was processed
there. The failover shown is between accounts and their capacity.

---

## 5. The app

**Gateway mode is configuration, not a new code path:**
- `AzureOpenAi:BaseUrl` = `https://<gateway host>/openai/v1/`
- `AzureOpenAi:TokenScope` = `api://<gateway app client id>/.default`

**Changes:**
- **App stack.** `infra/terraform` gains an `AzureOpenAi__TokenScope` setting beside the existing
  `AzureOpenAi__BaseUrl`, from a variable whose default is today's scope, so a deployed API can
  be switched to the gateway without a code change.
- **A test** that pins gateway mode: with a gateway base URL and scope, the request goes to
  `<gateway>/openai/v1/chat/completions`, the token is requested for the gateway scope, and no
  `api-key` header is sent.
- **No change to the 429 handling.** The gateway's 429 sends `Retry-After` in seconds, which the
  provider already reads as its fallback; the daily-budget 403 and the gateway's 503 already make
  the chain move on.

Direct mode stays the default, because the gateway exists only during a session. The README says
that a production setup would remove direct access and force traffic through the gateway.

---

## 6. Failure handling

| What happens | What the caller sees | What the app does |
|---|---|---|
| Token missing, wrong audience, no role | 401 from the gateway | provider unavailable, chain moves on |
| Over the minute budget | 429 with `Retry-After` | waits if it fits the 3 s budget, else moves on |
| Over the daily budget | 403 | moves on |
| Primary throttles | nothing: the retry goes to the secondary | — |
| Both backends tripped | 503 | moves on |
| Gateway destroyed | connection failure | moves on (direct mode is the default anyway) |
| Prompt filtered | the model's 400, unchanged | `ContentFilteredException`, as today |

---

## 7. The measured test

### 7.1 The harness

`eval/gateway/`, Python, run as the owner through `AzureCliCredential` with the tenant pinned.
For every request it records: the sequence number, the time sent, status, latency, the region
signal (§7.4), whether the response came from the gateway without a model call (no `x-ms-region`),
and the usage the response reports. Records are JSON Lines. The report replaces every `oid` with
a label (`owner`, `deploy`) and prints no hostname, account name or GUID.

The fixed prompt is a made-up paragraph of about 300 tokens, `max_tokens = 40`, temperature 0.

### 7.2 Test 1: failover

- **Workload.** 45 requests, sent one every 4 seconds over 3 minutes whatever the responses, each
  with a 30-second timeout: about 5,000 tokens a minute, five times what the tiny deployment
  allows, and half the gateway's per-minute budget.
- **Before.** Straight to australiaeast's `releaselens-chat-failover-test`, as the owner. The
  client follows the app's rule (§2): on a 429, one wait if the advised wait fits a 3,000 ms
  budget, then one more send; otherwise the request fails. The harness copies that rule, and a
  test pins it against the app's cases.
- **After.** The same workload, the same deployment name, through the gateway, with the same
  client rule.
- **Order.** Before, then a pause of at least 2 minutes, then after.

**The rule, fixed before any run:**
- **Validity.** If fewer than 14 of the 45 before-run requests fail (under 30%), the test did not
  stress the primary, and the verdict is **inconclusive**, whatever the after run shows.
- **Failover held** if at least 43 of the 45 after-run requests succeed and at least one of them
  was answered by Southeast Asia.
- **Failover did not hold** otherwise, and the report gives each failure's status.
- **Latency.** p50 and p95 of both runs, descriptive only.

### 7.3 Test 2: budgets and access

Run after test 1, because it spends the owner's daily budget. Each check passes or fails.

- **B1.** Requests through the gateway to `releaselens-chat`, sent until one is refused, reach a
  429 with `Retry-After` and no `x-ms-region`, and the Application Insights request record for it
  has no backend call.
- **B2.** Continuing past the minute budget's resets, once 50,000 tokens are spent, the next
  call gets 403.
- **B3.** Within 5 minutes of the owner's 403, a new workflow `gateway-check.yml`, run on the
  owner's yes, makes one call as `id-releaselens-deploy` and gets 200. The workflow:
  - `workflow_dispatch` only, runs on `main` only, environment `azure`
  - reads the gateway's URL and the gateway app's client ID from two environment secrets, set
    for the session and deleted after it, so they are masked in the log
  - prints only the status code
- **B4.** A call with no token gets 401, and a call with the owner's token for
  `https://ai.azure.com` gets 401.
- **B5.** The `releaselens-gateway` metric in Application Insights shows both callers, and its
  total tokens per caller for the session are within 2% of the totals the clients recorded.

**Not tested live:** a valid token for the gateway that lacks `Gateway.Invoke`. There is no second
user to test it with, and none is created. With assignment required (§3.1), such a token cannot
be issued at all. The report says so.

### 7.4 Freeze

The smoke test (§10, step 3) settles which region signal the rule uses: `x-ms-region` if it is
present and names the region, otherwise a label header the outbound policy sets from the backend
that answered. The rule, the workload and that choice are then committed, and the report cites
the commit. Nothing about the rule changes after the first measured request.

### 7.5 Cost

About USD 1 a session, estimated:
- **API Management:** about USD 0.65, for about 3 hours at USD 0.20548 an hour (Retail Prices
  API, 2026-10-06), assuming each started hour is billed
- **Tokens:** under USD 0.10, at gpt-4.1-mini's list price
- **Monitoring ingestion:** a few cents, within the free 5 GB

The existing budget alert covers it.

---

## 8. Security and privacy

- **No keys.** No subscription keys, key authentication off on both accounts, and the gateway
  identity is the only principal on the Southeast Asia account.
- **Least access to the gateway.** Assignment required on the Entra app; one role; three
  assignments.
- **Public endpoint.** The gateway's endpoint is public, protected by the Entra token alone, with
  no IP restriction. The README says so.
- **No prompts logged.** No request or response bodies in the gateway's logs; only token counts
  reach the metric. (Unlike Foundry's server-side tracing in project 3.)
- **No identifiers in public files.** Tenant, client and object IDs, hostnames and account names
  come from git-ignored tfvars or the bootstrap's state. The report uses labels. The existing
  static checks (`tests/infra/test_terraform_static.py`, `test_check_workflows.py`) are extended
  to `infra/gateway` and the new workflow.
- **Data at rest.** The Southeast Asia account stores nothing: no fine-tuning, no stored
  completions, no files.
- **Who applies.** The owner applies both stacks, from WSL. CI gets no new rights; the deploy
  identity gains only the `Gateway.Invoke` assignment.

---

## 9. Testing in CI, with no Azure calls

- **CI's Terraform job** gains `infra/gateway` beside the other three stacks (fmt, validate,
  test, and its lock file in the cache key).
- **`terraform test`, mocked providers**, for `infra/gateway`:
  - `subscription_required = false`; no product or subscription resources
  - the pool's priorities; each breaker's 429 range and `acceptRetryAfter`
  - managed identity on the logger; 0 body bytes in the diagnostic
  - the purge and no-recover provider features; the random suffix in the name
- **`terraform test` for the bootstrap additions**: both new deployments' capacities, the
  Southeast Asia account's key authentication off, the gateway identity's three roles, the app
  role and its three assignments, assignment required.
- **Policy XML checks**, in pytest beside the existing infra tests in `tests/infra`, parsing the
  files:
  - token validation comes first, then the budget, then the metric, then the credential swap
  - the counter key and the `Caller` dimension use `oid`
  - the retry is in the backend section and its condition is the model's 429
  - the on-error body is fixed
  - no literal GUID or hostname
- **The app's gateway-mode test** (§5).
- **Harness tests:** the copied 429 rule against the app's cases; the verdict rule against
  fixtures for each of its outcomes (inconclusive, held, did not hold on count, did not hold on
  region); the label replacement; the report's scan for GUIDs and hostnames.

---

## 10. Delivery

**Plan 1, offline.** Everything in §3 to §9 that needs no Azure call: the bootstrap additions,
`infra/gateway`, the policies, the app setting and test, the harness, the workflow, the tests,
and the README and runbook. It ends in one PR.

**Plan 2, the live session**, a runbook. Each step needs the owner's yes:
1. Apply the bootstrap (the second account, deployments, gateway identity, app registration,
   monitoring, state container). First, read-only: the Southeast Asia Global Standard quota and
   model version for gpt-4.1-mini.
2. Apply the gateway.
3. Smoke test: one call each way; revision 2 at `;rev=2`; the region signal; no hostname in any
   body or header; a filtered prompt's 400 passes through; then make revision 2 current.
4. Commit the rule (§7.4).
5. Run test 1, then test 2 up to B2.
6. Run the workflow (B3), then B4 and B5.
7. Publish the report and the README's results section.
8. Destroy the gateway, and check: the resource group is gone, and no deleted APIM instance
   remains. Delete the two session secrets.

---

## 11. Out of scope

Semantic caching; content-safety policies; products and subscription keys; private networking;
streaming (the app does not stream); embeddings through the gateway; load balancing as opposed to
priority failover; multi-region gateways or Front Door; the preview AI Gateway tier (key-only
callers, no XML policies, not in Australia); deploying the app stack during a gateway session.

---

## 12. Unverified, to settle in plan 2

1. Basic v2 creates in about 5–10 minutes in australiaeast (research §1d).
2. The Southeast Asia Global Standard quota for gpt-4.1-mini `2025-04-14`, and whether quota is
   pooled across regions for this model (research §7). Either way, the tiny primary throttles on
   its own capacity.
3. Capacity 1 is accepted for a Global Standard deployment.
4. `x-ms-region` is present and names the region (§7.4 settles the fallback).
5. Within one request, the retry after the breaker trips goes to the secondary (the official lab
   relies on this).
6. The Retry-After the tiny deployment sends is short. A long one keeps the primary tripped for
   that long, which the report states.
7. Cognitive Services OpenAI User is enough for the gateway identity (research §6 notes the docs
   disagree).
8. The logger ingests with Entra on Basic v2, and Application Insights' "custom metrics with
   dimensions" setting can be set in Terraform (otherwise through azapi or one documented
   portal step).
9. The key name for the Azure CLI in `azuread_application_published_app_ids`.
10. A user's app-role assignment appears in the `roles` claim of the token `az` gets.
11. The daily quota's window starts at 00:00 UTC (research §3), which fixes when B2 can run.
12. `llm-token-limit` sends `Retry-After` in seconds.
13. The purge on destroy succeeds as the owner.

---

## 13. Changes after approval

None yet.
