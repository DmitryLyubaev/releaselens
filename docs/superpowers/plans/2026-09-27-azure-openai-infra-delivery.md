# Azure OpenAI Keyless Infrastructure and Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deliver the infrastructure: the long-lived bootstrap stack, the rewritten app stack, the OIDC-only deploy and destroy workflows, the smoke test, and the docs. With them, the keyless runtime from plan 1 runs in Azure as the app identity, and a real call is priced above $0.

**Architecture:** There are two Terraform stacks:
- **`infra/bootstrap`** is applied by the owner, locally, and never destroyed. It holds the state storage, both user-assigned identities, the federated credential, the Azure OpenAI account and its Global Standard deployment, the budget, the empty app resource group, and every role assignment.
- **`infra/terraform`** is deployed and destroyed by GitHub Actions through OIDC. It holds only the Container App and Postgres, and reads four handoff values from GitHub environment variables.

Pure logic goes in small, tested tools, so the workflows stay thin:
- a workflow checker
- `deploy_tools.py`, for smoke, the empty-group check and the OIDC exchange
- a `price` command on the Worker

**Tech Stack:**
- Terraform 1.15.8, run in WSL on the owner's machine (see Global Constraints) and on `ubuntu-latest` in CI. azurerm `~> 5.6` resolves to 5.7.0 as of 2026-09-27.
- GitHub Actions.
- Python 3 with the standard library only for the tools, plus pytest and PyYAML for their tests.
- .NET 10 for the Worker `price` command.

**Spec:** `docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md`, approved 2026-09-24 and amended 2026-09-27 to use Global Standard. This is plan 2 of 3. Plan 1, the runtime, is merged. Plan 3, the evaluation, follows.

## Global Constraints

**Versions:**
- Terraform: `required_version = "~> 1.15.0"` in both stacks, and exactly `1.15.8` in every workflow.
- azurerm: `version = "~> 5.6"`. random: `version = "~> 3.6"`.

**Where Terraform runs on the owner's machine:** in WSL, never on Windows. TLS inspection software on that Windows machine intercepts Terraform's local connection to its provider plugins, and every provider fails with `x509: certificate signed by unknown authority`.
- Every Terraform command in this plan runs as `wsl.exe -d Ubuntu --exec bash -c 'cd /mnt/e/Projects/ReleaseLens/infra/<stack> && export TF_DATA_DIR=$HOME/tfdata/<stack> TF_PLUGIN_CACHE_DIR=$HOME/.terraform.d/plugin-cache && terraform <args>'`, written below as **`TF <stack> <args>`**. Use `--exec`: without it, WSL's default shell re-parses the line and expands `$VAR` and `$?` before the inner bash runs.
- WSL has Terraform 1.15.8 and az 2.89.0. Its HTTPS is not intercepted.

**Terraform tests:**
- They live in `tests/*.tftest.hcl` in each stack, use `command = plan`, and use `mock_provider "azurerm" { override_during = plan }`.
- Computed IDs that an assertion compares get `mock_resource` defaults.
- Tests never apply or destroy, which is why `prevent_destroy` is safe in them.
- They are run with `TF <stack> init -backend=false -input=false` and then `TF <stack> test`.

**Names:**
- Resource groups: `rg-releaselens-bootstrap` and `rg-releaselens`.
- State containers: `tfstate-bootstrap` and `tfstate-app`.
- GitHub environment: `azure`.
- Concurrency: group `releaselens-azure`, with `queue: max` and `cancel-in-progress: false`.
- Nightly destroy cron: `0 14 * * *` (UTC).

**Azure OpenAI account and deployment:**
- Account: kind `AIServices`, `project_management_enabled = false`, `local_auth_enabled = false`, and `custom_subdomain_name` generated once.
- Deployment: model format `OpenAI`, name `gpt-4.1-mini`, version `2025-04-14`, SKU `GlobalStandard`, capacity `100`, `version_upgrade_option = "NoAutoUpgrade"`.

**Roles** (spec §4.10). Role definitions are looked up by name, never by GUID. The spec's six rows become seven `azurerm_role_assignment` resources, because the owner's state row covers both containers:

| Identity | Role | Scope |
|---|---|---|
| App identity | Cognitive Services OpenAI User | the account |
| Owner | Cognitive Services OpenAI User | the account |
| Owner | Storage Blob Data Contributor | `tfstate-bootstrap` |
| Owner | Storage Blob Data Contributor | `tfstate-app` |
| Deploy identity | Contributor | `rg-releaselens` |
| Deploy identity | Managed Identity Operator | the app identity |
| Deploy identity | Storage Blob Data Contributor | `tfstate-app` |

CI has no subscription-scope right and no role on the account.

**The app stack reads nothing from bootstrap.** It uses no `terraform_remote_state` and no data sources for bootstrap resources. The four handoff values arrive as `TF_VAR_*`, taken from the GitHub environment variables:
- `APP_IDENTITY_ID`
- `APP_IDENTITY_CLIENT_ID`
- `AZURE_OPENAI_BASE_URL`
- `AZURE_OPENAI_DEPLOYMENT`

The container's `AZURE_CLIENT_ID` is always the app identity's client ID.

**The app stack contains none of these:** a resource group, a role assignment, a Key Vault, a budget, a Log Analytics workspace, or an Anthropic or OpenAI key.

**GitHub holds no secrets.** The environment `azure` holds only these variables:
- `AZURE_CLIENT_ID`, the deploy identity
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `TFSTATE_STORAGE_ACCOUNT`
- the four handoff values
- `SMOKE_OPEN_RUNNER_IP`, which defaults to unset

**Workflows:**
- Top-level `permissions: {}`.
- `id-token: write, contents: read` only on the job that needs Azure.
- Every action is pinned to a 40-hex commit SHA, with the version tag in a trailing comment.
- Terraform authenticates through `ARM_USE_OIDC=true` and `ARM_CLIENT_ID`, `ARM_TENANT_ID` and `ARM_SUBSCRIPTION_ID`. There is no `azure/login` step.
- Every Terraform state command uses `-lock-timeout=10m`.

**Never done by an implementer, only by the controller after the owner's explicit yes in chat:**
- any `terraform apply` or `destroy` against Azure
- any `az` command that writes
- any push, merge or GitHub settings change: environments, variables, rulesets
- deleting local secret files

Implementers only run tests, `validate`, `fmt` and mocked plans.

