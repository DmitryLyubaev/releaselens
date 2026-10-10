# Bootstrap stack

This is the long-lived half of ReleaseLens's Azure infrastructure. The owner applies it once,
locally, signed in with their own Azure CLI account, and never destroys it. The other half, the
app stack in [`../terraform`](../terraform/README.md), is deployed and destroyed by GitHub
Actions.

**The owner applied this stack on 2026-09-30, with the runbook below.** This page describes the
stack as built, and that runbook. The design and its reasons are in the
[spec](../../docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md), §4.9–§4.13.

## What it holds

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-releaselens-bootstrap` | holds everything below except the budget, `rg-releaselens`, and the deploy identity's Contributor assignment, which is scoped to `rg-releaselens` |
| Management lock | `lock-releaselens-bootstrap` | `CanNotDelete` on that group; see [the standing rules](#standing-rules-after-r8) |
| State storage account | `strlstate<suffix>` | containers `tfstate-bootstrap` (owner only), `tfstate-app`, `tfstate-search` (owner only), `tfstate-gateway` (owner only) and `tfstate-functions` (owner only); shared keys and local users off; OAuth by default; TLS 1.2; blob versioning; 7 days of blob and container soft delete; old versions deleted 90 days after they were written; `prevent_destroy` |
| Deploy identity | `id-releaselens-deploy` | user-assigned; the identity the workflows sign in as |
| Federated credential | `github-environment-azure` | on the deploy identity, with one subject, for the GitHub environment `azure`; it does not exist while `github_oidc_subject` is unset |
| App identity | `id-releaselens-app` | user-assigned; the identity the Container App runs as |
| Azure OpenAI account | `aoai-releaselens-<suffix>` | kind `AIServices`, SKU `S0`, `local_auth_enabled = false`, `project_management_enabled = false`, custom subdomain equal to its name |
| Model deployment | `releaselens-chat` | `gpt-4.1-mini` version `2025-04-14`, format `OpenAI`, SKU `GlobalStandard`, capacity `300`, `version_upgrade_option = "NoAutoUpgrade"` |
| Model deployment | `releaselens-embed-small` | `text-embedding-3-small` version `1`, format `OpenAI`, SKU `GlobalStandard`, capacity `350`, `version_upgrade_option = "NoAutoUpgrade"`; for the retrieval benchmark |
| Model deployment | `releaselens-embed-large` | `text-embedding-3-large` version `1`, format `OpenAI`, SKU `GlobalStandard`, capacity `350`, `version_upgrade_option = "NoAutoUpgrade"`; for the retrieval benchmark |
| Gateway identity | `id-releaselens-gateway` | user-assigned; the identity the AI gateway's API Management signs in as to call both model accounts |
| Second Azure OpenAI account | `aoai-releaselens-sea-<suffix>` | the AI gateway's failover backend: kind `AIServices`, SKU `S0`, in `var.failover_location` (default `southeastasia`), `local_auth_enabled = false`, `project_management_enabled = false`, custom subdomain equal to its name |
| Model deployment | `releaselens-chat` (second account) | `gpt-4.1-mini` version `2025-04-14`, format `OpenAI`, SKU `GlobalStandard`, capacity `var.failover_capacity` (default `100`), `version_upgrade_option = "NoAutoUpgrade"` |
| Model deployment | `releaselens-chat-failover-test` (both accounts) | `gpt-4.1-mini` version `2025-04-14`, format `OpenAI`, SKU `GlobalStandard`, `NoAutoUpgrade`; capacity `1` on the australiaeast account, so it throttles on purpose, and `var.failover_capacity` on the second |
| Gateway Entra app | `releaselens-ai-gateway` | the app registration API Management validates tokens for, single-tenant, version 2 tokens, identifier URI `api://<its client ID>`; one app role `Gateway.Invoke` (users and applications), one delegated scope `access_as_user` with the Azure CLI pre-authorised on it (its client ID comes from `azuread_application_published_app_ids`); the signed-in owner owns it; no secret and no certificate |
| Gateway service principal | `releaselens-ai-gateway` | `app_role_assignment_required = true`: only an identity holding `Gateway.Invoke` can get a token |
| Ingest identity | `id-releaselens-ingest` | user-assigned; the identity the ingest Function app runs as |
| Tool identity | `id-releaselens-tool` | user-assigned; the identity the search tool's Function app runs as |
| Ingestion storage account | `strlingest<suffix>` | containers `artefacts-in` and `deadletter-events`, both private; queues `ingest-events` and `ingest-events-poison`; no host storage and no deployment package; shared keys and local users off; OAuth by default; no public blob access; TLS 1.2 |
| Ingest host account | `strlingesthost<suffix>` | the ingest app's host storage (`AzureWebJobsStorage`) and its private deployment container `deploy-ingest`; keyless, no public blob access, TLS 1.2, as above |
| Tool host account | `strltoolhost<suffix>` | the tool app's host storage and its private deployment container `deploy-tool`; keyless, no public blob access, TLS 1.2, as above |
| Event Grid system topic | `evgt-releaselens-ingest` | on the ingestion account, with a system-assigned identity |
| Event Grid subscription | `artefacts-in-to-ingest-events` | `Microsoft.Storage.BlobCreated` only, subject beginning `/blobServices/default/containers/artefacts-in/` and ending `.json`; delivers to the `ingest-events` queue and dead-letters to `deadletter-events`, both as the topic's identity, with no key |
| Search tool Entra app | `releaselens-search-tool` | the tool app's audience: single-tenant, version 2 tokens, identifier URI `api://<its client ID>`; one app role `Tool.Invoke` (applications only); no delegated scope and no pre-authorised client; the signed-in owner owns it; no secret and no certificate |
| Search tool service principal | `releaselens-search-tool` | `app_role_assignment_required = true`: only an identity holding `Tool.Invoke`, the gateway identity, can get a token |
| Log Analytics workspace | `log-releaselens` | `PerGB2018`, 30 days of retention, a daily ingestion cap of `0.1` GB, `local_authentication_enabled = false` |
| Application Insights | `appi-releaselens` | workspace-based on the workspace above, `application_type = "other"`, `local_authentication_enabled = false`; the "custom metrics with dimensions" setting is a portal step in the gateway runbook, not Terraform |
| Action group | `ag-releaselens-budget` | emails the alert address |
| Subscription budget | `budget-releaselens-monthly` | at subscription scope, so the lock does not cover it |
| App resource group | `rg-releaselens` | created empty; the app stack deploys into it |
| Role assignments | thirty-three Azure role assignments and four Entra app role assignments (three `Gateway.Invoke`, one `Tool.Invoke`), all in `roles.tf` | see [Roles](#roles) |

`<suffix>` is six random lowercase letters and digits (`random_string.suffix`), generated once.

All five identities stay in the bootstrap group and must never move into `rg-releaselens`. CI holds
Contributor on that group, which includes writing federated credentials. An identity there would
let CI add a trust for itself outside the environment gate.

The provider registers the twelve resource providers that the stacks use: `Microsoft.Storage`,
`Microsoft.ManagedIdentity`, `Microsoft.CognitiveServices`, `Microsoft.App`,
`Microsoft.DBforPostgreSQL`, `Microsoft.Consumption`, `Microsoft.Insights`,
`Microsoft.Search`, which the [search stack](../search/README.md) uses, and
`Microsoft.ApiManagement` and `Microsoft.OperationalInsights`, which the AI gateway uses, and
`Microsoft.EventGrid` and `Microsoft.Web`, which the ingestion and the Function apps use. The owner is allowed to
register them and CI is not, so it happens here. The provider also sets:
- `storage_use_azuread = true`, so storage data-plane calls authenticate through Entra ID, which
  an account with shared keys off requires
- `purge_soft_delete_on_destroy` for the Azure OpenAI account, which matters only if this stack
  is ever torn down on purpose

### Roles

| Assignment | Identity | Role | Scope |
|---|---|---|---|
| `app_openai_user` | app identity | Cognitive Services OpenAI User | the Azure OpenAI account |
| `owner_openai_user` | the owner | Cognitive Services OpenAI User | the Azure OpenAI account |
| `owner_state_bootstrap` | the owner | Storage Blob Data Contributor | `tfstate-bootstrap` |
| `owner_state_app` | the owner | Storage Blob Data Contributor | `tfstate-app` |
| `owner_state_search` | the owner | Storage Blob Data Contributor | `tfstate-search` |
| `owner_state_gateway` | the owner | Storage Blob Data Contributor | `tfstate-gateway` |
| `gateway_openai_user_primary` | gateway identity | Cognitive Services OpenAI User | the australiaeast Azure OpenAI account |
| `gateway_openai_user_failover` | gateway identity | Cognitive Services OpenAI User | the second Azure OpenAI account |
| `gateway_metrics_publisher` | gateway identity | Monitoring Metrics Publisher | Application Insights |
| `deploy_contributor` | deploy identity | Contributor | `rg-releaselens` |
| `deploy_identity_operator` | deploy identity | Managed Identity Operator | the app identity |
| `deploy_state_app` | deploy identity | Storage Blob Data Contributor | `tfstate-app` |
| `owner_state_functions` | the owner | Storage Blob Data Contributor | `tfstate-functions` |
| `owner_artefacts_in` | the owner | Storage Blob Data Contributor | `artefacts-in` |
| `owner_deploy_ingest` | the owner | Storage Blob Data Contributor | `deploy-ingest` |
| `owner_deploy_tool` | the owner | Storage Blob Data Contributor | `deploy-tool` |
| `owner_queue_poison_reader` | the owner | Storage Queue Data Reader | the `ingest-events-poison` queue (the harness peeks it for I4) |
| `owner_queue_events_reader` | the owner | Storage Queue Data Reader | the `ingest-events` queue |
| `ingest_artefacts_reader` | ingest identity | Storage Blob Data Reader | `artefacts-in` |
| `ingest_queue_events` | ingest identity | Storage Queue Data Contributor | the `ingest-events` queue |
| `ingest_queue_poison` | ingest identity | Storage Queue Data Contributor | the `ingest-events-poison` queue |
| `ingest_openai_user` | ingest identity | Cognitive Services OpenAI User | the australiaeast Azure OpenAI account |
| `ingest_host_blob_owner` | ingest identity | Storage Blob Data Owner | the ingest host account |
| `ingest_host_queue_contributor` | ingest identity | Storage Queue Data Contributor | the ingest host account |
| `ingest_host_table_contributor` | ingest identity | Storage Table Data Contributor | the ingest host account |
| `tool_openai_user` | tool identity | Cognitive Services OpenAI User | the australiaeast Azure OpenAI account |
| `tool_host_blob_owner` | tool identity | Storage Blob Data Owner | the tool host account |
| `tool_host_queue_contributor` | tool identity | Storage Queue Data Contributor | the tool host account (the MCP extension's queues) |
| `tool_host_table_contributor` | tool identity | Storage Table Data Contributor | the tool host account |
| `ingest_metrics_publisher` | ingest identity | Monitoring Metrics Publisher | Application Insights |
| `tool_metrics_publisher` | tool identity | Monitoring Metrics Publisher | Application Insights |
| `eventgrid_queue_sender` | Event Grid topic's identity | Storage Queue Data Message Sender | the `ingest-events` queue |
| `eventgrid_deadletter_writer` | Event Grid topic's identity | Storage Blob Data Contributor | `deadletter-events` |

Storage Blob Data Owner on a whole account is the Functions host's documented minimum, so each
app's host storage is in an account of its own: an app's host roles reach its own host account
and nothing else, and its deployment container is covered by them. The tool identity has no role
on the ingestion account. The ingest identity's roles there are on `artefacts-in` and its two
queues only.

Four more assignments are Entra app role assignments (`azuread_app_role_assignment`), not Azure
roles. Three give one identity each `Gateway.Invoke` on the gateway's service principal, and
nothing else can get a token for the gateway. The fourth gives the gateway identity `Tool.Invoke`
on the search tool's service principal, and nothing else can get a token for the tool:

| Assignment | Identity | App role | Resource |
|---|---|---|---|
| `gateway_invoke_owner` | the owner | `Gateway.Invoke` | the gateway's service principal |
| `gateway_invoke_app` | app identity | `Gateway.Invoke` | the gateway's service principal |
| `gateway_invoke_deploy` | deploy identity | `Gateway.Invoke` | the gateway's service principal |
| `tool_invoke_gateway` | gateway identity | `Tool.Invoke` | the search tool's service principal |

Role definitions are looked up by name, never by GUID. The deploy identity, which is what CI
runs as, has:
- nothing at subscription scope
- no right to write role assignments
- no access to the bootstrap state
- no role on the Azure OpenAI account

**Whoever applies this stack is treated as the owner.** The owner's assignments, including the
owner's `Gateway.Invoke`, use the object ID of the principal that is signed in (`data.azurerm_client_config.current`). A plan run
by anyone else would move those assignments to that principal. After R8, the lock would also
block the deletes that move requires.

### Outputs

The app stack reads nothing from this stack. The owner copies the outputs into the GitHub
environment `azure` once, in R10. The output `github_environment_variables` maps eight names to
their values. Despite its name, four of them become environment secrets, and the other four
environment variables:

| Name | In the environment | Value |
|---|---|---|
| `AZURE_CLIENT_ID` | variable | the deploy identity's client ID, which the workflows sign in as |
| `AZURE_TENANT_ID` | secret | the Entra tenant, from the owner's sign-in |
| `AZURE_SUBSCRIPTION_ID` | secret | the subscription |
| `TFSTATE_STORAGE_ACCOUNT` | secret | the name of the state storage account |
| `APP_IDENTITY_ID` | variable | the app identity's resource ID |
| `APP_IDENTITY_CLIENT_ID` | variable | the app identity's client ID; the container gets it as `AZURE_CLIENT_ID` |
| `AZURE_OPENAI_BASE_URL` | secret | `https://aoai-releaselens-<suffix>.openai.azure.com/openai/v1/` |
| `AZURE_OPENAI_DEPLOYMENT` | variable | `releaselens-chat` |

All eight are identifiers, not credentials, so no output is marked sensitive. The four variables
appear unmasked in public run logs, on purpose. The exception is `APP_IDENTITY_ID`: it contains
the subscription ID, so it shows with its subscription segment masked. The four secrets are
secrets only so that GitHub masks them in those logs. GitHub prints variable values in each
step's header before any masking step could run (spec §4.12). GitHub masks a secret only where
its whole value appears, so Terraform's apply and destroy output in the workflows passes through
a filter that hides the subscription ID even when Terraform truncates it.
- **The tenant and subscription IDs** became secrets on 2026-09-30, the owner's decision.
- **The storage account's name and the base URL** became secrets on 2026-10-01, also the owner's
  decision. A request with an invalid token to the storage account's blob endpoint gets a 401
  whose `WWW-Authenticate` header names the tenant ID (checked 2026-09-30). The storage account
  shares its `<suffix>` with the Azure OpenAI account, and both name patterns are in this
  repository, so the base URL would give the storage account's name away. The Azure OpenAI
  endpoint's own 401 names no tenant. Other routes to the tenant ID have not been ruled out.

The outputs `embedding_small_deployment` and `embedding_large_deployment` are for the
retrieval benchmark's `--deployment`, and are not in `github_environment_variables`.

`SMOKE_OPEN_RUNNER_IP`, a fifth variable, is set by hand, and only if the smoke test cannot reach
Postgres.

The AI gateway stack reads these outputs through `terraform_remote_state`, so nobody copies them
by hand: `gateway_identity_id`, `gateway_identity_client_id`, `primary_openai_backend_url`,
`failover_openai_backend_url`, `failover_test_deployment`, `gateway_app_client_id`,
`app_insights_id` and `app_insights_connection_string`. The last is marked `sensitive`, because
the string carries an instrumentation key even though local authentication is off. None is a
credential.

The functions stack reads these the same way: `ingest_identity_id`, `ingest_identity_client_id`,
`ingest_identity_principal_id`, `tool_identity_id`, `tool_identity_client_id`,
`tool_identity_principal_id`, `ingest_storage_account_name`, `ingest_storage_blob_endpoint`,
`ingest_storage_queue_endpoint`, `ingest_host_storage_account_name`, `ingest_host_blob_endpoint`,
`ingest_host_deploy_container`, `tool_host_storage_account_name`, `tool_host_blob_endpoint`,
`tool_host_deploy_container`, `search_tool_app_client_id` and `search_tool_app_identifier_uri`.
They are identifiers and endpoints; no key, connection string or SAS of any of the three accounts
is output.

## Terraform runs in WSL

On the owner's Windows machine, TLS inspection software intercepts Terraform's local connection
to its provider plugins. Every provider then fails with
`x509: certificate signed by unknown authority`. That does not happen in Ubuntu under WSL. So
every Terraform command for every stack runs there, with Terraform 1.15.8, and so does the
Azure CLI that Terraform signs in through.

Inside Ubuntu, once per shell:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/bootstrap   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/bootstrap" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
mkdir -p "$TF_PLUGIN_CACHE_DIR"
```

`TF_DATA_DIR` keeps Terraform's working data (providers and backend settings) on the Linux side,
with one directory per stack. The plugin cache shares provider downloads between the stacks.
Unless a step says PowerShell, its commands run in that shell.

From Windows, one command can run the same way, from PowerShell or Git Bash:

```powershell
wsl.exe -d Ubuntu --exec bash -c 'cd /mnt/e/Projects/ReleaseLens/infra/bootstrap && export TF_DATA_DIR=$HOME/tfdata/bootstrap TF_PLUGIN_CACHE_DIR=$HOME/.terraform.d/plugin-cache && terraform plan'
```

Keep `--exec`. Without it, WSL passes the line to its default shell first. That shell expands
`$VAR` and `$?` before `bash -c` runs, so variables arrive empty and the exit code is lost.

`gh` is not installed in WSL. The GitHub and `git` steps run in PowerShell on Windows, from the
repository root. There, `gh` fills in `{owner}` and `{repo}` from the clone's remote.

## Runbook

The order is part of the design. Two orderings would be unsafe:
- **The budget must exist before anything that can bill:** R5 comes before R8.
- **The environment `azure` must exist before any workflow that names it reaches any branch on
  GitHub:** R6 comes before R7. A run that references a missing environment creates it with no
  protection rules. A token for that environment would then satisfy the federated credential
  from any branch.

Each step says what it changes.

### R1. Sign in inside WSL

*Changes nothing.* WSL keeps its own Azure CLI token cache, separate from Windows.

```bash
az login --tenant <tenant-id>     # add --use-device-code if no browser opens
az account set --subscription <subscription-id>
az account show --query "{user: user.name, tenant: tenantId, subscription: id}" -o table
```

It must show the account, tenant and subscription you intend to use. Stop if it does not.

### R2. Check that the CLI can reach Entra ID

*Changes nothing.* Everything local depends on this: Terraform's sign-in, the state backend, and
the app's `AzureCliCredential` for local runs.

```bash
az account get-access-token --query expiresOn -o tsv
```

It must print an expiry time, and nothing else.

### R3. Remove the single-stack leftovers

*Deletes local files.* This step applies only to a clone that was used with the earlier
single-stack design. That design passed the Anthropic key in as a Terraform variable, so its
local files can hold that key, and its state can also hold a Log Analytics shared key.

```bash
cd ../terraform
ls -la terraform.tfstate terraform.tfstate.backup tfplan terraform.tfvars .terraform
rm -rf terraform.tfstate terraform.tfstate.backup tfplan terraform.tfvars .terraform
cd ../bootstrap
```

Check the list before you delete anything. Then rotate the Anthropic key, and keep the new one
only on your machine: in the environment (for example a Windows user environment variable),
entered with `Read-Host -MaskInput`, or in the git-ignored `.env`. Never write it into a file
the repository tracks.

### R4. Confirm the ignore rules

*Changes nothing.* Bootstrap's local state holds the state storage account's keys, and this
repository is public. PowerShell:

```powershell
git check-ignore -v infra/bootstrap/terraform.tfvars infra/bootstrap/backend_override.tf infra/bootstrap/terraform.tfstate infra/bootstrap/tfplan
```

All four paths must be listed.

### R5. The budget first

*Changes the subscription; bills nothing.* Even the plan registers the twelve resource providers,
because the provider registers them when it is configured. Registration is free.

1. Create the git-ignored variables file. The budget's start date must be the first of the
   current month. Leave `github_oidc_subject` out: while it is unset, no federated credential
   exists.

   ```bash
   cat > terraform.tfvars <<EOF
   subscription_id    = "<subscription-id>"
   budget_alert_email = "<alert address>"
   budget_start_date  = "$(date -u +%Y-%m-01T00:00:00Z)"
   budget_amount_usd  = 50
   location           = "australiaeast"
   EOF
   ```

   Despite the variable's name, the amount of this subscription-scope budget is in the billing
   currency, not necessarily US dollars. For this project's subscription that is AUD, as step 5
   showed on 2026-09-30.

2. Keep the state local until R9, with a git-ignored backend override:

   ```bash
   cat > backend_override.tf <<'EOF'
   terraform {
     backend "local" {}
   }
   EOF
   ```

3. Initialise, plan only the budget and its action group, and read the plan:

   ```bash
   terraform init -input=false
   terraform plan -target=azurerm_consumption_budget_subscription.this -target=azurerm_monitor_action_group.budget -out=tfplan
   ```

   It adds the budget, the action group and `rg-releaselens-bootstrap`, which holds the action
   group. It adds nothing else.

4. Apply it:

   ```bash
   terraform apply tfplan
   ```

5. Read the budget's currency. The budget reports its current spend with a currency unit:

   ```bash
   az rest --method get --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/providers/Microsoft.Consumption/budgets/budget-releaselens-monthly?api-version=2023-05-01" --query properties.currentSpend
   ```

   The portal shows the same under **Subscriptions**, then the subscription, then **Budgets**.
   For this project's subscription it was AUD on 2026-09-30. If yours differs, correct the
   currency notes in these docs.

### R6. Create the GitHub environment `azure`

*Changes GitHub settings.* This must happen before R7. It sets custom deployment branches with
one branch rule, `main`, no tag rules and no required reviewers, and turns administrator bypass
off. PowerShell:

```powershell
@'
{ "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true },
  "can_admins_bypass": false }
'@ | gh api --method PUT 'repos/{owner}/{repo}/environments/azure' --input -
gh api --method POST 'repos/{owner}/{repo}/environments/azure/deployment-branch-policies' -f name=main -f type=branch
gh api 'repos/{owner}/{repo}/environments/azure'
gh api 'repos/{owner}/{repo}/environments/azure/deployment-branch-policies'
```

When you read it back, check all of these:
- `can_admins_bypass` is `false`
- there is no `required_reviewers` protection rule
- `custom_branch_policies` is `true`
- the only branch policy is `main`, of type `branch`

GitHub's REST reference (read 2026-09-27) does not list `can_admins_bypass` as a request field.
If the read-back shows it `true`, turn administrator bypass off on the environment's settings
page, and read it back again.

### R7. Merge, then run the OIDC probe

*Pushes to GitHub.* Done on 2026-09-30. The probe workflow, `.github/workflows/oidc-probe.yml`,
no longer exists: it was removed after R11, with the deploy and destroy workflows, and taken out
of `ALLOWED_AZURE` in `scripts/check_workflows.py`. To repeat this step, for example to write a
new federated credential:
1. Restore the file from git history:

   ```powershell
   $removed = git log --format=%h -1 -- .github/workflows/oidc-probe.yml   # the commit that removed it
   git checkout "$removed^" -- .github/workflows/oidc-probe.yml
   ```

2. Put `oidc-probe.yml` back in `ALLOWED_AZURE` and `TRIGGERS` (`{"workflow_dispatch"}`) in
   `scripts/check_workflows.py`.
3. Remove the `bad-probe-after-removal` case from `BAD` in `tests/infra/test_check_workflows.py`,
   and delete its fixture directory, `tests/infra/fixtures/workflows/bad-probe-after-removal`.
   Otherwise CI goes red: that case expects the probe to be refused, and every `bad-*` directory
   must have a case.

PowerShell, for every step here.

1. Check who the commits you are about to push are from:
   `git log --format='%an <%ae>' origin/main..HEAD`.
2. Push the branch, open a pull request, wait for CI to pass, and fast-forward `main`.
   Dispatched workflows run only from files on the default branch.
3. Run the probe with no inputs, and record the subject it prints:

   ```powershell
   gh workflow run oidc-probe.yml --ref main
   gh run list --workflow oidc-probe.yml --limit 1
   gh run watch <run-id>
   gh run view <run-id> --log | Select-String 'repo:'
   ```

   The job `environment-subject` prints the decoded `sub` of its token, never the token itself.
   `variables.tf` accepts only a subject that meets all of these:
   - it starts with this repository's immutable prefix,
     `repo:DmitryLyubaev@57339946/releaselens@1331560542:`
   - it carries the claim `environment:azure`
   - it contains no `ref:`

   A wrong subject fails only when a workflow signs in, so copy the subject exactly.

### R8. The full apply

*Creates the federated credential and everything else.* Nothing here bills by the hour. The
model deployment bills per token, and the storage account costs a few cents a month (an
estimate).

The `azuread` provider signs in with the same `az` session. The owner needs the right to create
app registrations in their tenant (the tenant setting "Users can register applications" on, or an
Entra role that allows it, such as Application Developer), and the right to assign app roles on
the gateway's and the search tool's own service principals, which owning them gives. Without them
the apply fails at the first Entra app.

```bash
echo 'github_oidc_subject = "<the sub recorded in R7>"' >> terraform.tfvars
terraform plan -out=tfplan
terraform show -no-color tfplan | grep -E 'kind |local_auth_enabled|version_upgrade_option|capacity|GlobalStandard|2025-04-14'
```

The plan must show:
- `kind = "AIServices"`
- `local_auth_enabled = false`
- model version `2025-04-14`
- `version_upgrade_option = "NoAutoUpgrade"`
- SKU `GlobalStandard`
- capacity `300`

Then apply it:

```bash
terraform apply tfplan
```

Then check that no Azure OpenAI key is in the state. This prints only the lengths of the keys,
never their values:

```bash
terraform show -json | python3 -c '
import json, sys
resources = json.load(sys.stdin)["values"]["root_module"]["resources"]
account = next(r["values"] for r in resources if r["address"] == "azurerm_cognitive_account.openai")
print([len(account.get("primary_access_key") or ""), len(account.get("secondary_access_key") or "")])
'
```

Expected: `[0, 0]`. The keys exist on the account, with key authentication disabled. This checks
that the provider did not copy them into state. `terraform state show` cannot answer the
question, because it masks sensitive values whether they are set or not. WSL has no `jq`, which
is why the check uses `python3`. If the result is anything other than `[0, 0]`, the README's
claim that no key is in state is wrong and must change.

The state storage account's keys *are* in this state. They exist, with shared-key access
disabled, and the bootstrap state is the owner's alone.

### R9. Move the state into Azure

*Changes nothing billable.*

1. Wait up to about ten minutes after R8 for the owner's Storage Blob Data Contributor roles to
   propagate.
2. Read the account name *before* you remove the override. Once `backend_override.tf` is gone,
   Terraform refuses to read state until the backend is initialised.

   ```bash
   ACCOUNT=$(terraform output -raw tfstate_storage_account)
   echo "$ACCOUNT"
   rm backend_override.tf
   terraform init -migrate-state -backend-config=storage_account_name="$ACCOUNT"
   ```

   Answer `yes` when Terraform asks whether to copy the existing state to the new backend. If
   the copy is refused with an authorisation error, the role has not propagated yet: wait, then
   run the `init` again.
3. Run a plan. Expected: `No changes.`

   ```bash
   terraform plan
   ```

4. Delete the local copies:

   ```bash
   rm -f terraform.tfstate terraform.tfstate.* tfplan
   ```

From then on, the state is the blob `bootstrap.tfstate` in `tfstate-bootstrap`. A fresh
`TF_DATA_DIR` needs `terraform init -backend-config=storage_account_name=<account>` again. Keep
`terraform.tfvars`, because later bootstrap plans need it.

### R10. Copy the outputs into the environment

*Changes GitHub settings.* PowerShell:

```powershell
$values = wsl.exe -d Ubuntu --exec bash -c 'cd /mnt/e/Projects/ReleaseLens/infra/bootstrap && export TF_DATA_DIR=$HOME/tfdata/bootstrap TF_PLUGIN_CACHE_DIR=$HOME/.terraform.d/plugin-cache && terraform output -json github_environment_variables' | ConvertFrom-Json
$secrets = 'AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID', 'TFSTATE_STORAGE_ACCOUNT', 'AZURE_OPENAI_BASE_URL'
foreach ($v in $values.PSObject.Properties) {
  if ($v.Name -in $secrets) { gh secret set $v.Name --env azure --body $v.Value }
  else { gh variable set $v.Name --env azure --body $v.Value }
}
gh variable list --env azure
gh secret list --env azure
```

The variable list must show exactly the four variables in [Outputs](#outputs), and the secret
list exactly the four secrets there. GitHub never shows a secret's value again, so only the
secrets' names can be read back. GitHub holds no credentials: all eight are identifiers.

Leave `SMOKE_OPEN_RUNNER_IP` unset. If the first deploy's smoke test cannot reach Postgres, set
it, and deploy again:

```powershell
gh variable set SMOKE_OPEN_RUNNER_IP --env azure --body true
```

### R11. Prove the gate

*Changes nothing.* Done on 2026-09-30, with the result expected below: `environment-subject`
printed `exchange: ok`, and `no-environment` printed `exchange: denied (AADSTS700213)` and passed.

The probe takes the client and tenant IDs as workflow inputs. GitHub printed both in the `env`
header of each exchange step, so that run's public log shows the tenant ID, and a repeat would
print it again. The deploy and destroy workflows read the tenant ID from an environment secret
instead, which GitHub masks.

The steps below need the probe restored (see [R7](#r7-merge-then-run-the-oidc-probe)). The
tenant ID is a secret now, which cannot be read back, so they take it from bootstrap's output.
PowerShell:

```powershell
$client = gh variable get AZURE_CLIENT_ID --env azure
$tenant = wsl.exe -d Ubuntu --exec bash -c 'cd /mnt/e/Projects/ReleaseLens/infra/bootstrap && export TF_DATA_DIR=$HOME/tfdata/bootstrap TF_PLUGIN_CACHE_DIR=$HOME/.terraform.d/plugin-cache && terraform output -raw tenant_id'
gh workflow run oidc-probe.yml --ref main -f client_id=$client -f tenant_id=$tenant
gh run list --workflow oidc-probe.yml --limit 1
gh run watch <run-id>
gh run view <run-id> --log | Select-String 'exchange:'
```

Expected:
- the job `environment-subject` prints `exchange: ok`
- the job `no-environment` prints `exchange: denied (AADSTS…)` and passes

`no-environment` runs only after `environment-subject` has passed. A wrong client or tenant ID is
denied too, so the denial proves something only after the same IDs were accepted with the
environment.

### R12. Protect `main`

*Changes GitHub settings.* Deploy authority is the right to push to `main`, so `main` gets a
ruleset that blocks force pushes and deletion. PowerShell:

```powershell
@'
{ "name": "main", "target": "branch", "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [ { "type": "non_fast_forward" }, { "type": "deletion" } ] }
'@ | gh api --method POST 'repos/{owner}/{repo}/rulesets' --input -
gh api 'repos/{owner}/{repo}/rulesets'
```

After R12 come the deploy and destroy workflows, `deploy.yml` and `destroy.yml`. How they run is
in [`../terraform`](../terraform/README.md#deployed-and-destroyed-by-the-workflows).

## Standing rules after R8

- **The lock blocks every delete in `rg-releaselens-bootstrap`.** That includes forced
  replacements, the federated credential, and the role assignments scoped to the state
  containers. Before any later bootstrap plan that deletes or replaces something, the owner
  lifts the lock, and puts it back afterwards. Creating things is unaffected. The lock does not
  cover the subscription-scope budget.
- **Whoever applies this stack is treated as the owner.** Apply it only as the owner (see
  [Roles](#roles)).
- **Never change a state container's access type in place.** azurerm 5.7.0's documentation for
  `azurerm_storage_container` says it makes that update with a shared key, which the account
  refuses.
- **Local app-stack plans are reviewed, never `-auto-approve`.** CI can rewrite the app stack's
  state. So any planned deletion of something other than the Container Apps resources or
  Postgres is treated as tampering: stop, and restore an earlier version of `app.tfstate` (see
  [`../terraform`](../terraform/README.md#running-it-locally)).
- **There is no 90-day rollback.** Every state write keeps the previous content as a blob
  version. The lifecycle rule deletes a version 90 days after its content was written, not 90
  days after it was replaced.
  - The app state is rewritten on every deploy and destroy, so recent versions of it are there
    to restore.
  - The bootstrap state changes rarely. If it is overwritten or deleted after it has sat
    unchanged for 90 days, the previous version can be deleted at once. Its practical recovery
    window can then be only the 7 days of soft delete.
- **The repository stays public.** On GitHub Free, a private repository's environment protection
  rules are ignored. So the federated credential is deleted before any change of visibility,
  which means lifting the lock.
- **No workflow triggered by `pull_request_target`, `workflow_run` or `issue_comment` references
  `environment: azure`.** Those runs carry the default branch's ref, so they would pass the
  `main` rule. `scripts/check_workflows.py` runs in CI and fails the build in these cases:
  - a workflow names the environment and is not `deploy.yml`, `destroy.yml` or `gateway-check.yml`
  - one of those three gains a trigger it should not have
  - a job outside them calls an external reusable workflow
- **Key authentication stays off.** CI has no role on the Azure OpenAI account, so only the owner
  could turn it back on.

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests in `tests/` plan against a mocked provider, and never apply or destroy anything. CI
runs them with `fmt` and `validate`, using Terraform 1.15.8, on every pull request to `main`.
