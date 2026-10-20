# Project 8 Simplified Delivery Implementation Plan (plan 1, offline)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Project 8's session controller with a plain Azure Pipelines delivery on Microsoft-hosted
agents:
- a long-lived platform stack;
- a per-run search service;
- release, negative and scheduled verify-then-teardown runs.

Everything is built and tested offline, with no Azure calls.

**Architecture:** The delivery logic is kept as it is: `eval/app/delivery_search`, `plans.py`,
`identity.py` and the index stack. `run.py` loses its authority, clock and budget layers, and reads
plain settings from a private Azure DevOps variable group instead. New Terraform stacks:
- `platform`: long-lived identities, storage, an empty search group and role grants;
- `service`: the per-run Basic search service.

The pipeline YAML runs every job on `ubuntu-24.04`. About 2,900 lines of controller code and their
tests are removed from `main`.

**Tech Stack:**
- Python 3.12 (agents) and 3.14 (CI), pytest, httpx;
- Terraform 1.15.8 with azurerm 5.7.0 and azapi 2.8.0;
- Azure Pipelines YAML;
- the Azure CLI 2.81.0 (pinned venv).

**Spec:** `docs/superpowers/specs/2026-10-20-pipelines-simplified-delivery-design.md` (approved 20
October 2026). Read it first.

**Where:** `E:\Projects\ReleaseLens`, branch `feat/p8-simplified-delivery` (from `origin/main` at
`5642301`). Leave the untracked `HANDOVER-project-6.md` and `HANDOVER-project-8-claude-code-2026-10-20.md`
alone, and never `git add` them. Do not touch anything under `C:\Users\Dmitry.Lyubaev\.codex\`.

## Global Constraints

- **The gate is unchanged:** at least 120 of 229 correct at top 1, `evaluated == expected == 229`, and
  `errors == missing == skipped == 0`. The negative gate requires 0 correct, all 229 rows empty and
  all 229 evaluated.
- **Zero model or embedding calls.** The frozen bundle is reused:
  - its digest is the manifest SHA-256 (begins `21cc39b5`);
  - it is fetched from `inputs/releases/<digest>/release.tar.gz`;
  - its bundled `scorer.py`, `requirements.lock` and `provider.lock.hcl` must equal the repository's
    copies;
  - its `manifest.source_sha` is recorded, never compared with the running commit.
- **Pins:** Terraform `= 1.15.8`, azurerm `= 5.7.0`, azapi `= 2.8.0`, `-lockfile=readonly`. Agents
  use Python 3.12 and the hash-locked `build/delivery-agents/requirements.lock` and
  `cli-requirements.lock` (`pip install --require-hashes --only-binary=:all: --no-deps`).
- **Names:**
  - service connections `ReleaseLens-P8-Test`, `ReleaseLens-P8-Prod` and `ReleaseLens-P8-Verify`;
  - environments `ReleaseLens-P8-Test` and `ReleaseLens-P8-DemoProd`;
  - variable group `ReleaseLens-P8-Delivery`;
  - resource groups `rg-p8-platform` and `rg-p8-search`;
  - identities `id-p8-test`, `id-p8-prod` and `id-p8-verify`;
  - indexes `p8-test` and `p8-demo-prod`.
- **Schedule:** `cron: '30 23 * * *'` (09:30 Brisbane), branch `main`, `always: true`. Pool
  `vmImage: ubuntu-24.04` for every job.
- **Storage containers:** `inputs`, `test-state`, `prod-state`, `service-state` and `evidence`.
- **Keyless:** OIDC or Azure AD only. The search service has local auth off; storage has shared keys
  off; there are no secrets in YAML or the variable group.
- **No identifiers in any committed file:** no GUIDs except all-zero and placeholder ones, no
  hostnames except templates and `example`, no emails, no real resource suffixes.
  `tests/infra/test_public_docs_identifiers.py` must pass.
- **Commits:** the repository's own git identity, ending with
  the Claude co-author footer line the session gives. Do not push; the owner approves pushes.
- **No Azure, Azure DevOps or network calls in any test.** Use mocks, MockTransport and
  `terraform test` with mock providers.
- **Commands to run tests:**
  - from the repository root: `eval/.venv/Scripts/python.exe -m pytest tests/infra -q`;
  - from `eval/`: `.venv/Scripts/python.exe -m pytest -q`;
  - Terraform only in WSL, on a clean copy of the stack without `terraform.tfvars`:
    `terraform init -backend=false && terraform validate && terraform test`.

## Review Focus

1. **The schedule fires while the release still waits for approval.** The status must be
   `incomplete`, with no verify and no teardown, and the run ends green with "run incomplete:
   skipped". Owner: Task 4 (`test_status_incomplete_when_prod_receipt_missing`).
2. **A second release is queued while a search service still exists.** It must refuse before any
   create, plan or upload. Owner: Task 4 (`test_create_refuses_when_service_present`).
3. **Teardown runs when the service is already gone but an index state still lists the index.** The
   index state must be cleared with `terraform state rm`, and the run ends green. Owner: Task 4
   (`test_teardown_clears_index_state_when_service_absent`).
4. **The bad candidate unexpectedly passes the gate.** The negative run must still end red, and the
   prod stage must never apply. Owner: Task 2 (`test_negative_that_passes_gate_is_refused`), Task 5
   (`test_promote_negative_always_refuses`).
5. **Evidence from an earlier release run is already in the shared storage.** A new release must write
   under its own run namespace without a `reserved` collision. Negative and verify must check only
   `current.json`'s release. Owner: Task 3 (`test_run_keys_are_namespaced`), Task 2
   (`test_verify_refuses_receipt_from_other_release`).

---

### Task 1: Settings from the variable group

**Files:**
- Create: `scripts/pipelines/settings.py`
- Test: `tests/infra/test_pipelines_settings.py`

**Interfaces:**
- **Produces:**
  - `ROLES = ("test", "prod", "verify")`;
  - `@dataclass(frozen=True) class Settings` with fields `tenant_id: str`, `subscription_id: str`,
    `storage_account_name: str`, `search_group: str`, `search_service_name: str`,
    `release_digest: str` and `principals: dict[str, dict]`;
  - the properties `storage_host`, which is `f"{storage_account_name}.blob.core.windows.net"`, and
    `search_host`, which is `f"{search_service_name}.search.windows.net"`;
  - `load_settings(env: Mapping[str, str]) -> Settings`;
  - `role_for(stage: str, reason: str) -> str`.
- **The environment keys** come from the variable group. They are `P8_TENANT_ID`,
  `P8_SUBSCRIPTION_ID`, `P8_STORAGE_ACCOUNT`, `P8_SEARCH_GROUP`, `P8_SEARCH_SERVICE`,
  `P8_RELEASE_DIGEST` and `P8_PRINCIPALS_JSON`.
- **`P8_PRINCIPALS_JSON`** maps each role to `{client_id, object_id, issuer, subject,
  service_connection_id, oidc_request_origin, oidc_path_prefix}`. The tenant and subscription are
  added to each principal, so `identity.verify_principal` receives the keys it already expects.

- [ ] **Step 1: Write the failing tests**

```python
VALID = {"P8_TENANT_ID": "00000000-0000-0000-0000-000000000001", "P8_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000002",
         "P8_STORAGE_ACCOUNT": "p8a1b2c3", "P8_SEARCH_GROUP": "rg-p8-search", "P8_SEARCH_SERVICE": "p8-search-a1b2c3",
         "P8_RELEASE_DIGEST": "0"*64, "P8_PRINCIPALS_JSON": json.dumps({r: PRINCIPAL for r in ("test","prod","verify")})}

