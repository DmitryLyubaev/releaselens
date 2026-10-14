# Ephemeral Azure Pipelines Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Demonstrate a queued Azure Pipeline starting a fresh Container Apps Job agent, building/testing ReleaseLens, publishing results and returning to zero workers.

**Architecture:** GitHub remains the source and builds a public, digest-pinned agent image. A private Azure DevOps pipeline uses a dedicated pool with an offline placeholder; a native Container Apps Jobs queue rule starts one-job agents. A separate Terraform stack owns only this demo's Azure host, while small offline-tested helpers handle secret insertion and evidence validation.

**Tech Stack:** Linux AMD64, .NET 10, Azure Pipelines agent, Bash/Python standard library, GitHub Actions/GHCR, Terraform 1.15.8 and AzureRM 5.6-compatible provider, Container Apps Jobs, Key Vault and managed identity.

**Spec:** [Approved design](../specs/2026-10-15-ephemeral-build-agents-design.md). Read it before this plan. Approval of the design permits planning; publication, organisation setup and live Azure work have later gates below.

## Global Constraints

- CV/interview demonstration only; no full CI migration or Project 8 deployment stages.
- Build/test `tests/ReleaseLens.Core.Tests` and `tests/ReleaseLens.Web.Tests` individually. No Docker daemon, Testcontainers, real backends, model calls or platform refreshes.
- Linux AMD64, SDK compatible with `global.json`, public anonymous-pull GHCR image pinned by digest, no additional Azure registry.
- One private personal Azure DevOps project, dedicated pool and manual trusted-main pipeline; no CI, PR, fork or scheduled triggers. Only the owner queues it.
- Target one active worker: minimum executions 0, maximum executions 1, polling 30 seconds, one replica/completion, retry limit 0, timeout 900 seconds, 2 vCPU/4 GiB. Observe actual counts rather than claiming a global scaling mutex.
- Two successful sequential runs, then one deliberately failed pipeline step. Failures remain failures; missing, empty or skipped tests never pass.
- One short-lived Agent Pools read/manage PAT, privately supplied, referenced through Key Vault/managed identity; no PAT in Terraform inputs/state, image, process arguments, logs or artifacts.
- Separate new stack/state/resource group; preserve existing bootstrap/application stacks and personal budget controls. No employer accounts/resources, added users, purchased parallelism, app registrations or federated credentials.
- Proposed live window 60 minutes including cleanup; no queues after minute 30; cleanup starts by minute 45. Obtain actual Brisbane deadlines and monetary ceiling before apply. One host apply, no automatic recreate after failure.
- Retain resources after a failed gate for bounded diagnosis; stop new queues. Cleanup remains within the agreed session, preserves Azure DevOps result history and revokes the PAT. No Key Vault purge is implied.
- Keep source, digest, timing and failures honest; public evidence contains sanitized labels/counts/times/Booleans, not account URLs, personal emails, resource IDs, credentials or raw logs.

## Review Focus

1. Cancellation or configuration failure must not leave a worker serving a second job or unregister another execution's agent. Task 1 tests signals, failed registration and unique ownership.
2. A malicious/manual non-main run or a queue capability mismatch must not become a credited demo. Task 2 tests main/source guards, capability parity and disabled triggers.
3. A missing secret or delayed role propagation must stop job creation without leaking the PAT or repeating an apply. Task 3 tests bounded secret readiness and Terraform dependency order.
4. Missing results, inconsistent timestamps, duplicate workers or a manually started worker must not become passing evidence. Task 4 tests rejection of each condition.
5. Cleanup must not race an active apply or remove unrelated resources; a partial failure must remain resumable and visible. Tasks 4/5 test ownership and document the closeout gate.

---

## File and responsibility map

