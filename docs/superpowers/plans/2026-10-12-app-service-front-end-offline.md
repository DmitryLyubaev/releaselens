# App Service Front End Offline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify project 6's owner-only Razor Pages front end, keyless infrastructure,
gated release workflow and evidence tooling without live Azure operations.

**Architecture:** Easy Auth supplies the owner's delegated gateway token to a small server-rendered
app. Bootstrap owns its registration and identity; an owner-applied session stack owns Linux S1
and staging. Separate workflow dispatches deploy and promote, using protected authenticated
health checks and receipts; a Python harness records the fixed checks without identifiers.

**Tech Stack:** .NET 10, Razor Pages, xUnit v3, Python 3.14/httpx/pytest, Terraform ~> 1.15.0,
azurerm ~> 5.6, azuread ~> 3.0, azapi ~> 2.13, existing GitHub Actions and central dependency pins.

**Spec:** [Approved design](../specs/2026-10-12-app-service-front-end-design.md).

## Global Constraints

- ASP.NET Core Razor Pages on .NET 10; no JavaScript build pipeline.
- One Linux Standard S1 instance in Australia East; four-hour session target.
- Direct gateway calls only; no corpus retrieval, artefact citations or conversation history.
- One question at a time; trim and allow 1 to 2,000 characters; form limit 16 KiB.
- One non-streaming request, maximum 256 completion tokens, 60-second timeout, no automatic model retry.
- Owner tokens only for model calls; no managed-identity fallback and no production auth bypass.
- Owner role `WebApp.Use`, application role `WebApp.Smoke`; deployment callers use health only.
- Keyless Easy Auth via `OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID`; `offline_access` and the gateway's `access_as_user` scope.
- Main and staging share one registration/identity; settings are slot-sticky, package version moves with code.
- Basic publishing credentials stay disabled; use Entra-authorized ZIP deployment and `WEBSITE_RUN_FROM_PACKAGE=1`.
- Separate owner approval for seed deployment, staging deployment and normal staging-to-main swap.
- Existing `azure` environment, literal `main` guards, exact job permissions, pinned actions and shared concurrency.
- Readiness: at most 30 attempts over five minutes, 10-second request timeout; identity/version mismatch fails immediately.
- Telemetry deadline 15 minutes; missing metrics fail W1. W6 needs settings and teardown subchecks.
- All measured commands require a clean tree and ignored output; raw credentials/identifiers remain in memory.
- Terraform only in WSL, one data directory per stack; offline clean copies without tfvars and with mocked providers.
- No `.env` reads, Azure calls, applies, destroys, deploys, role creation or paid calls during execution of this plan.
- No push, PR, merge or branch deletion without separate owner approval. Preserve the untracked handover.
- No security-software changes, permanent file deletion, employer material or Unicode em dashes.

Plan 2 is written after offline implementation/review and CI evidence. This plan creates its
runbook and tooling; it does not execute the runbook or populate a live report with passes.

## Review Focus

- Multiple/malformed principal headers and legacy claim names must deny ambiguity and accept only documented claim aliases (Task 1).
- Slow refusal bodies, malformed success JSON, HTTP-date Retry-After and cancellation must produce safe outcomes without retrying (Task 2).
- HTML in model output, missing/forged antiforgery and a protocol-relative return URL must not execute content or make a gateway request (Task 3).
- A valid-looking receipt from another run, a fixture or a changed staging package must never authorize a swap (Task 7).
- Chunked session cookies, 204 refresh responses, unchanged expiry and stale Caller metrics must not manufacture refresh/identity evidence (Tasks 6 and 8).

## File structure and task order

| Task | Files and responsibility |
|---|---|
| 1 | New `src/ReleaseLens.Web/{Program.cs,WebOptions.cs,BuildInfo.cs,Auth/EasyAuthPrincipalReader.cs,Auth/CallerKind.cs,Auth/SessionSnapshot.cs}`; new Web test project and fixtures; solution registration. Protected host and safe diagnostics contracts. |
| 2 | New `src/ReleaseLens.Web/{Auth/GatewayTokenInspector.cs,Gateway/GatewayClient.cs,Gateway/QuestionService.cs,Gateway/QuestionOutcome.cs}` and focused tests. Owner token checks and one-call transport. |
| 3 | New `src/ReleaseLens.Web/Pages/{Index.cshtml,Index.cshtml.cs,SignedOut.cshtml,SignedOut.cshtml.cs,_ViewImports.cshtml,_ViewStart.cshtml,Shared/_Layout.cshtml}`, `wwwroot/{css/site.css,js/site.js}` and page tests. Actual agreed UI. |
| 4 | New `infra/bootstrap/webapp.tf`, `tests/webapp.tftest.hcl`; modify bootstrap outputs and add static tests. Long-lived identity and consent declarations. |
| 5 | New `infra/webapp/{versions.tf,backend.tf,remote_state.tf,variables.tf,main.tf,auth.tf,outputs.tf,README.md,.terraform.lock.hcl,tests/webapp.tftest.hcl}` and static tests. Session stack. |
| 6 | New `eval/app/webapp/{__init__.py,contracts.py,http_client.py,private_az.py}` and their tests. Memory-only HTTP and CLI boundary shared by release and evidence. |
| 7 | New `eval/app/webapp/release.py`, `scripts/package_webapp.py`, unhealthy fixture project and `webapp-release.yml`; modify workflow checker/tests. Immutable packages and release gate. |
| 8 | New `eval/app/webapp/{checks.py,observations.py,metrics.py,report.py,__main__.py}` and tests; extend existing report/document privacy guards. Fixed checks and CLI. |
| 9 | New webapp runbook and honest report skeleton; modify README, CI and release dependency-list comments. Whole-plan validation and review preparation. |