def test_load_settings_reads_every_field_and_derives_hosts():
    s = load_settings(VALID)
    assert s.storage_host == "p8a1b2c3.blob.core.windows.net" and s.search_host == "p8-search-a1b2c3.search.windows.net"
    assert s.principals["prod"]["tenant_id"] == VALID["P8_TENANT_ID"]

@pytest.mark.parametrize("key,value", [("P8_TENANT_ID","not-a-guid"), ("P8_STORAGE_ACCOUNT","Has_Upper"),
    ("P8_SEARCH_SERVICE","x"), ("P8_RELEASE_DIGEST","abc"), ("P8_PRINCIPALS_JSON", json.dumps({"test": {}}))])
def test_load_settings_refuses_malformed(key, value):
    with pytest.raises(DeliveryRefused): load_settings(VALID | {key: value})

def test_load_settings_refuses_missing_role():
    with pytest.raises(DeliveryRefused): load_settings(VALID | {"P8_PRINCIPALS_JSON": json.dumps({"test": PRINCIPAL})})

@pytest.mark.parametrize("stage,reason,role", [("build","Manual","test"), ("create-service","Manual","test"),
    ("plan-test","Manual","test"), ("test","Manual","test"),
    ("plan-prod","Manual","prod"), ("prod","Manual","prod"), ("negative","Manual","test"),
    ("promote-negative","Manual","prod"),
    ("status","Schedule","verify"), ("verify","Schedule","verify"), ("teardown-prod","Schedule","prod"),
    ("teardown-test","Schedule","test"), ("teardown-prod","Manual","prod")])
def test_role_for(stage, reason, role):
    assert role_for(stage, reason) == role

