# Search stack

This is a short-lived stack for the retrieval benchmark. It holds one Azure AI Search service,
which the benchmark's S2 and S3 arms query. The owner applies it locally at the start of a
measurement session, and destroys it at the end. CI never runs it. The design and its reasons are
in the [benchmark spec](../../docs/superpowers/specs/2026-10-02-azure-ai-search-benchmark-design.md),
§6.2–§6.4, and the session's steps are in [`eval/retrieval/README.md`](../../eval/retrieval/README.md).

**Not yet applied.** This page describes what the code configures.

## What it holds

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-releaselens-search` | its own group: in `rg-releaselens`, the nightly destroy's empty-group check would find the service and fail |
| Search service | `srch-releaselens-<suffix>` | SKU `basic`, 1 replica, 1 partition, `australiaeast`; `local_authentication_enabled = false`; `semantic_search_sku = "free"` |
| Role assignment | `owner_service_contributor` | the owner, `Search Service Contributor`, on the service only: to manage the index |
| Role assignment | `owner_index_data_contributor` | the owner, `Search Index Data Contributor`, on the service only: to load and query |
| Role assignment | `ingest_index_data_contributor` | the ingest identity (`id-releaselens-ingest`), `Search Index Data Contributor`, on the service only: to write each artefact's chunks and delete its stale ones |
| Role assignment | `tool_index_data_reader` | the tool identity (`id-releaselens-tool`), `Search Index Data Reader`, on the service only: to query, never to write |

`<suffix>` is six random lowercase letters and digits, generated on each apply. The output
`endpoint` is `https://srch-releaselens-<suffix>.search.windows.net`, which `build-index` and
`run-arms` take as `--endpoint`.

- **Keyless.** With key authentication off, only Entra ID works. The provider stores the
  service's keys in state, as it does for every search service; they authenticate nothing,
  because key authentication is off, and none is output or used.
- **The semantic ranker is on its free plan.** Past the monthly allowance (1,000 requests, read
  2026-10-02), ranked queries return a billing error instead of being billed (spec §6.3).
- **The role assignments live here, not in bootstrap.** The rule that bootstrap holds every role
  assignment keeps role-assignment rights away from CI. Only the owner applies this stack, and
  the owner holds those rights already. No other principal gets access: the owner and the two
  Function identities are the only ones.
- **The two identities are bootstrap's.** Their principal IDs come from the bootstrap's state
  (`ingest_identity_principal_id`, `tool_identity_principal_id`), read through
  `terraform_remote_state` as the [gateway stack](../gateway/README.md) reads its own. The
  bootstrap change that creates them must be applied before this stack.
- **Nothing is registered from here.** Bootstrap registers `Microsoft.Search`.
- **No `prevent_destroy`.** The stack is meant to be destroyed.

Its state is in the container `tfstate-search` of the state storage account, under the key
`search.tfstate`. Bootstrap creates that container and gives the owner `Storage Blob Data
Contributor` on it, so the bootstrap change must be applied first.

## Inputs

| Variable | Value | Default |
|---|---|---|
| `subscription_id` | the subscription | none |
| `owner_object_id` | the owner's Entra object ID: `az ad signed-in-user show --query id -o tsv` | none |
| `tfstate_storage_account` | the state storage account, to read the bootstrap's outputs: `terraform -chdir=../bootstrap output -raw tfstate_storage_account` | none |
| `location` | the region | `australiaeast` |

Put them in a git-ignored `terraform.tfvars` in this folder, in your own window. Never commit them.

## Apply and destroy

Terraform runs in WSL, as [`../bootstrap`](../bootstrap/README.md#terraform-runs-in-wsl)
describes, with its own data directory:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/search   # the clone, as WSL sees it; adjust to yours
export TF_DATA_DIR="$HOME/tfdata/search" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
terraform init -backend-config=storage_account_name=<state storage account>
```

At the start of a session, check that no group is left from an earlier one. This must print
`false`:

```bash
az group exists --name rg-releaselens-search
```

Then plan, read the plan, and apply it. The plan must add a resource group, a search service, a
random suffix and four role assignments, and nothing else:

```bash
terraform plan -out=tfplan
terraform apply tfplan
terraform output -raw endpoint
```

A new role assignment can take a few minutes to take effect. If `build-index` gets a `403`
straight after the apply, wait a few minutes and run it again.

At the end of the session, whether it succeeded or not:

```bash
terraform destroy
az group exists --name rg-releaselens-search
```

The check must print `false`. A forgotten Basic service costs US$3.19 a day (spec §8). The budget
alert is the backstop, and it stops nothing.

## Tests

```bash
terraform init -backend=false -input=false
terraform test
```

The tests plan against a mocked provider, and never apply or destroy anything. CI runs them with
`fmt` and `validate` on every pull request to `main`.