Tasks 1-3 define the web contract. Tasks 4-5 implement its settings. Task 6 is the common
transport boundary for Tasks 7-8. Task 9 integrates all of them. Read the spec and neighboring
Interfaces blocks before each task. At execution start use the worktree skill to establish
isolation using the current local HEAD, so these document commits are included; do not lose or add the owner's
untracked handover. Do not create a worktree while merely reviewing this plan.

### Task 1: Protected host, configuration and safe diagnostics

**Files:** Create the Task 1 Web files above; create
`tests/ReleaseLens.Web.Tests/{ReleaseLens.Web.Tests.csproj,Fixtures.cs,StubHttpHandler.cs,WebAppFactory.cs,PrincipalTests.cs,HostTests.cs}`;
modify `ReleaseLens.sln`. Use existing central package pins, including Mvc.Testing. Do not
reference Docker-dependent suites or `ReleaseLens.Llm` for this small web app.

**Interfaces:** `WebOptions` holds `GatewayBaseUrl: Uri`, `Model: string`, `TenantId: string`,
`GatewayAudience: string`, `OwnerObjectId: string`, `DeployObjectId: string`, `Slot: string`;
`From(IConfiguration) -> WebOptions` validates without echoing values. Configuration keys are
`Web:GatewayBaseUrl`, `Web:Model`, `Web:TenantId`, `Web:GatewayAudience`, `Web:OwnerObjectId`,
`Web:DeployObjectId`, `Web:Slot`. `CallerKind` is `None`, `Owner`, `Deployment`.
`EasyAuthPrincipalReader.Read(IHeaderDictionary, WebOptions) -> CallerKind` uses AAD principal
claims and the corresponding role. `BuildInfo.Version: string` reads immutable assembly
informational version. `SessionSnapshot` exposes `TokenPresent`, `TokenParseable`, `TenantMatches`,
`AudienceMatches`, `OwnerMatches`, `ScopeMatches`, `RoleMatches`, `Usable` (all Boolean), and
nullable `ExpiresAtUnixSeconds: long`; Task 2 supplies its values. Until Task 2 exists, session
diagnostics remain an owner-only no-store 503 response, without an invented token result.

Test helpers: `Fixtures.Options() -> WebOptions` supplies HTTPS example hosts and zero-GUID
placeholders; `Fixtures.Principal(CallerKind) -> string` builds the platform principal header;
`Fixtures.GatewayToken(DateTimeOffset expiry) -> string` builds an unsigned test JWT with
expected placeholder claims. `StubHttpHandler.Requests` records in-memory requests and supports
canned replies; `WebAppFactory` injects those options, clock and handler without opening a
network listener. Keep test bearer values out of helper `ToString()` and assertion messages.

- [ ] **Step 1: Write `PrincipalTests` and `HostTests` with these assertions.**

```csharp
// OwnerRequiresUseRole; DeploymentRequiresSmokeRole; UnknownPrincipalIsDenied
Assert.Equal(CallerKind.Owner, EasyAuthPrincipalReader.Read(ownerHeaders, options));
Assert.Equal(CallerKind.Deployment, EasyAuthPrincipalReader.Read(smokeHeaders, options));
Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(noRoleHeaders, options));
// MalformedOrConflictingPrincipalIsDenied; LegacyOidAndRoleAliasesAreAccepted
Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(conflictingHeaders, options));
// HealthHasNoDownstreamCalls; DeploymentCannotUseOwnerRoutes
Assert.Equal(HttpStatusCode.OK, health.StatusCode);
Assert.Equal(HttpStatusCode.Forbidden, ownerRouteAsDeployment.StatusCode);
Assert.Empty(stub.Requests);
```

Test header absence, malformed base64/JSON, duplicate/conflicting security claims and role-case
mismatch. Pin only short `oid`/`roles` and the documented objectidentifier/role URI aliases,
not arbitrary aliases. Test no identity/name/email in health JSON, no-store session responses,
and generic configuration failures. Test initialization with invalid options makes health
non-successful, without an exception/URI appearing in output.

- [ ] **Step 2: Run `dotnet test tests/ReleaseLens.Web.Tests --filter "PrincipalTests|HostTests"`; confirm failure from absent production contracts.**
- [ ] **Step 3: Implement the contracts and host, including health and signed-out boundaries.** Use a public partial `Program` for the test host. All routes use the explicit caller classification; health returns `{status,version,slot,configurationValid,caller}` with caller labels only. Owner-only routes initially return their unfinished placeholder response, never a gateway call. Wire the session route to the token inspector in Task 2. Configure production errors/logging so option/header values cannot be emitted.
- [ ] **Step 4: Run those tests and `dotnet build`; require passing tests and zero warnings.**
- [ ] **Step 5: Commit only the Task 1 files: `feat: add protected web host and caller boundaries`.**

### Task 2: Owner-token validation and bounded gateway requests

