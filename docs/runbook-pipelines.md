# Project 8: checked Search delivery

Local checkpoint, 16 October 2026: Tasks 1-6 are implemented. Offline verification
passed 485 infrastructure tests, 1,185 eval tests, three Terraform validations and
four mock plans. The real Windows-to-WSL ownership/path/exit probe also passed without
Azure access. Independent whole-branch review completed; nine Important findings
were fixed in one regression-tested pass, with no deferred minors. Actual image/PR/main
CI, account setup, paid preparation and P8-1 through P8-8 live acceptance have not run.

This CV demonstration delivers one frozen Search schema and S2 configuration to
`p8-test` and `p8-demo-prod` on one new Basic service. It uses 41,825 existing
chunks and 229 audited questions. The gate is complete 229-question top-1 scoring
with at least 120 correct and no error, skipped or missing measurement. The prior
0.637 figure is MRR, not top-1 accuracy. The design and implementation plan are in
`docs/superpowers/specs/2026-10-15-azure-pipelines-search-delivery-design.md` and
`docs/superpowers/plans/2026-10-16-azure-pipelines-search-delivery.md`.

## Approval boundaries

Local implementation does not authorize public push, PR/merge, image publication,
Azure DevOps configuration, identity creation, paid input preparation or resources.
Obtain separate concrete approval for each. Final dated evidence publication also
needs approval. A failure never authorizes a replay or corrective apply.

Before live preparation require green original PR/main CI and the image workflow's
real pinned-parent container smoke, a source-labelled immutable image digest and
anonymous GHCR pull proof. Windows provider mTLS validation failed locally;
offline Terraform validation uses WSL without weakening TLS. Local isolated Linux
toolchain checks do not replace actual image acceptance.

```text
python -m pytest tests/infra -q
python scripts/check_workflows.py .github/workflows
cd eval
python -m pytest tests/delivery_search tests/retrieval -q
# For each of infra/pipelines/identity, foundation and index:
terraform init -backend=false -input=false -lockfile=readonly
terraform fmt -check
terraform validate
terraform test
```

## Review the real Azure DevOps configuration before billing

Before starting the billing clock, include `owner_ado_id` from the sole actual
approver in the pinned demo-prod approval check. Use
`scripts.pipelines.readiness.require_owner_binding` with the actual checks
snapshot. The owner ID and timing profile are part of the human-approved scope.
Missing or malformed IDs refuse before login; a mismatched owner, changed checks
snapshot or insufficient complete execution budget refuses an owner create
reservation. A successful packed-variable Save alone does not prove these fields
are complete.

The `standard` timing profile remains 75/78/80/120 minutes from the original
clock for queue cutoff, cleanup due, latest cleanup start and physical closeout.
It cannot fit the conservative complete provisioning path. The separately
approved `provisioning` profile uses 210/213/215/270 minutes. It is an option for a
new reviewed attempt, never an extension of an existing attempt. Reprice it and
obtain the concrete identity/resource/timing approvals before use.

The complete sequential budget is 196 minutes: five for the final platform Save,
30 for the foundation parent, 20 for its postcheck, 55 for the agent parent,
15 for the separate placeholder setup, 12 for the two upload parents (eight and
four), 49 for all live jobs and ten for owner/cron/artifact handoffs. Run the
placeholder and uploads after the agent apply; no concurrency is assumed. Keep
another ten minutes before
queue cutoff. `readiness.require_execution_window` checks the whole or remaining
phase budget at `clock`, `foundation`, `foundation-apply`, `agents` and
`agents-apply`. Use these checks in both the reviewed parent launchers and the
owner create reservations. Do not reduce provider timeouts to pass a preflight.
Before billing, the future provisioning packet must freeze a start/Schedule pair
that fits the complete budget. To permit faster completion, its concrete approval
may also permit one earlier replacement of that unfired temporary Schedule after
the release and negative checks pass. Save and read back that exact earlier cron
while Disabled. Never move it later, reset the clock, or queue a second Schedule
build. Without that explicit permission, retain the approved pairing. An early
all-pass cleanup need not wait for the maximum window.

Use the personal organisation and private project. Create a new disabled pipeline
using `pipelines/search-delivery.yml` on protected GitHub main. Never modify or
enable Project 7 pipeline 1. The source uses a literal required `extends` template,
no arbitrary commands or queue-provided endpoint overrides. Only the new pipeline
may use the reused pool and the following new resources:

