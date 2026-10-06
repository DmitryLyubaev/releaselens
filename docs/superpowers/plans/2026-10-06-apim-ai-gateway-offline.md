# API Management as the AI gateway, plan 1 (offline), implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything the gateway project needs that makes no Azure call: the bootstrap
additions, the per-session `infra/gateway` stack and its policies, the app's gateway setting, the
measurement harness, the CI check workflow, the tests, and the runbook for the live session
(plan 2).

**Architecture:**
- **Bootstrap (long-lived, owner-applied)** gains a second model account in Southeast Asia, the
  failover-test deployments, the gateway's user-assigned identity and its roles, an Entra app
  registration for the gateway's audience, Log Analytics and Application Insights, and a state
  container.
- **`infra/gateway` (per session, owner-applied)** holds API Management Basic v2, one API with
  XML policies loaded from files, a backend pool with circuit breakers (azapi), named values, a
  logger, and a non-current revision 2. It reads the bootstrap's outputs through
  `terraform_remote_state`.
- **The app** needs only a new app-stack setting (`AzureOpenAi__TokenScope`) and a test that pins
  gateway mode. **The harness** is a new package, `eval/app/gateway/`, run as the owner.

**Tech Stack:**
- **Terraform 1.15.8**, azurerm `~> 5.6` (as the other stacks), azapi `~> 2.13`, azuread `~> 3.0`,
  random `~> 3.6`; mocked `terraform test`; run in WSL only
- **C#, .NET 10:** `ReleaseLens.Llm` and its xUnit tests
- **Python 3.14:** `eval/` (httpx, azure-identity, pytest) and `tests/infra` (pytest, PyYAML)

**Spec:** `docs/superpowers/specs/2026-10-06-apim-ai-gateway-design.md`, approved 2026-10-06.

## Global Constraints

- **The design is fixed (spec §3–§9).** Implement it as written. A conflict goes to the controller
  as a question, never a silent change.
- **Names (spec §3):**
  - second account `aoai-releaselens-sea-<existing suffix>`, kind `AIServices`, location
    `var.failover_location` (default `southeastasia`), `local_auth_enabled = false`, custom
    subdomain equal to its name, `project_management_enabled = false`
  - deployments, all gpt-4.1-mini `2025-04-14`, `GlobalStandard`, `NoAutoUpgrade`:
    australiaeast `releaselens-chat-failover-test` capacity **1**; Southeast Asia
    `releaselens-chat` and `releaselens-chat-failover-test`, capacity `var.failover_capacity`
    (default **100**)
  - identity `id-releaselens-gateway`; app registration `releaselens-ai-gateway`; app role
    `Gateway.Invoke` (value and display name), allowed member types `User` and `Application`;
    delegated scope `access_as_user`
  - Log Analytics `log-releaselens` (retention 30 days, `daily_quota_gb = 0.1`); Application
    Insights `appi-releaselens` (workspace-based, `application_type = "other"`); both with local
    authentication off
  - state container `tfstate-gateway`, key `gateway.tfstate`
  - gateway resource group `rg-releaselens-gateway`; APIM `apim-releaselens-<random suffix of the
    gateway stack>`, `BasicV2_1`, `australiaeast`
- **Budgets (spec §4.1):** `tokens_per_minute` default **10000**, `tokens_per_day` default
  **50000**, `token-quota-period="Daily"`, `estimate-prompt-tokens="true"`, counter key the
  token's `oid`.
- **Breakers (spec §3.2):** one rule per backend: `count = 1`, `interval = "PT5M"`, status codes
  429–429, `tripDuration = "PT1M"`, `acceptRetryAfter = true`. Pool: primary priority 1,
  secondary priority 2.
- **Keys:** no key anywhere. `subscription_required = false`; no product or subscription
  resource; no output, setting or log line carries a key.
- **Identifiers:** no tenant, client or object ID, hostname, account name or email in any public
  file. Tests use the placeholder GUIDs already used in this repository's tests
  (`00000000-…`, `11111111-…`, `22222222-…`) and `example.com`.
- **Role assignments live in `infra/bootstrap/roles.tf` only.** The gateway stack creates none.
- **No network in tests.** `httpx.MockTransport`, fake credentials and fake clocks stand in for
  every call. No test sleeps for real.
- **Terraform runs only in WSL**, with `TF_DATA_DIR=$HOME/tfdata/<stack>` and `-backend=false` for
  checks. Never `plan` or `apply` against Azure in a task.