**Files:** Task 2 production files; create
`tests/ReleaseLens.Web.Tests/{GatewayTokenTests.cs,GatewayClientTests.cs,QuestionServiceTests.cs}`;
complete `/session` in `Program.cs` using Task 1's snapshot contract.

**Interfaces:** `GatewayTokenInspector.Inspect(string? token, WebOptions options, DateTimeOffset now)
-> SessionSnapshot`, whose `Usable` is true only when every mandatory check passes and expiry
is in the future. Accept the configured gateway client audience or its `api://` form, expected
tenant/owner, space-delimited `scp` containing `access_as_user`, and `Gateway.Invoke` role.
Missing/duplicate/conflicting required JWT claims are invalid. This parser does not validate
signatures or replace Easy Auth/gateway verification.

`GatewayClient.AskAsync(string question, string bearer, CancellationToken) -> Task<QuestionOutcome>`
uses the configured URI/model and an injected HttpClient/TimeProvider.
`QuestionService.AnswerAsync(CallerKind caller, string question, string? gatewayToken,
CancellationToken) -> Task<QuestionOutcome>` performs local checks before that client.
Define `QuestionKind` in `QuestionOutcome.cs` with `Success`, `InvalidQuestion`, `SignInRequired`,
`Forbidden`, `RateLimited`, `Unavailable`. `QuestionOutcome` carries `Kind: QuestionKind`,
nullable `Answer` and nullable `RetryAfterSeconds`; never a
token, host or backend error. Page status mapping is 200/400/401/403/429/503 respectively.

- [ ] **Step 1: Write the token, service and transport tests.**

```csharp
// MissingExpiredOrWrongSubjectNeverCallsGateway
Assert.Equal(QuestionKind.SignInRequired, rejected.Kind);
Assert.Empty(stub.Requests);
// OwnerRequestForwardsOnlyItsBearerAndPinnedLimits
Assert.Single(stub.Requests);
Assert.Equal("Bearer", sent.Authorization!.Scheme);
Assert.Equal(ownerTestToken, sent.Authorization.Parameter);
Assert.Equal(256, payload.MaxCompletionTokens);
Assert.False(payload.Stream);
// RefusalDoesNotReadBodyOrRetry
Assert.Equal(QuestionKind.RateLimited, refused.Kind);
Assert.Equal(0, unreadableBody.ReadAttempts);
Assert.Single(stub.Requests);
```

Add parameterized missing/malformed/expired token, wrong tenant/audience/subject/scope/role,
non-owner caller, whitespace and 2,001-character input cases. Use a response content stub
that throws if read to prove 3xx/401/403/429/5xx bodies are untouched. Test a successful nonempty
answer; empty/null/invalid JSON; a 1 MiB success-body limit; timeout versus user cancellation;
per-request Authorization and no cross-request reuse. Test integer and HTTP-date Retry-After,
with only 0-3,600 seconds displayed; omit negative, invalid or larger values. Capture logging
and assert there is no test bearer, configured host, question or response text.

- [ ] **Step 2: Run `dotnet test tests/ReleaseLens.Web.Tests --filter "GatewayTokenTests|GatewayClientTests|QuestionServiceTests"`; verify the missing-client failure.**
- [ ] **Step 3: Implement the signatures.** Use System.Text.Json and bounded base64url decoding, without another identity SDK. Send `chat/completions` relative to the trusted gateway base URL using `ResponseHeadersRead`; apply a linked timeout through headers and bounded success-body reading. Disable automatic redirects and HttpClient's URI/header logging for this client. Rethrow caller cancellation, map other failures to fixed outcomes and use no retries. Never reference a TokenCredential.
- [ ] **Step 4: Run all Web tests; require zero unexpected HTTP requests and passing refusal/diagnostic tests.**
- [ ] **Step 5: Commit: `feat: forward validated owner tokens to the AI gateway`.**

### Task 3: Razor Pages and browser-facing refusal behaviour

**Files:** Task 3 page/assets files; create `tests/ReleaseLens.Web.Tests/PageTests.cs`;
finish Razor/antiforgery routing in `Program.cs`.

**Interfaces:** `IndexModel.OnGet()` renders the owner page; `OnPostAsync(CancellationToken)
-> Task<IActionResult>` binds `Question`, delegates to Task 2 and renders its outcome. Explicitly
add the `/ask` page route for Index and give its form a fixed `/ask` action, retaining `GET /`
for the form. Remove Task 1's temporary root endpoint to avoid an ambiguous route. Bind no
caller-provided URL/token. `/session` remains owner-only and no-store. The sign-in link always returns to `/`;
the sign-out link always returns to `/signed-out`.

- [ ] **Step 1: Write integration assertions using fresh GET forms and antiforgery cookies.**

```csharp
// ValidOwnerFormCallsOnceAndEncodesModelText
Assert.Equal(HttpStatusCode.OK, reply.StatusCode);
Assert.Contains("&lt;script&gt;", renderedAnswer);
Assert.DoesNotContain("<script>model", renderedAnswer);
Assert.Single(stub.Requests);
// MissingOrForgedAntiforgeryAndDeploymentPostNeverCallGateway
Assert.Empty(stub.Requests);
// SignedOutHasNoFormAndIgnoresExternalReturnUrl
Assert.DoesNotContain("action=\"/ask\"", signedOutHtml);
Assert.DoesNotContain("//example.invalid", signInHref);
```

