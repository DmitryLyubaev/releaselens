# Project 8, simplified: Azure Pipelines search delivery on hosted agents

Status: design for the owner's review, 20 October 2026. It replaces the session-controller parts of
[the 15 October design](2026-10-15-azure-pipelines-search-delivery-design.md). That design's intent,
gate and frozen data stand; its self-hosted agents, session clocks and controller do not.

## Why this exists

Project 8 is a CV piece: a small, explainable delivery pipeline. It builds one versioned search
release and deploys it to a test index. It measures retrieval quality there and gets a real human
approval. Then it deploys the same release to a demonstration "prod" index. It proves that a bad
configuration cannot be promoted, and that a genuine scheduled run verifies what was deployed.

Five live attempts (p8-1018e to p8-1019e) all failed inside the bespoke session machinery that
wrapped the pipeline, not in Azure DevOps or Azure:
- a validator expected empty state;
- a five-minute submission window expired;
- a process-identity check refused its own launcher;
- a too-strict comparison rejected an ID that Azure DevOps generates.

That machinery had grown to session clocks, keepers, cross-OS file locks, browser handoffs, paid
admission and a 5.5-hour window, with the owner on standby. It also made the system the pipeline
delivers hard to see. The owner chose to keep the delivery and replace the machinery with standard
Azure DevOps and Terraform features, then run once.

## Decisions the owner made (20 October 2026)

- **Microsoft-hosted agents.** No self-hosted agents, agent infrastructure, custom agent image or
  registration PAT. Project 7 already showed self-hosted agents in Container Apps.
- **Free pieces are set up once and kept.** These are the pipeline identities, their federated
  credentials, the Azure DevOps service connections, and the environments with their approval check.
  Only the search service and its indexes are created and destroyed per run.
- **The owner's time on the run day is about two hours, ideally less.**
- **The genuine scheduled run fires at 09:30 Brisbane, which is 23:30 UTC** (Brisbane has no daylight
  saving). The owner starts the release at about 08:30.

## Goals

1. **One manual release run on `main`:**
   1. build and test;
   2. create the search service;
   3. plan, apply and load the test index, then score all 229 questions against it;
   4. **owner approval** on the demo-prod environment;
   5. plan, apply and load the same release into the demo-prod index, then score it.
2. **One manual negative run.** The release pipeline runs with the deliberately bad query
   configuration as its candidate. It must go red at the test gate, its prod stages must not run, and
   the prod index must be unchanged.
3. **One genuine scheduled run** (`Build.Reason = Schedule`). It verifies prod read-only by scoring
   all 229 questions, then destroys the indexes and the search service, even if verification failed.
4. **The evidence:** per-question JUnit results, the pipeline runs and their artifacts, gate results
   written to Blob, and a short public report, `docs/pipelines-report.md`, with no identifiers.
5. **The owner's part on the day** is three clicks and a final look, inside about 75 minutes. See
   "The run day".

## Non-goals

- **Rebuilding the corpus or embeddings, or calling any model.** The frozen 41,825 rows, 229
  questions and 1,536-dimension vectors stay as they are, with zero model or embedding calls.
- **Session clocks, keepers, locks, watchdogs, browser handoffs, pipeline definition Saves from code,
  or PATs.**
- **Moving the API or the RAG application.** Project 8 stays search-only, as the 15 October design
  set out.
- **Production-grade isolation.** Test and demo-prod are two indexes on one shared Basic service:
  logical separation, as before.

## Anti-goals: these make it a failure even if it runs

- **A green release that skipped a gate.** Every one of the 229 questions must be evaluated, with zero
  errors or skips, for both the test and prod scores.
- **A negative run that does not go red at the gate,** or one that changes the prod index.
- **A scheduled run that was really manual,** or that tears down without verifying first.
- **Anything left billing after the scheduled run.** A search service still present after teardown is
  a failure, even if every check passed.
- **Any identifier in a public file:** tenant, subscription, object or client IDs, hostnames, emails,
  real resource-name suffixes.
- **Deleting historical evidence.** Codex's worktrees, attempt directories and branches stay
  untouched.

## Constraints

- **The gate is unchanged:** at least 120 of 229 correct at top 1, all 229 evaluated, zero
  errors, missing or skipped questions. Do not lower it to rescue a run. The negative run must have
  0 correct, with all 229 rows empty (its filter matches nothing).
