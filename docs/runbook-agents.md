# Project 7: ephemeral build-agent demonstration

This is a focused CV demonstration, not a production agent platform. Source remains on
GitHub. Azure DevOps holds the private project, pool, pipeline and results. Only Core/Web
tests run here; existing Docker-dependent CI and Project 8 deployment remain separate.
The [design](superpowers/specs/2026-10-15-ephemeral-build-agents-design.md) defines A1-A6.
No live measurements are claimed by this runbook or by the mocked Terraform tests.

## Gates and offline checks

Implement/review locally first. The owner separately approves public push, PR/merge and
agent-image publication. Require green original PR/main CI and the image workflow's actual
container tests. Confirm the GHCR package is public and the recorded source-labelled digest
is anonymously readable before Azure apply. No local Docker installation is needed.

```text
python -m pytest tests/infra -q
python scripts/check_workflows.py .github/workflows
terraform -chdir=infra/agents fmt -check
terraform -chdir=infra/agents init -backend=false -input=false
terraform -chdir=infra/agents validate
terraform -chdir=infra/agents test
```

Use the existing development Python environment with `tests/infra/requirements.txt`.
The image workflow builds Linux AMD64, checks the agent executable, runs fake lifecycle
tests as non-root and actually builds/tests both selected projects inside the image.
It performs no Azure login. Publication is manual main-only after validation.

On this Windows machine, a local provider RPC certificate handshake can fail before
schema validation. Use the existing WSL Terraform runtime with a separate private
`TF_DATA_DIR` for Linux validation. Do not disable TLS verification. A tooling failure is
not an Azure failure or evidence of a working agent.

## Create the personal organisation before the billable window

1. The owner signs in with their **personal Microsoft account**, manually creates an
   Azure DevOps organisation and associates their personal active subscription as required
   by the current portal. Choose organisation name/geography privately. No employer tenant,
   app registration, extra users, paid parallel capacity or automated organisation creation.
2. Create one **private project**, a dedicated self-hosted agent pool and a simple Boards
   work item for this demonstration. Verify the organisation has one usable self-hosted
   parallel job. Missing allowance/permissions is a setup blocker, not permission to buy it.
3. Connect only the existing ReleaseLens GitHub repository. The owner performs any GitHub
   consent. Create the pipeline from `pipelines/agents-demo.yml` on main, but do not run it
   yet. Do not initialize/copy a second source repository.
4. Grant that pipeline access to the dedicated pool only. Leave "all pipelines" access
   off. Configure owner-only queue access. Disable UI CI/PR trigger overrides, fork builds
   and schedules. Set the default branch to main. Leave the pipeline disabled until the
   live gate; only the owner may enable/queue it.
5. Set private pipeline variables `P7_POOL`, `P7_SESSION` and `P7_SOURCE_SHA` to the exact
   dedicated pool, fresh session and reviewed merged main commit. The YAML checks the ref
   and SHA before checkout/workload and demands `P7Agent -equals ReleaseLens`.

