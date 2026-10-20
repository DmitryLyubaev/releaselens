# Project 8: Azure Pipelines search delivery - scope refresh

> **Superseded in part, 20 October 2026,** by
> [the simplified delivery design](2026-10-20-pipelines-simplified-delivery-design.md): its
> hosted agents, long-lived platform stack and per-run search service replace this design's
> self-hosted agents, session clocks and controller. The intent, gate and frozen data below stand.

Status: search-only scope approved by the owner on 16 October 2026. Scope refreshed
15 October. Approval authorizes the implementation plan; implementation, public publication,
identity changes, Azure resources and paid calls retain their separate gates.
Audit baseline: ReleaseLens main `3baa6bf92e30848ccde0852792da82462b031a5e`.

## Intent

Demonstrate a small, explainable delivery pipeline for a CV: build a versioned search
release, deploy it to a test index, measure retrieval quality, obtain a real approval,
then deploy the same release to a demonstration production index. Prove that bad or
missing benchmark evidence prevents promotion. Code stays on GitHub and the Azure DevOps
project stays private. The production label describes a demo target.

This proposal narrows the September plan to search index/schema/query-configuration
delivery. It does not migrate the whole RAG application or deploy a new HTTP application.
The owner approved that boundary and the full designer defaults on 16 October 2026.

## What the audit found

| Existing component | Reuse and correction |
|---|---|
| Project 2 | Frozen 229-question set, 41,825-chunk corpus, saved small-model corpus vectors, search schema/query code and exact scoring. The original six-arm research CLI also requires Postgres/Worker outputs and fresh embedding calls; it is not a ready-made CI gate. |
| Search infrastructure | `infra/search/main.tf` uses Basic, with key authentication disabled. Its group/state/index names and owner/bootstrap roles are tied to earlier projects. Reuse patterns, not its live state or ownership. The old Free-tier assumption is obsolete for this implementation. |
| Project 5 | Existing ingestion and MCP tool apps have different purposes and fixed index settings. They are not test/prod replicas. No new Function apps, MCP authentication path or APIM stack is needed for this proposal. |
| Project 6 | Its App Service release and evidence stay on their current GitHub Actions path. |
| Project 7 | Reuse the personal organisation, private project, pool, one-shot agent lifecycle and immutable-image publication pattern. Session resources were removed. The existing image has .NET/Python/Git, but no Azure CLI or Terraform; a reviewed, pinned toolchain update is required. |
| Current GitHub workflows | Keep repository CI/image publication and existing app/gateway/webapp deployment ownership. A separate P8 search stack is deployed only by Azure Pipelines; no two systems manage its state. |

## Proposed defaults selected by the designer

1. One fresh, session-owned Basic search service with two distinct indexes: test and
   demo-prod. Separate Azure DevOps environments and index Terraform states. This is
   logical separation on a shared service, not a claim of production security isolation.
2. Deliver the existing hybrid-search configuration, equivalent to Project 2's S2 arm.
   Semantic ranking/S3 and the complete six-arm comparison stay outside this gate.
3. Use all 229 existing questions. The original S2 baseline is **120 correct top-1 out of
   229**, approximately 52.4%; 0.637 is its MRR, not its top-1 accuracy. The initial floor
   is at least 120 correct, with all 229 evaluated and zero errors/skips. Do not lower it
   to rescue a failed candidate; a different gate needs a newly reviewed decision.
4. Reuse saved corpus embeddings. Freeze query vectors once, with model/version, dimensions,
   question hashes and exact qid mapping. The research runner does not retain its query
   vectors, so their availability must be checked. If absent, one separately approved
   preparation batch embeds the 229 questions with the existing small-model deployment
   (source declares `text-embedding-3-small`, version `1`). No question generation, chat
   model calls, full-corpus re-embedding or runtime embedding calls belong in delivery.
5. Keep Docker/image builds in GitHub Actions. P8 agents run a limited Docker-free workload:
   applicable unit/contract tests, Terraform, index loading, queries and evidence output.
   Existing full-solution/Docker integration CI remains on GitHub.