- **The frozen release is reused.** The 14-file bundle built at `5642301` has a manifest digest
  beginning `21cc39b5` and a 171,575,192-byte archive. It is kept privately in Codex's recovery
  worktree under `.local/project8/p8-1020a/`. Rebuild it only if a bundled file it checks against the
  repository (`scorer.py`, `requirements.lock`, `provider.lock.hcl`) changes.
- **Terraform 1.15.8, azurerm 5.7.0, azapi 2.8.0,** with `-lockfile=readonly`.
- **The Python dependencies and the pinned Azure CLI** come from the existing hash-locked files in
  `build/delivery-agents/`.
- **Keyless throughout:**
  - pipeline jobs authenticate with workload identity federation;
  - the search service has local auth off;
  - storage has shared keys off.
- **The owner's standing rules:**
  - ask first before any apply, destroy, push, paid run or identity or credential change;
  - check the signed-in account before any Azure command;
  - Terraform runs only in WSL;
  - never permanently delete the owner's files;
  - report results faithfully, including failures.
- **Norton** inspects TLS on the owner's Windows machine and can stall short HTTP replies. Any
  owner-side check that reads a refusal or an empty reply reads the status line first, or runs from
  WSL.

## The shape

### Set up once and kept: `infra/pipelines/platform`

A new long-lived stack, owner-applied, with remote state in the existing bootstrap state storage. It
replaces the per-session `identity` stack and the long-lived half of `foundation`:

- **Three user-assigned identities:** test, prod and verify. Their names lose the session slug.
- **Their federated credentials.** Each trusts the matching Azure DevOps service connection's issuer
  and subject.
- **A storage account** (shared keys off, LRS) with the private containers `inputs`, `test-state`,
  `prod-state`, `evidence` and `service-state`.
- **An empty resource group, `rg-p8-search`,** where each run's search service will live.
- **The role grants, made once at resource-group or container scope,** so a run never creates a role
  assignment. The 15 October matrix carries over, with its search-service scope moved to the group:
  - test and prod: Search Service Contributor and Search Index Data Contributor;
  - verify: Search Index Data Reader;
  - every identity: Blob Data Reader on `inputs` and Blob Data Contributor on `evidence`;
  - each writer: Blob Data Contributor on its own state container;
  - test: also Blob Data Contributor on `service-state`.

  Each identity destroys only what it owns: Prod destroys the demo-prod index and writes
  `prod-state`; Test destroys the test index and then the service. No identity gets write access to
  another's state.

It costs pennies a month: storage, plus a resource group, identities and grants that are free.

**One-time Azure DevOps setup:**
- **create a fresh pipeline definition from the new YAML.** The old definition, pipeline 2, was
  saved from code many times by the controller. It is left disabled and untouched, as history,
  because UI trigger overrides on it could stop the YAML schedule from registering. The owner
  permits the connections, environments and variable group on the new pipeline's first run (one-time
  clicks in Azure DevOps);
- **a new non-secret variable group, `ReleaseLens-P8-Delivery`,** written once from the platform
  stack's outputs. It holds the private values the public YAML must not carry: the storage
  account's name, the search group's name, the expected tenant, subscription and principal IDs, and
  the release digest. The old `ReleaseLens-P8` group is left as it is;
- point the three existing service connections (`ReleaseLens-P8-Test`, `-Prod`, `-Verify`) at the
  long-lived identities;
- keep the existing environments, `ReleaseLens-P8-Test` and `ReleaseLens-P8-DemoProd`, with the owner
  approval and exclusive lock on demo-prod;
- keep the connections' branch checks;
- upload the frozen release archive once to `inputs/releases/<digest>/release.tar.gz`, and read it back
  by hash.

### Per run: the search service and the indexes

- **`infra/pipelines/service`:** a new small stack holding one Basic search service in `rg-p8-search`
  (1 replica, 1 partition, local auth off, free semantic tier), with state in `service-state`. The
  release's first cloud stage applies it under the Test connection; teardown destroys it.
- **`infra/pipelines/index`** is unchanged: one index per target, with state in `test-state` or
  `prod-state`.
- **Teardown order:** destroy the test and demo-prod indexes, then the service. All three states end
  empty, so the next run starts clean.

