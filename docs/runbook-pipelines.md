# Project 8: checked Search delivery

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
reviewed owner authority, with no queue override permission. Keep the whole
authority secret. Also mark the individual tenant, subscription, client and object
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
that exact binary plan, loads once, grades and creates the original prod plan;
`prod` regrades the original complete test JSON/JUnit before consuming the prod
plan. Bind plan bytes, input/lock hashes, source/release, target and canonical
state lineage/serial/hash. Authenticated initial Blob absence is distinct from a
failed read. Wrong target, dirty source, stale state or changed artifact refuses.
Private Blob `If-None-Match: *` receipts prevent replay across disposable agents.
Plan binaries/inputs and gate originals also publish as private pipeline artifacts.
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

The daily `0 9 * * *` UTC cron is 19:00 Brisbane, main only, `always: true`.
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

The concrete dated proposal must fit first billable apply at 18:00 Brisbane and
the 19:00 schedule: queue cutoff +75 minutes, cleanup trigger +78, actual cleanup
start by +80 and verified closeout by +120. Apply start is the first billable
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