| Files | Responsibility |
|---|---|
| `build/agents/Dockerfile`, `.dockerignore`, `versions.json`, `start.py` | Locked agent image and one-job/placeholder lifecycle |
| `.github/workflows/agent-image.yml` | Offline container smoke tests and separately approved image publication |
| `pipelines/agents-demo.yml` | Manual main-only Core/Web build/test and controlled failing step |
| `infra/agents/{versions,variables,main,outputs}.tf`, `.terraform.lock.hcl`, `tests/agents.tftest.hcl`, `README.md` | Isolated host, identity, vault, jobs and offline contract checks |
| `scripts/agents/seed_pat.py` | Account-checked private PAT upload before job provisioning |
| `scripts/agents/evidence.py` | Validate retained observations/TRX and export sanitized A1-A6 results |
| `tests/infra/test_agents_{runtime,pipeline,infra,evidence}.py` | Focused offline tests with synthetic subprocess/cloud/result fixtures |
| `.github/workflows/ci.yml`, `.gitignore` | Run offline checks and ignore private settings/evidence |
| `docs/runbook-agents.md`, `docs/agents-report.md`, `README.md` | Setup/live/cleanup instructions, actual measured results and portfolio link |

Use no Azure DevOps Terraform provider or custom organisation-creation controller. Private settings, state, PAT input and original observations live in ignored `.local/project7/<session>/`. Use existing Python/WSL tooling; do not install local Docker for this work.

### Task 1: Locked image and single-job agent lifecycle

**Files:** Create `build/agents/{Dockerfile,.dockerignore,versions.json,start.py}` and `tests/infra/test_agents_runtime.py`.

**Interfaces:** `start.py` consumes `AZP_URL`, `AZP_POOL`, `AZP_TOKEN`, `P7_SESSION`, `P7_MODE=worker|placeholder`, and `P7Agent=ReleaseLens`. It runs the installed `/opt/azp/config.sh` and `/opt/azp/run.sh`. Produce `main() -> int`, `run_agent(agent_dir: Path, environment: dict[str, str], nonce: str) -> int` and `agent_name(session: str, mode: str, nonce: str) -> str`. Tests inject a fake executable directory, environment and nonce into `run_agent`, without Azure access.

- [ ] Write failing tests: `test_worker_runs_once_and_unregisters_exact_name`, `test_failed_registration_does_not_remove_another_agent`, `test_term_forwards_signal_and_bounds_cleanup`, `test_placeholder_registers_offline_without_listener`, `test_pat_never_enters_argv_stdout_or_capabilities`, and `test_two_workers_have_distinct_names`. Assert one `run.sh --once`, no `--replace`, unique session prefix, exit preservation, maximum 30-second unregister attempt, no infinite loop and no listener for placeholder mode. Placeholder intentionally keeps only its own offline registration.
- [ ] Run `python -m pytest tests/infra/test_agents_runtime.py -q`; confirm failures exercise the missing implementation, not unavailable Azure/network tooling.
- [ ] Implement the lifecycle. Pass the PAT to configuration/removal only through supported `VSTS_AGENT_INPUT_TOKEN` child environment; remove it from the listener environment and exclude all credential variables from capability discovery. Capture configuration/removal output privately and emit sanitized phase/result markers. Terminate child process groups on cancellation; never dump environment or diagnostics into artifacts. Preserve the owned token in RAM only until bounded unregister finishes. Worker workspace is container-local with no shared persistent volume.
- [ ] Populate `versions.json` with agent `5.280.0`, official Linux x64 package URL and SHA-256 `f0daf1ce526af0b5eebf420249d21689de10faf00ffb0f19851626eb4352d791`. Resolve the Linux AMD64 digest for `mcr.microsoft.com/dotnet/sdk:10.0.201-noble` from MCR and record it. Verify package checksum and architecture; if unavailable/incompatible, stop to revise the lock before image publication. Never silently download latest at startup. Record the approved upstream source/license links.
- [ ] Implement a non-root Dockerfile using the locked base/package, Git, Python and official agent runtime dependencies. Copy only the image directory through its narrow build context. No Docker socket, Azure CLI, PAT, repository checkout, private files or package-manager credential configuration in the image. In-image smoke checks must find .NET/Git/agent executables and both worker/placeholder startup modes.
- [ ] Run the runtime tests and `python -m pytest tests/infra/test_public_docs_identifiers.py -q`. Inspect changed files for secret/identifier hazards, then commit `feat: add locked one-job Azure Pipelines agent image`.

