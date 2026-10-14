# Project 7: Ephemeral Azure Pipelines agents

Status: approved by the owner on 15 October 2026. Design only; no organisation,
project, secret, infrastructure or pipeline has been created by this work.

## Intent and agreed scope

This is a CV and interview demonstration of queue-driven build agents in Azure Container
Apps Jobs. It should be small enough to explain and repeat: queue a real pipeline, observe
a fresh agent start, build/test real code, publish test results and observe the agent exit.
It is not a production agent platform or a migration of the full ReleaseLens pipeline.

The owner agreed to use ReleaseLens for the focused demonstration. They have no personal
Azure DevOps organisation yet and asked to include creating one. Code stays on GitHub;
Azure DevOps hosts the private project, pipeline and results. Project 8 remains responsible
for deployment stages, benchmark gates, environments and workload-identity deployment.

## Approved defaults selected by the designer

These choices were proposed by the designer and included in the owner's approval of the
complete design:

- Keep code and infrastructure in ReleaseLens; no second source repository.
- Use Linux AMD64 agents with the .NET SDK compatible with `global.json`.
- Build and test `tests/ReleaseLens.Core.Tests` and `tests/ReleaseLens.Web.Tests` individually.
  Their inspected dependencies use local fakes/in-process test hosting and do not require
  Docker. Do not build/test the entire solution on this agent or call real Azure backends.
- Build the agent image in GitHub Actions, where Docker is available. Publish a public
  GHCR image without secrets; use a verified immutable digest in Azure. Confirm anonymous
  image access before creating billable infrastructure. No local Docker installation or
  additional Azure registry is required for this demonstration.
- Use one dedicated agent pool and one manually queued pipeline on trusted `main`.
  Disable CI, PR, fork and scheduled triggers. Only the owner may queue this demo.
- Target one active agent for sequential demonstrations. Configure minimum executions 0,
  maximum executions 1, polling 30 seconds, one replica/completion per execution, no replica
  retry, and a 15-minute execution timeout. Default size: 2 vCPU and 4 GiB.
- Observe actual execution counts; do not describe a per-poll scaling setting or Azure
  DevOps' one parallel-job allowance as a proved global concurrency mutex. Unexpected
  duplicate workers stop further queuing and become a finding, not a hidden successful run.
- Perform two successful sequential runs to show different fresh agents, then one
  deliberately failed pipeline step to demonstrate failure reporting and agent termination.
  These are small CV examples, not load or availability measurements.

## Setup and human actions

The owner creates the Azure DevOps organisation manually in the web portal using their
personal Microsoft account and personal subscription. Microsoft currently requires an
active subscription and billing association for new organisations. Do not use the employer's
account, tenant, subscription or resources, buy extra parallel capacity, or invite users.
The actual organisation name and hosting geography are owner-entered private settings.

Create one private Azure DevOps project, a dedicated self-hosted pool and a manual YAML
pipeline connected to the existing GitHub repository. Enable access only to this pipeline,
not every pipeline in the organisation. Any GitHub app/connection consent is performed by
the owner. Keep setup in a short runbook; no custom organisation-provisioning platform.
One Azure Boards work item can track Project 7; elaborate Boards automation is out of scope.

The planned single-agent demo uses the self-hosted parallel-job allowance, subject to
verification in the newly created organisation. It does not require Microsoft-hosted Azure
Pipelines minutes. GitHub Actions builds the image separately. If the expected allowance or
required permissions are unavailable, record a setup blocker rather than purchasing capacity.

## Agent and queue flow

1. Publish the reviewed agent image, with no repository credentials or PAT baked into it.
2. Register the tutorial's offline placeholder agent in the dedicated pool. It advertises
   the same required capabilities; it is an offline registration, not a permanent worker.
3. The owner queues a manual main-branch build. KEDA's `azure-pipelines` rule monitors only
   the dedicated pool and starts an event-driven Container Apps Job execution.
4. A uniquely named agent registers, checks out the approved GitHub source, restores/builds
   the two selected test projects, runs their tests and publishes TRX results even on failure.
5. The agent uses its supported one-job mode and exits. Normal completion/cancellation
   attempts to unregister only that execution's agent. A timeout/crash may need narrowly
   scoped stale-registration cleanup; never delete another pool's or session's agents.
6. A second successful build uses a different execution/agent and a fresh workspace.
7. A controlled pipeline parameter adds a failing command after the ordinary tests. Azure
   DevOps must report that pipeline as failed; the agent must still terminate. A failed
   pipeline can have a normally completed container execution, so container exit status is
   not a substitute for the actual pipeline result.

The placeholder can be created by a short manual Container Apps job using the same image.
That setup execution is recorded separately from queue-triggered proof. An explicitly
started execution cannot satisfy the queue-scaling check. No Docker commands or container
jobs run inside the Azure agent.

## Infrastructure and secret boundary

Add a separate `infra/agents` session stack with its own state: resource group, Consumption
Container Apps environment, bounded log workspace, event-driven agent job, placeholder
setup job, Key Vault and a user-assigned identity with only required secret access. Use the
existing personal budget controls; do not modify existing application/bootstrap stacks or
recreate API Management, Search, Functions or App Service. Exact provider syntax and
resource count are builder decisions exposed by an offline plan before live approval.