def test_role_for_refuses_verify_outside_schedule():
    with pytest.raises(DeliveryRefused): role_for("verify", "Manual")
```

`PRINCIPAL` is a module-level dict with placeholder GUIDs, the issuer
`https://<ado-issuer>/00000000-0000-0000-0000-000000000003`, the subject
`sc://example/ReleaseLens/ReleaseLens-P8-Test`, the origin `https://example.vstoken.visualstudio.com`
and the prefix `/`.

- [ ] **Step 2: Run them to verify they fail.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_settings.py -q`. Expected:
  `ModuleNotFoundError: scripts.pipelines.settings`.
- [ ] **Step 3: Implement `settings.py`.** Validation:
  - GUIDs match `[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}`;
  - the storage account matches `[a-z0-9]{3,24}`;
  - the search service matches `[a-z0-9][a-z0-9-]{1,58}[a-z0-9]`;
  - the digest matches `[0-9a-f]{64}`;
  - the group matches `rg-[a-z0-9-]{1,60}`;
  - every role has all seven principal keys, non-empty.

  Any failure raises `DeliveryRefused("settings")`. Add `"settings"` to the codes `DeliveryRefused`
  accepts in `eval/app/delivery_search/contracts.py` if it uses an allowlist.
- [ ] **Step 4: Run the tests until they pass** (same command; expected: all pass).
- [ ] **Step 5: Commit** with the message `feat(pipelines): settings from the delivery variable group`.

### Task 2: The simplified runtime (`run.py`)

**Files:**
- Modify: `scripts/pipelines/run.py` (rewrite context, Runtime init and main; keep plan, apply_and_load,
  measure, prior_gate, require_gate, negative_passes and require_result_files)
- Test: rewrite `tests/infra/test_pipelines_runtime.py`

**Interfaces:**
- **Consumes:** Task 1's `load_settings` and `role_for`; Task 3's
  `BlobStore(host, tokens, client)`, `run_key` and `read_pointer`/`write_pointer`.
- **Produces:**
  - `STAGES = ("build", "plan-test", "test", "plan-prod", "prod", "negative", "promote-negative", "verify")`;
  - `build` runs the offline pytest and verifies the bundle, then writes the pointer. It no longer
    plans: the search service doesn't exist yet. `plan-test` runs `plan("test")`, and `test` runs
    `apply_and_load("test")` and then `measure("test")`;
  - `main(argv) -> int`, with the CLI `python -m scripts.pipelines.run <stage> --receipt-dir <dir>
    [--guard-only]`;
  - exit codes: `0` pass, `17` expected quality failure (negative only), `2` refused;
  - `Runtime(settings, stage, receipt_dir, client)`;
  - `proof = {"source_sha": <BUILD_SOURCEVERSION>, "release_digest": settings.release_digest,
    "release_run_id": int}`.
- **`release_run_id`** is `BUILD_BUILDID` for the release stages `build`, `plan-test`, `test`,
  `plan-prod` and `prod`. Otherwise it comes from `read_pointer()["release_run_id"]`.
- **The release-wide checks** (`require_gate` and friends) compare `release_digest` and
  `release_run_id` only.

- [ ] **Step 1: Write the failing tests.** Keep and adapt the existing gate and negative tests. Add:

```python
def test_context_requires_main_and_settings(monkeypatch): ...      # BUILD_SOURCEBRANCH != refs/heads/main -> DeliveryRefused("authority")
def test_no_authority_or_cutoff_variables_are_read(monkeypatch): ...  # P8_AUTHORITY_* absent -> context() succeeds
def test_bundle_source_sha_may_differ_from_running_commit(tmp_path): ...  # manifest.source_sha != BUILD_SOURCEVERSION -> accepted, recorded in receipt
def test_bundle_copies_must_match_repository(tmp_path): ...       # changed bundled scorer.py -> DeliveryRefused("bundle")
def test_release_stage_writes_pointer_once_in_build(fake_store): ... # build stage -> write_pointer({"release_run_id": BUILD_BUILDID, "release_digest": ...})
def test_negative_that_passes_gate_is_refused(fake_search): ...    # 120+ correct with negative config -> exit 2, receipt acceptance_pass False, never 0
def test_negative_expected_failure_exits_17_and_records_prod_unchanged(fake_search): ...
                                                                  # receipt has prod_before == prod_after == {"document_count": 41825, "schema_sha256": ...}