- **Commits:**
  - repo-local identity, never `--author`, never `git config --global`
  - every message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - branch `feat/apim-ai-gateway` (the spec's two commits are already on it)
  - nothing is pushed without the owner's yes

## Rulings made while planning

- **The harness lives in `eval/app/gateway/`**, not the spec's `eval/gateway/`: every harness
  package is under `eval/app/` and runs as `python -m app.<name>`. Cost if wrong: a rename.
- **The backend URLs end in `/openai/v1`**, not the spec's `/openai`. With the version set's
  segment scheme the client calls `/openai/v1/chat/completions`, the operation's template is
  `/chat/completions`, and APIM forwards only the operation path after the backend URL. Smoke
  check in the runbook. Cost if wrong: a one-line change to two backends.
- **Revision 2 is called at `/openai/v1;rev=2/chat/completions`** for the same reason. Smoke check.
- **Both region signals are built in from the start.** The outbound policy sets
  `x-releaselens-backend: primary|secondary` by comparing the forwarded host with a named value,
  besides passing `x-ms-region` through. The smoke test picks one (spec §7.4) without a policy
  change during the live session.
- **The frozen choice is a file**, `eval/app/gateway/freeze.json`, committed now as
  `{"region_signal": null}`. Measured commands refuse to run while it is null.
- **"Custom metrics with dimensions"** on Application Insights is one documented portal step in
  the runbook (spec §12.8 allows it), not azapi in the bootstrap.
- **The CI check calls Entra without a new action.** `ci_check.py` reads GitHub's OIDC token and
  uses azure-identity's `ClientAssertionCredential`, so the workflow pins only the checkout and
  setup-python actions it already pins in `ci.yml`.
- **APIM's required `publisher_email`** comes from a git-ignored tfvars variable, never a literal.

## Review Focus

1. **A 429 whose advice the app would not wait for** (a malformed `retry-after-ms`, an HTTP-date
   `retry-after`, a wait longer than the 3,000 ms budget, a second 429). The harness must count
   it exactly as the app would, or the before run misstates the app. (Task 6
   `test_rule_matches_the_apps_cases`)
2. **A request that times out or cannot connect** during a workload. It is a failed request with
   no status, and the run still sends all 45. (Task 6
   `test_a_timeout_is_a_failed_request_not_a_crash`)
3. **A measured command run before the freeze, or from a dirty tree.** It refuses, naming why.
   (Task 6 `test_measured_run_refuses_without_a_frozen_region_signal`,
   `test_measured_run_refuses_a_dirty_tree`)
4. **An identifier reaching a published file** (an `oid`, a hostname, a GUID). The report and the
   records refuse to be written. (Task 7 `test_report_refuses_text_with_a_guid_or_hostname`)
5. **A bootstrap change that disturbs what runs today.** The primary account, `releaselens-chat`
   and the embedding deployments are unchanged. (Task 2 `chat_deployment_unchanged`,
   `primary_account_unchanged`)

---

### Task 1: The app's gateway setting and the gateway-mode test

**Files:**
- Modify: `infra/terraform/variables.tf` (new `azure_openai_token_scope`, default
  `"https://ai.azure.com/.default"`), `infra/terraform/container_app.tf` (env
  `AzureOpenAi__TokenScope` from it), `infra/terraform/tests/app.tftest.hcl`
- Create: `tests/ReleaseLens.Llm.Tests/AzureOpenAiGatewayModeTests.cs`

**Interfaces:**
- Consumes: `AzureOpenAiChatProvider`, `EntraTokenHandler`, `EntraTokenCache`,
  `FakeTokenCredential`, `StubHttpMessageHandler` (all existing)
- Produces: the app-stack variable `azure_openai_token_scope`

- [ ] **Step 1: Write the failing tests.** The provider is built exactly as `Program.cs` builds it
  (an `HttpClient` over `EntraTokenHandler(new EntraTokenCache(credential, scope, time))`), with
  `BaseUrl = "https://gateway.example.com/openai/v1/"` and
  `TokenScope = "api://11111111-1111-1111-1111-111111111111/.default"`:
  - `Gateway_PostsToTheGatewaysChatCompletions`: the request URI is
    `https://gateway.example.com/openai/v1/chat/completions`
  - `Gateway_AsksForATokenForTheGatewayScope`: the credential's only requested scope is the
    gateway scope, and the `Authorization` header is `Bearer <the fake token>`
  - `Gateway_SendsNoApiKey`: no `api-key` header
  - `Gateway_BudgetRefusal403_IsProviderUnavailable` and `Gateway_BothBackendsTripped503_IsProviderUnavailable`:
    each throws `ProviderUnavailableException`
  - `Gateway_MinuteBudget429_WithRetryAfterSeconds_WaitsAndRetriesOnce`: a 429 with only
    `Retry-After: 2` and a `QueryContext` with the default budget waits 2 s on the fake clock,
    then the second response (200) is the answer

  `app.tftest.hcl`: a run asserting the container's `AzureOpenAi__TokenScope` env equals the
  default, and a run with `azure_openai_token_scope = "api://11111111-1111-1111-1111-111111111111/.default"`
  asserting it is passed through.
- [ ] **Step 2: Run, expect FAIL** (the Terraform test; the C# tests compile and some may already
  pass, which is expected: they pin behaviour that must not regress).
  `dotnet test tests/ReleaseLens.Llm.Tests --filter AzureOpenAiGatewayModeTests`; in WSL,
  `terraform test` in `infra/terraform`.
- [ ] **Step 3: Implement** the variable (description names the gateway, spec §5) and the env
  block beside `AzureOpenAi__BaseUrl`.
- [ ] **Step 4: Run, expect PASS**, plus `dotnet test` for the whole solution and
  `python -m pytest tests/infra -q`.
- [ ] **Step 5: Commit** `feat(app): the token scope is an app-stack setting, so a deployed API can use the gateway`

---

### Task 2: Bootstrap: the second account, the failover-test deployments, the gateway identity and its model roles

**Files:**
- Create: `infra/bootstrap/failover.tf` (the Southeast Asia account, its two deployments, and the
  australiaeast `releaselens-chat-failover-test` deployment)
- Modify: `infra/bootstrap/identities.tf` (`azurerm_user_assigned_identity.gateway`),
  `roles.tf` (`gateway_openai_user_primary`, `gateway_openai_user_failover`: Cognitive Services
  OpenAI User on each account), `variables.tf` (`failover_location`, `failover_capacity`),
  `versions.tf` (add `Microsoft.ApiManagement` and `Microsoft.OperationalInsights` to
  `resource_providers_to_register`: 10 providers), `state.tf` (`tfstate-gateway`) and `roles.tf`
  (`owner_state_gateway`), `outputs.tf`
- Test: `infra/bootstrap/tests/failover.tftest.hcl` (new), `tests/openai.tftest.hcl`,
  `tests/state.tftest.hcl`, `tests/roles.tftest.hcl`; `tests/infra/test_terraform_static.py`

**Interfaces:**
- Produces outputs, read by Task 5 through remote state:
  - `gateway_identity_id`, `gateway_identity_client_id`
  - `primary_openai_backend_url` = `https://<primary subdomain>.openai.azure.com/openai/v1`
  - `failover_openai_backend_url` = the same for the Southeast Asia account
  - `failover_test_deployment` = `"releaselens-chat-failover-test"`

- [ ] **Step 1: Write the failing tests.**
  - `failover.tftest.hcl`, run `failover_account`: kind, `local_auth_enabled == false`, location
    `southeastasia`, name and subdomain `aoai-releaselens-sea-a1b2c3`, in the bootstrap group;
    run `failover_deployments`: the three deployments' names, model, version, `GlobalStandard`,
    capacities 1 / 100 / 100, `NoAutoUpgrade`, and which account each is on
  - `openai.tftest.hcl`: `chat_deployment_unchanged` (name, model, version, capacity 300, type)
    and `primary_account_unchanged` (kind, location, local auth off, subdomain) (Review Focus 5)
  - `roles.tftest.hcl`: both gateway assignments, role name, principal the gateway identity's,
    scope each account (distinct mock IDs per account, as the file already does per container);
    `owner_state_gateway` on the new container
  - `test_terraform_static.py`: `BOOTSTRAP_PROVIDERS` gains the two namespaces;
    `test_bootstrap_registers_exactly_the_ten_providers`; role assignments become 11, all in
    `roles.tf` (rename the test); `test_failover_account_keeps_keys_off`
- [ ] **Step 2: Run, expect FAIL.** In WSL: `terraform fmt -check`, `validate`, `test` in
  `infra/bootstrap`; then `python -m pytest tests/infra -q`.
- [ ] **Step 3: Implement**, in the stack's existing style (comments that state why; each
  deployment's comment cites spec §3.1). The deploy identity gets nothing new here.
- [ ] **Step 4: Run, expect PASS.** Then update every count of providers, role assignments or
  deployments in `README.md`, `docs/architecture.md` and `infra/bootstrap/README.md` (find them
  with `grep -rn "eight\|8 role\|providers" README.md docs infra/bootstrap/README.md`).
- [ ] **Step 5: Commit** `feat(infra): a second account in Southeast Asia, the failover-test deployments, and the gateway identity's model roles`

---

### Task 3: Bootstrap: the gateway's Entra app, monitoring, and their roles

**Files:**
- Create: `infra/bootstrap/gateway_app.tf` (`azuread_application`, `azuread_service_principal`,
  the app role, the scope, the Azure CLI pre-authorisation, the identifier URI),
  `infra/bootstrap/monitoring.tf` (Log Analytics, Application Insights)
- Modify: `versions.tf` (the `azuread` provider, `~> 3.0`, and its lock file entry),
  `roles.tf` (`gateway_metrics_publisher`: Monitoring Metrics Publisher on Application Insights;
  three `azuread_app_role_assignment`s: `gateway_invoke_owner`, `gateway_invoke_app`,
  `gateway_invoke_deploy`), `outputs.tf`
- Test: `infra/bootstrap/tests/gateway_app.tftest.hcl`, `tests/monitoring.tftest.hcl` (new);
  `tests/infra/test_terraform_static.py`

**Interfaces:**
- Produces outputs: `gateway_app_client_id`, `app_insights_id`, `app_insights_connection_string`
  (marked `sensitive`: it holds an instrumentation key, even though local authentication is off)

- [ ] **Step 1: Write the failing tests.**
  - `gateway_app.tftest.hcl` (mock `azuread` and `azurerm`; mock
    `azuread_application_published_app_ids` with a placeholder):
    - display name `releaselens-ai-gateway`; identifier URI `api://<client id>`;
      `requested_access_token_version == 2`
    - exactly one app role, value `Gateway.Invoke`, member types `User` and `Application`
    - one delegated scope `access_as_user`, and the Azure CLI's ID from the published-app-IDs
      data source pre-authorised on it
    - the service principal has `app_role_assignment_required == true`
    - no `azuread_application_password` and no certificate anywhere (static test too)
    - three role assignments of `Gateway.Invoke`: to the signed-in owner's object ID, to the app
      identity's principal and to the deploy identity's principal, each with resource the
      gateway's service principal
  - `monitoring.tftest.hcl`: names, retention 30, `daily_quota_gb == 0.1`, local authentication
    off on both, Application Insights workspace-based on the new workspace;
    `gateway_metrics_publisher` scoped to Application Insights, principal the gateway identity
  - static: role assignments become 12 `azurerm_role_assignment` in `roles.tf`, plus exactly 3
    `azuread_app_role_assignment` in `roles.tf`; `test_bootstrap_has_no_app_secret`
- [ ] **Step 2: Run, expect FAIL** (as Task 2). Check the azuread 3.x and azurerm 5.x argument
  names first with `terraform providers schema -json` (local-auth arguments on Log Analytics and
  Application Insights are named differently across major versions; the Azure CLI's key in
  `azuread_application_published_app_ids` is unverified, spec §12.9). Report any name that
  differs from this plan.
- [ ] **Step 3: Implement.** The `azuread` provider uses the owner's `az` sign-in and the tenant
  from `data.azurerm_client_config.current`. The app's `owners` is the signed-in owner. No
  literal GUID.
- [ ] **Step 4: Run, expect PASS**, then update the docs' counts as in Task 2, and
  `infra/bootstrap/README.md`'s apply section: the owner needs the right to create app
  registrations in their tenant.
- [ ] **Step 5: Commit** `feat(infra): the gateway's Entra app with one role, and monitoring that keeps no keys`

---

### Task 4: The gateway's policies, and their checks

**Files:**
- Create: `infra/gateway/policies/api-v1.xml`, `infra/gateway/policies/api-v1-rev2.xml`
- Create: `tests/infra/test_gateway_policies.py`

**Interfaces:**
- Produces: the named values the policies reference, which Task 5 must create, exactly:
  `tenant-id`, `gateway-app-client-id`, `gateway-identity-client-id`, `tokens-per-minute`,
  `tokens-per-day`, `primary-backend-host`; backend ID `aoai-pool`; metric namespace
  `releaselens-gateway`; response header `x-releaselens-backend`

- [ ] **Step 1: Write the failing tests** (parse with `xml.etree.ElementTree`; both files unless
  named):
  - `test_inbound_order`: the inbound children, ignoring `base`, are in order
    `validate-azure-ad-token`, `llm-token-limit`, `llm-emit-token-metric`,
    `authentication-managed-identity`, `set-backend-service`
  - `test_token_validation`: `tenant-id="{{tenant-id}}"`, `output-token-variable-name="jwt"`,
    audiences `api://{{gateway-app-client-id}}` and `{{gateway-app-client-id}}`, a required
    claim `roles` with value `Gateway.Invoke`
  - `test_budget`: `counter-key` reads `oid` from `jwt`; `tokens-per-minute="{{tokens-per-minute}}"`,
    `token-quota="{{tokens-per-day}}"`, `token-quota-period="Daily"`,
    `estimate-prompt-tokens="true"`
  - `test_metric`: namespace `releaselens-gateway`; exactly three dimensions, `API ID`, `Caller`
    (from `oid`) and `Deployment` (from the body's `model`)
  - `test_credential_swap`: resource `https://cognitiveservices.azure.com`, `client-id="{{gateway-identity-client-id}}"`
  - `test_route`: `backend-id="aoai-pool"`
  - `test_retry_only_in_backend_on_a_model_429`: exactly one `retry`, in `backend`, condition
    `context.Response.StatusCode == 429`, `count="1"`, `interval="0"`,
    `first-fast-retry="true"`, wrapping `forward-request buffer-request-body="true"`
  - `test_backend_label`: outbound sets `x-releaselens-backend` to `primary` when the forwarded
    host equals `{{primary-backend-host}}`, else `secondary`
  - `test_on_error_body_is_fixed`: on-error returns a JSON body built only from the status code
    and a fixed message; no `context.LastError` detail, no `Url` or `Host`
  - `test_rev2_differs_only_by_stripping_headers`: rev 2 equals rev 1 except one added outbound
    block deleting `azureml-model-session`, `x-ms-rai-invoked`, `x-envoy-upstream-service-time`,
    `x-ms-deployment-name` and `apim-request-id`; it never deletes `x-ms-region`,
    `retry-after`, `retry-after-ms` or `x-releaselens-backend`
  - `test_no_literal_identifiers`: no GUID pattern and no `.azure.com`, `.azure-api.net` or
    `.openai.azure.com` host in either file
- [ ] **Step 2: Run, expect FAIL.** `python -m pytest tests/infra/test_gateway_policies.py -q`
- [ ] **Step 3: Write the two files.** Comments in the XML say why each step is where it is
  (spec §4). Expressions use the documented form
  `@(((Jwt)context.Variables["jwt"]).Claims.GetValueOrDefault("oid", "unknown"))`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(infra): the gateway's policies as code: who, how much, then the gateway's own credential`

---

### Task 5: The `infra/gateway` stack

**Files:**
- Create in `infra/gateway`: `versions.tf` (azurerm, azapi, random; provider features
  `api_management { purge_soft_delete_on_destroy = true, recover_soft_deleted = false }`;
  `resource_provider_registrations = "none"`), `backend.tf` (container `tfstate-gateway`, key
  `gateway.tfstate`, `use_azuread_auth = true`), `variables.tf` (`subscription_id`, `location`
  default `australiaeast`, `tfstate_storage_account`, `publisher_email`, `tokens_per_minute`,
  `tokens_per_day`, `release_revision_2` default `false`), `bootstrap.tf` (the
  `terraform_remote_state`), `main.tf` (group, suffix, APIM, named values), `api.tf` (version
  set, API rev 1, operation, policy, rev 2 and its policy, the release with
  `count = var.release_revision_2 ? 1 : 0`), `backends.tf` (azapi, `@2024-05-01`),
  `monitoring.tf` (logger, API diagnostic, `azapi_update_resource` for `metrics = true`),
  `outputs.tf`, `terraform.tfvars.example`, `README.md`, `.terraform.lock.hcl`
- Test: `infra/gateway/tests/gateway.tftest.hcl`; `tests/infra/test_terraform_static.py`;
  `.github/workflows/ci.yml` (a `Check infra/gateway` step and its lock file in the cache key)

**Interfaces:**
- Consumes: Task 2 and Task 3 outputs (by name, through `data.terraform_remote_state.bootstrap.outputs`),
  and Task 4's files and named values
- Produces outputs: `gateway_base_url` = `https://<gateway host>/openai/v1/` and
  `gateway_scope` = `api://<gateway app client id>/.default`, used by the harness and the
  workflow's secrets

- [ ] **Step 1: Write the failing tests.** `gateway.tftest.hcl` overrides the remote state with
  placeholder outputs (`override_data`), and checks:
  - group `rg-releaselens-gateway` in australiaeast; APIM `BasicV2_1`, name starts
    `apim-releaselens-`, identity `UserAssigned` with exactly the gateway identity
  - the six named values, their values from the remote state or variables, none `secret`
  - API: path `openai`, `subscription_required == false`, protocols `["https"]`, version `v1` in
    a `Segment` version set; one operation `POST /chat/completions`; policy content equals
    `file("policies/api-v1.xml")`
  - revision 2 exists, its policy is `api-v1-rev2.xml`; no release by default; one release with
    `release_revision_2 = true`
  - backends: two Single, URLs from the remote state; each breaker as in Global Constraints; the
    pool with priorities 1 and 2
  - logger uses the gateway identity's client ID; API diagnostic: sampling 100, every
    `body_bytes == 0`, `log_client_ip == false`; the metrics update sets `metrics = true`
  - outputs as in Interfaces

  Static: `test_stack_registers_no_providers` covers `gateway`;
  `test_gateway_stack_has_no_role_assignments`; `test_gateway_stack_keeps_keys_off` (no
  `azurerm_api_management_subscription`, `_product`, `subscription_key`, `primary_key`,
  `secondary_key`); `test_gateway_stack_is_not_in_the_app_group` (as the search stack's);
  `test_gateway_stack_purges_and_never_recovers`.
- [ ] **Step 2: Run, expect FAIL** (in WSL, `-backend=false`). Check azurerm 5.x and azapi
  argument names against `terraform providers schema -json` first, and the backend pool's body
  against the `2024-05-01` schema; report differences.
- [ ] **Step 3: Implement.** No `prevent_destroy` anywhere: the stack is meant to be destroyed.
  The README covers: what the stack is, that it bills by the hour (about USD 0.21) and must be
  destroyed, the apply and destroy commands in WSL, and the two git-ignored inputs.
- [ ] **Step 4: Run, expect PASS**, and `python -m pytest tests/infra -q`.
- [ ] **Step 5: Commit** `feat(infra): a per-session API Management gateway over two accounts, keyless both ways`

---

### Task 6: The harness: the client rule, the workload, the failover verdict

**Files:**
- Create in `eval/app/gateway/`: `__init__.py`, `rule.py`, `client.py`, `workload.py`,
  `verdict.py`, `freeze.py`, `freeze.json` (`{"region_signal": null}`), `__main__.py`
- Create: `eval/tests/gateway/test_rule.py`, `test_workload.py`, `test_verdict.py`,
  `test_freeze.py`, `test_cli.py`

**Interfaces:**
- Produces:
  - `rule.advised_wait_ms(headers: Mapping[str, str]) -> int | None` and
    `rule.WAIT_BUDGET_MS = 3000`
  - `client.Record` (frozen dataclass): `seq: int`, `sent_at: float`, `status: int | None`,
    `latency_ms: float`, `region: str | None` (`"primary"`, `"secondary"` or `None`),
    `model_called: bool`, `waited_ms: int`, `prompt_tokens: int`, `completion_tokens: int`,
    `caller: str`
  - `client.send_one(http: httpx.AsyncClient, url: str, token: str, deployment: str, seq: int, *, caller: str, region_signal: str, clock, sleep) -> Record`
  - `workload.run(send: Callable[[int], Awaitable[Record]], *, count: int = 45, spacing_s: float = 4.0, clock, sleep) -> list[Record]`
  - `verdict.failover(before: list[Record], after: list[Record]) -> Verdict`, where `Verdict` has
    `outcome` (`"inconclusive"`, `"held"`, `"did not hold"`), `reason: str`, and the counts
  - `freeze.load() -> Freeze` (`region_signal: Literal["x-ms-region", "x-releaselens-backend"]`),
    raising `FreezeError` when null; `freeze.require_clean_tree(run=subprocess.run)`
  - `python -m app.gateway failover --mode direct|gateway --out <dir>` writes
    `<dir>/failover-<mode>.jsonl`
- Constants (spec §7): prompt of about 300 tokens (a fixed made-up paragraph in `client.py`),
  `max_tokens = 40`, `temperature = 0`, timeout 30 s, 45 requests, 4 s spacing; verdict
  thresholds 14 failures (validity) and 43 successes.

- [ ] **Step 1: Write the failing tests.**
  - `test_rule_matches_the_apps_cases` (Review Focus 1): a table copied from the cases in
    `tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs` (read it; one row per case:
    headers → expected wait or `None`), including `retry-after-ms` preferred over `retry-after`,
    a malformed `retry-after-ms` falling back, an HTTP-date ignored, signs, decimals, lists and
    values past `int` max rejected
  - `send_one`: a 429 advising 1,000 ms waits once on the fake clock then sends again; advising
    4,000 ms fails at once; a second 429 fails; region read from the frozen signal (`x-ms-region`
    `Southeast Asia` → `secondary`, `Australia East` → `primary`, unknown → `None`); no
    `x-ms-region` → `model_called == False`; usage read from the body
  - `test_a_timeout_is_a_failed_request_not_a_crash` (Review Focus 2): a transport raising
    `httpx.ReadTimeout` or `httpx.ConnectError` gives `status None`, and `workload.run` still
    returns 45 records
  - `workload.run` sends at 0, 4, 8, … s on the fake clock, whatever each response takes
  - `test_verdict`: fixtures for each outcome — 13 before-failures → inconclusive (even with 45
    after-successes); 14 failures and 43 successes with one secondary → held; 42 successes → did
    not hold, reason names the count; 45 successes, none secondary → did not hold, reason names
    the region
  - `test_measured_run_refuses_without_a_frozen_region_signal` and
    `test_measured_run_refuses_a_dirty_tree` (Review Focus 3): the CLI exits non-zero with the
    reason, before any request
  - the token comes from the existing `app.retrieval.azure_auth.TokenSource` (direct: scope
    `https://ai.azure.com/.default`; gateway: `--scope`), tenant from `--tenant`
- [ ] **Step 2: Run, expect FAIL.** From `eval/`: `python -m pytest tests/gateway -q`
- [ ] **Step 3: Implement.** Direct mode's URL is `<--base-url>chat/completions` with the
  deployment as `model`, exactly as the app sends; the gateway's is the same against
  `gateway_base_url`. Records never hold a token, an `oid` or a hostname.
- [ ] **Step 4: Run, expect PASS**, and the whole `eval` suite.
- [ ] **Step 5: Commit** `feat(eval): the gateway harness: the app's 429 rule, a fixed workload, and the failover verdict fixed in advance`

---

### Task 7: The harness: budgets, access, the usage metric, and the report

**Files:**
- Create in `eval/app/gateway/`: `checks.py`, `metric.py`, `report.py`, `ci_check.py`; extend
  `__main__.py`
- Create: `eval/tests/gateway/test_checks.py`, `test_metric.py`, `test_report.py`,
  `test_ci_check.py`

**Interfaces:**
- Consumes: Task 6's `Record`, `send_one`, `freeze`
- Produces:
  - `checks.minute_budget(send) -> CheckResult` (B1: sends until a 429; passes when it has
    `Retry-After` and `model_called == False`), `checks.day_budget(send) -> CheckResult` (B2:
    sends until a 403; passes on a 403), `checks.access(post_without_token, post_with_wrong_audience) -> CheckResult`
    (B4: both 401). `CheckResult`: `check: str`, `passed: bool`, `detail: str`
  - `metric.totals(rows: list[dict], labels: Mapping[str, str]) -> dict[str, int]` (sums total
    tokens per caller; an `oid` not in `labels` becomes `"other"`, never dropped) and
    `metric.matches(client: dict[str, int], metric: dict[str, int], tolerance: float = 0.02) -> CheckResult` (B5)
  - `metric.query(app_id: str, token: str, since: str, http) -> list[dict]`: one POST to the
    Application Insights query API (scope `https://api.applicationinsights.io/.default`),
    KQL over `customMetrics` in namespace `releaselens-gateway`, grouped by the `Caller` dimension
  - `report.render(...) -> str` and `report.write(path, text)`, which raises `IdentifierError` on
    any GUID, any `.azure.com` / `.azure-api.net` host, or any string from a `forbidden` list
  - `ci_check.main(env, http, credential_factory) -> int`: reads `ACTIONS_ID_TOKEN_REQUEST_URL`
    and `_TOKEN`, gets GitHub's OIDC token for audience `api://AzureADTokenExchange`, builds
    `ClientAssertionCredential(AZURE_TENANT_ID, AZURE_CLIENT_ID, …)`, gets a token for
    `GATEWAY_SCOPE`, makes one call to `GATEWAY_BASE_URL`, prints only `status=<code>`, returns 0
    on 200 else 1
  - `checks.smoke(...) -> list[CheckResult]`: one call direct, one through the gateway, one at
    `/openai/v1;rev=2/chat/completions`; for each, the status, both region signals as received,
    and the response's header *names*; fails if any body or header value contains a hostname
  - CLI commands: `smoke`, `minute-budget`, `day-budget`, `access`, `metric-totals`, and
    `report --b3 passed|failed` (the workflow run's outcome, entered by the owner)
- Labels come from the environment variable `GATEWAY_CALLER_LABELS` (JSON, `oid` → label), held
  only in the owner's shell, never written to disk.

- [ ] **Step 1: Write the failing tests.**
  - B1, B2, B4 each pass and fail on fixtures; B2 keeps going past minute-budget 429s, waiting
    their `Retry-After` on the fake clock
  - `metric.totals`: two labelled callers and one unknown `oid` → three keys, `other` included;
    `matches` passes at 1.9% and fails at 2.1%, and fails when a caller is missing on either side
  - `metric.query`: the request body's KQL names the namespace and the time window; no token is
    logged
  - `test_report_refuses_text_with_a_guid_or_hostname` (Review Focus 4); `render` on a fixture
    run gives the verdict line worded exactly as `Verdict.outcome`, both runs' counts, p50 and
    p95, B1–B5 with pass or fail, the freeze commit, and the "not tested live" paragraph from
    spec §7.3
  - `smoke`: a fixture whose gateway body contains `aoai-releaselens-sea-a1b2c3.openai.azure.com`
    fails; one with only labels passes; the rev 2 URL is exactly as above
  - `ci_check`: with fakes, prints `status=200` and returns 0; prints `status=403` and returns 1;
    never prints the URL, the scope or a token
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS**, and the whole `eval` suite.
- [ ] **Step 5: Commit** `feat(eval): the budget and access checks, the usage metric's totals, and a report that refuses identifiers`

---

### Task 8: The `gateway-check` workflow

**Files:**
- Create: `.github/workflows/gateway-check.yml`; a good fixture copy of it in
  `tests/infra/fixtures/` (as `test_good_fixture_is_a_byte_copy_of_the_repository_workflow`
  requires); bad fixtures for a wrong trigger and a missing `main` guard
- Modify: `scripts/check_workflows.py` (`ALLOWED_AZURE` and `TRIGGERS` gain
  `"gateway-check.yml": {"workflow_dispatch"}`; its job must carry
  `if: github.ref == 'refs/heads/main'`), `tests/infra/test_check_workflows.py`

**Interfaces:**
- Consumes: Task 7's `python -m app.gateway.ci_check`
- The workflow: `on: workflow_dispatch` only; `permissions: {}` at the top; one job,
  `environment: azure`, `permissions: { id-token: write, contents: read }`,
  `if: github.ref == 'refs/heads/main'`, `timeout-minutes: 10`, the shared concurrency group;
  steps: checkout and setup-python with the SHAs `ci.yml` pins, `pip install -r eval/requirements.txt`,
  then `python -m app.gateway.ci_check` from `eval/` with env `AZURE_TENANT_ID` (secret),
  `AZURE_CLIENT_ID` (variable), `GATEWAY_BASE_URL` and `GATEWAY_SCOPE` (secrets, set for the
  session only)

- [ ] **Step 1: Write the failing tests:** the checker accepts the new workflow and rejects each
  bad fixture with its violation; `test_every_allowed_workflow_has_its_triggers_and_nothing_else_does`
  covers it.
- [ ] **Step 2: Run, expect FAIL.** `python -m pytest tests/infra -q`
- [ ] **Step 3: Implement**, with a header comment saying what the workflow proves (B3), that it
  makes one model call, and that its two secrets exist only during a session.
- [ ] **Step 4: Run, expect PASS**, and `python scripts/check_workflows.py .github/workflows`.
- [ ] **Step 5: Commit** `ci: a dispatch-only check that a second caller is served while the first is over budget`

---

### Task 9: The runbook and the README

**Files:**
- Create: `docs/runbook-gateway.md`
- Modify: `README.md` (a short "AI gateway" section: what it is, that it exists only during
  sessions, that direct mode is the default and production would force traffic through it, that
  the endpoint is public behind Entra, and a link to the runbook; results come in plan 2),
  `docs/architecture.md` (the gateway path beside the direct one)

**Interfaces:** none new.

- [ ] **Step 1: Write the runbook** from spec §10, one section per step, each marked **Ask
  first** where the spec requires the owner's yes, with: the exact commands (WSL for
  Terraform, with `TF_DATA_DIR`; PowerShell for `az` with the per-process CA bundle; `az account
  show` first, stopping on a work account), what to check, the expected cost, and where results
  go. It must include:
  - step 1's read-only checks before applying: Southeast Asia's Global Standard quota and model
    version for gpt-4.1-mini, and the portal step for "custom metrics with dimensions"
  - step 3's smoke checks: one call direct and one through the gateway; `;rev=2`; both region
    signals compared, and the choice written to `freeze.json`; no hostname in any body or header;
    a filtered prompt's 400 passes through; the backend URL ruling (`/openai/v1`); then
    `release_revision_2 = true`
  - the session secrets for the workflow, set before step 6 and deleted in step 8
  - B1's confirmation: the Application Insights query that shows the refused request has no
    backend dependency
  - the order of tests: before, a pause of at least 2 minutes, after; then B1, B2; B3 within 5
    minutes of the owner's 403; then B4, B5
  - step 8's checks: `az group exists -n rg-releaselens-gateway` is `false`, and
    `az apim deletedservice list` shows no gateway instance
  - a findings table for every item in spec §12, to fill during the session
- [ ] **Step 2: Check** that every spec §12 item and every §10 step appears, and that no
  identifier is in the file (`python -m pytest tests/infra -q` still passes).
- [ ] **Step 3: Commit** `docs: the gateway's runbook, and where it fits in the architecture`
