# Gateway stack

This is a short-lived stack for the AI gateway sessions. It holds Azure API Management, Basic v2,
in front of the two Azure OpenAI accounts the bootstrap creates: one API, keyless both ways. A
caller brings an Entra token with the `Gateway.Invoke` role, and the gateway calls the models
with its own identity. The owner applies it locally at the start of a session, and destroys it
at the end. CI never applies it. The design and its reasons are in the
[gateway spec](../../docs/superpowers/specs/2026-10-06-apim-ai-gateway-design.md), §3.2, §4
and §8.

**Applied and destroyed once, on 9 October 2026,** for the measured session (the
[report](../../docs/gateway-report.md)). It is not running. This page describes what the code
configures.

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
| Policies (azapi) | [`policies/api-v1.xml`](policies/api-v1.xml), [`policies/api-v1-rev2.xml`](policies/api-v1-rev2.xml) | loaded with `file()`, format `xml`; each on its own revision (`;rev=1`, `;rev=2`), never on "whichever is current"; each file's marker comment becomes the Event Hubs log statement, or nothing (below) |
| Revision 2 | `azure-openai-v1;rev=2` | non-current until `release_revision_2 = true` |
| Backends (azapi) | `aoai-primary`, `aoai-secondary`, `aoai-pool` | two Single backends, each with one breaker rule (one 429 in 5 minutes trips it for 1 minute, or for the Retry-After); the pool, primary priority 1, secondary priority 2 |
| Logger and diagnostic | `appi-releaselens`, `applicationinsights` | to the bootstrap's Application Insights, ingesting as the gateway identity; every request sampled, no body, no client IP; custom metrics on (azapi) |
| Event Hubs logger and its named values | `p10-eventhub`; `caller-labels` (secret), `p10-session` | only with `event_hub_logging = true` ([below](#event-hubs-logging-project-10)); none of them exists by default |

`<suffix>` is six random lowercase letters and digits, new each session, so the 48 hours a
deleted service's name stays reserved never block the next one.

Clients call `https://<gateway host>/openai/v1/chat/completions`, and revision 2 at
`https://<gateway host>/openai/v1;rev=2/chat/completions`. The outputs are the two gateway-mode
settings (spec §5):
- `gateway_base_url`, `https://<gateway host>/openai/v1/`
- `gateway_scope`, `api://<gateway app client id>/.default`

Three more are for the [functions stack](../functions/README.md), which reads them from this
stack's state to publish the search tool on the service: `api_management_id`, `gateway_url`
(`https://<gateway host>`) and `app_insights_logger_id`.

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
| `event_hub_logging` | send one event per call to Event Hubs ([below](#event-hubs-logging-project-10)) | `false` |
| `event_hub_namespace` | the Event Hubs namespace's name, not its host; required when logging is on | `""` |
| `event_hub_name` | the event hub's name; required when logging is on | `""` |
| `p10_session` | the session name every event carries, for example `p10-2026-11-12`; required when logging is on, never `replay` | `""` |
| `caller_labels_json` | sensitive: a JSON object mapping each known caller's object ID to `owner`, `deploy`, `webapp` or `other` | `"{}"` |

Put them in a git-ignored `terraform.tfvars` in this folder, in your own window, starting from
[`terraform.tfvars.example`](terraform.tfvars.example). Never commit them.

## Apply and destroy

Terraform runs in WSL, as [`../bootstrap`](../bootstrap/README.md#terraform-runs-in-wsl)
describes, with its own data directory:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/gateway   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/gateway-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
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

That plan should add the release and change nothing else, apart from the policy updates that
Live checks describes, if they occur. The release changes which revision is current, not what
either holds: each policy is pinned to its own revision, so a later apply leaves revision 2's
policy on revision 2 and revision 1's on revision 1. The operation and the diagnostic address
the current revision by name, so after the release they refer to revision 2's copies; if a later
plan shows either of them created or replaced, revision 2 did not inherit it, and the plan says
what the apply would do. After the release there is no further apply until the destroy: the
measured commands and the report call the gateway, and apply no Terraform.

At the end of the session, whether it succeeded or not:

```bash
terraform destroy
az group exists --name rg-releaselens-gateway
az apim deletedservice list --query "[?starts_with(name, 'apim-releaselens-')].name" -o tsv
```

The first check must print `false`, and the second nothing.

If revision 2 was released, it is the current revision when the destroy reaches
`azurerm_api_management_api.v1_rev2`, and API Management may refuse to delete the current
revision. If the destroy stops on that resource, remove it from the state, with any of its
children the error names, and destroy again:

```bash
terraform state rm 'azurerm_api_management_api.v1_rev2'
terraform destroy
```

That loses nothing: deleting the service deletes every revision, policy and backend inside it.
Then run the two checks above.

## Live checks

The mocked tests cannot show these. The smoke test settles the first two, and the destroy the
third:
- **Revision 2 inherits the diagnostic.** Revision 2 is copied after the diagnostic and its
  metrics switch exist, and before either policy. A call at `;rev=2` must appear in Application
  Insights, and its tokens in the `releaselens-gateway` metric. **If it does not** (the call is
  missing from `requests`, or its tokens from `customMetrics`), revision 2 did not inherit the
  diagnostic or its `metrics = true`: do not set `release_revision_2`, and run the measured steps
  on revision 1, the current revision. The runbook's step 3, point 4 says so too, and the
  findings table records it.
- **The policies round-trip.** API Management may return a policy's XML normalised: different
  quoting or indentation, or without the leading comment. That is expected. azapi then shows an
  in-place update of both policies on every plan after the first, the release's included, and
  that update only writes the same file to the same revision again. It is harmless, not a fault.
- **The destroy after a release.** Whether API Management deletes the current revision 2 with
  the rest, or the destroy stops on it and needs the fallback above.

## Event Hubs logging (project 10)

For the Fabric real-time session (project 10, in the Fabric repository), the gateway can send one
event per call to an event hub. It is **off by default**: with `event_hub_logging = false`, the
Event Hubs logger and its two named values do not exist, and each policy is its file with the
marker comment `<!-- p10:log-to-eventhub -->` removed, so every other session applies as before.
The one other change to the files is inert: `llm-token-limit` names the variable it keeps the
call's token count in (`tokensConsumed`), which nothing reads while logging is off.

With `event_hub_logging = true`:
- [`policies/log-to-eventhub.xml`](policies/log-to-eventhub.xml) takes the place of the marker at
  the end of `outbound` (after the backend label) and at the end of `on-error`, in both revisions,
  so calls the gateway refuses are logged too. `api.tf` does the replacement with the statement
  alone, from its opening tag, so the file's header comment stays in the file; the policy tests
  render both revisions both ways.
- **Telemetry never changes an answer.** An exception in the statement would send an answered
  call to `on-error` (a 502), and the `on-error` copy would throw again, so a refusal would lose
  its status. The two steps that could throw are guarded: a `caller-labels` value that does not
  parse, or holds anything but `owner`, `deploy` or `webapp`, gives `other`; `tokens` is null
  unless the count is an `int`, or a `long` that fits in an `int`.
- The logger `p10-eventhub` (azurerm; the pinned provider's `eventhub` block takes a
  user-assigned identity) sends to `<namespace>.servicebus.windows.net` as the gateway identity.
  The namespace has local authentication off, so there is no connection string. The Fabric
  repository's Event Hubs stack gives the gateway identity **Azure Event Hubs Data Sender** on the
  hub; this stack still grants no role, so that stack must be applied first.
- `caller-labels` is a secret named value built from `caller_labels_json`, and `p10-session` holds
  `p10_session`.

Each event is one line of JSON with exactly these fields: `event_time` (UTC), `request_id` (API
Management's request ID), `session`, `api`, `operation`, `caller`, `answered_by`, `status`,
`refused_by_gateway`, `tokens` and `latency_ms`. There is no client IP, no request or response
body, and no deployment or model. `caller` is a label: the caller's object ID is looked up in
`caller-labels`, and a call with no token, or an ID not in the map, is `other`, so no object ID
leaves the gateway. `answered_by` is the backend label (`primary` or `secondary`), and null when no
backend answered. A call with no backend answer and status 401, 403 or 429 is the gateway's own
refusal (`refused_by_gateway` true); any other status without an answer (a 502 or 503 from a
backend failure) is not a refusal. With no answer, `tokens` is null.

What the gateway can observe about 429s is its own refusals and the calls the secondary answered:
the pool's retry hides the primary's 429 inside one request, so there is no event for it.

The namespace and hub names, the session and the object IDs go in the git-ignored
`terraform.tfvars`, never in a committed file:

```hcl
event_hub_logging   = true
event_hub_namespace = "<namespace name>"
event_hub_name      = "<event hub name>"
p10_session         = "p10-<date>"
caller_labels_json  = "{\"<owner object id>\": \"owner\", \"<deploy object id>\": \"deploy\", \"<webapp object id>\": \"webapp\"}"
```

The first apply after this change merges shows an in-place update of both policies even with
logging off, because the marker comment is removed from what the gateway holds; it is harmless.

Switching it on or off is an in-place update of both policies, plus the logger and the named
values created or destroyed. The mocked tests cannot show these; check them in the session:
- an answered call and a refused call (no token, a 401) each arrive in the hub as one event;
- `tokens` holds a number on an answered call (`llm-token-limit` sets `tokensConsumed`; if it is
  null on every answered call, the variable holds a type the guard does not take, and the session
  still runs, without token counts);
- `caller` is `owner`, `deploy` or `webapp` for the known callers (if every event says `other`,
  the `caller-labels` value did not parse in the gateway).

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests plan against mocked providers and placeholder bootstrap outputs, and never apply or
destroy anything. CI runs them with `fmt` and `validate` on every pull request to `main`.

The policies' structure, rendered with Event Hubs logging off and on, is checked from the
repository root by `python -m pytest tests/infra/test_gateway_policies.py`.