Use one short-lived Azure DevOps PAT with Agent Pools read/manage scope for the dedicated
organisation, and pool permissions limited as far as Azure DevOps supports. Record its
actual effective scope honestly; PAT scope is not itself a pool-specific access boundary.
The owner creates it privately. Store it in Key Vault using an approved private input
procedure. Container Apps references it through Key Vault and managed identity.

Terraform manages the vault/identity/reference, not the PAT value. Do not read the value
through a Terraform secret data source, put it in state/tfvars, bake it into an image,
publish it as an artifact or print it in commands/logs. Private references/state stay ignored.
The runtime agent handles trusted build code because that execution can access its own
registration credentials. The owner alone controls job starts and configuration; granting
job-start permission can expose job secrets/identities through execution overrides.

Agent-pool authentication uses the documented PAT path for this CV demonstration. Other
generic KEDA authentication methods do not establish native Container Apps support by
themselves. Secret-free agent registration is not a required research project here.

## Acceptance and public evidence

| Check | Required observed result |
|---|---|
| A1 queue activation | With no active worker, an actually queued manual main build causes a new event-driven execution and a matching online agent. No manual job start credited as scaling. |
| A2 real build and tests | Selected Core/Web projects build; actual test results contain executed tests, zero failures/skips for the successful runs, and published TRX. An empty/skipped job is not a pass. |
| A3 fresh execution | Two successful sequential builds have different execution/agent identities and fresh workspaces, with no persistent worker reused. |
| A4 controlled failure | The deliberately failed build remains failed in Azure DevOps; its test results are retained and its worker terminates. It is expected negative evidence, not a successful build. |
| A5 return to idle | After the queue clears, no worker executions or ephemeral online agents remain. The one known offline placeholder is distinguished from running compute. Record the observed interval. |
| A6 session closeout | Owned Azure host resources are removed, demo queue is clear/disabled, PAT is revoked and unrelated resources are unchanged. Any incomplete action remains a finding. |

Measure queue-to-agent start, build/test duration, executed test counts, execution/agent
counts and return-to-idle time. Bind them to the source and image digest. Separate facts
from inference: agent timestamps alone do not prove queue-triggered startup, and a test
artifact's existence does not prove its tests passed. Keep originals privately and publish
only sanitized labels, counts, times, Booleans and approved source/image references.
No secrets, account/organisation URLs, Azure resource IDs, personal emails or raw logs go
into public documentation. Screenshots are optional and must be reviewed/redacted first.

The CV claim is a measured 0-to-1-to-0 agent demonstration with infrastructure as code,
secure PAT delivery and a real build/test workload. Do not claim production readiness,
multi-agent scale-out, zero total idle cost or a performance benchmark from these samples.
Include one short comparison with Managed DevOps Pools as a managed VM-agent alternative.

## Live session and cleanup

Finish implementation, offline checks, image availability and Azure DevOps setup before
starting the billable demo window. Publishing code/image, creating paid infrastructure and
running the demo require the owner's later explicit approval. This design approves none
of those outward actions. Refresh current prices and present the actual plan and cost cap
before apply; do not invent a price from the CPU limit or promise a one-day completion.

Proposed live window: 60 minutes including cleanup, with no new queued demonstrations
after 30 minutes and cleanup starting no later than 45 minutes. Agree actual wall-clock
times and the monetary ceiling before apply. One host apply is planned. A failed gate stops
further queuing and retains resources for diagnosis within that window, rather than deleting
and rebuilding automatically. No repeat apply or new paid attempt without further approval.

Before teardown, stop this demo's queuing/scaling and handle only its owned executions.
Destroy only the new session's Azure resources and verify absence. Revoke the PAT; keep
the private Azure DevOps organisation/project, pool/known offline placeholder, work item
and result history for Project 8. Do not delete the organisation or other existing resources.
Retain soft-delete protection; no Key Vault purge is implicitly authorized. Document any
retained soft-deleted reference and do not confuse it with running billable agents.

## Builder decisions and next gate

The builder selects pinned compatible agent/base-image versions and digests, startup
script details, exact Terraform resources/API versions, private setting names, capability
discovery, evidence collection commands and small offline tests within these constraints.
They may not expand the demo to full CI/deployment, add secrets/scopes, relax pass rules,
raise cost/concurrency/time limits, introduce a Docker daemon or use employer resources.

After builder review and full owner approval of this design, write the implementation plan.
Do not scaffold, install dependencies, create cloud/DevOps resources or implement beforehand.

## Primary references checked 15 October 2026

- [Container Apps agent tutorial](https://learn.microsoft.com/en-us/azure/container-apps/tutorial-ci-cd-runners-jobs)
- [Organisation creation and manual setup](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/create-organization)
- [Self-hosted parallel-job allowance](https://learn.microsoft.com/en-us/azure/devops/pipelines/licensing/concurrent-jobs)
- [Agent one-job mode](https://learn.microsoft.com/en-us/azure/devops/pipelines/agents/docker)
- [Container Apps job settings and job-start permissions](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
- [Job Key Vault references](https://learn.microsoft.com/en-us/cli/azure/containerapp/job/secret)
- [KEDA Azure Pipelines scaler](https://keda.sh/docs/2.21/scalers/azure-pipelines/)
