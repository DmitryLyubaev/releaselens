# Project 8 Retry Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. The owner already chose Native and explicitly instructed fixing the issues and retrying. Use one whole-branch review after local verification.

**Goal:** Catch missing approval metadata and impossible execution budgets before billing, and close never-applied agents with genuine absence evidence.

**Architecture:** Shared readiness checks serve owner reservations and runtime guards. An explicit approved timing profile preserves the old contract. The owner cleanup adapter emits a narrow local-state absence original that the evidence grader independently validates.

**Tech Stack:** Existing Python, pytest, httpx, Terraform and Windows/WSL owner tools. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-17-pipelines-retry-readiness.md`.

## Global Constraints

- Preserve p8-1017b source, authority, originals and failures without amendments.
- No public push, new credential, identity, deployment, queue or paid model call in this implementation.
- Keep native setup/create/shutdown timeouts; do not reduce them to fit a window.
- Require actual authenticated absence, never a CLI error converted to success.
- Preserve the existing Search-only scope and owner approval boundaries.

## Review Focus

- An owner GUID differing from the sole pinned actual approver must fail before creation.
- An ARM 403, wrong group, or remote backend must not certify local absence.
- A state backup or apply reservation must prevent the never-applied shortcut.
- A changed timing profile must not be adopted into an existing started authority.
- A late browser acknowledgement must not rewrite pre-delete quiescence history.

### Task 1: Repair retry preflight and incomplete-launch cleanup

**Files:** Create `scripts/pipelines/readiness.py` and `tests/infra/test_pipelines_readiness.py`; modify `session.py`, `run.py`, `ownership.py`, `owner.py`, `evidence.py`, `watchdog.py`, the relevant infra tests and `docs/runbook-pipelines.md`.

**Interfaces:** `require_owner_id(authority)` rejects missing/non-GUID owner IDs. `require_owner_binding(authority, snapshot)` checks the actual pinned approver. `require_execution_window(authority, now, phase)` rejects insufficient complete remaining budgets. `require_local_state_absence(proof, component, authority)` validates narrow authenticated never-applied agent evidence.

- [x] Write regressions for a complete packed authority missing the owner field and a changed owner missing from the human scope digest; observe failures before implementation.
- [x] Write timing tests for the old insufficient window, exact provisioning boundaries and immutable profile adoption; observe failures.
- [x] Write owner-adapter/grader tests for never-applied agents with exact 404 and for all refusal cases in Review Focus; observe failures.
- [x] Implement the smallest shared checks, consumer guards and explicit profile support. Existing cleanup remains independent of execution-readiness checks.
- [x] Implement authenticated agent absence observation without state writes; extend only the matching grader branch.
- [x] Run `python -m pytest tests/infra -q` and `python scripts/check_workflows.py .github/workflows`; expect all tests and workflow checks to pass.
- [x] Commit the repair and record test evidence in the private task ledger.
- [x] Obtain one fresh whole-branch review, fix Critical/Important findings in one tested pass, and leave public publication approval as the final concrete gate.
