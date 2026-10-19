# Pipeline recovery: evidence, coverage and remaining gates

Implementation base: `e71ace8705bd05f111e41abda9fa9af0c3f16917`. Owner approved repairs, local offline tests and Git/CI on 20 October 2026. No live Azure actions or image publication are authorized. Preserve all historical evidence, worktrees and this ledger.

## Failure inventory checked against original files

| Attempt / original | Established finding | Coverage / disposition |
|---|---|---|
| p8-1018e `final-closeout.json`, `cutoff-handoff-failure.json` | Acceptance incomplete; timing/ordering failures preserved; cutoff handoff reported `reserved`. Do not infer a single original trigger from these closeout records. | Existing session cutoff, cleanup continuation and quiescence tests; original closeout remains failed. |
| p8-1019a `full-closeout-verification.json`, `promotion-validation-refusal.json` | Promotion validator expected absent state although saved create-only plan bound initialized serial1. | Existing `test_pipelines_pending_target.py` covers initialized empty state, changed state and exact target. |
| p8-1019c `closeout.json`, `setup-stop.json` | Five-minute submission window started before independent review and browser configuration; expired before submission. | Staged diagnostic preparation starts the short window after final Save; slow static/Save/deadline tests in `test_pipelines_diagnostic_preparation.py`. |
| p8-1019d `startup-actual-operation-failure.json`, `startup-freeze-failure.command.private.json` | Startup ownership gate failed before provisioning. The subsequent tracked fix admits a verified direct launcher child. | `test_pipelines_startup.py`: real Windows venv launch, exact process chain, reuse and stale/slow heartbeat cases. |
| p8-1019e `diagnostic-finalize-cause.json`, independent rev64/rev65 GET originals | Only remaining normalized difference was generated `scheduleJobId`; variables and all other operational fields matched. | New captured-fragment Save regression plus all operational mutation controls. Historical attempt stays failed. |
| Independent recovery audit | Paid native query could outlast heartbeat freshness; dispatch reservation retained a caller timestamp across slow checks. These were statically reachable, not observed live. | New post-query heartbeat, slow source/grade/lock, final browser handoff and cancellation regressions. |

Private originals remain exclusively in their original `.local/project8` attempt directories under the existing count-readiness, handoff-repair, acceptance-preflight and diagnostic-preparation worktrees. Sanitized fixture provenance records exact hashes; private originals are not copied into Git.

## Full transition and failure coverage

Observed means captured from an actual attempt, not successful acceptance. Offline means executable local contract coverage. Unproven means a future approved live observation is still required.

| Transition / consumer | Inputs and mutation | Evidence / failure path |
|---|---|---|
| Native keeper startup / `startup` | Clock hash, exact launcher and interpreter identities, heartbeat; read-only ownership admission | Observed working in e; Windows behavioral CI plus stale/slow/reused-PID negatives. Existing keeper owns cleanup on actual stop. |
| Identity/Reader setup / owner setup helpers | Independently reviewed exact saved plans; actual outputs, source and state hashes | e observed seven plus three creates/deletes. Existing plans, ownership, ARM-ID grammar and cleanup tests. Future exact helpers/plans still require actual review. |
| Static definition/client Save / `diagnostic_preparation.stage` | Original full14 readbacks, explicit defaults amendment, disabled schedule and three clients | Observed e; pre-Save schedule IDs remain strict. No authority yet. Failure triggers prepaid keeper rollback. |
| Variable Save / `draft`, `finalize` | One packed authority, secret-masked GET, exact Save witness, short nonrenewable window | Observed e rev64/65 shapes; new reproduction passes only after generated-ID projection. Flags, variables and operational schedule changes still refuse. |
| Enable / queue / `admit`, wrapper recheck | Exact packet/review hashes, fresh complete readback, one consumed intent each | Offline Save chain, slow deadlines, stop injection at every preflight phase and uncertain-submit no replay. Real later Save behavior remains unproven. |
| Three-role authentication / `identity_preflight`, `identity` | Exact job source/reason, principal claims and refresh endpoint; no model calls | Existing real MockTransport refresh/context tests; composed replay validates three principals. Actual future federation and refreshed tokens remain unproven. |
| Reader removal / setup restoration adapter | Fresh full14 restoration, approved new client continuity, exact owned Reader delete plan/state | e rollback removal observed; continuing-path physical removal is not proved by composed synthetic transport. Future helper calls tracked adapter and requires actual result before paid handoff. |
| Paid adoption / `paid_admission`, `startup.observe_keeper` | Exact authority, one paid intent, same-process ack, setup clock; native query then fresh heartbeat | New real-file/hash tests cover changed review/authority/process, slow query and cleanup during inspection. No paid clock is created by these validators. |
| Foundation/agents / `ownership`, `readiness`, owner driver | Approved source-bound plans, finite phase budgets and exact resource policy | Prior actual creates exist in e's predecessor; current exact plans and image are not transferable. Existing reservation/plan/ownership tests plus composed transport stops after both creates. |
| Release / `session.reserve_dispatch`, `Runtime` | Review/read/query budgets; exact saved plan, state, lock, bundle and source | Composed replay runs actual plan/apply binding, receipt production and 229-query scoring for test/prod. Terraform, ADO, ARM and bulk upload are scripted boundaries. Existing unit tests exercise bulk upload, denied count, timeout, distinct-agent artifacts and changed bindings. |
| Negative verification / `Runtime` | One reservation, prior production gate, zero-hit query filter | Composed replay executes actual prior-gate files, 229 queries and expected-negative receipt. Incorrect nonempty hits covered separately. |
| Genuine Schedule / registration, runtime and evidence grading | Source/main/reason, prior negative gate, verification identity and own-plan checks | Composed replay registers reason `schedule` and runs 229-query verify with no apply; existing run-check tests validate its own check evaluations. No offline test proves Azure cron firing. |
| Restoration / `quiescence`, `definition.require_setup_restored` | Original definition/clients/checks/permissions, Disabled and empty queue | New Save projection is shared; branch/time/options/other IDs stay significant. e actual full14+10 closeout is retained. |
| Physical cleanup / `cleanup`, owner driver | Original manifest, idle owner, fresh remaining delete-only plan, empty state and retained baseline | Composed replay reaches due cleanup after each injected phase failure, preserves original clock, and does not replay a completed cleanup. Existing tests cover partial delete timeout, remaining-plan continuation, missing state, denied absence and active provider. Real future deletion and exact deadlines remain unproven. |