Cover all Task 2 status/messages, absent/expired downstream token, 16 KiB body rejection,
input retention on refusal, no email/name/token in HTML, no answer persistence and no-store
headers. Assert exactly zero calls for every local rejection. Do not assert decorative CSS.

- [ ] **Step 2: Run `dotnet test tests/ReleaseLens.Web.Tests --filter PageTests`; confirm the missing-page/route failure.**
- [ ] **Step 3: Implement the agreed page.** Use Razor encoding and `white-space: pre-wrap`, never `Html.Raw` for model text. Give model output the fixed `answer-text` element ID for the harness's HTML parser. No citations, history, mockup selector or metrics dashboard. Use standard Razor antiforgery, fixed relative auth links and a small submit-disable script only. Show Signed in rather than personal identity. Replace Task 1's temporary signed-out endpoint with the page without duplicate routing. The signed-out route contains no owner functionality.
- [ ] **Step 4: Run all Web tests and `dotnet build`; inspect the page in an offline test host for readable narrow and desktop layouts.** This is a UI preview, not a live Easy Auth test.
- [ ] **Step 5: Commit: `feat: add the owner question page and safe error states`.**

### Task 4: Bootstrap registration, identity and consent declarations

**Files:** Create `infra/bootstrap/webapp.tf`, `infra/bootstrap/tests/webapp.tftest.hcl` and
`tests/infra/test_webapp_bootstrap.py`; modify `infra/bootstrap/outputs.tf` and its README.

**Interfaces:** New sensitive outputs: `webapp_name`, `webapp_identity_id`,
`webapp_identity_client_id`, `webapp_identity_principal_id`, `webapp_app_client_id`,
`webapp_app_identifier_uri`, `webapp_owner_object_id`, `deploy_identity_principal_id`.
Reuse the existing gateway client/scope resources, owner client-config data and deploy identity.
`webapp_name` uses the existing persistent bootstrap suffix, not a new per-session suffix.

- [ ] **Step 1: Add mocked Terraform assertions and static privacy/security guards.**

```hcl
# registration_and_federation run, using distinct zero-GUID mock values
assert {
  condition = azuread_service_principal.webapp.app_role_assignment_required
  error_message = "Front-end assignment must be required."
}
# Assert WebApp.Use allows User, WebApp.Smoke allows Application;
# only owner/deploy are assigned; callbacks contain both expected slots;
# FIC subject is the webapp MI principal, not the GitHub subject;
# gateway pre-authorization selects the existing delegated scope ID.
```

Static tests assert no application password/certificate, no Gateway.Invoke/model role for the
new identity, no GitHub trust widening, and sensitive identifier outputs. Check callback names
derive from the same stable output used by the session stack.

- [ ] **Step 2: Run `eval/.venv/Scripts/python.exe -m pytest tests/infra/test_webapp_bootstrap.py -q` and the mocked bootstrap Terraform test in an offline WSL copy; confirm missing-resource failures.**
- [ ] **Step 3: Implement Terraform declarations.** Use `azuread_application`, service principal, two random role IDs, user/application assignments and `azuread_application_federated_identity_credential`. Its issuer uses the bootstrap tenant's v2 endpoint, audience `api://AzureADTokenExchange`, subject the dedicated managed identity's principal ID. Pre-authorize the front end for the existing gateway scope without replacing the gateway registration. Create no live directory object while writing this code.
- [ ] **Step 4: Pass bootstrap fmt/validate/mocked test and static tests; check existing bootstrap tests for regression.**
- [ ] **Step 5: Commit: `infra: declare keyless webapp identity and gateway consent`.**

### Task 5: Session-owned Linux S1, staging and authentication settings

**Files:** Task 5 stack files; create `tests/infra/test_webapp_terraform.py`.

**Interfaces:** Inputs `subscription_id`, `location` default `australiaeast`,
`tfstate_storage_account`; remote bootstrap and gateway state follow existing backend conventions.
Consume Task 4 outputs and gateway `gateway_base_url`. Generate the Easy Auth delegated login
scope from the gateway application ID, not its existing `.default` output.
Sensitive outputs `webapp_resource_id`, `webapp_main_url`, `webapp_staging_url`,
`webapp_frontend_audience`; public labels only in documentation.

- [ ] **Step 1: Write mocked stack and static tests.**

```hcl
assert {
  condition = azurerm_service_plan.webapp.os_type == "Linux" && azurerm_service_plan.webapp.sku_name == "S1"
  error_message = "One Linux S1 plan is required."
}
# Assert main/staging identity equality, HTTPS, Always On, DOTNETCORE|10.0;
# both basic-publishing policies deny; required auth excludes no protected route;
# owner/deploy-only platform allowlist; token store and delegated offline_access;
# sticky Web__* keys and OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID;
# Website Contributor scope is the web app, never bootstrap/subscription.
```

Test no per-session identity or secret creation, no additional retrieval stacks, no auth
override/auto swap, and release-owned package state is not restored to a seed by a later plan.