| Resource | Identity and permitted purpose |
| --- | --- |
| ReleaseLens-P8-Test connection | Fresh Test UAMI, Search writer and test-state only |
| ReleaseLens-P8-Prod connection | Fresh Prod UAMI, Search writer and prod-state only |
| ReleaseLens-P8-Verify connection | Fresh Verify UAMI, Search reader, no state writer |
| ReleaseLens-P8-Test environment | Test deployment checks |
| ReleaseLens-P8-DemoProd environment | Demo-prod approval and exclusive release lock |

All connections must use workload identity federation with exact actual issuer,
subject, client/object, tenant and subscription bindings. No deploy client secret,
storage key, new Build/Code PAT scope or registration PAT reuse is allowed. Writers
share the Search service permission boundary and technically can affect both
indexes; do not claim index-isolated RBAC. Verify has Search reader plus private
inputs reader and append-reserved evidence writer, with no schema/load capability.

On every connection configure resource-owned protected `refs/heads/main` branch
control, reject unknown protection, and require the exact repository/template.
On demo-prod configure owner approval and exclusive lock. This personal demo uses
explicit self-approval and makes no two-person separation claim. Disable bypasses,
open access and authorization to other pipelines. Preserve branch/wait/admin
protections. Require exactly the approved check IDs, resource bindings, type IDs,
versions and canonical settings hashes through `checks.verify_checks`.

Retain original resource check API responses, pipeline authorizations, pool
permissions, private-project evidence, GitHub main protection, original variable
dictionary and actual check evaluations. A normalized snapshot must be derived
from these originals and reviewed; the checker cannot infer settings from opaque
check IDs or turn a supplied boolean into platform proof. Freeze the snapshot and
approved profile hashes in the private authority, and verify again before queues.
Actual runtime evaluations, including approval, must succeed without bypass.
Use Azure DevOps preview compilation to check both Manual modes and Schedule
against the actual service. Local YAML tests are not a compiled platform result.

Use a private `ReleaseLens-P8` variable group authorized only to the new pipeline.
Set `P8_AUTHORITY_JSON`, canonical `P8_AUTHORITY_SHA256` and `P8_SOURCE_SHA` from the
reviewed owner authority, with no queue override permission. Azure DevOps limits
a variable value to 4,096 characters. Before billing, pack the complete approved
authority into a private file using the reviewed full-authority digest:

```text
python -m scripts.pipelines.authority_transport --input PRIVATE_AUTHORITY.json --output PRIVATE_TRANSPORT.json --sha256 REVIEWED_CANONICAL_SHA256
```

Use the complete contents of `PRIVATE_TRANSPORT.json` as the secret
`P8_AUTHORITY_JSON` value. This versioned gzip/base64 envelope preserves every
authority field; `P8_AUTHORITY_SHA256` still hashes the decoded canonical full
authority, not the envelope. Packing refuses values above the platform limit,
decoded authorities above 32 KiB and existing output files. The runtime accepts
legacy plain JSON as well, bounds decompression and rejects corrupt, concatenated
or unknown transport formats before login. Compression does not encrypt anything.
Keep both files and the whole variable secret. Before any billable operation,
derive the final full authority with its reviewed timing and check bindings,
pack it, save it to the real variable group, retain actual readback, and verify
the effective value and full hash through the runtime guard. Repeat this gate if
the final authority changes. A local pack test is not proof that a platform Save
succeeded. Do not publish either authority file in artifacts or public reports.
Also mark the individual tenant, subscription, client and object
GUID values as separate secret variables: masking the whole JSON does not mask
its substrings in the built-in AzureCLI login command. Never export job tokens or
browser cookies. `visibleAzLogin: false` suppresses login response, not all task
command metadata. Capture originals privately and inspect effective masking.

AzureCLI@2 exposes a fresh initial assertion and keeps CLI federation active. The
Terraform providers/backend use the exact connection's native ADO OIDC request
URL and job token to obtain fresh assertions during the job. Both remain process
environment only. Pin the actual HTTPS organisation origin and project path in
each principal; reject a different connection or request URL. No token is stored
in plan inputs, receipts or artifacts. Inherited Terraform trace logging is removed.