6. One positive release, one deliberately bad test-only query configuration and one bounded
   scheduled read-only verification. No always-on environment or unattended billable retry.

Alternatives considered: rerunning all six research arms adds unrelated databases/model
calls; copying the existing Function apps adds hosting/auth work; moving the full API adds
its Postgres/container deployment. Those remain separate future work rather than P8 prerequisites.

## Release and stage boundaries

Build/test produces one digest-bound release bundle: source SHA, schema, S2 query settings,
scoring code, dependency/provider locks, corpus/vector hashes, question/query-vector hashes
and the predeclared gate. Raw corpus/vector inputs travel through approved private storage
and are verified against this manifest; they are not committed or manually copied between
agent workspaces. Storage location and bounded transfer sizes are settled in the implementation plan.

The manual pipeline follows these stages:

1. Build/test and validate the complete input bundle before cloud writes.
2. Plan the test index; publish its binary plan and redacted review summary privately.
3. Apply that exact test plan, load the frozen payload and verify its identity/count.
4. Run the 229-question gate against the actual test index and publish JSON/JUnit evidence.
5. Plan demo-prod from the same release bundle. Publish its separate target-bound plan.
6. Wait for the real owner approval and resource checks, apply that exact prod plan,
   load the same frozen payload and verify prod count/configuration and all 229 queries.

Test and prod have different saved plans and state lineages. Never apply a test plan to
prod, rebuild/re-embed after the gate or silently re-plan at apply. Bind each plan to its
source, release digest, target, state lineage/serial, provider locks and input hashes.
Changed/stale/missing artifacts refuse before mutation. Terraform controls index schemas
through supported AzAPI data-plane resources; document uploads are a separate bounded,
digest-bound step. No index aliases or atomic-swap claim is required.

Publish failures and missing results honestly. Each qid is a JUnit case; an incorrect
answer can be allowed by the aggregate floor, but every missing/error/skipped case and
an absent or incomplete benchmark fail the release regardless of the aggregate score.
Production smoke/quality failure is a failed release, not permission to rebuild or retry.

The negative run uses the same frozen corpus but an explicitly labelled test-only query
filter that returns no matching artefacts. Its genuine retrieval results must fail the
floor and leave prod unchanged. It can never be approved for production. Offline tests
also cover missing/skipped evidence, altered plan/bundle hashes and wrong target/source.

## Identity, checks and ownership

Use dedicated workload-federated Azure service connections for the P8 targets. No Azure
deployment client secret is introduced. Their exact issuer/subject come from the current
personal organisation setup, not copied older issuer strings. Validate expected tenant,
subscription and principal; do not run the owner-only research account guard in CI.

Owner-only, separately approved preparation creates the fresh service, scoped identities/
role bindings and P8 state/input containers. CI receives access only to the fresh search
service and required P8 containers, with no subscription-wide grant or role-assignment
permission. Schema-management rights can cover both indexes on this shared service;
separate service connections therefore do not prove hard test/prod isolation. No existing
bootstrap role, previous state or earlier search index is repurposed silently.

Restrict the new delivery pipeline to protected GitHub main, a required `extends` template,
explicit service-connection/pool authorization and exclusive release access. Configure
approval/branch/template checks on the protected Azure DevOps resources outside pipeline
YAML. With one personal owner, allowing that owner to approve their own demo is explicit;
do not claim independent two-person approval. Do not modify or re-enable the P7 demo pipeline.

Raw Terraform plans/state and input data remain private, access-restricted and retained
only as the approved evidence policy allows. Never publish account URLs/IDs, credentials,
OIDC tokens or raw cloud logs. The agent-registration PAT remains a separate, short-lived
Agent Pools credential; deployment federation does not eliminate that existing requirement.

## Schedule, costs and cleanup

A nightly YAML schedule selects a read-only prod quality-verification path using the
already frozen vectors; it cannot create resources or approve promotion. Demonstrate one
actual scheduled run within a separately approved active window, record its schedule
reason and result, then disable the pipeline/schedule. Outside that window it stays off;
expired/missing session authority refuses. Do not claim continuous nightly operation.

