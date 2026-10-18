# Search upload retry implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Remove avoidable upload encoding work, expose safe upload refusal reasons, and accept fixed ARM grammar casing during owned cleanup.

**Architecture:** Encode each document once and assemble bounded JSON batches from those bytes. Keep the existing upload reservation, exact response validation, request cap and deadline. Compare ARM grammar case separately from exact resource names, subscription and Terraform addresses.

**Tech stack:** Python, NumPy, httpx, pytest, Terraform.

**Spec:** ../specs/2026-10-15-azure-pipelines-search-delivery-design.md

## Global constraints

- Frozen 41,825 documents, 229 questions and floor 120/229; no model or embedding calls.
- At most 500 documents and 12 MiB per batch, 200 requests per index, no retries.
- Keep the 240-second upload limit and existing stage deadlines.
- Preserve closed retry1 evidence and immutable original plans.
- Only explicitly owned resources may be deleted, using fresh delete-only plans.
- Existing human fix-and-retry authorization covers continued execution; a fresh credential still requires owner handoff.

## Review focus

- Multibyte content and exact byte boundaries must preserve valid JSON and every document.
- A slow final response must not evade the upload deadline.
- Partial, duplicate or wrong response keys must refuse without replay.
- ARM path comparison must reject changed names, subscription, provider or Terraform address.
- Failure artifacts must not expose remote response bodies, credentials or corpus text.

### Task 1: Bounded upload encoding and diagnostics

Files: eval/app/delivery_search/client.py, scripts/pipelines/run.py, eval/tests/delivery_search/test_client.py, tests/infra/test_pipelines_identity_diagnostics.py.

Interface: document_batches(documents) yields (keys, payload) with each document encoded once. UploadRefused retains public code upload and a fixed allowlisted check.

- [ ] Profile exact old uploader with frozen inputs and mock HTTP only, recording encoding and validation time.
- [ ] Add failing tests for exact UTF-8 payload, batch boundaries, oversized documents, and safe refusal diagnostics.
- [ ] Implement single encoding, preserving vector numeric values and response checks.
- [ ] Add and run deadline boundary tests, including the final response.
- [ ] Run delivery, retrieval and pipeline regression suites; compare full-corpus old/new offline profiles.

### Task 2: Cleanup ownership identity comparison

Files: scripts/pipelines/cleanup.py, tests/infra/test_pipelines_cleanup.py.

Interface: same_resource_id(actual, pinned) accepts fixed ARM grammar casing only; all other identifiers retain exact equality.

- [ ] Add failing tests for mixed-case identity group, identity and federated-credential paths in cleanup execution and delete validation.
- [ ] Add negative cases for changed names, subscriptions, providers, types and Terraform addresses.
- [ ] Implement comparison in initial ownership validation, drift and delete-plan checks. Preserve raw originals and saved-plan hashes.
- [ ] Run cleanup and owner suites, then full project suites and independent review.

### Task 3: Same-scope live retry

- [ ] Commit and publish reviewed source, verify CI and exact-source image.
- [ ] Create a new attempt directory, bind new source/image/plans and fresh ownership while retaining retry1 closeout.
- [ ] Complete all prebilling guards and fresh PAT handoff before starting one finite clock.
- [ ] Execute the approved positive, negative and scheduled acceptance sequence once, restore settings and independently verify cleanup.
- [ ] Publish sanitized evidence only if all eight gates actually pass.