## Freeze inputs and plans

Privately verify the existing saved corpus vectors, links, audited questions,
scorer and locks. If query vectors do not exist, separately approve exactly one
229-question `text-embedding-3-small`, model version `1`, 1,536-dimension batch
and its cost ceiling. `prepare_query_vectors` reserves durably before each call;
a partial/failed batch cannot replay. No chat call, new question, corpus embedding
or runtime embedding is part of delivery.

Assemble the exact 14-file flat bundle described by `delivery_search.bundle` in a
separate input directory. Do not archive authority, preparation journals, secrets
or raw cloud output. Build validates/seals the owner's frozen archive with the
same source/scorer/workload/provider locks. The release SHA-256 is identical in
every job. Upload the archive only to the new approved private inputs container.
Retain complete original input/source provenance privately.

`build` seals the release once and creates the original test plan; `test` consumes
that exact binary plan, loads once and grades. The separate `plan-prod` job uses
only the Prod identity, regrades the original test gate and creates the prod plan.
The approved `prod` deployment regrades that gate before consuming the prod plan.
Test never accesses the prod state backend. Bind plan bytes, input/lock hashes, source/release, target and canonical
state lineage/serial/hash. Authenticated initial Blob absence is distinct from a
failed read. Wrong target, dirty source, stale state or changed artifact refuses.
Private Blob `If-None-Match: *` receipts prevent replay across disposable agents.
Complete binding verification precedes backend initialization. Shown plans,
pre-apply canonical state, apply intents and success/failure results persist in
private Blob evidence and pipeline artifacts before the one-shot agent exits.
Limit artifact retention to 7 days and verify owner-only effective access. The
evidence container's contributor roles must not imply public access.

Before billing, exercise the actual original browser ZIP download, save and local
reader path, including private source-labelled plans and JUnit. Keep registration
PAT scopes narrow; do not add Code/Build scopes to avoid the human handoff.

## Release, blocked quality and schedule

Only exact source on protected main with the current authority can log in or run.
Each job is clean, credentials are not persisted and it demands the new image's
`P8Agent=ReleaseLens` capability. Per-stage budgets stop further requests/processes
before the job timeout and preserve failures; a successful fast run is still needed
to prove the live window fits. Budget exhaustion does not authorize a retry.

Queue one Manual `release`, with the real demo-prod approval after the original
test pass. Capture all original run/timeline/check/ZIP/JUnit records. Then queue
one Manual `negative`: it uses Test only, no Terraform or document upload, applies
the frozen no-match filter to real test requests, and must fail the actual quality
gate with 229 valid zero-match results. Preserve prod schema/state/document digests
before and after. Never approve a promotion under that failed gate.

The committed default `0 9 * * *` UTC cron is 19:00 Brisbane, main only,
`always: true`. A dated live packet must bind its actual start minute and one
genuine Schedule minute before billing, using the approved timing profile and
complete remaining budget. Keep the pipeline Disabled while saving and reading
back any approved temporary main-only override; retain the original configuration
for restoration. Do not leave an additional default cron active.
An actual `Build.Reason=Schedule` compiles only the Verify connection path,
regardless of the manual mode parameter. Verify reuses the original release and
successful prod gate, checks the original negative receipt, performs one 229-query
read-only prod measurement and retains its originals. A manual run cannot substitute
for a missed schedule. Disable the schedule/pipeline after this bounded demo.

## Independent window and closeout

Use a fresh private `.local/project8/<session>/` directory and actual source/tree,
human receipts, plans/manifests, absence baseline and full retained ARM/deleted-vault
inventories. Never borrow earlier session ownership. The owner controller and
finite Windows watchdog run outside ACA through the existing WSL owner Python.
Test the actual Windows-to-WSL path, process identity and exit before billing.

The concrete dated proposal binds either the standard +75/+78/+80/+120 profile
or the separately approved provisioning +210/+213/+215/+270 profile, with offsets
for queue cutoff, cleanup trigger, actual cleanup start and verified closeout.
The actual start/Schedule pair must fit the complete execution budget and shutdown
reserve; 18:00/19:00 is not a mandatory pairing. Apply start is the first billable
operation, not apply completion. Late preparation stops downstream work, preserving
the deletion reserve. Failures retain resources until the scheduled cleanup or a
genuine explicit owner stop; successful original acceptance may clean up early.

