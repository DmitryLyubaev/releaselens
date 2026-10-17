# Pipeline Identity Failure Diagnosis Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline. Steps use checkbox syntax for tracking.

**Goal:** Make an early identity refusal diagnosable from its original artifact, then establish the rejected predicate before any fresh paid attempt.

**Architecture:** Preserve existing fail-closed identity checks and their public `identity` code. Add fixed, allowlisted check identifiers and a token-free failure artifact inside the directory already published by every stage. Exercise the actual Runtime constructor with synthetic AzureCLI task inputs. Actual cloud authentication remains a distinct pre-billing prerequisite; synthetic tests are not live acceptance.

**Tech Stack:** Python, pytest, existing AzureCLI@2 pipeline template.

**Spec:** docs/superpowers/specs/2026-10-15-azure-pipelines-search-delivery-design.md and the owner's instruction to find the fix and rerun after failed build 7.

## Global Constraints

- Frozen source 88e3e0e and all p8-1017d originals stay unchanged.
- No access token, assertion, System.AccessToken, credential, raw cloud error or exception text enters the published artifact or console.
- Existing identity, exact-source, checks, reservation, timing and scope checks retain their meaning.
- No paid replay, corrective apply, new model call, privilege expansion or publication is implied by local implementation.
- Missing original evidence is not retroactively replaced by a synthetic reproduction.
- Owner deleted live-status.txt; do not recreate it.

## Review Focus

- Authentication failures before Runtime assignment must still publish a diagnostic.
- Unexpected exceptions containing secrets must produce only a fixed internal code.
- Diagnostics must not weaken rejection of wrong tenant, principal, issuer, subject, connection or refresh endpoint.
- A successful guard-only invocation must remain free of authentication or cloud calls.
- Distinguish an identified failure boundary from proof of the historical failure's root cause.

### Task 1: Preserve a safe early-failure artifact

**Files:**
- Modify: scripts/pipelines/run.py
- Modify: scripts/pipelines/identity.py
- Create: tests/infra/test_pipelines_identity_diagnostics.py

**Interfaces:**
- Consumes: DeliveryRefused, existing verify_principal/terraform_environment and Runtime construction.
- Produces: IdentityRefused with a fixed check identifier and p8-publish/failure.json containing only fixed codes and stage. The original pipeline run/artifact supplies source/run binding; unvalidated context is never echoed.

- [ ] Write tests for principal and refresh rejection diagnostics, early constructor failure publication, secret canaries, guard-only behavior and the real synthetic constructor boundary.
- [ ] Run the new tests; require assertion failures demonstrating missing behavior.
- [ ] Add minimal fixed-code diagnostics and safe failure publication without changing successful behavior.
- [ ] Run targeted and complete infrastructure/eval checks, workflow checks and an independent review.
- [ ] Commit the reviewed change locally; publication requires the separate concrete decision in the runbook.

### Task 2: Obtain the missing actual authentication evidence before billing

**Files:**
- Private preparation packet for a fresh non-billable identity/authentication diagnostic, never the stopped d attempt.

**Interfaces:**
- Consumes: Reviewed safe diagnostics and a source-bound actual Azure DevOps job context.
- Produces: Original, token-free authentication check results for all three exact identities, with no Search/Container Apps deployment or model call.

- [ ] Determine available personal hosted/local execution without starting an agent or queueing a job.
- [ ] Prepare the smallest exact identity and diagnostic-only plan, including any zero-cost roles or local agent lifecycle; preserve separate approval boundaries.
- [ ] Review and execute only the concrete authorized non-billable diagnostic.
- [ ] Reproduce and fix the actual rejected predicate with a failing regression test. Do not guess from a generic identity refusal.
- [ ] Only after actual authentication passes, prepare and review the complete fresh paid execution and unattended closeout packet.

**Current evidence gap:** Original build 7 authenticated successfully in AzureCLI@2, then returned generic identity refusal before identity.json. The original ZIP is empty. Neither a guessed URL mismatch nor credential-free passing tests prove the historical predicate.