- [ ] **Step 2: Run the new static tests and offline mocked test; require missing-stack failures.**
- [ ] **Step 3: Implement the stack.** Use azurerm for group/plan/RBAC and the repository's azapi
pattern at `2024-11-01` for site, slot, authsettingsV2 and basic-publishing policies, avoiding
overlapping Terraform ownership of configuration. Explicitly configure app startup
`dotnet ReleaseLens.Web.dll`, run-from-package, server-side builds off and `ASPNETCORE_ENVIRONMENT=Production`
in both slots. Main/staging labels differ. The `slotConfigNames` registry keeps environment
keys sticky; package selection/version moves with code. Read gateway/model settings from state.
Do not make platform auth settings or deployment permission more permissive during validation.
- [ ] **Step 4: Pass webapp fmt/validate/mocked test and static tests; generate its provider lock file with backend disabled in WSL.** Document which package-selection property is release-owned and test that a subsequent plan does not reset it.
- [ ] **Step 5: Commit: `infra: add the session webapp and protected staging slot`.**

### Task 6: In-memory HTTP and Azure CLI transport

**Files:** Task 6 Python files; create `eval/tests/webapp/{test_http_client.py,test_private_az.py,test_contracts.py}`.

**Interfaces:** `contracts.py` defines immutable `Health(http_status: int,status: str|None,
version: str|None,slot: str|None,configuration_valid: bool,caller: str|None)`,
`SessionSnapshot` matching Task 1's diagnostic JSON, `Observation(check,statuses,matches,counts,times)`
with allowlisted Boolean/numeric fields, and `CheckResult(check,verdict,evidence,observations,commit,measured_at)`.
Verdicts are `pass`, `fail`, `not run`; check IDs are W1,W2,W3,W4,W5,W6a,W6b. No free-form detail.

`WebAppClient(http: httpx.Client, origin: str, cookie: str|None)` holds origin and cookies only
in memory, does not follow redirects, and exposes `health(bearer: str|None) -> Health`,
`session() -> SessionSnapshot`, `ask(question: str) -> AskObservation`, `refresh() -> int`.
`AskObservation(status,answer_nonempty)` carries no answer text. Ask gets a fresh form first,
including antiforgery input/cookie, then parses only the `answer-text` element using the standard
library HTMLParser. `PrivateAz(expected_tenant,expected_subscription,expected_principal,
run=subprocess.run).call(args: list[str]) -> dict` captures output, checks the expected account
before calls and raises fixed-code `TransportRefused` errors without printing arguments/results.
Credential/configuration containers have redacted repr. CI caller classification is deployment;
owner-run account checks follow `acctok` and WORK_DOMAIN. Do not run these methods live yet.

- [ ] **Step 1: Add HTTP/CLI contract tests with fake subprocess and httpx transports.**

```python
# refusal_body_is_never_read; refresh_204_has_no_body_read
assert refusal.body_read_count == 0
assert refresh_body.body_read_count == 0
# wrong_account_stops_before_operation; exception_does_not_echo_credentials
assert operation_calls == []
assert captured_output == "status=refused\n"
# ask_uses_fresh_antiforgery_and_chunked_cookie_jar
assert submitted_form["__RequestVerificationToken"] == fresh_form_token
assert submitted_cookies == expected_auth_and_antiforgery_cookies
```

Test two slot origins never share cookies; refresh Set-Cookie updates including chunked auth
cookies; network error text has no host/token; malformed diagnostics fail closed; HTTP 200 is
mandatory for health; 401/302 are retained without visiting sign-in. Size-cap successful HTML/JSON
at 1 MiB. Test account mismatch fails before tokens, resource commands or file writes. All
identity fixtures are placeholders and no real CLI login is performed.

- [ ] **Step 2: From eval run `.venv/Scripts/python.exe -m pytest tests/webapp/test_http_client.py tests/webapp/test_private_az.py tests/webapp/test_contracts.py -q`; confirm import failures.**
- [ ] **Step 3: Implement the signatures.** Use httpx streaming, status/header inspection before body handling, explicit 204 handling and no automatic request retry. Do not import the large Functions CLI just to reuse its guard. Use the existing freeze guard's pure functions and existing Azure token source pattern where needed. Defer cookie/credential acquisition until measured-command guards have passed.
- [ ] **Step 4: Pass Task 6 tests and existing gateway/Functions transport tests.**
- [ ] **Step 5: Commit: `eval: add private webapp session and deployment transports`.**

### Task 7: Immutable package, receipt verification and gated workflow

**Files:** Create `scripts/package_webapp.py`, `eval/app/webapp/release.py`,
`tests/fixtures/ReleaseLens.Web.Unhealthy/{ReleaseLens.Web.Unhealthy.csproj,Program.cs}`,
`.github/workflows/webapp-release.yml`, `eval/tests/webapp/test_release.py`,
`tests/infra/test_webapp_release_workflow.py`; modify `scripts/check_workflows.py`,
`tests/infra/test_check_workflows.py` and good/bad workflow fixtures. The unhealthy assembly name
is `ReleaseLens.Web`, matching the same slot startup command. It serves health 503 and no model calls.

**Interfaces:** `package_webapp.create_zip(publish_dir: Path, destination: Path) -> str` returns
ZIP SHA-256. Versions are `<source-sha>.<run-id>.<attempt>.<seed|stage|fixture>` set with
`InformationalVersion` and `IncludeSourceRevisionInInformationalVersion=false` at publish.
The published package never includes environment/settings files or local identity data.
Receipt schema 1 contains `repository`, `workflow_path`, `source_sha`, `run_id`, `run_attempt`,
`version`, `package_sha256`, `kind` (`candidate` only for eligible receipts), `created_at`,
`baseline_version`, `checks_passed`; no runtime resource information.

