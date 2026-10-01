# Azure OpenAI without keys — design

**Status: specification, approved by the owner on 2026-09-24. Amended 2026-09-27, with the
owner's approval: the deployment type is Global Standard, not regional Standard, because the
subscription has no regional Standard quota for the model (§4.8). The runtime (plan 1) is
built and merged; nothing is deployed or measured.**
Every figure below is either read from a named source on a stated date, or labelled as an
estimate. Claims that could not be checked are labelled *unverified* and listed in §11.

- Date: 2026-09-24
- Repository: ReleaseLens, `main` at `7416992`
- Sibling that must not break: `releaselens-mcp`, which depends on the `/evidence/*` contract
- Brief: the project brief and the project's rules are private; this document restates
  everything it needs from them

---

## 1. Purpose

Add Azure OpenAI to ReleaseLens as a third chat provider, beside Anthropic and OpenAI, the way
an enterprise would: no keys, deployment names kept separate from model names, 429 handling
that respects the quota, content filtering reported as its own outcome, Terraform, and GitHub
Actions deploying through OIDC.

The provider is the small part. The identity model, the infrastructure and the delivery are
what this is for, and what a reviewer should be able to check.

### The claim under test

> Going keyless on Azure changed how requests are authenticated, not what is sent. Any
> difference in the answers is measured, not assumed.

Three pieces of evidence, each proving something different:

1. **A deterministic test** (§7, T-P7): with the same conversation, the request body sent to
   Azure OpenAI is byte-identical to the one sent to OpenAI, apart from `model`, and the
   headers differ only in `Authorization`. That is the claim at the wire.
2. **The deploy's smoke test** (§6.1): the deployed app, running as its managed identity, gets
   an answer from Azure OpenAI and prices it above $0. That proves the shipped keyless path
   works. It does not measure answer quality.
3. **A measured run** (§8) of the same model through both auth paths. For practical reasons
   the Azure arm authenticates with the owner's own Entra identity from a local run, not the
   managed identity, so it measures Entra against key, not the managed-identity path. A small
   run can show a difference; it cannot show the absence of one. If no difference is found,
   the result is reported as *inconclusive* at the sample size stated per metric, never as
   "the same".

If the run does show a difference, that is the published result.

---

## 2. What exists today

Read from the repository on 2026-09-24.