See Microsoft's [organisation setup](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/create-organization)
and [parallel-job rules](https://learn.microsoft.com/en-us/azure/devops/pipelines/licensing/concurrent-jobs).
The build uses self-hosted capacity, not Microsoft-hosted Azure Pipelines minutes.

## Private inputs and personal-account guard

Create a fresh ignored `.local/project7/<session>/` directory with owner-only permissions.
Use a new 4-16 character lowercase session name; do not reuse earlier live ownership or
Project 6 cookies/account files. Keep state, tfvars, original JSON/TRX/logs and receipts here.
Never print their contents in chat or publish them. The directory needs a private backup
until closeout because local state is required to destroy exactly this host.

The owner privately creates one short-lived PAT for this organisation with only
**Agent Pools read/manage** scope. PAT scope is organisation-wide, not inherently pool-bound;
limit pool permissions as far as the service supports and record the actual boundary.
Write it without newline/BOM/whitespace to owner-only `pat.txt`. On WSL use `umask 077` and
`chmod 600`; on Windows restrict its ACL to the owner. Do not put its value in shell history,
CLI arguments, Terraform variables/data sources/state, images, artifacts or chat.

Private `inputs.tfvars.json` contains only these metadata/path inputs:

```text
subscription_id, tenant_id, owner_object_id, owner_account,
forbidden_account_domains,
location, session_name, organization_url, pool_name, image_digest,
pat_file, session_dir, source_sha
```

`owner_account` is the exact personal CLI account name, not the employer account.
`pat_file` and `session_dir` are absolute WSL paths. `image_digest` must be the published
`ghcr.io/<owner>/releaselens-agent@sha256:<digest>`, never a mutable tag.

`organization_url` accepts the organisation root with or without one trailing slash.
The module removes that slash before setting KEDA's `organizationURL` and both jobs'
`AZP_URL`. Check these planned values are identical and end in the organisation name;
KEDA cannot extract an organisation name from a URL ending in `/`. Offline Terraform
tests cover both accepted forms. They do not replace live queue-to-worker acceptance.

Before every Azure operation capture `az account show`, verify exact personal user,
subscription and tenant, and stop on mismatch. Do not print the account object. For
subsequent commands, this WSL helper assumes the private metadata variables are already
set and the repository is the current directory:

```bash
p7_az() {
  local p7_account_json
  p7_account_json=$(az account show --output json 2>>"$p7dir/azure-errors.log") || return 1
  printf '%s' "$p7_account_json" | python3 -c '
import json, os, sys
try:
    a = json.load(sys.stdin); u = a.get("user", {})
    ok = (a.get("id") == os.environ["P7_SUBSCRIPTION"]
          and a.get("tenantId") == os.environ["P7_TENANT"]
          and a.get("state") == "Enabled" and u.get("type") == "user"
          and u.get("name", "").casefold() == os.environ["P7_OWNER"].casefold()
          and bool(json.loads(os.environ["P7_FORBIDDEN_DOMAINS"]))
          and not any(u.get("name", "").casefold().endswith("@" + d.casefold())
                      for d in json.loads(os.environ["P7_FORBIDDEN_DOMAINS"])))
except (ValueError, KeyError):
    ok = False
sys.exit(0 if ok else 1)
' || return 1
  az "$@" 2>>"$p7dir/azure-errors.log"
}
```

Redirect every helper call's stdout to a private file. It must never be used to print
raw cloud output. Provider operations also require this guard immediately before the
Terraform command; provider auto-registration is disabled. Verify the existing budget,
resource-provider registrations and Consumption quota/location before planning; do not
change bootstrap, buy capacity or widen privileges to bypass a blocker.

## Review the one-apply session

Refresh regional Consumption compute, Log Analytics and Key Vault prices. Present the
actual plan and estimated ceiling to the owner. A proposed **60 minutes** includes cleanup;
no queues after **30 minutes**, cleanup starts by **45 minutes**. Record approved monetary
limit, exact Brisbane start/queue/cleanup/end deadlines and source/digest privately.
Budget alerts and min0 do not prove a hard monetary cap or zero total idle cost.

Before apply, establish a finite cleanup monitor and one exclusive operation lock for
this session, including apply/cleanup reservation and process IDs. A monitor resumes an
existing owned cleanup rather than launching another. Capture canonical absence of the
fresh resource group/vault names, complete retained-group resource-ID inventories and
all state/resource ownership before any creation. A collision or incomplete baseline
stops preparation. These are fresh Project 7 identities, not earlier session ownership.

From the trusted repository root in WSL, choose the private absolute directory as
`p7dir` and export its metadata variables without printing them. Initialize a separate
local backend and capture all raw output privately:

```bash
export TF_DATA_DIR="$p7dir/tfdata"
terraform -chdir=infra/agents init -input=false -reconfigure \
  -backend-config="path=$p7dir/agents.tfstate" >"$p7dir/init.log" 2>&1
terraform -chdir=infra/agents plan -input=false \
  -var-file="$p7dir/inputs.tfvars.json" -out="$p7dir/agents.tfplan" \
  >"$p7dir/plan.log" 2>&1
terraform -chdir=infra/agents show -json "$p7dir/agents.tfplan" >"$p7dir/plan.json"
```

Inspect the plan privately. Exactly ten additions are expected: nine new ARM resources
and one Terraform-only readiness gate. Require no updates/deletes/replacements and no
references to existing application/bootstrap resources. Bind approved addresses and
eventual actual ARM IDs into the private session manifest. Verify source/configuration
and personal account again, then obtain the separate live authorization before:

```bash
terraform -chdir=infra/agents apply -input=false "$p7dir/agents.tfplan" \
  >"$p7dir/apply.log" 2>&1
terraform -chdir=infra/agents output -json owned >"$p7dir/owned.json"
```

The single apply creates the vault/owner role, invokes `seed_pat.py` with path/metadata
only, then creates jobs after its private upload receipt and managed-identity role.
The environment must explicitly select `logs_destination = "log-analytics"` for its
dedicated workspace. A mocked provider plan alone does not exercise AzureRM's real
diff validation when that newly created workspace ID resolves during apply.
Role readiness retries are bounded to 120 seconds. A failed apply/gate stops further
deployment/queues. Retain resources/state for diagnosis inside the agreed window; do not
destroy/recreate or run a corrective apply automatically. Keep any partial ownership.

AzureRM models secrets as a set, so it cannot ignore only one secret value. This stack
does not ignore the secret block: the URI/identity remain subject to drift checks. Before
runtime work, check state privately for the known PAT value and print only the Boolean
result. Any unexpected secret persistence stops queuing. After verified upload, the owner
removes the private PAT input file; Key Vault retains the runtime value until teardown.

## Three original runs and recorded observations

Start the owned placeholder setup job once, capturing the returned execution privately.
Require its normal termination and one matching-capability offline agent registration.
It never calls the listener. Its manual start is setup only, never queue-activation proof.
Record job IDs/names privately as `P7_GROUP`, `P7_WORKER`, `P7_SETUP` before commands.

```bash
p7_az containerapp job start --name "$P7_SETUP" --resource-group "$P7_GROUP" \
  --output json >"$p7dir/setup-execution.json"
p7_az containerapp job show --name "$P7_WORKER" --resource-group "$P7_GROUP" \
  --output json >"$p7dir/worker-definition.json"
p7_az containerapp job execution list --name "$P7_WORKER" --resource-group "$P7_GROUP" \
  --output json >"$p7dir/initial-executions.json"
```

Confirm zero active workers/online ephemeral agents and clear queue before run 1.
Enable this pipeline only. The owner queues a manual main build with `controlledFailure`
false. Observe queue -> Event execution -> matching online agent, without explicitly
starting the worker job. Capture execution list/show, actual image/configuration and
worker-count samples; query the owned worker's Azure Activity Log for the whole run to
exclude manual `Microsoft.App/jobs/start/action`. Capture its complete paginated result.
Record missing/late telemetry as a limit; do not turn an assertion into an observation.

After original run 1 succeeds and returns to idle, queue run 2 with the same source/digest.
Require a different execution/agent/workspace marker. After run 2 passes and returns to
idle, queue the one `controlledFailure: true` run. It must fail in the intentional step
after ordinary build/tests succeed; container success alone does not grade pipeline status.
At any unexpected failure, mismatch or duplicate worker, stop new queuing and retain
resources for bounded diagnosis. Never replay to replace a failed run.

For each run, the owner retains original build JSON and timeline JSON from their signed-in
DevOps session, downloads `p7-results` and captures agent status. The build REST paths are
`_apis/build/builds/<id>?api-version=7.1` and `_apis/build/builds/<id>/timeline?api-version=7.1`
under the private project. No Build-read PAT is added. Agent Pools read/manage can read
the dedicated pool snapshot. Store all originals and SHA-256 file hashes privately.
Do not export account pages, cookie headers or raw diagnostics into public documentation.

The allowlisted artifact has project TRX files and `workload.json` with original build ID,
source, agent, session, unique workspace marker and TRX digests. The fresh marker must not
exist before the guarded job. Keep each run in its own artifact directory; don't mix TRX
from different builds. Positive results require executed tests and zero failures/skips.
Both named publication tasks must have succeeded in the original timeline, including for
the deliberate failure. Worker samples need ordered current timestamps, and all three
runs need distinct build/execution/agent/workspace identities.

`scripts/agents/evidence.py` accepts a private observation object assembled from these
originals, not hand-entered pass labels. Its schema is exercised by
`tests/infra/test_agents_evidence.py`:

| Field | Original source |
|---|---|
| `schema_version`, `source`, `image_digest`, `session` | Session binding, schema 1, approved reviewed source/digest |
| `worker_definition` | Original owned Event job configuration |
| `initial_idle` | Time, zero `workers`/`online_agents` lists and zero `queued` count before run 1 |
| `placeholder` | Known name and offline status, separate from compute |
| `runs[0..2].build`, `.timeline` | Original completed build and task timeline JSON |
| `.execution`, `.pool_snapshot`, `.activity` | Original owned execution JSON, matching agent snapshot, complete run Activity Log |
| `.workload`, `.artifact_dir` | Original workload manifest and that run's private extracted artifact path |
| `.worker_samples` | Timestamped active execution-name lists, at least matching owned execution; unexpected duplicates fail |
| `.idle_samples` | At least three timestamps separated by 30 seconds; zero workers, online agents and queued jobs |
| `window` | `apply_started_at`, `queue_cutoff_at`, `cleanup_started_at`, `cleanup_deadline_at`, `closeout_at` |
| `owned_group_id`, `owned_ids` | Fresh baseline, saved plan and actual manifest-bound nine ARM identities |
| `closeout` | The verified facts listed below, never inferred from a destroy exit code |

Times use explicit UTC offsets. Runs are sequential and source-bound. The grader reports
queue-to-agent, original pipeline duration, actual build/test task duration, executed test
counts and return-to-idle time. Count samples do not prove a global concurrency mutex.

## Scoped teardown and A6

On completion or by the agreed cleanup deadline, hold the same exclusive operation lock.
Never run cleanup alongside an owned Terraform process or start duplicate destroy helpers.
Disable only this demo's pipeline in DevOps, cancel only its owned queued work, and verify
queue empty. This quiesces its min0 queue scaler; do not create replacement configuration
via Terraform. If needed, stop only exact manifest-owned worker executions using the
documented job-stop command, and capture termination. Remove only owned stale online
worker registrations. Keep the one known offline placeholder and private result history.

Review a saved destroy plan against the session's actual identities/state, including any
partially created resources. Require only their deletes, then apply that saved destroy
plan with raw output captured privately. There is **no Key Vault purge**: protection and
soft-deleted metadata remain. Do not delete the DevOps organisation/project/pool, change
bootstrap or stop unrelated jobs. Soft-deleted retention is distinct from running compute.

If an uncreated resource's configuration also blocks a partial-session destroy plan,
preserve that failure and target every address actually present in this session's state.
Review the saved destroy plan against those same owned IDs, then require the entire
session state to be empty afterward. Do not use targeting to retry creation or abandon
remaining state. Cleanup may finish before the scheduled queue cutoff; no further run
may be queued once cleanup begins.

Capture these actual closeout facts:

- `group_exists: false` from the canonical group query, not an empty filtered list.
- `state_read_ok: true` and `terraform_addresses: []` from a successful state read.
- `destroyed_ids` exactly matching `owned_ids`; no foreign identities.
- `operations_active: []`, `cleanup_lock_exclusive: true`, `queue_disabled: true`, `queued: 0`.
- Complete `retained_before` and `retained_after` group-to-resource-ID inventories equal,
  with `inventories_complete: true`; an incomplete query is not an empty inventory.
- Owner-confirmed `pat_revoked: true` and `pat_revocation_confirmed_at`, not merely vault deletion.

```bash
p7_az group exists --name "$P7_GROUP" --output json >"$p7dir/group-exists.json"
terraform -chdir=infra/agents state list >"$p7dir/state-addresses.txt" 2>"$p7dir/state-errors.log"
# Repeat for every retained baseline group, saving complete output privately.
p7_az resource list --resource-group "$P7_RETAINED_GROUP" --output json >"$p7dir/retained-after.json"
python3 scripts/agents/evidence.py --observations "$p7dir/observations.json" \
  --trx-dir "$p7dir/artifacts" --output "$p7dir/acceptance-final.json"
```

The grader creates a new output and refuses to overwrite one. Preserve originals and
failed/not-run outcomes even when later checks pass. Delete the finite cleanup monitor
only after actual verified closeout. An incomplete PAT revocation, state read or resource
inventory keeps A6 incomplete and is surfaced to the owner.

## Publication and CV claim

Only after actual A1-A6 evidence, prepare a dated `docs/agents-report.md` and README link.
Include two positive samples and one expected negative; measured counts/timings; original
source/digest; verified cleanup; and any failed/not-run attempts. Use the grader's allowlist
and independent review before seeking publication approval. No account URLs, resource IDs,
personal emails, secrets, raw state, logs or private controllers are published.

A supported claim is measured queue-driven 0-to-1-to-0 agents with IaC, Key Vault delivery
and real builds/tests. Do not claim production readiness, multi-agent scale-out, zero total
idle cost or benchmark-quality performance. Managed DevOps Pools offer a managed VM-agent
alternative with a different tool/runtime footprint; this demo chooses short-lived ACA jobs
for a small Docker-free workload. Keep quantitative comparisons out unless actually measured.

Project 7 is done only after live acceptance, verified cleanup and approved evidence merge
with green main CI. Keep the private DevOps project/pool/history for Project 8.