`release.validate_source(run_metadata: dict, receipt: dict, expected_repository: str,
ci_metadata: dict) -> bool`; `release.smoke(client: WebAppClient, bearer: str, expected_version: str,
expected_slot: str, clock: Callable[[],datetime], sleep: Callable[[float],None]) -> Observation`;
`release.promote(context: ReleaseContext, receipt: dict,
source_metadata: dict) -> tuple[Observation,Observation]` returns W4 and configuration evidence.
`ReleaseContext` owns `PrivateAz`, main/staging clients, front-end audience, in-memory config
baselines and the verified receipt/package metadata; it never serializes those runtime values.
CLI modes `python -m app.webapp.release seed|stage|promote` consume environment configuration,
verified bundle and explicit expected versions, and exit nonzero on any failure. Names/IDs
are not CLI input flags.

- [ ] **Step 1: Test package and release trust boundaries, plus checker fixtures.**

```python
# fixture_failed_foreign_or_stale_stage_cannot_promote
assert validate_source(failed_run, receipt, expected_repo, ci) is False
assert validate_source(foreign_run, receipt, expected_repo, ci) is False
assert validate_source(good_run, fixture_receipt, expected_repo, ci) is False
# changed_staging_or_main_blocks_swap; no_swap_before_smoke
assert private_az.swap_calls == []
# successful_swap_checks_both_versions_and_configuration
assert result_w4.verdict == "pass"
assert config_observation.matches["settings_retained"] is True
```

Test missing receipts, run-attempt mismatch, failing required CI, non-main source, altered ZIP
digest, path traversal/absolute/symlink ZIP entries, wrong slot/version/caller, auth redirect,
503 fixture, deadline and post-swap failure. Reject a changed candidate even if an old successful
receipt exists. Capture one regular swap only after fresh smoke, never preview/auto swap or
automatic rollback. Persist observations before raising a failed result. Fresh data-plane
version must match the immutable receipt; verify the selected stored deployment package digest
in memory through Entra-authorized SCM reads, or refuse if that cannot be established.

Checker tests must reject push/PR triggers, missing main guard, expanded permissions, unpinned
actions, changed concurrency and any extra Azure-enabled workflow. Add the exact new workflow
to the byte-copy good-fixture assertion. Keep every existing bad fixture and rule intact.

- [ ] **Step 2: Run release and workflow tests; confirm missing implementation/allowlist failures.**
- [ ] **Step 3: Implement package helper and release interfaces.** Bind receipts to trusted successful stage runs, exact versions, package hashes and successful `build-and-test` CI for the source SHA. A receipt is stale if the current staging state, source/run attempt or configuration no longer matches it. Do not execute code taken from a receipt/artifact. CLI operations derive resource names from protected environment values and capture all Azure output. Package/SCM hashing operates on bytes in memory; log neither SCM URLs nor package-selection values. Package lookup support remains a live deployment probe, never a basic-auth fallback.
- [ ] **Step 4: Implement the workflow and exact checker rules.** Manual input `mode=seed|stage|promote`; seed/stage build only the current main SHA. `fixture` is an explicit stage input and cannot enter promote. Preflight has exactly `contents: read, actions: read`, no Azure environment/token; it checks source CI and verifies any earlier stage artifact, then uploads a verified current-run bundle. Release has exactly `id-token: write, contents: read`, `environment: azure`, and downloads that same-run bundle using artifact runtime authorization. Both jobs have literal main guards. Protected release rechecks staging after approval while holding the existing shared concurrency group. Reuse existing verified action pins and the hash-locked gateway-check dependency closure; no Terraform apply/deletion in this workflow.
- [ ] **Step 5: Pass release/checker/static tests, package normal and fixture outputs offline, validate startup assembly names and inspect ZIP manifests without running Azure commands.**
- [ ] **Step 6: Commit: `ci: gate webapp promotion on trusted authenticated staging checks`.**

### Task 8: Fixed measurements, result schema and report privacy

**Files:** Task 8 Python files; create `eval/tests/webapp/{test_checks.py,test_observations.py,test_metrics.py,test_report.py,test_cli.py}`;
modify `eval/app/gateway/checks.py` hostname matcher, `eval/app/gateway/report.py` guarded writer,
`tests/infra/test_public_docs_identifiers.py` and their existing tests. Preserve old harness results.

**Interfaces:** `checks.evaluate(check: str, observation: Observation) -> CheckResult` implements
the approved table, including all mandatory subconditions. `metrics.owner_matches(rows: list[dict],
owner_oid: str, start: datetime, end: datetime) -> tuple[bool,int]` classifies only in memory;
`metrics.query_window(http: httpx.Client, app_id: str, bearer: str, start: datetime,
end: datetime) -> list[dict]` uses the existing gateway metric name/API and an explicit bounded
window, returning private rows to `owner_matches`, never to a writer.
`LiveContext` in `observations.py` owns main/staging clients, `PrivateAz`, owner ID, Application
Insights app ID and the existing `TokenSource`, expected private configuration/resource refs,
source commit, `now: Callable[[],datetime]` and `sleep: Callable[[float],None]`. Load runtime
values from environment after the guards. It has no serializable/revealing repr.
`observations.owner(context: LiveContext) -> Observation`,
`refresh(context: LiveContext) -> Observation`, `access(context: LiveContext) -> Observation`,
`release(receipt: dict, source_metadata: dict) -> Observation`,
`settings(context: LiveContext, promotion_settings: Observation) -> Observation`, and
`teardown(context: LiveContext) -> Observation` implement the corresponding facts.