**Commits:**
- Plain `git commit` in the ReleaseLens repository. Its local identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`. Never pass `--author` and never run `git config`.
- Every message ends with exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No step pushes.

**Honesty:**
- Comments and docs make no claim that is not true of the code as written.
- Every Azure fact in the docs carries its date and source.
- The README makes only the claims in spec §10.

## Review Focus

1. **A workflow other than `deploy.yml`, `destroy.yml` or `oidc-probe.yml` gets `environment: azure`**, in any spelling: `Azure`, `{ name: azure }`, or an `${{ }}` expression. So does one of those three if it gains a `push`, `pull_request`, `pull_request_target`, `workflow_run` or `issue_comment` trigger. The build must fail. Task 10 has the tests.
2. **Secrets reach a log.** The smoke tool must never print the API key or the connection string, including on its error and exception paths. The workflow masks both before any other step prints. Task 9 tests the tool with sentinel values; Task 13's review checks that the masking comes first.
3. **A destroy leaves something behind,** including a resource created outside Terraform. The destroy run must fail and name each remaining resource. The check must follow ARM's `nextLink` paging. Task 9 has the tests.
4. **The smoke cost check gets cached input tokens wrong.** From plan 1, `tokensIn` is uncached input, so the recomputation must add `cacheReadInputTokens` at the cached rate. A mismatch at 6 decimal places fails the check, and so does `costUsd == 0`. Task 9 has the tests.
5. **Bootstrap's local state reaches git, or the first targeted apply is blocked.** `.gitignore` must ignore bootstrap's `terraform.tfstate*`, `backend_override.tf` and `terraform.tfvars` before any bootstrap command runs; Task 1 has the tests. The budget-only apply must work before the OIDC subject is known (null subject, no credential); Task 4 has the tests.

---

## Delivery shape

Spec §6 fixes the order, so the plan ships in two branches, with owner steps (the runbook at the end) between them.

- **Part A**, branch `feat/azure-infra-bootstrap`, is Tasks 1–12. It holds both stacks, the tools, the checker, the CI changes, the OIDC probe workflow and the docs. After it merges, the owner runs runbook steps R1–R12.
- **Part B**, branch `feat/azure-deploy-workflows`, is Task 13. It holds `deploy.yml` and `destroy.yml`, deletes the probe workflow, and removes it from the checker. After it merges, the runbook continues from R13.

The GitHub environment `azure` must exist before Part A is pushed (R6), because Part A contains a workflow that references it.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `.gitignore` | ignore both stacks' local state, plans, tfvars, override files and `.terraform/` | 1 |
| `tests/infra/requirements.txt`, `tests/infra/conftest.py` | pytest and PyYAML pins; repo-root fixture | 1 |
| `tests/infra/test_gitignore.py` | `git check-ignore` assertions | 1 |
| `infra/bootstrap/versions.tf` | Terraform and provider pins; provider features, registration list, `storage_use_azuread` | 2 |
| `infra/bootstrap/backend.tf` | partial `azurerm` backend for `tfstate-bootstrap` | 2 |
| `infra/bootstrap/variables.tf`, `terraform.tfvars.example` | subscription, location, budget and OIDC subject inputs | 2, 4 |
| `infra/bootstrap/groups.tf` | both resource groups; `CanNotDelete` lock on the bootstrap group | 2 |
| `infra/bootstrap/budget.tf` | action group and subscription budget, moved from the app stack | 2 |
| `infra/bootstrap/state.tf` | state storage account, containers, lifecycle policy | 3 |
| `infra/bootstrap/identities.tf` | deploy and app identities; federated credential | 4 |
| `infra/bootstrap/openai.tf` | Azure OpenAI account and deployment | 5 |
| `infra/bootstrap/roles.tf` | the seven role assignments (the spec's six rows) | 6 |
| `infra/bootstrap/outputs.tf` | handoff values and environment-variable values | 6 |
| `infra/bootstrap/tests/*.tftest.hcl` | mocked-plan assertions per concern | 2–6 |
| `infra/terraform/*.tf` | app stack: Container App and Postgres only; `main.tf` and `budget.tf` deleted | 7 |
| `infra/terraform/tests/app.tftest.hcl` | mocked-plan assertions | 7 |
| `src/ReleaseLens.Llm/Providers/PriceCommand.cs` | CLI-facing cost calculation over `ModelPricing` | 8 |
| `src/ReleaseLens.Worker/Program.cs`, `ReleaseLens.Worker.csproj` | `price` dispatched before the database check; reference to `ReleaseLens.Llm` | 8 |
| `tests/ReleaseLens.Llm.Tests/PriceCommandTests.cs` | price command tests | 8 |
| `scripts/deploy_tools.py` | smoke, empty-group check, OIDC subject and exchange; standard library only | 9 |
| `tests/infra/test_deploy_tools.py` | tests with fake HTTP and fake price commands | 9 |
| `scripts/check_workflows.py`, `tests/infra/test_check_workflows.py`, `tests/infra/fixtures/workflows/**` | static workflow rules | 10 |
| `.github/workflows/ci.yml` | the same three jobs (spec §4.12: "its job set is unchanged"): Terraform 1.15.8 checks for both stacks, the infra tests and the workflow checker as steps, SHA-pinned `publish-image` | 11 |
| `.github/workflows/oidc-probe.yml` | temporary: print the environment subject; show the exchange succeeds with the environment and is denied without it | 11 |
| `README.md`, `docs/architecture.md`, `infra/bootstrap/README.md`, `infra/terraform/README.md` | docs for the two-stack design (spec §9, §10) | 12 |
| `.github/workflows/deploy.yml`, `.github/workflows/destroy.yml` | Part B | 13 |

---

## Part A — branch `feat/azure-infra-bootstrap`

### Task 1: Ignore rules and the infra test harness

**Files:**
- Modify: `.gitignore`, replacing the `infra/terraform/...` block at lines 22–30
- Create: `tests/infra/requirements.txt`, `tests/infra/conftest.py`, `tests/infra/test_gitignore.py`

**Interfaces:**
- Produces:
  - `tests/infra/` as the pytest root, run with `python -m pytest tests/infra -q`
  - a `repo_root` fixture that returns a `pathlib.Path`

- [ ] **Step 1: Write the failing test** `tests/infra/test_gitignore.py`.
  - It holds one parametrized test, `test_ignored(path)`. It runs `git check-ignore -q <path>` from the repo root and asserts exit 0 for each of:
    - `infra/bootstrap/terraform.tfstate`
    - `infra/bootstrap/terraform.tfstate.backup`
    - `infra/bootstrap/.terraform/x`
    - `infra/bootstrap/terraform.tfvars`
    - `infra/bootstrap/backend_override.tf`
    - `infra/bootstrap/tfplan`
    - `infra/terraform/terraform.tfstate`
    - `infra/terraform/app.tfplan`
    - `infra/terraform/terraform.tfvars`
  - A second test, `test_not_ignored(path)`, asserts exit 1 for:
    - `infra/bootstrap/terraform.tfvars.example`
    - `infra/bootstrap/.terraform.lock.hcl`
    - `infra/bootstrap/tests/state.tftest.hcl`
    - `infra/terraform/main.tf`
  - `requirements.txt` pins `pytest` and `PyYAML==6.0.3`, each to its current release, with the version and the date it was read recorded in a comment.
- [ ] **Step 2: Run it.** Run `python -m pip install -r tests/infra/requirements.txt` and then `python -m pytest tests/infra/test_gitignore.py -q`. Expected: the `infra/bootstrap/*` cases FAIL.
- [ ] **Step 3: Implement.** Replace lines 22–30 with patterns under `infra/**`: `.terraform/`, `*.tfstate`, `*.tfstate.*`, `tfplan`, `*.tfplan`, `*.tfvars`, `!*.tfvars.example` and `*_override.tf`.
  - Keep the existing comment about the saved plan holding secrets, reworded for both stacks.
  - Keep the lock-file comment.
- [ ] **Step 4: Run it again.** Expected: all cases PASS.
- [ ] **Step 5: Commit** `chore(infra): ignore both stacks' state, plans, tfvars and override files`.

### Task 2: Bootstrap skeleton — providers, groups, budget, lock, backend

**Files:**
- Create in `infra/bootstrap/`:
  - `versions.tf`, `backend.tf`, `variables.tf` (the Task 2 variables), `groups.tf`, `budget.tf`
  - `terraform.tfvars.example`
  - `tests/budget_and_groups.tftest.hcl`
  - `.terraform.lock.hcl`, from `TF bootstrap init -backend=false`

**Interfaces:**
- Produces:
  - `azurerm_resource_group.bootstrap` (`rg-releaselens-bootstrap`) and `azurerm_resource_group.app` (`rg-releaselens`), both in `var.location`
  - `random_string.suffix`: 6 characters, lowercase and digits, `special = false`
  - `var.subscription_id`, `var.location` (default `australiaeast`), `var.budget_amount_usd` (default `50`), `var.budget_alert_email`, `var.budget_start_date`
- Decisions:
  - **Provider.** Set `resource_provider_registrations = "none"` and `resource_providers_to_register = ["Microsoft.Storage", "Microsoft.ManagedIdentity", "Microsoft.CognitiveServices", "Microsoft.App", "Microsoft.DBforPostgreSQL", "Microsoft.Consumption", "Microsoft.Insights"]`. Also set `storage_use_azuread = true` and `features { cognitive_account { purge_soft_delete_on_destroy = true } }`.
  - **Backend.** `backend "azurerm" { container_name = "tfstate-bootstrap", key = "bootstrap.tfstate", use_azuread_auth = true }`. `storage_account_name` is supplied with `-backend-config` at migration (R9). Before that, the owner uses a git-ignored `backend_override.tf` containing `terraform { backend "local" {} }` (R5).
  - **Budget.** Copy the thresholds from today's `infra/terraform/budget.tf`: actual 50, 80 and 100, plus forecast 100. The action group `ag-releaselens-budget` (`short_name = "rlbudget"`) goes in the bootstrap group, and the budget is named `budget-releaselens-monthly`.
  - **Lock.** `azurerm_management_lock.bootstrap`: `CanNotDelete` on the bootstrap group, named `lock-releaselens-bootstrap`, with `notes` saying it must be lifted to delete anything inside.
  - **Tags** on both groups: `project = "releaselens"`, `managedby = "terraform"`, and `stack = "bootstrap"`. `rg-releaselens` gets `contents = "app stack, deployed and destroyed by CI"`.

- [ ] **Step 1: Write the failing test.** Create `tests/budget_and_groups.tftest.hcl`. Its variables are `subscription_id = "00000000-0000-0000-0000-000000000000"`, `budget_alert_email = "owner@example.com"` and `budget_start_date = "2026-10-01T00:00:00Z"`. Its `run "groups_and_budget"` asserts:
  - both group names and locations
  - the budget amount is `50` and its time grain is `Monthly`
  - the set of actual thresholds is `[50, 80, 100]` and there is exactly one forecast notification at 100
  - the action group's resource group is `rg-releaselens-bootstrap`
  - the lock level is `CanNotDelete` and its scope equals the bootstrap group's `id` (give the group a `mock_resource` default)
- [ ] **Step 2: Run it.** Run `TF bootstrap init -backend=false -input=false`, then `TF bootstrap test`. Expected: FAIL (the resources are undeclared).
- [ ] **Step 3: Implement the files** to the decisions above.
- [ ] **Step 4: Run the checks.** Run `TF bootstrap fmt -check`, `TF bootstrap validate` and `TF bootstrap test`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(bootstrap): providers, resource groups, lock and the budget`.

### Task 3: State storage

**Files:**
- Create: `infra/bootstrap/state.tf`, `infra/bootstrap/tests/state.tftest.hcl`

**Interfaces:**
- Consumes: `azurerm_resource_group.bootstrap`, `random_string.suffix`
- Produces: `azurerm_storage_account.state` (name `strlstate${random_string.suffix.result}`), `azurerm_storage_container.bootstrap` (`tfstate-bootstrap`) and `azurerm_storage_container.app` (`tfstate-app`). Both containers use `storage_account_id` and `container_access_type = "private"`.
- Decisions (spec §4.13):
  - `account_tier = "Standard"`, `account_replication_type = "LRS"`
  - `shared_access_key_enabled = false`, `default_to_oauth_authentication = true`, `local_user_enabled = false`
  - `allow_nested_items_to_be_public = false`, written out even though it is the 5.x default
  - `min_tls_version = "TLS1_2"`
  - `blob_properties`: `versioning_enabled = true`, `delete_retention_policy { days = 7 }`, `container_delete_retention_policy { days = 7 }`. Seven days is this plan's choice; the spec fixes no number.
  - `azurerm_storage_management_policy.state`: one rule that deletes blob versions 90 days after creation
  - `lifecycle { prevent_destroy = true }`

- [ ] **Step 1: Write the failing test.** Create `tests/state.tftest.hcl`, with one assertion for every decision above, and assert that both containers' `storage_account_id` equals the account's `id` (use a `mock_resource` default for the account's `id`).
- [ ] **Step 2: Run** `TF bootstrap test`. Expected: FAIL.
- [ ] **Step 3: Implement** `state.tf`.
- [ ] **Step 4: Run** `TF bootstrap fmt -check`, `validate` and `test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(bootstrap): state storage with shared keys and local users off`.

### Task 4: Identities and the federated credential

**Files:**
- Create: `infra/bootstrap/identities.tf`, `infra/bootstrap/tests/identities.tftest.hcl`
- Modify: `infra/bootstrap/variables.tf`, `infra/bootstrap/terraform.tfvars.example`

**Interfaces:**
- Produces:
  - `azurerm_user_assigned_identity.deploy` (`id-releaselens-deploy`) and `azurerm_user_assigned_identity.app` (`id-releaselens-app`), both in `rg-releaselens-bootstrap`. Per spec §4.9, neither may ever move into `rg-releaselens`.
  - `var.github_oidc_subject`: a string, `default = null`, `nullable = true`
  - `azurerm_federated_identity_credential.github_environment`, with `count = var.github_oidc_subject == null ? 0 : 1`. It is named `github-environment-azure`, with `issuer = "https://token.actions.githubusercontent.com"`, `audience = ["api://AzureADTokenExchange"]`, `subject = var.github_oidc_subject`, and parented to the deploy identity. Use whatever argument azurerm 5.x names for the parent: check `TF bootstrap providers schema -json` and record it in the commit message.
- Decisions:
  - **The subject's validation** passes when the value is null. Otherwise it must start with `repo:DmitryLyubaev@57339946/releaselens@1331560542:`, contain `environment:azure`, and contain no `ref:`. The error message says the value must be exactly the `sub` the probe printed (R7).
  - **No other credential** is declared anywhere.

- [ ] **Step 1: Write the failing test** `tests/identities.tftest.hcl`. It has four runs:
  - `"no_subject_no_credential"`: the variable is unset. Assert `length(azurerm_federated_identity_credential.github_environment) == 0`, and that both identities are in the bootstrap group.
  - `"subject_creates_one_credential"`: the subject is `repo:DmitryLyubaev@57339946/releaselens@1331560542:environment:azure`. Assert one credential with the exact issuer, audience and subject, whose parent equals the deploy identity's `id`, using a mock default.
  - `"branch_subject_rejected"`: the subject is `...:ref:refs/heads/main`. Use `expect_failures = [var.github_oidc_subject]`.
  - `"other_repository_rejected"`: the subject is `repo:someone/else:environment:azure`. Use `expect_failures = [var.github_oidc_subject]`.
- [ ] **Step 2: Run** `TF bootstrap test`. Expected: FAIL.
- [ ] **Step 3: Implement** the identities, the variable and the credential.
- [ ] **Step 4: Run** `TF bootstrap fmt -check`, `validate` and `test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(bootstrap): deploy and app identities, one environment-scoped federated credential`.

### Task 5: Azure OpenAI account and deployment

**Files:**
- Create: `infra/bootstrap/openai.tf`, `infra/bootstrap/tests/openai.tftest.hcl`
- Modify: `infra/bootstrap/variables.tf`

**Interfaces:**
- Produces:
  - `azurerm_cognitive_account.openai` (name `aoai-releaselens-${random_string.suffix.result}`, `custom_subdomain_name` the same)
  - `azurerm_cognitive_deployment.chat` (name `var.azure_openai_deployment_name`, default `releaselens-chat`)
  - `var.azure_openai_capacity` (default `100`)
  - `local.azure_openai_base_url = "https://${azurerm_cognitive_account.openai.custom_subdomain_name}.openai.azure.com/openai/v1/"`
- Decisions: exactly the Global Constraints values. The account has `sku_name = "S0"` and default public network access. The deployment's capacity comes from the variable.

- [ ] **Step 1: Write the failing test.** Create `tests/openai.tftest.hcl` with one run that asserts:
  - the account: kind, `local_auth_enabled == false`, `project_management_enabled == false`, the subdomain prefix `aoai-releaselens-`, and that it lives in `rg-releaselens-bootstrap`
  - the deployment: model format, name and version, SKU name `GlobalStandard`, capacity `100` and `version_upgrade_option == "NoAutoUpgrade"`
  - the base URL starts with `https://aoai-releaselens-` and ends with `.openai.azure.com/openai/v1/`

  A second run sets `azure_openai_capacity = 250` and asserts that it flows through.
- [ ] **Step 2: Run** `TF bootstrap test`. Expected: FAIL.
- [ ] **Step 3: Implement** `openai.tf` and the variables.
- [ ] **Step 4: Run** `TF bootstrap fmt -check`, `validate` and `test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(bootstrap): Azure OpenAI account with key auth off and a Global Standard deployment`.

### Task 6: Role assignments and handoff outputs

**Files:**
- Create: `infra/bootstrap/roles.tf`, `infra/bootstrap/outputs.tf`, `infra/bootstrap/tests/roles.tftest.hcl`

**Interfaces:**
- Consumes: every resource from Tasks 2–5
- Produces seven role assignments, named for their use: `app_openai_user`, `owner_openai_user`, `owner_state_bootstrap`, `owner_state_app`, `deploy_contributor`, `deploy_identity_operator`, `deploy_state_app`. All use `role_definition_name`. The owner's principal comes from `data.azurerm_client_config.current.object_id`, because the owner is the one applying.
- The container scope must be the container's Azure Resource Manager ID. Check in the 5.x schema whether that is `id` or `resource_manager_id` for a container created with `storage_account_id`, and use that attribute.
- Outputs:
  - Non-sensitive: `app_identity_id`, `app_identity_client_id`, `azure_openai_base_url`, `azure_openai_deployment`, `deploy_identity_client_id`, `tenant_id`, `subscription_id`, `tfstate_storage_account`
  - `github_environment_variables`: a map from each GitHub environment variable name in the Global Constraints to its value, so R10 can copy it mechanically

- [ ] **Step 1: Write the failing test.** Create `tests/roles.tftest.hcl` with an `override_data` for `data.azurerm_client_config.current`, setting `object_id = "22222222-2222-2222-2222-222222222222"` and `tenant_id`. It asserts, for each assignment:
  - its role name, its scope (against mock IDs) and its principal
  - that no assignment's scope is the subscription (`/subscriptions/<id>`)
  - that no assignment gives the deploy identity's principal anything on the account
  - that `output.github_environment_variables` has exactly the eight keys (`SMOKE_OPEN_RUNNER_IP` is set by hand) and that `AZURE_CLIENT_ID` maps to the deploy identity's client ID, not the app identity's
- [ ] **Step 2: Run** `TF bootstrap test`. Expected: FAIL.
- [ ] **Step 3: Implement** `roles.tf` and `outputs.tf`.
- [ ] **Step 4: Run** `TF bootstrap fmt -check`, `validate` and `test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(bootstrap): role assignments and the handoff outputs`.

### Task 7: App stack rewrite

**Files:**
- Delete: `infra/terraform/main.tf`, `infra/terraform/budget.tf`
- Rewrite: `infra/terraform/versions.tf`, `variables.tf`, `database.tf`, `container_app.tf`, `outputs.tf`, `terraform.tfvars.example`
- Create: `infra/terraform/backend.tf`, `infra/terraform/tests/app.tftest.hcl`
- Regenerate: `infra/terraform/.terraform.lock.hcl` with `TF terraform init -backend=false -upgrade`

**Interfaces:**
- Consumes (as variables, from `TF_VAR_*`): `subscription_id`, `app_identity_id`, `app_identity_client_id`, `azure_openai_base_url`, `azure_openai_deployment`.
- Produces:
  - Outputs: `api_url`, `database_connection_string` (`sensitive = true`) and `pricing_identity`, an object of `model`, `model_version` and `deployment_type`.
  - Variables with these defaults:
    - `resource_group_name`: `rg-releaselens`
    - `location`: `australiaeast`
    - `azure_openai_model`: `gpt-4.1-mini`
    - `azure_openai_model_version`: `2025-04-14`
    - `azure_openai_deployment_type`: `GlobalStandard`
    - `smoke_runner_ip`: `""`
    - `postgres_admin_username`: `releaselens`
    - `image`: `ghcr.io/dmitrylyubaev/releaselens-api@sha256:` followed by 64 zeros, the placeholder valid only for destroy
- Decisions:
  - **`image` validation.** It must match `^ghcr\.io/dmitrylyubaev/releaselens-api@sha256:[0-9a-f]{64}$`, so a tag such as `:latest` is rejected.
  - **Versions.** Terraform and azurerm as in the Global Constraints. The provider has `subscription_id = var.subscription_id` and `features {}`. The old `key_vault` and `resource_group` feature blocks go, because this stack owns neither.
  - **Backend.** `backend "azurerm" { container_name = "tfstate-app", key = "app.tfstate", use_azuread_auth = true }`, with `storage_account_name` supplied through `-backend-config`.
  - **Postgres.** Keep today's settings: v16, `B_Standard_B1ms`, 32 GB, extensions `VECTOR,PG_TRGM`, and the `allow-azure-services` rule at `0.0.0.0`. The server takes `resource_group_name = var.resource_group_name`. Its name is `psql-releaselens-${random_string.suffix.result}`.
  - **Runner firewall rule.** `azurerm_postgresql_flexible_server_firewall_rule.smoke_runner`, with `count = var.smoke_runner_ip == "" ? 0 : 1`. Its start and end IP are both the variable.
  - **Container Apps environment.** `cae-releaselens`, with no Log Analytics.
  - **Container App.** `ca-releaselens-api`:
    - `identity { type = "UserAssigned", identity_ids = [var.app_identity_id] }`
    - the secret `database-connection`
    - image `var.image`
    - today's probes, scale and ingress
  - **The container's environment variables:**

    | Name | Value |
    |---|---|
    | `RELEASELENS_DB` | from the secret |
    | `ASPNETCORE_ENVIRONMENT` | `Production` |
    | `Chat__Providers__0` | `azure-openai` |
    | `AzureOpenAi__BaseUrl` | `var.azure_openai_base_url` |
    | `AzureOpenAi__Deployment` | `var.azure_openai_deployment` |
    | `AzureOpenAi__Credential` | `ManagedIdentity` |
    | `AzureOpenAi__Model` | `var.azure_openai_model` |
    | `AzureOpenAi__ModelVersion` | `var.azure_openai_model_version` |
    | `AzureOpenAi__DeploymentType` | `var.azure_openai_deployment_type` |
    | `AZURE_CLIENT_ID` | `var.app_identity_client_id` |

    No Anthropic or OpenAI key variable exists.

- [ ] **Step 1: Write the failing test.** Create `tests/app.tftest.hcl`. It supplies all the required variables and a valid digest image. `run "deploys_only_app_resources"` asserts:
  - the identity is `UserAssigned` with exactly `[var.app_identity_id]`
  - each environment variable in the table has its value; build a map from the container's `env` blocks
  - no environment variable named `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` exists
  - the image equals the input
  - the smoke-runner rule count is 0
  - the Container Apps environment's `log_analytics_workspace_id` is null
  - the Postgres server is in `rg-releaselens`

  Three more runs:
  - `"runner_rule_when_ip_set"`: count is 1, with that IP.
  - `"latest_tag_rejected"`: `expect_failures = [var.image]`.
  - `"destroy_placeholder_is_valid"`: `image` unset, and the plan succeeds.
- [ ] **Step 2: Run** `TF terraform init -backend=false -input=false -upgrade`, then `TF terraform test`. Expected: FAIL.
- [ ] **Step 3: Implement** the stack.
- [ ] **Step 4: Run** `TF terraform fmt -check`, `validate` and `test`. Expected: PASS. Also run `grep -rniE "key_vault|log_analytics|anthropic|azurerm_resource_group|role_assignment" infra/terraform/*.tf`. Expected: no match.
- [ ] **Step 5: Commit** `feat(infra): app stack runs as the app identity against Azure OpenAI only`.

### Task 8: `price` command

**Files:**
- Create: `src/ReleaseLens.Llm/Providers/PriceCommand.cs`, `tests/ReleaseLens.Llm.Tests/PriceCommandTests.cs`
- Modify: `src/ReleaseLens.Worker/Program.cs`, `src/ReleaseLens.Worker/ReleaseLens.Worker.csproj` (add a `ProjectReference` to `..\ReleaseLens.Llm\ReleaseLens.Llm.csproj`)

**Interfaces:**
- Produces: `public static class PriceCommand { public static int Execute(IReadOnlyList<string> args, TextWriter output, TextWriter error, DateOnly? asOf = null); }` in namespace `ReleaseLens.Llm.Providers`.
  - **Arguments:** `provider model version deploymentType inputTokens cachedTokens outputTokens`. A `-` for version or deployment type means null.
  - **On success:** prints `ModelPricing.CostUsd(identity, new TokenUsage(input, output, cached, 0), asOf ?? today UTC)` formatted with `CultureInfo.InvariantCulture`, then a newline, and returns 0.
  - **No rate:** writes the `InvalidOperationException` message to `error` and returns 1.
  - **Bad arguments** (wrong count or non-integer tokens): writes a line starting `usage: price <provider> <model> <version|-> <deploymentType|-> <inputTokens> <cachedTokens> <outputTokens>` and returns 2.
- **Worker:** `price` is dispatched to `PriceCommand.Execute(args.Skip(1).ToList(), Console.Out, Console.Error)` and returns its exit code. This happens before `RELEASELENS_DB` is read, so pricing needs no database.

- [ ] **Step 1: Write the failing tests** in `PriceCommandTests`:
  - `Execute_AzureGlobalStandard_PrintsTheCostAtTheReadRate`: args `azure-openai gpt-4.1-mini 2025-04-14 GlobalStandard 12345 1024 678`. The output parses to `0.0061252m` and the exit code is 0.
  - `Execute_IdentityWithNoRate_ExitsOneAndNamesIt`: `DataZoneStandard`. Exit 1, and the error contains `DataZoneStandard`.
  - `Execute_DashMeansNoVersionOrType`: `anthropic claude-sonnet-5 - - 1000000 0 0` with `asOf 2026-09-24`. The output parses to `3.00m`.
  - `Execute_WrongArgumentCount_ExitsTwoWithUsage`, and `Execute_NonNumericTokens_ExitsTwoWithUsage`: exit 2, and the error starts `usage: price`.
- [ ] **Step 2: Run** `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~PriceCommandTests"`. Expected: a compile error, because `PriceCommand` does not exist yet.
- [ ] **Step 3: Implement** `PriceCommand` and the Worker dispatch and reference.
- [ ] **Step 4: Run the checks.**
  - The same filter. Expected: 5 passed.
  - `dotnet build ReleaseLens.sln`. Expected: 0 warnings.
  - With `RELEASELENS_DB` unset: `dotnet run --project src/ReleaseLens.Worker -- price azure-openai gpt-4.1-mini 2025-04-14 GlobalStandard 12345 1024 678`. Expected: it prints `0.0061252`.
- [ ] **Step 5: Commit** `feat(worker): price command over ModelPricing, for the smoke test's cost check`.

### Task 9: `deploy_tools.py`

**Files:**
- Create: `scripts/deploy_tools.py`, `tests/infra/test_deploy_tools.py`

**Interfaces** (standard library only; every network call goes through an injectable `fetch(request) -> (status, headers, body)`):
- `evaluate_smoke(body: dict, expected_cost: Decimal) -> list[str]`: returns failure messages; empty means pass.
  - It checks: `metadata.provider == "azure-openai"`, `metadata.providers == ["azure-openai"]`, `metadata.degraded is False`, and `Decimal(str(metadata.costUsd)) > 0`.
  - It also checks that `costUsd` and `expected_cost`, each quantized to 6 decimal places, are equal.
- `recompute_cost(metadata: dict, identity: tuple[str, str, str], price_cmd: list[str], run=subprocess.run) -> Decimal`: runs `price_cmd + ["azure-openai", model, version, type, tokensIn, cacheReadInputTokens, tokensOut]`.
- `run_smoke(url, api_key, identity, price_cmd, deadline_s=300, fetch=..., sleep=..., clock=...) -> int`:
  - It POSTs `{"question": "What changed in the latest release?"}` to `{url}/query`, with header `X-Api-Key`.
  - It retries on connection errors, timeouts and 5xx until the deadline. On 4xx it does not retry.
  - It prints only: the status, `provider`, `providers`, `model` (informational, spec §11), `degraded`, `costUsd`, the token counts, and the failure messages.
  - It never prints the key, the request headers or the response body.
- `list_group_resources(subscription_id, resource_group, arm_token, fetch) -> list[dict]`: does `GET https://management.azure.com/subscriptions/{s}/resourceGroups/{g}/resources?api-version=2021-04-01` and follows `nextLink`.
- `check_empty(resources) -> list[str]`: returns one line per resource, `<type> <name>`.
- `github_oidc_token(audience="api://AzureADTokenExchange", fetch, env=os.environ) -> str`: uses `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN`.
- `decode_subject(jwt: str) -> str`: returns the payload's `sub`.
- `exchange(client_id, tenant_id, assertion, scope="https://management.azure.com/.default", fetch) -> tuple[bool, str | None]`: returns `(ok, AADSTS code or None)`. It never returns or prints the access token.
- **CLI subcommands:**
  - `smoke`: `--url --model --model-version --deployment-type --price-cmd`, with the key in env `SMOKE_API_KEY`
  - `check-empty`: `--subscription --resource-group --client-id --tenant-id`, obtaining the ARM token through `github_oidc_token` and the same private token request `exchange` uses. `exchange` itself never returns the token (Task 9's resolution of this brief's contradiction).
  - `oidc-subject`
  - `oidc-exchange`: `--client-id --tenant-id --expect ok|denied`
  - Exit codes: 0 pass, 1 check failed, 2 usage.

- [ ] **Step 1: Write the failing tests** in `test_deploy_tools.py`, with a fake `fetch` and a fake `run`. Cover the Review Focus 2–4 cases:
  - `test_smoke_passes_on_a_priced_azure_answer`
  - `test_wrong_provider_fails`, with `provider: "anthropic"`. This is the spec's "a deliberate wrong-provider configuration fails the smoke test".
  - `test_providers_list_must_be_exactly_azure`, for `["azure-openai", "openai"]`
  - `test_degraded_fails`
  - `test_zero_cost_fails`
  - `test_cost_mismatch_at_six_places_fails`
  - `test_recompute_passes_cached_tokens_to_price`: assert the argv holds `cacheReadInputTokens` in the cached position
  - `test_retries_503_then_passes`
  - `test_401_fails_without_retry`
  - `test_deadline_stops_retrying`
  - `test_key_and_connection_string_never_printed`: sentinel key `SENTINEL-KEY-123`, fake responses that include it, and a `run` that raises. Capture stdout and stderr and assert the sentinel appears in neither.
  - `test_check_empty_names_each_leftover`
  - `test_list_follows_next_link`
  - `test_exchange_denied_returns_aadsts_code`, for a body with `"error_codes": [700213]`: expect `(False, "AADSTS700213")`
  - `test_exchange_ok_never_exposes_token`
  - `test_decode_subject`
- [ ] **Step 2: Run** `python -m pytest tests/infra/test_deploy_tools.py -q`. Expected: FAIL (an import error).
- [ ] **Step 3: Implement** `scripts/deploy_tools.py`.
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(delivery): smoke, empty-group and OIDC tools with tests`.

### Task 10: Workflow checker

**Files:**
- Create:
  - `scripts/check_workflows.py`
  - `tests/infra/test_check_workflows.py`
  - fixtures under `tests/infra/fixtures/workflows/good/` and `.../bad-*/`, each directory a small set of `*.yml` files

**Interfaces:**
- Produces:
  - `check(workflows_dir: pathlib.Path) -> list[str]`
  - the CLI `python scripts/check_workflows.py <dir>`, which prints each violation and exits 1 if there are any
  - `ALLOWED_AZURE = {"deploy.yml", "destroy.yml", "oidc-probe.yml"}`, whose comment says that `oidc-probe.yml` is removed in Task 13
- **Rules.** Parse with PyYAML. PyYAML reads the key `on` as `True`, so treat `True` as `on`.
  1. **Environment.** For each job's `environment` (a string, or a mapping's `name`): an `${{`-expression is a violation. A value equal to `azure`, ignoring case, is a violation outside `ALLOWED_AZURE`.
  2. **Triggers.** `deploy.yml` has exactly `{workflow_dispatch}`. `destroy.yml` has exactly `{workflow_dispatch, schedule}`, and its schedule contains cron `0 14 * * *`. `oidc-probe.yml` has exactly `{workflow_dispatch}`.
  3. **Top-level permissions.** Files in `ALLOWED_AZURE` have `permissions == {}`.
  4. **Job permissions.** In `deploy.yml` and `destroy.yml`, the job with `environment: azure` has `permissions == {"id-token": "write", "contents": "read"}`. `deploy.yml`'s job `preflight` has `permissions == {"actions": "read"}`. No other job in those files has `id-token`.
  5. **Concurrency.** `deploy.yml` and `destroy.yml` have top-level `concurrency == {"group": "releaselens-azure", "cancel-in-progress": False, "queue": "max"}`.
  6. **Pinned actions.** Every `uses:` in `ALLOWED_AZURE` files, and in `ci.yml`'s job `publish-image`, matches `^[\w.-]+/[\w./-]+@[0-9a-f]{40}$`. Local `./` actions are exempt.
- A file in `ALLOWED_AZURE` that doesn't exist is not a violation. Part A has no deploy or destroy workflow yet.

- [ ] **Step 1: Write the failing tests:**
  - `test_good_fixtures_pass`
  - one test per `bad-*` fixture, asserting the exact violation text:
    - `bad-env-in-ci`
    - `bad-env-mapping-uppercase`: `{name: Azure}`
    - `bad-env-expression`
    - `bad-deploy-on-push`
    - `bad-destroy-wrong-cron`
    - `bad-missing-top-permissions`
    - `bad-extra-job-permission`
    - `bad-concurrency-cancel-true`
    - `bad-unpinned-action`
    - `bad-publish-image-unpinned`
  - `test_repository_workflows_pass`: runs `check` on the real `.github/workflows`

  This is the spec's "the static workflow check passes, and fails on a deliberately bad workflow".
- [ ] **Step 2: Run** `python -m pytest tests/infra/test_check_workflows.py -q`. Expected: FAIL (an import error).
- [ ] **Step 3: Implement** `check_workflows.py`.
- [ ] **Step 4: Run** the same command. Expected: all PASS, except `test_repository_workflows_pass`, which fails only on `publish-image`'s unpinned actions. Task 11 fixes that. Mark the test `xfail(strict=True)` with that reason, and remove the mark in Task 11.
- [ ] **Step 5: Commit** `feat(ci): static workflow rules for the azure environment`.

### Task 11: CI changes and the OIDC probe workflow

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/oidc-probe.yml`
- Modify: `tests/infra/test_check_workflows.py`, removing the `xfail` from Task 10

**Interfaces:**
- Consumes: `scripts/check_workflows.py`; `deploy_tools.py oidc-subject` and `oidc-exchange`.
- Decisions:
  - **`ci.yml` keeps its three jobs, with their names and triggers** (spec §4.12: "its job set is unchanged").
  - **`terraform-plan`** stays on pull requests only. It uses `hashicorp/setup-terraform` pinned to a SHA, with `terraform_version: 1.15.8` and `terraform_wrapper: false`.
    - It caches `~/.terraform.d/plugin-cache`, keyed on the hash of both lock files.
    - It runs `terraform fmt -check -recursive infra`, and then for each of `infra/bootstrap` and `infra/terraform`: `init -backend=false -input=false`, `validate`, `test`.
    - The stale comment about federated credentials is replaced with one line: CI checks the stacks without Azure credentials, and deploys run from `deploy.yml` through OIDC.
  - **`build-and-test`** gains these steps after the .NET test step, so the static check runs on every pull request and push: `actions/setup-python` pinned to a SHA with Python `3.13`, `pip install -r tests/infra/requirements.txt`, `python -m pytest tests/infra -q`, and `python scripts/check_workflows.py .github/workflows`.
  - **`publish-image`:** every `uses:` is pinned to the commit SHA of its current major tag. Look each one up with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, following annotated tags to the commit, and add a trailing `# vX.Y.Z` comment. The other jobs keep their tags; the spec pins only `publish-image`.
  - **`oidc-probe.yml`:**
    - `name: OIDC probe`, triggered by `workflow_dispatch` only, with optional inputs `client_id` and `tenant_id`, and `permissions: {}`.
    - Job `environment-subject`: `environment: azure`, `permissions: {id-token: write, contents: read}`, and a checkout pinned to a SHA. It runs `python3 scripts/deploy_tools.py oidc-subject`, which prints only the decoded `sub`. Then, if `inputs.client_id != ''`, it runs `oidc-exchange --expect ok`.
    - Job `no-environment`: no environment, `permissions: {id-token: write, contents: read}`, and a pinned checkout. If `inputs.client_id != ''`, it runs `oidc-exchange --expect denied`. This is the spec's "once, by hand: a job without `environment: azure` cannot get an Azure token".
- [ ] **Step 1:** Remove the `xfail` mark, then run `python -m pytest tests/infra -q`. Expected: `test_repository_workflows_pass` FAILS, because `publish-image` is unpinned. A missing allowed file is not a violation.
- [ ] **Step 2:** Implement the `ci.yml` changes and `oidc-probe.yml`.
- [ ] **Step 3:** Run `python -m pytest tests/infra -q` and `python scripts/check_workflows.py .github/workflows`. Expected: all pass, with no violations.
- [ ] **Step 4:** Run `actionlint` if it is available in WSL. If not, check every changed workflow with `python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>`. Expected: no errors.
- [ ] **Step 5: Commit** `ci: check both stacks with Terraform 1.15.8, run infra checks, pin publish-image, add the OIDC probe`.

### Task 12: Docs for the two-stack design

**Files:**
- Create: `infra/bootstrap/README.md`
- Rewrite: `infra/terraform/README.md`
- Modify: `README.md` (the Architecture table rows for Telemetry and Infrastructure, `## Deployment`, `## What this is not`) and `docs/architecture.md` (a new `## Azure deployment` section)

**Decisions, all from the spec, dated where they state an Azure fact:**
- **`infra/bootstrap/README.md`** is the owner's runbook for R1–R12, with the exact WSL commands. It covers the override file, the targeted budget apply, the full apply with the subject, the migration with `-backend-config=storage_account_name=...`, copying `github_environment_variables` into the environment, and the rule that local app-stack plans are reviewed and never `-auto-approve`.
- **`infra/terraform/README.md`** covers what the app stack holds, the variables it takes from the environment, and that it is deployed and destroyed only by the workflows.
- **`README.md`:**
  - **Architecture table.** Telemetry becomes: logs stream with `az containerapp logs show`, and there is no Log Analytics workspace (spec §4.9). Infrastructure becomes: two Terraform stacks, OIDC deploy with no secrets, Azure OpenAI with key authentication disabled, and Global Standard.
  - **`## Deployment`** is rewritten for the new flow: bootstrap once, then deploy and destroy through the workflows, with the nightly destroy described as best effort (spec §4.12). The 12 August "Verified against a live subscription" subsection stays as history, retitled with its date and stack ("single-stack deployment, 12 August 2026"). It is not edited to claim anything new.
  - **The claims** are only those in spec §10: key authentication is off and no key is used or held in state, GitHub holds no secrets, CI's rights, data at rest in the Australia geography, and inference on Global Standard "may be processed in any Azure region", attributed to Microsoft's deployment-types page (dated 2026-08-06, read 2026-09-27). The forbidden claims in §10 appear nowhere.
  - **`## What this is not`** adds the Postgres password and the "allow Azure services" firewall rule (spec §4.14), and the four things CI can do (spec §4.10). It also brings the evaluation paragraph in line with the current `## Evaluation` section.
- **`docs/architecture.md`** covers the architecture diagram, the identity and role table, the deployment-type decision and why it changed, what bills, and what CI can do.

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Check them:
  - `grep -niE "stays in australiaeast|inference stays in australia|no secrets|budget caps|has no keys" README.md docs/architecture.md infra/*/README.md`. Expected: the only matches are "GitHub holds no secrets", which spec §10 allows, and lines inside a quoted "may not claim" list, if one is used.
  - `grep -n "Key Vault\|Log Analytics" README.md`. Expected: matches only where the text says they are not used.
- [ ] **Step 3: Commit** `docs: the two-stack Azure deployment, what CI can do, and what this is not`.

**Part A ends here.** The controller runs the whole local check set:
- `dotnet build`, and the non-Docker `dotnet test` filters
- `python -m pytest tests/infra -q`
- `TF bootstrap test` and `TF terraform test`
- `check_workflows.py`

Then comes the whole-branch review. The runbook starts once the checks and review are clean.

---

## Part B — branch `feat/azure-deploy-workflows` (after runbook R1–R12)

### Task 13: Deploy and destroy workflows

**Files:**
- Create: `.github/workflows/deploy.yml`, `.github/workflows/destroy.yml`
- Delete: `.github/workflows/oidc-probe.yml`. Spec §6 step 10 deletes it right after the variables are copied. It is kept one step longer here so that R11 can re-run it for the "no environment, no token" check, and it goes out with Part B.
- Modify: `scripts/check_workflows.py`, removing `oidc-probe.yml` from `ALLOWED_AZURE`, and its test fixtures to match

**Interfaces:**
- Consumes:
  - The environment variables in the Global Constraints.
  - `deploy_tools.py smoke` and `check-empty`.
  - The Worker commands `migrate`, `create-tenant` and `issue-key`, and `price` from Task 8.
  - App stack outputs `api_url`, `database_connection_string` and `pricing_identity`.
- Decisions (spec §4.12, §6.1):
  - **`deploy.yml`:** `on: workflow_dispatch`, `permissions: {}`, and the concurrency block.
    - **Job `preflight`:** `permissions: {actions: read}`, with no checkout, so no `contents` right is needed.
      - It fails unless `gh api repos/${{ github.repository }}/actions/workflows/destroy.yml --jq .state` is `active`.
      - It resolves `sha-${{ github.sha }}` to a digest anonymously: a token from `https://ghcr.io/token?scope=repository:dmitrylyubaev/releaselens-api:pull`, then a `HEAD` on `https://ghcr.io/v2/dmitrylyubaev/releaselens-api/manifests/sha-${{ github.sha }}` with the OCI index and Docker manifest `Accept` types, reading `Docker-Content-Digest`.
      - If the tag is missing, it fails with: `Image tag sha-<sha> is not in GHCR yet; wait for ci.yml's publish-image job on this commit, then re-run.`
      - It outputs `image=ghcr.io/dmitrylyubaev/releaselens-api@<digest>`.
    - **Job `deploy`:** `needs: preflight`, `environment: azure`, `permissions: {id-token: write, contents: read}`.
      - Environment: `ARM_USE_OIDC: "true"`, the `ARM_*` variables and every `TF_VAR_*` from `vars.*`, and `TF_VAR_image` from `needs.preflight.outputs.image`.
      - Steps: checkout, setup-terraform (1.15.8, no wrapper), setup-dotnet (from `global.json`), and setup-python 3.13, all pinned to SHAs.
      - In `infra/terraform`: `terraform init -input=false -backend-config=storage_account_name=${{ vars.TFSTATE_STORAGE_ACCOUNT }}`, then `terraform apply -auto-approve -input=false -lock-timeout=10m`.
      - **Smoke:**
        1. Read `terraform output -raw database_connection_string` into a shell variable and immediately `echo "::add-mask::$value"`, before anything else prints. Export it as `RELEASELENS_DB`.
        2. Run `dotnet build src/ReleaseLens.Worker -c Release`.
        3. With `RELEASELENS_Tenant__Slug=smoke`, `RELEASELENS_Tenant__DisplayName=smoke`, `RELEASELENS_Tenant__DailyTokenBudget=200000` and `Logging__LogLevel__Default=Warning`, run the Worker's `migrate`, then `create-tenant`, then `issue-key smoke smoke-test`.
        4. Capture `issue-key`'s stdout without echoing it. The key is its last non-empty line; `::add-mask::` it before anything else runs.
        5. Run `python3 scripts/deploy_tools.py smoke`, passing `--url` from `api_url`, the three pricing-identity values from `terraform output -json pricing_identity`, `--price-cmd "dotnet run --no-build -c Release --project src/ReleaseLens.Worker -- price"`, and the key in `SMOKE_API_KEY`.
      - **Runner firewall.** Only when `vars.SMOKE_OPEN_RUNNER_IP == 'true'`: before the smoke, apply again with `TF_VAR_smoke_runner_ip` set to the runner's public IP. In a final `if: always()` step, apply again with it empty.
  - **`destroy.yml`:** `on: workflow_dispatch` plus `schedule: - cron: "0 14 * * *"`, `permissions: {}`, and the concurrency block.
    - **Job `destroy`:** `environment: azure` and `permissions: {id-token: write, contents: read}`, with the same `ARM_*` and `TF_VAR_*` variables. `TF_VAR_image` is left out, so the placeholder applies.
    - It runs `terraform init` as in deploy, then `terraform destroy -auto-approve -input=false -lock-timeout=10m`.
    - Then it runs `python3 scripts/deploy_tools.py check-empty --subscription ${{ vars.AZURE_SUBSCRIPTION_ID }} --resource-group rg-releaselens --client-id ${{ vars.AZURE_CLIENT_ID }} --tenant-id ${{ vars.AZURE_TENANT_ID }}`.
    - There is no confirmation step.
- [ ] **Step 1:** Update the checker fixtures:
  - The `good` set gains a copy of the intended `deploy.yml` and `destroy.yml`.
  - `oidc-probe.yml` is now a violation, with a test named `bad-probe-after-removal`.

  Run `python -m pytest tests/infra/test_check_workflows.py -q`. Expected: the new cases FAIL.
- [ ] **Step 2:** Implement both workflows, delete the probe, and update `ALLOWED_AZURE`.
- [ ] **Step 3:** Run `python -m pytest tests/infra -q`, `python scripts/check_workflows.py .github/workflows`, and `actionlint` or the YAML load check. Expected: all pass.
- [ ] **Step 4: Commit** `feat(delivery): OIDC deploy with a priced smoke test, and nightly destroy with an empty-group check`.

---

## Runbook — owner steps, run by the controller only after an explicit yes for each "Ask first"

Commands marked **WSL** run inside Ubuntu in WSL. Before any Azure command there, `az account show` must show the owner's personal account and tenant, which are recorded privately and not in this repository; stop otherwise. The exact commands for R1–R12 are in `infra/bootstrap/README.md` (Task 12), which corrects this list where running it showed a problem: WSL has no `jq`, `gh` runs in Windows PowerShell, and R9's order.

- **R1.** **WSL:** the owner signs in with `az login` inside WSL, a separate token cache from Windows. Then `az account show` must match the account and tenant above.
- **R2.** **WSL:** `az account get-access-token --query expiresOn -o tsv` succeeds. Print only the expiry.
- **R3.** **Ask first (deletes files).** In `infra/terraform/`, delete `terraform.tfstate`, `terraform.tfstate.backup`, `tfplan`, `terraform.tfvars` and `.terraform/`. Spec §6 step 3 says they hold an Anthropic key and a Log Analytics key. Show the file list before deleting. The owner rotates the Anthropic key and keeps the new one only in `.env`.
- **R4.** Task 1's `.gitignore` is committed on the Part A branch before R5.
- **R5.** **Ask first (bills nothing, but it changes the subscription).** Even the `plan` registers the seven resource providers, because the provider does that when it is configured; registration is free. **WSL:**
  1. Create `infra/bootstrap/terraform.tfvars` from the example. It is git-ignored; it holds the subscription, the budget email and a start date on the first of the current month (Azure rejects a new monthly budget that starts in a past month), and no subject yet.
  2. Create the git-ignored `backend_override.tf` with `terraform { backend "local" {} }`.
  3. Run `terraform init`.
  4. Run `terraform plan -target=azurerm_consumption_budget_subscription.this -target=azurerm_monitor_action_group.budget -out=tfplan`, and show the plan.
  5. Run `terraform apply tfplan`.
- **R6.** **Ask first (a GitHub settings change).** Create the environment `azure` with `gh api`: deployment branches set to custom, one branch rule `main`, no tag rules, no reviewers, and `can_admins_bypass=false`. Read it back with `gh api`. This must happen before R7's push.
- **R7.** **Ask first (a push).**
  1. Check `git log --format='%an <%ae>' origin/main..HEAD`.
  2. Push Part A, open the pull request, wait for CI to go green, and fast-forward `main` after the owner's yes.
  3. Dispatch `OIDC probe` with no inputs, and record the printed `sub`.
- **R8.** **Ask first (creates the federated credential; the deployment bills per token only).** **WSL:**
  1. Add the recorded `sub` to `terraform.tfvars` as `github_oidc_subject`.
  2. Run `terraform plan -out=tfplan`. The plan must show `local_auth_enabled = false`, kind `AIServices`, model version `2025-04-14`, `NoAutoUpgrade`, SKU `GlobalStandard` and capacity `100`.
  3. Show the plan.
  4. Run `terraform apply tfplan`.
  5. Then check that no key is in state, as spec §10 claims, without printing any value. Read `terraform show -json` with `python3`, because WSL has no `jq`, and print only the lengths of `primary_access_key` and `secondary_access_key` (the command is in `infra/bootstrap/README.md`). Expected: both 0. `terraform state show` is no use here, because it masks sensitive values either way.
- **R9.** **WSL:**
  1. Wait up to about ten minutes for the owner's blob role to propagate.
  2. Read the account name with `terraform output -raw tfstate_storage_account` while the local backend is still in place (once the override is gone, `terraform output` refuses to run), and stop if it is empty.
  3. Delete `backend_override.tf`, then run `terraform init -migrate-state -backend-config=storage_account_name=<that name>`.
  4. Run `terraform plan`. Expected: no changes.
  5. Delete the local `terraform.tfstate*` and `tfplan`.
- **R10.** **Ask first (GitHub settings).** Set every entry of `terraform output -json github_environment_variables` as an environment variable of `azure` with `gh api`. Read them back.
- **R11.** Dispatch `OIDC probe` again, with `client_id` set to the deploy identity's client ID and `tenant_id`. Expected:
  - `environment-subject` prints `exchange: ok`
  - `no-environment` prints `exchange: denied (AADSTS…)` and passes
- **R12.** **Ask first (a GitHub settings change).** Create a ruleset on `main` that blocks force pushes and deletion.
- **Standing rule for bootstrap after R8.** The `CanNotDelete` lock blocks every delete in `rg-releaselens-bootstrap`, including forced replacements, the federated credential, and role assignments scoped to the containers. Any later bootstrap plan that deletes or replaces something needs the owner to lift the lock first, and to put it back afterwards (spec §4.9).
- **R13.** **Ask first (a push).** Push Part B, open the pull request, wait for CI to go green, and fast-forward `main` after the owner's yes. Then confirm that `Destroy` shows as active and scheduled.
- **R14.** **Ask first (billing: Postgres by the hour, and tokens).**
  1. Dispatch `Deploy`. It must pass preflight, apply and smoke. Record the `costUsd` it prints.
  2. If the smoke test cannot reach Postgres, set `SMOKE_OPEN_RUNNER_IP=true` (R10's command) and re-run. Record which way it went; this is spec §11.
- **R15.** **Ask first.**
  1. Dispatch `Destroy`. It must end with the empty-group check passing.
  2. Dispatch `Deploy` again (spec §7: "deploy, destroy, confirm empty, deploy again"), and then `Destroy`.
- **R16.** **Docs.** In the README, add a dated "two-stack deployment, <date>" record. Include only what R11, R14 and R15 showed: which runs passed, the smoke `costUsd`, and whether the runner firewall rule was needed. Then commit it, and push it with the owner's yes. Also update the portfolio tracker, which is already approved.
