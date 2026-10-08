# Runbook: the AI gateway session

This is the owner's script for the live session of the AI gateway project: apply the infrastructure,
run the measured test, publish the result, and destroy the gateway. The design and the rule that
decides the result are in the [gateway spec](superpowers/specs/2026-10-06-apim-ai-gateway-design.md),
§7 (the test), §10 (the delivery) and §12 (what is unverified). The stack is described in
[`infra/gateway/README.md`](../infra/gateway/README.md), whose "Live checks" section and destroy
fallback this runbook points to and does not repeat.

**Nothing in this runbook has been run.** Every step below is a plan. Results go in the findings
table at the end, and in the report, after the session.

## Rules

- **Every step marked "Ask first" waits for the owner's explicit yes.** The owner runs it, or
  approves each command before it runs. A yes for one step is not a yes for the next. The steps
  are the eight in spec §10, and there are more yeses inside them: setting the session secrets
  (before step 6), the workflow dispatch (step 6), the destroy (step 8) and deleting the session
  secrets (step 8).
- **Before any `az` command,** run the account check (`acctok`, defined in "Before the session"),
  and stop unless it prints `True`. It prints nothing else: `az account show` would print the
  account's email, which this runbook's own no-identifier rule forbids on a screen. The check is
  `True` only when the signed-in user's name does not contain the work domain (the owner's
  employer's, not the personal one) and the signed-in tenant is the bootstrap's `tenant_id`. The
  runbook does not name the work domain, because this file is public: the owner holds it in the
  user environment variable `WORK_DOMAIN`, set once in Windows' settings, outside every repository
  and never typed into a command. WSL sees it when `WSLENV` lists it (`WSLENV=WORK_DOMAIN`). If it
  is unset, the check prints `False`. Sign in with the personal account first.