Pin these required match keys in `contracts.py`; missing keys cannot count as true:

| Check | Required Boolean matches |
|---|---|
| W1 | `token_usable`, `ask_succeeded`, `answer_nonempty`, `owner_metric_match` |
| W2 | `anonymous_main_refused`, `anonymous_staging_refused`, `forged_headers_refused`, `deployment_health_only`, `offline_edges_passed` |
| W3 | `initial_lifetime_elapsed`, `expiry_advanced`, `owner_audience_match`, `ask_succeeded`, `answer_nonempty` |
| W4 | `baseline_healthy`, `staging_healthy`, `approval_recorded`, `swap_succeeded`, `main_candidate`, `staging_baseline`, `slot_labels_retained` |
| W5 | `fixture_health_503`, `stage_failed`, `promotion_rejected`, `no_swap`, `main_unchanged` |
| W6a | `intended_settings_before`, `settings_retained`, `main_owner_signin`, `staging_owner_signin` |
| W6b | `webapp_group_absent`, `gateway_group_absent`, `plan_site_slot_absent`, `deleted_apim_absent`, `bootstrap_intact` |

Keep status arrays integer/null, counts finite nonnegative numbers, times UTC timestamps and
evidence classes from `live`, `offline`, `mixed`, `workflow`, `none`. W2's offline result is referenced by its source commit;
neither an absent test result nor unrelated green CI can fill `offline_edges_passed`.

`report.render(results: Sequence[CheckResult], commit: str) -> str` lists W1-W6, W6 subchecks,
evidence kind and not-run/failed cases; `report.write(path: Path, text: str, forbidden: Sequence[str])`
delegates to the shared guarded writer. `__main__.main(argv: list[str]|None=None) -> int` exposes
`owner`, `access`, `refresh`, `release`, `settings`, `teardown`, `report`; check commands read
environment/hidden cookie input after tree/output guards and write only schema-validated results.
`release` consumes sanitized workflow observations and source-run metadata, not an owner-entered
pass flag. `teardown` observes absence only; it never destroys or purges.

- [ ] **Step 1: Write verdict, CLI and privacy tests.**

```python
# http_success_without_owner_metric_is_not_identity_evidence
assert evaluate("W1", without_caller_metric).verdict == "fail"
# refresh_status_without_changed_expiry_cannot_pass
assert evaluate("W3", unchanged_expiry).verdict == "fail"
# settings_without_teardown_does_not_pass_w6
assert "W6: not run" in render([settings_pass], commit)
# dirty_tree_precedes_cookie_token_or_network
assert credential_calls == network_calls == []
# unsafe_artifact_writes_nothing_and_does_not_echo_match
assert not destination.exists()
assert forbidden_value not in captured_error
```

Pin each W1-W6a/W6b condition and its missing/failed counterpart from spec section 8. W2
must retain separate offline/live evidence and W5 must distinguish a passed gate test from a
failed release. Test bounded metric windows exclude stale/out-of-window rows and other callers,
with 15-minute timeout and no extra model call while polling. W3 waits for initial expiry,
refreshes, checks later expiry/same owner/audience and makes exactly one approved question call;
expiry advancement proves token contents changed without publishing a digest. Test interruptible
waits with progress at least every 30 seconds. Tests use fake clocks, never real waits. A 403,
timeout or ambiguous resource lookup is not an absence result; only explicit confirmed absence
can fill a W6b match. A transport failure after a check starts must produce a failed result;
a pre-measurement guard refusal writes nothing. Interruption records incomplete observations
without another paid request.

Guard tests cover bare app/SCM hostnames, emails, any non-placeholder GUID in docs, every GUID
in written measurement artefacts, exact runtime names and identifiers, malformed result fields,
unexpected text values and error paths. Add `.azurewebsites.net` to the existing shared hostname
matcher so old and new writers reject it. Introduce an email matcher in the shared writer
without changing existing safe-report formatting. Sanitize private string values by refusal,
not by silently removing evidence. Reject results whose commit differs from the measured commit.

- [ ] **Step 2: Run the new Webapp eval tests plus existing gateway/report/Functions tests; verify failures for missing checks and new privacy cases.**
- [ ] **Step 3: Implement observations, evaluation, CLI and writer extensions.** Reuse existing clean-tree/output guards and metric constants without importing heavy CLI modules. Prompt cookies with hidden input only, clear credentials on exit and emit fixed status labels. Refuse unsupported/evidence-free results; do not create fake live passes. Parse `/ask` safely and keep model answer text out of files. Store one guarded `check-w*.json` per subcheck plus sanitized pending observations under ignored reports; `report` never contacts Azure.
- [ ] **Step 4: Pass all eval/infra tests for the touched guards and all new commands on offline transports.** Exercise CLI errors and report missing-file/partial-result behaviour. Measured entry points must guard before requesting credentials or querying Azure; report rendering is offline.
- [ ] **Step 5: Commit: `eval: add fixed webapp checks and guarded evidence reports`.**

### Task 9: Runbook, honest documentation and full offline validation