### Task 2: Manual pipeline and isolated image publication

**Files:** Create `.github/workflows/agent-image.yml`, `pipelines/agents-demo.yml`, `tests/infra/test_agents_pipeline.py`.

**Interfaces:** Pipeline parameters: `controlledFailure: boolean=false`. Private pipeline variables: `P7_POOL`, `P7_SESSION`, `P7_SOURCE_SHA`. Agent demand: `P7Agent -equals ReleaseLens`, matching Task 1 and placeholder. Pipeline publishes `p7-results` containing only TRX and an allowlisted workload manifest, not environment/agent diagnostics. Image workflow emits digest and source labels for `ghcr.io/<owner>/releaselens-agent`.

- [ ] Write failing tests for `trigger: none`, `pr: none`, absent schedules, owner-configured queue permissions, main guard before workload, exact `Build.SourceVersion == P7_SOURCE_SHA`, matching demand, `checkout clean: true`, `persistCredentials: false`, no container jobs/Docker commands, and no app deployment. Tests must reject broader project/solution test selection and an unguarded controlled failure.
- [ ] Run `python -m pytest tests/infra/test_agents_pipeline.py -q`; confirm RED.
- [ ] Implement a single pipeline job with timeout 12 minutes. Before checkout/build work, require main and the approved source SHA. Assert no prior workspace marker, then create a fresh marker with execution-scoped identity. Restore/build/test each selected project in Release using `--no-restore`/`--no-build` appropriately, separate TRX filenames, and `PublishTestResults@2` with `condition: always()`, missing-file failure and failed-test failure. Run `exit 17` only when `controlledFailure` is true, after normal tests. Retain ordinary test failures rather than continuing to a fake success.
- [ ] Add an image workflow with PR validation that builds/smoke-tests on GitHub-hosted Linux but never publishes, plus manual main-only publication with `contents: read`, publisher-only `packages: write`, no Azure environment/OIDC, and SHA-pinned actions following current `ci.yml` pins. Build Linux AMD64 with the narrow context; execute fake-lifecycle tests inside the container, verify SDK compatibility, then push only in the approved publisher job. Upload public digest/provenance without logs/configuration.
- [ ] Run pipeline/runtime tests and `python scripts/check_workflows.py .github/workflows`. Commit `feat: add manual agent demo pipeline and image workflow`.

### Task 3: Isolated Terraform host and private PAT readiness

**Files:** Create `infra/agents/{versions,variables,main,outputs}.tf`, `.terraform.lock.hcl`, `tests/agents.tftest.hcl`, `README.md`; create `scripts/agents/seed_pat.py`, `tests/infra/test_agents_infra.py`; modify `.gitignore`.

**Interfaces:** Private Terraform inputs: `subscription_id`, `tenant_id`, `owner_object_id`, `location`, `session_name`, `organization_url`, `pool_name`, `image_digest`, `pat_file`, `session_dir`. No PAT value variable. Outputs: owned group/job/vault/identity references, secret versionless URI and `session_name`, captured privately. `seed_pat.py --vault-name ... --subscription ... --tenant ... --owner ... --pat-file ... --receipt ...` returns only `pat_ready=true|false` and sanitized failure category.

