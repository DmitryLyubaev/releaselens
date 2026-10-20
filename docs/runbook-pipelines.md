# Runbook: Project 8, the search delivery on hosted agents

The one-time setup and the run day of [the 20 October design](superpowers/specs/2026-10-20-pipelines-simplified-delivery-design.md):
a gated release to test and, once approved, demo-prod; a negative run that must go red; and the 16:00
scheduled run that verifies prod and tears down. (The schedule moved from 09:30 to 16:00 Brisbane, 06:00 UTC,
so a morning release is verified and torn down the same day.) **Status: not yet run;** nothing is applied or queued.

## Rules

- **Every step marked "Ask first" waits for the owner's explicit yes** in chat, one yes per step: every
  apply, destroy, upload, merge, run, approval, permit click, and Azure DevOps or identity change.
- **The owner signs in** with the personal account (`az login` per window, the portal in the browser).
  **No keys** exist (shared keys and local auth are off); no password, PAT or token goes anywhere.
- **Before any `az` command, `acctok` must print `True`,** and it prints nothing else (`az account show`
  alone prints the email): the user lacks `WORK_DOMAIN` (WSL: `WSLENV=WORK_DOMAIN`), the tenant is the bootstrap's.
- **Never print an identifier** (tenant, subscription, client, object or connection ID, storage or search
  name, organisation, email): values stay in variables or `.local/project8/setup/`, which git ignores.
- **On Windows, `az` needs the Norton CA bundle** for that process only (`$env:REQUESTS_CA_BUNDLE`, below).
  Norton can hold a short reply (an empty body, a refusal) for two minutes: if `az` hangs, use WSL.
- **Terraform runs only in WSL (Ubuntu),** with `TF_DATA_DIR=$HOME/tfdata/p8-platform` or `p8-service`;
  its plan, apply and error text (stdout and stderr) go to private files, with only summary lines
  shown. **Never permanently delete the owner's files,** Codex's worktrees,
  `.local` evidence, or the old pipeline 2 and its `ReleaseLens-P8` group.
- **Findings go in `.local/project8/findings.md`,** then into the table at the end, failures included.

## What it costs

AI Search Basic is about US$0.13 an hour (US$3.19 a day; refresh it) from `CreateService` to `TeardownTest`,
75 to 90 minutes: under AUD 1 a run. Platform: pennies a month; hosted jobs: the free grant. **The service
bills until destroyed;** left to the next day's 16:00, about US$3.20 more.

## The windows and the helpers

PowerShell at the repository root; the archive's path and manifest digest are in the 20 October handover:

```powershell
Set-Location (git rev-parse --show-toplevel)
$env:REQUESTS_CA_BUNDLE = "$env:USERPROFILE\.azure\ca-bundle-with-norton.pem"; $env:PYTHONPATH = "eval"
$P8 = ".local/project8/setup"; $archive = '<frozen archive path>'; $digest = '<release manifest digest>'
function acctok {
  $a = az account show --query "{user: user.name, tenant: tenantId}" -o json | ConvertFrom-Json
  $boot = (wsl.exe -d Ubuntu --exec bash -c "cd /mnt/e/Projects/ReleaseLens/infra/bootstrap && TF_DATA_DIR=`$HOME/tfdata/bootstrap-live terraform output -raw tenant_id").Trim()
  [bool]($env:WORK_DOMAIN -and ($a.user -notmatch [regex]::Escape($env:WORK_DOMAIN)) -and ($a.tenant -eq $boot))
}
```

Ubuntu in WSL. Organisation and project are typed without echo; `setting` is used only inside a
command; `stack` enters a clean copy of a stack, with no `terraform.tfvars*` to load by accident:

```bash
REPO=/mnt/e/Projects/ReleaseLens   # adjust to your clone
P8="$REPO/.local/project8/setup"; mkdir -p "$P8"; export TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
read -rsp 'Organisation: ' ADO_ORG; echo; read -rsp 'Project: ' ADO_PROJECT; echo
ado() { az "$@" --org "https://dev.azure.com/$ADO_ORG" --project "$ADO_PROJECT"; }
setting() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$P8/variables.json" "$1"; }
stack() { local s="$REPO/infra/pipelines/$1"; W=$(mktemp -d); cp "$s"/{main,variables,versions,outputs,backend}.tf "$s/.terraform.lock.hcl" "$W"; cd "$W"; }
acctok() { local u t b; u=$(az account show --query user.name -o tsv) && t=$(az account show --query tenantId -o tsv) || { echo False; return; }
  b=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir="$REPO/infra/bootstrap" output -raw tenant_id)
  [[ -n "$WORK_DOMAIN" && "$u" != *"$WORK_DOMAIN"* && -n "$t" && "$t" == "$b" ]] && echo True || echo False; }
