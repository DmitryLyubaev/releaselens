# App stack

This is the half of ReleaseLens's Azure infrastructure that is deployed and destroyed every
session. It holds the Container App and Postgres, and nothing long-lived. The long-lived half is
[`../bootstrap`](../bootstrap/README.md). It holds the identities, the Azure OpenAI account, the
state storage, the budget and every role assignment.

**Nothing described here has been deployed yet.**

## What it holds

| Resource | Name | Notes |
|---|---|---|
| Container Apps environment | `cae-releaselens` | no Log Analytics workspace; logs stream with `az containerapp logs show` |
| Container App | `ca-releaselens-api` | runs as the user-assigned app identity only; image pinned by digest; 0 to 2 replicas, scaling at 10 concurrent requests; external ingress on port 8080; `/health` liveness and readiness probes; the connection string is in the secret `database-connection` |
| Postgres Flexible Server | `psql-releaselens-<suffix>` | version 16, `B_Standard_B1ms`, 32 GB, 7-day backups, public network access; administrator `releaselens` with a generated 32-character password |
| Database | `releaselens` | UTF8 |
| Server configuration | `azure.extensions` | allow-lists `VECTOR,PG_TRGM` |
| Firewall rule | `allow-azure-services` | `0.0.0.0`, which admits Azure services; see "What this is not" in the [root README](../../README.md#what-this-is-not) |
| Firewall rule | `allow-smoke-runner` | exists only while `smoke_runner_ip` is set |

The stack holds none of these: a resource group, a role assignment, a Key Vault, a budget, a Log
Analytics workspace, or an Anthropic or OpenAI key. It registers no resource providers, because
CI may not; bootstrap registers them. It reads nothing from bootstrap: there is no
`terraform_remote_state` and no data source for a bootstrap resource.

### The container's environment

| Name | Value |
|---|---|
| `RELEASELENS_DB` | from the secret `database-connection` |
| `ASPNETCORE_ENVIRONMENT` | `Production` |
| `Chat__Providers__0` | `azure-openai` |
| `AzureOpenAi__BaseUrl` | `var.azure_openai_base_url` |
| `AzureOpenAi__Deployment` | `var.azure_openai_deployment` |
| `AzureOpenAi__Credential` | `ManagedIdentity` |
| `AzureOpenAi__Model` | `var.azure_openai_model` |
| `AzureOpenAi__ModelVersion` | `var.azure_openai_model_version` |
| `AzureOpenAi__DeploymentType` | `var.azure_openai_deployment_type` |
| `AZURE_CLIENT_ID` | `var.app_identity_client_id` |

In Azure the app uses Azure OpenAI only. It authenticates as the app identity, with no key, and
the Anthropic and OpenAI keys never reach Azure. `AZURE_CLIENT_ID` is always the app identity's
client ID, never the deploy identity's.

The model, model version and deployment type are the app's pricing identity: it looks up its
rate by them, and Azure never sees them. They must describe the deployment that bootstrap
created.

## Inputs

| Variable | Comes from | Default |
|---|---|---|
| `subscription_id` | `AZURE_SUBSCRIPTION_ID` | none |
| `app_identity_id` | `APP_IDENTITY_ID` | none |
| `app_identity_client_id` | `APP_IDENTITY_CLIENT_ID` | none |
| `azure_openai_base_url` | `AZURE_OPENAI_BASE_URL` | none |
| `azure_openai_deployment` | `AZURE_OPENAI_DEPLOYMENT` | none |
| `image` | on deploy, the digest the preflight job resolves | an all-zero digest, valid only for destroy |
| `smoke_runner_ip` | the runner's public IP, only when `SMOKE_OPEN_RUNNER_IP` is `true` | `""` |
| `resource_group_name` | | `rg-releaselens` |
| `location` | | `australiaeast` |
| `azure_openai_model` | | `gpt-4.1-mini` |
| `azure_openai_model_version` | | `2025-04-14` |
| `azure_openai_deployment_type` | | `GlobalStandard` |
| `postgres_admin_username` | | `releaselens` |

The first five come from the GitHub environment `azure`, passed to Terraform as `TF_VAR_*`:
`subscription_id` from its secret `AZURE_SUBSCRIPTION_ID`, and the four handoff values from its
variables. The owner copied them into the environment once, from bootstrap's output
`github_environment_variables`.

`image` must be `ghcr.io/dmitrylyubaev/releaselens-api@sha256:` followed by 64 lowercase hex
characters. A tag such as `:latest` fails validation. So the image that was checked is the image
that runs, and a re-apply always rolls out the image it names.

## Outputs

| Output | Value |
|---|---|
| `api_url` | the app's public HTTPS URL |
| `database_connection_string` | the Postgres connection string, including the password; `sensitive` |
| `pricing_identity` | `model`, `model_version` and `deployment_type`, which the smoke test uses to recompute the cost |

## State

The backend is `azurerm`, with container `tfstate-app`, key `app.tfstate` and Entra ID
authentication (`use_azuread_auth = true`). The storage account's name is passed at `init`:

```bash
terraform init -backend-config=storage_account_name=<TFSTATE_STORAGE_ACCOUNT>
```

Only the owner and the deploy identity can read or write this state, and it holds the Postgres
password.

## Deployed and destroyed by the workflows

Deploys and destroys go through GitHub Actions, signed in through OIDC as the deploy identity.
Their design is in the [spec](../../docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md)
(§4.12, §6.1). Neither workflow has run yet.
- **[`deploy.yml`](../../.github/workflows/deploy.yml)** is dispatched by hand, from `main`, under
  the environment `azure`.
  1. Its `preflight` job refuses to continue while `destroy.yml` is disabled. It resolves the
     image tag `sha-<commit>` to its digest in GHCR, and fails if `ci.yml`'s `publish-image` job
     has not pushed that tag yet.
  2. The `deploy` job applies this stack with the image pinned to that digest.
  3. It runs the Worker's `migrate`, `create-tenant` and `issue-key` against the deployed
     Postgres. The connection string, its password and the issued key are masked in the log
     before anything else can print them.
  4. It runs `scripts/deploy_tools.py smoke` against the `api_url` output. The smoke test
     recomputes the answer's cost with the Worker's `price` command, for the model, version and
     deployment type in the `pricing_identity` output.
- **[`destroy.yml`](../../.github/workflows/destroy.yml)** is dispatched by hand, and runs nightly
  at 14:00 UTC as a best-effort safety net. It destroys this stack with the placeholder image.
  Then `scripts/deploy_tools.py check-empty` fails, naming each resource, if anything is left in
  `rg-releaselens`.
- **Both** authenticate with `ARM_USE_OIDC` and the `ARM_*` variables, with no stored
  credential. They read the tenant and subscription IDs from the environment's secrets. Both use
  `-lock-timeout=10m` and share one concurrency group.

Dispatch them from PowerShell:

```powershell
gh workflow run deploy.yml --ref main
gh workflow run destroy.yml --ref main
```

Whether GitHub-hosted runners pass `allow-azure-services` is unverified. If the smoke test
cannot reach Postgres, set the environment variable `SMOKE_OPEN_RUNNER_IP` to `true` and deploy
again:

```powershell
gh variable set SMOKE_OPEN_RUNNER_IP --env azure --body true
```

The deploy job then reads its public IPv4 address from `https://api.ipify.org`, and applies with
`smoke_runner_ip` set to it before the smoke test. A final step applies again with it empty, and
runs even if the smoke test fails.

## Running it locally

The owner does not deploy or destroy this stack by hand. If the owner ever runs it locally, for
example to inspect a plan or repair state, the plan is interactive and reviewed, never
`-auto-approve`. The reason is that CI can rewrite this state. In WSL (see the
[bootstrap README](../bootstrap/README.md#terraform-runs-in-wsl) for why):

```bash
cd /mnt/e/Projects/ReleaseLens/infra/terraform   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/terraform" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
export TF_VAR_subscription_id=<AZURE_SUBSCRIPTION_ID> TF_VAR_app_identity_id=<APP_IDENTITY_ID> \
  TF_VAR_app_identity_client_id=<APP_IDENTITY_CLIENT_ID> TF_VAR_azure_openai_base_url=<AZURE_OPENAI_BASE_URL> \
  TF_VAR_azure_openai_deployment=<AZURE_OPENAI_DEPLOYMENT>
terraform init -backend-config=storage_account_name=<TFSTATE_STORAGE_ACCOUNT>
terraform plan -lock-timeout=10m -out=app.tfplan
```

Read the plan before you apply anything. A planned deletion of anything other than the Container
Apps resources or Postgres is treated as tampering. Stop, and restore an earlier blob version of
`app.tfstate` in `tfstate-app`. Only a plan that holds no such deletion is applied, with
`terraform apply -lock-timeout=10m app.tfplan`. Leave `TF_VAR_image` unset only for a destroy:
the placeholder digest names no image.

## Cost

Postgres bills by the hour while it exists. The Container App scales to zero. What stops the
Postgres bill is destroying the stack at the end of every session. Bootstrap's budget only alerts,
and stops no consumption.

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests in `tests/` plan against a mocked provider, and never apply or destroy anything.