- [ ] Write failing mocked Terraform tests for exact settings from Global Constraints, immutable image digest, identity-bound Key Vault reference, secret never managed/read by Terraform, no shared state/remote-state references, no external stack writes and no broad roles. Python tests reject blank/missing PAT, wrong account/tenant/subscription, unowned receipt, leaked CLI errors, propagation timeout and plaintext token in child argv or generated files/state.
- [ ] Run `python -m pytest tests/infra/test_agents_infra.py -q` and the targeted mocked `terraform test` cases; confirm meaningful RED before implementation.
- [ ] Implement a local-state-only stack in a private `TF_DATA_DIR`/state path. No bootstrap backend changes. Use resource-provider auto-registration `none`; preflight confirms needed registrations. Create nine ARM resources: dedicated resource group, PerGB2018 log workspace (30-day retention, 0.1 GB daily quota), Consumption environment, RBAC Key Vault with soft-delete protection, user-assigned identity, two vault-scoped role assignments, event worker job and manual placeholder job. The identity receives only Key Vault Secrets User; the existing personal owner receives Key Vault Secrets Officer on this new vault for private setup. Provider features must not auto-purge/recover unrelated deleted vaults. Add one `terraform_data.pat_ready` dependency gate, giving ten planned managed additions. Reject any extra resource, destroy, replacement or change outside this stack.
- [ ] Use the dependency graph `vault + owner secret role -> pat_ready -> both jobs`; identity secret-reader role also precedes both jobs. The local-exec gate calls Task 3's helper with metadata/path arguments only, never the PAT. The helper captures `az account show` before Azure operations, checks the exact personal account bindings, reads the owner-supplied private file without printing it, and calls `az keyvault secret set --file ... --output none` with captured output. Bound role-propagation retries to 120 seconds. Receipt contains source/session/vault identity and secret readiness metadata only. No Terraform secret resource/data source/value, no secret output, no helper rerun after a successful receipt without matching identity.
- [ ] Configure worker Event and placeholder Manual jobs, both 2 CPU/4 GiB, retry 0, timeout 900, completion/parallelism 1. Worker scale is min0/max1/poll30, native `azure-pipelines`, pool/organisation metadata, `targetPipelinesQueueLength=1`, and PAT secret auth. Both jobs receive Task 1's environment contract and the same capabilities. Give placeholder a fixed session name and worker fresh execution names. Ignore only the provider's referenced-secret `value` field as documented, not whole configurations. Attach no ingress, registry credential, persistent volume or extra identity.
- [ ] Add `.local/project7/` to ignores. Use constraints matching repository Terraform/AzureRM versions and commit provider checksum lock for Linux AMD64 and Windows AMD64. Run `terraform fmt -check`, `terraform init -backend=false -input=false`, `terraform validate`, `terraform test` with mocks, and Python tests. No credentials or real plan/apply required by these checks. Commit `feat: add isolated ephemeral agent host stack`.

### Task 4: Honest evidence validation and scoped closeout contract

**Files:** Create `scripts/agents/evidence.py`, `tests/infra/test_agents_evidence.py` and synthetic fixtures under `tests/infra/fixtures/agents/`.

**Interfaces:** `grade(observations: dict, trx_paths: list[Path]) -> dict` returns `schema_version`, source/digest, `checks` A1-A6 with pass/fail/not-run and reasons, sanitized measurements and retained original-result labels. CLI: `python scripts/agents/evidence.py --observations <private-json> --trx-dir <private-dir> --output <private-json>`. No network calls; no credentials required. Observations use private original run/execution/agent identifiers; output maps them to run-1/run-2/negative labels.

- [ ] Write tests from a valid synthetic three-run observation set, then mutate one fact per rejection: manual activation; wrong source/digest; duplicate active workers; repeated agent/workspace; missing/zero/skipped/failed TRX for success; missing result publication; negative run mislabeled success; incomplete timestamps; no verified idle; unrelated-inventory drift; missing PAT revocation; active apply/cleanup collision. Assert A1-A6 fail/not-run honestly and no private field leaks.
- [ ] Run `python -m pytest tests/infra/test_agents_evidence.py -q`; confirm RED.
- [ ] Implement namespace-aware TRX parsing from executed counters/results, including duration and failed/not-executed cases. Match files to the two selected projects and actual builds rather than globbing any artifact into success. Require two successful original pipeline results with distinct identities, one deliberate failed original pipeline, actual queue/execution/agent correlations, zero active ephemeral workers after completion, and a known offline placeholder separated from compute.
- [ ] Define private observation schema in the module/runbook: recorded UTC times and Brisbane session deadlines; source/image digest; approved resource identities/baseline; original pipeline results; manual/event activation origin; worker count samples; placeholder identity; project TRX digests/counters; disabled queue/scaler state; canonical owned-group absence; empty Terraform state; unchanged unrelated inventory; owner-confirmed PAT revocation. Missing proof cannot satisfy A6. Idle observation is at least three consecutive 30-second samples with no queued work or running worker.
- [ ] Specify collection in the runbook using captured `az containerapp job execution list/show` for the exact owned job, `az group exists`, retained-group `az resource list`, and `terraform state list`. The owner exports original pipeline results/TRX and checks agent status from the private DevOps UI; no Build-read PAT is added to the Agent-Pools-only token. Match original exports/timestamps to observations, record their hashes, and never treat hand-entered success labels alone as proof. Before subsequent runtime work, inspect state privately for the known PAT value using a Boolean-only check; any unexpected secret persistence stops queuing and becomes a security finding.
- [ ] Make output a strict allowlist: labels, counts, durations, UTC/Brisbane timestamps, Booleans, public commit/digest and check reasons without raw input interpolation. Reject non-finite/negative timing and malformed records. Keep sanitized partial results on failure, never overwrite the original observations or original failures.
- [ ] Run Task 4 tests plus public-doc privacy tests. Commit `feat: validate ephemeral agent acceptance evidence`.