### The pipeline (`pipelines/search-delivery.yml` and its template)

- **Pool:** `vmImage: ubuntu-24.04` for every job.
- **Every job's toolchain:**
  - `UsePythonVersion` 3.12;
  - two virtual environments built from the hash-locked files, as the existing identity-preflight stage
    already does;
  - the pinned Azure CLI venv first on `PATH`;
  - Terraform 1.15.8 downloaded and SHA-256-checked by `build/delivery-agents/install.py`.
- **Triggers:** `trigger: none`, `pr: none`, a `mode` parameter (`release`, `negative`, `teardown`),
  and the schedule `cron: '30 23 * * *'` on `main` with `always: true`.
- **Release stages:**
  1. build and test (validate the bundle, run the unit tests);
  2. create the service;
  3. plan the test index;
  4. apply the test index, load it and gate it (`ReleaseLens-P8-Test`);
  5. plan prod;
  6. apply prod, load it and gate it (`ReleaseLens-P8-DemoProd`, after the owner's approval).

  Each plan is bound and verified as `scripts/pipelines/plans.py` does today.
- **Negative:** the release pipeline itself, with the deliberately bad query configuration
  (`negative.json`, a filter that matches nothing) as its candidate. It is the same code path as
  today's negative stage, with no new Terraform:
  - the test gate goes red;
  - the prod stages show as not run, which is the visible proof that a bad candidate is not promoted;
  - the prod index's document count and schema are compared before and after, and must be unchanged.
- **Schedule:**
  1. If no search service exists, end green: "nothing deployed".
  2. If the current run's release or negative evidence is not complete (for example, the release is
     still waiting for approval at 09:30), end with "run incomplete: skipped". Do not verify, and do
     not tear down: tearing down under a running release could leave state locked and resources
     billing. The owner then queues `mode: teardown`, or leaves the service until the next day's
     09:30, which costs about one more day of Basic search.
  3. Otherwise, verify prod under the Verify connection. That needs the current run's prod and
     negative results in `evidence`.
  4. Then tear down, with `condition: always()`: Prod destroys the demo-prod index, then Test
     destroys the test index and the service.
- **Teardown mode:** the manual fallback. It runs the same teardown.
- **Evidence:** JUnit per question, pipeline artifacts, and gate results written once (create-only)
  under a namespace per release run, `evidence/<release run ID>/...`. The release writes a pointer,
  `evidence/current.json`, which is the one object it may overwrite. The negative and scheduled runs
  use it to find the release they check. A new release refuses to start while a search service
  still exists, so a retry always begins from a clean teardown.
- **The bundle and the source commit are recorded separately.** The pipeline runs a later commit of
  `main` than the one the frozen bundle was built at (`5642301`), so `run.py`'s check that the two
  match is relaxed. The bundle's own digest, and its bundled copies of `scorer.py`,
  `requirements.lock` and `provider.lock.hcl`, are still checked against the repository.
- **After the one genuine scheduled run,** a small follow-up PR removes the `cron`, so nothing runs
  nightly afterwards. The report shows the single scheduled run as evidence, not a claim of
  continuous operation.

### What carries over and what goes

- **Kept as they are:**
  - `eval/app/delivery_search/` (bundle, client, gate, contracts, prepare) and its tests;
  - the reused `eval/app/retrieval` scoring and schema;
  - `scripts/pipelines/plans.py` and `identity.py`, with their tests;
  - `infra/pipelines/index`;
  - `pipelines/templates/identity-preflight-task.yml`;
  - the hash-locked dependency files and `install.py`.
- **Kept with changes:**
  - `scripts/pipelines/run.py` keeps plan, apply, upload, measure and prior-gate, but loses the
    authority, cutoff, metadata, claims and attempt checks;
  - `storage.py` keeps Blob transport and state reads, but loses the budgets;
  - `identity_preflight.py` loses the packet and the pipeline-ID pin;
  - the pipeline YAML and template;
  - the `test_pipelines_{runtime,yaml,identity_diagnostics,identity_preflight,blob_collisions,infra}`
    tests;
  - `docs/runbook-pipelines.md`, rewritten short.