The composed replay uses production controllers and real files/hashes/locks, saved-plan verification, scoring, receipt consumption and cleanup. It uses a smaller synthetic ARM inventory, not a simulation of all42 resources. Prepaid rollback has additional direct tests of the tracked `SetupRollback` controller with no paid authority or packet, seven identities/federations/group plus three Readers, locked ordering, due fallback, paid-transfer race refusal and consumed-intent refusal. The eight failure labels share a fully-created10-resource fixture and establish phase-bound stop provenance, not eight independently constructed interrupted setup histories. Its dispatch prerequisites delegate to the real runtime prior-gate validators; full original-registry/check-suite grading has separate existing tests. Neither synthetic outputs nor old attempts may be presented as new live evidence.

## Required future helper bindings

Untracked attempt helper bodies were another source of drift. Fresh generated helpers must use the versioned adapters in `scripts/pipelines/helper_templates/admission.py`:

- Setup rollback delegates to `prepaid_cleanup.SetupRollback(context).cleanup_all()` for abort, or `.cleanup("readers")` for approved continuation. The class owns its setup-operations lock; callers must not nest that same lock. Context transport must retain independently reviewed source/scope, command bounds, exact ARM statuses and immutable clock/evidence behavior. Paid transfer writers must hold `admission.setup_operations(context)` for short transfer/ack/first-paid-intent critical sections; never wait for another writer while holding the lock; rollback rechecks transfer markers again immediately before delete reservation and apply.
- Setup `verify_restoration(clients)` delegates to `admission.verify_restoration(context, clients)`.
- Paid `verify_paid_owner(raw, ...)` delegates to `admission.verify_paid_owner(context, raw, native_snapshot=bounded_native_query, ...)`.
- The diagnostic wrapper keeps `diagnostic_preparation._observation` after-Save behavior; initial staging explicitly uses strict pre-Save comparison.
- Immediately before each paid browser submission, call `admission.final_dispatch(context, mode, native_snapshot=bounded_native_query)` after the consumed dispatch/query reservations and every slow validation step.

The exact future caller files must be generated, inspected and behaviorally tested before their execution. Presence of the adapters alone does not prove that a future helper uses them. Never patch e's sealed wrappers or reuse its authority/clock/consumed intents.

## Integration and live-readiness gates

1. Independent whole-path review before tests: completed with three Important findings; each has a regression and implementation. Final corrected-code review and full offline results are required before integration.
2. Commit/push and PR CI are authorized. Inspect actual CI results, not just local test counts.
3. Main CI currently automatically publishes the API image. Because image publication remains excluded, do not merge until that side effect is explicitly approved. Do not silently change CI to bypass it.
4. Delivery-agent image publication, merged-source image labels/digest/pull and fresh release bundle bindings need explicit publication authority. The old e71 image cannot establish compatibility with a new commit.
5. Only then generate the exact new attempt packet/helpers/plans and independently review actual hashes. No fresh live authority or billable clock may be invented during repair.
6. Present remaining live-only assumptions, cost/scope/time limits and human handoffs together for separate live approval. If configuration-only Azure validation is needed, scope and approve that before paid provisioning.

There is no guarantee of zero external failures. The deliverable is removal of known deterministic defects, explicit coverage, a reproducible readiness record and preserved stop/cleanup behavior. Acceptance remains NOT RUN until new actual release, negative, genuine scheduled verification and independent cleanup evidence exist.

## Local verification on the corrected sources

- Existing offline Linux dependency runtime: infrastructure suite1054passed,1skipped(the Windows-native lifecycle case),5subtests; evaluation suite1204passed. Both commands exit0.
- Native Windows Python3.14 startup suite:30passed, including the actual launcher/interpreter lifecycle.
- Commands: `python -m pytest -p offline_guard tests/infra -q`, `python -m pytest -p offline_guard eval/tests -q`, and native `python -m pytest tests/infra/test_pipelines_startup.py -q`. The local Linux plugin rejects external socket connections; local loopback and mocked transports remain available.
- Independent static review, including the added prepaid core and transfer-race correction, found no unresolved Critical/Important issue. Minor adapter-signature documentation was corrected. Review did not claim live readiness.
- GitHub CI uses Python3.14 for the complete suites and independently runs Windows startup, .NET and offline Terraform checks; its actual PR results must be checked before integration.
- No Azure mutation, PAT action, pipeline queue, live acceptance run or image publication occurred during this repair.