- **Providers** (`src/ReleaseLens.Llm/Providers/`): `IChatProvider` with `Name` and
  `CompleteAsync`. `AnthropicChatProvider`. `OpenAiChatProvider`, a raw `HttpClient` speaking
  the OpenAI wire format with a configurable `BaseUrl` (which is how Ollama works).
  `FallbackChatProvider`, fixed at `[Anthropic, OpenAI]` in `Program.cs`. `ProviderHttp` maps
  429, 401, 403, 408 and 5xx to `ProviderUnavailableException` (falls through); every other
  non-2xx becomes `InvalidOperationException` (treated as ReleaseLens's own bug).
- **Agent** (`QueryAgent`): passes `Agent:Model` (default `claude-sonnet-5`) as
  `ChatRequest.Model` to whichever provider it calls, and prices the whole query from the last
  response's `model` string through `ModelPricing`, which matches names exactly and prices an
  unknown name at $0.
- **Infrastructure** (`infra/terraform/`): one stack, local state, azurerm `~> 4.14` (locked at
  4.81.0). Resource group, Log Analytics, Key Vault, Postgres Flexible Server (B_Standard_B1ms,
  v16, 32 GB, firewall `0.0.0.0`, which allows Azure services), Container Apps environment and
  app (system-assigned identity, 0–2 replicas, image `:latest`), and a subscription budget
  ($50/month default). Secrets reach the app by value. The local state holds zero resources:
  nothing is deployed.
- **CI** (`.github/workflows/ci.yml`): build and test; on pull requests, Terraform 1.9.8 `fmt`
  and `validate` with no Azure credentials; on `main`, the image is published to GHCR as
  `:latest` and `:sha-<commit>`.
- **Evaluation** (`eval/`): 43 golden queries, posted to one API; the answering provider is
  whatever the chain picks, and the harness records neither the provider nor the model per
  query.

---

## 3. Findings verified before designing

The brief asked for two suspicions to be proven or disproven with a test before anything was
built on them. Both tests were throwaway characterisation tests, run and then deleted; the
permanent versions are in §7.

| # | Finding | Status | Evidence |
|---|---|---|---|
| F1 | A fall-through to OpenAI sends `"model":"claude-sonnet-5"` | **Confirmed by test** | Anthropic stub returns 503; the captured OpenAI body was `{"model":"claude-sonnet-5","max_tokens":2048,...}` |
| F1a | …and real OpenAI then returns 404, which ReleaseLens classes as its own bug, so an Anthropic outage produces HTTP 500 instead of the degraded answer | Confirmed by reading; the 404 itself is *unverified* (needs a paid call) | `ProviderHttp` classes 404 as `InvalidOperationException`, which `FallbackChatProvider` does not catch |
| F2 | A versioned model name in a response prices at $0 | **Code path confirmed by test**; whether real responses carry versioned names is *unverified* | `gpt-4o` → $12.50 for 1M+1M tokens; `gpt-4o-2024-08-06` → $0. Microsoft's documented example responses disagree with each other on the `model` field |
| F3 | On the OpenAI wire path, a filtered completion becomes a silent empty answer and a filtered prompt becomes HTTP 500 | Confirmed by reading | `finish_reason` is never read; a 400 is `InvalidOperationException` |
| F4 | On the OpenAI wire path, cached tokens are billed twice and Anthropic's cache multipliers are applied | Confirmed by reading, **on the premise** that OpenAI's `prompt_tokens` already includes cached tokens, which is from recall and *unverified* against current docs (§11) | `ModelPricing` bills `InputTokens` and `CacheReadInputTokens` separately, and applies 0.1× read / 1.25× write to every model |
| F5 | The chain restarts at the top on every agent iteration, so one answer can mix providers, and the whole query is priced at the last provider's rates | Confirmed by reading | `QueryAgent` overwrites `modelName` each iteration |
| F6 | A failed tool call loses its error flag on the OpenAI wire path | Confirmed by reading | `ToolResult.IsError` is dropped when building the `tool` message |
| F7 | `eval/app/judge.py` says large sweeps use the Batch API; nothing implements it | Confirmed by reading | An aspirational claim in a repository whose rule is that there are none |
| F8 | The judge's price constant is $2.00/MTok input, which its own comment says became $3.00 on 2026-09-01; output tokens are not counted | Confirmed by reading | `eval/app/runner.py` |

All of F1–F8 are fixed in v1 (§9), on every provider they affect, because F1, F2, F3, F4, F5
and F6 would each distort the measurement in §8.

---

## 4. Decisions

Each records what was chosen, why, and what was rejected. Items marked **(brief)** were
decided in the brief and are recorded here, not reopened.

**Departures, all stated where they happen:**

- from the brief's decided items:
  - the app's identity is user-assigned, not system-assigned (§4.10)
  - the default token scope differs from the one the brief fixed (§4.7), confirmed by the
    owner on 2026-09-24
  - Terraform is two stacks, not one, and the Azure OpenAI account and its purge setting
    live in the long-lived one (§4.9), approved by the owner on 2026-09-24
- from the project's rules: the Postgres password is not kept in Key Vault with an expiry
  date, and Postgres is not keyless (§4.14)

### 4.1 Provider shape

**Decision.** Extract the OpenAI wire format — building the request body, parsing the
response, and mapping `finish_reason: content_filter` to the filter outcome — into one shared
codec. `OpenAiChatProvider` and a new `AzureOpenAiChatProvider` both use it.

The Azure provider owns what is Azure-specific:

- its `Name`, `azure-openai`
- always sending its deployment name
- the 429 wait
- mapping a filtered-prompt HTTP 400 body
- recognising `content_filter_results.error`

The Entra token is attached by a `DelegatingHandler` on the Azure provider's `HttpClient`.

**Reasoning.** The brief's framing was that the difference is identity, not payload. Checking
found five differences, and only two are identity:

1. the provider name recorded on spans and usage rows
2. the model field always carries the deployment name
3. no API key, ever
4. a bounded wait on 429 that honours `retry-after-ms`
5. the filtered-prompt 400 body

`finish_reason: content_filter` is part of OpenAI's own wire format, so it belongs in the
shared codec and fixes F3 on the OpenAI path too. Sharing the codec keeps one copy of the wire
code, which is what T-P7 (the byte-identical test) depends on. Keeping the provider separate
stops the OpenAI class accumulating Azure-only flags.

**Rejected — the existing adapter plus a token handler, with Azure differences as options on
the OpenAI class.** Less code moved, but the OpenAI provider would carry the Azure filter body
and retry-after behaviour, and every Azure change would be a change to the OpenAI class.

**Rejected — a fully separate Azure class with its own wire code.** Two copies of the request
builder can drift, and T-P7 would then be testing a coincidence.

**Rejected — the official OpenAI .NET SDK.** Its built-in retry would wait out a long
`Retry-After` up to three times, breaking the wait budget (§4.4) and inflating latency, and its
token-credential path is marked experimental (`OPENAI001`), which `TreatWarningsAsErrors`
turns into a build error.

### 4.2 Model routing (fixes F1)

**Decision.** Each provider sends the model it is configured with. `ChatRequest.Model` is
removed; each provider's options name its model. Azure's options name the deployment, and the
Azure provider sends that name in the `model` field.

**Reasoning.** A provider in a fallback chain has to answer with its own model, or the
fallback cannot work (F1a).

### 4.3 Provider order, pinning and per-query state (fixes F5)

**Decision.**

- **The order is configuration:** `Chat:Providers`, an ordered list of provider names.
  - The deployed app uses `["azure-openai"]` only; Anthropic and OpenAI keys never reach the
    cloud (§4.11).
  - Local runs may use any order.
  - Each evaluation arm is a process configured with exactly one provider (§8).
- **Stickiness.** When a provider answers an iteration, the next iteration of the same query
  starts with that provider. If it then fails, the chain continues from the next provider
  after it and never returns to earlier ones. A query therefore mixes providers only when one
  fails mid-query.
- **Metadata.** `metadata.provider` keeps its meaning: the provider that answered the final
  iteration. A new additive field, `metadata.providers`, lists the **distinct** providers that
  answered, in the order they first answered, so a mixed answer is visible.
- **Per-query state** lives in a query-scoped context object, created by `QueryAgent` for each
  query and passed with every `CompleteAsync` call. It holds the provider that last answered
  and the remaining 429 wait budget (§4.4). The providers and `FallbackChatProvider` stay
  stateless singletons, so concurrent queries cannot share a budget or a sticky provider.

**Reasoning.** The brief's trap: a broken identity returns 401/403, which falls through to
Anthropic, which answers, so nothing looks wrong. With Azure alone in the cloud, a broken
identity produces the degraded answer instead, and the smoke test (§6.1) fails on it.

**Rejected — Azure first, Anthropic second in the cloud.** It keeps resilience, but brings
back the trap and needs third-party keys in Azure.

### 4.4 The 429 wait budget

**Decision.** On a 429 from Azure:

1. Read `retry-after-ms` (milliseconds). If it is absent, read `retry-after`, taken only as a
   whole number of seconds; any other form is ignored. When both headers are present,
   `retry-after-ms` wins.
2. If the advised wait fits within what remains of the query's budget, wait that long
   (through `TimeProvider`), subtract it from the budget, and retry the same provider **once**.
3. Otherwise — no usable header, or a wait longer than what remains — fall through
   immediately, as today.

The budget is **3 seconds per query**, held in the query context (§4.3), configurable, and
documented in the README.

There is no second retry layer: no resilience handler on this `HttpClient`, and the managed
identity credential's own retry is capped (§4.7), because either would compound the wait.

**Reasoning.** Microsoft documents `retry-after-ms` on 429 responses; `retry-after` in seconds
is not in its header table, so it is read only as a fallback. A per-query budget bounds the
latency a user can see, which a per-call budget would not, since an agent query makes several
calls. Starting a wait that cannot finish within the budget buys nothing.

In the cloud, where Azure is the only provider, "fall through" means the degraded answer.

### 4.5 Content filtering (fixes F3)

**(brief)** A filtered prompt or completion is its own outcome, neither ReleaseLens's bug nor
"unavailable", and **never falls through to another provider**, because that would route
around the filter.

**Decision.**

- **The outcome.** A new `ContentFilteredException(provider, stage, usage)`, where `stage` is
  `prompt` or `completion`.
- **Filtered completion,** on every OpenAI-wire provider (in the shared codec): HTTP 200 with
  `finish_reason == "content_filter"`. The exception carries the response's parsed usage.
- **Filtered prompt,** Azure provider: HTTP 400 whose body has `code == "content_filter"`
  either inside an `error` envelope (`error.code`) or at the top level (`code`).
  - The v1 wire shape is *unverified*. The only documented example is the legacy shape, with
    an `error` envelope, while the v1 OpenAPI error schema has no envelope. So both are
    accepted until one real response settles it (§11).
  - Everything under `innererror` or `inner_error` is diagnostics only; Microsoft's own
    examples disagree on its shape.
- **What OpenAI-direct sends for a filtered prompt is *unverified*** (§11). Until it is known,
  an OpenAI 400 stays ReleaseLens's own bug.
- **Not filtered:** a 200 whose `content_filter_results` contains an `error` object means the
  filter did not run. That is recorded, not treated as filtered.
- **No fall-through.** `FallbackChatProvider` does not catch the exception.
- **What `/query` returns.** `QueryAgent` catches the exception and returns HTTP 200 with a
  fixed answer and an additive `metadata.filtered = { stage, provider }`.
  - Like the degraded path, the filtered answer reports the usage and cost of every iteration
    that ran, including the filtered completion's own usage; a prompt-stage 400 adds none.
  - That usage counts against the tenant's daily token budget.
  - Whether Azure bills a filtered call, and for which tokens, is *unverified*. ReleaseLens
    prices whatever usage the response reports.
- **The `/evidence/*` responses do not change.**

### 4.6 Pricing (fixes F2, F4, F5)

**Decision.**

- **Pricing identity.** Each provider has one: provider, model, version and deployment type.
  For Azure: `azure-openai`, `gpt-4.1-mini`, `2025-04-14`, `GlobalStandard` (amended
  2026-09-27; see §4.8).
- **Per iteration.** Cost is computed per iteration from the answering provider's pricing
  identity. The response's `model` string is recorded, never priced.
- **No silent $0.** A provider whose pricing identity has no rate fails at startup.
  `ModelPricing`'s $0 for unknown names stays only for providers explicitly marked unpriced
  (local runtimes such as Ollama).
- **Cached tokens.** On the OpenAI wire, cached tokens are treated as a subset of input tokens
  and priced at the model's own cached rate, with no cache-write charge. The subset premise is
  *unverified* (§11). If it proves wrong, only this line changes.

**Rates**, from the Azure Retail Prices API for `australiaeast`, read 2026-09-24, USD per 1M
tokens. The Global Standard row was read again on 2026-09-27 and had not changed; it is the
rate the app uses. `ModelPricing` keeps the regional row too, so regional quota would need no
code change.

| Model | Deployment type | Input | Cached input | Output |
|---|---|---:|---:|---:|
| gpt-4.1-mini 2025-04-14 | Global Standard (used) | 0.40 | 0.10 | 1.60 |
| gpt-4.1-mini 2025-04-14 | Standard (regional) | 0.44 | 0.11 | 1.76 |

OpenAI's pricing page could not be read in this session, so OpenAI's own rate for
`gpt-4.1-mini` is *unverified*. It is read and dated before it goes into `ModelPricing`.

### 4.7 Identity and credentials in code

**Decision.**

- **The token handler.** A `DelegatingHandler` gets a token from a `TokenCredential` and
  attaches it as `Authorization: Bearer`. The token cache lives in a singleton, not in the
  handler, because `IHttpClientFactory` recycles handlers. The token is refreshed before it
  expires.
- **Which credential,** chosen by configuration: `AzureOpenAi:Credential` is `ManagedIdentity`
  or `AzureCli`, with `AzureOpenAi:TenantId` for the second.
  - **`ManagedIdentity`** (in Azure) builds `ManagedIdentityCredential` through its
    optional-argument constructor with `ManagedIdentityId.FromUserAssignedClientId(...)`. The
    client ID comes from the container's `AZURE_CLIENT_ID`. Startup fails if that is empty.
    The legacy `(string clientId)` overload is `[Obsolete]`, which `TreatWarningsAsErrors`
    turns into a build error. The credential's own retry is capped at one retry with a short
    delay (`ManagedIdentityCredentialOptions.Retry`), because by default it retries up to five
    times with exponential backoff, a hidden wait the 429 budget knows nothing about.
  - **`AzureCli`** (locally) uses `AzureCliCredential` with the tenant pinned.
  - **`DefaultAzureCredential` is not used anywhere.** It is meant for development, and its
    chain can pick up the wrong account on a machine signed in to more than one tenant.
- **Failures.** A credential exception (for example `CredentialUnavailableException`) becomes
  `ProviderUnavailableException`, so it produces the degraded answer, not HTTP 500.
- **Configurable, not hard-coded:** the base URL and the token scope, because an API
  Management gateway in front of Azure OpenAI, planned as later work, will change both.
- **The token scope defaults to `https://ai.azure.com/.default`.** This departs from the
  brief, which fixed `https://cognitiveservices.azure.com/.default`.
  - Current v1 documentation uses `https://ai.azure.com/.default`; the OpenAPI spec lists the
    other.
  - The brief itself says to check its Azure details against current documentation, and to
    keep the scope configurable.
  - Which scope the account accepts is proved with one real token (§11). The owner confirmed
    this default on 2026-09-24, with the spec's approval.
- **Packages.** `Azure.Identity` is added. Its dependency `Azure.Core` requires
  `Microsoft.Extensions.*.Abstractions` 10.0.10 or later, so the central pins in
  `Directory.Packages.props` move up from 10.0.0 in the same change. Versions are chosen at
  implementation time, with NuGet audit clean.

**Reasoning.** `AzureCliCredential` does not cache tokens. Without our own cache, each agent
iteration would start an `az` process, and the Azure arm's latency in §8 would include it.

### 4.8 Model, deployment type and capacity

**Decision (amended 2026-09-27).** `gpt-4.1-mini`, version `2025-04-14`, deployment type
**Global Standard** (`GlobalStandard`), account in `australiaeast`,
`version_upgrade_option = "NoAutoUpgrade"`.

**Why it changed.** The original decision was regional Standard. A read-only check on
2026-09-27 (`az cognitiveservices usage list`) found the subscription's quota for
`OpenAI.Standard.gpt4.1-mini` is 0 in `australiaeast`, and also in eastus2, swedencentral,
japaneast and westus3. Every regional Standard chat model is at 0 there; only embedding models
have regional quota. `OpenAI.GlobalStandard.gpt4.1-mini` has 5000. The owner chose Global
Standard over asking Microsoft for regional quota.

**Capacity: 100, which is 100,000 tokens per minute. This is an estimate, not a
measurement.** Its basis:

- In the 12 August run the most expensive query (gq-022) cost $0.129 in about 23 seconds at
  $2/$10 per MTok. That implies a few tens of thousands of input tokens within a minute; this
  is an inference from the cost, not a measured token count.
- Azure also counts each request's `max_tokens` (2048 here) against the per-minute quota.
- Terraform's default of 1 (1,000 TPM) is smaller than a single request's `max_tokens`, so it
  would throttle every call.
- The dry run in §8 measures per-query token counts. The value is revised from those, and the
  quota must cover it (§11).

**What capacity does and does not bound.** It caps how fast spend can grow, not how much. The
worst case at 100,000 TPM sustained around the clock is about 144 million tokens a day,
roughly $58 a day at the Global Standard input rate of $0.40 per 1M tokens (arithmetic, not a
measurement; $63 at the regional rate).

- Every `/query` caller is limited by its tenant's daily token budget.
- Code running as the app identity is not: it can call the endpoint directly (§4.10).

**Reasoning.**

- **Where processing happens.** Microsoft's deployment-types page (dated 2026-08-06, read
  2026-09-27) says data stored at rest remains in the designated Azure geography, here
  Australia, but for Global types inference "may be processed in any Azure region". The README
  says exactly that and no more (§10). For this project the prompts carry public GitHub data
  (commits, issues, pull requests and releases of a public repository), so where inference runs
  matters less than it would for private data.
