# Project 6: Webapp evidence

Acceptance and session cleanup completed on **14 October 2026**, with explicit owner
approval. All seven check records passed, including both W6 halves. This is a combined
acceptance record across approved sessions, not seven newly measured checks on one commit.
Earlier failed workflows and missing measurements remain failed or not run.

## Results and source binding

Source **A** is `7985dbe16f4450a4bf543debba9bc88d70a94f5e`.
Source **B** is `89aa03be11a16d7a7d9430279b36411967b57275`.
Every measurement below is dated 14 October 2026; times are UTC.

| Check | Verdict | Evidence | Source | Measured at UTC | Observed result |
|---|---|---|---|---|---|
| W1 owner identity, answer and Caller metric | pass | live, original | A | 00:37:01.874124 | One ask returned 200 with a nonempty answer; one owner metric row matched; one gateway call. |
| W2 access and separately bound offline edges | pass | mixed, new | B | 08:05:41.658777 | Anonymous main/staging and forged headers returned 401. Deployment health returned 200; deployment ask/index/session returned 403. Source-bound offline edges passed; 12 requests, zero gateway calls. |
| W3 expired-token refresh and one answer | pass | live, original | A | 01:32:59.570624 | Initial lifetime elapsed; one refresh returned 200 and advanced expiry; one subsequent ask returned 200 with a nonempty answer. |
| W4 approved normal promotion | pass | workflow, new | B | 08:23:11.785552 | Baseline/staging health and approval passed; one swap succeeded, main selected the candidate, staging selected the baseline, and slot labels remained correct. |
| W5 unhealthy stage and rejected promotion | pass | workflow, new | B | 08:46:20.169368 | The reviewed fixture returned 503 with its version marker; main remained healthy and unchanged. Fixture stage failed, promotion was rejected at preflight, and protected release was skipped. Zero additional swaps or gateway calls. |
| W6a retained settings and fresh slot sessions | pass | mixed, new | B | 08:37:32.384344 | Intended settings before promotion and current retained settings matched; separate fresh post-swap main/staging owner sessions worked. Zero gateway calls. |
| W6b teardown and bootstrap preservation | pass | live, new | B | 08:55:55.922992 | Canonical session groups, plan/site/slot and deleted-service entry were absent; session states were empty and complete retained bootstrap ARM resource inventories were unchanged. |

**W6: pass**, because W6a and W6b both passed. W2 and W6a combine live facts with
source-bound CI or workflow evidence. W4/W5 use original workflow archives whose digests
and run/attempt/source bindings were verified, not later health reads.

### Approved reuse of original W1/W3

The owner explicitly approved reuse after verifying that application, authentication,
infrastructure and workflow files were unchanged between A and B. Changes were confined
to the reviewed release/diagnostic harness and its tests. Original check bytes, sources
and times were retained and regraded against the fixed conjunctions.

| Original check | SHA-256 of original check file |
|---|---|
| W1 | `a231e1e04283864c406996232d5f7648bc3d1a7fd6b5b01535d1be66aac45009` |
| W3 | `593e484c710ff42ff0547607c896951566acb5c55a39c8317e28ddfe054af872` |

W3's initial expiry was 01:32:52 UTC and refreshed expiry was 02:40:12 UTC. No new W1/W3
paid measurement or platform refresh occurred in the final session. The harness still
refuses cross-commit result sets: source-B results were rendered separately, and this
publication identifies the approved source-A prerequisites without relabeling them.

## Final workflow evidence

All five runs below are attempt 1 at source B, on the protected main workflow.