### Task 5: Offline integration, setup runbook and reviewable publication

**Files:** Modify `.github/workflows/ci.yml`; create `docs/runbook-agents.md`; update `infra/agents/README.md`. Do not write measured results before Task 6.

**Interfaces:** CI runs existing pytest infra tests, workflow checks and new stack mocked validation. Runbook consumes Tasks 1-4's exact names/commands and records a private session checklist, without a new orchestration platform.

- [ ] Extend current terraform-plan CI to validate/mock-test `infra/agents` and include its lock in the cache key. Run runtime/pipeline/evidence tests in existing infra test job. Retain all existing stack checks and application CI.
- [ ] Write owner setup steps: personal-account manual organisation creation and subscription association; choose private organisation name/geography; private project; dedicated pool; verify single self-hosted parallel allowance; one Boards work item; owner GitHub connection consent; manual YAML pipeline; restrict pool authorization to that pipeline; owner-only queue access; disable CI/PR/UI overrides, forks and schedules. Record a blocker if required allowance/permissions are absent. No automated account creation or extra purchases.
- [ ] Write private PAT procedure: Agent Pools read/manage only, dedicated organisation, shortest practical expiry, hidden/private file under ignored session directory with owner-only filesystem permissions, upload through Task 3 gate, then owner deletes that input and confirms revocation at closeout. Never ask for values in chat or reuse Project 6 inputs/cookies. Preflight private account/budget/provider/quota checks capture raw output privately and emit only Booleans/categories.
- [ ] Document the single-apply readiness gate and partial-failure response. Before apply: publish/review image, confirm anonymous digest pull, complete DevOps setup, capture fresh absence/unrelated-resource baseline, exact reviewed source, plan JSON ten additions/zero other actions, current price estimate and agreed monetary/deadline limits. No runtime worker starts until explicit placeholder setup/manual queue steps. A failed apply stops deployment, retains partial state, and proceeds only to bounded diagnosis/owned cleanup; no corrective reapply disguised as a retry.
- [ ] Document scoped cleanup: exclusive operation lock, no concurrent Terraform, disable this pipeline and clear only its queued runs, stop the demo's scaling, stop only manifest-owned executions, remove only owned stale online worker registrations, retain the known offline placeholder, review saved destroy plan against exact owned identities, destroy this state only, verify canonical group absence/empty state/unrelated inventory, owner revokes PAT, record any soft-deleted vault without purge. Resume an existing cleanup reservation instead of starting another. Establish finite cleanup monitor before apply and remove it after verified closeout.
- [ ] Run all `tests/infra`, workflow checker, Terraform fmt/validate/mock tests, .NET Core/Web tests individually if dependency-affecting files changed, and container lifecycle/workload smoke tests in GitHub validation once publication is authorized. Read the whole diff for source/secret boundaries. Obtain independent code review before merge; fix actual findings and rerun affected checks. Commit the integration/runbook.
- [ ] Present the completed code/diff and verification to the owner for public push/PR/image-publication approval under portfolio rules. After authorization, publish, attach the PR, require green original PR and main CI, verify GHCR package public/anonymous digest access. No Azure infrastructure apply merely because CI passes.

### Task 6: Owner setup, bounded live evidence and final report

