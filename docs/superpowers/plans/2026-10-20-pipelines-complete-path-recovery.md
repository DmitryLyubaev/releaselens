# Complete Pipeline Path Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task after approval. Independent review covers the complete execution path before tests and again after corrections.

**Goal:** Establish evidence that the entire acceptance and cleanup path is ready before requesting another live attempt.

**Architecture:** Preserve immutable attempt evidence. Test production controllers through an offline scripted transport using sanitized actual responses; separately label simulated and unobserved behavior. Compare operational configuration while allowing only explicitly identified server-managed changes at authorized Save boundaries.

**Tech Stack:** Existing Python controllers, pytest, Terraform saved-plan validation, Azure DevOps pipeline definitions and native Windows/WSL launchers.

**Spec:** Existing `docs/superpowers/specs/2026-10-17-pipelines-retry-readiness.md`, supplemented by the owner's instruction to review Save, enable, queue, verification, restoration and cleanup as one path before another run. This plan grants no execution authority.

## Global Constraints

- Archive nothing. Preserve every original, historical state, failed receipt and consumed intent.
- The owner approved implementation, local offline tests and Git/CI integration on20October2026. Image publication and live work still need separate approval.
- No automatic retry, old attempt reuse, clock reset, new PAT, Azure changes or pipeline queues.
- Keep existing ownership, protected checks, approval, source/digest bindings, freshness, resource/read/query limits and cleanup deadlines strict.
- Implement in an isolated development worktree. Never edit sealed historical helpers to repair an old attempt.
- The implementation owner makes changes; independent reviewers inspect bounded evidence and report findings. Avoid repeated overlapping full-context reviews.

## Review Focus

1. Server-generated metadata changes across Save versus real schedule/configuration drift.
2. Secret-masked GETs, omitted fields, defaults and transport shape versus actual configuration changes.
3. Cross-stage source, image, identity, state and artifact bindings after merge/publication.
4. Process ownership, ambiguous side effects, restarts and consumed intents across Windows/WSL.
5. Stop, timeout and partial failure from every stage through restoration and physical deletion.

## Task 1: Build one failure and transition ledger

**Files:** Create a recovery ledger alongside this plan; inspect `scripts/pipelines/`, `app/delivery_search/`, `tests/infra/test_pipelines_*.py`, pipeline YAML and preserved attempt helper sources.

- [ ] Inventory recorded failures from previous attempts and their fixes, original evidence and regression coverage. Do not infer causes from conversation summaries alone.
- [ ] Trace setup, static Save, variable Save, finalize, enable, queue, identity authentication, Reader removal, paid admission, provisioning, release, negative verification, genuine Schedule, restoration and cleanup.
- [ ] For each transition record its actual caller, inputs, mutation, expected response, next consumer, required review and failure cleanup owner.
- [ ] Mark each assertion as observed, offline-tested or unproven. Audit whether duplicated attempt helpers or excessive snapshot equality create recurring defects; propose consolidation only where the ledger demonstrates it.

**Exit:** No transition or previous recorded failure lacks an owner and evidence classification.

## Task 2: Repair the demonstrated comparison defect

**Files:** Inspect/modify `scripts/pipelines/diagnostic_preparation.py`, `scripts/pipelines/quiescence.py`; add a shared comparison module if needed. Trace restoration consumers in `scripts/pipelines/evidence.py` and future setup-helper generation. Add sanitized fixtures under `tests/infra/fixtures/` and tests in `test_pipelines_diagnostic_preparation.py` and `test_pipelines_quiescence.py`.

- [ ] Derive sanitized fixtures from p8-1019e actual revision64/revision65 responses; retain provenance hashes privately and exclude credentials/private transport from tracked fixtures.
- [ ] Write the reproduction before the fix. With approved offline execution, prove it fails for the recorded generated-ID difference.
- [ ] Introduce a pure, non-mutating projection excluding only `triggers[*].schedules[*].scheduleJobId` at the required authorized-Save comparisons. Preserve raw evidence hashes and strict pre-Save observations.
- [ ] Exercise finalize, enable/queue admission, wrapper rechecks and restoration. Keep schedule branch filters, time/day/timezone, counts, change policy, variables, identities, checks and permissions significant.

**Exit:** Recorded reproduction passes after the fix; operational mutations still fail. Regeneration at other stages remains a simulated case unless captured evidence demonstrates it.

## Task 3: Exercise the complete controller path offline

**Files:** Add an integration test module under `tests/infra/`; use production controllers and a scripted transport, never a replacement controller that merely restates expected behavior.

- [ ] Replay the complete sequence from preparation through terminal cleanup with actual fixtures wherever available.
- [ ] Add ledger-derived cases for defaults, masked variables, source/image mismatch, token expiry, identity continuity, late/mismatched artifacts, Schedule reason, verification failure and resource/read budget exhaustion.
- [ ] Inject a stop or failure at every mutating transition. Assert no later queue/apply occurs, no consumed action replays, and owned cleanup remains reachable within the original clock.
- [ ] Cover partial creation/deletion, absent never-applied state, uncertain command completion, lost browser access, stale heartbeat and PID reuse without claiming synthetic cases were observed live.

**Exit:** One coverage table links each transition and failure branch to test evidence or an explicit remaining live dependency.

## Task 4: Independently review the whole path

- [ ] Before running tests, a fresh reviewer traces the ledger, production code, helpers, fixtures, real ADO responses and cleanup paths; report concrete findings and missing evidence.
- [ ] Resolve findings and run only the approved offline suites. Record exact commands and results, including the original reproduction's before/after result.
- [ ] Independently review corrections and the final whole-path coverage table. A PASS must state remaining live-only uncertainties and cannot promise zero failures.

**Exit:** No unresolved critical/important findings; no known deterministic assumption is deferred to a paid run.

## Task 5: Integrate and bind the actual release

- [ ] Under approved Git/CI scope, commit, push, review and merge; verify required CI on the resulting main commit.
- [ ] Under publication authority, build/publish only if required and verify image source labels, digest, smoke results and pull access for that exact commit.
- [ ] Generate a fresh attempt packet from corrected sources. Review actual plans/helpers, artifacts, original clock rules, cleanup ownership and remaining live checks without reusing old authority.

**Exit:** One reviewed release candidate and one readiness report, each bound to actual hashes, with remaining human handoffs identified.

## Task 6: Request one separate live approval

- [ ] Present changes, test/review results, exact live scope/cost/deadlines and remaining uncertainties together.
- [ ] If any required server behavior remains unproven, propose a bounded configuration-only validation before paid provisioning, with explicit scope and restoration. Do not perform it silently.
- [ ] Start a fresh live attempt only after approval. Keep the existing stop-and-cleanup rule; do not automatically retry a failed phase.

**Exit:** Acceptance success requires actual release, negative and genuine scheduled verification plus independently verified cleanup. Offline tests alone do not satisfy acceptance.
