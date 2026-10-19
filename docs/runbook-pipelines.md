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

## Authentication diagnostic before paid infrastructure

After build 7's identity refusal on 17 October, its published artifact was empty:
the private traceback had been written outside the published directory. Failed
stages now publish a fixed-code `failure.json`, never raw exception text, claims,
tokens or cloud response bodies. This repairs observability; it does not establish
the original rejected predicate or turn that failed run into passing evidence.

The separate `identity-preflight` mode uses one Microsoft-hosted Ubuntu job with
Python 3.12 and the same pinned CLI/workload dependencies. It runs the delivery
runtime's authentication boundary for each of the three exact connections, then
actually requests and verifies a fresh job assertion. It performs no Terraform,
storage, Search, model or embedding operation. Its three source/run-bound receipts
are diagnostic evidence, not P8 release, negative or Schedule acceptance.

Require a fresh, human-approved diagnostic packet with a maximum 30-minute validity,
exact main source/tree, pipeline 2, first job/stage attempt and Manual reason. Check
hosted quota before queueing. The ordinary pipeline stays Disabled until the exact
diagnostic queue window is approved, then returns to its original Disabled settings.

### Stage configuration before starting the submission window

The p8-1019c attempt stopped before any diagnostic queue because its private helper
started the five-minute submission window before independent review and browser
configuration. Its resources were removed. Preserve that attempt's sealed files;
do not resume it or reuse its authority, clocks, identities or reservations.

Use the versioned `scripts.pipelines.diagnostic_preparation` owner handoff for a
separately approved fresh attempt. This CLI only validates and writes local
evidence. It does not authenticate, change Azure DevOps, enable a definition,
queue a job, provision resources or authorize cleanup. Keep the approved setup
keeper and existing `setup_live()` guard alongside it: the actual keeper process,
ownership and heartbeat bound to the original setup clock must still be fresh
immediately before each browser mutation. A preparation PASS does not replace
that live guard or the actual job's identity checks.

First review a private `preparation-spec.json` in the fresh attempt directory:

- `kind` is `identity-diagnostic-preparation`. `authority_template` has the hosted
  diagnostic's kind, session, source/tree, pipeline, approval reference and three
  actual principals, without `issued_at` or `expires_at`.
- `setup_clock` is a `{path, sha256}` binding to the original clock. Its approval
  hash must match the `approval` binding and the original 60/120/330-minute limits.
- `bindings` has six `{path, sha256}` originals: `approval`, `identity`, `readers`,
  `rollback`, `rollback_review` and `baseline`. The approval includes the actual
  `message_ref`, session and source/tree. The baseline contains the complete
  operational `readbacks` captured before this attempt's temporary edits.
- `expected_static` contains all 14 original readback keys: definition, variable
  groups, group permission/checks, unfinished builds and each of the three
  endpoints with its permission/checks. Review the exact post-edit GET shape.
  The CLI permits only the three client-ID replacements and suppressing schedules;
  protected settings and unrelated fields must equal the bound baseline. Variable
  groups retain the already approved public source pin. No other variable change
  belongs in the static stage.

An initial classic-editor Save can materialize checkout defaults that were absent
from the original definition. These are operational changes. If needed, include
them explicitly in the fresh human approval and independent static-spec review,
before resource setup. Bind a `static_amendment` file by `{path, sha256}` in the
specification. Its kind is `human-approved-diagnostic-static-amendment`, its
`message_ref` is the exact fresh approval, `baseline_sha256` binds the original
baseline, and `expected_static_sha256` binds canonical `expected_static`. Its
`additions` maps exact dotted definition paths to exact approved values. The
code permits only the absent-to-fixed checkout defaults in `CHECKOUT_DEFAULTS`
and the three exact disabled options in `DISABLED_OPTIONS`. It refuses changed
existing values, other paths, other option IDs or enabled options. This code
does not itself approve an amendment. Preserve the original baseline for restore.

Complete the slow service-connection and schedule edits while Disabled. Capture
the actual saved readbacks before issuing authority. Schedules must exclude all
branches; if present their branch filters are exactly
`+refs/heads/<session>-no-schedule` and `-refs/heads/*`, with
`scheduleOnlyWithChanges: true`. Azure DevOps may return `daysToBuild: "all"` and a
server-generated `scheduleJobId`; review and preserve those actual values instead
of comparing the GET with a guessed form payload. The only ignored fields are
the documented top-level audit fields listed in `_normalize` and omitted empty
definition collections or false attributes on the three diagnostic variables.
Definition `createdDate`, endpoint `modificationDate`, and repository
`properties.lastUpdated` are revision audit metadata. Comments, repository
identity, checkout settings and options remain compared; preserve the original
Save comment unless a separate approved change covers it.

