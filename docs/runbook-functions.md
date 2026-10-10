# Runbook: the Azure Functions session

This is the owner's script for the live session of the Functions project. It applies the
infrastructure, deploys the two Function apps, runs the measured checks, records the Claude Code
demo, publishes the result and destroys everything that bills. The design and the rules that
decide each check are in the
[functions spec](superpowers/specs/2026-10-10-functions-ingestion-and-search-tool-design.md):
§7 (the checks and their pass rules), §9 (teardown), §11 (the delivery steps, one section each
below) and §12 (what is unverified, one findings row each). The stacks are described in
[`infra/functions/README.md`](../infra/functions/README.md),
[`infra/search/README.md`](../infra/search/README.md) and
[`infra/gateway/README.md`](../infra/gateway/README.md). This runbook points to their "Apply and
destroy" and "Live checks" sections and does not repeat them.

**Not yet run.** Every command below was written against the code and has not been run against
Azure. The findings table is empty until the session fills it.

## Rules

- **Every step marked "Ask first" waits for the owner's explicit yes.** The owner runs it, or
  approves each command before it runs. A yes for one step is not a yes for the next. The steps
  are the eight in spec §11, and there are more yeses inside them: the bootstrap, gateway, search
  and functions applies, the two code deploys, B1's burst (step 2), the uploads (step 6), the
  Claude Code configuration (step 7), the optional recreate check (step 7), the destroys
  (step 8) and the push (step 8).
- **The owner signs in.** The owner runs `az login` in each window, and signs into the Azure
  portal in the browser, with the personal account. Nothing in this runbook asks for a password,
  and no password, key or token is typed into a command or a file.
- **Before any `az` command,** run the account check (`acctok`, defined in "Before the session"),
  and stop unless it prints `True`. It prints nothing else: `az account show` would print the
  account's email, which this runbook's own no-identifier rule forbids on a screen. The check is
  `True` only when the signed-in user's name does not contain the work domain (the owner's
  employer's, not the personal one) and the signed-in tenant is the bootstrap's `tenant_id`. The
  owner holds the work domain in the user environment variable `WORK_DOMAIN`, set once in
  Windows' settings, outside every repository and never typed into a command. WSL sees it when
  `WSLENV` lists it (`WSLENV=WORK_DOMAIN`). If it is unset, the check prints `False`.