**Files:** Create `docs/runbook-webapp.md`, `docs/webapp-report.md` as a not-run skeleton;
modify `README.md`, `.github/workflows/ci.yml`, `eval/requirements-gateway-check.txt` comments,
`tests/infra/test_webapp_release_workflow.py` for locked install/import coverage and runbook
approval boundaries. Update the approved design's section 13 only for an actual approved
material change, not to relabel a failed check.

**Interfaces:** The runbook invokes Tasks 6-8's exact commands and Task 7's modes. CI builds/tests
the new app/fixture, checks the bootstrap/webapp stacks and imports the release module in the
same clean hash-locked environment used for the gateway check. New Terraform lock participates
in the cache key. All required CI checks for candidate source are available through
`build-and-test`; avoid treating its skipped Terraform job on a main push as a passed live check.

- [ ] **Step 1: Add targeted runbook/workflow assertions.**

```python
# every_live_action_names_an_approval_boundary
assert all("**Ask first.**" in section for section in live_steps)
# nightly_destroy_does_not_claim_webapp_cleanup
assert "nightly" in text and "does not" in text and "webapp" in text
# protected_release_installs_locked_closure
assert "--require-hashes --only-binary=:all: --no-deps" in install_step
```

Require a cookie hidden-input step, account guard before Azure operations, explicit model call
counts/current rates, WSL HTTP handling, fresh per-slot owner sign-in and findings/teardown
tables. Do not test prose layout. Test the locked closure can import `app.webapp.release` and
its Azure credential library without pulling full eval dependencies into the protected job.

- [ ] **Step 2: Run those checks; confirm missing runbook and CI integration failures.**
- [ ] **Step 3: Write the runbook and documentation.** Copy the existing runbooks' owner-action
shape, with separate **Ask first.** sections for bootstrap, gateway/webapp applies, role grant
inside the webapp apply, seed, stage, paid W1/W3 calls, promote, unhealthy fixture, rejected
promotion demonstration, each destroy and purge. Capture Terraform outputs into process
variables, never a tfvars/credential file. Separate measurement-only absence checks from
destructive commands. Prominently state that nightly app-stack cleanup does not cover webapp.
Keep billing estimates and unknowns explicit, with the four-hour target and no unattended
cleanup permission. Findings stay empty/not run offline. Report and README say implementation
exists but live checks are not run, and include the service-placement table without a success claim.
- [ ] **Step 4: Integrate CI without Azure credentials in regular test jobs.** Add webapp fmt,
validate/mocked test on PRs, lock cache key and release-module import check. Keep container/API
jobs and Docker-dependent tests intact. All pinned actions in sensitive workflows remain pinned.
- [ ] **Step 5: Run the complete offline verification below and record actual output summaries.** If a tool/prerequisite is unavailable, document the limitation and use CI for the stated coverage; do not weaken checks or claim a pass. Fix defects at their boundary before continuing.
- [ ] **Step 6: Commit: `docs: add webapp runbook and pending evidence report`.**

## Complete offline verification

Run from the isolated checkout, respecting shell-specific paths:

```powershell
dotnet build
dotnet test tests/ReleaseLens.Core.Tests
dotnet test tests/ReleaseLens.Embedding.Tests
dotnet test tests/ReleaseLens.Functions.Tests
dotnet test tests/ReleaseLens.Web.Tests
eval/.venv/Scripts/python.exe -m pytest tests/infra -q
eval/.venv/Scripts/python.exe scripts/check_workflows.py .github/workflows
```

From `eval`: `.venv/Scripts/python.exe -m pytest -q`. A worktree will not contain the ignored
venv/model files automatically: use the already-authorized local runtime/venv or create an
offline-test environment from the repository's pinned requirements, without copying `.env`
or private Terraform data. Use existing cached model assets for Embedding tests.

For bootstrap and webapp in WSL, copy only versioned `.tf`, lock and mocked `.tftest.hcl` files
into fresh task-owned offline copies, preserve them after the run, and set separate
`$HOME/tfdata/<stack>-offline` data directories. Then run:

```bash
terraform init -backend=false -input=false
terraform fmt -check
terraform validate
terraform test
```

No tfvars, saved plan, provider debug log, live state refresh or live Terraform plan is part
of that validation. Inspect each actual command's exit code, rather than relying on the last
command in a shell block. CI remains responsible for all Docker-dependent suites. Before a PR,
perform the required code review and obtain the owner's explicit push/PR approval. Do not
create the PR in execution handoff just because offline tests passed.

## Self-review and execution handoff

Coverage: spec 1-3 -> all tasks and Task 9 narrative; 4 -> Tasks 1-3; 5 -> Tasks 1,4,5,6;
6 -> Tasks 5,7; 7 -> Task 7; 8 -> Tasks 6,8; 9 -> Tasks 1-3,6-9; 10 -> Task 9 runbook;
11 -> Task 9 checks/review; 12 -> runbook early probes without assumed passes; 13 -> change log
only when evidence and owner approval require it. Every Review Focus input is pinned above.
The live-only unknowns are deliberately not converted into offline test claims.

An executor must preserve task interfaces and red/green evidence, use small descriptive commits,
and inspect repository-local git identity without displaying the address. Live operation remains
outside this plan even after all tasks pass. Recommend native execution for these nine tasks:
their interfaces are closely connected, the tests define their boundaries, and a whole-branch
independent review can evaluate security and release behaviour together. The owner selects the
execution method after reviewing this plan.