def test_negative_refuses_when_prod_fingerprint_changes(fake_search): ...  # after != before -> exit 2
def test_promote_negative_always_refuses(): ...                     # main(["promote-negative", ...]) == 2 for any settings
def test_verify_refuses_receipt_from_other_release(fake_store): ...  # negative receipt release_run_id != pointer -> DeliveryRefused("gate")
def test_verify_requires_schedule_reason(monkeypatch): ...          # BUILD_REASON=Manual -> DeliveryRefused("authority")
```

`fake_store` and `fake_search` are in-memory fixtures in the test file: a dict-backed `BlobStore`
stand-in and an `httpx.MockTransport` answering index GET, count and search requests. No test opens
a real socket.

- [ ] **Step 2: Run them to verify they fail.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_runtime.py -q`. Expected: the
  new tests fail.
- [ ] **Step 3: Implement.**
  - **`context(stage)`:**
    - requires `BUILD_SOURCEBRANCH == refs/heads/main` and a clean `git diff --quiet HEAD`;
    - `role_for(stage, BUILD_REASON)`;
    - returns `(settings, principal)`.
  - **Remove** `StageBudget`, `checks`, `authority_transport`, `readiness`, `metadata`, the
    `queue_cutoff_at`/`apply_started_at`/attempt/pipeline-ID checks, `claim_release` and the
    `operations/*.json` reservation.
  - **Every evidence key** goes through `run_key(release_run_id, key)`.
  - **The negative stage:**
    1. `prior_gate("prod")`;
    2. take the prod fingerprint before: `document_count` of `p8-demo-prod`, plus the SHA-256 of
       the canonical index GET body;
    3. `measure("test", negative=True)`;
    4. take the prod fingerprint after;
    5. write the receipt with both fingerprints. A mismatch raises `DeliveryRefused("gate")`.
  - **`promote-negative`** raises `DeliveryRefused("authority")` before authentication.
  - **The workspace** is `Path.home()/"p8"/str(release_run_id)`.
- [ ] **Step 4: Run the tests until they pass.** Also run
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_plans.py tests/infra/test_pipelines_identity.py -q`
  (expected: they still pass).
- [ ] **Step 5: Commit** with the message
  `refactor(pipelines): run.py reads plain settings; negative proves prod unchanged`.

### Task 3: Storage without budgets, with run namespaces

**Files:**
- Modify: `scripts/pipelines/storage.py`
- Test: rewrite `tests/infra/test_pipelines_blob_collisions.py` as `tests/infra/test_pipelines_storage.py`

**Interfaces:**
- **Produces:**
  - `BlobStore(host, tokens, client)`, with no `metadata_policy`;
  - `get`, `json`, `put_once(container, key, data)` and `put_json(key, value)`, as today;
  - `state(target)` for `"test"`, `"demo-prod"` and `"service"`;
  - `run_key(release_run_id: int, key: str) -> str`, which returns `f"runs/{release_run_id}/{key}"`;
  - `write_pointer(value: dict) -> None`, which overwrites `evidence/current.json` with no
    `If-None-Match`, the only overwriting write;
  - `read_pointer() -> dict`.

- [ ] **Step 1: Write the failing tests:**
  - `test_run_keys_are_namespaced`: `run_key(41, "receipts/test.json") == "runs/41/receipts/test.json"`,
    and two run IDs never collide;
  - `test_put_once_refuses_existing_key`: a 409 `BlobAlreadyExists` gives `DeliveryRefused("reserved")`;
  - `test_pointer_overwrites_without_if_none_match`: the recorded request has no `If-None-Match`
    header;
  - `test_containers_allowlist_includes_service_state`;
  - `test_service_state_read_returns_none_on_404`;
  - `test_no_metadata_budget_api`: `not hasattr(BlobStore, "reserve_metadata")` and
    `not hasattr(BlobStore, "require_workload_open")`.
- [ ] **Step 2: Run them to verify they fail.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_storage.py -q`. Expected: they
  fail.
- [ ] **Step 3: Implement.** Remove `METADATA_*` and the budget methods. The state key for the
  `service` target is `service.tfstate` in `service-state`.
- [ ] **Step 4: Run the tests until they pass.** Delete `tests/infra/test_pipelines_blob_collisions.py`.
- [ ] **Step 5: Commit** with the message `refactor(pipelines): plain Blob store with per-run evidence namespaces`.

### Task 4: Service lifecycle, status and teardown

**Files:**
- Create: `scripts/pipelines/service.py`
- Test: `tests/infra/test_pipelines_service.py`

