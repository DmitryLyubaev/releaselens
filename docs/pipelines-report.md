# Azure Pipelines search delivery: the run of 21 October 2026

Pipeline commit `a3c173d`. Release source `5642301c`, release manifest digest `21cc39b5…`. Every check passed:
a gated release to test and demo-prod, a negative run that went red with prod unchanged, and one genuine
scheduled run that verified prod read-only and tore the service down.

The gate was fixed in the [design](superpowers/specs/2026-10-20-pipelines-simplified-delivery-design.md)
before any live run: all 229 audited questions evaluated, at least 120 correct at top 1, no errors, and no
model or embedding call (cached query vectors, search only).

## Results

| Check | Run | Result |
|---|---|---|
| Identity preflight: test, prod and verify sign in by workload identity federation and refresh | 16 | pass, `code=identity-preflight-all` |
| Test gate on the test index | 22 | pass: 229 of 229 evaluated, **121 correct** (needs 120), 0 errors |
| Owner's approval of demo-prod | 22 | approved 73 s after it opened |
| Prod gate on the demo-prod index | 22 | pass: 229 evaluated, 121 correct, 0 errors |
| Negative candidate must fail the gate | 23 | red as required: `status=expected-failure code=quality`, 0 of 229 correct; promotion skipped |
| Prod unchanged by the negative run | 23 | 41,825 documents and the same schema hash before and after |
| Scheduled verify (reason `Schedule`, verify identity, read-only) | 24 | pass: 229 evaluated, 121 correct, 0 errors |
| Scheduled teardown of demo-prod, then test and the service | 24 | both `outcome: pass`, service `destroyed` |
| After the run | — | 0 search services and 0 resources in the search group; all three state files empty and unleased; platform plan "No changes" |

The release passed its gate by one question: 121 against a floor of 120. The questions are Project 2's
229 audited questions; the margin is reported as measured, not tuned.

## Timings (UTC, 21 October 2026)

| Run | Stage | Time |
|---|---|---|
| 22 release | BuildAndTest | 2 min 24 s |
| | CreateService (Basic search service) | 2 min 37 s |
| | PlanTest | 1 min 53 s |
| | TestIndex (41,825-row upload and gate) | 5 min 45 s |
| | PlanProd | 1 min 56 s |
| | Waiting for approval | 1 min 13 s |
| | ProdIndex | 6 min 1 s |
| 23 negative | NegativeGate | 1 min 55 s |
| 24 scheduled | Status | 2 min 6 s |
| | Verify | 2 min 43 s |
| | TeardownProd | 2 min 12 s |
| | TeardownTest (indexes and the service) | 2 min 12 s |

Release run 22 took 22 min 45 s from its start to prod. The service existed from 01:52 to 06:10, about 4 h 18 min,
because the verify-and-teardown run was the next scheduled one.

## Notes on the run

- **Two earlier releases stopped at the approval.** The demo-prod approval times out after 10 minutes. In
  run 18 the operator's watcher failed to report the wait; in run 20 nobody was at the screen. Each timed
  out, so `ProdIndex` was skipped, though Azure DevOps shows such a run as "succeeded". Each was followed by
  a manual teardown (runs 19 and 21, both `outcome: pass`) and a fresh release, as the runbook prescribes.
  Release 22 then had its approver ready.
- **The schedule moved.** The design scheduled the verify-and-teardown run for 23:30 UTC (09:30 Brisbane).
  Setup finished at about 09:00 Brisbane, so PR #82 moved it to 06:00 UTC to finish the same day. The
  09:30 run (run 17) still fired once before the change and found no release, as designed. This PR
  removes the schedule now that its one genuine run has happened; the template keeps the scheduled path.
- **The frozen release was rebuilt to the same digest.** The archive built during the earlier attempts was
  lost with a worktree before upload. Thirteen of its fourteen files survived unchanged in an earlier
  archive; `schema.json` had changed since, and regenerating it canonically from the code reproduced the
  manifest digest `21cc39b5…` exactly. The pipeline checks the digest, and its scorer and lock copies
  against `main`, at every consumer.
- **The pipeline was created through the existing GitHub App connection** (`az pipelines create
  --skip-first-run`), not the portal wizard, which asked for a broad OAuth grant.
- **Identities and grants.** Three user-assigned identities (test, prod, verify) with federated
  credentials and resource-group-scoped grants, and no subscription-level role. Azure DevOps verified
  each service connection on save regardless.

## Cost

AI Search Basic at about US$0.13 an hour: about 6.3 service-hours in all (41 min, 80 min and 4 h 18 min
for releases 18, 20 and 22), roughly US$0.82. Hosted jobs ran in the free grant. The platform stack (the
identities, the storage account and the empty search group) was destroyed after the run, once its
evidence had been copied and checked.