- **Quota.** Global Standard is where the subscription has quota (see above), and Microsoft
  recommends it as the default: it gets new models first and has the lowest price.
- **Why this model, still.** Global Standard offers newer models, but keeping
  `gpt-4.1-mini` 2025-04-14 means no payload change and the same model OpenAI sells directly,
  which the comparison in §8 needs. `az cognitiveservices model list` (2026-09-27) lists it for
  `australiaeast` with a `GlobalStandard` SKU, on both `OpenAI` and `AIServices` account kinds.
- **No payload changes.** It is not a reasoning model, so the existing payload (`max_tokens`,
  tools) works unchanged.
- **The comparison.** The same model is available directly from OpenAI, which is what the
  comparison in §8 needs.
- **The pin.** Terraform's default for `version_upgrade_option` auto-upgrades the model, so the
  pin is set explicitly.

**Known limit, stated in the README.** Microsoft's retirement schedule (read 2026-09-24) lists
`gpt-4.1-mini` 2025-04-14 as **Legacy, retiring 2027-04-14**, with no named replacement.

- With `NoAutoUpgrade`, the deployment stops working on that date.
- Global Standard already offers successors (for example `gpt-5.4-mini`, retiring
  2027-09-21), but the GPT-5 family needs payload changes (`max_completion_tokens`, no
  `temperature`).
- Review in February 2027.
- The deployment lives in the long-lived stack (§4.9), so it is created once, not every
  session.

**Rejected — regional Standard (the original decision).** It keeps inference in the Australia
geography, but the subscription's quota for it is 0 (2026-09-27). Asking Microsoft for quota
was possible but slow and uncertain for a personal subscription. The regional rate stays in
`ModelPricing`, so switching back is a configuration change.

**Rejected — Data Zone Standard.** An APAC data zone now exists, but it spans several
countries, so it does not keep data in Australia.

**Rejected — Provisioned.** It bills by the hour, and the project's rules exclude it.

### 4.9 Infrastructure: two stacks

**Decision.** A change to the brief's single-stack picture, approved by the owner on
2026-09-24: Terraform is two stacks.

**Long-lived stack, `infra/bootstrap`.** Applied by the owner, locally, with the owner's own
sign-in, and never destroyed. It holds:

- **Resource group `rg-releaselens-bootstrap`**, with a `CanNotDelete` lock applied by the
  owner. The lock has to be lifted to delete anything inside, and it does not cover the
  subscription-scope budget. It contains:
  - a storage account for Terraform state (§4.13), with two containers: `tfstate-bootstrap`
    (the owner only) and `tfstate-app`
  - the **deploy identity**: a user-assigned managed identity with **one** federated
    credential, for the GitHub environment `azure` (§4.12). No Entra app registration exists.
  - the **app identity**: a user-assigned managed identity used by the Container App
  - the **Azure OpenAI account**:
    - kind `AIServices`, with `project_management_enabled = false`
    - `local_auth_enabled = false`
    - `custom_subdomain_name` generated once
    - one deployment (§4.8)
- **The app resource group `rg-releaselens`**, created here and left empty. The app stack
  deploys into it. An empty resource group costs nothing.
- **The subscription budget and its action group.** The first bootstrap apply is targeted at
  them, so the budget exists before anything that can bill.