- **Removed from `main`.** Git history keeps all of it, and Codex's worktrees and `.local` evidence
  are untouched:
  - the controller modules: session, cleanup, owner, ownership, watchdog, quiescence, startup,
    paid_admission, prepaid_cleanup, definition, diagnostic_preparation, fingerprint,
    physical_observation, metadata, readiness, authority_transport, promotion, evidence, and
    `helper_templates/`;
  - their controller-only tests;
  - `test_pipelines_image.py`;
  - the delivery-agent Dockerfile and smoke parts;
  - `.github/workflows/delivery-agent-image.yml`;
  - CI's `keeper-startup-windows` job;
  - the per-session `infra/pipelines/identity` and `foundation` stacks.
- **Untouched:** Project 7's `infra/agents/` and `scripts/agents/`.

## The run day

| Brisbane | Step | The owner |
|---|---|---|
| ~08:30 | Queue the release: build and test, create the service, test index, gate | one click |
| ~08:55 | The release waits at demo-prod | approve |
| ~09:00 | Prod is applied, loaded and gated | — |
| ~09:10 | Queue the negative run: red at the gate, prod unchanged | one click |
| 09:30 | The scheduled run verifies prod, then tears down | — |
| ~09:45 | Check that no search service is left; the teardown evidence is green | a look |

**If anything stops:** queue `mode: teardown`. If that cannot run, the owner destroys the service
stack from WSL. The runbook gives the one command.

**Cost:** a Basic search service for about 90 minutes, plus storage pennies, so about AUD 1 a run.
Refresh the price before the run.

## Before the run day (each with the owner's yes)

1. Apply the platform stack, plan first.
2. Point the service connections at the long-lived identities, and run one identity-preflight job, a
   hosted job with zero search cost, to prove federation and token refresh for all three roles.
3. Upload the frozen release archive and read it back by hash.
4. Push the YAML with the 23:30 UTC schedule to `main`, through a reviewed PR. Azure DevOps registers
   schedules from the default branch.

## Left to the builder

- The exact stage, job and timeout layout, and how gate results pass between stages (Blob, as today).
- How the scheduled run detects "nothing deployed": the service's state, or an
  `az search service show` that answers 404.
- Whether `checks.py`, the pinned snapshot of the Azure DevOps check configuration, is worth keeping.
- How to rename or rewrite the tests listed above so that offline coverage stays meaningful. This
  includes plan binding, gate grading, the negative rule, and the schedule's verify-then-teardown
  ordering.
- How to present the 15 October design as superseded: a dated pointer to this document, without
  rewriting its approved text.

## Calls made by the designer (Claude), not the owner

- **The long-lived storage account and resource group,** with role grants at group scope.
- **The Test connection doubles as the lifecycle identity** that creates and destroys the service. It
  already needs Search Service Contributor, so no fourth identity is created. Prod destroys its own
  index, so no identity writes another's state.
- **A fresh pipeline definition and a new variable group.** The controller-saved pipeline 2 and its
  `ReleaseLens-P8` group are left untouched, as history.
- **The negative run is the release pipeline with the bad candidate,** so the prod stages visibly
  don't run.
- **A scheduled run during an unfinished release skips without tearing down;** the owner chooses
  between a manual teardown and leaving the service until the next day.
- **Evidence is namespaced per release run,** with a current-release pointer, and a new release
  refuses to start while a service exists.
- **The `cron` is removed by a follow-up PR** after the genuine scheduled run.
- **On non-run days, the schedule ends green with "nothing deployed" in about a minute.** It does not
  pause the pipeline, because changing the pipeline definition from code is what broke p8-1019e.
- **Teardown runs as the scheduled run's last stage,** with a manual fallback mode.
- **The controller code is removed from `main`.** It is not kept unused.

## Unverified until the run

- Hosted capacity is recorded as present: the 20 October handover lists 1 private hosted parallel
  job, with 8 of 1,800 free minutes used. It needs rechecking before the run.
- That hosted Ubuntu agents install the pinned toolchain within the job timeouts, and that the
  41,825-row upload stays inside its 240 s and 200-request budget from a hosted agent.
- Workload identity federation and token refresh for the long-lived identities. The one-time
  preflight in "Before the run day", step 2, settles this.
- How long a Basic search service takes to create and to delete.
- That the 23:30 UTC schedule registers from `main` and fires as `Build.Reason = Schedule`.
- That the Test identity can delete the service, and the indexes with it, under the group-scope grant.