**Interfaces:**
- **Consumes:** Task 1 `Settings`, Task 3 `BlobStore`, and `identity.terraform_environment`.
- **Produces** (each a stage of `python -m scripts.pipelines.service <command> --receipt-dir <dir>`,
  with command names equal to Task 1's `role_for` stage names):
  - **`create-service`** (Test). Exits `2` if `service_present(...)` or `state("service")` is not
    `None`; otherwise it does Terraform init, plan and apply of `infra/pipelines/service`.
  - **`status`** (Verify). Prints `##vso[task.setvariable variable=P8Status;isOutput=true]<value>`,
    where the value is `absent`, `incomplete` or `ready`. It exits `0` in all three cases.
  - **`teardown-prod`** (Prod). Destroys the `p8-demo-prod` index state.
  - **`teardown-test`** (Test). Destroys the `p8-test` index state, then the service stack.
  - **`service_present(settings, run=subprocess.run) -> bool`** runs
    `az search service show --name <service> --resource-group <group>`. Exit 0 means present, and the
    error text `ResourceNotFound` means absent. Anything else raises `DeliveryRefused("request")`.
- **Status rules:**
  - `absent`: no service;
  - `ready`: the service is present, and `runs/<pointer.release_run_id>/receipts/prod.json` passes
    `require_gate`, and the negative receipt for that run has `acceptance_pass is True`;
  - `incomplete`: anything else.
- **Teardown rules:**
  - Each teardown is idempotent: an empty or absent state means nothing to do, and exit `0`.
  - If the service is absent and an index state still lists the index, run
    `terraform state rm azapi_data_plane_resource.index`. Never delete the state blob.

- [ ] **Step 1: Write the failing tests.** Use a fake `run` that records argv, and the `fake_store`
  from Task 2's test module, moved into `tests/infra/conftest.py` if shared:
  - `test_create_refuses_when_service_present`: no Terraform argv recorded, exit 2;
  - `test_create_refuses_when_service_state_not_empty`;
  - `test_create_runs_init_plan_apply_in_order`, with the backend container `service-state`, key
    `service.tfstate` and `-lockfile=readonly`;
  - `test_status_absent`, `test_status_ready` and
    `test_status_incomplete_when_prod_receipt_missing`;
  - `test_status_incomplete_when_negative_missing`;
  - `test_teardown_prod_destroys_only_demo_prod`: the argv backend container is `prod-state`, with
    no `test-state` or `service-state`;
  - `test_teardown_test_destroys_index_then_service`, checking the order;
  - `test_teardown_is_noop_when_states_empty`;
  - `test_teardown_clears_index_state_when_service_absent`: `state rm` is recorded and no
    `destroy` is.
- [ ] **Step 2: Run them to verify they fail.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_service.py -q`. Expected:
  `ModuleNotFoundError`.
- [ ] **Step 3: Implement `service.py`.** Reuse `run.py`'s Terraform workspace pattern: copy the stack
  into `Path.home()/"p8"/"service"`, then init with `storage_account_name` and `container_name`.
  - The service stack's inputs are `search_service_name`, `resource_group_name`, `location`
    (`australiaeast`), `subscription_id` and `tenant_id`.
  - Destroying an index uses `terraform destroy -auto-approve` with the same inputs the index plan
    used: schema from the bundle, the target, `search_host`. The index stack needs the schema file
    path, so teardown fetches and verifies the bundle like `Runtime`. Use `fetch_bundle` and
    `settings.release_digest`.
- [ ] **Step 4: Run the tests until they pass.**
- [ ] **Step 5: Commit** with the message `feat(pipelines): per-run search service create, status and idempotent teardown`.

### Task 5: Terraform, the platform and service stacks

**Files:**
- Create: `infra/pipelines/platform/{versions.tf,backend.tf,variables.tf,main.tf,outputs.tf,.terraform.lock.hcl,tests/contracts.tftest.hcl}`
- Create: `infra/pipelines/service/{versions.tf,backend.tf,variables.tf,main.tf,outputs.tf,.terraform.lock.hcl,tests/contracts.tftest.hcl}`
- Delete: `infra/pipelines/identity/` and `infra/pipelines/foundation/`
- Modify: `.github/workflows/ci.yml` (the Terraform checks and the cache key list: replace `identity`
  and `foundation` with `platform` and `service`)

**Interfaces:**
- **Platform:**
  - backend `azurerm`, key `p8-platform.tfstate`, with `use_azuread_auth`. The storage account and
    container are given at init by the runbook: the bootstrap's state account, and the same state
    container the bootstrap uses (read `infra/bootstrap/state.tf` for its name);
  - variables `subscription_id`, `tenant_id`, `owner_object_id`, `location` (default `australiaeast`),
    `federation = map(object({issuer, subject}))`, with keys validated to exactly `test`, `prod` and
    `verify`;
  - outputs `storage_account_name`, `search_group`, `search_service_name` (`p8-search-<6-char random
    suffix>`), `tenant_id`, `subscription_id`, and `principals`: per role, `client_id` and
    `object_id`.
- **Service:**
  - backend `azurerm`, key `service.tfstate`, `use_azuread_auth`, `use_oidc`; the account and
    container are given at init;
  - variables `search_service_name`, `resource_group_name`, `location`, `subscription_id`,
    `tenant_id`;
  - output `search_host`.

- [ ] **Step 1: Write the failing contract tests** (`terraform test`, mock providers, as in the
  deleted `foundation/tests/contracts.tftest.hcl`).

  **Platform:**
  - exactly three `azurerm_user_assigned_identity`, named `id-p8-test`, `id-p8-prod` and
    `id-p8-verify`, in `rg-p8-platform`;
  - three federated credentials with audience `["api://AzureADTokenExchange"]`;
  - a storage account with `shared_access_key_enabled = false` and `min_tls_version = "TLS1_2"`;
  - exactly the containers `inputs`, `test-state`, `prod-state`, `service-state` and `evidence`,
    all private;
  - `azurerm_resource_group.search` named `rg-p8-search`, with no other resource in it;
  - the role grants. Each grant is asserted as a set of `(principal, role, scope suffix)`:
    - on `rg-p8-search`: test and prod get Search Service Contributor and Search Index Data
      Contributor, and verify gets Search Index Data Reader;
    - every role gets Storage Blob Data Reader on `inputs` and Storage Blob Data Contributor on
      `evidence`;
    - test gets Blob Data Contributor on `test-state` and `service-state`, and prod on
      `prod-state`;
    - the owner gets Storage Blob Data Contributor on the account;
  - no role assignment at subscription scope.

  **Service:**
  - one `azurerm_search_service`, `basic`, with 1 replica, 1 partition,
    `local_authentication_enabled = false` and `semantic_search_sku = "free"`;
  - no role assignment;
  - no storage.
- [ ] **Step 2: Run them in WSL to verify they fail.** On a clean copy:
  `terraform init -backend=false && terraform test`. Expected: errors, because the resources aren't
  defined yet.
- [ ] **Step 3: Implement both stacks.** Copy the provider pins and `.terraform.lock.hcl` from
  `infra/pipelines/index`. Remove `identity/` and `foundation/` with `git rm -r`.
- [ ] **Step 4: Run `terraform fmt -check`, `validate` and `test` until they pass** for platform,
  service and the unchanged index stack.
- [ ] **Step 5: Commit** with the message `feat(infra): long-lived P8 platform and per-run search service stacks`.

### Task 6: Pipeline YAML on hosted agents

**Files:**
- Rewrite: `pipelines/search-delivery.yml` and `pipelines/templates/search-delivery.yml`
- Create: `pipelines/templates/setup-steps.yml`
- Modify: `pipelines/templates/identity-preflight-task.yml` (drop the `P8_PREFLIGHT_*` env; add the
  `ReleaseLens-P8-Delivery` variables)
- Test: rewrite `tests/infra/test_pipelines_yaml.py`

**Interfaces:**
- **Consumes:** the CLIs from Tasks 2, 4 and 7.
- **`setup-steps.yml`** is used by every job:
  1. `checkout: self` with `clean: true` and `persistCredentials: false`;
  2. `UsePythonVersion@0` 3.12;
  3. the two venvs from the hash-locked files, as the old identity-preflight job built them;
  4. Terraform via `python build/delivery-agents/install.py --terraform-out "$(Agent.TempDirectory)/p8-bin/terraform"`;
  5. `##vso[task.prependpath]` for the CLI venv and `p8-bin`.
- **Parameters:** `mode ∈ {release, negative, teardown, identity-preflight}`.
- **Release stages** (each a job on `ubuntu-24.04`, with timeouts the builder sets from the spec's
  timeline):
  1. `BuildAndTest` (Test): runs the `build` stage;
  2. `CreateService` (Test): `service create-service`;
  3. `PlanTest` (Test): `plan-test`;
  4. `TestIndex`: a deployment job, environment `ReleaseLens-P8-Test`, Test, runs `test`;
  5. `PlanProd` (Prod): `plan-prod`;
  6. `ProdIndex`: a deployment job, environment `ReleaseLens-P8-DemoProd`, Prod, runs `prod`.
- **Negative stages:**
  1. `NegativeGate`: a deployment job, environment `ReleaseLens-P8-Test`, Test, runs `negative`;
  2. `PromoteNegative`: Prod, default `succeeded()` condition, runs `promote-negative`.
- **Schedule stages:**
  1. `Status` (Verify): `service status`;
  2. `Verify` (Verify), when the status is `ready`;
  3. `TeardownProd` (Prod), when the status is `ready`, with `always()` after Verify;
  4. `TeardownTest` (Test), with the same condition, depending on `TeardownProd`.
- **Teardown mode:** `TeardownProd`, then `TeardownTest`, with no status condition.
- **Identity-preflight mode:** one job running `identity_preflight.py`, as today.
- **Every Azure step** uses `AzureCLI@2` with `addSpnToEnvironment: true`, `visibleAzLogin: false`,
  `keepAzSessionActive: true`, and `SYSTEM_ACCESSTOKEN` and `SYSTEM_OIDCREQUESTURI` in its env.
  JUnit is published with `failTaskOnMissingResultsFile: true` where a gate runs.

- [ ] **Step 1: Write the failing YAML tests.** Parse with `yaml.safe_load`; expand the `${{ if }}`
  blocks by key inspection, as the existing test does:
  - `test_every_job_uses_hosted_ubuntu`, and `test_no_self_hosted_pool_or_demands` (no
    `ReleaseLens-P7` and no `demands` anywhere);
  - `test_schedule_is_2330_utc_main_always`;
  - `test_variable_group_is_delivery_and_no_authority_variables` (no `P8_AUTHORITY`, `P8_PREFLIGHT`
    or `P8_SOURCE_SHA` anywhere);
  - `test_release_stage_order_and_connections`, which asserts the exact list of
    `(stage, connection, environment)`;
  - `test_prod_index_is_deployment_job_on_demoprod_environment`;
  - `test_negative_promote_stage_runs_promote_negative_with_default_condition`;
  - `test_schedule_verify_and_teardown_conditions`: Verify and TeardownProd are gated on
    `P8Status == 'ready'`; TeardownProd has `always()`; TeardownTest depends on TeardownProd;
  - `test_teardown_mode_has_both_teardown_stages_unconditional`;
  - `test_no_docker_or_opt_p8_workload_paths`.
- [ ] **Step 2: Run them to verify they fail.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_pipelines_yaml.py -q`.
- [ ] **Step 3: Write the YAML.** Keep `trigger: none`, `pr: none` and `lockBehavior: sequential`.
- [ ] **Step 4: Run the tests until they pass.**
- [ ] **Step 5: Commit** with the message `feat(pipelines): hosted-agent YAML with release, negative, schedule and teardown`.

### Task 7: Identity preflight without the packet

**Files:**
- Modify: `scripts/pipelines/identity_preflight.py`
- Test: `tests/infra/test_pipelines_identity_preflight.py` and `tests/infra/test_pipelines_identity_diagnostics.py`

**Interfaces:**
- **Consumes:** Task 1 `load_settings`.
- **Produces:** the CLI `python -m scripts.pipelines.identity_preflight {test|prod|verify|all}
  --receipt-dir <dir> [--guard-only]`. It has no `P8_PREFLIGHT_*`, no `pipeline_id` pin and no
  30-minute packet. `all` requires the three role receipts written by the role commands in this job.

- [ ] **Step 1: Update the tests.** Remove the packet and expiry cases. Add:
  - `test_preflight_reads_settings_not_packet`;
  - `test_all_requires_three_role_receipts`.

  Keep the refresh-endpoint, wrong-subject and secret-present cases.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them until they pass.**
- [ ] **Step 5: Commit** with the message `refactor(pipelines): identity preflight reads delivery settings`.

### Task 8: Remove the session controller and the agent image

**Files:**
- **Delete** (with `git rm`):
  - `scripts/pipelines/{session,cleanup,owner,ownership,watchdog,quiescence,startup,paid_admission,prepaid_cleanup,definition,diagnostic_preparation,fingerprint,physical_observation,metadata,readiness,authority_transport,promotion,evidence,checks}.py`;
  - `scripts/pipelines/helper_templates/`;
  - `tests/infra/fixtures/keeper_startup_probe.py`;
  - `tests/infra/test_pipelines_{authority_transport,cleanup,diagnostic_preparation,evidence,fingerprint,helper_admission,local_absence,metadata_policy,ownership,physical_observation,prepaid_cleanup,promotion_bindings,quiescence,readiness,run_checks,save_lifecycle,session,startup,acceptance_readiness,admission_time,pending_target,recovery_sequence,image}.py`;
  - `.github/workflows/delivery-agent-image.yml`;
  - `build/delivery-agents/{Dockerfile,smoke.sh,toolchain_receipt.py}`.
- **Modify:** `.github/workflows/ci.yml`, removing the `keeper-startup-windows` job.

- [ ] **Step 1:** Before deleting, run `git grep -n -E "<each module name>"` across `scripts`,
  `eval`, `tests`, `.github` and `pipelines`. Only the files listed above may reference them. Any
  other hit means stop and add it to this task. Keep `build/delivery-agents/install.py`,
  `versions.json`, `requirements.*` and `cli-requirements.*` (`install.py` reads `versions.json`).
- [ ] **Step 2:** Delete the files and edit `ci.yml`.
- [ ] **Step 3:** Run the full suites:
  - `eval/.venv/Scripts/python.exe -m pytest tests/infra -q`;
  - from `eval/`, `.venv/Scripts/python.exe -m pytest -q`;
  - `eval/.venv/Scripts/python.exe scripts/check_workflows.py .github/workflows`.

  Expected: all pass, with no collection errors.
- [ ] **Step 4: Commit** with the message
  `refactor(pipelines): remove the session controller and the delivery agent image`.

### Task 9: Owner helpers, the runbook and the docs

**Files:**
- Create: `scripts/pipelines/owner_setup.py`
- Test: `tests/infra/test_pipelines_owner_setup.py`
- Rewrite: `docs/runbook-pipelines.md`
- Modify: `README.md` (the Project 8 section) and
  `docs/superpowers/specs/2026-10-15-azure-pipelines-search-delivery-design.md` (add a dated top
  note: superseded in part by the 2026-10-20 design, with a link; nothing else changed)

**Interfaces:**
- **`owner_setup.py variables --platform-outputs <json> --connections <json> --release-digest <hex> --out <file>`**
  writes the `ReleaseLens-P8-Delivery` key/value JSON to a git-ignored path under `.local/`. It
  refuses a path inside a tracked directory. It never prints values; it prints only the key names and
  "written".
- **`owner_setup.py verify-upload --archive <path> --digest <hex>`** compares the local archive's
  SHA-256 with a downloaded copy's. The download is a separate `az storage blob download
  --auth-mode login` command in the runbook. It prints `match` or `mismatch`.

- [ ] **Step 1: Write the tests:**
  - `test_variables_have_exactly_the_settings_keys`: the keys equal Task 1's environment keys;
  - `test_variables_output_refuses_tracked_path`;
  - `test_variables_prints_no_values`, using `capsys`;
  - `test_verify_upload_match_and_mismatch`.
- [ ] **Step 2: Run them, implement, and run them again until they pass.**
- [ ] **Step 3: Write `docs/runbook-pipelines.md`, under 250 lines,** in `docs/runbook-functions.md`'s
  shape:
  - rules, including the WSL Terraform, CA bundle, `acctok` and Norton notes;
  - cost;
  - "before the run day", each step marked **Ask first**:
    1. platform plan and apply, which must be exactly the adds the contract test lists;
    2. create the three service connections' federated credentials from the connection
       issuer/subject;
    3. write the variable group with `az pipelines variable-group create` from the helper's file;
    4. upload the archive and verify it;
    5. create the pipeline from YAML, and permit its resources on the first run;
    6. the identity-preflight run;
  - the run day, following the spec's timeline table;
  - "if anything stops": queue `mode: teardown`, then the WSL fallback `terraform destroy` on
    `infra/pipelines/service`, with the exact init flags;
  - the after-run checks: no search service, three states empty, the role grants and identities
    still present;
  - the follow-up PR removing the `cron`;
  - a findings table built from the spec's "Unverified until the run" list.
- [ ] **Step 4: Update the README and the old spec note.** Run:
  `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_public_docs_identifiers.py -q`.
  Expected: pass.
- [ ] **Step 5: Commit** with the message `docs(pipelines): short runbook, owner setup helpers, README and superseded note`.

### Task 10: Whole-branch check

- [ ] **Step 1:** Run every suite:
  - `dotnet build` (0 warnings);
  - `eval/.venv/Scripts/python.exe -m pytest tests/infra -q`;
  - `eval/.venv/Scripts/python.exe -m pytest -q` from `eval/`;
  - `check_workflows.py`;
  - in WSL, `terraform fmt -check`, `validate` and `test` for `infra/pipelines/{platform,service,index}`.

  Expected: all pass.
- [ ] **Step 2:** Run `git grep -n -E "P8_AUTHORITY|P8_PREFLIGHT|ReleaseLens-P7|/opt/p8-workload|queue_cutoff|keeper"`
  over `scripts`, `pipelines`, `tests/infra/test_pipelines_*` and `docs/runbook-pipelines.md`.
  Expected: no hits.
- [ ] **Step 3:** Record the line counts removed and added (`git diff --stat origin/main`) for the PR
  body. No commit is needed if nothing changed.

**Not in this plan (plan 2, the owner's session):** the one-time setup, the run day, the follow-up
PR that removes the `cron`, and the public report `docs/pipelines-report.md`. They follow
`docs/runbook-pipelines.md`, each step with the owner's yes.