- **Every role assignment** (§4.10).
- **Resource-provider registration**, listed on this stack's provider
  (`resource_providers_to_register`): the namespaces both stacks use, for example
  `Microsoft.Storage`, `Microsoft.ManagedIdentity`, `Microsoft.CognitiveServices`,
  `Microsoft.App` and `Microsoft.DBforPostgreSQL`. The owner's rights cover this; CI's do not.
- **(brief)** The azurerm feature `cognitive_account { purge_soft_delete_on_destroy = true }`
  is set on this stack's provider, the stack that holds the account.
- **Outputs** that the app stack needs (see the handoff below).

**App stack, `infra/terraform`.** Deployed and destroyed by GitHub Actions:

- the Container Apps environment and app, running as the app identity, with environment
  variables for the Azure OpenAI base URL, the deployment name, and `AZURE_CLIENT_ID` set to
  the app identity's client ID
- Postgres Flexible Server
- no resource group, role assignments, Key Vault, budget or Log Analytics workspace

**The handoff from bootstrap to the app stack.** The app stack reads nothing from bootstrap:
no `terraform_remote_state`, and no data sources for bootstrap resources. CI has no rights to
read either.

- Four bootstrap outputs are copied once by the owner into the GitHub environment, with names
  that cannot be mistaken for the deploy identity's `AZURE_CLIENT_ID`. Three are variables;
  `AZURE_OPENAI_BASE_URL` is a secret (amended 2026-10-01, §4.12):
  - `APP_IDENTITY_ID`, the app identity's resource ID
  - `APP_IDENTITY_CLIENT_ID`
  - `AZURE_OPENAI_BASE_URL`, the v1 base URL
  - `AZURE_OPENAI_DEPLOYMENT`
- The workflows pass them to Terraform as `TF_VAR_*`.
- The container's `AZURE_CLIENT_ID` is always set from `APP_IDENTITY_CLIENT_ID`.

**How the brief's "destroy and re-apply, soft-delete handled" is met.** The stack that is
destroyed and re-applied every session is the app stack, and it holds no soft-deletable
resource. The purge setting matters only if the bootstrap stack is ever deliberately torn
down. The check that matters now is that `rg-releaselens` is empty after a destroy (§4.12).

**Reasoning.**

- **The account is persistent.** A Global Standard deployment is billed per token (§5); only Postgres
  bills by the hour. Destroying the account every session would have required:
  - a subscription-scope purge right for CI
  - a sweep for soft-deleted accounts
  - a new endpoint on every deploy
  - recreating a Legacy model every session
  - a full, billing deploy just to run locally
- **Kind `AIServices`, not `OpenAI`.** Microsoft automatically upgrades eligible long-lived
  kind-`OpenAI` accounts to Foundry (`AIServices`). The opt-out cannot be set through
  azurerm, so a later bootstrap plan would try to roll the kind back. `AIServices` avoids that
  and keeps the `https://<subdomain>.openai.azure.com/openai/v1/` endpoint. That the
  `Cognitive Services OpenAI User` role grants inference on an `AIServices` account is
  *unproven* until the first real call (§11).
- **The budget moves here because today `terraform destroy` deletes it every session.** In the
  same state as everything else, the subscription has no budget between sessions, which
  contradicts the "budget first" rule.
- **Log Analytics goes, for two reasons:**
  - The Container Apps environment authenticates to a Log Analytics workspace with the
    workspace's shared key, which would put an Azure key in the design.
  - Creating a workspace also lists deleted workspaces at subscription scope, which CI has no
    rights to do.

  Logs stream instead (`az containerapp logs show`).
- **Both identities live in the bootstrap resource group, and must never move into
  `rg-releaselens`.** Contributor there would include writing federated credentials, which
  would let CI add a trust for itself outside the environment gate.

### 4.10 Roles, and what CI can do

**Decision.** All role assignments are made by the bootstrap stack. Role definitions are looked
up by name in Terraform, never by a hard-coded GUID.

| Identity | Role | Scope | Why |
|---|---|---|---|
| App identity | Cognitive Services OpenAI User | the Azure OpenAI account | inference, plus the account's assistants, responses and file-read data plane, which this role also grants |
| Owner's user | Cognitive Services OpenAI User | the Azure OpenAI account | local runs through `az login` |
| Owner's user | Storage Blob Data Contributor | both state containers | the Owner role has no data actions, so without this the owner cannot migrate state or run the app stack locally |
| Deploy identity | Contributor | `rg-releaselens` only | create and destroy the app stack |
| Deploy identity | Managed Identity Operator | the app identity only | attach an identity that lives in another resource group to the Container App (`userAssignedIdentities/assign/action`) |
| Deploy identity | Storage Blob Data Contributor | `tfstate-app` only | read and write the app stack's state, including its lock |

**CI has no subscription-scope rights, cannot write role assignments, and cannot read the
bootstrap state. It has no role on the Azure OpenAI account, so it cannot turn key
authentication back on; only the owner can.**

**What CI can do, stated plainly in the README:**

- **It can run code as the app identity,** because it holds Contributor on `rg-releaselens` and
  Managed Identity Operator on the app identity. That identity can call the model and the
  account's data plane directly, outside `/query`, so no tenant token budget limits it.
  Capacity (§4.8) caps the rate of that spend but not its total. The subscription budget only
  alerts, about once a day, on cost data up to a day old, and stops nothing.
- **It can read the Postgres password,** which is in the app stack's state.
- **It can create any billable resource in `rg-releaselens`.** Deploy authority, which is the
  right to push to `main`, is therefore also spending authority. Resources created outside
  Terraform are not in its state and survive `terraform destroy`, which is why the destroy
  workflow checks that the group is empty (§4.12).
- **It can rewrite the app stack's state.** The owner therefore runs the app stack locally only
  through an interactive plan that is reviewed, never `-auto-approve`. Any planned deletion of
  something other than the Container Apps resources or Postgres is treated as tampering, and
  recovered by restoring a blob version (§4.13).

**A departure from the brief (decided item 2).** The brief gives the role to the Container
App's *system-assigned* identity. This design uses a *user-assigned* identity, with the role
still on the account.

- A system-assigned identity does not exist until CI creates the app, so CI would need rights
  to write role assignments.
- Every deploy would then wait up to about ten minutes for the new assignment to propagate,
  with calls failing meanwhile.
- A user-assigned identity is created and granted once, so CI never writes roles and deploys
  never wait.

**Rejected — Owner, or unconditioned User Access Administrator.** Either can grant itself
anything.

**Rejected — Contributor plus Role Based Access Control Administrator, conditioned to the
stack's roles.** It is documented and sound (write constrained with `@Request`, delete with
`@Resource`), but it is machinery for safely granting CI a power this design does not need.

### 4.11 Third-party keys

**Decision.** The Anthropic and OpenAI keys never reach Azure or GitHub. They exist only in the
owner's local `.env` (git-ignored), for local runs and the evaluation. The deployed app uses
Azure OpenAI only (§4.3).

**Reasoning.**

- It removes a Key Vault, its references, hand-set secrets with expiry dates, and the failure
  mode where a reference to a secret that was never set breaks provisioning.
- It narrows what CI can reach by running as the app identity to one account.

**Rejected — a long-lived Key Vault in bootstrap, read by the Container App through a Key Vault
reference.** It is sound, and it keeps the value out of state, but it exists only to support a
cloud fallback this design deliberately does not have.

**Rejected — a GitHub secret.** GitHub holds no credentials (§4.12).

### 4.12 CI and delivery

**Decision.**

**GitHub environment `azure`:**

- Deployment branches: "Selected branches and tags", with one branch rule, `main`, and no tag
  rules.
- No required reviewers. On a repository with one maintainer, a reviewer would only be the
  owner approving their own deploy.
- Administrator bypass is off.
- Its **environment** variables are:
  - `AZURE_CLIENT_ID` (the deploy identity)
  - three of the four app handoff values (§4.9): `APP_IDENTITY_ID`, `APP_IDENTITY_CLIENT_ID` and
    `AZURE_OPENAI_DEPLOYMENT`
