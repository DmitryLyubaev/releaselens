# Functions stack

This is a short-lived stack for the functions sessions. It holds two Azure Function apps on Flex
Consumption, the ingest app and the search tool app, and publishes the tool on the gateway as
an MCP API. Both apps are keyless: their host storage, their deployment packages, the queue and
every model and search call use each app's own managed identity. The owner applies it locally
in a session, after the bootstrap, the gateway and the search stack, and destroys it at the end.
CI never applies it. The design and its reasons are in the
[functions spec](../../docs/superpowers/specs/2026-10-10-functions-ingestion-and-search-tool-design.md),
§3.3, §5.2, §8 and §9.

**Not yet applied.** This page describes what the code configures.

**Nothing here bills while idle:** Flex Consumption bills executions and the memory they use,
and no always-ready instance is configured. The gateway the tool is published on does bill by
the hour, so the session still ends with every stack destroyed.

## What it holds

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-releaselens-functions` | its own group: in `rg-releaselens`, the nightly destroy's empty-group check would find the apps and fail |
| Plan | `asp-releaselens-functions` | Flex Consumption (`FC1`), Linux, `australiaeast` |
| Ingest app (azapi) | `func-releaselens-ingest-<suffix>` | `Microsoft.Web/sites`, `functionapp,linux`; .NET 10 isolated; 2,048 MB instances, none always ready; the ingest identity, user-assigned; deploys from `deploy-ingest` in its own host account |
| Tool app (azapi) | `func-releaselens-tool-<suffix>` | the same, with the tool identity and `deploy-tool` in its own host account |
| Basic publishing credentials (azapi) | `scm` and `ftp` on each app | `allow = false`: no username and password for the SCM (Kudu) or FTP endpoints |
| Authentication (azapi) | `authsettingsV2` on each app | every route requires an Entra token: 401 without one; the tenant's v2 issuer; audiences the search tool's identifier URI and client ID; allowed application the gateway identity |
| Named values (azapi) | `tool-tenant-id`, `tool-gateway-app-client-id`, `tool-app-audience`, `tool-gateway-identity-client-id` | on the gateway's service; what the policy refers to as `{{name}}`; none secret |
| MCP API (azapi) | `releaselens-search` | on the gateway's service, `2025-09-01-preview`, type `mcp`, path `releaselens-search`, HTTPS only, `subscriptionRequired = false`; Streamable HTTP passed through to the tool app's `/runtime/webhooks/mcp` |
| Policy (azapi) | [`policies/mcp-api.xml`](policies/mcp-api.xml) | loaded with `file()`, format `xml` |
| Diagnostic (azapi) | `applicationinsights` on the MCP API | through the gateway's `appi-releaselens` logger; every request sampled, no payload in front or behind, no client IP; metrics on, so `emit-metric` publishes |

`<suffix>` is six random lowercase letters and digits, new each session, because an app's name
is its host and must be unique across Azure.

Each app's settings:

| Setting | Ingest app | Tool app |
|---|---|---|
| `AzureWebJobsStorage__accountName` | its own host account | its own host account |
| `AzureWebJobsStorage__credential` | `managedidentity` | `managedidentity` |
| `AzureWebJobsStorage__clientId`, `AZURE_CLIENT_ID` | the ingest identity | the tool identity |
| `OPENAI_BASE_URL` | the Azure OpenAI account's v1 URL | the same |
| `OPENAI_EMBEDDING_DEPLOYMENT` | `releaselens-embed-small` | the same |
| `SEARCH_ENDPOINT`, `SEARCH_INDEX` | the search stack's endpoint, `releaselens-chunks` | the same |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | the bootstrap's, sensitive | the same |
| `APPLICATIONINSIGHTS_AUTHENTICATION_STRING` | `ClientId=<ingest identity>;Authorization=AAD` | `ClientId=<tool identity>;Authorization=AAD` |
| `IngestQueue__queueServiceUri`, `__credential`, `__clientId` | the ingestion account's queue service, as the ingest identity | none |
| `INGEST_BLOB_ENDPOINT` | the ingestion account's blob service | none |

- **No storage connection string anywhere.** The apps are `Microsoft.Web/sites` through azapi,
  in the shape of Microsoft's Flex MCP sample, because azurerm's Flex resource injects an
  `AzureWebJobsStorage` connection string, which takes precedence over the identity-based
  settings and fails on accounts with shared keys off (azurerm #29693, #33211, #30732).
- **Code deploys with the owner's Entra sign-in.** Basic publishing credentials (SCM and FTP)
  are off on both apps, so no deployment password exists; the packages go to each app's
  deployment container with the owner's data role on it.
- **The one sensitive setting** is Application Insights' connection string, which carries the
  instrumentation key. It is marked sensitive, so a plan shows every other setting and not it.
  Local authentication is off on Application Insights, so ingestion uses Entra ID, as each app's
  identity.
- **Least access.** Each app's host storage and deployment container are in a host account of
  its own, which its identity alone has host roles on. The ingest app reads artefacts and works
  its queues on the ingestion account. The tool app has no role on the ingestion account.
- **Reachable from the gateway alone.** App Service authentication refuses any call without a
  token from the gateway identity for the search tool's registration, before the Functions host
  sees it. The MCP extension's own key is off (`webhookAuthorizationLevel = "Anonymous"` in the
  tool's `host.json`), so this is the tool's only lock. The ingest app has no HTTP function; the
  setting closes its runtime endpoints too.
- **No payload logged.** The MCP API's diagnostic records no request or response body: reading
  one would buffer Streamable HTTP, and no query or result leaves the gateway in a log.
- **Identifiers come from the other stacks' state**, read through `terraform_remote_state`, so
  no tenant, client ID or account name passes through a file.
- **Nothing is registered from here, and no role is granted.** Bootstrap registers
  `Microsoft.Web` and holds every role the apps use; the search stack holds the two search roles.
- **No `prevent_destroy`.** The stack is meant to be destroyed.

Its state is in the container `tfstate-functions` of the state storage account, under the key
`functions.tfstate`. Bootstrap creates that container and gives the owner `Storage Blob Data
Contributor` on it.

## Inputs

| Variable | Value | Default |
|---|---|---|
| `subscription_id` | the subscription | none |
| `tfstate_storage_account` | the state storage account: `terraform -chdir=../bootstrap output -raw tfstate_storage_account` | none |
| `location` | the region | `australiaeast` |

Put them in a git-ignored `terraform.tfvars` in this folder, in your own window, starting from
[`terraform.tfvars.example`](terraform.tfvars.example). Never commit them.

## Apply and destroy

The order is the bootstrap, the gateway and the search stack, then this one, because it reads
all three states and publishes the tool on the gateway. Terraform runs in WSL, as
[`../bootstrap`](../bootstrap/README.md#terraform-runs-in-wsl) describes, with its own data
directory:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/functions   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/functions-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
terraform init -backend-config=storage_account_name=<state storage account>
az group exists --name rg-releaselens-functions   # must print false
terraform plan -out=tfplan
terraform apply tfplan
terraform output -raw tool_mcp_url
terraform output -raw tool_app_url
```