```

## Before the run day

### Step 1. The platform stack

**Ask first.** Identities, credentials, storage, `rg-p8-platform`, the empty `rg-p8-search`, the grants.

**1a. Read the three connections** (WSL, with the `azure-devops` extension). It writes each one's
issuer, subject and ID, and the stack's inputs; a `KeyError` is a connection missing or not federated:

```bash
ado devops service-endpoint list -o json > "$P8/endpoints.private.json"
PROJECT_ID=$(ado devops project show --query id -o tsv) SUB=$(az account show --query id -o tsv) \
TENANT=$(az account show --query tenantId -o tsv) OID=$(az ad signed-in-user show --query id -o tsv) ADO_ORG="$ADO_ORG" python3 - "$P8" <<'EOF'
import json, os, sys; d, R = sys.argv[1], ("test", "prod", "verify")
ep = {e["name"][len("ReleaseLens-P8-"):].lower(): e for e in json.load(open(f"{d}/endpoints.private.json")) if e["name"] in [f"ReleaseLens-P8-{r.title()}" for r in R]}
fed = {r: {k: ep[r]["authorization"]["parameters"]["workloadIdentityFederation" + k.title()] for k in ("issuer", "subject")} for r in R}
conn = {r: fed[r] | {"service_connection_id": ep[r]["id"]} for r in R} | {"oidc_path_prefix": f"/{os.environ['ADO_ORG']}/{os.environ['PROJECT_ID']}/_apis/distributedtask/hubs/build/"}
open(f"{d}/connections.json", "x").write(json.dumps(conn))
open(f"{d}/platform.tfvars.json", "x").write(json.dumps({"subscription_id": os.environ["SUB"], "tenant_id": os.environ["TENANT"], "owner_object_id": os.environ["OID"], "federation": fed}))
EOF
```

**1b. Plan** (state: the bootstrap's account, `tfstate-bootstrap`, `p8-platform.tfstate`). Run 1b and 1c in one shell:

```bash
stack platform; export TF_DATA_DIR="$HOME/tfdata/p8-platform"
ACCOUNT=$(TF_DATA_DIR="$HOME/tfdata/bootstrap-live" terraform -chdir="$REPO/infra/bootstrap" output -raw tfstate_storage_account)
terraform init -input=false -reconfigure -lockfile=readonly -backend-config=storage_account_name="$ACCOUNT" -backend-config=container_name=tfstate-bootstrap > "$P8/platform-init.private.txt" 2>&1; grep -E '^Terraform has been successfully initialized|Error:' "$P8/platform-init.private.txt"
az group exists --name rg-p8-platform; az group exists --name rg-p8-search    # both false
terraform plan -no-color -input=false -var-file="$P8/platform.tfvars.json" -out="$P8/platform.tfplan" > "$P8/platform-plan.private.txt" 2>&1; grep -E '^(Plan:|Apply complete|No changes)|Error:' "$P8/platform-plan.private.txt"
terraform show -no-color "$P8/platform.tfplan" | grep -E '^Plan:|^  # '
```

It must be **`Plan: 31 to add, 0 to change, 0 to destroy.`**, all `will be created` (else stop): the
suffix, two groups, three identities and their credentials, the account and five containers, the owner's
grant, and the 15 grants that `grants_made_once` in `infra/pipelines/platform/tests/contracts.tftest.hcl` lists.

**1c. Apply, save the outputs** (sensitive; `-json` writes the values) **and check the credentials** (`1` each):

```bash
terraform apply -no-color -input=false "$P8/platform.tfplan" > "$P8/platform-apply.private.txt" 2>&1; grep -E '^(Plan:|Apply complete|No changes)|Error:' "$P8/platform-apply.private.txt"
terraform output -no-color -json > "$P8/platform-outputs.json" 2> "$P8/platform-output.private.txt"; grep -E 'Error:' "$P8/platform-output.private.txt"
for r in test prod verify; do az identity federated-credential list --identity-name "id-p8-$r" --resource-group rg-p8-platform --query "length(@)" -o tsv; done
```

### Step 2. Point the connections at the identities

**Ask first.** Step 1 made the credentials from each connection's issuer and subject. Copy each client ID
unseen (PowerShell; `test`, then `prod`, `verify`) into the portal: Project settings, Service connections,
`ReleaseLens-P8-Test` (`-Prod`, `-Verify`), Edit, client ID; Verify and save. Keep each branch check. If it cannot change, stop.

```powershell
(Get-Content "$P8/platform-outputs.json" | ConvertFrom-Json).principals.value.test.client_id | Set-Clipboard
```

### Step 3. The variable group

**Ask first.** The helper builds the seven `P8_*` values, checks them with the pipeline's own settings
and preflight checks, and prints only the key names and `written`:

```powershell
eval/.venv/Scripts/python.exe -m scripts.pipelines.owner_setup variables --platform-outputs "$P8/platform-outputs.json" --connections "$P8/connections.json" --release-digest $digest --out "$P8/variables.json"
```

Then in WSL, where `az` gets the JSON's quotes intact (Windows' `az.cmd` strips them), create the
non-secret group, not authorised for all pipelines, and read it back; it must print `match`:

```bash
mapfile -t PAIRS < <(python3 -c 'import json,sys; print(*(f"{k}={v}" for k,v in json.load(open(sys.argv[1])).items()),sep="\n")' "$P8/variables.json")
ado pipelines variable-group create --name ReleaseLens-P8-Delivery --authorize false --variables "${PAIRS[@]}" -o none; unset PAIRS
ado pipelines variable-group list --group-name ReleaseLens-P8-Delivery -o json | python3 -c 'import json,sys; g=json.load(sys.stdin); w=json.load(open(sys.argv[1])); print("match" if len(g)==1 and {k:v["value"] for k,v in g[0]["variables"].items()}==w else "mismatch")' "$P8/variables.json"
```

### Step 4. Upload the release archive and read it back

**Ask first.** 171,575,192 bytes to `inputs/releases/<digest>/release.tar.gz`, the path every stage
reads. A `403` soon after step 1 is the owner's grant propagating: wait and retry. It prints `match`:

```powershell
$acct = (Get-Content "$P8/variables.json" | ConvertFrom-Json).P8_STORAGE_ACCOUNT
az storage blob upload --auth-mode login --account-name $acct --container-name inputs --name "releases/$digest/release.tar.gz" --file $archive --overwrite false -o none --only-show-errors
az storage blob download --auth-mode login --account-name $acct --container-name inputs --name "releases/$digest/release.tar.gz" --file "$P8/readback.tar.gz" -o none --only-show-errors
eval/.venv/Scripts/python.exe -m scripts.pipelines.owner_setup verify-upload --archive "$P8/readback.tar.gz" --digest (Get-FileHash $archive -Algorithm SHA256).Hash
$acct = $null
```

### Step 5. The pipeline from YAML

**Ask first,** for the merge and each portal change. **Do steps 5 and 6 in one sitting, before 16:00
Brisbane:** merging to `main` registers the 16:00 schedule, whose run would wait on step 6's permits.
Merge the reviewed PR, then: Pipelines, New pipeline, GitHub, this repository, Existing Azure Pipelines
YAML file, `main`, `/pipelines/search-delivery.yml`; **Save, not Run.** Leave pipeline 2 disabled. The
Triggers must not override the schedule. On both environments (Security, Pipeline permissions) add the
pipeline; DemoProd keeps its approval and exclusive lock. Parallel jobs: one hosted private job, minutes left.

### Step 6. The identity preflight

**Ask first.** Run pipeline, `main`, mode `identity-preflight`; permit the three connections and the group. No Azure
cost. Green, with `<role>-identity.json` per role; red leaves `<role>-failure.json` with a check code: stop.

## The run day

| Brisbane | Step | The owner |
|---|---|---|
| before 08:30 | `acctok` is `True`; `az search service list --resource-group rg-p8-search --query "length(@)" -o tsv` prints `0`; nothing queued (cancel a scheduled run waiting on permissions) | a look |
| ~08:30 | **Ask first.** Run pipeline, `main`, mode `release`: BuildAndTest, CreateService, PlanTest, TestIndex | one click |
| ~08:55 | TestIndex green; the release waits at `ReleaseLens-P8-DemoProd` | **Ask first:** approve |
| ~09:00 | PlanProd, then ProdIndex: prod applied, loaded and gated | — |
| ~09:10 | **Ask first.** Run pipeline, `main`, mode `negative` | one click |
| 16:00 | The scheduled run: Status, Verify, TeardownProd, TeardownTest | — |
| ~16:15 | "After the run", below | a look |

- **The negative run must go red:** `NegativeGate` ends `status=expected-failure code=quality`,
  `PromoteNegative` is skipped, and its `gate/receipt.json` has `acceptance_pass` and `prod_unchanged`
  `true`. A `status=refused code=...` line is a real failure.
- **The scheduled run** (reason Scheduled): Status prints `code=ready`, then all four stages green.
  `code=incomplete` skips Verify and teardown: queue `mode: teardown`, or wait for the next 16:00.
- **A failed run means teardown and a fresh release.** Evidence is create-only under `evidence/runs/<release
  run id>/` (pointer `evidence/current.json`): a rerun or a second negative is refused (`code=reserved`).
- **A release refuses while a service exists** (`code=reserved` at BuildAndTest, when a search service
  exists or `service-state` lists one): queue `mode: teardown` first.

### If anything stops

1. **Ask first.** If a release waits at the DemoProd approval, reject it first. Then run pipeline, `main`,
   mode `teardown`: TeardownProd, then TeardownTest, which runs even if the first failed. Both green;
   each artifact's `teardown.json` says `outcome: pass`.
2. **If teardown cannot run, destroy the service stack from WSL. Ask first.** A clean copy, `service.py`'s
   inputs, `run.py`'s init flags and the owner's `az login` (`use_oidc=false` overrides the backend's):

   ```bash
   stack service; unset ${!ARM_*}; export TF_DATA_DIR="$HOME/tfdata/p8-service"
   python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); print(json.dumps({"search_service_name":v["P8_SEARCH_SERVICE"],"resource_group_name":v["P8_SEARCH_GROUP"],"location":"australiaeast","subscription_id":v["P8_SUBSCRIPTION_ID"],"tenant_id":v["P8_TENANT_ID"]}))' "$P8/variables.json" > inputs.tfvars.json
   terraform init -input=false -reconfigure -lockfile=readonly -backend-config=storage_account_name="$(setting P8_STORAGE_ACCOUNT)" \
     -backend-config=container_name=service-state -backend-config=key=service.tfstate -backend-config=use_oidc=false > "$P8/service-init.private.txt" 2>&1; grep -E '^Terraform has been successfully initialized|Error:' "$P8/service-init.private.txt"
   acctok                                                          # must print True
   terraform plan -destroy -no-color -input=false -var-file=inputs.tfvars.json -out=destroy.tfplan > "$P8/destroy-plan.private.txt" 2>&1; grep -E '^(Plan:|Apply complete|No changes)|Error:' "$P8/destroy-plan.private.txt"
   terraform show -no-color destroy.tfplan | grep -E '^Plan:|^  # '
   ```

   It must be only `azurerm_search_service.search`, `Plan: 0 to add, 0 to change, 1 to destroy.`; else stop. Then:

   ```bash
   terraform apply -no-color -input=false destroy.tfplan > "$P8/destroy-apply.private.txt" 2>&1; grep -E '^(Plan:|Apply complete|No changes)|Error:' "$P8/destroy-apply.private.txt"
   ```

   The indexes go with the service; **before any new release, run `mode: teardown` once** to clear their states.
3. **A state lease left by a stage killed at its timeout** (`leased` below). **Ask first.** First break it, then queue `mode: teardown` again:
   `az storage blob lease break --auth-mode login --account-name "$(setting P8_STORAGE_ACCOUNT)" --container-name <container> --blob-name <blob>`.
4. **A search service no state lists** (the list prints `1`, `service-state` empty). **Ask first:**
   `az search service delete --name "$(setting P8_SEARCH_SERVICE)" --resource-group rg-p8-search --yes`.
   **Before any new release, run `mode: teardown` once** to clear the index states.

## After the run

In WSL: `0` services; each state `empty` and `available`; platform plan `0` (no changes: identities, credentials
and grants all there). A service still present is a failure, whatever passed.

```bash
az search service list --resource-group rg-p8-search --query "length(@)" -o tsv
for s in test-state/index.tfstate prod-state/index.tfstate service-state/service.tfstate; do c=${s%%/*} b=${s#*/}
  az storage blob download --auth-mode login --account-name "$(setting P8_STORAGE_ACCOUNT)" --container-name "$c" --name "$b" --file "$P8/$c.private.json" -o none --only-show-errors
  python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(sys.argv[2], "HOLDS" if any(r.get("mode")=="managed" and r.get("instances") for r in s.get("resources",[])) else "empty")' "$P8/$c.private.json" "$c"
  az storage blob show --auth-mode login --account-name "$(setting P8_STORAGE_ACCOUNT)" --container-name "$c" --name "$b" --query properties.lease.state -o tsv