- **On Windows, `az` needs the Norton CA bundle for that process only.** Nothing is persisted.
  In the PowerShell window that runs `az`, the harness and `gh`:

  ```powershell
  $env:REQUESTS_CA_BUNDLE = "$env:USERPROFILE\.azure\ca-bundle-with-norton.pem"
  ```

  The harness starts `az` itself to get tokens, so it needs the same variable in the same window.
  WSL has its own `az` session (the [bootstrap README](../infra/bootstrap/README.md#r1-sign-in-inside-wsl)
  has R1), and needs no bundle. Run its own `acctok` (below) there as well before Terraform.
- **Terraform runs only in WSL (Ubuntu),** with its own data directory for each stack:
  `TF_DATA_DIR=$HOME/tfdata/bootstrap-live` for the bootstrap and `$HOME/tfdata/gateway-live` for
  the gateway. Never plan or apply from Windows.
- **The harness runs from `eval/`,** in PowerShell, as `.venv/Scripts/python.exe -m app.gateway
  <command>`.
- **No identifier goes into a public file, a chat, a log or a screen.** That means no tenant,
  subscription, client or object ID, no hostname, no account name and no email. Outputs are read
  with `terraform output -raw` into the PowerShell process environment and never printed. The
  harness refuses to write a file that holds a GUID or an Azure hostname, and says which kind it
  found. If a command prints one anyway, do not paste it anywhere.
- **No key exists to handle.** If a step seems to need one, stop: something is wrong.
- **Write the findings in a scratch file, not in this runbook,** until the last measured command
  has run (step 5 and 6). A measured command refuses to start on a tree with uncommitted changes,
  and an edit to this file would be one. Use `eval/reports/findings.md`, which git ignores, and
  copy it into the table in step 7.
- **Do not run a session across 00:00 UTC (10:00 Brisbane).** The daily budget's window starts
  then (spec §12 item 11, to be confirmed), and so does the harness's count of the day's tokens.
- **Keep steps 5 and 6 clear of about 13:45 to 14:30 UTC (23:45 to 00:30 Brisbane).**
  `destroy.yml` runs nightly at 14:00 UTC and shares the concurrency group `releaselens-azure`
  with `gateway-check.yml`, so a B3 dispatch that lands then queues behind the destroy and can
  miss its 5-minute window. Steps 5 and 6 take about 40 minutes, so this means not starting step 5
  between about 13:05 and 14:30 UTC (23:05 and 00:30 Brisbane), as well as the one-hour rule in
  the clock check at step 5.

## What it costs

About US$1 for a session (spec §7.5, an estimate):

| Item | Cost |
|---|---|
| API Management, Basic v2 | about US$0.65 for about 3 hours, at US$0.20548 an hour (Retail Prices API, 2026-10-06), assuming each started hour is billed |
| Tokens | under US$0.10, at gpt-4.1-mini's list price |
| Monitoring ingestion | a few cents, within the free 5 GB |
| The bootstrap additions | nothing by the hour: Global Standard deployments bill per token |

**API Management bills by the hour from the moment step 2 starts, called or not.** If the session
stops for any reason, go to step 8. Forgotten, it costs about US$5 a day. The budget alert stops
nothing.

## Before the session

- The plan 1 pull request is merged to `main`. A dispatched workflow runs only from a file on
  the default branch, so `gateway-check.yml` must be there for step 6. The session works from an
  up-to-date `main`, or from a branch with nothing else on it.
- The GitHub environment `azure` exists, with its four secrets and four variables from the
  bootstrap's runbook (R10), including the variable `AZURE_CLIENT_ID` and the secret
  `AZURE_TENANT_ID`. The check workflow reads both.
- `eval/.venv` exists and `.venv/Scripts/python.exe -m pytest -q`, run from `eval/`, passes.
- About 3 hours free, and at least one hour of the UTC day left when step 5 begins, but not a
  step 5 that starts between about 13:05 and 14:30 UTC (see the clock check there). The session
  must not cross 00:00 UTC (10:00 Brisbane).

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
acctok      # must print True
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
acctok      # must print True
```

A `False` means the work account is signed in, the wrong tenant is, `az` is not signed in, or
`WORK_DOMAIN` is not set. Do not go on: sign in with the personal account and run it again.

## Step 1. Apply the bootstrap

**Ask first.** The second Azure OpenAI account, the new deployments, the gateway identity, the
Entra app registration, the monitoring and the state container. Cost: nothing by the hour.

### 1a. Read-only checks, before applying anything

PowerShell, after `acctok` prints `True`. These change nothing:

```powershell
az cognitiveservices usage list --location southeastasia --query "[?contains(name.value, 'gpt-4.1-mini')]" -o table
az cognitiveservices usage list --location australiaeast --query "[?contains(name.value, 'gpt-4.1-mini')]" -o table
az cognitiveservices model list --location southeastasia --query "[?model.name=='gpt-4.1-mini'].{version: model.version, skus: model.skus[].name}" -o json
```

Check, and write the answers in the scratch file:
- Southeast Asia has a **Global Standard** quota line for gpt-4.1-mini with at least the
  `failover_capacity` the bootstrap asks for (default 100, which is 100,000 tokens a minute).
  That is spec §12 item 2.
- Whether the two regions' lines are separate or pooled (the limit and the usage move together).
  The tiny primary throttles on its own capacity either way.
- Version **`2025-04-14`** is on offer in Southeast Asia, with `GlobalStandard` among its SKUs.

If either is missing, **stop.** The deployments would fail. Choose another region or capacity
with the owner, as a design change (spec §3.1), before going on. If the CLI's output has a
different shape from what the queries expect, the portal shows the same under the account
region's quotas page.

### 1b. Plan

WSL, in `infra/bootstrap`. The live data directory already holds the backend. The account name
goes into a variable and is not printed:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/bootstrap   # adjust to your clone
export TF_DATA_DIR="$HOME/tfdata/bootstrap-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
acctok                                                    # must print True: the personal account, not the work one
ACCOUNT=$(terraform output -raw tfstate_storage_account)
terraform init -input=false -backend-config=storage_account_name="$ACCOUNT"
terraform plan -out=tfplan
terraform show -no-color tfplan | grep -E 'kind |local_auth_enabled|local_authentication_enabled|app_role_assignment_required|version_upgrade_option|capacity|GlobalStandard|2025-04-14'
```

`init` is needed because the `azuread` provider is new. The plan reads the owner's git-ignored
`terraform.tfvars`, which needs no new variable: `failover_location` and `failover_capacity` have
defaults. Read the whole plan, then check:
- **It only adds.** Any delete or replace is wrong: the bootstrap's lock would block it, and
  nothing in this change should need it. Stop and find out why.
- **What it adds:** the second account in Southeast Asia and its two deployments; the
  `releaselens-chat-failover-test` deployment on the first account; the gateway identity; the
  Entra app, its service principal, pre-authorisation and three app role assignments; the Log
  Analytics workspace and Application Insights; the `tfstate-gateway` container; and the new role
  assignments. The provider registrations for `Microsoft.ApiManagement` and
  `Microsoft.OperationalInsights` may show too.
- **The grep shows:**
  - `kind = "AIServices"` on both accounts
  - `local_auth_enabled = false` on both accounts
  - `local_authentication_enabled = false` on **both** Log Analytics and Application Insights.
    The bootstrap README's R8 grep does not match this spelling, so it is in the grep above.
  - `app_role_assignment_required = true` on the gateway's service principal. R8's grep misses
    this too.
  - model version `2025-04-14` and `version_upgrade_option = "NoAutoUpgrade"` on every new
    deployment, SKU `GlobalStandard`
  - capacity `1` on the australiaeast failover-test deployment, and the value of
    `failover_capacity` on the two Southeast Asia deployments
- **The Azure CLI's pre-authorisation resolves.** The plan must contain
  `azuread_application_pre_authorized.azure_cli` with an `authorized_client_id` value, and no
  error about a missing key. The key `MicrosoftAzureCli` in
  `azuread_application_published_app_ids` was confirmed from the provider's source and has not
  been run (spec §12 item 9). If the plan fails there, stop: the key is wrong, and the fix is a
  code change.

### 1c. Apply

```bash
terraform apply tfplan
```

- **A 409 on the first apply is expected to be possible.** Writing several deployments to one
  account in parallel may return a conflict on one of them. Run `terraform plan -out=tfplan` and
  `terraform apply tfplan` again; the second run finishes the rest. Write it in the findings.
- **Capacity 1 on a Global Standard deployment** may be refused (spec §12 item 3). If it is, stop
  and take it to the controller: the test needs a tiny primary.
- If the apply fails at the Entra app, the signed-in owner lacks the right to register
  applications or assign app roles (R8 of the bootstrap README says which).

Then wait about ten minutes before step 2, for the new role assignments (the gateway identity's
and the owner's on `tfstate-gateway`) to propagate. Check the new outputs exist, without
printing them:

```bash
for o in gateway_identity_client_id gateway_app_client_id failover_openai_backend_url app_insights_id; do
  terraform output -raw "$o" > /dev/null && echo "$o: set"
done
```

### 1d. The portal step: custom metrics with dimensions

Terraform cannot set this (spec §12 item 8, the plan's ruling). In the Azure portal, open
`appi-releaselens`, then **Configure**, **Usage and estimated costs**, **Custom metrics
(Preview)**, choose **With dimensions**, and save. Without it the `Caller` dimension of the
usage metric is dropped, and B5 cannot work. The menu names are as the portal showed them when
this was written, not checked; if they differ, search the resource's settings for "custom
metrics".

**Where results go:** findings rows 2 and 3 (the quota, capacity 1), 8 (the portal step), 9 (the
pre-authorisation), 25 (the 409) and 27 (the plan check).

## Step 2. Apply the gateway

**Ask first.** From here API Management bills by the hour, about US$0.21, until step 8. Basic v2
is expected to take about 5 to 10 minutes to create (spec §12 item 1): note the time it takes.

The first time, create the gateway stack's git-ignored `terraform.tfvars` in your own window,
from [`terraform.tfvars.example`](../infra/gateway/terraform.tfvars.example). It needs the
subscription, the state storage account's name and a publisher email. Do not paste any of them
anywhere else. Then, in WSL, from `infra/gateway`:

```bash
cd /mnt/e/Projects/ReleaseLens/infra/gateway   # adjust to your clone
export TF_DATA_DIR="$HOME/tfdata/gateway-live" TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
```

and the start-of-session check from [that README](../infra/gateway/README.md#apply-and-destroy).
In PowerShell, this must print `False`:

```powershell
az group exists --name rg-releaselens-gateway
```

Then, in WSL:

```bash
ACCOUNT=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir=../bootstrap output -raw tfstate_storage_account)
terraform init -backend-config=storage_account_name="$ACCOUNT"
terraform plan -out=tfplan
terraform show -no-color tfplan | grep -E 'subscription_required|azurerm_api_management_(product|subscription)|role_assignment'
terraform apply tfplan
```

Read the plan. It must add one resource group, API Management `BasicV2_1`, the named values, the
version set and API, one operation, the two revisions, three backends, the logger and diagnostic,
and two policies. The grep must show `subscription_required = false` and **no** product,
subscription or role assignment. If any appears, stop.

**What to watch for in the apply** (the stack README's "Live checks" section has the detail, and
is the authority if they ever differ):
- **Revision 2 may be rejected on create.** It sets `version` and `version_set_id` beside
  `source_api_id`, and the service may refuse that. If the apply stops there, API Management
  already exists and bills. Record the error text, without any identifier, and take it to the
  controller: the fix is a change to revision 2's resource, then a re-apply, or a destroy
  (step 8).
- **Whether revision 2 inherits the diagnostic** is settled in step 3.
- **Every later plan shows an in-place update of both policies.** API Management normalises the
  stored XML. It is harmless: it writes the same file to the same revision.

Then read the two settings into the window, without printing them:

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

And the label map the harness uses to turn an `oid` into `owner` or `deploy`. It stays in this
window's environment, never in a file:

```powershell
$owner  = az ad signed-in-user show --query id -o tsv
$deploy = az identity show --name id-releaselens-deploy --resource-group rg-releaselens-bootstrap --query principalId -o tsv
$env:GATEWAY_CALLER_LABELS = @{ $owner = 'owner'; $deploy = 'deploy' } | ConvertTo-Json -Compress
$owner = $deploy = $null
```

**Where results go:** findings rows 1 (the creation time), 17 (revision 2 on create) and 26 (the
policy updates).

## Step 3. Smoke test

**Ask first.** A few calls, a few cents. `smoke` runs before the freeze, so it has no guard, and
writes to its own directory under `eval/reports/` so that its calls are not counted by the
measured commands (see step 5). From `eval/`:

```powershell
cd eval
.venv/Scripts/python.exe -m app.gateway smoke --tenant $env:GW_TENANT --direct-url $env:GW_DIRECT_URL --gateway-url $env:GW_BASE --scope $env:GW_SCOPE --out reports/step3-a
```

It makes one call straight to the primary account, one through the gateway and one at revision 2,
and prints one line for each: the status, both region signals and the names of the headers. Each
must be `pass` (a 200 with no hostname in any body or header value). Then:

1. **Both ways work.** `smoke-direct` and `smoke-gateway` are 200. A 200 through the gateway also
   shows that the owner's token carries the `Gateway.Invoke` role (spec §12 item 10) and that the
   Azure CLI could get a token for the gateway.
2. **The backend URL ruling.** The backends' URLs end in `/openai/v1`, and the gateway sends only
   the operation's path after that. A 200 settles it. If the gateway answers 404, the URL is
   wrong: it is a one-line change to the two backends. Record it and stop.
3. **Revision 2 at `;rev=2`.** `smoke-rev2` is 200 at `/openai/v1;rev=2/chat/completions`. If it
   is not, revision 2's path is wrong. Its header list must show `x-ms-region`, and no
   backend-only headers. Revision 2 keeps `x-ms-region`, `retry-after`, `retry-after-ms` and the
   content headers (spec §4.3).
4. **Revision 2 reached Application Insights.** After a few minutes, in the portal's Logs for
   `appi-releaselens`, the `requests` table shows the revision 2 call, and the check in step 3,
   point 8 shows its tokens. If the call is missing from `requests`, or is there but its tokens
   are not in `customMetrics`, revision 2 did not inherit the diagnostic or its `metrics = true`.
   **Then do not set `release_revision_2`** (point 9 is skipped). Revision 1 is the current
   revision and serves the plain path, so run steps 4 to 6 on it, with the same settings. Record
   it in finding row 17: revision 2 did not inherit the diagnostic, and the session ran on
   revision 1.
5. **No hostname in any body or header.** The three checks above fail if one appears. Also look at
   the error bodies in point 6 and in step 6's B4 run: a gateway error carries a fixed message and
   a status code, and nothing else.
6. **A filtered prompt's 400 passes through.** The harness has no command for this, so call the
   gateway once by hand. The owner chooses a short prompt that the account's default content
   filter blocks. It is not written in any file. Prints only the status and the error code:

   ```powershell
   $token = az account get-access-token --scope $env:GW_SCOPE --query accessToken -o tsv
   $body = @{ model = 'releaselens-chat'; max_tokens = 40; messages = @(@{ role = 'user'; content = (Read-Host 'Prompt') }) } | ConvertTo-Json -Depth 5
   $r = Invoke-WebRequest -Method Post -Uri ($env:GW_BASE.TrimEnd('/') + '/chat/completions') -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $body -SkipHttpErrorCheck
   $r.StatusCode; ($r.Content | ConvertFrom-Json).error.code
   $token = $null
   ```

   It passes on a 400 whose error code names the content filter, which is what the app's
   existing `ContentFilteredException` handling relies on. If two prompts are not filtered, write
   "not provoked" and move on. Do not try harder prompts.
7. **Which region signal, and the secondary.** The primary answers every call while its breaker
   is closed, so the first smoke shows only the primary's values. To see the secondary answer,
   send the same three calls to the tiny deployment. Capacity 1 allows 1,000 tokens a minute. A
   call is about 330 to 350 tokens (a prompt of about 310 tokens and an answer of up to 40), so
   three calls come to about 1,000 to 1,050 and may pass the limit by tokens, and the
   requests-per-minute limit that goes with the capacity may stop them too. Either gives the 429.
   Whether three calls reach a limit is not known: this is a try, not a certainty. The aim is a
   429 from the primary, which the gateway re-sends to the secondary. **`smoke-direct` printing
   `fail` on a 429 from the tiny deployment is expected here:** it is the primary being
   throttled, not a fault, and the "each must be `pass`" rule above is for the first smoke only:

   ```powershell
   .venv/Scripts/python.exe -m app.gateway smoke --tenant $env:GW_TENANT --direct-url $env:GW_DIRECT_URL --gateway-url $env:GW_BASE --scope $env:GW_SCOPE --deployment releaselens-chat-failover-test --out reports/step3-b
   ```

   Wait a minute first, so the first smoke's breaker has reset. Then read the two signals on
   every `smoke-gateway` and `smoke-rev2` line of both smoke runs that carried a model's answer.
   `x-releaselens-backend` is set by the outbound policy from the host the request went to, so it
   names the account that answered. `x-ms-region` may name the region that processed the prompt
   instead (spec §4.4), and on a Global Standard deployment that need not be the account's.
   - **Freeze `x-ms-region` only if it agrees with the label on every one of those calls,
     including at least one that the secondary answered.** Agreeing means `Australia East` with
     `primary` and `Southeast Asia` with `secondary`, the two strings the harness maps (spec §12
     item 4). A call with `x-ms-region` absent, or with another region's name, does not agree.
   - **Otherwise freeze `x-releaselens-backend`.** One disagreement on one call is enough.
   - **Write both down** whichever is frozen: whether they agreed, and on which calls (finding
     rows 4 and 18). The harness records both raw values for every request in the measured runs
     as well, and the report prints how many responses had the two disagree. The rule that
     decides the verdict does not change with the signal.
   - If no call went to the secondary after two tries, write that down, wait a minute and try
     once more. If the secondary never answers, the failover itself is in doubt (spec §12 item 5):
     take it to the controller before freezing.
8. **The metric's name.** Wait a few minutes for ingestion, then in the portal's Logs for
   `appi-releaselens`:

   ```kusto
   customMetrics
   | where timestamp > ago(1h)
   | summarize calls = count(), tokens = sum(valueSum) by name, ns = tostring(customDimensions["_MS.MetricNamespace"]), caller_set = isnotempty(tostring(customDimensions["Caller"]))
   ```

   The harness's B5 query looks for the name `Total Tokens` in the namespace
   `releaselens-gateway`, with `Caller` as a dimension. If any of the three differs, B5's query
   in `eval/app/gateway/metric.py` needs the change, which is a code change before the freeze.
   The number of calls and tokens here also tells you whether the smoke calls were counted.
9. **Then make revision 2 current,** only if point 4 passed. Only now, in WSL:

   ```bash
   terraform plan -var release_revision_2=true -out=tfplan
   terraform apply tfplan
   ```

   The plan should add the release and change nothing else, apart from the policy updates (see
   [the stack README](../infra/gateway/README.md#apply-and-destroy), which also says what a
   created or replaced operation or diagnostic would mean). Check that `v1` still serves: run
   the first smoke command again with `--out reports/step3-c`. Then `release_revision_2 = true`
   is the state to be in for the measured runs, and from here the plain path serves revision 2.

The 503 when both backends are tripped, and the status on a backend connection failure, cannot be
provoked from here: the secondary has capacity 100, and no connection can be broken without
changing the stack. Do not try. Read them from the measured runs' status counts if they ever
occur, and otherwise write "not observed".

**Where results go:** findings rows 4, 5, 7, 8, 10, 14 to 21.

## Step 4. Commit the rule

**Ask first.** Spec §7.4: the rule, the workload and the region signal are committed before the
first measured request, and nothing about them changes afterwards. Set the signal in
`eval/app/gateway/freeze.json` to the one that point 7 chose, either `"x-ms-region"` or
`"x-releaselens-backend"`, and commit it. PowerShell, from the repository root (`cd ..` if the
window is still in `eval/`):

```powershell
git add eval/app/gateway/freeze.json
git commit -m "eval: freeze the region signal the smoke test chose"
git status --short
```

`git status --short` must print nothing: a measured command refuses to start on a dirty tree, and
the report cites this commit. Do not push. Until `freeze.json` names a signal, `failover`,
`minute-budget` and `day-budget` all refuse to run.

**Where results go:** the report cites the commit.

## Step 5. Run test 1, then test 2 up to B2

**Ask first.** About 40 minutes. The only tokens are about US$0.10. The harness commands run from
`eval/` (`cd eval`).

### Before step 6: set the two session secrets

**Ask first.** This changes GitHub settings, and it is done now because B3 has 5 minutes after
B2's 403, which leaves no time for it then. The two secrets are the gateway's base URL and its
scope, which holds the gateway app's client ID, set in the environment `azure` for the session
and deleted in step 8. As secrets, GitHub masks them in the public log. In PowerShell, from the
repository root, with the values still in the window's environment. They go in through the pipe,
not through an argument:

```powershell
$env:GW_BASE  | gh secret set GATEWAY_BASE_URL --env azure
$env:GW_SCOPE | gh secret set GATEWAY_SCOPE    --env azure
gh secret list --env azure
```

The list must show the four secrets from the bootstrap's runbook and these two.

Check the clock, in UTC. Two things must both hold:
- **At least one hour of the UTC day must be left** (before 23:00 UTC, 09:00 Brisbane), because
  steps 5 and 6 take about 40 minutes and B2 is invalid if they cross 00:00 UTC.
- **Steps 5 and 6 must not meet the nightly destroy** (`destroy.yml`, 14:00 UTC, in the same
  concurrency group as B3's workflow): do not start step 5 between about 13:05 and 14:30 UTC (23:05
  and 00:30 Brisbane). Before 13:05 UTC, or after 14:30 UTC and before 23:00 UTC, is fine.
  If B3 is queued behind a destroy anyway, let it run and write the delay in the findings; do not
  dispatch a second one (B5).

If either fails, **stop before step 5** and resume another day. The gateway bills meanwhile, so
destroy it (step 8) and start a new session at step 2; the bootstrap stays as it is. Then note the
start of B5's window. It is the moment before the first measured call through the gateway, and step 3's calls,
which are in other directories, fall outside it:

```powershell
$env:SINCE = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
```

**Test 1, the order is fixed: before, a pause of at least 2 minutes, after.**

```powershell
.venv/Scripts/python.exe -m app.gateway failover --mode direct --tenant $env:GW_TENANT --base-url $env:GW_DIRECT_URL
Start-Sleep -Seconds 150
.venv/Scripts/python.exe -m app.gateway failover --mode gateway --tenant $env:GW_TENANT --base-url $env:GW_BASE --scope $env:GW_SCOPE
```

Each run takes about 3 minutes (45 requests, one every 4 seconds) and writes
`eval/reports/failover-<mode>.jsonl`, then prints the number of records and the status counts.
- A measured run is never overwritten. A run that crashes or is interrupted (Ctrl+C) still
  writes the records of the requests that finished, and then reports the error. To repeat a run
  that is wrong or crashed, **rename its file in place, keeping the start of its name**
  (`failover-gateway.jsonl` to `failover-gateway-crashed.jsonl`), then run it again. Do not move
  it to another directory, copy it or edit it: the gateway counted its tokens, and B2 and B5 find
  the gateway's record files by name prefix and (B2) by modification time. A before-run
  (`failover-direct*`) never reaches the gateway, and is not counted.
- The rule was fixed before the run: a before-run with fewer than 14 failures out of 45 means
  the test did not stress the primary, and the verdict is **inconclusive**, whatever the after
  run shows. Report it as that.
- **The Retry-After the tiny deployment sends (spec §12 item 6)** is not printed. Read it off the
  before-run: each record holds `waited_ms`. A request the client waited for (`waited_ms` above
  0) had a short advice. A 429 failure with `waited_ms` 0 had none or one over 3,000 ms. Write
  down which, and what it means for how long the primary's breaker stays open:

  ```powershell
  Get-Content reports/failover-direct.jsonl | ConvertFrom-Json | Select-Object seq, status, waited_ms, latency_ms | Format-Table
  ```

- **The retry goes to the secondary (spec §12 item 5)** if some after-run request has
  `region` `secondary` in `failover-gateway.jsonl`. The verdict needs at least one of those.

**Test 2, up to B2.**

B1 first, then B2, because B2 spends the day's budget. B1 sends until a request is refused:

```powershell
.venv/Scripts/python.exe -m app.gateway minute-budget --tenant $env:GW_TENANT --base-url $env:GW_BASE --scope $env:GW_SCOPE
```

It must print `B1: pass`: a 429 with `Retry-After` and no `x-ms-region`. That is the client's
side, and its "no model call" half rests on `x-ms-region` being absent from a gateway refusal
while present on an answer. **If step 3's smoke showed `x-ms-region` absent on normal answers too,
that half proves nothing:** the harness's `B1: pass` then says only that a 429 with `Retry-After`
came, and the Application Insights query below alone decides B1. Write that in finding row 22.
**B1's confirmation is in Application Insights:** the refused request must have no backend
dependency. After a few minutes of ingestion, in the portal's Logs for `appi-releaselens`:

```kusto
requests
| where timestamp > ago(1h) and resultCode == "429"
| join kind=leftouter (dependencies | project operation_Id, depTarget = target) on operation_Id
| summarize backendCalls = countif(isnotempty(depTarget)) by operation_Id, timestamp
| order by timestamp desc
```

The refused request, by its time, must show `backendCalls` 0. The query was written from the
tables' usual columns, not run: adjust the names if the portal rejects it, and keep the meaning.
Do not paste its output anywhere that shows `target`; the query summarises it away. Also read the
`retry_after` field in the last line of `reports/budget-minute.jsonl`: whole seconds answer
spec §12 item 12.

Then B2, which continues past the minute budget's resets until the daily budget answers 403:

```powershell
.venv/Scripts/python.exe -m app.gateway day-budget --tenant $env:GW_TENANT --base-url $env:GW_BASE --scope $env:GW_SCOPE
```

It waits out each 429 for the advised time, and takes several minutes. It passes only on a 403
that comes after **at least 45,000 tokens** were recorded today (the daily quota is 50,000, and
the floor allows for the policy's estimates). Its detail prints the figure.
- The harness counts the day's tokens from this directory's records of calls the gateway
  answered, by file modification time since 00:00 UTC: every `failover-gateway*.jsonl` and
  `budget-*.jsonl`, and any `smoke*.jsonl`. Step 3's calls are in other directories and spend
  about 3,000 of the 50,000, so a 403 comes sooner than the records say. The floor leaves about
  5,000 tokens of room for that: keep step 3 to what it lists. The run's own file does not count
  twice: its 200s are counted as the run's own.
- **After a crash, B2 can be re-run once the file is renamed.** Its records are saved even then,
  and a re-run refuses to overwrite them. Rename `budget-day.jsonl` in place (to
  `budget-day-crashed.jsonl`: keep the `budget-` start, and rename it, do not copy or move it) and
  delete or rename `check-b2.json`, then run B2 again. The renamed file still counts toward the
  45,000 floor and toward B5's totals, with its original modification time. The detail shows the
  figure. If it fails, say so in the findings.

**When B2 prints its result, go straight on to step 6.** B3 must start within 5 minutes of the
403.

**Where results go:** findings rows 5, 6, 11, 12, 19, 20, 22 and 23. The records are in
`eval/reports/`.

## Step 6. The workflow (B3), then B4 and B5

**Ask first** for the dispatch. The two secrets were set at the start of step 5.

**B3.** Dispatch the workflow on the owner's yes, **within 5 minutes of the 403**:

```powershell
gh workflow run gateway-check.yml --ref main
gh run list --workflow gateway-check.yml --limit 1
gh run watch <run-id>
gh run view <run-id> --log | Select-String 'status='
```

The workflow signs in as `id-releaselens-deploy`, makes one call and prints one line:
`status=200 total_tokens=<n>`. **B3 passes on `status=200`.** It runs once, inside B5's window:
a second run would add tokens that the harness cannot see. Write down `<n>`: it is the deploy
identity's total for B5. If it prints `status=error`, the log has nothing more by design. The
likely causes are the two secrets, the deploy identity's `Gateway.Invoke` assignment (spec §3.1)
or the 5-minute window.

**B4,** which spends nothing:

```powershell
.venv/Scripts/python.exe -m app.gateway access --tenant $env:GW_TENANT --base-url $env:GW_BASE
```

It must print `B4: pass`: a call with no token and a call with the owner's token for
`https://ai.azure.com` both get 401.

**B5.** Wait a few minutes after the last call for ingestion. Then compare the metric's total
per caller with the totals the clients recorded, and give the deploy identity's total from the
workflow log, which has no client record. The metric lags, so B5 can be re-run (it overwrites
only its own result):

```powershell
.venv/Scripts/python.exe -m app.gateway metric-totals --tenant $env:GW_TENANT --app-id $env:APPI_ID --since $env:SINCE --client-total deploy=<n>
```

It passes if both callers are in the metric and each total is within 2% of the clients'. It
prints both sets of totals, as labels. A mismatch has a short list of causes: the workflow ran
twice or outside the window, a refused call (the 429s and 403s) emitted tokens into the metric
(settle that and write it down: spec §7.3's B5 does not say), a call was made by hand inside the
window, or the metric is still arriving.

The Application Insights query uses the owner's Entra token with no key, and the resource has
local authentication off. If the query API refuses it, take it to the controller: this is a live
check the offline tests could not make.

**Where results go:** findings rows 15, 21 and 24.

## Step 7. Publish the report and the README's results

**Ask first.** Run the report before committing anything after step 4, so that it cites the commit
the runs used. The harness does not measure B3, so enter the workflow's outcome:

```powershell
.venv/Scripts/python.exe -m app.gateway report --b3 passed --report-out ../docs/gateway-report.md
```

(Use `--b3 failed` if it did not.) The report holds the verdict, both runs' counts and latencies,
B1 to B5, the frozen signal, how many after-run responses had the two region signals disagree,
and the commit, with labels instead of identifiers. The harness refuses to write it if it holds a
GUID or an Azure hostname. Read it before committing.

Then copy the scratch file's findings into the table below, change the README's
[AI gateway section](../README.md#ai-gateway) to say what was measured, and change the status
lines of this runbook, the [gateway README](../infra/gateway/README.md) and
[`docs/architecture.md`](architecture.md) from "not yet run" to what happened. Commit them as one
commit. Do not push without the owner's yes. **Both verdicts are publishable.** "Failover did not
hold" and "inconclusive" are written up as plainly as "held".

## Step 8. Destroy the gateway

**Ask first.** Do this whether the session succeeded or not. It ends the hourly billing. **If
anything fails or goes wrong at any point, the session ends at step 8, and step 8 ends only when
both checks below pass.** WSL, from `infra/gateway` with `TF_DATA_DIR=$HOME/tfdata/gateway-live`:

```bash
terraform destroy
```

- **If the destroy stops on revision 2**, use the fallback in
  [the stack README](../infra/gateway/README.md#apply-and-destroy): `terraform state rm
  'azurerm_api_management_api.v1_rev2'` (and the next resource it stops on, such as
  `azurerm_api_management_api.v1`), then destroy again. Deleting the service removes everything
  inside it. Record whether it was needed (finding 26).
- Each destroy's `purge_soft_delete_on_destroy` must purge the deleted service (spec §12 item 13).

Then the two checks, in PowerShell after `acctok` prints `True`:

```powershell
az group exists --name rg-releaselens-gateway
az apim deletedservice list --query "[?starts_with(name, 'apim-releaselens-')].name" -o tsv
```

The first must print `false`. The second must print **nothing**: no gateway instance is left
soft-deleted. If both pass, go on to the secrets. If not, use the fallback below, then run both
checks again.

### If the destroy fails, or a check does not pass

**Ask first.** This applies when `terraform destroy` fails for any reason other than revision 2
(after the `state rm` fallback above, if that was the reason), when `az group exists` prints
`true`, or when the second check lists a name. Do not leave API Management running: it bills about
US$5 a day. Record the error text, without any identifier.

1. **Delete the resource group,** which removes the service and everything in it:

   ```powershell
   az group delete --name rg-releaselens-gateway --yes
   ```

2. **Purge the soft-deleted service,** if the second check lists a name. Read the name into the
   shell without printing it. It must not be pasted anywhere:

   ```powershell
   $apim = az apim deletedservice list --query "[?starts_with(name, 'apim-releaselens-')].name | [0]" -o tsv
   az apim deletedservice purge --service-name $apim --location australiaeast
   $apim = $null
   ```

   A purge by hand is needed because the name stays reserved for 48 hours otherwise. Record that
   the destroy's own purge did not succeed as the owner (finding 13).
3. **Run both checks again.** The first must print `false` and the second nothing. If either does
   not, repeat the fallback or stop and take it to the owner now: step 8 is not finished.

After a group delete the stack's state still lists the deleted resources. Before the next session,
run `terraform destroy` again in `infra/gateway`: it finds nothing left and clears the state. If it
cannot, take it to the owner before the next apply.

### Delete the two session secrets

**Ask first.** Do it the same day, even if the session was abandoned, and check that only the four
remain:

```powershell
gh secret delete GATEWAY_BASE_URL --env azure
gh secret delete GATEWAY_SCOPE --env azure
gh secret list --env azure
```

Last, clear the window's variables and close it: `Remove-Item Env:GW_TENANT, Env:GW_DIRECT_URL,
Env:GW_BASE, Env:GW_SCOPE, Env:APPI_ID, Env:SINCE, Env:GATEWAY_CALLER_LABELS`. The bootstrap stays
as it is: nothing in it bills by the hour.

**Where results go:** findings rows 13 and 26.

## Findings

One row for every item in spec §12, then every item that only a live session can settle. Fill the
**Finding** column from the scratch file in step 7. "Not observed" is a finding too. Spec §12 item
numbers are in the first column.

| # | Item | Step | Finding |
|---|---|---|---|
| 1 | Spec §12.1: Basic v2 creates in about 5 to 10 minutes in australiaeast | 2 | |
| 2 | Spec §12.2: Southeast Asia's Global Standard quota for gpt-4.1-mini `2025-04-14`, and whether quota is pooled across regions | 1a | |
| 3 | Spec §12.3: capacity 1 is accepted for a Global Standard deployment | 1c | |
| 4 | Spec §12.4: `x-ms-region` is present and names the region (the account, or the processing region); which signal the rule uses | 3 | |
| 5 | Spec §12.5: within one request, the retry after the breaker trips goes to the secondary | 3, 5 | |
| 6 | Spec §12.6: the Retry-After the tiny deployment sends is short, and how long the primary stays tripped | 5 | |
| 7 | Spec §12.7: Cognitive Services OpenAI User is enough for the gateway identity | 3 | |
| 8 | Spec §12.8: the logger ingests with Entra on Basic v2, and custom metrics with dimensions (the portal step) | 1d, 3 | |
| 9 | Spec §12.9: the key `MicrosoftAzureCli` in `azuread_application_published_app_ids`; the first bootstrap plan resolves the pre-authorisation | 1b | |
| 10 | Spec §12.10: a user's app-role assignment appears in the `roles` claim of the token `az` gets | 3 | |
| 11 | Spec §12.11: the daily quota's window starts at 00:00 UTC, which fixes when B2 can run | 5 | |
| 12 | Spec §12.12: `llm-token-limit` sends `Retry-After` in seconds | 5 | |
| 13 | Spec §12.13: the purge on destroy succeeds as the owner | 8 | |
| 14 | A filtered prompt's 400 passes through the gateway unchanged | 3 | |
| 15 | No hostname in any body or header, including the gateway's own error bodies | 3, 6 | |
| 16 | The backend URLs ending in `/openai/v1` work, and revision 2 answers at `/openai/v1;rev=2/chat/completions` | 3 | |
| 17 | Revision 2 was created by the apply (it sets `version` and `version_set_id` beside `source_api_id`), and inherits the API diagnostic and its `metrics = true` (if not, `release_revision_2` stayed false and steps 4 to 6 ran on revision 1) | 2, 3 | |
| 18 | `x-releaselens-backend` reflects the pool member that answered, and whether it agreed with `x-ms-region` on every smoke call (and, in the report, on how many measured responses it did not) | 3, 7 | |
| 19 | The status and path of the 503 when both backends are tripped (whether it carries the label `secondary`) | 3, 5 | |
| 20 | The status when a backend connection fails | 3, 5 | |
| 21 | The metric's name `Total Tokens` and the dimension key `_MS.MetricNamespace`; whether refused calls (429, 403) emit token metrics | 3, 6 | |
| 22 | B1's Application Insights check: the refused request has no backend dependency (and, if `x-ms-region` was absent on normal answers, that only this check decided B1) | 5 | |
| 23 | B2: the 403 came after at least 45,000 recorded tokens (the figure), and the session did not cross 00:00 UTC | 5 | |
| 24 | B5: the deploy identity's total is the `total_tokens` the workflow log printed, entered with `--client-total` | 6 | |
| 25 | The first apply of the bootstrap returned a 409 on parallel deployment writes (and was re-run) | 1c | |
| 26 | Every later plan showed an in-place update of both policies (harmless), and the destroy after the release did or did not stop on revision 2 | 2, 8 | |
| 27 | The bootstrap plan check found `local_authentication_enabled = false` and `app_role_assignment_required = true`, which R8's grep misses | 1b | |

## Maintenance: the check workflow's requirements

`gateway-check.yml` installs `eval/requirements-gateway-check.txt` with `--require-hashes`,
`--only-binary=:all:` and `--no-deps`, because that job holds an OIDC token for the deploy
identity. The versions equal those in `eval/requirements.txt`, and
`tests/infra/test_gateway_check_requirements.py` checks that. **When a pin in
`eval/requirements.txt` changes, regenerate that package's hashes in the lock file:** take the
hashes of the new version's wheels for CPython 3.14 on x86_64 Linux (glibc) from the package's
release on PyPI, or download the wheel for that platform with `pip download --no-deps
--only-binary=:all: --platform manylinux_2_28_x86_64 --python-version 3.14 <name>==<version>` and
run `pip hash` on each file. Put every wheel the platform can use on its own `--hash=` line, then
run `python -m pytest tests/infra -q`.