Cleanup disables only the new pipeline, cancels only original session builds,
captures disabled/empty queue and restores the exact original variable/settings
dictionary. The independent owner then destroys index states, foundation, identities
and finally the fresh agent host. Use owner AAD/CLI backend authentication with
`use_oidc=false`; never make the agent destroy itself. Require original manifest IDs,
exclusive operation lock and no active owned Terraform/provider/native operation.
A timeout preserves the original failure/full manifest and permits only a fresh
delete-only remaining-identity plan. Never replay a saved destroy or purge a vault.

Closeout requires canonical absence, successful empty state lists, complete unchanged
retained inventories/deleted-vault metadata, restored settings, disabled/empty queue,
owner confirmation of this fresh PAT's revocation after cleanup starts, and exact
watchdog/keeper/inbox exit. Late timing remains a failure after safe physical cleanup.
Keep raw originals private; public results use an explicit allowlist and report
failed/not-run outcomes and true provenance. Cost can remain unavailable; an estimate
must not be relabelled as measured cost. Public report/README publication is separate.

## Private controller protocol

Keep the live checkout frozen at the approved source/tree. `authority.json` carries
the `SessionAuthority` fields plus exact owner/principal/scope bindings, new pipeline
ID, storage/Search hosts, WSL session path and SHA-256 bindings for the original human
receipt and baseline. The actual human receipt contains its real chat message reference,
source/tree/release and `session.scope_digest(authority)`. This digest binds the owner,
groups, CI principals, grants, pipeline, hosts and immutable manifest/baseline hashes. Never manufacture a human receipt
from a failure, heartbeat or synthetic test. The baseline contains all three authenticated
group-absence originals and full retained resources, role assignments and deleted vaults.

Before billing prepare the five components in `cleanup-manifest.json`: `index-test`,
`index-demo-prod`, `foundation`, `identity`, `agents`. Freeze each component's private
relative directory, `owner.config_digest`, original resource address/ID pairs and backend
binding. ARM records also include kind and API version; index records include exact host
and logical name; only the agents component permits its one local terraform_data gate.
Bounds are one index each, foundation 23 ARM, identity 7 ARM, agents 9 ARM plus the local
gate. Fresh canonical group absence and original create-only plans establish ownership.
The manifest bytes never change after approval. IDs unknown before apply may fill only
predeclared typed `resolve` slots: exact role scope/role/principal (or fixed UAMI reference),
exact index host/name, or the single local gate's frozen input. Components with slots
carry an original hash-bound `create_plan`: `owner` names the binary/shown files and
their SHA-256 values; `pipeline-index` names only its exact logical target.
The separately approved owner launcher calls `ownership.reserve_owner_apply` immediately
before its one original owner apply. Index jobs publish their original apply intent;
retain it as `apply-intents/index-<target>.json` before cleanup ownership is reconciled.
`ownership.bind_state` validates actual state against the original create plan/intent,
fixed addresses and typed predicates. It writes create-only hash-bound state snapshots
and identity bindings, preserving both manifest bytes and every already bound ID.
No recovery operation can add an address or widen a scope. A group name match
alone is insufficient. No bootstrap or prior-session state belongs in this manifest.