done
stack platform; TF_DATA_DIR="$HOME/tfdata/p8-platform" terraform plan -no-color -input=false -detailed-exitcode -var-file="$P8/platform.tfvars.json" > "$P8/platform-recheck.private.txt" 2>&1; echo $?
```

## The follow-up PR

**Ask first.** After the one genuine scheduled run, a small PR removes `schedules:` from `pipelines/search-delivery.yml`
and the tests pinning it (`tests/infra/test_pipelines_yaml.py`); `docs/pipelines-report.md` shows that one run.

## Findings

| # | Unverified until the run | Step | Finding |
|---|---|---|---|
| 1 | One private Microsoft-hosted parallel job, with minutes left (rechecked) | 5 | |
| 2 | Hosted agents install the pinned toolchain within the job timeouts, and the 41,825-row upload stays inside its 240 s and 200-request budget (`upload-*.metrics.json`) | run day | |
| 3 | Workload identity federation and token refresh for the long-lived identities | 6 | |
| 4 | How long a Basic search service takes to create (`CreateService`) and to delete (`TeardownTest`) | run day | |
| 5 | The 06:00 UTC schedule registers from `main` and fires as `Build.Reason = Schedule` | run day | |
| 6 | The Test identity deletes the service, and the indexes with it, under the group-scope grant | run day | |
| 7 | Every run's service is named `p8-search-<suffix>`; a create soon after a delete may meet a name Azure has not yet released | run day | |
| 8 | A stage killed at its timeout (`run.py`, `service.py`) leaves a state lease or a service no state lists; whether the recovery was needed | run day | |
| 9 | Verify's `Reader` on `rg-p8-search` lets the scheduled Status run `az search service show` | run day | |
| 10 | `az devops` and `az pipelines` work with the owner's `az login` in WSL; the endpoint list gives each issuer and subject; the OIDC request path prefix is `/<organisation>/<project ID>/_apis/distributedtask/hubs/build/` (step 6). The prefix check is case-sensitive and the prefix is built from the organisation name as typed; if the step-6 preflight fails with `refresh-endpoint`, compare the prefix's casing with `System.OidcRequestUri`, rerun `owner_setup variables` with a new `--out` path (it never overwrites), then rewrite the variable group | 1, 3, 6 | |
| 11 | The platform plan was exactly 31 to add (or the number, and why) | 1 | |
| 12 | `-backend-config=use_oidc=false` let the fallback destroy use the owner's `az login` (or "not used") | stops | |