- **On Windows, `az` needs the Norton CA bundle for that process only.** Nothing is persisted.
  In the PowerShell window that runs `az`, the harness, `dotnet` and `claude`:

  ```powershell
  $env:REQUESTS_CA_BUNDLE = "$env:USERPROFILE\.azure\ca-bundle-with-norton.pem"
  ```

  The harness starts `az` itself to get tokens, and so does the Claude Code header helper in
  step 7, so each needs the same variable. WSL has its own `az` session (the
  [bootstrap README](../infra/bootstrap/README.md#r1-sign-in-inside-wsl) has R1), and needs no
  bundle.
- **Terraform runs only in WSL (Ubuntu),** with its own data directory for each stack:
  `TF_DATA_DIR=$HOME/tfdata/bootstrap-live`, `$HOME/tfdata/gateway-live`, `$HOME/tfdata/search`
  and `$HOME/tfdata/functions-live`. Never plan or apply from Windows.
- **Every PowerShell block that depends on the directory starts by setting it,** relative to the
  repository's root: `Set-Location (git rev-parse --show-toplevel)` for the root, and the same
  joined with `eval` for the harness. Run each block whole.
- **The harness runs from `eval/`,** in PowerShell, as `.venv/Scripts/python.exe -m app.functions
  <command>`, and writes to `reports/functions` (git-ignored). Its commands are in
  "The harness" below.
- **No identifier goes into a public file, a chat, a log or a screen.** That means no tenant,
  subscription, client or object ID, no hostname, no account or app name and no email. Outputs
  are read with `terraform output -raw` into the PowerShell process environment and never
  printed. The harness refuses to write a file that holds a GUID, an Azure hostname or any
  identifier it was given, and says which kind it found. If a command prints one anyway, do not
  paste it anywhere.
- **No key exists to handle.** If a step seems to need one, stop: something is wrong.
- **Write the findings in a scratch file, not in this runbook,** until the last measured command
  has run. Every measured harness command (`upload`, `ingest-checks`, `tool-checks`, and the
  gateway's `minute-budget`) refuses to start on a tree with uncommitted changes, and an edit to
  this file would be one. Use `eval/reports/findings-functions.md`, which git ignores, and copy
  it into the table in step 8.

### The clock

- **The Windows clock must be synchronised.** T4 reads the `Tool Calls` metric over the whole
  minute its burst starts in, with a margin of 10 seconds after it. A clock more than a few
  seconds off cuts the burst's metric rows out of that window. Check Settings, Time and
  language, Date and time: "Last successful time synchronisation" is recent. Synchronise there if
  not.
- **Nothing else calls the tool while T4 runs.** Its count is a lower bound over a whole minute,
  so another caller's tool call in that minute could hide a missing row. Claude Code is
  configured only in step 7, after T4, for this reason: a configured Claude Code session connects
  to the tool when it starts, and that handshake counts.
- **Leave a minute between the smoke test's tool calls and T1.** The gateway allows 20 tool
  requests a minute per caller, and T1 sends 13 (the handshake, the tool list and ten calls).
- **B1 needs a UTC day on which the owner's daily gateway budget is unspent.** Do not run step 2
  on a UTC day on which a gateway B2 ran.
- **No workflow is dispatched in this session,** so the gateway runbook's 13:05 to 14:30 UTC rule
  does not apply. The nightly `destroy.yml` touches only `rg-releaselens`.
- **The semantic ranker's free allowance is monthly** (1,000 ranked queries, spec §5.1). The
  session needs about 60 to 80 (the smoke test, T1's twenty, T4's burst, the demo). If T1's direct
  query fails with "a hit has no reranker score", or the tool answers `search failed`, suspect the
  allowance and write it down.

## What it costs

About US$1 for a session (spec §7, an estimate):

| Item | Cost |
|---|---|
| API Management, Basic v2 | about US$0.21 an hour (US$0.20548, Retail Prices API, 2026-10-06) from step 2 to step 8; about US$0.65 for 3 hours |
| AI Search, Basic | US$3.19 a day, about US$0.13 an hour, from step 3 to step 8; about US$0.40 for 3 hours |
| Embeddings and B1's chat tokens | cents: seven artefacts, about 80 queries, and B1's burst of about 18,000 tokens |
| Flex Consumption | effectively nothing at this volume, within the monthly free grant (spec §7) |
| The bootstrap additions | nothing by the hour: storage, Event Grid within its free operations, identities and app registrations |

**API Management bills by the hour from step 2, and AI Search from step 3, called or not.** If
the session stops for any reason, go to step 8. Forgotten, the two cost about US$8 a day. The
budget alert stops nothing.

## Before the session

All of this is free, and none of it calls Azure.

- The plan 1 pull request is merged to `main`, and the session works from an up-to-date `main`.
  The report cites the commit the checks ran from.
- `eval/.venv` exists and `.venv/Scripts/python.exe -m pytest -q`, run from `eval/`, passes.
- The .NET 10 SDK is installed, and `dotnet build ReleaseLens.sln` passes.
- `eval/retrieval-data/` holds project 2's `chunks.jsonl`, `releaselens-embed-small.npy` and
  `releaselens-embed-small.ids.json`. The corpus is never embedded again.
- The restored `releaselens_eval` database is running in Docker, as the
  [retrieval README](../eval/retrieval/README.md#before-every-step) describes, with WSL kept
  running by an open window.
- About 3 hours free.

### Choose and export the artefacts

The checks need seven real artefacts from the corpus, each exported by the Worker's
`export-artefacts` command into a folder of its own. A folder holds `<base64url key>.json`
files and a `manifest.json` that gives each file's expected chunk count, counted by the ingest
app's own chunker. The harness reads both.

| Check | Artefacts | Rule |
|---|---|---|
| I1 | five, held back from the bulk load | any type; one to three chunks each is enough; a mix of types is better |
| I2 | one, in the bulk load | at least four chunks in `chunks.jsonl`, so a shortened copy yields fewer |
| I3 | one, in the bulk load | not I2's, and not one of I1's (an upload reuses its file's name) |

To see candidates, from `eval/` (it prints public artefact IDs, nothing else):

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
@'
import collections, json, random
counts = collections.Counter(json.loads(line)["artefact"] for line in open("retrieval-data/chunks.jsonl", encoding="utf-8"))
rng = random.Random()
for kind in ("commit", "issue", "pull_request", "release"):
    few = sorted(a for a, n in counts.items() if a.startswith(kind + ":") and n <= 3)
    many = sorted(a for a, n in counts.items() if a.startswith(kind + ":") and n >= 4)
    print(kind, "| 1 to 3 chunks:", *rng.sample(few, min(3, len(few))), "| 4 or more:", *rng.sample(many, min(2, len(many))))
'@ | .venv/Scripts/python.exe -
```

Then export them, with `RELEASELENS_DB` set to `releaselens_eval` (see the
[retrieval README](../eval/retrieval/README.md#before-every-step)). Each folder is under
`eval/reports/`, which git ignores, so the tree stays clean:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
dotnet run --project ../src/ReleaseLens.Worker -- export-artefacts reports/functions-export/new <key1> <key2> <key3> <key4> <key5>
dotnet run --project ../src/ReleaseLens.Worker -- export-artefacts reports/functions-export/changed <I2 key>
dotnet run --project ../src/ReleaseLens.Worker -- export-artefacts reports/functions-export/duplicate <I3 key>
```

A key is `issue:<number>`, `pull_request:<number>`, `commit:<sha>` or `release:<tag>`. The
command checks every key before it touches the database, writes the ones it finds, and exits 1
naming any it did not find.

**Shorten I2's file.** Open the one file in `reports/functions-export/changed/` and cut most of
its long text field (the issue or pull request `body`, the release `body`, or the commit
`message`) down to its first sentence or two. Keep the JSON valid and every other field as it is.
Then choose the **gone text**: a short phrase of plain words that is in the cut-away part, that
appears in that artefact's chunks in `retrieval-data/chunks.jsonl`, and that is not anywhere in
the shortened file. `upload` refuses a phrase that is still in the file, or not in the old
chunks. Write the phrase in the scratch file. The `manifest.json` in that folder still gives the
unshortened count; I2 does not use it.

### Build the bulk load without I1's five

`build-index` loads every chunk in its `--data` folder, so the five are held back by giving it a
copy of the data without them. From `eval/`:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
@'
import base64, json, pathlib, sys
import numpy as np
src, export, dst = pathlib.Path("retrieval-data"), pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
held = {base64.urlsafe_b64decode(p.stem + "=" * (-len(p.stem) % 4)).decode("utf-8")
        for p in export.glob("*.json") if p.name != "manifest.json"}
lines = (src / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
missing = held - {json.loads(line)["artefact"] for line in lines}
if len(held) != 5 or missing:
    sys.exit(f"expected five exported artefacts, each in the corpus: {len(held)} exported, {len(missing)} not in the corpus")
kept = [line for line in lines if json.loads(line)["artefact"] not in held]
keep = {json.loads(line)["chunkId"] for line in kept}
ids = json.loads((src / "releaselens-embed-small.ids.json").read_text(encoding="utf-8"))
vectors = np.load(src / "releaselens-embed-small.npy")
rows = [row for row, chunk_id in enumerate(ids) if chunk_id in keep]
dst.mkdir(parents=True, exist_ok=True)
(dst / "chunks.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
(dst / "releaselens-embed-small.ids.json").write_text(json.dumps([ids[row] for row in rows]), encoding="utf-8")
np.save(dst / "releaselens-embed-small.npy", vectors[rows])
print(f"held back {len(held)} artefacts ({len(lines) - len(kept)} chunks); the bulk load has {len(kept)} chunks")
'@ | .venv/Scripts/python.exe - reports/functions-export/new reports/functions-bulk
```

Write the printed bulk-load count in the scratch file: `build-index` must reach it in step 3.
The saved vectors are copied, not recomputed, so the bulk load costs no embedding.

### The windows and the helpers

Open two windows: PowerShell on Windows, from the repository root, and Ubuntu in WSL. In the
PowerShell window, set the CA bundle as above, then this helper, which reads a Terraform output
through WSL without printing it:

```powershell
function tfout($stack, $dir, $name) {
  (wsl.exe -d Ubuntu --exec bash -c "cd /mnt/e/Projects/ReleaseLens/infra/$stack && export TF_DATA_DIR=`$HOME/tfdata/$dir TF_PLUGIN_CACHE_DIR=`$HOME/.terraform.d/plugin-cache && terraform output -raw $name").Trim()
}
```

The repository's path as WSL sees it is `/mnt/e/Projects/ReleaseLens`. Adjust it to the clone's.

Then the account check, in each window. It reads the bootstrap's `tenant_id` output without
printing it, and prints only `True` or `False`. PowerShell:

```powershell
function acctok {
  $a = az account show --query "{user: user.name, tenant: tenantId}" -o json | ConvertFrom-Json
  [bool]($env:WORK_DOMAIN -and ($a.user -notmatch [regex]::Escape($env:WORK_DOMAIN)) -and ($a.tenant -eq (tfout bootstrap bootstrap-live tenant_id)))
}
```

and the same in WSL, where `REPO` is the repository's path as WSL sees it:

```bash
REPO=/mnt/e/Projects/ReleaseLens   # adjust to your clone
acctok() {
  local user tenant boot
  user=$(az account show --query user.name -o tsv) && tenant=$(az account show --query tenantId -o tsv) || { echo False; return; }
  boot=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir="$REPO/infra/bootstrap" output -raw tenant_id)
  [[ -n "$WORK_DOMAIN" && "$user" != *"$WORK_DOMAIN"* && -n "$tenant" && "$tenant" == "$boot" ]] && echo True || echo False
}
```

**The bootstrap's `terraform output` fails until step 1's `init`.** The bootstrap gained the
`azapi` provider after its last apply (project 4's follow-up), so its live data directory lacks
that plugin, and both `acctok` functions and `tfout bootstrap ...` read an output. Step 1 checks
the account by name only, runs `init`, and only then calls `acctok`. A `False` after that means
the work account is signed in, the wrong tenant is, `az` is not signed in, or `WORK_DOMAIN` is
not set. Do not go on: sign in with the personal account and run it again.

### The harness

Every command runs from `eval/`, as `.venv/Scripts/python.exe -m app.functions <command>`, and
takes `--tenant` (the Entra tenant to take tokens from) and `--out reports/functions`. Each check
writes its own `check-<id>.json` there, so a re-run overwrites only that check's result.

| Command | What it does | Flags it needs |
|---|---|---|
| `upload --kind new` | uploads every file in `--dir` to `artefacts-in`, then **watches** each one's 120-second window while it is open and records what it saw (I1) | `--dir`, `--blob-endpoint`, `--search-endpoint`; `--new-session` on the session's first upload |
| `upload --kind changed` | uploads the one shortened file, and watches its 120-second window (I2) | `--dir`, `--blob-endpoint`, `--search-endpoint`, `--gone-text` |
| `upload --kind duplicate` | uploads the file, waits until it is searchable, records its keys, and uploads it again (I3) | `--dir`, `--blob-endpoint`, `--search-endpoint` |
| `upload --kind broken` | uploads one malformed file under a unique name, after counting the index's documents (I4) | `--blob-endpoint`, `--search-endpoint` |
| `ingest-checks` | judges I1 and I2 from what `upload` watched, re-reads I3 60 seconds after its second upload, and waits up to 10 minutes for I4's poison message | `--search-endpoint`; `--queue-endpoint` for I4; `--check i1` and so on, repeated, to choose |
| `tool-checks` | T1 to T4, or those named with `--check t1` and so on | T1: `--mcp-url`, `--scope`, `--search-endpoint`, `--openai-base-url`; T2: `--mcp-url`; T3: `--app-url`, `--scope`; T4: `--mcp-url`, `--scope`, `--app-id` |
| `tool-checks --check t4-metric` | reads T4's metric again, from the recorded burst, and sends nothing | `--app-id` |
| `report` | renders the saved results; sends nothing | `--dir reports/functions`, `--report-out` |

What the harness refuses, before any token is asked for or any file is written:
- a tree with uncommitted changes, or an output folder git would see;
- a `new` upload of an artefact already in the index (I1 needs one held back), a `changed` one
  not in the index or no longer under the bulk load's numeric keys, a `--gone-text` still in the
  shortened file or not in the old chunks, and a blob name already taken in the session;
- `ingest-checks` for an I1 or I2 upload that `upload` did not watch to the end of its window:
  a read made later says nothing about the window, so nothing is judged. **A crashed or
  interrupted watch leaves that upload unjudged:** upload it again under a new name, with
  `--new-session` (the old record is kept as `functions-session-<id>.json`), and let `upload`
  finish;
- a poison queue holding more than 32 messages, which one peek cannot see past;
- a T4 whose metric window would hold T1's tool calls. T4 itself waits, before its handshake,
  until 15 seconds past the next whole minute (16 to 75 seconds), so that the whole minute its
  burst lands in holds only its own calls;
- any file holding a GUID, an Azure hostname, the tenant, the scope, the app ID, a URL's host, or
  an object ID from `GATEWAY_CALLER_LABELS`.

## Step 1. Apply the bootstrap

**Ask first.** The ingestion storage account and its two host accounts, the containers and
queues, the Event Grid system topic and subscription, the ingest and tool identities, the
`releaselens-search-tool` app registration, the state container `tfstate-functions`, 21 Azure
role assignments and one Entra one, and project 4's pending `azapi` update. Cost: nothing by the
hour.

### 1a. Plan

WSL, in `infra/bootstrap`. The first time, check the account by name only, read the state
account's name from `az` rather than from an output, and `init` (see "Before the session"):

```bash
cd /mnt/e/Projects/ReleaseLens/infra/bootstrap   # adjust to your clone
export TF_DATA_DIR="$HOME/tfdata/bootstrap-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
[[ -n "$WORK_DOMAIN" && "$(az account show --query user.name -o tsv | tr A-Z a-z)" != *"${WORK_DOMAIN,,}"* ]] && echo True || echo False
ACCOUNT=$(az storage account list --resource-group rg-releaselens-bootstrap --query "[?starts_with(name, 'strlstate')].name | [0]" -o tsv)
terraform init -input=false -backend-config=storage_account_name="$ACCOUNT"
acctok                                                    # must print True
terraform plan -out=tfplan
terraform show -no-color tfplan | grep -E 'shared_access_key_enabled|local_user_enabled|app_role_assignment_required|requested_access_token_version|Microsoft\.Storage\.BlobCreated|subject_(begins|ends)_with|CustomMetricsOptedInType'
```

The plan reads the owner's git-ignored `terraform.tfvars`, which needs no new variable. Read the
whole plan, then check:
- **It only adds:** `Plan: 41 to add, 0 to change, 0 to destroy.` That is 40 for this project
  (the three storage accounts, four containers, two queues, the topic and its subscription, two
  identities, the app registration with its role ID, identifier URI and service principal, the
  `tfstate-functions` container, 21 role assignments and one app role assignment) and
  `azapi_update_resource.appi_custom_metrics` from project 4's follow-up, which is new to the
  state. Any change or destroy is wrong: stop and find out why. A different number of adds is not
  wrong by itself, but read why before applying.
- **The grep shows:** `shared_access_key_enabled = false` and `local_user_enabled = false` on all
  three new accounts; `app_role_assignment_required = true` on the search tool's service
  principal; `requested_access_token_version = 2`; `"Microsoft.Storage.BlobCreated"`, which must
  be the only entry of `included_event_types` (read that list in the plan); the subject filter
  beginning `/blobServices/default/containers/artefacts-in/` and ending `.json`; and
  `CustomMetricsOptedInType = "WithDimensions"`.

### 1b. Apply

```bash
terraform apply tfplan
```

- **The Event Grid subscription may fail on the first apply.** The topic's identity must hold its
  queue and dead-letter roles before the subscription is created, and a new role assignment can
  take minutes to propagate. If the apply stops there, wait five minutes, then run
  `terraform plan -out=tfplan` and `terraform apply tfplan` again. Write it in the findings.
- If it fails at the app registration, the signed-in owner lacks the right to register
  applications or assign app roles (R8 of the bootstrap README).

Then check the new outputs exist, without printing them, and that a queue's role scope is in the
Resource Manager form (azurerm 5.x puts that in a queue's `id`):

```bash
for o in ingest_identity_client_id tool_identity_client_id ingest_storage_blob_endpoint ingest_storage_queue_endpoint search_tool_app_client_id search_tool_app_identifier_uri; do
  terraform output -raw "$o" > /dev/null && echo "$o: set"
done
terraform state show -no-color azurerm_role_assignment.ingest_queue_events | grep -E '^\s+scope\s' | grep -c '/queueServices/default/queues/ingest-events"'
```

The last line must print `1`. A `0` means the queue roles are scoped to something that is not the
queue: stop, and take it to the controller.

Then, in PowerShell, `acctok` must print `True`. Wait about ten minutes before step 3's search
roles and step 4's apps, for the new identities' roles to propagate. Step 2 can run meanwhile.

**Where results go:** findings rows 19 (the Event Grid subscription on the first apply), 21 (the
queue scope), 29 (the custom-metrics update) and 32 (the plan).

## Step 2. Apply the gateway, and verify project 4's follow-ups

**Ask first.** From here API Management bills by the hour, about US$0.21, until step 8.

### 2a. Apply

The gateway stack is unchanged from project 4 apart from three outputs this project reads and
revision 2's own metrics switch. Follow the
[gateway runbook's step 2](runbook-gateway.md#step-2-apply-the-gateway) up to and including its
apply (its git-ignored `terraform.tfvars`, `TF_DATA_DIR=$HOME/tfdata/gateway-live`, the
start-of-session check, `init`, the plan check and the apply), and then come back here for the
settings: this session's label map differs. Its plan check applies, plus
`azapi_update_resource.diagnostic_metrics_rev2` among the adds. Do **not** set
`release_revision_2`: this session needs revision 2 only for the metric check below.

Then read the settings into the PowerShell window, without printing them:

```powershell
$env:GW_TENANT     = tfout bootstrap bootstrap-live tenant_id
$env:GW_DIRECT_URL = tfout bootstrap bootstrap-live azure_openai_base_url
$env:GW_BASE       = tfout gateway gateway-live gateway_base_url
$env:GW_SCOPE      = tfout gateway gateway-live gateway_scope
```

The Application Insights application ID is in the portal: `appi-releaselens`, **Configure**,
**API Access**, **Application ID**. Enter it without echoing it to a file:

```powershell
$env:APPI_ID = Read-Host 'Application ID'
```

And the label map T4 uses to turn the owner's `oid` into the label `owner`. It stays in this
window's environment, never in a file:

```powershell
$owner = az ad signed-in-user show --query id -o tsv
$env:GATEWAY_CALLER_LABELS = @{ $owner = 'owner' } | ConvertTo-Json -Compress
$owner = $null
```

### 2b. Custom metrics with dimensions

In the portal: `appi-releaselens`, **Usage and estimated costs**, **Custom metrics (Preview)**.
It must read **With dimensions**. Step 1's apply set it through
`azapi_update_resource.appi_custom_metrics`, which replaced project 4's portal step. If it reads
anything else, the update did not take: record it, set it in the portal so T4 can work, and take
it to the controller afterwards.

### 2c. Revision 2's token metric

**Ask first.** Three calls, a few cents. `smoke` has no guard and writes to its own folder.
From `eval/`:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.gateway smoke --tenant $env:GW_TENANT --direct-url $env:GW_DIRECT_URL --gateway-url $env:GW_BASE --scope $env:GW_SCOPE --out reports/functions-gw-smoke
```

Each of its three lines must be `pass`. Wait about five minutes for ingestion, then in the
portal's Logs for `appi-releaselens`:

```kusto
customMetrics
| where timestamp > ago(30m) and name == "Total Tokens"
| summarize samples = count(), tokens = sum(valueSum) by api = tostring(customDimensions["API ID"])
```

Revision 2 now has its own `metrics = true` (`azapi_update_resource.diagnostic_metrics_rev2`),
so its call is expected to emit a token metric: a row whose `api` names revision 2 (`;rev=2`), or,
if `API ID` does not name the revision, two samples where project 4 saw one. Run it before 2d,
whose burst adds samples.

### 2d. B1's burst, re-measured

**Ask first.** One burst of 60 requests at once through the gateway, about 18,000 tokens of
estimates against the owner's 10,000-token minute bucket; the refused ones cost nothing. From
`eval/`:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.gateway minute-budget --tenant $env:GW_TENANT --base-url $env:GW_BASE --scope $env:GW_SCOPE --out reports/functions-gw-b1
```

It must print `B1: pass`: at least one 429 in the burst, with `Retry-After` and no `x-ms-region`.
Its detail says how many got a model's 429. Confirm in Application Insights that the refused
requests reached no backend, with the query in the
[gateway runbook's step 5](runbook-gateway.md#step-5-run-test-1-then-test-2-up-to-b2). Write the
printed line and the query's result in the scratch file: they become a dated amendment to
[project 4's report](gateway-report.md) in step 8.

**Where results go:** findings rows 29, 30 and 31.

## Step 3. Apply search, and bulk-load the index

**Ask first.** From here AI Search bills by the hour, about US$0.13, until step 8.

### 3a. Apply

The search stack now reads the bootstrap's state for the two identities, so its git-ignored
`terraform.tfvars` needs `tfstate_storage_account` as well as `subscription_id` and
`owner_object_id`. Write it from PowerShell, without printing any value, from the repository
root:

```powershell
Set-Location (git rev-parse --show-toplevel)
$sub = az account show --query id -o tsv
$oid = az ad signed-in-user show --query id -o tsv
$st  = tfout bootstrap bootstrap-live tfstate_storage_account
Set-Content infra/search/terraform.tfvars @("subscription_id = `"$sub`"", "owner_object_id = `"$oid`"", "tfstate_storage_account = `"$st`"")
$sub = $oid = $st = $null
git status --short
```

`git status --short` must print nothing: the file is ignored. Then, in WSL:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/search   # adjust to your clone
export TF_DATA_DIR="$HOME/tfdata/search" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
ACCOUNT=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir=../bootstrap output -raw tfstate_storage_account)
terraform init -input=false -backend-config=storage_account_name="$ACCOUNT"
az group exists --name rg-releaselens-search    # must print false
terraform plan -out=tfplan
```

Read the plan. It must be `Plan: 7 to add, 0 to change, 0 to destroy.`: the resource group, the
suffix, the search service and four role assignments (the owner's two, Search Index Data
Contributor for the ingest identity and Search Index Data Reader for the tool identity). See the
stack's [Apply and destroy](../infra/search/README.md#apply-and-destroy). **If it shows anything
else, stop:** do not apply, and find out why. Only then, in the same WSL window:

```bash
terraform apply tfplan
```

Then, in PowerShell:

```powershell
$env:FN_SEARCH = tfout search search endpoint
```

### 3b. Bulk load

From `eval/`, from the copy without I1's five ("Before the session"):

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.retrieval build-index --data reports/functions-bulk --endpoint $env:FN_SEARCH --tenant $env:GW_TENANT
```

It prints the documents uploaded, then the index's document count, which must be the bulk-load
count in the scratch file. A `403` straight after the apply is the owner's new role propagating:
wait a few minutes and run it again.

**Where results go:** findings row 5 (role propagation, with step 6).

## Step 4. Apply the functions stack, and deploy both packages

**Ask first.** Two Flex Consumption apps, their authentication, and the tool's MCP API, policy,
named values and diagnostic on the gateway. Then the two code deploys, with the owner's sign-in.
Flex Consumption bills only executions.

### 4a. Apply

The stack's git-ignored `terraform.tfvars` needs `subscription_id` and
`tfstate_storage_account` ([inputs](../infra/functions/README.md#inputs)). From PowerShell, at the
repository root:

```powershell
Set-Location (git rev-parse --show-toplevel)
$sub = az account show --query id -o tsv
$st  = tfout bootstrap bootstrap-live tfstate_storage_account
Set-Content infra/functions/terraform.tfvars @("subscription_id = `"$sub`"", "tfstate_storage_account = `"$st`"")
$sub = $st = $null
git status --short     # must print nothing
```

Then in WSL:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/functions   # adjust to your clone
export TF_DATA_DIR="$HOME/tfdata/functions-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
ACCOUNT=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir=../bootstrap output -raw tfstate_storage_account)
terraform init -input=false -backend-config=storage_account_name="$ACCOUNT"
az group exists --name rg-releaselens-functions    # must print false
terraform plan -out=tfplan
terraform show -no-color tfplan | grep -E 'AzureWebJobsStorage|requireAuthentication|unauthenticatedClientAction|\ballow = |subscriptionRequired|maximumInstanceCount|^Plan:'
terraform show -no-color tfplan | grep -A1 'APPLICATIONINSIGHTS_CONNECTION_STRING'
```

Read the whole plan. It must be `Plan: 18 to add, 0 to change, 0 to destroy.`: the resource
group, the suffix, the Flex plan, the two apps, two authentication updates, four basic-publishing
updates, four named values, the MCP API, its policy and its diagnostic. Terraform prints azapi
body keys unquoted (`+ allow = false`), and the greps must show:
- the three `AzureWebJobsStorage__` settings (`accountName`, `clientId`, `credential`) on each
  app, and no setting named `AzureWebJobsStorage` alone, which would be a connection string;
- `APPLICATIONINSIGHTS_CONNECTION_STRING` twice, each followed on the next line by
  `value = (sensitive value)`;
- `requireAuthentication = true` and `unauthenticatedClientAction = "Return401"`, twice each;
- `allow = false` four times (SCM and FTP on each app);
- `subscriptionRequired = false`, and `maximumInstanceCount = 10` twice.

The MCP API's service URL is known only after the apply; the type check below and step 5's
`initialize` settle it. **If the plan or a grep shows anything else, stop:** do not apply, and
find out why. Only then, in the same WSL window:

```bash
terraform apply tfplan
```

**What to watch for in the apply** (the stack README's
[Live checks](../infra/functions/README.md#live-checks) has the detail):
- **The MCP API's diagnostic may be refused** because of its `largeLanguageModel` block, on an
  API of type `mcp`. Payload bytes 0 alone keep the bodies out, so the block can go. If the apply
  stops there:
  1. In `infra/functions/mcp_api.tf`, delete the three lines of the `largeLanguageModel` block
     from `azapi_resource.mcp_diagnostic`.
  2. In `infra/functions/tests/functions.tftest.hcl`, in `run "mcp_diagnostic"`'s third assert,
     delete the line `azapi_resource.mcp_diagnostic.body.properties.largeLanguageModel.logs ==
     "disabled"` and the ` &&` that ends the line before it.
  3. Run the stack's tests in WSL, from `infra/functions`, with a data directory of their own, so
     the live one is untouched. The tests' own `variables` block overrides the live
     `terraform.tfvars`, so that file can stay (checked offline on a copy that held one):

     ```bash
     TF_DATA_DIR="$HOME/tfdata/test-functions" terraform init -backend=false -input=false
     TF_DATA_DIR="$HOME/tfdata/test-functions" terraform test
     terraform fmt -check mcp_api.tf tests/functions.tftest.hcl
     ```

     The test must end `Success! 9 passed, 0 failed.` and `fmt` must print nothing.
  4. Commit both files in PowerShell, before step 6, because the measured commands refuse a dirty
     tree: `Set-Location (git rev-parse --show-toplevel)`, `git add infra/functions/mcp_api.tf
     infra/functions/tests/functions.tftest.hcl`, then `git commit -m "fix(infra): the MCP API's
     diagnostic without the largeLanguageModel block the service refused"`.
  5. Back in the live window (`TF_DATA_DIR=$HOME/tfdata/functions-live`), plan, check and apply
     again as above.

  The azapi bodies were checked only against mocks, so the first apply is where any other body
  error shows: record its text, without any identifier, and take it to the controller.
- **The apps may fail to start** on accounts with shared keys off (spec §12 item 2). That shows in
  4c.

Then three checks, in PowerShell after `acctok` prints `True`. They print a word or a number:

```powershell
$apim   = tfout gateway gateway-live api_management_id
$ingest = tfout functions functions-live ingest_app_name
$tool   = tfout functions functions-live tool_app_name
az rest --method get --url "https://management.azure.com$apim/apis/releaselens-search?api-version=2025-09-01-preview" --query properties.type -o tsv
az resource show --resource-group rg-releaselens-functions --name $ingest --resource-type Microsoft.Web/sites --query properties.functionAppConfig.scaleAndConcurrency.maximumInstanceCount -o tsv
az resource show --resource-group rg-releaselens-functions --name $tool --resource-type Microsoft.Web/sites --query properties.functionAppConfig.scaleAndConcurrency.maximumInstanceCount -o tsv
$apim = $null
```

- **The MCP API reports type `mcp`.** `apiType` is write-only and never read back, so this is the
  only proof the service created an MCP API and not a plain HTTP one. Anything else: stop, and
  take it to the controller (every call would get a 404).
- **Both apps report `10`** as their instance cap. An empty answer means the property is
  elsewhere in the site's body: record it, and read it in the portal (the app, **Scale and
  concurrency**).

Then read the harness's inputs into the window, without printing them:

```powershell
$env:FN_MCP_URL = tfout functions functions-live tool_mcp_url
$env:FN_APP_URL = tfout functions functions-live tool_app_url
$env:FN_BLOB    = tfout bootstrap bootstrap-live ingest_storage_blob_endpoint
$env:FN_QUEUE   = tfout bootstrap bootstrap-live ingest_storage_queue_endpoint
```

### 4b. Deploy both packages

**Ask first.** Basic publishing credentials are off on both apps, so the deploy goes with the
owner's Entra sign-in through `az`. Build and zip each app, from the repository root. The zips
are in `artifacts/`, which git ignores:

```powershell
Set-Location (git rev-parse --show-toplevel)
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($app in 'Ingest', 'Tool') {
  $out = "artifacts/functions/$($app.ToLower())"
  Remove-Item -Recurse -Force $out, "$out.zip" -ErrorAction SilentlyContinue
  dotnet publish "src/ReleaseLens.Functions.$app" -c Release -o $out
  [IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path $out), "$PWD/$out.zip")
}
az functionapp deployment source config-zip --resource-group rg-releaselens-functions --name $ingest --src artifacts/functions/ingest.zip -o none --only-show-errors
az functionapp deployment source config-zip --resource-group rg-releaselens-functions --name $tool --src artifacts/functions/tool.zip -o none --only-show-errors
```

Each deploy prints nothing on success (`-o none`). An error may print the app's host: do not paste
it anywhere. `ZipFile` writes forward slashes in the entry names, which the Linux host needs.

- **If `az` refuses for want of basic authentication,** the CLI did not use the owner's Entra
  token against Flex's deployment endpoint (spec §12 item 2). Record the error text without any
  identifier, and take it to the controller. Do not turn basic publishing credentials on.
- The packages land in each app's deployment container, `deploy-ingest` and `deploy-tool`, in its
  own host account. The owner holds Storage Blob Data Contributor on both.

### 4c. Did both apps start?

After a minute, each must print `Running` and then `1` (one function each, `ingest` and
`search_corpus`):

```powershell
az functionapp show --resource-group rg-releaselens-functions --name $ingest --query state -o tsv
az functionapp function list --resource-group rg-releaselens-functions --name $ingest --query "length(@)" -o tsv
az functionapp show --resource-group rg-releaselens-functions --name $tool --query state -o tsv
az functionapp function list --resource-group rg-releaselens-functions --name $tool --query "length(@)" -o tsv
```

A `0` after a few minutes means the host did not index the function: the host storage (identity
based, on an account with shared keys off), the runtime, or the package. The app's **Log stream**
in the portal shows why. Record it without any identifier.

**Where results go:** findings rows 1, 2, 22, 23, 24 and 33.

## Step 5. Smoke test

**Ask first.** A few tool calls through the gateway, cents.

### 5a. The tool through the gateway

This helper sends one JSON-RPC request to the tool's gateway URL with a fresh owner token, and
returns the status and the parsed reply. It prints nothing by itself and keeps no token:

```powershell
$script:McpSessionId = $null
function mcp($method, $params) {
  $token = az account get-access-token --scope $env:GW_SCOPE --query accessToken -o tsv
  $body = @{ jsonrpc = '2.0'; method = $method }
  if ($method -notlike 'notifications/*') { $body.id = Get-Random -Maximum 100000 }
  if ($null -ne $params) { $body.params = $params }
  $headers = @{ Authorization = "Bearer $token"; Accept = 'application/json, text/event-stream' }
  if ($script:McpSessionId) { $headers['Mcp-Session-Id'] = $script:McpSessionId }
  $r = Invoke-WebRequest -Method Post -Uri $env:FN_MCP_URL -ContentType 'application/json' -Headers $headers -Body ($body | ConvertTo-Json -Depth 10 -Compress) -SkipHttpErrorCheck
  $token = $null
  if ($r.Headers['Mcp-Session-Id']) { $script:McpSessionId = [string]$r.Headers['Mcp-Session-Id'] }
  $text = [string]$r.Content
  if ($text -match '(?m)^data: ?(.+)$') { $text = $Matches[1].Trim() }      # one event-stream message
  $reply = $null
  if ($text) { try { $reply = $text | ConvertFrom-Json } catch { } }
  [pscustomobject]@{ Status = [int]$r.StatusCode; Reply = $reply }
}
```

Then, in order. The tool app has been idle since its deploy, so the first call is a cold start:

```powershell
$sw = [Diagnostics.Stopwatch]::StartNew()
$init = mcp 'initialize' @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'runbook-smoke'; version = '1.0' } }
$sw.Stop()
"{0} {1} {2:n1} s" -f $init.Status, $init.Reply.result.serverInfo.name, $sw.Elapsed.TotalSeconds
(mcp 'notifications/initialized').Status
(mcp 'tools/list' @{}).Reply.result.tools.name
$hits = mcp 'tools/call' @{ name = 'search_corpus'; arguments = @{ query = 'what changed in the latest release?' } }
$hits.Status; $hits.Reply.result.isError; @($hits.Reply.result.content[0].text | ConvertFrom-Json).Count
$bad = mcp 'tools/call' @{ name = 'search_corpus'; arguments = @{ query = ' ' } }
$bad.Status; $bad.Reply.result.isError; $bad.Reply.result.content.text; $bad.Reply.error.message
```

1. **`initialize` answers `200 releaselens-search`.** That one line shows the MCP pass-through
   path (the API's service URL ending `/runtime/webhooks` plus the endpoint `/mcp` reaches the
   app's `/runtime/webhooks/mcp`), App Service authentication accepting the gateway identity's v2
   token (audience the client ID), and the host honouring
   `extensions.mcp.system.webhookAuthorizationLevel = "Anonymous"` with no `mcp_extension` key.
   - A **404** is the path split: change it as the stack README's
     [Live checks](../infra/functions/README.md#live-checks) says, commit, apply again, and repeat
     this step.
   - A **401** comes either from the gateway (the owner's token: check `$env:GW_SCOPE` and the
     owner's `Gateway.Invoke`) or from App Service authentication behind it (the gateway's token
     for the tool: the allowed audiences or the allowed application). Application Insights tells
     them apart: in `requests` for the MCP API, a 401 with no backend dependency is the gateway's
     own, and a dependency with result 401 is the app's.
   - The elapsed time includes about two seconds for `az` to issue the token. Write it down: it is
     the cold start (spec §12 item 10).
2. **The notification answers `202` or `200`,** and the tool list prints `search_corpus`.
3. **A call without `top` gives 5 hits:** `200`, `isError` empty or `False`, and `5`. The tool
   reads a missing `top` as null and uses 5. A tool error `search failed` within about 10 minutes
   of step 3's apply may be the tool identity's new search role still propagating: wait a few
   minutes and call again before recording it as a finding.
4. **An empty query is a tool error with the tool's own text.** `isError` `True` and the text
   `query must not be empty` is what the code throws. Write down exactly what came back,
   whichever way the host renders it (a tool result with `isError`, or a JSON-RPC `error` with its
   own message). If the host replaces the text with a generic message, that is a finding, not a
   failure. The other fixed message, `search failed`, appears only on a backend error and is not
   provoked here.

### 5b. The index's paging, live

The harness and the ingest app both read an artefact's chunks by following
`@search.nextPageParameters` (the service's page is 50). The offline tests stub that shape. The
corpus has six artefacts of more than 50 chunks, so read the largest through the harness's reader
and compare. From `eval/`, it prints two numbers and an artefact ID:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
@'
import collections, json, os
import httpx, truststore
truststore.inject_into_ssl()
from app.functions import ingest_checks as ic
from app.retrieval import search_index
from app.retrieval.azure_auth import TokenSource
counts = collections.Counter(json.loads(line)["artefact"] for line in open("reports/functions-bulk/chunks.jsonl", encoding="utf-8"))
artefact, expected = counts.most_common(1)[0]
tokens = TokenSource(search_index.SCOPE, os.environ["GW_TENANT"])
with httpx.Client(timeout=60) as http:
    read = ic.chunks_for_artefact(http, os.environ["FN_SEARCH"], tokens.token, artefact)
print(artefact, "in the bulk load:", expected, "read back:", len(read))
'@ | .venv/Scripts/python.exe -
```

The two numbers must be equal. A difference, or an error saying the index paged without moving
forward, means the paging shape differs from the stub: record it and take it to the controller
before step 6, because I2 and the ingest app's stale-key delete rely on it.

**Where results go:** findings rows 6, 7, 10, 13, 16, 17, 18, 20 and 22.

## Step 6. Run I1 to I4, then T1 to T4

**Ask first.** About 40 minutes. Uploads, embeddings and searches cost cents. Every command runs
from `eval/`, with the window's variables from steps 2 to 4. Wait at least ten minutes after step
3's apply before the first upload, so that the ingest identity's new search role has propagated:
an I1 upload that meets a 403 fails three times and goes to the poison queue.

### 6a. The poison queue at the start

The poison queue should be empty at the start, and I4 can only see 32 messages. This helper peeks
it with the harness's own code (nothing is dequeued) and prints a count and the first character of
each message:

```powershell
function poisonpeek {
Push-Location (Join-Path (git rev-parse --show-toplevel) eval)
@'
import os
import httpx, truststore
truststore.inject_into_ssl()
from app.functions import ingest_checks as ic
from app.retrieval.azure_auth import TokenSource
tokens = TokenSource(ic.STORAGE_SCOPE, os.environ["GW_TENANT"])
with httpx.Client(timeout=60) as http:
    messages = ic.peek_poison(http, os.environ["FN_QUEUE"], tokens.token)
print(len(messages), "message(s); first characters:", sorted({m.text[:1] for m in messages}))
'@ | .venv/Scripts/python.exe -
Pop-Location
}
poisonpeek
```

It should print `0 message(s)`. Messages left from an earlier session do not stop the session: I4
matches its own upload's unique name and refuses only a queue of more than 32, and the owner can
only read the queue, not clear it. Record the count. It also reads the queue's metadata first, as I4
does, so an answer other than an error shows that Storage Queue Data Reader allows Get Queue
Metadata under Entra. A `QueueReadError` with `403` means it does not: record it and take it to the
controller.

### 6b. Ingestion: I1, I2 and I3 first

I4 compares the index's document count before and after its upload, so it needs ingestion to be
quiet: run I1 to I3 to the end before I4's upload.

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions upload --kind new --dir reports/functions-export/new --blob-endpoint $env:FN_BLOB --search-endpoint $env:FN_SEARCH --tenant $env:GW_TENANT --out reports/functions --new-session
.venv/Scripts/python.exe -m app.functions upload --kind changed --dir reports/functions-export/changed --gone-text '<the gone text>' --blob-endpoint $env:FN_BLOB --search-endpoint $env:FN_SEARCH --tenant $env:GW_TENANT --out reports/functions
.venv/Scripts/python.exe -m app.functions upload --kind duplicate --dir reports/functions-export/duplicate --blob-endpoint $env:FN_BLOB --search-endpoint $env:FN_SEARCH --tenant $env:GW_TENANT --out reports/functions
.venv/Scripts/python.exe -m app.functions ingest-checks --check i1 --check i2 --check i3 --search-endpoint $env:FN_SEARCH --tenant $env:GW_TENANT --out reports/functions
```

- Each `new` and `changed` upload returns only after its 120-second window has closed, and prints
  a line for any artefact not searchable as expected in it. The first upload includes the ingest
  app's cold start.
- `duplicate` waits for the first upload to be searchable before it uploads the same file again.
- `ingest-checks` prints one line per check, `I1: pass - ...` or `fail`, and writes each result.
  I3 waits 60 seconds after the second upload before it reads the index.
- **If a watch crashes or is interrupted** (Ctrl+C, a network error), the record keeps the upload
  but `ingest-checks` refuses to judge it. First judge the kinds that were watched, with
  `ingest-checks --check <id>` for each: every check writes its own result file, which the report
  reads whichever session it came from. Then upload the unjudged kind again, under a new name, with
  `--new-session` (the old record is kept as `functions-session-<id>.json`), and judge it with
  `ingest-checks --check <id>`. The artefact must still qualify:
  - **I2** needs an artefact still under the bulk load's numeric keys, so a second try uses another
    bulk-loaded artefact, exported and shortened as before.
  - **I1** needs artefacts not in the index. Once the five have been ingested, I1 can be repeated
    only in a later session from a new hold-back: record it as not measured.
  - **I3** can use the same file again.
- All blob names are the harness's `<session>-<base64url key>.json`, letters, digits, `-` and `_`
  only, so whether Event Grid's subject holds the raw or the percent-encoded name never matters
  here. A name that needed encoding could fail with a 404 and go to the poison queue; the demo uses
  plain names only.

Then, in the portal's Logs for `appi-releaselens`, the ingest app's own log lines (counts and
durations, no content), forwarded by the Functions host:

```kusto
traces
| where timestamp > ago(1h) and message startswith "Ingested "
| extend ms = toint(extract(@"in (\d+) ms", 1, message))
| summarize lines = count(), median_ms = percentile(ms, 50), max_ms = max(ms)
```

`lines` should be at least eight: one for each of I1's five uploads, one for I2's, and two for
I3's two uploads of the same file. A redelivered event adds a line. None means
the worker's logs do not reach Application Insights through the host: record it.

### 6c. Ingestion: I4

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions upload --kind broken --blob-endpoint $env:FN_BLOB --search-endpoint $env:FN_SEARCH --tenant $env:GW_TENANT --out reports/functions
.venv/Scripts/python.exe -m app.functions ingest-checks --check i4 --search-endpoint $env:FN_SEARCH --queue-endpoint $env:FN_QUEUE --tenant $env:GW_TENANT --out reports/functions
```

The broken file fails three times, 30 seconds apart, and the runtime moves its message to the
poison queue. `ingest-checks` waits up to 10 minutes, peeking every 15 seconds, and judges by the
poison message's own insertion time, so it does not depend on when it runs. Then:

```powershell
poisonpeek
```

It must now print one or two more messages than 6a's count. Event Grid delivers at least once, so
the same `BlobCreated` event can reach the queue twice, and each copy fails three times and lands in
the poison queue on its own. I4 counts the first. The first character settles the message encoding
(spec §12 item 3): `{` or `[` is plain JSON; `e` or `W` is base64 (of `{"` and `[{`). The runtime
copies the message to the poison queue as it was. The parser accepts both, so nothing depends on the
answer.

In the portal, the Event Grid system topic `evgt-releaselens-ingest`, **Metrics**: the
subscription's **Delivered Events** count the uploads, and **Dead Lettered Events** is 0. The
owner has no role on the dead-letter container by design, so the portal's metric is the check.

### 6d. The tool: T1, T2 and T3

Leave a minute after any earlier tool call (the smoke test), then:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions tool-checks --check t1 --check t2 --check t3 --mcp-url $env:FN_MCP_URL --app-url $env:FN_APP_URL --scope $env:GW_SCOPE --search-endpoint $env:FN_SEARCH --openai-base-url $env:GW_DIRECT_URL --tenant $env:GW_TENANT --out reports/functions
```

- **T1** sends the first ten questions of project 2's frozen set through the gateway and compares
  each top 5 with a direct hybrid-plus-semantic query of the same index, with the tool's own
  parameters (`k = top = 5`). It also writes `check-t1-ended.json`, which T4 reads.
- **T2** sends a call with no token and one with a token for the wrong audience
  (`https://ai.azure.com`). Both must get 401 from the gateway.
- **T3** calls the tool app's own URL with no token and with the owner's gateway token. Both must
  get 401 from App Service authentication: the owner is not assigned `Tool.Invoke`, so cannot get
  a token for the tool's audience at all.

### 6e. The tool: T4

Nothing else may call the tool now: no Claude Code session with the tool configured (it is not,
until step 7), no smoke call, no second window.

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions tool-checks --check t4 --mcp-url $env:FN_MCP_URL --scope $env:GW_SCOPE --app-id $env:APPI_ID --tenant $env:GW_TENANT --out reports/functions
```

It prints how long it waits first (up to 75 seconds), then sends 30 calls at once, records the
burst in `check-t4-burst.json`, and reads the `Tool Calls` metric for up to 5 minutes. It passes on
at least one gateway 429 with `Retry-After`, and the metric showing under `owner` at least the
calls the gateway let through plus the two handshake requests. If the metric is still arriving
when it gives up, read it again later without sending anything:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions tool-checks --check t4-metric --app-id $env:APPI_ID --tenant $env:GW_TENANT --out reports/functions
```

Then check two things T4's window rests on, in the portal's Logs:

```kusto
customMetrics
| where timestamp > ago(1h) and name == "Tool Calls"
| summarize rows = count(), calls = sum(valueSum) by second = datetime_part("second", timestamp), caller_set = isnotempty(tostring(customDimensions["Caller"]))
```

- **Every row is at second 0.** The harness assumes `emit-metric` rows are stamped at the start of
  their whole minute. Rows at other seconds mean T4's window may cut some off: record it, and treat
  a T4 metric shortfall as unproven rather than failed.
- **`caller_set` is true.** `Tool Calls` carries the `Caller` dimension (spec §12 item 12). Project
  4 found that custom metrics carry no namespace dimension, so the query picks the metric by name.

**Where results go:** findings rows 3, 4, 5, 8, 12, 14, 15, 25 to 28 and 34.

## Step 7. The Claude Code demo

**Ask first.** This adds an MCP server to Claude Code's configuration on this machine, in the
local scope (`~/.claude.json`, under this project's path), never the project scope: a project
`.mcp.json` would be committed to this public repository with the gateway's URL in it. The demo is
descriptive, not a check.

Claude Code runs a `headersHelper` command each time it connects, and sends the JSON object of
headers it prints. The helper below gets a fresh gateway token from `az` each time, so no token is
written into Claude Code's configuration. It lives outside the repository, and holds the gateway's
scope, which the window writes into it without printing:

```powershell
Set-Location (git rev-parse --show-toplevel)     # the local scope is keyed by this project's path
New-Item -ItemType Directory -Force "$HOME\.releaselens" | Out-Null
@"
`$env:REQUESTS_CA_BUNDLE = "`$env:USERPROFILE\.azure\ca-bundle-with-norton.pem"
`$t = az account get-access-token --scope "$($env:GW_SCOPE)" --query accessToken -o tsv
@{ Authorization = "Bearer `$t" } | ConvertTo-Json -Compress
"@ | Set-Content "$HOME\.releaselens\gateway-headers.ps1"
$PSNativeCommandArgumentPassing = 'Standard'
$config = @{ type = 'http'; url = $env:FN_MCP_URL; headersHelper = "pwsh -NoProfile -File $($HOME -replace '\\', '/')/.releaselens/gateway-headers.ps1" } | ConvertTo-Json -Compress
claude mcp add-json --scope local releaselens-search $config
$config = $null
claude mcp list | Select-String releaselens-search | ForEach-Object { $_ -replace 'https://\S+', '<url>' }
```

`claude mcp add` has no flag for a header helper, so the server is added as JSON. The last line
shows the server's state with its URL masked. Claude Code gives a helper 10 seconds: `az` on
Windows takes about two.

Then start a new Claude Code session in this repository (`claude`), run `/mcp` and check that
`releaselens-search` is connected. Ask a question about the corpus, for example which release
fixed a named problem, and check that Claude calls `search_corpus` and cites the artefact IDs it
returns. Record a short excerpt in the scratch file: the question, that the tool was called, and
the cited artefact IDs. No URL, hostname or account goes into it.

Write down:
- whether the first connection after the tool app was idle succeeded within Claude Code's MCP
  timeout (the `MCP_TIMEOUT` environment variable sets it), or failed and needed a reconnect from
  `/mcp` (spec §12 item 10);
- whether Claude Code ever offered an OAuth sign-in for the server. With an `Authorization` header
  from the helper it should not: a 401 makes it run the helper again and retry once (spec §12
  item 9).

### Optional: the recreate check (spec §12 item 11)

**Ask first.** About 15 minutes more of the gateway and search billing. It shows that the Event
Grid subscription survives the functions stack being destroyed and recreated, and that an upload
made in between waits in the queue and drains. Skip it if time is short, and write "not tested".

1. Destroy the functions stack (WSL, `infra/functions`, `TF_DATA_DIR=$HOME/tfdata/functions-live`):
   `terraform destroy`, then `az group exists --name rg-releaselens-functions` must print `false`.
2. Upload one of I1's files again under a new plain name, as the owner (PowerShell, from the
   repository root):

   ```powershell
   Set-Location (git rev-parse --show-toplevel)
   $acct = tfout bootstrap bootstrap-live ingest_storage_account_name
   $file = Get-ChildItem eval/reports/functions-export/new -Filter *.json | Where-Object Name -ne manifest.json | Select-Object -First 1
   az storage blob upload --auth-mode login --account-name $acct --container-name artefacts-in --name "recreate-$(Get-Date -Format yyyyMMddHHmmss).json" --file $file.FullName -o none --only-show-errors
   az storage message peek --auth-mode login --account-name $acct --queue-name ingest-events --num-messages 32 --query "length(@)" -o tsv
   ```

   The peek must print `1`: the event waits in `ingest-events` with no app to take it.
3. Apply the functions stack again and deploy both packages again (step 4a's plan and apply, the
   checks, then 4b, with `$ingest`, `$tool`, `$env:FN_MCP_URL` and `$env:FN_APP_URL` read again:
   the app names are new).
4. Within a few minutes, the same peek must print `0`, and step 6b's `traces` query must count one
   more `Ingested ` line. Re-ingesting writes the artefact's same keys. The gateway URL is
   unchanged, so Claude Code's configuration still holds.

Then remove `$acct` and `$file` from the window.

**Where results go:** findings rows 7, 9, 10, 11 and 35.

## Step 8. Publish the report; destroy and check

### 8a. The report

**Ask first.** The report reads the saved results and sends nothing, so it runs first, before
anything is committed after the checks, so that it cites the commit they ran from. From `eval/`:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) eval)
.venv/Scripts/python.exe -m app.functions report --dir reports/functions --report-out ../docs/functions-report.md
```

It holds the eight checks' outcomes with their details, labels instead of identifiers, the T3
note, and the commit. It refuses to write a GUID or an Azure hostname. Read it before going on.

### 8b. Destroy, in this order

**Ask first.** Do this whether the session succeeded or not. It ends the hourly billing. **If
anything fails or goes wrong at any point, the session ends at step 8, and step 8 ends only when
every check below passes.**

1. **The functions stack first,** because its MCP API, policy, diagnostic and named values live
   on the gateway's service. WSL, `infra/functions`, `TF_DATA_DIR=$HOME/tfdata/functions-live`:
   `terraform destroy`. If the gateway were destroyed first, those went with the service, and this
   destroy finds them gone.
2. **The gateway,** as the [gateway runbook's step 8](runbook-gateway.md#step-8-destroy-the-gateway)
   says, with its revision 2 fallback and its purge. Revision 2 was not released in this session.
   Skip that step's "Delete the two session secrets": this session sets no GitHub secrets.
3. **The search stack.** WSL, `infra/search`, `TF_DATA_DIR=$HOME/tfdata/search`:
   `terraform destroy`.

Then the checks, in PowerShell after `acctok` prints `True`:

```powershell
az group exists --name rg-releaselens-functions
az group exists --name rg-releaselens-gateway
az group exists --name rg-releaselens-search
az apim deletedservice list --query "[?starts_with(name, 'apim-releaselens-')].name" -o tsv
az resource list --resource-type Microsoft.Search/searchServices --query "length(@)" -o tsv
```

The three group checks must print `false`. The API Management check must print **nothing**: no
gateway instance is left soft-deleted. The search check must print `0`.

**If a destroy fails, or a check does not pass:** for the gateway, use the
[gateway runbook's fallback](runbook-gateway.md#if-the-destroy-fails-or-a-check-does-not-pass)
(delete the group, purge the soft-deleted service). For the functions or search stack, delete the
group (`az group delete --name <group> --yes`), which removes everything in it, then run the
checks again. Record the error text, without any identifier. After a group delete the stack's
state still lists the deleted resources: before the next session, run `terraform destroy` again
in that stack, which finds nothing left and clears the state.

**What stays** (spec §9): the bootstrap's storage accounts, the Event Grid topic, the identities
and the app registrations, which cost pennies a month, and the test uploads in `artefacts-in`,
uniquely named and tiny.

Then clean up this machine:

```powershell
Set-Location (git rev-parse --show-toplevel)     # the local scope is keyed by this project's path
claude mcp remove --scope local releaselens-search
Remove-Item -Recurse -Force "$HOME\.releaselens"
Remove-Item Env:GW_TENANT, Env:GW_DIRECT_URL, Env:GW_BASE, Env:GW_SCOPE, Env:APPI_ID, Env:GATEWAY_CALLER_LABELS, Env:FN_SEARCH, Env:FN_MCP_URL, Env:FN_APP_URL, Env:FN_BLOB, Env:FN_QUEUE
$ingest = $tool = $null
```

The server must be removed: with the gateway gone, every Claude Code session in this repository
would otherwise fail to connect to it at start.

### 8c. Write it up

**Ask first** for the commit, and again for the push. Copy the scratch file's findings into the
table below, and fill the session notes. Then, as one commit:
- add the Claude Code demo's excerpt to `docs/functions-report.md`, under its own heading;
- add a dated amendment to [project 4's report](gateway-report.md) with B1's re-measured burst,
  and the custom-metrics and revision 2 findings;
- change the README's [Azure Functions section](../README.md#azure-functions) to say what was
  measured;
- change the status lines of this runbook, the
  [functions stack README](../infra/functions/README.md) and
  [`docs/architecture.md`](architecture.md#the-functions-path) from "not yet run" to what
  happened.

Run `python -m pytest tests/infra -q` before committing: the doc scan reads every one of these
files. Do not push without the owner's yes. **Every result is publishable.** A failed check is
written up as plainly as a pass.

## Findings

One row for every item in spec §12, then every item that only a live session can settle. Fill
the **Finding** column from the scratch file in step 8. "Not observed" and "not tested" are
findings too.

| # | Item | Step | Finding |
|---|---|---|---|
| 1 | Spec §12.1: Flex Consumption is offered in australiaeast, and a .NET 10 isolated app starts there | 4 | |
| 2 | Spec §12.2: an azapi-created Flex app with identity-based host storage starts on an account with shared keys off; code deploys with the owner's Entra sign-in and basic authentication off | 4 | |
| 3 | Spec §12.3: Event Grid delivers to the queue with the topic's identity; the message encoding, plain or base64 (the poison message's first character) | 6 | |
| 4 | Spec §12.4: the poison write needs no role beyond the ingest identity's queue roles (I4 passes) | 6 | |
| 5 | Spec §12.5: the two identities' roles on a newly created search service propagate within the session (how long after step 3's apply the tool's smoke call and I1 worked) | 3, 5, 6 | |
| 6 | Spec §12.6: the MCP extension with the anonymous webhook level plus App Service authentication accepts the gateway identity's token (smoke, T1) and refuses anything else (T3) | 5, 6 | |
| 7 | Spec §12.7: the gateway's MCP pass-through reaches `/runtime/webhooks/mcp`; `initialize`, `tools/list` and `tools/call` work through Basic v2 (with the harness's own Streamable HTTP client, not the `mcp` SDK, by the planning ruling; Claude Code is the real client) | 5, 6, 7 | |
| 8 | Spec §12.8: `validate-azure-ad-token` and `rate-limit-by-key` on `oid` work on the MCP API (T2, T4), and how many of T4's 30 calls the token bucket let through | 6 | |
| 9 | Spec §12.9: Claude Code connects with `headersHelper` and does not attempt OAuth discovery on a 401 | 7 | |
| 10 | Spec §12.10: the first `search_corpus` call after idle starts within Claude Code's MCP timeout without an always-ready instance (the smoke test's cold start in seconds; Claude Code's first connection) | 5, 7 | |
| 11 | Spec §12.11: destroying and recreating the functions stack leaves the Event Grid subscription working and queued messages draining (or "not tested") | 7 | |
| 12 | Spec §12.12: `emit-metric` records `Tool Calls` with the `Caller` dimension | 6 | |
| 13 | AI Search's paging (page size 50, `@search.nextPageParameters` with `skip`), stubbed offline: the largest artefact read back in full | 5 | |
| 14 | The ingest worker's log lines reach Application Insights through the Functions host, with no worker Application Insights package (counts and durations seen) | 6 | |
| 15 | Whether Event Grid's subject holds the raw or the percent-encoded blob name (an encoded name would 404 and go to the poison queue); the session used plain names only | 6 | |
| 16 | How the host shows the tool's thrown errors to an MCP caller (`query must not be empty` seen as what) | 5 | |
| 17 | A call without `top` arrives as null and gives 5 hits | 5 | |
| 18 | The host reads `extensions.mcp.system.webhookAuthorizationLevel`, so no `mcp_extension` key is needed | 5 | |
| 19 | The first bootstrap apply failed at the Event Grid subscription for role propagation, and was re-run (or did not) | 1 | |
| 20 | The tool app's registration issues v2 tokens (audience the client ID), and App Service authentication accepts them | 5 | |
| 21 | `azurerm_storage_queue.id` is the Resource Manager ID in azurerm 5.x, so the queue role scopes are valid | 1 | |
| 22 | The MCP pass-through path split (`/runtime/webhooks` plus `/mcp`) works; the Flex apps' `maximumInstanceCount` reads 10 | 4, 5 | |
| 23 | The service accepted the MCP API diagnostic's `largeLanguageModel` block on an `mcp` API (or it was dropped); any other azapi body error on the first apply | 4 | |
| 24 | The MCP API reports type `mcp` after the apply (`apiType` is write-only) | 4 | |
| 25 | I1 to I3 ran before I4's broken upload; the poison queue was empty at the start (the peek's count) | 6 | |
| 26 | Any upload watch that crashed or was interrupted, and how it was re-uploaded under a new name | 6 | |
| 27 | `emit-metric`'s `customMetrics` rows are stamped at the whole minute (T4's window is floored to it); Storage Queue Data Reader allows Get Queue Metadata under Entra (I4's count read) | 6 | |
| 28 | T4 ran with no other tool calls in progress, Claude Code included | 6 | |
| 29 | Project 4's follow-up: the bootstrap plan showed `azapi_update_resource.appi_custom_metrics`, and the portal reads "With dimensions" | 1, 2 | |
| 30 | Project 4's follow-up: revision 2's own `metrics = true` makes its calls emit the token metric | 2 | |
| 31 | Project 4's follow-up: B1, a burst of 60 at once, re-measured (the line it printed, and the backend-dependency query) | 2 | |
| 32 | The bootstrap plan only added: 41 to add (or the number, and why) | 1 | |
| 33 | The code packages: built with `dotnet publish`, zipped with `ZipFile`, deployed with `az functionapp deployment source config-zip`; both apps indexed one function | 4 | |
| 34 | The semantic ranker's free allowance was not exceeded (no ranked query refused) | 6 | |
| 35 | The Claude Code demo's excerpt is recorded, identifier-free, in the report | 7, 8 | |

### Session notes

None yet.