- Its **environment** secrets are `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` (amended
  2026-09-30, the owner's decision), and `TFSTATE_STORAGE_ACCOUNT` and `AZURE_OPENAI_BASE_URL`
  (amended 2026-10-01, the owner's decision). They are identifiers, not credentials. They are
  secrets only because GitHub masks secret values in run logs, and prints variable values in each
  step's header before any masking step can run. The repository is public, and so are its run
  logs.

  The second pair was added because, as checked on 2026-09-30, a request with an invalid bearer
  token to the state storage account's blob endpoint returns a 401 whose `WWW-Authenticate`
  header names the tenant ID. The storage account shares its random suffix with the Azure OpenAI
  account (`strlstate<suffix>`, `aoai-releaselens-<suffix>`), and both patterns are in the public
  code, so the public base URL gave the storage name away. The Azure OpenAI endpoint's own 401
  names no tenant, in its headers or its body. Other routes to the tenant ID were not ruled out.

  Only jobs that pass the environment's gate can read any of them.

  Amended 2026-10-01: GitHub masks a secret only where its whole value appears. Terraform
  truncates long resource IDs in its progress lines, and whole-value masking misses a truncated
  ID. So the workflows pass Terraform's apply and destroy output through a filter that hides the
  subscription ID.
- **GitHub holds no credentials:** no key, password or token. Everything it holds is an
  identifier.

**The environment gate is the only branch binding, so two rules always hold:**

- **No workflow triggered by `pull_request_target`, `workflow_run` or `issue_comment`
  references `environment: azure`.** Those runs carry the default branch's ref and would pass
  the `main` rule.
- **The repository stays public.** On GitHub Free, a private repository's environment
  protection rules are ignored, so the federated credential is deleted before any change of
  visibility.

**The federated credential** has one subject, for that environment.

- This repository uses GitHub's immutable subject format. Its prefix, read from
  `GET /repos/DmitryLyubaev/releaselens/actions/oidc/customization/sub` on 2026-09-24, is
  `repo:DmitryLyubaev@57339946/releaselens@1331560542`.
- The full environment subject is read from a probe job before the credential is written
  (§6). The probe prints only the decoded `sub`, never the token. A wrong subject is accepted
  silently and fails only at login.
- **No branch-type credential exists,** so a job without `environment: azure` cannot get an
  Azure token from any branch.

**`ci.yml`:**

- Its job set is unchanged, and pull requests never get Azure tokens (given the rules above).
- Its stale comment that federated credentials are "more surface area than this project needs"
  is updated.
- The actions in `publish-image` are pinned to commit SHAs, because that job builds the image
  that runs as the app identity.
- The Terraform version is pinned to match the other workflows, and `fmt` and `validate` also
  cover `infra/bootstrap`.
- A static check fails the build if any workflow other than `deploy.yml` and `destroy.yml`
  references `environment: azure`, or if those two lack their required `permissions` and
  `concurrency` settings.

**`deploy.yml`:**

- `workflow_dispatch` only.
- A **preflight job**, with only `actions: read`:
  - It refuses to continue if `destroy.yml` has been disabled.
  - It resolves the image tag `sha-${{ github.sha }}` to its digest with an anonymous registry
    call, and fails with a clear message if the tag does not exist yet, since `publish-image`
    may not have finished.
- The **deploy job** uses `environment: azure` and runs `terraform apply` with the image pinned
  to `image@sha256:<digest>`, so the image that was checked is the image that runs. With
  `:latest`, a re-apply rolls out nothing.
- Then the smoke test (§6.1).

**`destroy.yml`:**

- `workflow_dispatch`, plus a nightly `schedule` at 14:00 UTC (midnight AEST). No confirmation
  prompt: destroy is the direction that stops billing.
- The job uses `environment: azure` for both the dispatched and the scheduled runs, since only
  an environment-subject credential exists.
- It supplies every required variable from the same environment variables and secrets, and the
  image reference has a placeholder default valid only for destroy.
- After `terraform destroy`, it lists the resources in `rg-releaselens` and fails if any
  remain.
- Amended 2026-10-01: `terraform destroy` is retried only on azurerm issue
  [#33433](https://github.com/hashicorp/terraform-provider-azurerm/issues/33433). Deleting a
  Container App or its environment succeeds in Azure, but Terraform stops on a polling error, and
  the next run drops the deleted resource from state. So there are at most three attempts: one for
  each of those two resources, then a clean pass. Any other failure fails the step at once.

**Both workflows:**

- Top-level `permissions: {}`, and `id-token: write, contents: read` only on the job that needs
  Azure.
- Actions pinned to commit SHAs.
- One shared concurrency group, `releaselens-azure`, with `queue: max` and
  `cancel-in-progress: false`, so a pending nightly destroy is not cancelled by a later run.
  Ordering is best effort.
- `terraform -lock-timeout`, because the owner's local runs do not go through GitHub's
  concurrency.
- Terraform authenticates through `ARM_USE_OIDC` and the `ARM_*` variables, with no
  `azure/login` step.

**`main`** gets a ruleset blocking force-push and deletion, because deploy authority equals the
right to push to `main`.

**Reasoning.**

- **Deploy is manual,** because applying on every push would create a billing Postgres server
  every time.
- **What makes the environment "protected" is the branch rule,** not a reviewer.
- **The nightly schedule is a best-effort safety net, not a guarantee.** GitHub disables
  scheduled workflows in public repositories after 60 days without activity, and scheduled
  runs can be delayed or dropped. The long-lived budget alert is the backstop; it notifies and
  stops nothing.

**Rejected — `terraform plan` on pull requests with Azure credentials.** It would give
pull-request code an Azure token.

**Rejected — every ID as a GitHub secret, which Microsoft's examples show.** The client IDs,
the app identity's resource ID and the deployment name are identifiers that reveal nothing about
the owner, so they stay variables and appear in public run logs; the README says so. The tenant
and subscription IDs are the exception above (amended 2026-09-30), and so are the storage account
name and the base URL (amended 2026-10-01: the original decision said they reveal nothing about
the owner, which the 401 above disproves). The original decision left all four visible, and
`::add-mask::` cannot hide a variable that a step's header has already printed.

### 4.13 Terraform state and versions

**Decision.**

- **The backend** is azurerm with `use_azuread_auth = true`: `use_oidc = true` in CI, and the
  Azure CLI locally.
- **The state storage account:**
  - `shared_access_key_enabled = false`, with OAuth as the default
  - `local_user_enabled = false`, because azurerm 5.x still defaults it to true
  - `allow_nested_items_to_be_public = false`, written out even though 5.x defaults it to
    false (4.x defaulted it to true), so the setting does not depend on the provider default
  - minimum TLS 1.2
  - blob versioning, plus blob and container soft delete
  - a lifecycle rule that removes old versions after about 90 days, because each version is a
    full state holding that session's Postgres password
  - `prevent_destroy`
- **The bootstrap state** starts local and is migrated into `tfstate-bootstrap` after the full
  bootstrap apply, once the owner's data-plane role has propagated. The local copies are then
  deleted.
- **azurerm moves to `~> 5.6`** in both stacks.
  - 4.x has had no release since 4.81.0 on 2026-07-14; 5.6.0 (2026-09-17) is current.
  - The state is empty, so there is nothing to upgrade.
  - `resource_provider_registrations` defaults to `none` in 5.x, which suits an identity that
    cannot register providers.
- **Terraform is pinned to one version** (1.15.x) in `required_version` and in every workflow.
  - CI's 1.9.8 needs `resource_group_name` in the backend, has no `use_cli`, and made a
    management-plane lookup that later versions dropped.
  - The Entra-only setup here needs 1.11.2 or later.
  - 1.15.x matches the owner's local Terraform.
- **`.gitignore`** widens from `infra/terraform/` to `infra/**` (`.terraform/`, `*.tfstate*`,
  `*.tfvars` except `*.tfvars.example`, and `tfplan`) **before** the first bootstrap command.
  Bootstrap's local state holds the storage account's keys, and this repository is public.

### 4.14 Postgres (deferred)

**Decision.** Postgres keeps its generated password and the existing firewall rule. Both are
stated in the README's "What this is not". Microsoft Entra authentication for Postgres, and
private networking, are later work (§9).

**The exposure, stated plainly.**

- The "allow Azure services" rule admits any Azure-hosted client in any tenant, GitHub-hosted
  runners included, not only this subscription.
- The server-administrator password is the only control.
- The password lives in the app stack's state, which only the owner and the deploy identity
  can read, and in the Container App's secret.
- It is generated when the database is created and deleted with it, so it lives until the
  next destroy, normally one session (corrected 2026-09-27: a redeploy without a destroy keeps
  it, and the nightly destroy is best effort). Earlier versions of the app state still hold it
  for up to 90 days after it was written, but it opens nothing once the server is gone.

**Departure from the project's rules.** Two rules are not met, and this is stated rather than
papered over:

- **"A secret that can't be avoided is kept in Key Vault with an expiry date."** The password
  is generated by Terraform, so a Key Vault would only hold a copy of a value that is already
  in state. Its lifetime, until the next destroy, stands in for the expiry date.
- **"Keyless wherever Azure allows it."** Azure allows Entra authentication for Postgres, which
  is deferred.

**Consequence for the claim.** The README says key authentication is off on the Azure OpenAI
account and no key is used. It does not say "no secret exists".

---

## 5. Architecture

```
GitHub Actions (main only, environment "azure")
  │ OIDC: federated credential on the deploy identity (no app registration, no client secret)
  ▼
┌──────────── rg-releaselens-bootstrap (long-lived, applied by the owner, CanNotDelete) ─┐
│  state storage (shared keys off)    tfstate-bootstrap · tfstate-app                     │
│  deploy identity ── federated credential (environment: azure)                           │
│  app identity                                                                           │
│  Azure OpenAI account (key auth off) ── deployment gpt-4.1-mini 2025-04-14, GlobalStd   │
│  budget + action group            all role assignments                                  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────── rg-releaselens (persistent group; contents deployed and destroyed by CI) ────┐
│  Container Apps environment + app  ── runs as the app identity ──► Azure OpenAI         │
│  Postgres Flexible Server (password; firewall: allow Azure services)                    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
Local runs: the API on the owner's machine, Postgres in Docker with the restored corpus,
Azure OpenAI through the owner's own `az login`; Anthropic and OpenAI keys in `.env`.
```

### What bills

| Resource | Stack | Bills |
|---|---|---|
| Postgres Flexible Server B1ms | app | by the hour while it exists |
| Container Apps (consumption, scales to zero) | app | per use |
| Azure OpenAI Global Standard deployment | bootstrap | per token. The Retail Prices API lists only per-token meters for it (read 2026-09-24 and 2026-09-27); that nothing is charged while idle is to be confirmed on the first invoice |
| State storage account | bootstrap | a few cents a month (estimate) |
| Managed identities, resource groups, budget | bootstrap | nothing |

Whether any Defender for Cloud plan is enabled on the subscription, which would charge per
storage account, is checked once and recorded (§11).

---

## 6. First-time setup

The order is part of the design; two orderings would be unsafe. **Every push or merge to
ReleaseLens needs the owner's approval first**, after checking
`git log --format='%an <%ae>' origin/main..HEAD`.

1. `az account show` must show the personal account and tenant. Stop otherwise.
2. **Prerequisite:** the Azure CLI can reach Microsoft Entra, which
   `az account get-access-token` checks. Everything local depends on it: bootstrap,
   Terraform's CLI authentication, `AzureCliCredential`, and the evaluation.
3. **Clean up the local secrets.** Delete `infra/terraform/terraform.tfstate.backup`, `tfplan`,
   and the `anthropic_api_key` line in `terraform.tfvars`. They hold an Anthropic key, possibly
   the current one, and the backup also holds a Log Analytics shared key. Rotate the Anthropic
   key, and keep the new one only in `.env`.
4. Commit the widened `.gitignore` (§4.13).
5. **Ask first.** Bootstrap apply, targeted at the budget and action group only.
6. **Create the GitHub environment `azure`,** with its branch rule and administrator bypass off,
   and no variables yet. This must happen **before any workflow that mentions it reaches any
   branch**: a run that references an environment that does not exist creates it with no
   protection rules, and a token for it would then satisfy the federated credential from any
   branch.
7. **Ask first (a push).** Merge the probe workflow to `main`, run it, and record the decoded
   `sub`.
8. **Ask first (it creates the federated credential).** The full bootstrap apply, with the
   credential's subject set to exactly the recorded `sub`.
9. Wait for the owner's blob role to propagate (up to about ten minutes), migrate the bootstrap
   state into `tfstate-bootstrap`, and delete the local state files.
10. Copy the bootstrap outputs into the environment's variables and secrets (§4.12), and delete
    the probe workflow.
11. **Ask first (a push).** Merge `deploy.yml` and `destroy.yml` to `main`. Scheduled and
    dispatched workflows only run from files on the default branch.
12. **Ask first.** The first deploy.

### 6.1 The smoke test

The deployed database has no schema, not just no tenant: the API does not run migrations, and
the Worker is not in the image. The smoke test provisions what it needs, then checks the answer.

1. **Read and mask before anything prints.** Read the connection string from a sensitive
   Terraform output into a variable and register it with `::add-mask::` before any other
   command runs.
2. **Provision with the existing Worker CLI** (`migrate`, `create-tenant`, `issue-key`),
   published in the job and run against the deployed Postgres.
   - Its output is captured into a variable, never echoed, and the issued key is masked
     before anything else runs.
   - The smoke tenant gets a small daily token budget.
3. **Call `/query`** over the app's public ingress with that key.
4. **Pass only if all of these hold:**
   - the response is HTTP 200
   - `metadata.provider == "azure-openai"`
   - `metadata.providers == ["azure-openai"]`
   - `metadata.degraded == false`
   - `metadata.costUsd > 0`, and it equals, to six decimal places, the cost recomputed from the
     response's own token counts at the rate the app is configured with. The check reads that
     rate from the app's configuration rather than a second copy of it.

   A cold start is absorbed by retrying for a bounded few minutes first.

Everything created dies with the database on destroy. The recorded `costUsd` is the evidence
that a real Azure call is priced above $0 at the configured rate.

*Unverified:* whether GitHub-hosted runners pass the "allow Azure services" firewall rule. If
they don't, a Terraform-managed firewall rule for the runner's IP is added by a variable for
the smoke step and removed in an `always()` step.

**Rejected for v1 — a Container Apps Job running the same checks inside Azure.** It keeps the
key off the runner entirely, but it needs the Worker in the image, a job resource, and a
key-revoke command that does not exist yet. It is listed as later work.

---

## 7. Test list

All unit tests use the existing house style: `StubHttpMessageHandler`, xUnit v3, and
`FakeTimeProvider` for waits, so nothing sleeps.

**Azure provider and identity**

| ID | Test |
|---|---|
| T-P1 | The bearer token comes from the credential, for the configured scope |
| T-P2 | The token is cached across requests, and refreshed before it expires |
| T-P3 | A credential failure becomes `ProviderUnavailableException` (degraded), not HTTP 500 |
| T-P3a | With `Credential = ManagedIdentity` and no `AZURE_CLIENT_ID`, startup fails |
| T-P4 | The request goes to `https://<subdomain>.openai.azure.com/openai/v1/chat/completions`, with no `api-version`, whatever the base URL's trailing slash |
| T-P5 | The payload's `model` is the deployment name, whatever any other provider's configured model is |
| T-P6 | No API key header is ever sent to the Azure host |
| T-P7 | **Byte-identical:** for the same conversation, the Azure body equals the OpenAI body except `model`, and the headers differ only in `Authorization` |
| T-P8 | A 429 whose `retry-after-ms` fits the budget waits that long and retries the same provider once; no other provider is called |
| T-P8a | A 429 with only `retry-after` (whole seconds) is honoured the same way |
| T-P8b | When both headers are present, `retry-after-ms` wins |
| T-P9 | A 429 whose advised wait exceeds what remains of the budget falls through without waiting |
| T-P10 | A 429 with no usable retry header falls through without waiting |
| T-P11 | The budget is per query: once it is spent, a later 429 in the same query falls through, while an interleaved second query on the same provider instance still has its full budget |
| T-P12 | A filtered prompt produces the filter outcome, stage `prompt`, and calls no other provider, for three fixtures: `error.code` in an envelope, top-level `code` with `inner_error`, and top-level `code` with `innererror` |
| T-P13 | A filtered completion (200, `finish_reason` `content_filter`) produces the filter outcome, stage `completion`, and calls no other provider |
| T-P14 | A 200 with `content_filter_results.error` is **not** treated as filtered |
| T-P15 | A 400 with `content_filter` in neither location is still ReleaseLens's own bug |
| T-P16 | `Name` is `azure-openai`, on spans and usage rows |

**Pricing**

| ID | Test |
|---|---|
| T-C1 | An Azure call is priced at the configured deployment type's rate (Global Standard by default): known tokens give an exact USD figure |
| T-C2 | A versioned `model` string in the response does not change the price |
| T-C3 | Startup fails if a priced provider has no rate |
| T-C4 | On the OpenAI wire, cached tokens are billed once, at the model's own cached rate |
| T-C5 | A query that changes provider mid-way is priced per iteration |
| T-C6 | One normal iteration followed by a filtered completion reports usage and cost equal to the sum of both iterations, priced at the Azure rate, not $0 |

**The verified findings, as regression tests**

| ID | Test |
|---|---|
| T-F1 | A fall-through from Anthropic sends OpenAI's own configured model (was `claude-sonnet-5`) |
| T-F2 | An OpenAI response with a versioned model name is priced correctly (was $0) |
| T-F3 | An OpenAI-direct 200 with `finish_reason` `content_filter` produces the filter outcome, not an empty answer |
| T-F4 | With the chain [A, B], A fails on iteration 1 and B answers; iteration 2 calls B first and never A. If B then fails, the chain moves on past B and does not return to A. A concurrent query still starts at the top of the list |
| T-F5 | A failed tool call keeps its error on the OpenAI wire path |

**Agent and API**

| ID | Test |
|---|---|
| T-A1 | The provider order is configuration; a one-entry list never falls through |
| T-A2 | A filtered outcome reaches `/query` as `metadata.filtered`; `metadata.providers` lists the distinct answering providers in first-answer order |
| T-A3 | The `/evidence/*` responses are unchanged (a contract test against the current shapes) |

**Infrastructure and delivery**, checked in CI or on the first real run rather than as unit
tests:

- `fmt` and `validate` pass for both stacks.
- A plan shows `local_auth_enabled = false`, kind `AIServices`, the pinned model version,
  `NoAutoUpgrade`, and the capacity.
- Deploy, destroy, confirm that `rg-releaselens` is empty, then deploy again.
- The smoke test passes, including its cost check.
- A deliberate wrong-provider configuration fails the smoke test.
- The static workflow check in `ci.yml` passes, and fails on a deliberately bad workflow.
- Once, by hand: a job without `environment: azure` cannot get an Azure token.

**Evaluation harness**

- A run takes an ordered list of arms, each with a name, a base URL and an expected provider,
  instead of a single `api_base_url`.
- The runner loops over pass, then query, then arm, rotating the arm order by pass. It tags
  each outcome with its arm and pass, and pairs outcomes by query and pass.
- Each outcome records `metadata.provider`, `metadata.providers` and `metadata.model`.
- An answer whose providers differ from the arm's expected provider is recorded as an error.
- The judge is priced at the current rate, including output tokens.
- The harness has unit tests for the rotation, the pairing and the wrong-provider rule.

---

## 8. Evaluation

Pre-registered: the design and the decision rule are fixed here, before any data exists.

- **Arms.** Each arm is a separate local API process on its own port, configured with a
  single-entry `Chat:Providers`. All three point at the same restored database. `/query` gains
  no provider input.

  | Arm | Provider | Model | Auth |
  |---|---|---|---|
  | A | Anthropic | Claude Sonnet 5 | key |
  | Z | Azure OpenAI | gpt-4.1-mini 2025-04-14 (Global Standard) | the owner's Entra identity (`AzureCliCredential`) |
  | O | OpenAI | gpt-4.1-mini | key |

- **Queries.** `per_category=2` gives gq-001, 002, 014, 015, 022, 023, 029, 030, 036 and 037.
  Eight are answerable; gq-036 and gq-037 are unanswerable.
- **Passes.** Three. Within a pass the three arms answer each query back to back, in the order
  A,Z,O, then Z,O,A, then O,A,Z.
- **Where.** Locally, against the corpus restored from the 2026-08-12 dump, so the results can
  be compared with the published baseline. On a fresh volume the restore may need the
  `releaselens_app` role created by hand, because `pg_dump` does not include roles.
- **Judge.** Claude Sonnet 5, blinded to the arm.
- **The comparison that tests the claim is Z against O:** the same model through two auth
  paths. Z against A is reported as well, but it compares two different models.
- **The unit of analysis is the query.** For each arm, a query's three passes are averaged
  first. Arms are paired by query, and the 95% confidence interval is a bootstrap that
  resamples queries with their passes kept together.
- **Each metric is scored only over the queries it applies to,** stated as k queries × 3 passes:

  | Metric | Queries (k) |
  |---|---|
  | groundedness | 10 (the pairs where both arms were judged) |
  | citation recall, citation precision | 8 (answerable) |
  | `must_contain` pass rate | 6: gq-001, 002, 022, 023, 029, 030; queries without `must_contain` are left out, not passed |
  | unanswerable accuracy | 2, reported descriptively only, since two queries cannot support a conclusion |

- **The decision rule** applies only to the 0–1 quality metrics above. A difference is
  declared only when the mean paired delta is at most −0.10 or at least +0.10 *and* its 95%
  confidence interval excludes zero. Anything else is reported as **inconclusive at k queries
  × 3 passes**. Per-query deltas are published alongside the means.
- **Latency** (p50 and p95, in milliseconds) and **cost per query** (USD) are reported
  descriptively per arm, with no significance claim. Z's latency includes getting a token on
  the first call of a run; later calls use the cache (§4.7).
- **Legitimate reasons Z and O could differ,** recorded rather than explained away:
  - Azure's content filter
  - OpenAI's `gpt-4.1-mini` may not be the exact `2025-04-14` snapshot; the response's `model`
    string (final iteration) is recorded for each query
  - Z authenticates as the owner, not the managed identity (§1)
- **The Anthropic arm is re-run, not reused.** The 12 August figures predate three behaviour
  changes, and Sonnet 5's price rose from $2/$10 to $3/$15 per MTok on 2026-09-01.
- **Price, an estimate of about $5, which could be half or double that.** Its basis:
  - Anthropic: about $0.07 per query at current rates. That is the 12 August run's $0.2367 for
    five queries, scaled by 1.5 for the price rise.
  - Azure and OpenAI: about $0.02 per query each. That assumes tens of thousands of input
    tokens and a few thousand output tokens per query at gpt-4.1-mini rates; it is not
    measured.
  - The judge: about $0.016 per answer, from the 12 August count-tokens estimate at the
    current input rate plus a small allowance for output.
  - Over 30 query-runs per arm and 90 judged answers.
- **The dry run spends too.** It calls the answering providers for one query per category per
  arm, about $0.55 by the same estimates, and skips the judge. It measures the per-query token
  counts that revise both this price and the capacity in §4.8. **Both the dry run and the real
  run need the owner's approval first.**

---

## 9. Scope

### In v1

- The Azure provider, the shared wire codec and the token handler
- The fixes for F1–F8, on every provider they affect
- Configurable provider order, sticky providers, `metadata.providers` and `metadata.filtered`
- Both Terraform stacks, on azurerm 5.6 and Terraform 1.15
- OIDC deploy, destroy and nightly destroy from `main`, and the smoke test
- The evaluation harness changes and the 30-run study
- README and `docs/architecture.md`, covering:
  - the architecture and identity tables
  - the deployment-type decision
  - what bills
  - what CI can do
  - "What this is not", which also fixes the stale lines there today

### Deliberately later

| Later | Why not now |
|---|---|
| Microsoft Entra authentication for Postgres | Real work: an Entra admin, a database role for the identity, token-based connections, and migrations by token |
| Private networking for Postgres | Costs more, and migrations would have to run inside Azure |
| An API Management gateway in front of Azure OpenAI | A separate, later piece of work; the base URL and token scope are configurable for it |
| Azure Monitor logs with keyless auth | Logs stream for now |
| A smoke test that keeps the key inside Azure | Needs a Container Apps Job and a key-revoke command |
| A GitHub OIDC subject that also binds the branch | Optional hardening on top of the environment rule |
| A successor model | `gpt-4.1-mini` 2025-04-14 retires 2027-04-14; review February 2027 |

### Not done at all

Provisioned deployments, regional Standard (no quota; the rate stays in `ModelPricing`), and a
fallback to other providers in the cloud.

---

## 10. What the README may claim after v1

- Key authentication is disabled on the Azure OpenAI account (`local_auth_enabled = false`),
  no key is used, and none is in Terraform state. CI has no rights on the account, so it
  cannot turn keys back on; only the owner can.
- GitHub holds no credentials, only identifiers. Four of them, the tenant and subscription IDs
  (amended 2026-09-30), and the state storage account's name and the Azure OpenAI base URL
  (amended 2026-10-01), are stored as environment secrets so that public run logs mask them.
- CI holds no role-assignment rights and no subscription-scope rights. It can run code as the
  app identity, read the Postgres password, and create billable resources in the app's
  resource group (§4.10).
- Data at rest stays in the Australia geography; inference runs on Global Standard and may be
  processed in any Azure region (Microsoft's wording).
- Measured figures, only with their date and sample size, and "inconclusive" where the rule in
  §8 says so.

**It may not claim:**

- "no secrets", because Postgres has a password
- that the Azure OpenAI account has no keys: they exist, with key authentication disabled
- that the state storage account has no keys: they exist, with shared-key access disabled, and
  sit in the owner-only bootstrap state
- "stays in australiaeast", or that inference or prompts stay in Australia
- that the budget caps spend: it alerts
- that the nightly destroy guarantees nothing is left running

---

## 11. To verify before or during implementation

| Item | How |
|---|---|
| ~~`gpt-4.1-mini` 2025-04-14 is deployable as regional Standard in `australiaeast`, and the subscription's quota covers the chosen capacity~~ Checked 2026-09-27: deployable, but regional quota is 0; Global Standard has 5000, which led to the amendment in §4.8. ~~Whether 5000 covers the chosen capacity of 100 is confirmed by the first apply~~ Settled by the full bootstrap apply on 2026-09-30 (R8): the deployment provisioned at capacity 100 | `az cognitiveservices model list` and `usage list` |
| Which token scope the account accepts, and that `Cognitive Services OpenAI User` grants inference on an `AIServices` account. Settled 2026-10-01 by the first deploys (R14, R15): the default scope `https://ai.azure.com/.default` works and the role grants inference; no other scope was tried | One real token for each scope, and one real call |
| The wire shape of a filtered-prompt 400 on `/openai/v1/chat/completions` (envelope or not; `innererror` or `inner_error`) | One deliberately filtered prompt against the deployed account, with the owner's approval; until then the classifier accepts both shapes |
| What OpenAI-direct returns for a filtered prompt | OpenAI's documentation, or one real response |
| Whether OpenAI's `prompt_tokens` includes cached tokens (the premise of F4). Inferred from both providers' examples, not stated (read 2026-10-02). OpenAI's prompt-caching cookbook (https://developers.openai.com/cookbook/examples/prompt_caching101) says `cached_tokens` shows "how many of the prompt tokens were a cache hit", and its example has `prompt_tokens` 1136 with `cached_tokens` 1024. Azure's prompt-caching page (https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching, ms.date 2026-08-11) reports cache hits as `cached_tokens` under `prompt_tokens_details`, and its example has `prompt_tokens` 1566 with `cached_tokens` 1408. Both examples show cached tokens as a subset of `prompt_tokens`, but neither page states it outright. `OpenAiWireFormat` subtracts cached tokens from `prompt_tokens` on both wires, and that rests on this reading | OpenAI's and Azure's current usage-object documentation, read and dated |
| ~~What Azure returns in the response `model` field~~ Settled 2026-10-01 by both smoke tests: `gpt-4.1-mini-2025-04-14`. `metadata.model` comes from the response's `model` field (`OpenAiWireFormat`) | One real call; informational only, since pricing no longer depends on it |
| ~~That Contributor on one resource group is enough to create Postgres Flexible Server and the Container Apps resources, with no subscription-scope name check~~ Settled 2026-10-01 by the first deploys (R14, R15): it is | The first real CI plan and apply |
| ~~That azurerm 5.x makes no subscription-scope provider call at init for an identity scoped to one resource group~~ Settled 2026-10-01 by the first deploys (R14, R15): `init` succeeded with no subscription-scope role | The same first run |
| ~~That Managed Identity Operator is enough to attach the identity from the other resource group~~ Settled 2026-10-01 by the first deploys (R14, R15): it is | The same first run; a failure would be a linked-authorisation error |
| ~~Whether GitHub-hosted runners reach Postgres through the "allow Azure services" rule~~ Settled 2026-10-01 by the first deploys (R14, R15): they do, in both smoke tests | The first smoke test |
| ~~OpenAI's own price for `gpt-4.1-mini`~~ Settled 2026-10-02 by OpenAI's pricing page (https://developers.openai.com/api/docs/pricing; the page shows no date): Standard tier, USD per 1M tokens, input 0.40, cached input 0.10, output 1.60. `ModelPricing` prices `openai gpt-4.1-mini` at these rates | OpenAI's pricing page, read and dated |
| Whether the Global Standard deployment charges anything while idle | The first invoice |
| ~~Whether a budget exists on the subscription today, and whether any Defender for Cloud plan is on~~ Checked 2026-09-27: no budget; only `Discovery` and `FoundationalCspm` are on Standard, and that they cost nothing is *unverified* | `az consumption budget list`, `az security pricing list` |
| ~~The full environment subject of the federated credential~~ Settled 2026-09-30 by the probe (R7); the credential was written with it | The probe (§6, step 7) |
| That current `Azure.Identity` and `Azure.Core` have no advisory | NuGet audit on restore |

---

## 12. Risks

- **A broken identity looks like a working system.** Mitigated by Azure being the only provider
  in the cloud, the smoke test, and the evaluation arm recording a wrong-provider answer as an
  error.
- **The environment gate is the only branch binding.** It holds only while no
  `pull_request_target`, `workflow_run` or `issue_comment` workflow references it and the
  repository stays public (§4.12). The static check enforces the first; the second is a rule
  for the owner.
- **CI can rewrite the app stack's state.** Mitigated by the owner never auto-approving a local
  app-stack plan, and by restorable blob versions.
- **Model retirement on 2027-04-14.** The deployment stops working, deliberately
  (`NoAutoUpgrade`); its Global Standard successors need payload changes (§4.8).
- **The nightly destroy fails silently** after 60 idle days or under GitHub load. The
  long-lived budget alert is the backstop; it notifies and stops nothing. Deploy refuses to
  run while the destroy workflow is disabled.
- **The state holds the Postgres password.** Every state version holds that session's password,
  so access is limited to the owner and the deploy identity, old versions expire, and the
  password is replaced every session anyway.
- **Documentation drift.** Microsoft's documentation moved between URL trees during this
  research, and several documented examples contradicted each other. Every Azure fact here is
  dated, and the README cites the page it came from.
