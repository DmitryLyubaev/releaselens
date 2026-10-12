# Runbook: project 6 Webapp session

Implementation is ready for offline review. **No project-6 live check has run.** Follow the
[approved design](superpowers/specs/2026-10-12-app-service-front-end-design.md), then record
facts in the [pending report](webapp-report.md). A failed or missing check stays failed or
not run. The target is one four-hour session, including the token-expiry wait.

**The nightly app-stack destroy does not remove the webapp stack.** An S1 plan bills while
idle. This runbook grants no unattended cleanup, destroy, purge or permanent deletion.
If the session stops, obtain separate teardown approval instead of leaving the plan overnight.

## Before starting the clock

Finish code review, obtain separate approval to push/create a PR, and merge through the
owner's normal process. Start from current `main` with green `build-and-test` CI for that
exact SHA. A skipped PR-only Terraform job on a main push proves no live configuration.
Docker-dependent suites run in CI; they were unavailable locally during implementation.

Use WSL for Terraform and live HTTP, with Python 3.14 and an existing personal `az` login.
The owner signs in themselves, in WSL and in each browser slot. Never request their password.
Do not introduce an app password, certificate, key, basic SCM authentication or another
GitHub federated credential. Unsupported keyless deployment or refresh is a failed finding.

Use a clean checkout. The original untracked `HANDOVER-project-6.md` must be resolved with
the owner before using that checkout for measurements; preserve it, without silently
committing or deleting it. The isolated native worktree does not contain that file.
Keep findings and downloaded sanitized workflow observations under ignored
`eval/reports/webapp/`. Edit public documentation after the last measurement, because the
harness refuses a dirty tracked or untracked tree.

Provide expected account values through the owner's existing private process environment:
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `WEBAPP_OWNER_PRINCIPAL` and `WORK_DOMAIN`.
Forward those variable names through `WSLENV` if needed, without displaying their values.
The work domain must be set and excluded from the signed-in account. No `.env`, private
tfvars, credential file or token file is part of this procedure. Disable shell tracing.

Define these WSL helpers. `acctok` captures the account and prints only a Boolean; it is
the sole initial account lookup and runs before tokens, resource lookups or Terraform.
Call it again before every Azure operation. A failed guard ends the operation.

```bash
acctok() {
  python3.14 - <<'PY'
import json, os, subprocess, sys
try:
    p = subprocess.run(['az','account','show','--only-show-errors','-o','json'],
                       capture_output=True, text=True, timeout=30)
    a = json.loads(p.stdout); u = a['user']; domain = os.environ.get('WORK_DOMAIN','')
    ok = (p.returncode == 0 and bool(domain) and u.get('type') == 'user'
          and domain.casefold() not in u['name'].casefold()
          and a['tenantId'] == os.environ['AZURE_TENANT_ID']
          and a['id'] == os.environ['AZURE_SUBSCRIPTION_ID']
          and u['name'].casefold() == os.environ['WEBAPP_OWNER_PRINCIPAL'].casefold())
except Exception:
    ok = False
print(bool(ok)); sys.exit(0 if ok else 1)
PY
}
capture() {
  CAPTURED=$("$@" 2>&1)
  local code=$?
  if [ "$code" -ne 0 ]; then printf 'status=refused\n'; return "$code"; fi
}
```

Never echo `CAPTURED`, Azure account metadata, Terraform outputs or raw errors. Review live
plans privately and publish only approved change descriptions, counts and Booleans.
Capture each command's exit code; a successful later command cannot erase an earlier failure.
For `terraform plan -detailed-exitcode`, explicitly distinguish 0, 2 (changes), and 1 (error).
Do not save plan files or provider debug logs in public paths.

Create/use an owner WSL virtual environment outside the checkout, such as
`$HOME/.venvs/releaselens-webapp`, and install the existing locked closure:

```bash
python3.14 -m venv "$HOME/.venvs/releaselens-webapp"
"$HOME/.venvs/releaselens-webapp/bin/pip" install --require-hashes --only-binary=:all: --no-deps -r eval/requirements-gateway-check.txt
```

Run harness commands from `eval/` with that environment's Python. They guard the tree and
ignored output before acquiring credentials, prompt cookies with hidden input, keep separate
in-memory slot cookie jars, and print fixed status labels. Clear private environment values
and close the ephemeral process after the session. On Windows, a short-response timeout needs
WSL reproduction before it is classified as an Azure failure; the live harness requires WSL.

## Current rates and the session limit

