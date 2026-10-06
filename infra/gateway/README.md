# Gateway stack

This is a short-lived stack for the AI gateway sessions. It holds Azure API Management, Basic v2,
in front of the two Azure OpenAI accounts the bootstrap creates: one API, keyless both ways. A
caller brings an Entra token with the `Gateway.Invoke` role, and the gateway calls the models
with its own identity. The owner applies it locally at the start of a session, and destroys it
at the end. CI never applies it. The design and its reasons are in the
[gateway spec](../../docs/superpowers/specs/2026-10-06-apim-ai-gateway-design.md), §3.2, §4
and §8.

**Not yet applied.** This page describes what the code configures.

**It bills by the hour, called or not:** about US$0.21 an hour for Basic v2 (US$0.20548, Retail
Prices API, 2026-10-06), so about US$5 a day if it is forgotten. **Destroy it at the end of every
session.** The budget alert is the backstop, and it stops nothing.

## What it holds

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-releaselens-gateway` | its own group: in `rg-releaselens`, the nightly destroy's empty-group check would find the service and fail |
| API Management | `apim-releaselens-<suffix>` | `BasicV2_1`, `australiaeast`; the bootstrap's gateway identity, user-assigned, and no other |
| Named values | `tenant-id`, `gateway-app-client-id`, `gateway-identity-client-id`, `tokens-per-minute`, `tokens-per-day`, `primary-backend-host` | what the policies refer to as `{{name}}`; none secret |
| Version set and API | `azure-openai`, `azure-openai-v1` | segment scheme, version `v1`, path `openai`, HTTPS only, `subscription_required = false`; one operation, `POST /chat/completions` |
| Policies | [`policies/api-v1.xml`](policies/api-v1.xml), [`policies/api-v1-rev2.xml`](policies/api-v1-rev2.xml) | loaded with `file()`; revision 1 and revision 2 |
| Revision 2 | `azure-openai-v1;rev=2` | non-current until `release_revision_2 = true` |
| Backends (azapi) | `aoai-primary`, `aoai-secondary`, `aoai-pool` | two Single backends, each with one breaker rule (one 429 in 5 minutes trips it for 1 minute, or for the Retry-After); the pool, primary priority 1, secondary priority 2 |
| Logger and diagnostic | `appi-releaselens`, `applicationinsights` | to the bootstrap's Application Insights, ingesting as the gateway identity; every request sampled, no body, no client IP; custom metrics on (azapi) |

`<suffix>` is six random lowercase letters and digits, new each session, so the 48 hours a
deleted service's name stays reserved never block the next one.

Clients call `https://<gateway host>/openai/v1/chat/completions`, and revision 2 at
`https://<gateway host>/openai/v1;rev=2/chat/completions`. The outputs are the two gateway-mode
settings (spec §5):
- `gateway_base_url`, `https://<gateway host>/openai/v1/`
- `gateway_scope`, `api://<gateway app client id>/.default`

- **Keyless both ways.** No product, no subscription, no subscription key. Callers are checked by
  their Entra token alone, and the gateway calls the models as the gateway identity, whose roles
  the bootstrap holds. This stack creates no role assignment.
- **Public endpoint.** The gateway is reachable from anywhere, protected by the Entra token
  alone, with no IP restriction (spec §8).
- **No prompts logged.** The diagnostic records no request or response body.
- **Identifiers come from the bootstrap's state**, read through `terraform_remote_state`, so no
  tenant, client ID or account host passes through a file.
- **Purged on destroy.** The provider purges the soft-deleted service, and never recovers an old
  one on create.
- **Nothing is registered from here.** Bootstrap registers `Microsoft.ApiManagement`.
- **No `prevent_destroy`.** The stack is meant to be destroyed.

Its state is in the container `tfstate-gateway` of the state storage account, under the key
`gateway.tfstate`. Bootstrap creates that container and gives the owner `Storage Blob Data
Contributor` on it, so the bootstrap must be applied first.

## Inputs

Besides `subscription_id`, which every stack takes, the stack needs two inputs that stay out of
git:

| Variable | Value | Default |
|---|---|---|
| `subscription_id` | the subscription | none |
| `tfstate_storage_account` | the state storage account: `terraform -chdir=../bootstrap output -raw tfstate_storage_account` | none |
| `publisher_email` | where API Management sends its service notifications | none |
| `location` | the region | `australiaeast` |
| `tokens_per_minute` | each caller's budget per minute | `10000` |
| `tokens_per_day` | each caller's budget per day | `50000` |
| `release_revision_2` | make revision 2 current | `false` |

Put them in a git-ignored `terraform.tfvars` in this folder, in your own window, starting from
[`terraform.tfvars.example`](terraform.tfvars.example). Never commit them.

## Apply and destroy

Terraform runs in WSL, as [`../bootstrap`](../bootstrap/README.md#terraform-runs-in-wsl)
describes, with its own data directory:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/gateway   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/gateway" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
terraform init -backend-config=storage_account_name=<state storage account>
```

At the start of a session, check that no group is left from an earlier one. This must print
`false`:

```bash
az group exists --name rg-releaselens-gateway
```

Then plan, read the plan, and apply it. Basic v2 takes several minutes to create:

```bash
terraform plan -out=tfplan
terraform apply tfplan
terraform output -raw gateway_base_url
terraform output -raw gateway_scope
```

Once revision 2 has passed the smoke test, make it current:

```bash
terraform plan -var release_revision_2=true -out=tfplan
terraform apply tfplan
```

That plan adds the release, and replaces revision 2's policy with the same file: the provider
reads the policy's API name back without its `;rev=2`. Every plan after the first shows that
replacement. After the release, the next step is the destroy: revision 1's policy resource
addresses the current revision, so another apply would write revision 1's policy over
revision 2's.

At the end of the session, whether it succeeded or not:

```bash
terraform destroy
az group exists --name rg-releaselens-gateway
az apim deletedservice list --query "[?starts_with(name, 'apim-releaselens-')].name" -o tsv
```

The first check must print `false`, and the second nothing.

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests plan against mocked providers and placeholder bootstrap outputs, and never apply or
destroy anything. CI runs them with `fmt` and `validate` on every pull request to `main`.