| Workflow | Original outcome | Meaning |
|---|---|---|
| [Seed 38530473185](https://github.com/DmitryLyubaev/releaselens/actions/runs/38530473185) | success | Initial package deployment and release gates passed. |
| [Normal stage 38531454833](https://github.com/DmitryLyubaev/releaselens/actions/runs/38531454833) | success | Candidate package, digest, health and configuration gates passed. |
| [Normal promotion 38533375218](https://github.com/DmitryLyubaev/releaselens/actions/runs/38533375218) | success | The one authorized normal swap and post-swap checks passed. |
| [Fixture stage 38536532259](https://github.com/DmitryLyubaev/releaselens/actions/runs/38536532259) | failure, expected | The unhealthy fixture was positively observed, with main unchanged and no swap. |
| [Fixture promotion 38537179011](https://github.com/DmitryLyubaev/releaselens/actions/runs/38537179011) | failure, expected | Fixture eligibility failed at preflight; release was skipped and never approved. |

The two expected failures are W5's positive gate evidence. They remain failed workflows.

## Earlier failures and missing results

The final success does not erase these outcomes. Private session ledgers retain the
complete originals, including additional diagnostic attempts.

| Earlier attempt | Preserved finding |
|---|---|
| [Morning promotion 38468904936](https://github.com/DmitryLyubaev/releaselens/actions/runs/38468904936) | Original promotion failed after a swap. Later reads or fixes do not credit it as W4. |
| Midday source-A continuation | W1-W4 passed, but W6a/W5 were not run before its owner-input deadline. Those missing results remain not run in that session. |
| [Later source-A stage 38515512950](https://github.com/DmitryLyubaev/releaselens/actions/runs/38515512950) | Read timeout before any upload; original diagnostic phase was `readiness`. W2/W4/W6a/W5 remained not run in that session. |
| [Source after PR #53, seed 38522858655](https://github.com/DmitryLyubaev/releaselens/actions/runs/38522858655) | `package_presence` read timeout before any upload, with zero swaps/model calls. Bounded read retries alone did not resolve the live failure. |
| Final session's first owner console | No W6a result was produced. Its reservation/context refusal was retained; a separately owner-authorized settings-only continuation supplied confirmed fresh separate sessions and produced the W6a pass above. |

[PR #53](https://github.com/DmitryLyubaev/releaselens/pull/53) bounded read retries and
corrected the diagnostic phase. [PR #54](https://github.com/DmitryLyubaev/releaselens/pull/54)
added authenticated SCM warmup and worker affinity before package reads/deployment, with
closed diagnostics and unchanged upload/swap replay prohibitions. Both merged after
review and green CI. Source B's actual seed/stage succeeded with that implementation.
This supports the fix's effectiveness in these runs; it does not prove Azure's underlying
cause for every earlier timeout.

## Session limits and verified cleanup

Final gateway apply began at **17:27:36.717159 Brisbane**. Verified closeout was
**18:57:44.606596 Brisbane**, within four hours and before the 20:00 cleanup deadline.
The final session created 22 gateway and 12 webapp resources, made zero new model calls or
platform refreshes, and performed one normal swap. Negative checks performed zero swaps.
Four paid questions were used across the earlier morning/midday sessions that day; none
were added in the final session. Actual invoiced charges were not measured here.

Post-apply plans showed two gateway policy normalization candidates and two webapp site
normalization candidates. Canonical gateway policies and actual intended site/auth/settings
were compared and passed before release; no corrective resource apply was performed.
This is not evidence that every future Terraform plan will be empty.

Both final stacks were removed using exact owned identities. Canonical absence and empty
states were verified, including the owned soft-deleted API Management reference. No
additional deleted-service purge command was needed. Complete retained bootstrap ARM
resource-ID inventories matched the pre-apply baseline; other stacks were outside cleanup
scope. Original reviewer settings were restored with branch, wait and admin protections
preserved. The exact owned keep-awake helper stopped after verification; its cleanup
monitor was deleted.

## Limits of this evidence

- W1 covers one owner ask and one matching metric row; W3 covers one expiry/refresh cycle
  and one subsequent ask. This is not a latency, load, availability or statistical study.
- Both slots share one front-end registration and managed identity. Separate fresh sessions
  do not demonstrate isolation between different registrations.
- W5 covers the reviewed unhealthy fixture and fixture-ineligible promotion refusal, not
  every deployment or startup failure.
- Publication contains labels, counts, Booleans, source hashes, times and public workflow
  references. Account values, tokens/cookies, answers, raw cloud output, state and private
  controller artifacts remain unpublished. Hosted artifact retention is finite.

Use the [runbook](runbook-webapp.md) for a separately authorized future session. This record
authorizes no new deployment, model call, swap or teardown.