Readback files contain `started_at` (or the existing collector's `at`),
`completed_at` and `readbacks`. Times must describe the actual capture interval,
never the time a stale response was copied. The whole capture must be at most two
minutes old. Missing, changed or active-build responses refuse.

From the repository root, with its `eval` package on `PYTHONPATH`:

```text
python -m scripts.pipelines.diagnostic_preparation stage --session <attempt> --spec preparation-spec.json --readbacks static-readbacks.private.json
python -m scripts.pipelines.diagnostic_preparation draft --session <attempt>
```

Stage writes no authority or execution packet. Draft starts one fixed authority
lifetime, at most 30 minutes and never beyond the original setup deadline. It
writes the exact private `diagnostic-preparation/variables.private.json` Save
payload and `configuration.private.json` expected GET shape. Keep the definition
Disabled while saving just these three variables. The draft is not owner-dispatch
approved; do not enable or queue from it. The hosted runtime could already accept
the saved authority, so this is an owner procedure boundary, not a second runtime
authentication mechanism.

Azure DevOps masks the secret `P8_PREFLIGHT_JSON` value in GET responses. Record an
actual private Save witness with `definition_id`, `started_at`, `completed_at`,
saved `revision` and the exact three `variables` captured from the submitted
inputs. Read back the saved definition and all other protected settings. The GET
must show a masked secret and the exact public authority digest/source, while the
witness binds the submitted transport. This cannot prove the masked plaintext
from GET; the hosted job still verifies its decoded authority against that digest.
Never substitute a claimed PASS or a locally edited expectation for either actual
browser original.

```text
python -m scripts.pipelines.diagnostic_preparation finalize --session <attempt> --readbacks configured-readbacks.private.json --save-witness authority-save.private.json
```

Only finalize creates `diagnostic-preparation/execution-packet.json`. Its cutoff is
the earliest of finalization plus five minutes, original authority expiry minus
21 minutes, and original setup deadline minus 21 minutes. The 21 minutes retain
the unchanged 20-minute job timeout plus one minute; finalization requires at
least four minutes for final review and submission. A slow variable Save can
therefore refuse before packet issuance without renewing the authority.

Obtain independent PASS on that exact packet and every bound original. Store the
actual review as `kind: independent-diagnostic-packet-review`, `verdict: PASS`,
zero integer `critical`/`important` counts, the independent `reviewer` and exact
`packet_sha256`. Rehearse and budget the final review, Save and readback path
before approving another resource setup; do not assume human review or browser
operations take no time.

```text
python -m scripts.pipelines.diagnostic_preparation enable --session <attempt> --readbacks before-enable.private.json --review independent-review.json
# Only after PASS and the existing live setup guard: owner enables via browser.
python -m scripts.pipelines.diagnostic_preparation queue --session <attempt> --readbacks before-queue.private.json --review independent-review.json
# Only after PASS and the existing live setup guard: owner submits the single exact diagnostic.
```

Before enable, capture a fresh complete Disabled snapshot. After enable, capture
the Enabled definition and retain only still-fresh actual originals for unchanged
resources; the snapshot start must remain the earliest included capture time.
Both guards compare exact operational settings, source, bindings, stop state and
review, then recheck wall time after slow reads/hashes. They require two minutes
of submission runway. Queue admission also enforces the original 120-second
maximum from the dispatch reservation, conservatively including enablement time.
The first guard exclusively reserves dispatch before
enablement; the second exclusively consumes queue submission before the browser
click. Partial output, an existing intent or an uncertain submission cannot be
replayed. Disable/restore and follow the original approved stop/cleanup procedure
on a blocked phase. Never remint a packet, reset a clock, extend a cutoff or
automatically retry. Ordinary pipeline results and acceptance remain unverified
until their separate actual runs pass.

Fresh identities and any temporary Reader assignments needed for AzureCLI
subscription discovery require their own exact plan and approval. Restrict each
temporary Reader assignment to that identity's own ARM resource, never the
subscription. Remove those assignments after diagnosis and before any paid attempt.
No agent PAT, Search service or paid Container Apps agents are needed for this mode.
Actual authentication and refresh must pass for all three roles before pricing and
reviewing the next paid execution packet. Synthetic unit tests cannot replace this
actual job evidence.

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

Ten minutes before cutoff it also writes `quiescence-preparation.json`. This advisory
file does not wake the operator by itself and authorizes no early restoration,
cancellation or deletion. Before billing, arrange an active operator to inspect it,
avoid long work after preparation, and prioritize the actual cutoff request. Its
`acknowledge_by` leaves 45 seconds before the unchanged cleanup due time. Failure to
write this notice cannot suppress the original cutoff or independent cleanup fallback.

Each authenticated runtime stage also copies the exact token-free `identity.json`
artifact bytes to `evidence/identities/<stage>.json`, after its create-only stage
reservation. Collect these originals through the existing owner Blob read channel;
verify source, release digest, role and actual build ID before promotion. The artifact
copy remains available, but its download permissions are no longer a prerequisite.

Before billing, prepare the owner-browser monitor to inspect this request at least once
per minute: disable only its exact new pipeline, cancel only its listed session-owned
unfinished builds, restore the exact original variables/settings, and retain actual
definition/empty-queue and `quiesced-build-<id>` originals through the private inbox.
Register those bytes in `originals.json`, then run `quiescence --session <session>
--acknowledge verified --request-sha256 <actual-request-sha256>`. The acknowledgement
rechecks those originals, every build and the request digest. For early successful or
explicit-stop cleanup, use `--request-closeout` and complete the same browser handoff
before requesting native cleanup; this command independently checks early authority.
Early native dispatch and direct cleanup execution require that verified
acknowledgement. A missing or failed handoff leaves the deletion/helper reservation
unused. Complete restoration and acknowledge it, then request early cleanup again.

If the browser is unavailable or an action fails, record `--acknowledge unavailable`
or `failed` against that same request. Missing/unverified acknowledgements are recorded
at cleanup, never an unbounded wait past the approved profile's cleanup due/start bounds.
Physical cleanup still proceeds within
its scope; P8-8 cannot pass without actual pre-delete disabled/empty/restored evidence.
This due-time fallback is unchanged by the early-cleanup acknowledgement gate.
The concrete monitor must preserve these instructions and adopt the existing watchdog
and native helper rather than launching duplicates. No monitor is created by this runbook.

Reservations precede browser queueing and corpus/query context. Release reserves test
and prod sets together; negative and scheduled require original positive gates. Worker
Blob reservations enforce the same no-replay rule on new agents. Fresh authority must
include the exact `metadata_policy` object defined by `scripts/pipelines/metadata.py`.
Its version is `controller-search-get-v2`: 256 Python controller/owner-helper Search
GETs, comprising workload slots 000..234, owner cleanup contingency 235..243 and
physical-observation-only 244..255. Each upload still has at most 200 requests.
A reservation is consumed even if its request subsequently fails. Missing, modified
or stale policy refuses before worker authentication. The human scope digest and
the complete agent authority digest bind the policy and its counting boundary.

Terraform/AzAPI provider traffic is outside this controller counter. It remains
limited to the manifest-bound two-index lifecycles, exact saved-plan validation,
pinned provider behavior and original parent command deadlines. This policy is
not a universal Search HTTP ceiling and grants no command retry or corrective apply.
The owner approved this changed counting boundary explicitly; future packets must
carry it unchanged. The 916 benchmark queries, correctness floor 120/229, resource,
upload, cost and timing limits are unchanged.

Use `BlobStore(..., metadata_policy=raw['metadata_policy'])`, then
`reserve_metadata()` for each ordinary GET and `reserve_metadata(physical=True)`
for the physical observer. Never copy the legacy 108/120 constants into a fresh
adapter. Those defaults only preserve the smaller allowance in low-level legacy
utilities; they cannot open 256 slots. Generic allocation cannot enter cleanup or
physical ranges through an interior start. Local fallback metadata allocations are
terminal cleanup-only; any such intent prevents later dispatch. There is no second
local allowance available to resumed cloud work.

Before the negative fingerprint, admission requires ordinary_used <= 101; after it,
ordinary_used <= 168. Pass the explicit policy to `fingerprint_capacity`; these
bounds retain both 43-read fingerprints, all remaining 24-read readiness loops and
nine cleanup reads. No optional diagnostics use the cleanup reserve early.
After capturing schema, 42 ordered pages and the actual state original, call
`complete_fingerprint` to seal each capture. The after receipt must follow the
completed source-bound negative run and match the before fingerprint. Owner cleanup
uses `reserve_cleanup_metadata`; opening 235..243 requires re-reading the complete
43-GET original evidence and its hashes, claims and chronology, or an explicit
source/session-bound human stop plus durable quiescence request. A stopped workload
is blocked locally at dispatch and through a create-only shared Blob marker on
subsequent worker metadata/process entry. Physical cleanup observations retain
their own range. Stage controllers never consume the nine cleanup slots.

Uploads serialize the frozen float32 vectors directly, preserving their float32
bits and keeping the existing 500-document/12-MiB batch and 240-second bounds.
The stage artifact includes `upload-test.metrics.json` or
`upload-demo-prod.metrics.json` on success and refusal. These contain numeric
request/byte/confirmed-document counts and encoding/HTTP/elapsed timing, never
corpus or response text. Encoding overlaps HTTP, so the timings are not additive.
A successful upload metric does not imply that the subsequent count or quality
gate passed.

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
  Release suites must carry the actual release build's orchestration PlanId and prove
  its six Test/Prod connection and demo-prod environment checks. Preserve the real
  browser association of that build ID, approval ID and approval check-run ID in the map;
  a successful approval from an earlier run does not satisfy this attempt.
- `scheduled-checks-evaluations`; original suites for the actual Schedule build's
  orchestration PlanId, proving both Verify connection checks. The Schedule branch
  uses only Verify; the Manual branch uses only Test and Prod. The complete pinned
  snapshot still contains all eight checks. Never substitute checks from one run's
  PlanId for another, or count an unused connection's checks as a release prerequisite.
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
