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
| State storage account | `strlstate<suffix>` | containers `tfstate-bootstrap` (owner only) and `tfstate-app`; shared keys and local users off; OAuth by default; TLS 1.2; blob versioning; 7 days of blob and container soft delete; old versions deleted 90 days after they were written; `prevent_destroy` |
| Deploy identity | `id-releaselens-deploy` | user-assigned; the identity the workflows sign in as |
| Federated credential | `github-environment-azure` | on the deploy identity, with one subject, for the GitHub environment `azure`; it does not exist while `github_oidc_subject` is unset |
| App identity | `id-releaselens-app` | user-assigned; the identity the Container App runs as |
| Azure OpenAI account | `aoai-releaselens-<suffix>` | kind `AIServices`, SKU `S0`, `local_auth_enabled = false`, `project_management_enabled = false`, custom subdomain equal to its name |
| Model deployment | `releaselens-chat` | `gpt-4.1-mini` version `2025-04-14`, format `OpenAI`, SKU `GlobalStandard`, capacity `100`, `version_upgrade_option = "NoAutoUpgrade"` |
| Action group | `ag-releaselens-budget` | emails the alert address |
| Subscription budget | `budget-releaselens-monthly` | at subscription scope, so the lock does not cover it |
| App resource group | `rg-releaselens` | created empty; the app stack deploys into it |
| Role assignments | seven, in `roles.tf` | see [Roles](#roles) |

`<suffix>` is six random lowercase letters and digits (`random_string.suffix`), generated once.

Both identities stay in the bootstrap group and must never move into `rg-releaselens`. CI holds
Contributor on that group, which includes writing federated credentials. An identity there would
let CI add a trust for itself outside the environment gate.

The provider registers the seven resource providers that both stacks use: `Microsoft.Storage`,
`Microsoft.ManagedIdentity`, `Microsoft.CognitiveServices`, `Microsoft.App`,
`Microsoft.DBforPostgreSQL`, `Microsoft.Consumption` and `Microsoft.Insights`. The owner is
allowed to register them and CI is not, so it happens here. The provider also sets:
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
| `deploy_contributor` | deploy identity | Contributor | `rg-releaselens` |
| `deploy_identity_operator` | deploy identity | Managed Identity Operator | the app identity |
| `deploy_state_app` | deploy identity | Storage Blob Data Contributor | `tfstate-app` |

Role definitions are looked up by name, never by GUID. The deploy identity, which is what CI
runs as, has:
- nothing at subscription scope
- no right to write role assignments
- no access to the bootstrap state
- no role on the Azure OpenAI account

**Whoever applies this stack is treated as the owner.** The owner's three assignments use the
object ID of the principal that is signed in (`data.azurerm_client_config.current`). A plan run
by anyone else would move those assignments to that principal. After R8, the lock would also
block the deletes that move requires.

### Outputs

The app stack reads nothing from this stack. The owner copies the outputs into the GitHub
environment `azure` once, in R10. The output `github_environment_variables` maps eight names to
their values. Despite its name, two of them become environment secrets, and the other six
environment variables:

| Name | In the environment | Value |
|---|---|---|
| `AZURE_CLIENT_ID` | variable | the deploy identity's client ID, which the workflows sign in as |
| `AZURE_TENANT_ID` | secret | the Entra tenant, from the owner's sign-in |
| `AZURE_SUBSCRIPTION_ID` | secret | the subscription |
| `TFSTATE_STORAGE_ACCOUNT` | variable | the name of the state storage account |
| `APP_IDENTITY_ID` | variable | the app identity's resource ID |
| `APP_IDENTITY_CLIENT_ID` | variable | the app identity's client ID; the container gets it as `AZURE_CLIENT_ID` |
| `AZURE_OPENAI_BASE_URL` | variable | `https://aoai-releaselens-<suffix>.openai.azure.com/openai/v1/` |
| `AZURE_OPENAI_DEPLOYMENT` | variable | `releaselens-chat` |

All eight are identifiers, not credentials, so no output is marked sensitive. The six variables
appear unmasked in public run logs, on purpose. The tenant and subscription IDs are secrets only
so that GitHub masks them in those logs. GitHub prints variable values in each step's header
before any masking step could run (spec §4.12, amended 2026-09-30). `SMOKE_OPEN_RUNNER_IP`, a
seventh variable, is set by hand, and only if the smoke test cannot reach Postgres.

## Terraform runs in WSL

On the owner's Windows machine, TLS inspection software intercepts Terraform's local connection
to its provider plugins. Every provider then fails with
`x509: certificate signed by unknown authority`. That does not happen in Ubuntu under WSL. So
every Terraform command for both stacks runs there, with Terraform 1.15.8, and so does the
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
only in the git-ignored `.env`.

### R4. Confirm the ignore rules

*Changes nothing.* Bootstrap's local state holds the state storage account's keys, and this
repository is public. PowerShell:

```powershell
git check-ignore -v infra/bootstrap/terraform.tfvars infra/bootstrap/backend_override.tf infra/bootstrap/terraform.tfstate infra/bootstrap/tfplan
```

All four paths must be listed.

### R5. The budget first

*Changes the subscription; bills nothing.* Even the plan registers the seven resource providers,
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
new federated credential, restore the file from git history, and put it back in `ALLOWED_AZURE`
and `TRIGGERS`:

```powershell
$removed = git log --format=%h -1 -- .github/workflows/oidc-probe.yml   # the commit that removed it
git checkout "$removed^" -- .github/workflows/oidc-probe.yml
```

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
- capacity `100`

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
$secrets = 'AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID'
foreach ($v in $values.PSObject.Properties) {
  if ($v.Name -in $secrets) { gh secret set $v.Name --env azure --body $v.Value }
  else { gh variable set $v.Name --env azure --body $v.Value }
}
gh variable list --env azure
gh secret list --env azure
```

The variable list must show exactly the six variables in [Outputs](#outputs), and the secret
list exactly `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`. GitHub never shows a secret's value
again, so only the secrets' names can be read back. GitHub holds no credentials: all eight are
identifiers.

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
  - a workflow names the environment and is not `deploy.yml` or `destroy.yml`
  - one of those two gains a trigger it should not have
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