Recheck Azure's Retail Prices API for Australia East Linux S1 and API Management Basic v2,
and the model's current token prices, before approving deployment. The design's 12 October
2026 observations were US$0.12/hour S1 and US$0.20548/hour Basic v2: about US$1.30 for four
hours of those two services, plus model, storage and logging charges. These are dated
observations, not a current quote. The indicative S1 AUD observation was A$0.171/hour;
the gateway AUD query was rate-limited. Record currency, observation time and actual runtime.
Budget alerts stop nothing. A four-hour overrun needs a recorded finding and teardown action.

W1 and W3 each authorize exactly one harmless question, with a 256-token completion bound.
The question is: “Explain why a staging slot is useful when releasing a web app.”
Health, access, configuration and absence checks call no model. Run no concurrent model
measurement during W1's controlled telemetry window. No extra paid probe is implied here.

## Bootstrap declaration and consent

**Ask first.** Obtain approval for the bootstrap plan, then separately for its apply and any
required consent. The reviewed additions are one front-end registration, one dedicated
managed identity, its managed-identity assertion federation, owner `WebApp.Use`, deployment
`WebApp.Smoke`, delegated gateway pre-authorization, and the private webapp state container
and owner data role. No model privilege is assigned to the front-end identity.

Use `TF_DATA_DIR=$HOME/tfdata/bootstrap-live`. Preserve existing private variable values
through process variables and follow the [bootstrap procedure](../infra/bootstrap/README.md).
Capture init/plan/apply output. Stop if a plan proposes an unrelated replacement or delete;
bootstrap lock removal, recreation or deletion needs additional approval.

After `acctok` succeeds, capture values into process environment without printing them:

```bash
export TF_DATA_DIR="$HOME/tfdata/bootstrap-live"
acctok || exit 1
capture terraform -chdir=infra/bootstrap output -raw tfstate_storage_account || exit 1
export TF_VAR_tfstate_storage_account="$CAPTURED"
export TF_VAR_subscription_id="$AZURE_SUBSCRIPTION_ID"
capture terraform -chdir=infra/bootstrap output -raw webapp_owner_object_id || exit 1
export WEBAPP_OWNER_OBJECT_ID="$CAPTURED"
capture terraform -chdir=infra/bootstrap output -raw deploy_identity_principal_id || exit 1
export WEBAPP_DEPLOY_OBJECT_ID="$CAPTURED"
capture terraform -chdir=infra/bootstrap output -raw webapp_identity_id || exit 1
export WEBAPP_IDENTITY_ID="$CAPTURED"
capture terraform -chdir=infra/bootstrap output -raw app_insights_id || exit 1
export WEBAPP_APPINSIGHTS_RESOURCE_ID="$CAPTURED"
```