**Files:** Create `docs/agents-report.md` after observations; update `README.md` with measured report link; retain original observations privately. Update the private portfolio tracker for meaningful state changes under its standing authorization.

**Interfaces:** Use reviewed Tasks 1-5 only, exact merged main source, locked published digest and a new private session. Acceptance is the approved A1-A6 table; do not reuse prior Project 6 acceptance for these checks.

- [ ] Complete manual owner DevOps setup and verify it before starting the billable window. Resolve any setup blocker without deploying. Present the actual resource plan, current region-specific price estimate including logs/vault, monetary ceiling, one-apply scope, two success/one negative queues and exact Brisbane stop/cleanup times. Obtain explicit live authorization and privately record it. Current proposed durations are not an already-approved monetary cap.
- [ ] Register finite cleanup monitoring with quiet unchanged-state behavior. Capture fresh canonical absence and complete retained-resource inventory. Acquire the exclusive session operation lock; run the one approved saved plan/apply, including PAT readiness gate. Capture raw output privately. A mismatch/failure stops queues and retains diagnosis evidence within the window.
- [ ] Start only the placeholder setup job once. Verify it terminates with the expected offline matching-capability registration and no listener. Record its manual execution as setup, never A1 evidence. Verify private GitHub connection and pipeline/pool permissions before the first queue.
- [ ] Queue success run 1 from approved main, observe actual queue-triggered worker, collect original result/TRX/times/counts, and wait for verified idle. Queue success run 2 only after run 1 passed, has returned to idle and the queue cutoff permits. Require a different execution/agent and fresh workspace.
- [ ] Queue the one controlled failure only after both success gates pass and before cutoff. Verify failed pipeline result, ordinary test artifacts retained, worker termination and idle. Unexpected duplicate workers, authentication/configuration drift, missing tests or a failed normal run stop further queuing; do not replay runs to replace failures.
- [ ] Start cleanup on completion or by the agreed minute-45 deadline. Follow Task 5's scoped closeout, confirm PAT revocation, grade actual A1-A6 and retain all partial/not-run results. Completion requires verified absence and unchanged unrelated resources, not a successful destroy process alone.
- [ ] Draft report with dated sample size (two positive, one intentional negative), measured startup/build/idle timings, actual test counts, source/image provenance, original pipeline outcomes and explicit concurrency/cost/production limits. Add a short Managed DevOps Pools comparison. Run public privacy checks and independent evidence review. Present for public publication approval, then merge only with green CI, verify main, update the private tracker and mark done only when code/evidence publication and A1-A6 closeout are verified.

## Verification and implementation handoff

Self-review coverage: acceptance A1/A3/A5 map to Tasks 1/2/4/6; A2/A4 to Tasks 2/4/6; A6 to Tasks 3/4/5/6. All five Review Focus cases have named test ownership. Live evidence remains separate from offline unit/mocked tests. Do not implement Tasks 1-5 until the owner reviews this plan and selects execution approach. Task 6 has its own concrete publication/live approval gates.

I recommend native execution for these six dependent tasks: image, pipeline, Terraform and evidence share a small interface, and implementing them in one session reduces context handoffs. Use one independent whole-branch review before merge. Subagent-driven execution remains available if the owner prefers a fresh implementer/reviewer per task.

## Sources checked 15 October 2026

- [Official agent 5.280.0 release and package checksums](https://github.com/microsoft/azure-pipelines-agent/releases/tag/v5.280.0)
- [Agent configuration environment input handling](https://github.com/microsoft/azure-pipelines-agent/blob/v5.280.0/src/Agent.Listener/CommandSettings.cs)
- [One-job mode](https://learn.microsoft.com/en-us/azure/devops/pipelines/agents/docker)
- [Native Container Apps agent pattern](https://learn.microsoft.com/en-us/azure/container-apps/tutorial-ci-cd-runners-jobs)
- [AzureRM job arguments and Key Vault reference behavior](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app_job)
- [Manual organisation creation](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/create-organization)
- [Parallel-job licensing](https://learn.microsoft.com/en-us/azure/devops/pipelines/licensing/concurrent-jobs)