The pinned [AzAPI 2.8.0 resource ID mapping](https://github.com/Azure/terraform-provider-azapi/blob/v2.8.0/internal/services/parse/data_plane_resources.json)
defines a Search index ID as `<host>/indexes('<name>')`; an arbitrary opaque ID or
another host cannot fill an index slot.

Prepare and verify the owner's private backend metadata before billing. Remote index
backends must point to the exact new account, `test-state`/`prod-state`, `index.tfstate`,
AAD enabled and OIDC disabled for owner cleanup. Local backends must resolve inside
their component. Preserve the deployment backend metadata separately. Copied reviewed
configuration, inputs and provider lock are hashed; alternate Terraform environment
overrides, workspace, trace and credentials are removed. Never reinitialize a backend
as an improvised cleanup fix.

Run the watchdog probe on the real Windows side with the existing Python and root/eval
on PYTHONPATH. It uses structured argv, the fixed WSL owner Python and no shell quoting:

```text
python -m scripts.pipelines.watchdog --session <absolute-session> --probe
python -m scripts.pipelines.watchdog --session <absolute-session> --run
python -m scripts.pipelines.session --session <session> --reserve-dispatch release
python -m scripts.pipelines.session --session <session> --reserve-dispatch negative
python -m scripts.pipelines.session --session <session> --reserve-dispatch scheduled
```

The first two commands run on Windows; reservations run in the reviewed owner runtime.
Probe files are create-only. Use a fresh probe directory when code changes and preserve
old outcomes. The real probe captures native creation time/command and exit as well as
the WSL path, without Azure access. Before an actual attempt require this exact helper
hash and a fresh continuing heartbeat, not merely an earlier probe. Start the finite
supervisor before apply; it can adopt the first actual apply timestamp once and cannot
reset it. Every subsequent authority must retain the same human receipt/source/release.

Immediately after each original browser dispatch, retain its actual build JSON and run
`quiescence --session <session> --register-build release|negative|scheduled --original <json>`.
This validates the new pipeline, exact main source, dispatch reservation and queue time.
At cutoff the Windows watchdog writes one `quiescence/request.json` with only those
original build IDs and their hashes. It never acquires a Build PAT or exports cookies.

Before billing, prepare the owner-browser monitor to inspect this request at least once
per minute: disable only its exact new pipeline, cancel only its listed session-owned
unfinished builds, restore the exact original variables/settings, and retain actual
definition/empty-queue and `quiesced-build-<id>` originals through the private inbox.
Register those bytes in `originals.json`, then run `quiescence --session <session>
--acknowledge verified --request-sha256 <actual-request-sha256>`. The acknowledgement
rechecks those originals, every build and the request digest. For early successful or
explicit-stop cleanup, use `--request-closeout` and complete the same browser handoff
before requesting native cleanup; this command independently checks early authority.

If the browser is unavailable or an action fails, record `--acknowledge unavailable`
or `failed` against that same request. Missing/unverified acknowledgements are recorded
at cleanup, never an unbounded wait past the approved profile's cleanup due/start bounds.
Physical cleanup still proceeds within
its scope; P8-8 cannot pass without actual pre-delete disabled/empty/restored evidence.
The concrete monitor must preserve these instructions and adopt the existing watchdog
and native helper rather than launching duplicates. No monitor is created by this runbook.

Reservations precede browser queueing and corpus/query context. Release reserves test
and prod sets together; negative and scheduled require original positive gates. Worker
Blob reservations enforce the same no-replay rule on new agents. Metadata requests use
120 conditional-create slots shared across the session, and each upload has at most
200 requests. A reserved slot is consumed even if its request subsequently fails.

Use the original watchdog's `--request-cleanup` path for an authorized early closeout.
At due time it adopts that exact native helper. Inspect `cleanup --status` and original
process-group records after a timeout; never blindly repeat the old saved destroy.
After all exact-owned Terraform/providers have exited, an authorized continuation of
`cleanup --session <session>` creates a fresh plan for remaining original IDs. Provider
refresh observations may remove already absent original IDs from state; physical actions
remain delete-only. Empty-state checkpoints survive deletion of the storage backend.
If an index was never created, a successful CLI exit with no state is insufficient.
Require both authenticated canonical backend absence/empty placeholder and an actual
index 404, or original exact parent ARM 404 observations. A failed state command never
becomes absence. The typed observation is retained as `empty-state-absence-<name>`;
ordinary existing states still require successful empty `terraform state list` originals.
For foundation or agents that were never applied, the corresponding
`empty-state-absence-foundation` or `empty-state-absence-agents` may instead retain
the exact authenticated component-group 404, an empty Terraform JSON view and absence
of the local state, backup, lock and owner apply reservation. It must bind the
source/session/manifest and exact local backend paths. This exception cannot be
used for an identity/index component, a denied/existing group, an applied component
or a remote backend. Cached absence is separate per component and rechecks local
state/backup/lock/reservation absence before reuse. It never
writes an empty state or converts a failed state-list command into success.
Preserve the original error, intent, plan, state and every attempt. No automatic replay
or extra deployment is available. Continue safe physical cleanup after a missed bound.

After native cleanup and the finite supervisor have exited, use Windows
`watchdog --session <absolute-session> --verify-exit`. It compares real PID, creation and
command observations and checks WSL operations without Azure login. PID reuse cannot
authorize an unrelated stop. `--verify-exit` only reads processes; if an exact helper is
still active, wait/adopt it or use a separately verified exact-owned stop after physical
cleanup. Never terminate an unrelated process. The watcher lease ends at closeout+30
minutes. Capture and stop only this attempt's inbox/observer after the final originals.

## Original evidence registry and grading

`originals.json` is private, `kind: actual`, source/session bound. Each named file entry
has a session-relative path, byte SHA-256, capture time, source/session, `kind: actual`
and capture channel (`owner-browser`, `owner-arm`, `runtime`, `github`). Retain raw
browser/API/ZIP/TRX/JUnit files first, then register them. Do not label test fixtures as
actual. The registry is an integrity/provenance index, not proof of an API action by
itself; review the captures and their actual request/run association before live grading.

The grader consumes these original keys, with no fallback to older sessions:

- `bundle`, `image-run`, `image-digest`, `image-smoke`, `image-anonymous-pull`.
- `release-build`, `negative-build`, `scheduled-build`; actual Azure DevOps build JSON.
- `identity-test`, `identity-plan-prod`, `identity-prod`, `identity-verify`, `rbac`; token-free claim/account
  projections and original scoped ARM role-assignment results.
- For targets `test` and `demo-prod`, `plan-<target>-binding`, `-binary`, `-inputs`,
  `-locks`, `-state-before` (JSON null for proved initial absence), `-shown`, `-apply-result`.
- For stages `test`, `prod`, `negative`, `verify`, `gate-<stage>-json`, `-xml`, `-receipt`.
- `checks-snapshot`, `checks-evaluations`, `prod-approval`, `prod-approval-map`, `offline-boundaries`;
  real resource checks/suites/approval and retained source-labelled offline refusal tests.
  Suites must carry the actual release build's orchestration PlanId. Preserve the real
  browser association of that build ID, approval ID and approval check-run ID in the map;
  a successful approval from an earlier run does not satisfy this attempt.
- `prod-before-negative`, `prod-after-negative`; identical full schema/state/document
  digests, not unchanged document count alone.
- `definition-original`, `definition-disabled`, `queue-after`; original variables and
  relevant settings restored, exact new pipeline disabled, count zero and empty queue.
- `cleanup-first-delete`, `group-absence-0` through `-2`, and for each manifest component
  `empty-state-command-<name>` / `empty-state-result-<name>`. Retain the original owner
  command/result records: matching PID, exact `terraform state list`, expected directory,
  successful exit and truly empty output. Group observations require all three exact 404s.
  Never-created index states and never-applied local agents may instead use their
  respective authenticated typed `empty-state-absence-<name>` originals described
  above. Their different validators must not be interchanged.
- `pat-revocation`, `helper-exit`; real owner message after cleanup starts and actual
  native/WSL exit observations, including the supervisor and cleanup helper.

Register original bytes, never a public sanitized dump. Raw originals remain private.
The container smoke emits one machine-readable `P8_TOOLCHAIN_RECEIPT` only after
the actual container checks finish; the published image binding records the same
config digest and source plus its registry digest. The grader compares these originals.
Version labels or an unrelated successful smoke log cannot stand in for that image.
The grader re-extracts and validates the bundle, regrades every gate with the frozen
scorer, verifies plans/roles/checks/provenance and checks actual first-delete/verified
timestamps. Cleanup self-reported success alone is insufficient. Missing originals are
NOT RUN or FAIL; a physically clean but late session cannot pass P8-8. The public
projection validates both field names and values and leaves measured cost unavailable.

Official API references used by the controller: [role assignment inventory](https://learn.microsoft.com/en-us/cli/azure/role/assignment?view=azure-cli-latest),
[deleted-vault inventory](https://learn.microsoft.com/en-us/cli/azure/keyvault?view=azure-cli-latest),
[original check evaluations](https://learn.microsoft.com/en-us/rest/api/azure/devops/approvalsandchecks/check-evaluations/get?view=azure-devops-rest-7.1),
[original approval](https://learn.microsoft.com/en-us/rest/api/azure/devops/approvalsandchecks/approvals/get?view=azure-devops-rest-7.1).