The gateway token still needs the owner's `Gateway.Invoke` assignment and delegated
`access_as_user` scope. Sign in and consent as the owner; do not claim a decoded unsigned
token establishes platform signature validation or Caller attribution.
Capture the monitoring resource's application ID separately. `app_insights_id` is the ARM
resource ID and cannot be substituted for the query API's app ID. The existing
`application-insights` Azure CLI extension is a prerequisite for this
[documented component lookup](https://learn.microsoft.com/cli/azure/monitor/app-insights/component#az-monitor-app-insights-component-show).

```bash
acctok || exit 1
capture az monitor app-insights component show --ids "$WEBAPP_APPINSIGHTS_RESOURCE_ID" \
  --query appId --only-show-errors -o tsv || exit 1
export WEBAPP_APPINSIGHTS_APP_ID="$CAPTURED"
```

## Gateway session stack

**Ask first.** Review/approve its plan and separately approve the gateway apply. Use
`TF_DATA_DIR=$HOME/tfdata/gateway-live` and the existing
[gateway runbook](runbook-gateway.md) and [stack instructions](../infra/gateway/README.md).
Supply publisher email through process environment. Do not repeat its paid burst checks
as an implied part of project 6. Confirm the intended API revision is current.
Capture `api_management_id` from Terraform into `WEBAPP_APIM_ID`, without printing it.

## Webapp plan, role and apply

**Ask first.** Review/approve the webapp plan. Separately approve its scoped Website
Contributor role grant to the existing deployment identity, then the apply that includes it.
Use `TF_DATA_DIR=$HOME/tfdata/webapp` and the [stack](../infra/webapp/README.md).

```bash
export TF_DATA_DIR="$HOME/tfdata/webapp"
acctok || exit 1
capture terraform -chdir=infra/webapp init -input=false \
  -backend-config="storage_account_name=$TF_VAR_tfstate_storage_account" \
  -backend-config="subscription_id=$AZURE_SUBSCRIPTION_ID" || exit 1
capture terraform -chdir=infra/webapp plan -input=false || exit 1
# After the separate apply approval:
acctok || exit 1
capture terraform -chdir=infra/webapp apply -input=false -auto-approve || exit 1
capture terraform -chdir=infra/webapp output -raw webapp_resource_id || exit 1
export WEBAPP_RESOURCE_ID="$CAPTURED"
capture terraform -chdir=infra/webapp output -raw webapp_main_url || exit 1
export WEBAPP_MAIN_URL="$CAPTURED"
capture terraform -chdir=infra/webapp output -raw webapp_staging_url || exit 1
export WEBAPP_STAGING_URL="$CAPTURED"
capture terraform -chdir=infra/webapp output -raw webapp_frontend_audience || exit 1
export WEBAPP_FRONTEND_AUDIENCE="$CAPTURED"
capture terraform -chdir=infra/webapp output -json webapp_intended_configuration || exit 1
export WEBAPP_INTENDED_CONFIG="$CAPTURED"
```

Configure the existing protected GitHub `azure` environment with these private values:
`WEBAPP_RESOURCE_ID`, `WEBAPP_MAIN_URL`, `WEBAPP_STAGING_URL`, `WEBAPP_FRONTEND_AUDIENCE`,
`WEBAPP_DEPLOY_OBJECT_ID`, `WEBAPP_INTENDED_CONFIG`, and the existing Azure tenant/subscription
secrets and deployment-client variable. Obtain approval for changing that environment.
Use private environment settings, with no public issue/comment/screenshot containing values.
Keep required reviewers and the existing shared release concurrency group.
The explicit `-auto-approve` above is used only after the recorded owner approval; it grants
no permission by itself. With saved private plan files prohibited, hold the session against
concurrent changes and re-review/re-approve if infrastructure or inputs drift. Terraform
`-input=false` alone refuses apply, as its [CLI reference](https://developer.hashicorp.com/terraform/cli/commands/apply)
documents.

## Seed and early deployment probes

**Ask first.** Dispatch `webapp-release.yml` manually on current main with `mode=seed`,
`fixture=false`. Approve its protected release job. It builds an immutable
`<source-sha>.<run-id>.<attempt>.seed` package, verifies source CI, publishes main and checks
authenticated health, selected package digest and configuration. Seed cannot be promoted.
It also captures the deployment principal's health-only route evidence without a model call.

Resolve .NET 10 Linux S1 availability, managed-identity client assertion, owner consent,
token-store availability and Entra-authorized SCM ZIP deployment/digest reading now.
If the narrow role cannot deploy/read the selected package, record the failure and stop;
do not enable basic credentials or widen privileges as a fallback.

## Owner sign-in and W1

**Ask first.** Approve W1's single paid question and telemetry read. The owner opens main,
signs in, then supplies their Easy Auth Cookie header to the WSL harness's **hidden input**.
Include all cookie chunks; never put the value in an argument, file, chat or screenshot.
The process obtains a fresh antiforgery token, parses only answer presence, and writes no
model answer. If a screenshot is retained, crop it to the answer/status with no address bar,
account information, identifier or cookie. The one-person limitation remains explicit.

```bash
cd eval
"$HOME/.venvs/releaselens-webapp/bin/python" -m app.webapp owner
```

W1 needs a usable expected token, HTTP 200/nonempty answer and matching owner Caller metric
in the question's bounded time window. Poll for at most 15 minutes with no further Ask.
Absent telemetry by the deadline fails W1.

## Access check W2

**Ask first.** Approve the read-only access probes. Download only the sanitized observation
artifact from the successful seed/stage run to ignored `reports/webapp/`. Keep the original
ZIP, named `webapp-observations-<run-id>-<attempt>.zip`; do not extract/repack it.
The harness retrieves that run's artifact metadata from GitHub and verifies the ZIP's exact
SHA-256 digest before reading its closed provenance envelope. Missing digest or mismatched
run/attempt/source/time refuses evidence. Set `GITHUB_REPOSITORY` and the public numeric
`WEBAPP_DEPLOYMENT_RUN_ID` in process environment. The harness independently fetches source
CI and requires its named `Webapp offline token and route edges` step for the measured SHA.

```bash
"$HOME/.venvs/releaselens-webapp/bin/python" -m app.webapp access
```

Main/staging anonymous and forged-header probes must get 401 or a 302 to that origin's Easy
Auth sign-in path, without redirect-following. Deployment health must be 200 with deployment
caller; owner routes must be 403. Live and offline facts remain separately labelled.
The offline token/antiforgery cases do not claim live injection through Easy Auth.

## Token-expiry interval and W3

**Ask first.** Approve the wait, one platform refresh and W3's single paid question.
Start this early enough to fit the four-hour target. Supply main's cookie by hidden input
to a new ephemeral process; retain that process for the initial expiry interval.

```bash
"$HOME/.venvs/releaselens-webapp/bin/python" -m app.webapp refresh
```

It captures initial expiry, waits in interruptible slices of at most 30 seconds with progress,
refreshes once the initial lifetime elapsed, accepts 200/204 without reading a refresh body,
updates cookie chunks and checks a later usable expiry with the same owner/audience.
Only then does it Ask once. HTTP 204 alone cannot pass. An interrupted wait records partial
failed evidence and sends no extra paid request. An unsupported or non-advancing refresh fails.

## Normal staging and W4 promotion

**Ask first.** Approve a normal stage deployment separately from seed. Dispatch current main
with `mode=stage`, `fixture=false`, and `baseline_version` equal to the actual healthy main
version. After environment approval the job verifies main, staging, package hash and intended
configuration. Only a successful candidate run creates an eligible receipt.

**Ask first.** Separately approve promotion. Dispatch `mode=promote`, `fixture=false`,
`source_run` and `source_attempt` for that successful candidate. Preflight has no Azure
authority; it validates run/repository/main/source-CI binding and artifact bytes. The protected
job re-fetches metadata and rechecks actual staging/main/configuration after approval under
shared concurrency. It performs one regular swap, then checks both versions and slot labels.
There is no preview, automatic swap, retrying swap or automatic rollback.

Download the original candidate artifact ZIP as `webapp-stage-<run-id>-<attempt>.zip` and
the original promotion observations ZIP as `webapp-observations-<run-id>-<attempt>.zip`
to ignored `reports/webapp/`. The harness verifies both against GitHub's exact run-bound
artifact digests before consuming the receipt, W4 and W6a envelopes. It never extracts or
executes package code. Public artifact metadata may be read anonymously; if GitHub requires
authentication or rate limits the lookup, provide a least-privilege read-only `GH_TOKEN` in
process environment, never an argument/file. Unsupported or unverified retrieval fails closed.
Set public numeric `WEBAPP_STAGE_RUN_ID` and `WEBAPP_PROMOTION_RUN_ID` in process environment.
Run `python -m app.webapp release` through the WSL virtual environment. It re-fetches actual
run/job metadata; no owner-entered pass flag is accepted.

## Fresh per-slot sign-in and W6a

**Ask first.** Approve configuration reads and fresh owner sign-in on each slot after W4.
Sign out and sign back into main and staging separately; do not reuse main's cookie on staging.
Run `python -m app.webapp settings`. It first verifies the same paired stage/promotion
archives and a successful W4 for those exact runs. Confirm the fresh sign-in procedure separately
for each slot, then supply each cookie through its hidden prompt. Old cookies cannot serve
as a fresh-sign-in attestation. No question is sent.

W6a compares declared intended settings with before/after workflow facts and current ARM
configuration, plus both fresh sessions. Sticky values, managed identity, auth policy,
publishing restrictions and slot labels must remain correct. Both slots share one registration
and identity; this does not demonstrate isolation between different registrations.

## Unhealthy fixture and rejected promotion W5

**Ask first.** Approve deploying the reviewed unhealthy fixture to staging. Dispatch
`mode=stage`, `fixture=true`, with the healthy main baseline version. It must get staging
health 503 with its immutable fixture version marker, verify the selected stored package
digest, fail the release, record zero swaps/model calls, and confirm main unchanged. A
platform/startup 503 without the reviewed fixture's marker cannot pass W5. Refusal bodies
stay unread; bounded readiness polling waits for the actual fixture.

**Ask first.** Separately approve the rejected-promotion demonstration referencing that
failed run/attempt. Promotion preflight must fail and the protected release job must remain
skipped. Preflight writes a fixed `fixture_ineligible` rejection record only when it positively
identifies that failed fixture run/attempt, after current source CI passes. Input, CI and
network failures cannot satisfy this condition. Download the original fixture observations
ZIP and the rejection artifact ZIP as `webapp-observations-<run-id>-<attempt>.zip` and
`webapp-rejection-<run-id>-<attempt>.zip`. Set the public run-ID variables to the failed stage
and rejected promotion, and run
`python -m app.webapp release`. W5 passes the gate check only with these actual failed-run
outcomes and main comparison; a failed fixture alone is insufficient.

## Capture teardown references before destruction

**Ask first.** Approve read-only resource and bootstrap inventory lookups while everything
exists. Capture main's ARM `properties.serverFarmId`, both session group IDs, the main ID,
staging ID and API Management deleted-service ID into `WEBAPP_TEARDOWN_REFS`, a private
process JSON object with keys `webapp_group`, `gateway_group`, `plan`, `site`, `slot`,
`deleted_apim`, `bootstrap` (list of retained bootstrap group IDs). Derive references from
the captured Terraform outputs and actual ARM reads, never guesses about names.

The deleted-service path is `/subscriptions/<subscription>/providers/Microsoft.ApiManagement/
locations/australiaeast/deletedservices/<service>` and uses API `2024-05-01`, per
[Microsoft's soft-delete procedure](https://learn.microsoft.com/azure/api-management/soft-delete).
Resource-group reads use `2021-04-01`; Web site/plan/slot reads use `2024-11-01`.
Only a canonical expected reference's confirmed 404 can prove absence; 403, timeout and
ambiguous lookup cannot. All HTTP refusal bodies remain unread.

For every retained bootstrap group, capture the complete ARM resources-list `value[].id`
in process memory before teardown. Set `WEBAPP_BOOTSTRAP_BASELINE` to a private JSON map
of group ID to that nonempty ID list. Do not write either private JSON object to a file.
W6b requires the group to exist and its complete resource-ID inventory to equal this baseline.
Paged/incomplete inventory refuses evidence. Session destruction never targets bootstrap or
its identities, registrations, model deployments, monitoring, state or ingestion resources.

## Each destroy and purge requires approval

**Ask first.** Separately approve webapp destruction, including site, staging slot, S1 plan,
session group and scoped deployment role. Run `acctok`, select `TF_DATA_DIR=$HOME/tfdata/webapp`,
privately review `terraform -chdir=infra/webapp plan -destroy`, then capture
`terraform -chdir=infra/webapp destroy -input=false -auto-approve` only after that separately
recorded destroy approval. Re-review any drift before executing. Do not destroy bootstrap.

**Ask first.** Separately approve gateway destruction. Run `acctok`, select
`TF_DATA_DIR=$HOME/tfdata/gateway-live`, review/capture its destroy through the gateway runbook.
Its provider may purge soft-delete as part of destruction; that irreversible purge requires
explicit approval too, before executing the destroy. Do not infer permission from session setup.

**Ask first.** If a deleted API Management entry remains, obtain specific irreversible purge
approval. Only then run the existing captured `az apim deletedservice purge` procedure after
`acctok`. No purge or permanent deletion is authorized by this offline implementation.

## W6b absence measurement and the report

**Ask first.** Approve the read-only absence checks after the approved destroys/purge.
Run `python -m app.webapp teardown` in the WSL virtual environment. It issues reads only;
it never destroys or purges. Preserve process references/baseline until this completes.
Then run `python -m app.webapp report`, which is fully offline and rejects cross-commit results.
W6 passes only when both W6a and W6b pass. Missing files are not run, failures remain failures.

| Teardown fact | Observation |
|---|---|
| Webapp session group absent | not run |
| Gateway session group absent | not run |
| S1 plan, main site and staging absent | not run |
| API Management deleted-service entry absent | not run |
| Retained bootstrap inventory unchanged | not run |

## Findings to record after measurement

| Live uncertainty | Finding |
|---|---|
| .NET 10 Linux S1 availability and slot warmup | not run |
| Keyless Easy Auth assertion and owner consent | not run |
| Token store and expiry/refresh behavior | not run |
| Entra SCM deployment/digest reads at scoped role | not run |
| Gateway owner Caller telemetry | not run |
| Swap versions, sticky settings and fresh per-slot sessions | not run |
| Teardown, purge and retained bootstrap inventory | not run |
| Actual charges, currency, runtime and any overrun | not run |

After all measurements, publish only the guarded report and sanitized findings after owner
review. Obtain separate approval for a documentation commit/push/PR. Preserve scratch and
handover files. Do not mark a live pass from offline tests or change the design to relabel failure.