Every live window needs a fresh reviewed source/plan, resource manifest, price estimate,
monetary ceiling, exact wall-clock cutoffs and cleanup monitor before apply. Proposed
planning envelope: at most two hours, with queue cutoff/cleanup reserves derived from
the measured P7 overhead (about 17 minutes creation and 26 minutes deletion). This is not
live authority; the implementation plan must show all stages and scheduled work fit.
Bound positive test/prod, negative and scheduled checks to four sets of 229 S2 queries,
plus explicit metadata/smoke bounds. No semantic-ranker allowance is consumed.

Cleanup is independent of successful deployment. After failure, stop promotion and permit
one bounded read-only diagnosis while retaining resources until the agreed cleanup time
or real owner stop. No corrective apply or automatic replay is implied. An owner runtime
outside ACA destroys only manifest-bound P8 search resources, then its separately owned
agent host; a job must not destroy the environment it is running inside. Verify canonical
absence, empty successful states, unchanged complete retained inventories, disabled/empty
queues, original settings restoration, fresh PAT revocation and owned keeper exit. Preserve
all original failed/not-run outcomes and any missed timing bound.

## Acceptance and completion

- P8-1: pinned agent/toolchain and original source/input/release digests verified.
- P8-2: actual workload-federated deployment with scoped identity and no Azure client secret.
- P8-3: approved exact target-bound binary plans applied; tampered/stale plans refused offline.
- P8-4: complete live test gate evaluates 229/229, zero errors/skips, at least 120 correct.
- P8-5: real owner approval/checks recorded; identical release promoted and prod gate passes.
- P8-6: deliberate live bad-quality candidate fails and prod remains unchanged; missing/skipped
  benchmark rejection and source/template/branch boundaries have retained validation evidence.
- P8-7: one actual scheduled read-only run, followed by disabled schedule/pipeline proof.
- P8-8: verified scoped cleanup, honest timing and complete unchanged retained inventories.

Finish with a dated report of actual sources, counts, costs/limits, failures and cleanup,
independent review, separate approved public evidence merge and green main CI. Only then
mark P8 done. Boards linking is optional supporting practice; a repo migration, Artifacts
feed, classic releases, production operations and projects 9 onward are outside this scope.

## Builder decisions and approval boundaries

The builder chooses exact file/module layout, compatible pinned tool/provider versions,
private storage transport, bounded batches/timeouts and presentation of JUnit cases within
the above constraints. Exact RBAC actions and state/input-container access must be listed
and tested before any identity preparation, not discovered by adding roles to a failed run.
Validate data-plane provider plan/apply support and toolchain in offline/contract tests before
billing. Keep published P2/P5/P6/P7 evidence and frozen session controllers unchanged.

The owner approved this search-only scope and designer defaults on 16 October 2026 via
`call_895ab24090224ca4b7e4dfcfcf8eaf34` ("Approve the search-only scope"). The implementation
plan is authorized; product implementation waits for plan review. Public push/merge,
identity/federation changes, paid preparation and a billable attempt each retain their
separate existing approval gates. None were performed by this refresh.

The owner subsequently approved the implementation plan and local Native execution on
16 October 2026 via `call_4aa3ab0648184500be087b607c127eb6`. That approval covers local
implementation and its independent whole-branch review. The outward/live gates above
remain separate.

## Official references checked 15 October 2026

- [Azure Pipelines approvals/checks](https://learn.microsoft.com/en-us/azure/devops/pipelines/process/approvals?view=azure-devops)
- [Workload identity service connections](https://learn.microsoft.com/en-us/azure/devops/pipelines/release/configure-workload-identity?view=azure-devops)
- [Azure CLI task and agent prerequisites](https://learn.microsoft.com/en-us/azure/devops/pipelines/tasks/reference/azure-cli-v2?view=azure-pipelines)
- [AzAPI data-plane framework and Search indexes](https://learn.microsoft.com/en-us/azure/developer/terraform/concept-azapi-data-plane-framework)
- [AI Search roles and index-scoped data access](https://learn.microsoft.com/en-us/azure/search/search-security-rbac)
