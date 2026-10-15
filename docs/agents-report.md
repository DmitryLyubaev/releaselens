# Short-lived build agents: measured acceptance

Measured on **15 October 2026**, Australia East. All six acceptance checks passed in
one fresh session: two positive builds and one controlled negative. This is a CV and
interview demonstration of queue-driven agents, infrastructure as code and cleanup.

## Source and image provenance

- Workload and grader source: `c063829792f5f80f0f206dd3afb52ad7be46ff8f`.
- Reviewed source tree: `500a1f35d7801e24f28a6374a2ee89ab0ee299ed`.
- Immutable Linux/amd64 agent image:
  `ghcr.io/dmitrylyubaev/releaselens-agent@sha256:3167931608ce2390fdc565d737074c9aaa57098f2099aabffe9f2fc431d94c82`.
- The image was originally built from `32d87968c540faa413c1ebd94e26caf05ca0fa5f`.
  Its manifest/config digest and anonymous pull were verified; the relevant image and
  publication inputs were unchanged at the measured workload source. The image is
  not relabelled as a new build from that later source.

The [allowlisted grader output](agents-results-2026-10-15.json) contains the exact
unrounded results. Private originals retain build/timeline/artifact JSON, original ZIPs,
workload manifests, digest-verified TRX files, agent/execution observations and cleanup
inventories. The published offline grader passed again against those originals before
this report was prepared. Raw originals and account/resource identifiers remain private.

## What ran

A manual main-branch pipeline queues a Docker-free .NET Core/Web build and test workload.
The Azure Pipelines scaler starts a Container Apps event job, configured for zero minimum
and one maximum execution, with 2 CPU and 4 GiB per worker. Each execution registers a
fresh agent and workspace. A separate setup job provides an offline placeholder; it is
not a running worker. The agent PAT is delivered through Key Vault and managed identity.

| Sample | Pipeline result | Queue to build start, s | Build duration, s | Build/test task, s | Executed tests | First observed idle after build, s |
|---|---|---:|---:|---:|---:|---:|
| Positive 1 | Succeeded | 74.18 | 88.61 | 29.27 | 154 | 18.36 |
| Positive 2 | Succeeded | 110.61 | 62.81 | 28.81 | 154 | 23.36 |
| Controlled negative | Failed as intended | 82.60 | 67.22 | 29.02 | 154 | 7.99 |

Each sample executed all 154 ordinary tests successfully, with no skips. The negative
pipeline failed only at its separate intentional failure step; its test and artifact
publication steps succeeded. That original failed pipeline result remains failed.

Times come from original pipeline/task timestamps and sampled worker/pool observations.
The grader's queue-to-agent metric uses build start minus queue time, rather than a
separate agent-registration timestamp. Build duration includes pipeline overhead;
the build/test task is a subset. Idle timing ends at the first qualifying observation,
not an exact shutdown instant. Each run retained three idle observations at least
30 seconds apart before the next queue. Pool observations were collected about every
5 seconds and worker observations about every 15 seconds, plus request time.

## Acceptance and teardown

| Check | Result | Actual evidence |
|---|---|---|
| A1: queue-driven start | Pass | Event-created execution, matching online agent and pinned image; sampled worker count never exceeded one; no manual worker-start operation in the retained Activity Log. |
| A2: real workload | Pass | Original main/source-bound builds, workload manifests, successful build/test tasks and both digest-verified TRX files per run. |
| A3: fresh repeated execution | Pass | Distinct agent, execution, workspace and build identities, with idle proof between queues. |
| A4: controlled failure | Pass | Ordinary tests succeeded, the intentional step failed, and the original third pipeline result was failed. |
| A5: return to idle | Pass | Three spaced observations per run showed no active worker, online agent or unfinished queue request; the placeholder remained offline. |
| A6: owned cleanup | Pass | Canonical group absence, successful empty Terraform state, unchanged complete retained ARM inventories, exclusive cleanup, disabled/empty queue, restored original pipeline variables and owner-confirmed PAT revocation. |

All times below are Brisbane, UTC+10. The one create-only apply began at **17:47:48**
and passed at **18:04:49**. After actual A1-A5 passed, approved early cleanup began at
**18:23:35**. Physical removal of the nine manifest-owned ARM identities was verified at
**18:50:02**; full evidence closeout passed at **18:51:37**, within the prospectively
approved 90-minute limit ending at 19:17:48. The queue cutoff was apply+45 minutes and
latest permitted cleanup start was apply+50 minutes; neither bound was extended.

The owned keep-awake supervisor and observer exited, and the private evidence inbox was
closed. Retained bootstrap/other resource inventories and protected pre-existing
soft-deleted-vault metadata were unchanged. No Key Vault purge or additional paid model
call occurred. The private DevOps project, pool and history remain for Project 8.

## Earlier attempts and limits

Earlier failed and not-run outcomes remain separate from this successful session:

| Earlier source | Outcome retained |
|---|---|
| `32d8796` | Apply failed because the logging destination needed an explicit Log Analytics value. A1-A5 were not run; original A6 failed. Physical cleanup was completed. |
| `56d9666` | The scaler rejected a trailing slash in the organisation URL; its queued build was canceled. A1/A2/A5 failed, A3/A4 were not run, and A6 failed its timing bound despite later physical cleanup. |
| `c518a9e` | One positive build passed A1/A2/A5, but artifact-download delay left A3/A4 not run. Original A6 failed a case-sensitive ARM identity comparison. A later, separately labelled offline regrade passed A6 after the comparison fix; it did not replace the original failure or supply missing live runs. |

The successful session also retained a pre-apply watchdog identity-guard refusal caused
by a Windows launch-path spelling mismatch. It was resolved before any apply intent or
Terraform execution, with unchanged reviewed helper code. It was not an apply replay.

Sample size is two positive builds and one expected negative on one small workload.
This demonstrates measured 0-to-1-to-0 workers, not multi-agent scale-out, production
readiness, benchmark-quality performance or zero total idle cost. Environment, logging
and secret-storage costs remain distinct from worker compute; no final invoice total
was measured. Managed DevOps Pools are a managed VM-agent alternative with a different
tool/runtime footprint; no quantitative comparison was measured here.