The apps start with no code. The runbook deploys both packages with the owner's Entra sign-in
(basic authentication is off), to `ingest_app_name` and `tool_app_name`.

At the end of the session, destroy this stack **before** the gateway and the search stack:

```bash
terraform destroy
az group exists --name rg-releaselens-functions   # must print false
```

If the gateway was destroyed first, the MCP API, its policy, diagnostic and named values went
with the service, and this destroy finds them gone.

## Live checks

The mocked tests cannot show these:
- **The MCP path split.** The service URL ends at `/runtime/webhooks` and the API's one endpoint
  is `/mcp`, the shape the research found for a pass-through, so a call to
  `https://<gateway host>/releaselens-search/mcp` should reach the tool app's
  `/runtime/webhooks/mcp`. Microsoft shows neither this split nor the other (service URL the
  app's root, endpoint `/runtime/webhooks/mcp`). If `initialize` through the gateway gets a 404
  from the app, change the split and apply again.
- **App Service authentication on Flex accepts the gateway identity's token**, with the v2
  issuer and the client ID as audience, and refuses a call with no token or with a token from
  anyone else (T3).
- **The MCP API takes this diagnostic**: `metrics` on and `largeLanguageModel.logs` off on an
  API of type `mcp`, and `Tool Calls` then appears in `customMetrics`.
- **The site body.** The app starts with identity-based host storage on an account with shared
  keys off, and `maximumInstanceCount`, left out, takes the service's default.
- **Basic authentication off does not block the deploy**: the owner's Entra sign-in deploys both
  packages with SCM and FTP basic credentials disallowed.
- **The policy round-trips.** API Management may return the policy's XML normalised, so azapi
  shows an in-place update of it on later plans, writing the same file again. Harmless.

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests plan against mocked providers and placeholder outputs of the three stacks, and never
apply or destroy anything. CI runs them with `fmt` and `validate` on every pull request to
`main`.
