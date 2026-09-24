# Azure OpenAI Keyless Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Azure OpenAI as a keyless third chat provider to ReleaseLens's runtime, and fix the verified findings F1–F6, each of which would otherwise distort the measurement (F7 and F8, in the evaluation harness, are in plan 3).

**Architecture:** The OpenAI wire format (request building, response parsing, completion-filter mapping) moves into one internal codec shared by `OpenAiChatProvider` and a new `AzureOpenAiChatProvider`. The Azure provider owns Azure-only behaviour (deployment name, 429 wait, filtered-prompt 400, `content_filter_results.error`), and a `DelegatingHandler` attaches a cached Entra token. Per-query state (sticky provider, remaining 429 budget) travels on `ChatRequest.Context`; every response carries the pricing identity it is billed under.

**Tech Stack:** .NET 10, xUnit v3, `Azure.Identity`, `Microsoft.Extensions.TimeProvider.Testing` (`FakeTimeProvider`), Testcontainers Postgres (existing `PostgresCollection`) for `QueryAgent` tests.

**Spec:** `docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md` (approved 2026-09-24). This is plan 1 of 3, the C# runtime. Plans 2 (infrastructure and delivery) and 3 (evaluation) follow; this plan leaves the Terraform, workflows and eval harness untouched, and changes the README only to add `"Unpriced": true` to the Ollama snippet (Task 4).

## Global Constraints

- .NET 10; `TreatWarningsAsErrors` is on, so a warning (including an `[Obsolete]` call, CS0618) fails the build.
- NuGet audit must stay clean; packages are managed centrally in `Directory.Packages.props` with transitive pinning on.
- Tests follow the house style: xUnit v3, `StubHttpMessageHandler` (in `tests/ReleaseLens.Ingestion.Tests`), `FakeTimeProvider` for any wait. No test sleeps.
- The `/evidence/*` request and response contract does not change; `releaselens-mcp` depends on it.
- The Azure provider always sends its own deployment name in `model`, and an Azure call is never priced at $0.
- A filtered prompt or completion never falls through to another provider.
- No API key is ever sent to the Azure host, and no Azure key exists in configuration.
- `DefaultAzureCredential` is not used anywhere.
- Token scope defaults to `https://ai.azure.com/.default`; it and the base URL are configurable.
- 429 wait budget: 3 seconds per query (`Agent:RateLimitWaitBudgetMs = 3000`), one retry, `retry-after-ms` wins over `retry-after`.
- Azure pricing identity: provider `azure-openai`, model `gpt-4.1-mini`, version `2025-04-14`, deployment type `Standard`; USD per 1M tokens input 0.44, cached input 0.11, output 1.76 (Azure Retail Prices API, `australiaeast`, read 2026-09-24).
- Departure from spec §4.6 / T-C4, recorded 2026-09-25 (the owner is told at handoff and can overrule it): an OpenAI-direct model whose cached-input rate has not been read and dated (today `gpt-4o` and `gpt-4o-mini`) has its cached tokens billed once, at the input rate, an upper bound; spec §4.6 says the model's own cached rate. A model added from OpenAI's pricing page gets its own cached-input rate from that same page. `gpt-4.1-mini` for evaluation arm O is one of these, so arm O is billed at its own cached rate and the §8 comparison of arm Z against arm O is not skewed.
- Every commit step uses plain `git commit` in the ReleaseLens repository. Its repo-local identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`: never pass `--author`, never run `git config`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No step pushes.
- Postgres-backed tests (classes in [Collection(nameof(PostgresCollection))], including QueryAgentTests, QueryEndpointTests and StartupConfigurationTests) need Docker; where Docker is absent they run in CI on the pull request, and a task is not complete until they have run green somewhere.
- Comments and docs make no claim that is not true of the code as written.

## Review Focus

The five inputs this spec implies but no task would otherwise exercise, most likely to bite first. Each has its test in the task that owns the code.

1. **A malformed or hostile `retry-after-ms`** (a decimal, a negative number, a value in the billions, whitespace): it is treated as absent, so the call falls through at once rather than waiting forever or throwing. Test in Task 9.
2. **A `Chat:Providers` entry that is misspelled, differently cased, duplicated, or an empty list:** startup fails with a message naming the valid providers, rather than silently running with fewer providers. Test in Task 5, on `ChatProviderSelection.Select`. An empty JSON array produces no configuration keys, so through configuration it is indistinguishable from an absent list and takes `ChatOptions`' default; "empty fails" is therefore tested on `ChatProviderSelection.Select([])`, not through configuration.
3. **Two requests needing a token at the same moment the cached one expires:** exactly one refresh happens and both get the new token. Test in Task 7.
4. **A 200 response with an empty `choices` array, or no `usage`:** a clear `InvalidOperationException` naming the provider, not an `IndexOutOfRangeException` or a silent $0. Task 2 owns and tests both halves.
5. **A filtered completion that still carries partial text:** the partial text is never returned to the caller; the answer is the fixed filtered answer. Test in Task 3.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/ReleaseLens.Llm/Providers/ChatContracts.cs` | `ChatRequest` loses `Model` and gains `Context`; `ChatResponse` gains `Pricing`; `ContentFilterStage`, `ContentFilteredException` | 1, 3, 4, 5 |
| `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs` (new, `internal static`) | build the OpenAI-wire payload; parse a response; map `finish_reason: content_filter`; normalise usage | 2, 3, 4 |
| `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs` | OpenAI and OpenAI-compatible endpoints, through the codec | 1, 2, 4 |
| `src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs` | sends its own model; reports its pricing identity | 1, 4 |
| `src/ReleaseLens.Llm/Providers/PricingIdentity.cs` (new) | `PricingIdentity`, `IPricedChatProvider` | 4 |
| `src/ReleaseLens.Llm/Providers/ModelPricing.cs` | rates by pricing identity, per-model cached rates, `EnsurePriced` | 4 |
| `src/ReleaseLens.Llm/Providers/QueryContext.cs` (new) | per-query sticky provider and remaining 429 budget | 5 |
| `src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs` | starts at the sticky provider; never returns to earlier ones | 3, 5 |
| `src/ReleaseLens.Llm/Providers/ChatProviderSelection.cs` (new) | `ChatOptions`; resolve `Chat:Providers` names to providers, failing loudly | 5 |
| `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiOptions.cs` (new) | configuration for the Azure provider | 7 |
| `src/ReleaseLens.Llm/Providers/Azure/EntraTokenCache.cs` (new) | singleton token cache, refresh before expiry, single refresh under contention | 7 |
| `src/ReleaseLens.Llm/Providers/Azure/EntraTokenHandler.cs` (new) | `DelegatingHandler` attaching the bearer token; credential failure becomes `HttpRequestException` | 7 |
| `src/ReleaseLens.Llm/Providers/Azure/AzureCredentialFactory.cs` (new) | `ManagedIdentity` or `AzureCli` credential, capped retry, fail on missing client ID | 7 |
| `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs` (new) | Azure v1 endpoint, deployment name, filtered-prompt 400, 429 wait | 8, 9 |
| `src/ReleaseLens.Llm/Agent/AgentModels.cs` | `AgentOptions` loses `Model`, gains `RateLimitWaitBudgetMs`; `AgentMetadata` gains `Providers`, `Filtered`; `FilteredOutcome` | 1, 5, 6 |
| `src/ReleaseLens.Llm/Agent/QueryAgent.cs` | builds requests with a `QueryContext`; prices per iteration; records providers; handles the filtered outcome | 1, 4, 5, 6 |
| `src/ReleaseLens.Api/Contracts.cs` | `QueryMetadataDto` gains `Providers` and `Filtered` (additive) | 5, 6 |
| `src/ReleaseLens.Api/Program.cs` | provider registration by name, Azure client with token handler, startup pricing check | 5, 6, 10 |
| `src/ReleaseLens.Api/appsettings.json` | `AzureOpenAi` section; `Agent:Model` removed. No `Chat` section: `Chat:Providers` lives only in environment-specific configuration | 1, 10 |
| `README.md` | the "Fully local, no egress" Ollama snippet gains `"Unpriced": true` | 4 |
| `Directory.Packages.props`, `src/ReleaseLens.Llm/ReleaseLens.Llm.csproj` | `Azure.Identity`; Microsoft.Extensions pins raised as `Azure.Core` requires | 7 |
| `tests/ReleaseLens.Llm.Tests/ReleaseLens.Llm.Tests.csproj` | references `Microsoft.Extensions.TimeProvider.Testing` | 7 |

---

## Interface contract

Every task implements exactly these names and shapes. A task may add private members; it may not rename or reshape anything listed here. The contract fixes signatures and parameter order, not constructor syntax: where a primary-constructor parameter would go unread (CS9113, an error under `TreatWarningsAsErrors`), the class uses an ordinary constructor with the same parameters in the same order. `AzureOpenAiChatProvider` does, because nothing reads `time` until Task 9.

```csharp
// ---- ChatContracts.cs (namespace ReleaseLens.Llm.Providers) ----
public sealed record ChatRequest(
    string SystemPrompt,
    IReadOnlyList<ChatMessage> Messages,
    IReadOnlyList<ToolDefinition> Tools,
    int MaxTokens)                                   // Task 1 removes `string Model`
{
    public QueryContext? Context { get; init; }      // Task 5
}

public sealed record ChatResponse(
    string? Text,
    IReadOnlyList<ToolCall> ToolCalls,
    TokenUsage Usage,
    string StopReason,
    string Model,                                    // recorded, never priced once Pricing is set
    string Provider,
    PricingIdentity? Pricing = null);                // Task 4; null only for test fakes

public enum ContentFilterStage { Prompt, Completion }                        // Task 3

// Task 3 creates it as (providerName, stage, usage). Task 4 adds `PricingIdentity? pricing = null`
// and the Pricing property.
public sealed class ContentFilteredException(                                // Task 3
    string providerName, ContentFilterStage stage, TokenUsage usage, PricingIdentity? pricing = null)
    : Exception($"{providerName} content filter blocked the {stage.ToString().ToLowerInvariant()}.")
{
    public string ProviderName { get; } = providerName;
    public ContentFilterStage Stage { get; } = stage;
    public TokenUsage Usage { get; } = usage;
    public PricingIdentity? Pricing { get; } = pricing;
}

// ---- PricingIdentity.cs (Task 4) ----
public sealed record PricingIdentity(
    string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false);

public interface IPricedChatProvider : IChatProvider { PricingIdentity Pricing { get; } }

// ---- ModelPricing.cs (Task 4) ----
public static class ModelPricing
{
    public static decimal CostUsd(string model, TokenUsage usage, DateOnly asOf);          // existing; kept for fakes
    public static decimal CostUsd(PricingIdentity identity, TokenUsage usage, DateOnly asOf);
    public static bool HasRate(PricingIdentity identity, DateOnly asOf);
    public static void EnsurePriced(IEnumerable<PricingIdentity> identities, DateOnly asOf); // throws InvalidOperationException
    public static int MinimumCacheablePrefixTokens(string model);                          // existing, unchanged
}
// TokenUsage keeps its shape. From Task 4, InputTokens means UNCACHED input on every provider:
// the OpenAI-wire codec reports InputTokens = prompt_tokens - cached_tokens and CacheReadInputTokens = cached_tokens.

// ---- OpenAiWireFormat.cs (internal static; Tasks 2-4) ----
internal static class OpenAiWireFormat
{
    public static JsonObject BuildPayload(ChatRequest request, string model);
    public static TokenUsage ParseUsage(JsonElement root);
    // Tasks 2-3 form: Parse(JsonElement root, string providerName, string fallbackModel).
    // Task 4 adds `PricingIdentity? pricing` (no default) and updates every call site and test.
    public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel, PricingIdentity? pricing);
    // Inside Parse the order is fixed:
    //   (1) choices missing, null or empty -> InvalidOperationException(
    //         $"{providerName} returned a completion with no choices.")
    //   (2) usage absent, null or not a JSON object -> InvalidOperationException(
    //         $"{providerName} returned a completion with no usage.")
    //       This holds for every provider that uses the codec (openai, azure-openai): a success
    //       with no usage would otherwise be priced at $0 silently. A filtered completion with no
    //       usage is therefore an InvalidOperationException, not a filter outcome (accepted).
    //   (3) choices[0].finish_reason == "content_filter" -> ContentFilteredException(
    //         providerName, Completion, usage, pricing)   (Task 3; pricing from Task 4)
    //   (4) read the message.
    // ParseUsage has no guard of its own; the guard is in Parse.
}

// ---- QueryContext.cs (Task 5) ----
public sealed class QueryContext(TimeSpan rateLimitWaitBudget)
{
    public string? LastProvider { get; set; }
    public TimeSpan RateLimitWaitRemaining { get; private set; } = rateLimitWaitBudget;
    public bool TrySpendWait(TimeSpan wait);   // true and subtracts when wait <= remaining; otherwise false, unchanged.
                                               // Throws ArgumentOutOfRangeException for a negative wait; zero is accepted.
}

// ---- ChatProviderSelection.cs (Task 5) ----
public sealed class ChatOptions
{
    public const string SectionName = "Chat";
    public List<string> Providers { get; set; } = ["anthropic", "openai"];
}
public static class ChatProviderSelection
{
    // Resolves names (exact, lowercase) against factories keyed by provider Name. Validates
    // every name before running any factory, and throws InvalidOperationException naming the
    // valid providers on an empty list, an unknown name (a differently cased one included), or
    // a duplicate. Runs only the factories of the listed names, so an unlisted provider is
    // never built. Returns the providers in configured order.
    public static IReadOnlyList<IChatProvider> Select(
        IReadOnlyList<string> names, IReadOnlyDictionary<string, Func<IChatProvider>> factories);
}

// ---- AgentModels.cs (namespace ReleaseLens.Llm.Agent) ----
public sealed class AgentOptions
{
    public const string SectionName = "Agent";
    public int MaxIterations { get; set; } = 6;
    public int MaxTokens { get; set; } = 2048;
    public int SeedRetrievalK { get; set; } = 8;
    public int RateLimitWaitBudgetMs { get; set; } = 3000;   // Task 5; `Model` removed in Task 1
}
public sealed record FilteredOutcome(string Stage, string Provider);   // Task 6; Stage is "prompt" or "completion"
// AgentMetadata gains two trailing parameters (existing ones unchanged):
//   IReadOnlyList<string>? Providers = null,      // Task 5: distinct, in first-answer order
//   FilteredOutcome? Filtered = null              // Task 6
// AgentMetadata.Model is "none" when no provider answered (Task 1), matching Provider = "none";
// usage is zero on that path, so nothing is priced under it.
// On a filtered answer (Task 6): Provider = the blocked call's provider (exception.ProviderName),
// Model = exception.Pricing?.Model ?? modelName, Filtered = new FilteredOutcome(stage, provider).
// QueryAgent.AnswerAsync locals other tasks name: `cost` (decimal) and `pricedOn` (DateOnly)
// from Task 4; `context` (QueryContext) and `providers` (List<string>) from Task 5. Task 6 uses
// `providers`.

// ---- Contracts.cs (namespace ReleaseLens.Api) ----
// QueryMetadataDto gains two trailing parameters after ElapsedMs:
//   IReadOnlyList<string>? Providers = null,
//   [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] FilteredOutcomeDto? Filtered = null
public sealed record FilteredOutcomeDto(string Stage, string Provider);

// ---- Azure/AzureOpenAiOptions.cs (Task 7; namespace ReleaseLens.Llm.Providers.Azure) ----
public enum AzureCredentialKind { AzureCli, ManagedIdentity }
public sealed class AzureOpenAiOptions
{
    public const string SectionName = "AzureOpenAi";
    public string BaseUrl { get; set; } = string.Empty;          // e.g. https://<subdomain>.openai.azure.com/openai/v1/
    public string Deployment { get; set; } = string.Empty;
    public string Model { get; set; } = "gpt-4.1-mini";          // pricing identity only; never sent
    public string ModelVersion { get; set; } = "2025-04-14";
    public string DeploymentType { get; set; } = "Standard";
    public string TokenScope { get; set; } = "https://ai.azure.com/.default";
    public AzureCredentialKind Credential { get; set; } = AzureCredentialKind.AzureCli;
    public string? TenantId { get; set; }
    public string? ClientId { get; set; }                         // Program.cs fills it from AZURE_CLIENT_ID
}

// ---- Azure/EntraTokenCache.cs, EntraTokenHandler.cs, AzureCredentialFactory.cs (Task 7) ----
public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)
{
    public static readonly TimeSpan RefreshBeforeExpiry = TimeSpan.FromMinutes(5);
    public ValueTask<string> GetTokenAsync(CancellationToken cancellationToken);
}
public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler { }
// Sets Authorization: Bearer <token> on every request; an AuthenticationFailedException
// (CredentialUnavailableException derives from it) is rethrown as HttpRequestException, which
// ProviderHttp already maps to ProviderUnavailableException.
public static class AzureCredentialFactory
{
    // Throws InvalidOperationException on ManagedIdentity with no ClientId (the message names
    // AZURE_CLIENT_ID) and on AzureCli with a null, empty or whitespace TenantId (the message
    // names AzureOpenAi:TenantId; spec 4.7, "AzureCliCredential with the tenant pinned").
    public static TokenCredential Create(AzureOpenAiOptions options);

    // What Create builds the ManagedIdentityCredential with. Retry: MaxRetries 1,
    // RetryMode.Fixed, Delay and MaxDelay 200 ms. Internal (InternalsVisibleTo
    // ReleaseLens.Llm.Tests) so the cap is tested.
    internal static ManagedIdentityCredentialOptions ManagedIdentityOptions(AzureOpenAiOptions options);
}

// ---- Azure/AzureOpenAiChatProvider.cs (Tasks 8-9) ----
// Written with an ordinary constructor of this signature (see the note above the contract).
public sealed class AzureOpenAiChatProvider(
    HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger)
    : IPricedChatProvider
{
    public string Name => "azure-openai";
    public PricingIdentity Pricing { get; }      // ("azure-openai", Model, ModelVersion, DeploymentType)
    public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken);
}
```

The concrete providers' pricing identities: `AnthropicChatProvider` → `("anthropic", options.Model)`; `OpenAiChatProvider` → `("openai", options.Model, Unpriced: options.Unpriced)`, where `OpenAiOptions` gains `bool Unpriced` (default `false`) for local OpenAI-compatible runtimes such as Ollama; `AzureOpenAiChatProvider` as above. Both existing providers implement `IPricedChatProvider` from Task 4.

---

## Task overview

1. **Providers send their own model (fixes F1).** Remove `ChatRequest.Model` and `AgentOptions.Model`; Anthropic and OpenAI send `options.Model`; update every construction site and test fake; remove `Agent:Model` from appsettings. Tests: T-F1.
2. **Extract the shared OpenAI wire codec, keep tool errors (fixes F6).** Move payload building and parsing from `OpenAiChatProvider` into `OpenAiWireFormat` with no behaviour change except: a tool result with `IsError` is sent as `"Error: " + content`; a 200 with missing or empty `choices`, or with no `usage` object, throws a clear `InvalidOperationException` naming the provider. Tests: T-F5, both halves of Review Focus 4, existing OpenAI tests stay green.
3. **The content-filter outcome (fixes F3 on the OpenAI path).** Add `ContentFilterStage`, `ContentFilteredException`; the codec maps `finish_reason: content_filter`; `FallbackChatProvider` does not catch it. Tests: T-F3, a fallback test proving no other provider is called, Review Focus 5 (partial text never surfaced — asserted where the exception carries no text).
4. **Price by pricing identity, per iteration (fixes F2, F4, and the pricing half of F5).** `PricingIdentity`, `IPricedChatProvider`, rates keyed by identity with per-model cached rates, `EnsurePriced`, `ChatResponse.Pricing`, codec usage normalisation, `QueryAgent` summing cost per iteration. The Azure identity's rates are added here. OpenAI-direct cached rates for `gpt-4o` and `gpt-4o-mini` have not been read and dated, so, as a recorded departure from spec §4.6 (see Global Constraints), their cached tokens are priced at the model's input rate (an upper bound), with a comment saying so; a model added from OpenAI's pricing page gets its own cached rate. T-C4 at the model's own cached rate is met on the Azure identity (`AzureWire_CachedTokens_AreBilledOnceAtTheCachedRate`). On the OpenAI wire, Azure included, `tokensIn` and the daily token budget now count uncached input only, as on Anthropic. README.md's Ollama snippet gains `"Unpriced": true`, without which that setup fails pricing. Tests: T-C1, T-C2, T-C3, T-C4, T-C5, T-F2.
5. **Per-query context, sticky fallback, configurable order (fixes the rest of F5).** `QueryContext`, `ChatRequest.Context`, `FallbackChatProvider` stickiness, `ChatOptions`/`ChatProviderSelection`, `AgentOptions.RateLimitWaitBudgetMs`, `AgentMetadata.Providers` and the DTO field. Tests: T-F4, T-A1, the `providers` half of T-A2, Review Focus 2.
6. **The filtered outcome, end to end.** `QueryAgent` catches `ContentFilteredException`, returns the fixed filtered answer with `Filtered` set, and counts every iteration's usage and cost including the filtered completion's; `QueryMetadataDto.Filtered`. Tests: T-C6, the `filtered` half of T-A2, T-A3 (`/evidence/*` unchanged).
7. **Entra token plumbing.** `Azure.Identity` and the pin bumps it forces; `AzureOpenAiOptions`, `EntraTokenCache`, `EntraTokenHandler`, `AzureCredentialFactory` (managed identity with capped retry; Azure CLI with pinned tenant, refused without one). Tests: T-P1, T-P2, T-P3, T-P3a, the retry cap, the tenant guard, Review Focus 3.
8. **The Azure provider.** `AzureOpenAiChatProvider` without the 429 wait: v1 URL, deployment name in `model`, no key header, filtered-prompt 400 in both envelope shapes, `content_filter_results.error` recorded not filtered. Tests: T-P4, T-P5, T-P6, T-P7 (byte-identical), T-P12, T-P13 (Azure), T-P14, T-P15, T-P16.
9. **The 429 wait budget.** Honour `retry-after-ms`, then `retry-after`, within `QueryContext.RateLimitWaitRemaining`, one retry, through `TimeProvider`. Tests: T-P8, T-P8a, T-P8b, T-P9, T-P10, T-P11, Review Focus 1.
10. **Wiring.** `Program.cs` builds providers by name from `Chat:Providers`, registers the Azure typed client with `EntraTokenHandler`, fills `AzureOpenAiOptions.ClientId` from `AZURE_CLIENT_ID`, and calls `ModelPricing.EnsurePriced` on the selected providers at startup; `appsettings.json` gains `AzureOpenAi` and no `Chat` section. Tests: the app starts with the default configuration; startup fails with an unknown provider name, with a priced provider that has no rate, with an incomplete `AzureOpenAi` section, and with azure-openai listed and no client ID or no tenant. Through `/query`, T-A1 through configuration and T-P3 end to end: `Chat:Providers:0=azure-openai` alone gives a one-provider chain, and a credential that cannot produce a token degrades the answer, naming only `azure-openai`, instead of failing it.

---

### Task 1: Providers send their own model (fixes F1)

**Files:**
- Modify: `src/ReleaseLens.Llm/Providers/ChatContracts.cs` (record `ChatRequest`, lines 32-37: remove `string Model`)
- Modify: `src/ReleaseLens.Llm/Agent/AgentModels.cs` (class `AgentOptions`, lines 6-14: remove `Model`)
- Modify: `src/ReleaseLens.Llm/Agent/QueryAgent.cs` (`AnswerAsync`: line 63 `var modelName = options.Model;`, lines 74-79 the loop's `new ChatRequest(`, lines 137-139 the final no-tools `new ChatRequest(`)
- Modify: `src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs` (`BuildPayload`, line 57: `["model"]`)
- Modify: `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs` (`BuildPayload`, line 122: `["model"]`)
- Modify: `src/ReleaseLens.Api/appsettings.json` (line 6, the `Agent` section: remove `"Model"`)
- Test: `tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs` (new T-F1 test; `Request()` helper, line 27-28)
- Test: `tests/ReleaseLens.Llm.Tests/AnthropicChatProviderTests.cs` (`Create` helper line 15-17, new test, `Request()` helper line 19-24, `Complete_SerialisesToolResultsAsUserContentBlocks` line 141)
- Test: `tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs` (`Request()` helper line 19-22, `Complete_SerialisesToolResultsAsToolRoleMessages` line 141)
- Test: `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs` (`Build` line 203; `Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised` lines 313-328)

Every construction site was found by grepping `src` and `tests` for `ChatRequest`, `new ChatRequest(`, `AgentOptions`, `options.Model` and `Agent:Model`/`Agent__Model`. The complete list: the two in `QueryAgent.AnswerAsync`, the two request helpers and two inline requests in the provider tests, the `Request()` helper in `FallbackChatProviderTests`, and `AgentOptions { Model = ... }` in `QueryAgentTests.Build`. `ScriptedChatProvider` and `SpyChatProvider` in `tests/ReleaseLens.Api.Tests` implement `IChatProvider` but never construct a `ChatRequest`, so they need no change. `Program.cs` binds `AgentOptions` from configuration but never reads `Model`, so it needs no change either. No other config file, workflow, Terraform file or eval script sets `Agent:Model`.

**Interfaces:**
- Consumes: none (first task).
- Produces:
  - `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens)`. `string Model` is removed. Task 5 adds `public QueryContext? Context { get; init; }` in a body.
  - `AgentOptions` with `SectionName`, `MaxIterations`, `MaxTokens`, `SeedRetrievalK`. `Model` is removed. Task 5 adds `RateLimitWaitBudgetMs`.
  - `AnthropicChatProvider` and `OpenAiChatProvider` send `options.Model` in the payload's `model`, whatever the request is. Task 2 moves the OpenAI line into `OpenAiWireFormat.BuildPayload(request, model)`, which `OpenAiChatProvider` calls with `_options.Model`.
  - `AgentMetadata.Model` is `"none"` when no provider answered, matching `Provider`, which is already `"none"` in that case.

- [ ] **Step 1: Write the failing tests**

These tests are written against the **current** `ChatRequest` shape, so they compile and fail on behaviour. The failure is F1 itself. After Step 3 they are adjusted to the new shape.

Replace the whole of `tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs` with:

```csharp
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class FallbackChatProviderTests
{
    private sealed class ScriptedProvider(string name, Func<ChatResponse> behaviour) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(behaviour());
        }
    }

    private static ChatResponse Ok(string provider) =>
        new("answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", provider);

    private static ChatRequest Request() =>
        new("system", [ChatMessage.User("q")], [], "model", 512);

    [Fact]
    public async Task Complete_PrimaryHealthy_NeverCallsTheSecondary()
    {
        var primary = new ScriptedProvider("anthropic", () => Ok("anthropic"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        var response = await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("anthropic", response.Provider);
        Assert.Equal(1, primary.Calls);
        Assert.Equal(0, secondary.Calls);
    }

    [Fact]
    public async Task Complete_PrimaryUnavailable_FallsThroughToTheSecondary()
    {
        var primary = new ScriptedProvider("anthropic",
            () => throw new ProviderUnavailableException("anthropic", "overloaded"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        var response = await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("openai", response.Provider);
        Assert.Equal(1, secondary.Calls);
    }

    /// <summary>
    /// T-F1. The chain hands every provider the same request, so a model named on the request
    /// reached whichever provider answered: an Anthropic outage sent "claude-sonnet-5" to
    /// OpenAI. Real providers over stubbed HTTP, because the bug was in the body on the wire.
    /// </summary>
    [Fact]
    public async Task Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel()
    {
        var anthropicHttp = new StubHttpMessageHandler().Enqueue(
            HttpStatusCode.ServiceUnavailable, """{"type":"error","error":{"type":"overloaded_error"}}""");
        var openAiHttp = new StubHttpMessageHandler().EnqueueJson("""
            {
              "id": "chatcmpl-1",
              "model": "gpt-4o",
              "choices": [{ "index": 0, "finish_reason": "stop",
                "message": { "role": "assistant", "content": "answered by openai" } }],
              "usage": { "prompt_tokens": 10, "completion_tokens": 5 }
            }
            """);

        var anthropic = new AnthropicChatProvider(
            new HttpClient(anthropicHttp) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = "claude-sonnet-5" });
        var openAiOptions = new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o" };
        var openAi = new OpenAiChatProvider(
            new HttpClient(openAiHttp) { BaseAddress = new Uri(openAiOptions.BaseUrl) }, openAiOptions);

        // The request QueryAgent built before this fix: Agent:Model on every call.
        var request = new ChatRequest("system", [ChatMessage.User("q")], [], "claude-sonnet-5", 512);

        var response = await new FallbackChatProvider([anthropic, openAi], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(request, TestContext.Current.CancellationToken);

        Assert.Equal("openai", response.Provider);
        Assert.Single(anthropicHttp.Requests);

        var body = await Assert.Single(openAiHttp.Requests).Content!
            .ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        Assert.Equal(openAiOptions.Model, document.RootElement.GetProperty("model").GetString());
    }

    [Fact]
    public async Task Complete_AllUnavailable_ThrowsAllProvidersUnavailable()
    {
        var primary = new ScriptedProvider("anthropic",
            () => throw new ProviderUnavailableException("anthropic", "overloaded"));
        var secondary = new ScriptedProvider("openai",
            () => throw new ProviderUnavailableException("openai", "502"));

        var exception = await Assert.ThrowsAsync<AllProvidersUnavailableException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(["anthropic", "openai"], exception.AttemptedProviders);
    }

    [Fact]
    public async Task Complete_NonTransientError_DoesNotFallThrough()
    {
        // A 400 is our bug. Retrying it on another provider hides it and wastes money.
        var primary = new ScriptedProvider("anthropic",
            () => throw new InvalidOperationException("malformed tool schema"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(0, secondary.Calls);
    }

    [Fact]
    public void Construct_WithNoProviders_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new FallbackChatProvider([], NullLogger<FallbackChatProvider>.Instance));
    }
}
```

In `tests/ReleaseLens.Llm.Tests/AnthropicChatProviderTests.cs`, replace the `Create` helper (lines 15-17) with:

```csharp
    private static AnthropicChatProvider Create(StubHttpMessageHandler handler, string model = "claude-sonnet-5") =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = model });
```

Then insert this test directly after `Complete_SendsApiKeyAndVersionHeaders` and before `Complete_ReturnsTextAndUsageIncludingCacheReads`. The file already has `using System.Text.Json;`. `Request()` still names `"claude-sonnet-5"`, so today the request's model wins and the test fails:

```csharp
    [Fact]
    public async Task Complete_SendsTheModelItIsConfiguredWith()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler, model: "claude-haiku-4-5-20251001")
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        Assert.Equal("claude-haiku-4-5-20251001", document.RootElement.GetProperty("model").GetString());
    }
```

In `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs`, replace `Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised` (lines 313-328) with:

```csharp
    [Fact]
    public async Task Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised()
    {
        var (factory, tenantId) = await SeedAsync("agent-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw new AllProvidersUnavailableException(["anthropic", "openai"])]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.NotNull(answer.Metadata.DegradedReason);
        Assert.NotEmpty(answer.Citations);
        Assert.Contains("sha_agent", answer.Answer, StringComparison.Ordinal);

        // No provider answered, so no model did either; the agent has no model of its own.
        Assert.Equal("none", answer.Metadata.Provider);
        Assert.Equal("none", answer.Metadata.Model);
    }
```

- [ ] **Step 2: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderTests|FullyQualifiedName~AnthropicChatProviderTests"
```

Expected: 2 failed, 14 passed.
- `Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel` fails with `Assert.Equal() Failure: Strings differ`, `Expected: "gpt-4o"`, `Actual: "claude-sonnet-5"`. That is F1, reproduced.
- `Complete_SendsTheModelItIsConfiguredWith` fails with `Expected: "claude-haiku-4-5-20251001"`, `Actual: "claude-sonnet-5"`.

This result was observed on a scratch copy of `main` at `95192d5`.

With Docker running:

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryAgentTests.Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised"
```

Expected: 1 failed. `Assert.Equal("none", answer.Metadata.Model)` gets `Actual: "claude-sonnet-5"`, which is `AgentOptions.Model`. This test uses the Postgres Testcontainers collection. Without Docker it fails in the fixture instead, which proves nothing, so run it only where Docker is available.

- [ ] **Step 3: Implement**

`src/ReleaseLens.Llm/Providers/ChatContracts.cs`: replace the `ChatRequest` record (lines 32-37) with:

```csharp
/// <summary>
/// What to ask, never which model to ask it of: every provider sends the model its own options
/// name. A model carried here reached whichever provider answered, so an Anthropic outage sent
/// an Anthropic model name to OpenAI.
/// </summary>
public sealed record ChatRequest(
    string SystemPrompt,
    IReadOnlyList<ChatMessage> Messages,
    IReadOnlyList<ToolDefinition> Tools,
    int MaxTokens);
```

`src/ReleaseLens.Llm/Agent/AgentModels.cs`: replace `AgentOptions` (lines 6-14) with:

```csharp
public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    public int MaxIterations { get; set; } = 6;
    public int MaxTokens { get; set; } = 2048;
    public int SeedRetrievalK { get; set; } = 8;
}
```

`src/ReleaseLens.Llm/Agent/QueryAgent.cs`, in `AnswerAsync`, make three edits. Nothing else in the method changes. The method runs to 186 lines, and Tasks 4-6 rewrite its loop, so the edits are given as exact replacements rather than a full copy.

(a) Lines 62-63. Replace

```csharp
        var providerName = provider.Name;
        var modelName = options.Model;
```

with

```csharp
        var providerName = provider.Name;

        // The agent has no model of its own; until a provider answers there is none to report.
        var modelName = "none";
```

(b) Lines 74-79, the loop's request. Replace

```csharp
                var response = await provider.CompleteAsync(new ChatRequest(
                    systemPrompt,
                    messages,
                    tools.Definitions,
                    options.Model,
                    options.MaxTokens), cancellationToken);
```

with

```csharp
                var response = await provider.CompleteAsync(new ChatRequest(
                    systemPrompt,
                    messages,
                    tools.Definitions,
                    options.MaxTokens), cancellationToken);
```

(c) Lines 137-139, the final no-tools request. Replace

```csharp
                    var final = await provider.CompleteAsync(new ChatRequest(
                        systemPrompt,
                        messages, [], options.Model, options.MaxTokens), cancellationToken);
```

with

```csharp
                    var final = await provider.CompleteAsync(new ChatRequest(
                        systemPrompt,
                        messages, [], options.MaxTokens), cancellationToken);
```

`src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs`: replace `BuildPayload` (lines 53-98) with the following. The only change is `["model"] = _options.Model`:

```csharp
    private JsonObject BuildPayload(ChatRequest request)
    {
        var payload = new JsonObject
        {
            ["model"] = _options.Model,
            ["max_tokens"] = request.MaxTokens,

            // System prompt as a block array so cache_control can be attached. Whether it
            // actually caches depends on the model's minimum prefix — check
            // usage.cache_read_input_tokens rather than assuming.
            ["system"] = new JsonArray
            {
                new JsonObject
                {
                    ["type"] = "text",
                    ["text"] = request.SystemPrompt,
                    ["cache_control"] = new JsonObject { ["type"] = "ephemeral" }
                }
            }
        };

        if (request.Tools.Count > 0)
        {
            var tools = new JsonArray();
            foreach (var tool in request.Tools)
            {
                tools.Add(new JsonObject
                {
                    ["name"] = tool.Name,
                    ["description"] = tool.Description,
                    ["input_schema"] = JsonNode.Parse(tool.JsonSchema.GetRawText())
                });
            }

            payload["tools"] = tools;
        }

        var messages = new JsonArray();
        foreach (var message in request.Messages)
        {
            messages.Add(BuildMessage(message));
        }

        payload["messages"] = messages;
        return payload;
    }
```

`src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs`: replace `BuildPayload` (lines 66-148) with the following. The only change is `["model"] = _options.Model`. Task 2 moves this method into `OpenAiWireFormat.BuildPayload(request, model)`.

```csharp
    private JsonObject BuildPayload(ChatRequest request)
    {
        var messages = new JsonArray
        {
            new JsonObject { ["role"] = "system", ["content"] = request.SystemPrompt }
        };

        foreach (var message in request.Messages)
        {
            if (message.ToolResults is { Count: > 0 })
            {
                // Each tool result is its own message in the OpenAI format.
                foreach (var result in message.ToolResults)
                {
                    messages.Add(new JsonObject
                    {
                        ["role"] = "tool",
                        ["tool_call_id"] = result.ToolCallId,
                        ["content"] = result.Content
                    });
                }

                continue;
            }

            var entry = new JsonObject
            {
                ["role"] = message.Role == ChatRole.Assistant ? "assistant" : "user",
                ["content"] = message.Text
            };

            if (message.ToolCalls is { Count: > 0 })
            {
                var calls = new JsonArray();
                foreach (var call in message.ToolCalls)
                {
                    calls.Add(new JsonObject
                    {
                        ["id"] = call.Id,
                        ["type"] = "function",
                        ["function"] = new JsonObject
                        {
                            ["name"] = call.Name,
                            ["arguments"] = call.Arguments.GetRawText()
                        }
                    });
                }

                entry["tool_calls"] = calls;
            }

            messages.Add(entry);
        }

        var payload = new JsonObject
        {
            ["model"] = _options.Model,
            ["max_tokens"] = request.MaxTokens,
            ["messages"] = messages
        };

        if (request.Tools.Count > 0)
        {
            var tools = new JsonArray();
            foreach (var tool in request.Tools)
            {
                tools.Add(new JsonObject
                {
                    ["type"] = "function",
                    ["function"] = new JsonObject
                    {
                        ["name"] = tool.Name,
                        ["description"] = tool.Description,
                        ["parameters"] = JsonNode.Parse(tool.JsonSchema.GetRawText())
                    }
                });
            }

            payload["tools"] = tools;
        }

        return payload;
    }
```

`src/ReleaseLens.Api/appsettings.json`: the whole file becomes the following. Only the `Agent` line changes:

```json
{
  "Logging": { "LogLevel": { "Default": "Information", "Microsoft.AspNetCore": "Warning" } },
  "AllowedHosts": "*",
  "Anthropic": { "Model": "claude-sonnet-5", "BaseUrl": "https://api.anthropic.com/", "MaxTokens": 2048 },
  "OpenAi": { "Model": "gpt-4o", "BaseUrl": "https://api.openai.com/v1/", "MaxTokens": 2048 },
  "Agent": { "MaxIterations": 6, "MaxTokens": 2048, "SeedRetrievalK": 8 },
  "Embedding": { "MaxSequenceLength": 512, "BatchSize": 32 }
}
```

Now move the tests to the new `ChatRequest` shape. Without these edits the test project does not compile.

`tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs`: replace the `Request()` helper with:

```csharp
    private static ChatRequest Request() =>
        new("system", [ChatMessage.User("q")], [], 512);
```

and, in `Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel`, replace

```csharp
        // The request QueryAgent built before this fix: Agent:Model on every call.
        var request = new ChatRequest("system", [ChatMessage.User("q")], [], "claude-sonnet-5", 512);

        var response = await new FallbackChatProvider([anthropic, openAi], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(request, TestContext.Current.CancellationToken);
```

with

```csharp
        var response = await new FallbackChatProvider([anthropic, openAi], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);
```

`tests/ReleaseLens.Llm.Tests/AnthropicChatProviderTests.cs`: replace the `Request` helper (lines 19-24) with

```csharp
    private static ChatRequest Request(params ToolDefinition[] tools) => new(
        SystemPrompt: "You answer questions about a repository.",
        Messages: [ChatMessage.User("What changed in release 1.30?")],
        Tools: tools,
        MaxTokens: 1024);
```

and, in `Complete_SerialisesToolResultsAsUserContentBlocks`, replace the request's last line `            [SearchTool()], "claude-sonnet-5", 1024);` with

```csharp
            [SearchTool()], 1024);
```

`tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs`: replace the `Request` helper (lines 19-22) with

```csharp
    private static ChatRequest Request(params ToolDefinition[] tools) => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        tools, 1024);
```

and, in `Complete_SerialisesToolResultsAsToolRoleMessages`, replace the request's last line `            [SearchTool()], "gpt-4o", 1024);` with

```csharp
            [SearchTool()], 1024);
```

`tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs`: in `Build` (line 203), replace `        new AgentOptions { Model = "claude-sonnet-5", MaxIterations = 6 },` with

```csharp
        new AgentOptions { MaxIterations = 6 },
```

- [ ] **Step 4: Run them and watch them pass**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderTests|FullyQualifiedName~AnthropicChatProviderTests|FullyQualifiedName~OpenAiChatProviderTests"
```

Expected:
- the build: `Build succeeded.`, `0 Warning(s)`, `0 Error(s)`. A `Model` argument left anywhere is a compile error here.
- the tests: all pass, including `Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel` and `Complete_SendsTheModelItIsConfiguredWith`.

With Docker running:

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryAgentTests.Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised"
```

Expected: 1 passed.

- [ ] **Step 5: Run the whole affected test projects**

```
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected: all pass. Both projects need Docker:
- In `ReleaseLens.Llm.Tests`, `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` use the Postgres Testcontainers collection.
- In `ReleaseLens.Api.Tests`, `QueryEndpointTests` and `EvidenceEndpointTests` use it. Only `GoldenSetValidationTests` there does not.

Without Docker, run only the tests that need no database:

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderTests|FullyQualifiedName~AnthropicChatProviderTests|FullyQualifiedName~OpenAiChatProviderTests|FullyQualifiedName~ModelPricingTests|FullyQualifiedName~JsonArgsTests|FullyQualifiedName~SystemPromptTests|FullyQualifiedName~TelemetryTests"
```

Expected: `Failed: 0`, with `Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel` and `Complete_SendsTheModelItIsConfiguredWith` among the passed tests. Say in the task report that the Docker-backed tests were not run; they run in CI on the pull request, and the task is not complete until they have run green somewhere.

- [ ] **Step 6: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/ChatContracts.cs src/ReleaseLens.Llm/Agent/AgentModels.cs src/ReleaseLens.Llm/Agent/QueryAgent.cs src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs src/ReleaseLens.Api/appsettings.json tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs tests/ReleaseLens.Llm.Tests/AnthropicChatProviderTests.cs tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs
git commit -F- <<'EOF'
fix(llm): each provider sends its own configured model (F1)

ChatRequest carried Agent:Model to whichever provider answered, so an
Anthropic outage sent "claude-sonnet-5" to OpenAI. ChatRequest.Model and
AgentOptions.Model are removed; Anthropic and OpenAI send options.Model.
With no provider answering, metadata.model is now "none", matching
metadata.provider, instead of the agent's configured default.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

(In PowerShell, use `git commit -m @'` … `'@` with the same message, with the closing `'@` at column 0.)

---

### Task 2: Extract the shared OpenAI wire codec, keep tool errors (fixes F6)

This task starts from the state Task 1 leaves: `ChatRequest` is `(SystemPrompt, Messages, Tools, MaxTokens)` with no `Model`, and `OpenAiChatProvider`'s private `BuildPayload` writes `["model"] = _options.Model`. It moves the OpenAI wire code out of `OpenAiChatProvider` into `OpenAiWireFormat`, unchanged except for three behaviours:

- a tool result with `IsError == true` is sent as `"Error: " + content`. The chat-completions `tool` message has no error field, so the flag was being dropped (F6).
- a 200 whose `choices` is missing, `null` or empty throws `InvalidOperationException` with the message `"<provider> returned a completion with no choices."`, instead of `KeyNotFoundException` / `IndexOutOfRangeException`.
- a 200 whose `usage` is absent, `null` or not a JSON object throws `InvalidOperationException` with the message `"<provider> returned a completion with no usage."`. Today such a reply counts as zero tokens, so a success would be priced at $0 without anyone noticing. This holds for every provider that uses the codec (`openai` now, `azure-openai` from Task 8).

Both halves of Review Focus 4 belong to this task. Inside `Parse` the order is fixed, and Tasks 3 and 4 keep it: (1) the `choices` guard, (2) the `usage` guard, (3) the content-filter check (added by Task 3), (4) reading the message. A consequence, accepted: a filtered completion with no `usage` is an `InvalidOperationException`, not a filter outcome.

How usage is counted does not change here: `InputTokens` is still `prompt_tokens`, and `CacheReadInputTokens` is still `cached_tokens`. Task 4 changes that. `ParseUsage` itself is the old usage block, unchanged; the new guard is in `Parse`. **Task 4 also adds the fourth `PricingIdentity? pricing` parameter to `Parse`**; in this task `Parse` has the three-parameter shape `Parse(root, providerName, fallbackModel)`, because `PricingIdentity` does not exist until Task 4. `finish_reason` is still returned as it arrives; Task 3 adds the `content_filter` mapping.

`tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs` is not edited in this task (beyond what Task 1 already did to it) and must stay green.

**Files:**
- Create: `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs`
- Modify: `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs`: rewrite `OpenAiChatProvider.CompleteAsync` to call the codec; delete the private members `BuildPayload(ChatRequest)` and `Parse(JsonElement)`; drop the now-unused `using System.Text.Json.Nodes;`. `OpenAiOptions` and the constructor are unchanged.
- Test: `tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs` (new)

No other file calls the members that move. `BuildPayload` and `Parse` are private to `OpenAiChatProvider` today, and a repo-wide grep for `choices` and `chat/completions` in `*.cs` finds only `OpenAiChatProvider.cs` and `OpenAiChatProviderTests.cs`. `OpenAiChatProvider`'s public surface (constructor, `Name`, `CompleteAsync`) does not change, so `src/ReleaseLens.Api/Program.cs` and every test fake are untouched. The Llm project already has `<InternalsVisibleTo Include="ReleaseLens.Llm.Tests" />`, so the test project can reach the `internal` codec.

**Interfaces:**
- Consumes (from Task 1): `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens)`, and `OpenAiOptions.Model` as the model the OpenAI provider sends. Existing and unchanged: `ChatResponse(string? Text, IReadOnlyList<ToolCall> ToolCalls, TokenUsage Usage, string StopReason, string Model, string Provider)`, `TokenUsage`, `ToolResult(string ToolCallId, string Content, bool IsError)`, `ProviderHttp.ReadInt`.
- Produces:
  - `internal static class OpenAiWireFormat` in namespace `ReleaseLens.Llm.Providers`, with
    - `public static JsonObject BuildPayload(ChatRequest request, string model);`
    - `public static TokenUsage ParseUsage(JsonElement root);`
    - `public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel);`, which throws `InvalidOperationException` with `$"{providerName} returned a completion with no choices."` when `choices` is missing, null or empty, and with `$"{providerName} returned a completion with no usage."` when `usage` is absent, null or not an object. The choices guard runs first. **Task 4 adds the fourth parameter `PricingIdentity? pricing`**, giving the contract's `Parse(JsonElement root, string providerName, string fallbackModel, PricingIdentity? pricing)`. Task 3 adds the `ContentFilteredException` for `finish_reason == "content_filter"`.
  - Wire behaviour later tasks rely on: a failed tool result's `content` is `"Error: " + ToolResult.Content`, and the payload's `model` is exactly the `model` argument (Task 8's T-P5 and T-P7 depend on this).

- [ ] **Step 1: Write the failing test**

Create `tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs`. T-F5 is `BuildPayload_FailedToolResult_KeepsItsErrorInTheContent`; Review Focus 4 is `Parse_NoChoices_ThrowsNamingTheProvider` (the `choices` half) and `Parse_NoUsage_ThrowsNamingTheProvider` (the `usage` half: absent, `null`, and a string). The other tests pin the behaviour that moves, so the extraction is checked at the codec and not only through the provider.

```csharp
using System;
using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class OpenAiWireFormatTests
{
    private static ToolDefinition SearchTool() => new(
        "search_commits", "Search commits.",
        JsonDocument.Parse("""{"type":"object","properties":{"query":{"type":"string"}}}""").RootElement);

    private static ChatRequest ToolRoundTrip(ToolResult result) => new(
        "system",
        [
            ChatMessage.User("What changed in release v9.9?"),
            ChatMessage.AssistantToolCalls([new ToolCall("call_01", "diff_between_releases",
                JsonDocument.Parse("""{"from_tag":"v9.8","to_tag":"v9.9"}""").RootElement)]),
            ChatMessage.UserToolResults([result])
        ],
        [SearchTool()], 1024);

    private static JsonElement LastMessage(JsonObject payload)
    {
        using var document = JsonDocument.Parse(payload.ToJsonString());
        var messages = document.RootElement.GetProperty("messages");
        return messages[messages.GetArrayLength() - 1].Clone();
    }

    [Fact]
    public void BuildPayload_FailedToolResult_KeepsItsErrorInTheContent()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "No release tagged 'v9.9' exists in the indexed evidence.", IsError: true)),
            "gpt-4.1-mini");

        var last = LastMessage(payload);

        Assert.Equal("tool", last.GetProperty("role").GetString());
        Assert.Equal("call_01", last.GetProperty("tool_call_id").GetString());
        Assert.Equal("Error: No release tagged 'v9.9' exists in the indexed evidence.",
            last.GetProperty("content").GetString());
    }

    [Fact]
    public void BuildPayload_SuccessfulToolResult_IsSentAsItIs()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
            "gpt-4.1-mini");

        Assert.Equal("3 commits found", LastMessage(payload).GetProperty("content").GetString());
    }

    [Fact]
    public void BuildPayload_SendsTheModelItIsGiven()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
            "my-deployment");

        Assert.Equal("my-deployment", payload["model"]!.GetValue<string>());
        Assert.Equal(1024, payload["max_tokens"]!.GetValue<int>());
    }

    [Fact]
    public void Parse_ReadsTextFinishReasonModelAndTheGivenProviderName()
    {
        using var document = JsonDocument.Parse("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                     "prompt_tokens_details": { "cached_tokens": 1024 } }
        }
        """);

        var response = OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "fallback-model");

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Empty(response.ToolCalls);
        Assert.Equal("stop", response.StopReason);
        Assert.Equal("gpt-4.1-mini-2025-04-14", response.Model);
        Assert.Equal("azure-openai", response.Provider);
    }

    [Fact]
    public void Parse_NoModelInTheResponse_ReportsTheFallbackModel()
    {
        using var document = JsonDocument.Parse("""
        {
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "ok" } }],
          "usage": { "prompt_tokens": 10, "completion_tokens": 2 }
        }
        """);

        Assert.Equal("fallback-model",
            OpenAiWireFormat.Parse(document.RootElement, "openai", "fallback-model").Model);
    }

    [Fact]
    public void Parse_ReadsToolCallsAndTheirJsonEncodedArguments()
    {
        using var document = JsonDocument.Parse("""
        {
          "model": "gpt-4.1-mini",
          "choices": [{ "index": 0, "finish_reason": "tool_calls",
            "message": { "role": "assistant", "content": null,
              "tool_calls": [{ "id": "call_01", "type": "function",
                "function": { "name": "search_commits", "arguments": "{\"query\":\"release 1.30\"}" } }] } }],
          "usage": { "prompt_tokens": 900, "completion_tokens": 60 }
        }
        """);

        var response = OpenAiWireFormat.Parse(document.RootElement, "openai", "gpt-4.1-mini");

        Assert.Null(response.Text);
        var call = Assert.Single(response.ToolCalls);
        Assert.Equal("call_01", call.Id);
        Assert.Equal("search_commits", call.Name);
        Assert.Equal("release 1.30", call.Arguments.GetProperty("query").GetString());
    }

    [Fact]
    public void ParseUsage_ReadsPromptCompletionAndCachedTokens()
    {
        using var document = JsonDocument.Parse("""
        { "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                     "prompt_tokens_details": { "cached_tokens": 1024 } } }
        """);

        var usage = OpenAiWireFormat.ParseUsage(document.RootElement);

        Assert.Equal(1200, usage.InputTokens);
        Assert.Equal(45, usage.OutputTokens);
        Assert.Equal(1024, usage.CacheReadInputTokens);
        Assert.Equal(0, usage.CacheCreationInputTokens);
    }

    [Theory]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [], "usage": { "prompt_tokens": 10, "completion_tokens": 0 } }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "usage": { "prompt_tokens": 10, "completion_tokens": 0 } }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": null }""")]
    public void Parse_NoChoices_ThrowsNamingTheProvider(string body)
    {
        using var document = JsonDocument.Parse(body);

        var exception = Assert.Throws<InvalidOperationException>(() =>
            OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "gpt-4.1-mini"));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("no choices", exception.Message, StringComparison.Ordinal);
    }

    // A success with no usage would otherwise count as zero tokens and be priced at $0.
    [Theory]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }] }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }], "usage": null }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }], "usage": "none" }""")]
    public void Parse_NoUsage_ThrowsNamingTheProvider(string body)
    {
        using var document = JsonDocument.Parse(body);

        var exception = Assert.Throws<InvalidOperationException>(() =>
            OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "gpt-4.1-mini"));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("no usage", exception.Message, StringComparison.Ordinal);
    }
}
```

- [ ] **Step 2: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiWireFormatTests"
```

Expected: the build fails with `error CS0103: The name 'OpenAiWireFormat' does not exist in the current context` in `OpenAiWireFormatTests.cs`, once per call site.

- [ ] **Step 3: Implement the codec**

Create `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs`.
- `BuildPayload` is `OpenAiChatProvider`'s old `BuildPayload`, with `model` taken as a parameter and the tool content going through `ToolContent`.
- `ParseUsage` is the old usage block lifted out verbatim, including `cached.GetInt32()`, so a non-numeric `cached_tokens` still throws exactly as before.
- `Parse` is the old `Parse` with the `choices` guard and the `usage` guard added in that order, the usage parsed once up front (so Task 3's filter check can use it), and `Name` / `_options.Model` replaced by `providerName` / `fallbackModel`. Tasks 3 and 4 reproduce this method exactly and add only their own lines.

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ReleaseLens.Llm.Providers;

/// <summary>
/// The OpenAI chat-completions wire format: the request body and the response. One copy,
/// shared by every provider that speaks it, so two providers given the same conversation
/// send the same bytes apart from what they pass as <c>model</c>. What differs between
/// them (address, authentication, status handling) stays in the provider.
/// </summary>
internal static class OpenAiWireFormat
{
    public static JsonObject BuildPayload(ChatRequest request, string model)
    {
        var messages = new JsonArray
        {
            new JsonObject { ["role"] = "system", ["content"] = request.SystemPrompt }
        };

        foreach (var message in request.Messages)
        {
            if (message.ToolResults is { Count: > 0 })
            {
                // Each tool result is its own message in the OpenAI format.
                foreach (var result in message.ToolResults)
                {
                    messages.Add(new JsonObject
                    {
                        ["role"] = "tool",
                        ["tool_call_id"] = result.ToolCallId,
                        ["content"] = ToolContent(result)
                    });
                }

                continue;
            }

            var entry = new JsonObject
            {
                ["role"] = message.Role == ChatRole.Assistant ? "assistant" : "user",
                ["content"] = message.Text
            };

            if (message.ToolCalls is { Count: > 0 })
            {
                var calls = new JsonArray();
                foreach (var call in message.ToolCalls)
                {
                    calls.Add(new JsonObject
                    {
                        ["id"] = call.Id,
                        ["type"] = "function",
                        ["function"] = new JsonObject
                        {
                            ["name"] = call.Name,
                            ["arguments"] = call.Arguments.GetRawText()
                        }
                    });
                }

                entry["tool_calls"] = calls;
            }

            messages.Add(entry);
        }

        var payload = new JsonObject
        {
            ["model"] = model,
            ["max_tokens"] = request.MaxTokens,
            ["messages"] = messages
        };

        if (request.Tools.Count > 0)
        {
            var tools = new JsonArray();
            foreach (var tool in request.Tools)
            {
                tools.Add(new JsonObject
                {
                    ["type"] = "function",
                    ["function"] = new JsonObject
                    {
                        ["name"] = tool.Name,
                        ["description"] = tool.Description,
                        ["parameters"] = JsonNode.Parse(tool.JsonSchema.GetRawText())
                    }
                });
            }

            payload["tools"] = tools;
        }

        return payload;
    }

    public static TokenUsage ParseUsage(JsonElement root)
    {
        var usage = root.TryGetProperty("usage", out var usageElement) ? usageElement : default;

        var cachedTokens = 0;
        if (usage.ValueKind == JsonValueKind.Object &&
            usage.TryGetProperty("prompt_tokens_details", out var details) &&
            details.TryGetProperty("cached_tokens", out var cached))
        {
            cachedTokens = cached.GetInt32();
        }

        return new TokenUsage(
            ProviderHttp.ReadInt(usage, "prompt_tokens"),
            ProviderHttp.ReadInt(usage, "completion_tokens"),
            cachedTokens,
            0);
    }

    public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel)
    {
        // A 200 with nothing to read is a malformed reply, not an empty answer. Indexing into
        // it would throw KeyNotFoundException or IndexOutOfRangeException, naming no provider.
        if (!root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no choices.");
        }

        // A success with no usage would count as zero tokens and be priced at $0, with nothing
        // to show that it was not free.
        if (!root.TryGetProperty("usage", out var usageElement) || usageElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no usage.");
        }

        var usage = ParseUsage(root);
        var choice = choices[0];
        var message = choice.GetProperty("message");

        var text = message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String
            ? content.GetString()
            : null;

        var toolCalls = new List<ToolCall>();
        if (message.TryGetProperty("tool_calls", out var calls) && calls.ValueKind == JsonValueKind.Array)
        {
            foreach (var call in calls.EnumerateArray())
            {
                var function = call.GetProperty("function");
                var rawArguments = function.GetProperty("arguments").GetString() ?? "{}";

                // OpenAI sends arguments as a JSON-encoded string, not an object. Clone the
                // parsed element before `parsed` is disposed, or it reads freed memory.
                using var parsed = JsonDocument.Parse(rawArguments);

                toolCalls.Add(new ToolCall(
                    call.GetProperty("id").GetString()!,
                    function.GetProperty("name").GetString()!,
                    parsed.RootElement.Clone()));
            }
        }

        return new ChatResponse(
            text,
            toolCalls,
            usage,
            choice.GetProperty("finish_reason").GetString() ?? "unknown",
            root.TryGetProperty("model", out var model) ? model.GetString() ?? fallbackModel : fallbackModel,
            providerName);
    }

    /// <summary>
    /// The chat-completions tool message has no error field, unlike Anthropic's
    /// <c>is_error</c>, so a failed call is marked in its text. Without the prefix a failed
    /// call and a successful one are indistinguishable on the wire.
    /// </summary>
    private static string ToolContent(ToolResult result)
        => result.IsError ? "Error: " + result.Content : result.Content;
}
```

`OpenAiChatProvider` still has its own private copies at this point, so the solution builds with both.

- [ ] **Step 4: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiWireFormatTests"
```

Expected: `Passed!  - Failed: 0, Passed: 13` (7 facts, plus the two 3-row theories).

- [ ] **Step 5: Write the failing provider-level tests**

The codec tests pass while `OpenAiChatProvider` still uses its private copies, so these two prove the provider actually goes through the codec. In `OpenAiWireFormatTests.cs`, replace the using block with:

```csharp
using System;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;
```

and add these members at the end of the class, after `Parse_NoUsage_ThrowsNamingTheProvider` and before the class's closing brace:

```csharp
    private static OpenAiChatProvider OpenAi(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4.1-mini", BaseUrl = "https://api.openai.com/v1/" });

    [Fact]
    public async Task OpenAiChatProvider_SendsAFailedToolResultWithItsError()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "There is no release v9.9." } }],
          "usage": { "prompt_tokens": 300, "completion_tokens": 8 }
        }
        """);

        await OpenAi(handler).CompleteAsync(
            ToolRoundTrip(new ToolResult("call_01", "No release tagged 'v9.9' exists in the indexed evidence.", IsError: true)),
            TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);
        var messages = document.RootElement.GetProperty("messages");

        Assert.Equal("Error: No release tagged 'v9.9' exists in the indexed evidence.",
            messages[messages.GetArrayLength() - 1].GetProperty("content").GetString());
    }

    [Fact]
    public async Task OpenAiChatProvider_EmptyChoices_ThrowsNamingTheProvider()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        { "model": "gpt-4.1-mini", "choices": [], "usage": { "prompt_tokens": 300, "completion_tokens": 0 } }
        """);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await OpenAi(handler).CompleteAsync(
                ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
                TestContext.Current.CancellationToken));

        Assert.Contains("openai", exception.Message, StringComparison.Ordinal);
    }
```

- [ ] **Step 6: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiWireFormatTests"
```

Expected: `Failed: 2, Passed: 13`.
- `OpenAiChatProvider_SendsAFailedToolResultWithItsError`: `Assert.Equal() Failure: Strings differ`, expected `"Error: No release tagged 'v9.9' exists in the inde"···`, actual `"No release tagged 'v9.9' exists in the indexed evi"···`.
- `OpenAiChatProvider_EmptyChoices_ThrowsNamingTheProvider`: `Assert.Throws() Failure: Exception type was not an exact match`, expected `typeof(System.InvalidOperationException)`, actual `typeof(System.IndexOutOfRangeException)`.

- [ ] **Step 7: Route `OpenAiChatProvider` through the codec**

In `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs`:

1. Delete the whole of `private JsonObject BuildPayload(ChatRequest request)` and the whole of `private ChatResponse Parse(JsonElement root)`: every line from each signature to its closing brace, in whatever form Task 1 left them.
2. Replace `CompleteAsync` with the version below. It is now the last member of the class; the class's closing brace follows it.

```csharp
    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Model);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return OpenAiWireFormat.Parse(document.RootElement, Name, _options.Model);
        }
    }
```

3. The file's using block becomes the following. Only `System.Text.Json.Nodes` goes: nothing left in the file uses `JsonObject`, `JsonArray` or `JsonNode`.

```csharp
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
```

`OpenAiOptions`, the `_client` and `_options` fields, `Name`, and the constructor (with its comment about not sending an empty `Bearer`) are unchanged.

- [ ] **Step 8: Run them and watch them pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiWireFormatTests|FullyQualifiedName~OpenAiChatProviderTests"
```

Expected: `Passed!  - Failed: 0, Passed: 24`: the 15 `OpenAiWireFormatTests` plus the 9 `OpenAiChatProviderTests`, with `OpenAiChatProviderTests.cs` not edited in this task. In particular `Complete_ReturnsTextAndMapsCachedPromptTokens` still sees `InputTokens == 1200` and `CacheReadInputTokens == 1024`, and `Complete_SerialisesToolResultsAsToolRoleMessages` (whose tool result has `IsError: false`) still passes. Both of that file's response fixtures carry a `usage` object, so the usage guard does not touch them.

- [ ] **Step 9: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceToolTests&FullyQualifiedName!~EvidenceToolBoundsTests"
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected: the build reports `0 Warning(s)` and `0 Error(s)` (`TreatWarningsAsErrors` is on), and every test passes.
- The second command needs no Docker. It runs every Llm test outside the Postgres Testcontainers collection: `OpenAiWireFormatTests`, `OpenAiChatProviderTests`, `AnthropicChatProviderTests`, `FallbackChatProviderTests`, `ModelPricingTests`, `JsonArgsTests`, `SystemPromptTests`, `TelemetryTests`.
- The third and fourth need Docker running. `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` in the Llm project, and `QueryEndpointTests` and `EvidenceEndpointTests` in the Api project, use the `PostgresCollection`. The Api project is run because its `/query` tests go through `QueryAgent`; nothing in it references the codec.

- [ ] **Step 10: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs
git commit -m "refactor(llm): share the OpenAI wire codec; keep tool errors on the wire" -m "Payload building and response parsing move from OpenAiChatProvider into
the internal OpenAiWireFormat, so a second OpenAI-wire provider can send
the same bytes. Three behaviour changes: a failed tool result is sent as
'Error: <content>' (F6: the tool message has no error field, so the flag
was dropped); a 200 with missing or empty choices throws an
InvalidOperationException naming the provider instead of an
IndexOutOfRangeException; and a 200 with no usage object throws the same
way, instead of counting as zero tokens and costing nothing. How usage
is counted is unchanged." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The content-filter outcome (fixes F3 on the OpenAI path)

A completion stopped by the provider's content filter (HTTP 200, `choices[0].finish_reason == "content_filter"`) becomes a `ContentFilteredException` at the completion stage. It carries the parsed usage and no text. Today `finish_reason` is copied into `StopReason` and ignored, so a filtered completion comes back as an ordinary answer with empty or partial text (F3). `FallbackChatProvider` already catches only `ProviderUnavailableException`, so the new exception passes through it with no code change. This task adds a regression test that fixes that behaviour in place.

This task defines the exception **without** the pricing parameter, because `PricingIdentity` does not exist until Task 4. Task 4 adds a trailing `PricingIdentity? pricing = null` constructor parameter and a `public PricingIdentity? Pricing { get; } = pricing;` property, and passes `pricing` at the one `throw` site in `OpenAiWireFormat.Parse`. The shape otherwise matches the contract exactly.

Between this task and Task 6, `QueryAgent` does not catch the new exception, so a filtered completion reaching `/query` returns HTTP 500 instead of a silent empty answer. Task 6 turns it into the fixed filtered answer.

**Files:**
- Modify: `src/ReleaseLens.Llm/Providers/ChatContracts.cs`. Append `ContentFilterStage` and `ContentFilteredException` after `ProviderUnavailableException`, which is the last type in the file.
- Modify: `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs`, as Task 2 created it. Two changes: in `Parse`, add a filter check between reading `choice` and reading `message`; and add a new private method `IsContentFiltered`.
- Modify: `src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs`. Change only the class's XML `<summary>`, so it names the filter outcome as something that does not fall through.
- Create: `tests/ReleaseLens.Llm.Tests/OpenAiContentFilterTests.cs`
- Modify: `tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs`. Add one test method, `Complete_ContentFiltered_DoesNotFallThrough`.
- Test: `tests/ReleaseLens.Llm.Tests/OpenAiContentFilterTests.cs`, `tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs`

No existing call site changes. The two new types are additive, and no current code or test constructs them. A grep of `src` and `tests` for `content_filter|ContentFilter` finds nothing before this task. No existing test fixture has `"finish_reason": "content_filter"`, so every existing OpenAI test keeps its behaviour.

**Interfaces:**
- Consumes:
  - `ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens)`, with `Model` removed in Task 1.
  - `TokenUsage` (unchanged).
  - `internal static class OpenAiWireFormat`, from Task 2, with `ParseUsage(JsonElement root)` and `Parse(...)`. `Parse` has Task 2's three-parameter shape, `Parse(JsonElement root, string providerName, string fallbackModel)`; Task 4 adds `PricingIdentity? pricing`. Task 2's choices guard and usage guard (Review Focus 4) both run before `choices[0]` is read, with the messages `$"{providerName} returned a completion with no choices."` and `$"{providerName} returned a completion with no usage."`.
  - `FallbackChatProvider(IReadOnlyList<IChatProvider>, ILogger<FallbackChatProvider>)`.
- Produces:
  - `public enum ContentFilterStage { Prompt, Completion }`
  - `public sealed class ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage) : Exception($"{providerName} content filter blocked the {stage.ToString().ToLowerInvariant()}.")` with `ProviderName`, `Stage` and `Usage`. This is the contract shape without `pricing`, which Task 4 appends.
  - `OpenAiWireFormat.Parse` throws `ContentFilteredException(providerName, ContentFilterStage.Completion, usage)` when `choices[0].finish_reason == "content_filter"`. Task 4 appends `pricing` to that `throw`. Tasks 6, 8 and 9 rely on this. The Azure provider's filtered prompt, stage `Prompt`, is Task 8's.

- [ ] **Step 1: Write the failing fallback test**

This test fixes the rule that a filter outcome never reaches a second provider (spec §4.5). `FallbackChatProvider` needs no change to pass it; the red is the compile error for the missing types. Add this method to `FallbackChatProviderTests` in `tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs`, directly after `Complete_NonTransientError_DoesNotFallThrough` and before `Construct_WithNoProviders_Throws`. It uses the class's existing private `ScriptedProvider`, `Ok` and `Request` helpers, and the file's existing usings, which already cover `NullLogger`, `ReleaseLens.Llm.Providers` and `Xunit`:

```csharp
    [Fact]
    public async Task Complete_ContentFiltered_DoesNotFallThrough()
    {
        // Answering a filtered request on another provider would route around the filter.
        var primary = new ScriptedProvider("openai",
            () => throw new ContentFilteredException("openai", ContentFilterStage.Completion, new TokenUsage(900, 12, 0, 0)));
        var secondary = new ScriptedProvider("anthropic", () => Ok("anthropic"));

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("openai", exception.ProviderName);
        Assert.Equal(1, primary.Calls);
        Assert.Equal(0, secondary.Calls);
    }
```

- [ ] **Step 2: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderTests"
```

Expected: the build fails with `error CS0246: The type or namespace name 'ContentFilteredException' could not be found` and `error CS0103: The name 'ContentFilterStage' does not exist in the current context`, both in `FallbackChatProviderTests.cs`.

- [ ] **Step 3: Add the outcome types**

In `src/ReleaseLens.Llm/Providers/ChatContracts.cs`, append after the closing brace of `ProviderUnavailableException`, which is the end of the file:

```csharp

public enum ContentFilterStage { Prompt, Completion }

/// <summary>
/// The provider's content filter blocked the prompt or the completion. This is neither our
/// bug nor the provider being unavailable, so the fallback chain lets it through untouched:
/// answering the same request on another provider would route around the filter.
/// </summary>
/// <remarks>
/// It carries the usage the provider reported so the caller can count and price the call like
/// any other; whether a provider bills a filtered call is unverified. It deliberately carries
/// no text: a filtered completion can arrive with partial content, and none of it may reach
/// the caller.
/// </remarks>
public sealed class ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage)
    : Exception($"{providerName} content filter blocked the {stage.ToString().ToLowerInvariant()}.")
{
    public string ProviderName { get; } = providerName;
    public ContentFilterStage Stage { get; } = stage;
    public TokenUsage Usage { get; } = usage;
}
```

In `src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs`, replace the XML summary on `FallbackChatProvider`. The code stays the same, because the `catch (ProviderUnavailableException ...)` already lets everything else through. The current summary is:

```csharp
/// <summary>
/// Tries each provider in order. Only <see cref="ProviderUnavailableException"/> falls
/// through — a malformed request is our bug and must surface on the first provider
/// rather than being retried at cost against the second.
/// </summary>
public sealed class FallbackChatProvider : IChatProvider
```

It becomes:

```csharp
/// <summary>
/// Tries each provider in order. Only <see cref="ProviderUnavailableException"/> falls
/// through — a malformed request is our bug and must surface on the first provider
/// rather than being retried at cost against the second, and a
/// <see cref="ContentFilteredException"/> must not be answered elsewhere, because that
/// would route around the filter.
/// </summary>
public sealed class FallbackChatProvider : IChatProvider
```

- [ ] **Step 4: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderTests"
```

Expected: `Passed!  - Failed: 0, Passed: 7`. That is the six tests Task 1 left in the file (the five on `main` plus T-F1) and the new one.

- [ ] **Step 5: Write the failing codec tests (T-F3, Review Focus 5)**

Create `tests/ReleaseLens.Llm.Tests/OpenAiContentFilterTests.cs`:

```csharp
using System;
using System.Linq;
using System.Net.Http;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// A completion the provider's content filter stopped is its own outcome. Before this, the
/// OpenAI wire path never read <c>finish_reason</c>, so a filtered completion came back as an
/// ordinary answer with empty text (F3).
/// </summary>
public class OpenAiContentFilterTests
{
    private const string PartialText = "The first half of a sentence the filter cut";

    private static OpenAiChatProvider Create(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o" });

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    // No cached tokens, so the expected usage is the same before and after Task 4 normalises
    // InputTokens to uncached input.
    private const string FilteredCompletion = """
    {
      "id": "chatcmpl-3",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "content_filter",
        "message": { "role": "assistant", "content": null } }],
      "usage": { "prompt_tokens": 900, "completion_tokens": 12 }
    }
    """;

    private const string FilteredCompletionWithPartialText = $$"""
    {
      "id": "chatcmpl-4",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "content_filter",
        "message": { "role": "assistant", "content": "{{PartialText}}" } }],
      "usage": { "prompt_tokens": 900, "completion_tokens": 30 }
    }
    """;

    // T-F3
    [Fact]
    public async Task Complete_FinishReasonContentFilter_ThrowsContentFilteredAtTheCompletionStage()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(FilteredCompletion);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Completion, exception.Stage);
        Assert.Equal(new TokenUsage(900, 12, 0, 0), exception.Usage);
        Assert.Equal("openai content filter blocked the completion.", exception.Message);
    }

    // Review Focus 5. The exception is the only thing a filtered completion produces, so
    // proving it holds none of the partial text proves no caller can surface any of it.
    [Fact]
    public async Task Complete_FilteredCompletionWithPartialText_CarriesNoneOfTheText()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(FilteredCompletionWithPartialText);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        var stringValues = exception.GetType()
            .GetProperties()
            .Where(property => property.PropertyType == typeof(string))
            .Select(property => (string?)property.GetValue(exception));

        Assert.All(stringValues, value => Assert.DoesNotContain(PartialText, value ?? string.Empty, StringComparison.Ordinal));
        Assert.DoesNotContain(PartialText, exception.ToString(), StringComparison.Ordinal);
        Assert.Empty(exception.Data);
        Assert.Null(exception.InnerException);
        Assert.Equal(new TokenUsage(900, 30, 0, 0), exception.Usage);
    }
}
```

- [ ] **Step 6: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiContentFilterTests"
```

Expected: both tests fail at run time with `Assert.Throws() Failure: No exception was thrown` / `Expected: typeof(ReleaseLens.Llm.Providers.ContentFilteredException)`. The codec still returns a normal `ChatResponse`, with `StopReason` set to `"content_filter"`.

- [ ] **Step 7: Map `finish_reason: content_filter` in the codec**

In `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs`, change `Parse` and add one private method. **The only new lines in `Parse` are the `if (IsContentFiltered(choice))` block and its comment.** They go after `var choice = choices[0];` and before the first read of `choice.GetProperty("message")`. By then, Task 2's choices guard and usage guard have run and `usage` is parsed.

The complete method below is Task 2's `Parse`, character for character, with only that block (and a blank line either side of it) added. The order is the one Task 2 fixed: (1) the choices guard, (2) the usage guard, (3) this filter check, (4) reading the message. Because the usage guard runs first, a filtered completion that carries no `usage` is an `InvalidOperationException`, not a filter outcome; that is accepted, since a filtered call with no usage could not be priced either.

```csharp
    public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel)
    {
        // A 200 with nothing to read is a malformed reply, not an empty answer. Indexing into
        // it would throw KeyNotFoundException or IndexOutOfRangeException, naming no provider.
        if (!root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no choices.");
        }

        // A success with no usage would count as zero tokens and be priced at $0, with nothing
        // to show that it was not free.
        if (!root.TryGetProperty("usage", out var usageElement) || usageElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no usage.");
        }

        var usage = ParseUsage(root);
        var choice = choices[0];

        // Checked before the message is read: a filtered completion can still carry partial
        // text, and none of it may become an answer.
        if (IsContentFiltered(choice))
        {
            throw new ContentFilteredException(providerName, ContentFilterStage.Completion, usage);
        }

        var message = choice.GetProperty("message");

        var text = message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String
            ? content.GetString()
            : null;

        var toolCalls = new List<ToolCall>();
        if (message.TryGetProperty("tool_calls", out var calls) && calls.ValueKind == JsonValueKind.Array)
        {
            foreach (var call in calls.EnumerateArray())
            {
                var function = call.GetProperty("function");
                var rawArguments = function.GetProperty("arguments").GetString() ?? "{}";

                // OpenAI sends arguments as a JSON-encoded string, not an object. Clone the
                // parsed element before `parsed` is disposed, or it reads freed memory.
                using var parsed = JsonDocument.Parse(rawArguments);

                toolCalls.Add(new ToolCall(
                    call.GetProperty("id").GetString()!,
                    function.GetProperty("name").GetString()!,
                    parsed.RootElement.Clone()));
            }
        }

        return new ChatResponse(
            text,
            toolCalls,
            usage,
            choice.GetProperty("finish_reason").GetString() ?? "unknown",
            root.TryGetProperty("model", out var model) ? model.GetString() ?? fallbackModel : fallbackModel,
            providerName);
    }
```

Add this private method as the last member of `OpenAiWireFormat`, before the class's closing brace:

```csharp
    private static bool IsContentFiltered(JsonElement choice)
        => choice.TryGetProperty("finish_reason", out var finishReason)
           && finishReason.ValueKind == JsonValueKind.String
           && finishReason.ValueEquals("content_filter");
```

The type check guards against a `null` `finish_reason`: calling `ValueEquals` on a non-string element throws `InvalidOperationException`. With the check, such a response keeps today's behaviour instead of becoming a parse crash.

- [ ] **Step 8: Run them and watch them pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~OpenAiContentFilterTests|FullyQualifiedName~FallbackChatProviderTests|FullyQualifiedName~OpenAiChatProviderTests"
```

Expected: `Passed!  - Failed: 0, Passed: 18`: 2 in `OpenAiContentFilterTests`, 7 in `FallbackChatProviderTests`, and the 9 `OpenAiChatProviderTests`, which Tasks 1 and 2 left at 9. The existing OpenAI tests must stay green, because none of their fixtures has a `content_filter` finish reason.

- [ ] **Step 9: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected: the build reports `0 Warning(s)` and `0 Error(s)` (`TreatWarningsAsErrors` is on), and both test runs report `Failed: 0`.

Both test projects **need Docker**. Their Postgres Testcontainers collection (`PostgresCollection`) backs `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` in `ReleaseLens.Llm.Tests`, and `QueryEndpointTests` and `EvidenceEndpointTests` in `ReleaseLens.Api.Tests`. Without Docker, run the Step 8 filter plus `dotnet build ReleaseLens.sln`, and record that the Postgres collection was not run.

`ReleaseLens.Api.Tests` is included because the Api project compiles against the changed `ChatContracts.cs`. No test there constructs the new types.

- [ ] **Step 10: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/ChatContracts.cs src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs tests/ReleaseLens.Llm.Tests/OpenAiContentFilterTests.cs tests/ReleaseLens.Llm.Tests/FallbackChatProviderTests.cs
git commit -F- <<'EOF'
fix(llm): a filtered completion is its own outcome, not an empty answer

On the OpenAI wire path finish_reason was copied into StopReason and never
read, so a completion the content filter stopped came back as an ordinary
answer with empty or partial text (F3). The shared codec now throws
ContentFilteredException at the completion stage, carrying the parsed usage
and none of the text. FallbackChatProvider catches only
ProviderUnavailableException, so the outcome never reaches another provider;
a test now pins that.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Price by pricing identity, per iteration (fixes F2, F4, and the pricing half of F5)

Every provider reports the identity it is billed under. `ModelPricing` keys rates by that identity, with a cached-input rate per model. The OpenAI-wire codec reports `InputTokens` as uncached input. `QueryAgent` adds up each call's cost, instead of pricing the whole query from the last `model` string.

This task starts from the tree as Tasks 1–3 leave it: `ChatRequest` has no `Model`; `AgentOptions` has no `Model`; `OpenAiWireFormat` exists with `BuildPayload(ChatRequest, string)`, `ParseUsage(JsonElement)`, `Parse(JsonElement, string providerName, string fallbackModel)` and the private `IsContentFiltered`; `tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs` (Task 2) and `tests/ReleaseLens.Llm.Tests/OpenAiContentFilterTests.cs` (Task 3) exist; and `ContentFilteredException(providerName, stage, usage)` exists. Every code block below was compiled, and every non-Docker test run, against a scratch copy of the repository. In that copy Tasks 1–3 were applied as stand-ins written to the contract, with Task 2's and Task 3's test files taken from their task text. The `QueryAgentTests` members were compiled but not run, because Docker was not available.

**Files:**
- Create: `src/ReleaseLens.Llm/Providers/PricingIdentity.cs`
- Create: `tests/ReleaseLens.Llm.Tests/ProviderPricingTests.cs`
- Modify: `src/ReleaseLens.Llm/Providers/ModelPricing.cs` (whole file replaced)
- Modify: `src/ReleaseLens.Llm/Providers/ChatContracts.cs` (`ChatResponse` gains `Pricing`; `ContentFilteredException` gains `pricing`)
- Modify: `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs` (`ParseUsage`, `Parse`)
- Modify: `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs` (whole file replaced: `OpenAiOptions.Unpriced`; `OpenAiChatProvider` implements `IPricedChatProvider`, gains `Pricing`, and passes it to the codec)
- Modify: `src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs` (class declaration, `Pricing`, constructor start, the `return` at the end of `Parse`)
- Modify: `src/ReleaseLens.Llm/Agent/QueryAgent.cs` (`AnswerAsync`: five anchored edits; new private `CostOf`)
- Modify: `tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs` (Task 2's file: five `Parse` calls gain `pricing: null`; `ParseUsage_ReadsPromptCompletionAndCachedTokens` is replaced)
- Modify: `tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs` (`Complete_ReturnsTextAndMapsCachedPromptTokens`)
- Test: `tests/ReleaseLens.Llm.Tests/ModelPricingTests.cs` (whole file replaced; the six existing tests are kept unchanged)
- Test: `tests/ReleaseLens.Llm.Tests/ProviderPricingTests.cs`
- Test: `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs` (new members after `Answer_AccumulatesUsageAcrossIterations`)
- Modify: `README.md` (the "Fully local, no egress" JSON snippet)

Call sites checked with `grep -rn "new ChatResponse(\|ChatResponse(\|ModelPricing\|ContentFilteredException(\|OpenAiWireFormat.Parse(" src tests`:
- `ChatResponse` is built in `AnthropicChatProvider`, `OpenAiWireFormat`, `tests/ReleaseLens.Api.Tests/ScriptedChatProvider.cs`, and the `ScriptedProvider` fakes in `QueryAgentTests` and `FallbackChatProviderTests`. The new parameter is optional, so the fakes compile unchanged and are still priced by name. `SpyChatProvider` only throws.
- `ModelPricing` is called only from `QueryAgent` and `ModelPricingTests`.
- `ContentFilteredException` is built in `OpenAiWireFormat.Parse`, and with three arguments in Task 3's `FallbackChatProviderTests.Complete_ContentFiltered_DoesNotFallThrough`. The new parameter is optional, so that test compiles unchanged.
- `OpenAiWireFormat.Parse` is called from `OpenAiChatProvider` and five times from Task 2's `OpenAiWireFormatTests` (one of them in `Parse_NoUsage_ThrowsNamingTheProvider`). The contract makes `pricing` a required parameter with no default, so all six change here.
- Task 3's `OpenAiContentFilterTests` fixtures have no `cached_tokens`, so their `TokenUsage` assertions hold under the new normalisation. That file is not touched.
- `Program.cs` is not touched. `EnsurePriced` is wired at startup in Task 10.

**Interfaces:**
- Consumes:
  - Task 1: `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens)`; `AgentOptions` without `Model`; `AnthropicChatProvider` and `OpenAiChatProvider` send `options.Model`.
  - Task 2: `internal static class OpenAiWireFormat` with `public static JsonObject BuildPayload(ChatRequest request, string model)`, `public static TokenUsage ParseUsage(JsonElement root)`, `public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel)`. This task adds the fourth parameter.
  - Task 3: `public enum ContentFilterStage { Prompt, Completion }`; `public sealed class ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage)`. This task adds the fourth parameter. Also Task 3's `Parse` body, with its `IsContentFiltered(choice)` check.
- Produces:
  - `public sealed record PricingIdentity(string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false);`
  - `public interface IPricedChatProvider : IChatProvider { PricingIdentity Pricing { get; } }`
  - `ChatResponse(string? Text, IReadOnlyList<ToolCall> ToolCalls, TokenUsage Usage, string StopReason, string Model, string Provider, PricingIdentity? Pricing = null)`
  - `ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage, PricingIdentity? pricing = null)` with `public PricingIdentity? Pricing { get; }`
  - `ModelPricing.CostUsd(string model, TokenUsage usage, DateOnly asOf)` (kept, for fakes), `ModelPricing.CostUsd(PricingIdentity identity, TokenUsage usage, DateOnly asOf)`, `ModelPricing.HasRate(PricingIdentity identity, DateOnly asOf)`, `ModelPricing.EnsurePriced(IEnumerable<PricingIdentity> identities, DateOnly asOf)` (throws `InvalidOperationException`), `ModelPricing.MinimumCacheablePrefixTokens(string model)` (unchanged)
  - `OpenAiWireFormat.Parse(JsonElement root, string providerName, string fallbackModel, PricingIdentity? pricing)`. It throws `ContentFilteredException(providerName, Completion, usage, pricing)` on `finish_reason == "content_filter"`. `OpenAiWireFormat.ParseUsage` reports `InputTokens = prompt_tokens - cached_tokens` (floored at 0) and `CacheReadInputTokens = cached_tokens`.
  - `OpenAiOptions.Unpriced` (`bool`, default `false`); `OpenAiChatProvider.Pricing` = `("openai", options.Model, Unpriced: options.Unpriced)`; `AnthropicChatProvider.Pricing` = `("anthropic", options.Model)`.
  - The Azure identity `("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard")` has a rate: 0.44 input, 0.11 cached input, 1.76 output, USD per 1M tokens. Task 8's `AzureOpenAiChatProvider.Pricing` must equal it exactly, or `CostUsd` throws.
  - In `QueryAgent.AnswerAsync`: the locals `cost` (running total, `decimal`) and `pricedOn` (`DateOnly`), and `private static decimal CostOf(ChatResponse response, DateOnly asOf)`. Task 6 adds a filtered completion's cost to `cost`, using `ModelPricing.CostUsd(exception.Pricing, exception.Usage, pricedOn)` when `exception.Pricing` is set.

- [ ] **Step 1: Write the failing test — rates keyed by identity (T-C1, T-C3)**

Replace `tests/ReleaseLens.Llm.Tests/ModelPricingTests.cs` with the file below. The first six tests are the existing ones, unchanged. The rest are new.

```csharp
using System;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class ModelPricingTests
{
    private static readonly TokenUsage OneMillionIn = new(1_000_000, 0, 0, 0);
    private static readonly TokenUsage OneMillionOut = new(0, 1_000_000, 0, 0);
    private static readonly DateOnly AfterIntroductoryPricing = new(2026, 9, 24);

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    [Fact]
    public void Sonnet5_BeforeThirtyFirstAugust2026_UsesIntroductoryInputPricing()
    {
        var cost = ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 8, 30));
        Assert.Equal(2.00m, cost);
    }

    [Fact]
    public void Sonnet5_OnThirtyFirstAugust2026_IsStillIntroductory()
    {
        var cost = ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 8, 31));
        Assert.Equal(2.00m, cost);
    }

    [Fact]
    public void Sonnet5_FromFirstSeptember2026_UsesStandardPricing()
    {
        Assert.Equal(3.00m, ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 9, 1)));
        Assert.Equal(15.00m, ModelPricing.CostUsd("claude-sonnet-5", OneMillionOut, new DateOnly(2026, 9, 1)));
    }

    [Fact]
    public void CacheReads_ArePricedBelowFreshInput()
    {
        var fresh = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(1_000_000, 0, 0, 0), new DateOnly(2026, 9, 1));
        var cached = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(0, 0, 1_000_000, 0), new DateOnly(2026, 9, 1));

        Assert.True(cached < fresh, $"cache read {cached} should cost less than fresh input {fresh}");
    }

    [Fact]
    public void UnknownModel_ReturnsZeroRatherThanThrowing()
    {
        Assert.Equal(0m, ModelPricing.CostUsd("some-local-ollama-model", OneMillionIn, new DateOnly(2026, 9, 1)));
    }

    [Fact]
    public void MinimumCacheablePrefix_IsModelDependentAndNotMonotonic()
    {
        Assert.Equal(512, ModelPricing.MinimumCacheablePrefixTokens("claude-opus-5"));
        Assert.Equal(1024, ModelPricing.MinimumCacheablePrefixTokens("claude-sonnet-5"));
        Assert.Equal(4096, ModelPricing.MinimumCacheablePrefixTokens("claude-haiku-4-5-20251001"));
    }

    // T-C1
    [Fact]
    public void AzureRegionalIdentity_KnownTokens_GiveAnExactUsdFigure()
    {
        // 12,345 x 0.44 + 1,024 x 0.11 + 678 x 1.76 = 5,431.80 + 112.64 + 1,193.28 = 6,737.72 per million.
        var usage = new TokenUsage(InputTokens: 12_345, OutputTokens: 678, CacheReadInputTokens: 1_024, CacheCreationInputTokens: 0);

        Assert.Equal(0.00673772m, ModelPricing.CostUsd(AzureRegional, usage, AfterIntroductoryPricing));
    }

    [Fact]
    public void AzureRegionalIdentity_OneMillionOfEach_MatchesThePublishedRates()
    {
        Assert.Equal(0.44m, ModelPricing.CostUsd(AzureRegional, OneMillionIn, AfterIntroductoryPricing));
        Assert.Equal(0.11m, ModelPricing.CostUsd(AzureRegional, new TokenUsage(0, 0, 1_000_000, 0), AfterIntroductoryPricing));
        Assert.Equal(1.76m, ModelPricing.CostUsd(AzureRegional, OneMillionOut, AfterIntroductoryPricing));
    }

    [Fact]
    public void AnthropicIdentity_KeepsTheNameBasedRatesAndCacheMultipliers()
    {
        var sonnet = new PricingIdentity("anthropic", "claude-sonnet-5");
        var usage = new TokenUsage(1_000_000, 1_000_000, 1_000_000, 1_000_000);

        // 3.00 input + 15.00 output + 0.30 cache read (0.1x) + 3.75 cache write (1.25x).
        Assert.Equal(22.05m, ModelPricing.CostUsd(sonnet, usage, AfterIntroductoryPricing));
        Assert.Equal(ModelPricing.CostUsd("claude-sonnet-5", usage, AfterIntroductoryPricing),
            ModelPricing.CostUsd(sonnet, usage, AfterIntroductoryPricing));
    }

    [Fact]
    public void OpenAiIdentity_PricesCachedTokensAtTheInputRate()
    {
        var gpt4o = new PricingIdentity("openai", "gpt-4o");

        Assert.Equal(2.50m, ModelPricing.CostUsd(gpt4o, new TokenUsage(0, 0, 1_000_000, 0), AfterIntroductoryPricing));
        Assert.Equal(2.50m, ModelPricing.CostUsd(gpt4o, OneMillionIn, AfterIntroductoryPricing));
    }

    [Fact]
    public void UnpricedIdentity_CostsNothing()
    {
        var ollama = new PricingIdentity("openai", "llama3.1", Unpriced: true);

        Assert.Equal(0m, ModelPricing.CostUsd(ollama, OneMillionIn, AfterIntroductoryPricing));
    }

    [Fact]
    public void PricedIdentityWithNoRate_ThrowsRatherThanPricingAtZero()
    {
        var unknown = new PricingIdentity("openai", "gpt-no-rate-test");

        var exception = Assert.Throws<InvalidOperationException>(() =>
            ModelPricing.CostUsd(unknown, OneMillionIn, AfterIntroductoryPricing));

        Assert.Contains("gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void HasRate_IsKeyedByTheWholeIdentity_NotJustTheModel()
    {
        Assert.True(ModelPricing.HasRate(AzureRegional, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { DeploymentType = "GlobalStandard" }, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { Version = "2024-07-18" }, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { Provider = "openai" }, AfterIntroductoryPricing));
    }

    // T-C3
    [Fact]
    public void EnsurePriced_NamesEveryPricedIdentityThatHasNoRate()
    {
        var globalStandard = AzureRegional with { DeploymentType = "GlobalStandard" };
        var openAiNoRate = new PricingIdentity("openai", "gpt-no-rate-test");

        var exception = Assert.Throws<InvalidOperationException>(() => ModelPricing.EnsurePriced(
            [
                new PricingIdentity("anthropic", "claude-sonnet-5"),
                globalStandard,
                openAiNoRate,
                new PricingIdentity("openai", "llama3.1", Unpriced: true)
            ],
            AfterIntroductoryPricing));

        Assert.Contains("azure-openai gpt-4.1-mini 2025-04-14 GlobalStandard", exception.Message, StringComparison.Ordinal);
        Assert.Contains("openai gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("claude-sonnet-5", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("llama3.1", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void EnsurePriced_AcceptsPricedAndExplicitlyUnpricedIdentities()
    {
        ModelPricing.EnsurePriced(
            [
                AzureRegional,
                new PricingIdentity("anthropic", "claude-sonnet-5"),
                new PricingIdentity("openai", "gpt-4o"),
                new PricingIdentity("openai", "llama3.1", Unpriced: true)
            ],
            AfterIntroductoryPricing);
    }
}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ModelPricingTests"`

Expected: the build fails with `error CS0246: The type or namespace name 'PricingIdentity' could not be found`. This was checked on the scratch copy.

- [ ] **Step 3: Implement `PricingIdentity` and identity-keyed `ModelPricing`**

Create `src/ReleaseLens.Llm/Providers/PricingIdentity.cs`:

```csharp
namespace ReleaseLens.Llm.Providers;

/// <summary>
/// What a provider's calls are billed as. Pricing reads this, never the <c>model</c> string a
/// response carries: that string is whatever the endpoint chose to echo (a versioned snapshot
/// name, or an Azure deployment name), and pricing by it would price a versioned snapshot name
/// at $0 (F2).
/// </summary>
/// <param name="Unpriced">
/// Set only for runtimes that do not bill per token, such as Ollama behind the OpenAI-compatible
/// provider. It is the one way an identity is allowed to cost nothing.
/// </param>
public sealed record PricingIdentity(
    string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false);

/// <summary>A provider that knows the identity its calls are billed under.</summary>
public interface IPricedChatProvider : IChatProvider
{
    PricingIdentity Pricing { get; }
}
```

Replace `src/ReleaseLens.Llm/Providers/ModelPricing.cs` with:

```csharp
namespace ReleaseLens.Llm.Providers;

/// <summary>
/// USD per million tokens. Sonnet 5 introductory pricing runs to 31 August 2026
/// inclusive, then reverts — so cost attribution has to be date-aware or every
/// figure in the eval report goes wrong on 1 September.
/// </summary>
/// <remarks>
/// Rates are keyed by <see cref="PricingIdentity"/>. Every provider reports
/// <see cref="TokenUsage.InputTokens"/> as uncached input, with cached input separately in
/// <see cref="TokenUsage.CacheReadInputTokens"/>, so each token is billed exactly once.
/// </remarks>
public static class ModelPricing
{
    private static readonly DateOnly SonnetIntroductoryEnds = new(2026, 8, 31);

    private sealed record Rates(
        decimal InputPerMillion, decimal CachedInputPerMillion, decimal CacheWritePerMillion, decimal OutputPerMillion)
    {
        /// <summary>Anthropic bills cache reads at a tenth of fresh input and cache writes at 1.25x.</summary>
        public static Rates Anthropic(decimal input, decimal output) => new(input, input * 0.1m, input * 1.25m, output);
    }

    /// <summary>
    /// Name-based pricing, kept for test fakes that report no <see cref="PricingIdentity"/>.
    /// An unknown name costs $0, which is why no real provider is priced through here.
    /// </summary>
    public static decimal CostUsd(string model, TokenUsage usage, DateOnly asOf)
    {
        var rates = ResolveAnthropic(model, asOf) ?? ResolveOpenAi(model);
        return rates is null ? 0m : Cost(rates, usage);
    }

    /// <summary>
    /// Prices one call under the identity it was billed as. An identity with no rate throws
    /// rather than costing $0: <see cref="EnsurePriced"/> is meant to have refused it at startup.
    /// </summary>
    public static decimal CostUsd(PricingIdentity identity, TokenUsage usage, DateOnly asOf)
    {
        if (identity.Unpriced)
        {
            return 0m;
        }

        var rates = Resolve(identity, asOf)
            ?? throw new InvalidOperationException(
                $"No rate for pricing identity {Describe(identity)}. Add it to ModelPricing, " +
                "or mark a runtime that does not bill per token as unpriced.");

        return Cost(rates, usage);
    }

    /// <summary>Whether a rate exists for this exact identity. The Unpriced flag is not a rate.</summary>
    public static bool HasRate(PricingIdentity identity, DateOnly asOf) => Resolve(identity, asOf) is not null;

    /// <summary>
    /// Fails if any identity that is not marked unpriced has no rate, naming every one of them,
    /// so a misconfigured provider stops the app at startup instead of pricing its calls at $0.
    /// </summary>
    public static void EnsurePriced(IEnumerable<PricingIdentity> identities, DateOnly asOf)
    {
        var missing = identities
            .Where(identity => !identity.Unpriced && !HasRate(identity, asOf))
            .Select(Describe)
            .Distinct(StringComparer.Ordinal)
            .ToList();

        if (missing.Count > 0)
        {
            throw new InvalidOperationException(
                $"No rate for pricing identity: {string.Join("; ", missing)}. Add each to ModelPricing, " +
                "or mark a runtime that does not bill per token as unpriced.");
        }
    }

    /// <summary>
    /// Minimum cacheable prefix, which is model-dependent and NOT monotonic.
    /// A 2,000-token system prompt caches on Sonnet 5 and silently does not on
    /// Haiku 4.5, with no error raised. Verify with usage.cache_read_input_tokens.
    /// </summary>
    public static int MinimumCacheablePrefixTokens(string model) => model switch
    {
        "claude-opus-5" => 512,
        "claude-sonnet-5" => 1024,
        "claude-haiku-4-5-20251001" => 4096,
        _ => 1024
    };

    private static decimal Cost(Rates rates, TokenUsage usage)
        => ((usage.InputTokens * rates.InputPerMillion)
          + (usage.OutputTokens * rates.OutputPerMillion)
          + (usage.CacheReadInputTokens * rates.CachedInputPerMillion)
          + (usage.CacheCreationInputTokens * rates.CacheWritePerMillion)) / 1_000_000m;

    // Anthropic and OpenAI identities carry no version or deployment type; one that does is
    // not the thing these rates were read for, so it gets no rate rather than a guessed one.
    private static Rates? Resolve(PricingIdentity identity, DateOnly asOf) => identity switch
    {
        { Provider: "anthropic", Version: null, DeploymentType: null } => ResolveAnthropic(identity.Model, asOf),
        { Provider: "openai", Version: null, DeploymentType: null } => ResolveOpenAi(identity.Model),

        // Azure Retail Prices API, australiaeast, read 2026-09-24: regional Standard, USD per
        // 1M tokens for input, cached input and output. The OpenAI wire reports no cache
        // writes, so none is charged.
        { Provider: "azure-openai", Model: "gpt-4.1-mini", Version: "2025-04-14", DeploymentType: "Standard" }
            => new Rates(0.44m, 0.11m, 0m, 1.76m),

        _ => null
    };

    private static Rates? ResolveAnthropic(string model, DateOnly asOf) => model switch
    {
        "claude-opus-5" => Rates.Anthropic(15m, 75m),
        "claude-sonnet-5" => asOf <= SonnetIntroductoryEnds ? Rates.Anthropic(2m, 10m) : Rates.Anthropic(3m, 15m),
        "claude-haiku-4-5-20251001" => Rates.Anthropic(1m, 5m),
        "claude-fable-5" => Rates.Anthropic(1m, 5m),
        _ => null
    };

    // For a model whose OpenAI cached-input rate has not been read and dated, cached tokens are
    // priced at the full input rate, an upper bound (a recorded departure from spec section 4.6).
    // A model added from OpenAI's pricing page gets its own cached rate from that page. The
    // OpenAI wire reports no cache writes, so none is charged.
    private static Rates? ResolveOpenAi(string model) => model switch
    {
        "gpt-4o" => new Rates(2.5m, 2.5m, 0m, 10m),
        "gpt-4o-mini" => new Rates(0.15m, 0.15m, 0m, 0.6m),
        _ => null
    };

    private static string Describe(PricingIdentity identity)
        => string.Join(' ', new[] { identity.Provider, identity.Model, identity.Version, identity.DeploymentType }
            .Where(part => !string.IsNullOrEmpty(part)));
}
```

What changed, and why:
- The name-based overload still prices Anthropic names with the 0.1x / 1.25x multipliers, as before. For `gpt-4o` and `gpt-4o-mini`, it now prices cached tokens at the input rate instead of 0.1x input. Only fakes reach this overload, because they report no `Pricing`, and every fake in the repository uses `claude-sonnet-5`.
- `CostUsd(PricingIdentity, …)` throws for a priced identity that has no rate. That is how the "never priced at $0" rule holds at run time. Task 10's startup `EnsurePriced` stops it happening in practice.
- Identities are matched exactly. The Azure rate applies only to `Standard` with version `2025-04-14`. `GlobalStandard`, or another version, has no rate and fails `EnsurePriced`.
- On the OpenAI wire, Azure included, `metadata.tokensIn` and the tenant's daily token budget now count uncached input only, and cached input is reported as `cacheReadInputTokens`. That matches what Anthropic already did, where the budget never counted cache reads. Before this task an OpenAI cached token counted once toward the budget and was billed twice; now it is billed once and does not count toward the budget. (The change itself is Step 7's `ParseUsage`; `Program.cs` records only `InputTokens` and `OutputTokens` against the budget.)

- [ ] **Step 4: Run it and watch it pass**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ModelPricingTests"`

Expected: `Passed!  - Failed: 0, Passed: 15`.

- [ ] **Step 5: Write the failing tests — providers report their identity, and the codec bills cached tokens once (T-C2, T-C4, T-F2)**

**5a.** Create `tests/ReleaseLens.Llm.Tests/ProviderPricingTests.cs`:

```csharp
using System;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// What each provider reports it is billed as, and what the OpenAI-wire codec does to usage
/// before it is priced. The rates themselves are pinned in <see cref="ModelPricingTests"/>.
/// </summary>
public class ProviderPricingTests
{
    private static readonly DateOnly PricedOn = new(2026, 9, 24);

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    private static OpenAiChatProvider OpenAi(StubHttpMessageHandler handler, bool unpriced = false) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o", Unpriced = unpriced });

    private static AnthropicChatProvider Anthropic(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = "claude-sonnet-5" });

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    private static string OpenAiWireResponse(
        string? model, int promptTokens, int completionTokens, int? cachedTokens, string finishReason = "stop")
    {
        var modelField = model is null ? string.Empty : $"\"model\": \"{model}\",";
        var details = cachedTokens is null
            ? string.Empty
            : $", \"prompt_tokens_details\": {{ \"cached_tokens\": {cachedTokens} }}";

        return $$"""
        {
          "id": "chatcmpl-1",
          {{modelField}}
          "choices": [{ "index": 0, "finish_reason": "{{finishReason}}",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
          "usage": { "prompt_tokens": {{promptTokens}}, "completion_tokens": {{completionTokens}}{{details}} }
        }
        """;
    }

    private static ChatResponse ParseAsAzure(string body)
    {
        using var document = JsonDocument.Parse(body);
        return OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "rl-gpt41mini", AzureRegional);
    }

    [Fact]
    public void OpenAi_ReportsItsConfiguredModelAsItsPricingIdentity()
    {
        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), OpenAi(new StubHttpMessageHandler()).Pricing);
    }

    [Fact]
    public void OpenAi_MarkedUnpriced_SaysSoInItsPricingIdentity()
    {
        Assert.Equal(new PricingIdentity("openai", "gpt-4o", Unpriced: true),
            OpenAi(new StubHttpMessageHandler(), unpriced: true).Pricing);
    }

    [Fact]
    public async Task Anthropic_ResponseCarriesTheProvidersPricingIdentity()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "id": "msg_01",
          "model": "claude-sonnet-5",
          "stop_reason": "end_turn",
          "content": [{ "type": "text", "text": "Release 1.30 fixed the planner." }],
          "usage": { "input_tokens": 1200, "output_tokens": 45, "cache_read_input_tokens": 1024, "cache_creation_input_tokens": 0 }
        }
        """);
        var provider = Anthropic(handler);

        var response = await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal(new PricingIdentity("anthropic", "claude-sonnet-5"), provider.Pricing);
        Assert.Equal(provider.Pricing, response.Pricing);

        // Anthropic already reports input_tokens without the cache reads, so nothing is subtracted.
        Assert.Equal(1200, response.Usage.InputTokens);
        Assert.Equal(1024, response.Usage.CacheReadInputTokens);
    }

    // T-F2
    [Fact]
    public async Task OpenAi_VersionedModelNameInTheResponse_IsPricedAtTheConfiguredModelsRate()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o-2024-08-06", promptTokens: 1_000_000, completionTokens: 1_000_000, cachedTokens: null));

        var response = await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("gpt-4o-2024-08-06", response.Model);
        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), response.Pricing);
        Assert.Equal(12.50m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));

        // The defect this replaces: pricing by the echoed name found no rate and returned $0.
        Assert.Equal(0m, ModelPricing.CostUsd(response.Model, response.Usage, PricedOn));
    }

    // T-C4's billed-once half on OpenAI; its cached-rate half is a recorded departure from spec
    // section 4.6, because gpt-4o's OpenAI cached-input rate has not been read and dated.
    [Fact]
    public async Task OpenAi_CachedTokens_AreReportedOutsideInputAndBilledOnce()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024));

        var response = await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal(new TokenUsage(176, 45, 1024, 0), response.Usage);

        // 1,200 prompt tokens at 2.50 plus 45 at 10.00: each prompt token is counted once, the
        // cached 1,024 at the input rate rather than on top of it.
        Assert.Equal(0.00345m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    // T-C4, on a model whose cached rate is known
    [Fact]
    public void AzureWire_CachedTokens_AreBilledOnceAtTheCachedRate()
    {
        var response = ParseAsAzure(
            OpenAiWireResponse("gpt-4.1-mini", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024));

        Assert.Equal(new TokenUsage(176, 45, 1024, 0), response.Usage);

        // 176 x 0.44 + 1,024 x 0.11 + 45 x 1.76 = 77.44 + 112.64 + 79.20 = 269.28 per million.
        Assert.Equal(0.00026928m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    // T-C2
    [Theory]
    [InlineData("gpt-4.1-mini")]
    [InlineData("gpt-4.1-mini-2025-04-14")]
    [InlineData("rl-gpt41mini")]
    [InlineData(null)]
    public void AzureWire_WhateverModelStringTheResponseCarries_ThePriceIsTheSame(string? model)
    {
        var response = ParseAsAzure(
            OpenAiWireResponse(model, promptTokens: 12_345, completionTokens: 678, cachedTokens: 0));

        Assert.Equal(model ?? "rl-gpt41mini", response.Model);
        Assert.Equal(AzureRegional, response.Pricing);

        // 12,345 x 0.44 + 678 x 1.76 = 5,431.80 + 1,193.28 = 6,625.08 per million.
        Assert.Equal(0.00662508m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    [Fact]
    public void OpenAiWire_CachedTokensAbovePromptTokens_NeverReportNegativeInput()
    {
        var response = ParseAsAzure(
            OpenAiWireResponse("gpt-4.1-mini", promptTokens: 100, completionTokens: 5, cachedTokens: 150));

        Assert.Equal(0, response.Usage.InputTokens);
        Assert.Equal(150, response.Usage.CacheReadInputTokens);
    }

    [Fact]
    public async Task OpenAi_FilteredCompletion_CarriesThePricingIdentityForItsUsage()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024,
                finishReason: "content_filter"));

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), exception.Pricing);
        Assert.Equal(new TokenUsage(176, 45, 1024, 0), exception.Usage);
    }
}
```

**5b.** In `tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs` (Task 2's file), add `, pricing: null` as the last argument of each of the five `OpenAiWireFormat.Parse(...)` calls. After the edit they read as below; the last line occurs twice, once in `Parse_NoChoices_ThrowsNamingTheProvider` and once in `Parse_NoUsage_ThrowsNamingTheProvider`:

```csharp
        var response = OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "fallback-model", pricing: null);
```
```csharp
            OpenAiWireFormat.Parse(document.RootElement, "openai", "fallback-model", pricing: null).Model);
```
```csharp
        var response = OpenAiWireFormat.Parse(document.RootElement, "openai", "gpt-4.1-mini", pricing: null);
```
```csharp
            OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "gpt-4.1-mini", pricing: null));
```

In the same file, replace the whole of `ParseUsage_ReadsPromptCompletionAndCachedTokens` with:

```csharp
    [Fact]
    public void ParseUsage_ReportsUncachedInputAndCachedTokensSeparately()
    {
        using var document = JsonDocument.Parse("""
        { "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                     "prompt_tokens_details": { "cached_tokens": 1024 } } }
        """);

        var usage = OpenAiWireFormat.ParseUsage(document.RootElement);

        // prompt_tokens includes the cached tokens; InputTokens is the uncached remainder.
        Assert.Equal(176, usage.InputTokens);
        Assert.Equal(45, usage.OutputTokens);
        Assert.Equal(1024, usage.CacheReadInputTokens);
        Assert.Equal(0, usage.CacheCreationInputTokens);
    }
```

**5c.** In `tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs`, the fixture `TextResponse` reports `prompt_tokens: 1200` with `cached_tokens: 1024`, so `InputTokens` becomes 176. Replace the test `Complete_ReturnsTextAndMapsCachedPromptTokens` with:

```csharp
    [Fact]
    public async Task Complete_ReturnsTextAndMapsCachedPromptTokens()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var response = await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);

        // prompt_tokens (1,200) includes the 1,024 cached; InputTokens is the uncached remainder.
        Assert.Equal(176, response.Usage.InputTokens);
        Assert.Equal(45, response.Usage.OutputTokens);
        Assert.Equal(1024, response.Usage.CacheReadInputTokens);
        Assert.Equal("stop", response.StopReason);
    }
```

- [ ] **Step 6: Run them and watch them fail**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ProviderPricingTests|FullyQualifiedName~OpenAiWireFormatTests|FullyQualifiedName~OpenAiChatProviderTests"`

Expected: the build fails. On the scratch copy it reported these errors:
- `error CS1061: 'ChatResponse' does not contain a definition for 'Pricing'`
- `error CS1061: 'ContentFilteredException' does not contain a definition for 'Pricing'`
- `error CS0117: 'OpenAiOptions' does not contain a definition for 'Unpriced'`
- `error CS1061: 'OpenAiChatProvider' does not contain a definition for 'Pricing'`
- `error CS1061: 'AnthropicChatProvider' does not contain a definition for 'Pricing'`
- `error CS1501: No overload for method 'Parse' takes 4 arguments`
- `error CS1739: The best overload for 'Parse' does not have a parameter named 'pricing'`

- [ ] **Step 7: Implement — `ChatResponse.Pricing`, the codec's usage normalisation, the providers' identities, and the README's Ollama snippet**

**7a.** In `src/ReleaseLens.Llm/Providers/ChatContracts.cs`, replace the `ChatResponse` record with:

```csharp
/// <param name="Model">The model string the endpoint echoed. It is recorded, and priced only when
/// <paramref name="Pricing"/> is null, which no real provider leaves it.</param>
/// <param name="Pricing">The identity this call is billed under; null only for test fakes.</param>
public sealed record ChatResponse(
    string? Text,
    IReadOnlyList<ToolCall> ToolCalls,
    TokenUsage Usage,
    string StopReason,
    string Model,
    string Provider,
    PricingIdentity? Pricing = null);
```

and replace the `ContentFilteredException` class Task 3 added, together with its XML doc comment, with:

```csharp
/// <summary>
/// The provider's content filter blocked the prompt or the completion. This is neither our
/// bug nor the provider being unavailable, so the fallback chain lets it through untouched:
/// answering the same request on another provider would route around the filter.
/// </summary>
/// <remarks>
/// It carries the usage the provider reported so the caller can count and price the call like
/// any other; whether a provider bills a filtered call is unverified. It deliberately carries
/// no text: a filtered completion can arrive with partial content, and none of it may reach
/// the caller.
/// </remarks>
public sealed class ContentFilteredException(
    string providerName, ContentFilterStage stage, TokenUsage usage, PricingIdentity? pricing = null)
    : Exception($"{providerName} content filter blocked the {stage.ToString().ToLowerInvariant()}.")
{
    public string ProviderName { get; } = providerName;
    public ContentFilterStage Stage { get; } = stage;
    public TokenUsage Usage { get; } = usage;

    /// <summary>The identity the filtered call's usage is billed under; null only for test fakes.</summary>
    public PricingIdentity? Pricing { get; } = pricing;
}
```

The doc comment is Task 3's, unchanged. The only additions are the `pricing` parameter and the `Pricing` property with its one-line summary.

**7b.** In `src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs`, replace `ParseUsage` and `Parse` with the two members below. `BuildPayload`, `ToolContent` and `IsContentFiltered` are unchanged.

```csharp
    /// <summary>
    /// Normalises OpenAI-wire usage to the shape every provider reports: <c>prompt_tokens</c>
    /// includes the cached tokens, so they are taken out of <see cref="TokenUsage.InputTokens"/>
    /// and reported only as <see cref="TokenUsage.CacheReadInputTokens"/>, and each token is
    /// priced once. That <c>prompt_tokens</c> includes them is unverified against current
    /// documentation (spec section 11); if it proves wrong, only this method changes.
    /// </summary>
    public static TokenUsage ParseUsage(JsonElement root)
    {
        var usage = root.TryGetProperty("usage", out var usageElement) ? usageElement : default;

        var details = usage.ValueKind == JsonValueKind.Object
                      && usage.TryGetProperty("prompt_tokens_details", out var detailsElement)
            ? detailsElement
            : default;

        var promptTokens = ProviderHttp.ReadInt(usage, "prompt_tokens");
        var cachedTokens = ProviderHttp.ReadInt(details, "cached_tokens");

        return new TokenUsage(
            // A response reporting more cached than prompt tokens would otherwise give negative
            // input, which prices as a credit.
            Math.Max(0, promptTokens - cachedTokens),
            ProviderHttp.ReadInt(usage, "completion_tokens"),
            cachedTokens,
            0);
    }

    public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel, PricingIdentity? pricing)
    {
        // A 200 with nothing to read is a malformed reply, not an empty answer. Indexing into
        // it would throw KeyNotFoundException or IndexOutOfRangeException, naming no provider.
        if (!root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no choices.");
        }

        // A success with no usage would count as zero tokens and be priced at $0, with nothing
        // to show that it was not free.
        if (!root.TryGetProperty("usage", out var usageElement) || usageElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no usage.");
        }

        var usage = ParseUsage(root);
        var choice = choices[0];

        // Checked before the message is read: a filtered completion can still carry partial
        // text, and none of it may become an answer.
        if (IsContentFiltered(choice))
        {
            throw new ContentFilteredException(providerName, ContentFilterStage.Completion, usage, pricing);
        }

        var message = choice.GetProperty("message");

        var text = message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String
            ? content.GetString()
            : null;

        var toolCalls = new List<ToolCall>();
        if (message.TryGetProperty("tool_calls", out var calls) && calls.ValueKind == JsonValueKind.Array)
        {
            foreach (var call in calls.EnumerateArray())
            {
                var function = call.GetProperty("function");
                var rawArguments = function.GetProperty("arguments").GetString() ?? "{}";

                // OpenAI sends arguments as a JSON-encoded string, not an object. Clone the
                // parsed element before `parsed` is disposed, or it reads freed memory.
                using var parsed = JsonDocument.Parse(rawArguments);

                toolCalls.Add(new ToolCall(
                    call.GetProperty("id").GetString()!,
                    function.GetProperty("name").GetString()!,
                    parsed.RootElement.Clone()));
            }
        }

        return new ChatResponse(
            text,
            toolCalls,
            usage,
            choice.GetProperty("finish_reason").GetString() ?? "unknown",
            root.TryGetProperty("model", out var model) ? model.GetString() ?? fallbackModel : fallbackModel,
            providerName,
            pricing);
    }
```

`ParseUsage` is new in full. It subtracts the cached tokens from `prompt_tokens`, floored at zero. It now reads `cached_tokens` through `ProviderHttp.ReadInt`, as it already read `prompt_tokens`, so a non-numeric `cached_tokens` counts as 0 instead of throwing; for such a response every prompt token is priced at the input rate, an upper bound.

`Parse` is Task 3's `Parse` with exactly three changes:
1. the signature gains `PricingIdentity? pricing` as its fourth parameter;
2. the completion-filter `throw` passes `pricing` to `ContentFilteredException` as the fourth argument;
3. the returned `ChatResponse` passes `pricing` as its seventh argument.

Every other line, the guard messages included, is Task 2's text as Task 3 left it.

**7c.** Replace `src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs` with the file below. Compared with the file Tasks 1–2 leave, the changes are: `OpenAiOptions.Unpriced`; `: IPricedChatProvider`; the `Pricing` property and its assignment in the constructor; and the fourth argument to `OpenAiWireFormat.Parse`. If Task 2 left anything else in this file, keep it.

```csharp
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace ReleaseLens.Llm.Providers;

public sealed class OpenAiOptions
{
    public const string SectionName = "OpenAi";

    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = "gpt-4o";

    /// <summary>
    /// Any endpoint speaking the OpenAI wire format. Point this at
    /// http://localhost:11434/v1/ for Ollama, or a vLLM or LM Studio address,
    /// and the whole query path runs with no data leaving the network.
    /// </summary>
    public string BaseUrl { get; set; } = "https://api.openai.com/v1/";

    public int MaxTokens { get; set; } = 2048;

    /// <summary>
    /// True for a local runtime that does not bill per token (Ollama, vLLM, LM Studio). It is
    /// the only way this provider's calls may cost $0; a hosted model with no rate fails the
    /// startup pricing check instead.
    /// </summary>
    public bool Unpriced { get; set; }
}

public sealed class OpenAiChatProvider : IPricedChatProvider
{
    private readonly HttpClient _client;
    private readonly OpenAiOptions _options;

    public string Name => "openai";

    public PricingIdentity Pricing { get; }

    public OpenAiChatProvider(HttpClient client, OpenAiOptions options)
    {
        _client = client;
        _options = options;
        Pricing = new PricingIdentity("openai", options.Model, Unpriced: options.Unpriced);

        _client.BaseAddress ??= new Uri(options.BaseUrl);

        // Local runtimes ignore the header, and sending "Bearer " with an empty
        // value can make some of them reject the request outright.
        if (!string.IsNullOrEmpty(options.ApiKey))
        {
            _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", options.ApiKey);
        }
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Model);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return OpenAiWireFormat.Parse(document.RootElement, Name, _options.Model, Pricing);
        }
    }
}
```

**7d.** In `src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs`, replace the class declaration, the fields, `Name` and the start of the constructor:

```csharp
public sealed class AnthropicChatProvider : IChatProvider
{
    private readonly HttpClient _client;
    private readonly AnthropicOptions _options;

    public string Name => "anthropic";

    public AnthropicChatProvider(HttpClient client, AnthropicOptions options)
    {
        _client = client;
        _options = options;
```

with:

```csharp
public sealed class AnthropicChatProvider : IPricedChatProvider
{
    private readonly HttpClient _client;
    private readonly AnthropicOptions _options;

    public string Name => "anthropic";

    public PricingIdentity Pricing { get; }

    public AnthropicChatProvider(HttpClient client, AnthropicOptions options)
    {
        _client = client;
        _options = options;
        Pricing = new PricingIdentity("anthropic", options.Model);
```

The rest of the constructor (`BaseAddress` and the two headers) is unchanged. At the end of `Parse`, replace:

```csharp
            root.GetProperty("stop_reason").GetString() ?? "unknown",
            root.GetProperty("model").GetString() ?? _options.Model,
            Name);
```

with:

```csharp
            root.GetProperty("stop_reason").GetString() ?? "unknown",
            root.GetProperty("model").GetString() ?? _options.Model,
            Name,
            Pricing);
```

Anthropic's `input_tokens` already excludes cache reads and writes, so its usage mapping does not change.

**7e.** In `README.md`, under "Fully local, no egress", replace:

```json
{ "OpenAi": { "BaseUrl": "http://localhost:11434/v1/", "Model": "llama3.1" } }
```

with:

```json
{ "OpenAi": { "BaseUrl": "http://localhost:11434/v1/", "Model": "llama3.1", "Unpriced": true } }
```

From this task `OpenAiChatProvider.Pricing` is `("openai", "llama3.1")` unless the flag is set. That identity has no rate, so without the flag every Ollama query throws in `ModelPricing.CostUsd`, and from Task 10 the startup `EnsurePriced` check refuses to start.

- [ ] **Step 8: Run them and watch them pass**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ProviderPricingTests|FullyQualifiedName~OpenAiWireFormatTests|FullyQualifiedName~OpenAiContentFilterTests|FullyQualifiedName~OpenAiChatProviderTests|FullyQualifiedName~AnthropicChatProviderTests|FullyQualifiedName~FallbackChatProviderTests|FullyQualifiedName~ModelPricingTests"`

Expected: `Failed: 0`. Among the passed tests: every `ProviderPricingTests` test (`OpenAi_VersionedModelNameInTheResponse_IsPricedAtTheConfiguredModelsRate`, `OpenAi_CachedTokens_AreReportedOutsideInputAndBilledOnce`, `AzureWire_CachedTokens_AreBilledOnceAtTheCachedRate`, the four rows of `AzureWire_WhateverModelStringTheResponseCarries_ThePriceIsTheSame`, `OpenAi_FilteredCompletion_CarriesThePricingIdentityForItsUsage` and the rest), `OpenAiWireFormatTests.ParseUsage_ReportsUncachedInputAndCachedTokensSeparately`, and the rewritten `OpenAiChatProviderTests.Complete_ReturnsTextAndMapsCachedPromptTokens`.

- [ ] **Step 9: Write the failing test — a query that changes provider is priced per iteration (T-C5)**

In `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs`, insert these members straight after the closing brace of `Answer_AccumulatesUsageAcrossIterations`, before `Answer_StopsAtMaxIterations_AndStillProducesAnAnswer`. They reuse the class's `ScriptedProvider`, `SeedAsync`, `Text`, `CallTool` and `Build`. `Text` gives 1,000 in and 50 out; `CallTool` gives 800 in and 30 out. The file's existing usings already cover everything used here.

```csharp
    private static readonly PricingIdentity Haiku = new("anthropic", "claude-haiku-4-5-20251001");

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    /// <summary>800 in, 30 out: 0.00095 at Haiku 4.5's 1.00/5.00, 0.0004048 at Azure's 0.44/1.76.</summary>
    private static ChatResponse PricedCallTool(string provider, string model, PricingIdentity pricing) =>
        CallTool("search_commits", """{"query":"planner"}""") with
        {
            Provider = provider, Model = model, Pricing = pricing
        };

    /// <summary>1,000 in, 50 out: 0.00125 at Haiku 4.5, 0.000528 at Azure.</summary>
    private static ChatResponse PricedText(string text, string provider, string model, PricingIdentity pricing) =>
        Text(text) with { Provider = provider, Model = model, Pricing = pricing };

    // T-C5
    [Fact]
    public async Task Answer_ProviderChangesMidQuery_IsPricedPerIteration()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-per-iteration");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => PricedCallTool("anthropic", "claude-haiku-4-5-20251001", Haiku),
            () => PricedText("Found it in [E1].", "azure-openai", "gpt-4.1-mini-2025-04-14", AzureRegional)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        // 0.00095 for the Anthropic iteration plus 0.000528 for the Azure one. Pricing the whole
        // query at the last provider's rate, as before, would give 0.0009328.
        Assert.Equal(0.001478m, answer.Metadata.CostUsd);
        Assert.Equal("azure-openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_CeilingFinalCall_IsPricedAtItsOwnProvidersRate()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => PricedCallTool("anthropic", "claude-haiku-4-5-20251001", Haiku));
        }

        script.Enqueue(() => PricedText("From the evidence so far: [E1].", "azure-openai", "gpt-4.1-mini", AzureRegional));

        var answer = await Build(new ScriptedProvider(script)).AnswerAsync(
            scope, "planner?", 5, TestContext.Current.CancellationToken);

        // Six Anthropic iterations at 0.00095 each, plus the no-tools final call at Azure's 0.000528.
        Assert.Equal(0.006228m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_ProvidersFailAfterAPricedIteration_TheDegradedAnswerKeepsThatCost()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => PricedCallTool("azure-openai", "gpt-4.1-mini-2025-04-14", AzureRegional),
            () => throw new AllProvidersUnavailableException(["azure-openai"])
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);

        // The versioned name has no name-based rate, so pricing it by name gave $0 here.
        Assert.Equal(0.0004048m, answer.Metadata.CostUsd);
    }
```

The Anthropic side uses `claude-haiku-4-5-20251001` (1.00 / 5.00). Its rate does not depend on the date, so the expected figures do not depend on the day the test runs.

- [ ] **Step 10: Run it and watch it fail** (needs Docker: `QueryAgentTests` is in the Postgres Testcontainers collection)

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryAgentTests.Answer_ProviderChangesMidQuery_IsPricedPerIteration|FullyQualifiedName~QueryAgentTests.Answer_CeilingFinalCall_IsPricedAtItsOwnProvidersRate|FullyQualifiedName~QueryAgentTests.Answer_ProvidersFailAfterAPricedIteration_TheDegradedAnswerKeepsThatCost"`

Expected: it builds, and all three fail on `Assert.Equal`, because `QueryAgent` still prices the query's total usage by the last `response.Model`, through the name-based overload. These figures are worked out by hand from the current code, not from a run:
- `Answer_ProviderChangesMidQuery_IsPricedPerIteration`: Expected `0.001478`, Actual `0`. `"gpt-4.1-mini-2025-04-14"` has no name-based rate.
- `Answer_CeilingFinalCall_IsPricedAtItsOwnProvidersRate`: Expected `0.006228`, Actual `0.00695`. All 5,800 in and 230 out are priced at Haiku's rate, because the final call never updates `modelName`.
- `Answer_ProvidersFailAfterAPricedIteration_TheDegradedAnswerKeepsThatCost`: Expected `0.0004048`, Actual `0`.

- [ ] **Step 11: Implement per-iteration cost in `QueryAgent`**

In `src/ReleaseLens.Llm/Agent/QueryAgent.cs`, make these five edits inside `AnswerAsync`. None of the anchors includes a line Task 1 changed (the `ChatRequest` constructions and the `modelName` initialiser), so they apply as written whatever Task 1 chose for those lines.

(1) Replace:

```csharp
        var usage = TokenUsage.Zero;
        var toolsCalled = new List<string>();
```

with:

```csharp
        var usage = TokenUsage.Zero;
        var cost = 0m;

        // One date for the whole query, so a query spanning midnight on a price change is not
        // priced half at each rate.
        var pricedOn = DateOnly.FromDateTime(DateTime.UtcNow);
        var toolsCalled = new List<string>();
```

(2) In the loop, replace:

```csharp
                usage += response.Usage;
                providerName = response.Provider;
```

with:

```csharp
                usage += response.Usage;
                cost += CostOf(response, pricedOn);
                providerName = response.Provider;
```

(3) In the tool-ceiling block, replace:

```csharp
                    usage += final.Usage;
                    answerText = final.Text ?? string.Empty;
```

with:

```csharp
                    usage += final.Usage;
                    cost += CostOf(final, pricedOn);
                    answerText = final.Text ?? string.Empty;
```

(4) In the `catch (AllProvidersUnavailableException unavailable)` block's `new AgentMetadata(...)`, replace the argument line:

```csharp
                    ModelPricing.CostUsd(modelName, usage, DateOnly.FromDateTime(DateTime.UtcNow)),
```

with:

```csharp
                    cost,
```

Keep the four-line comment above it ("Providers can fail on iteration 2+ …"), which still describes the argument.

(5) After the loop, delete this line. The `cost` local from edit 1 replaces it, and the later `activity?.SetTag("cost_usd", (double)cost)` and `new AgentMetadata(..., usage, cost, ...)` now read the running total:

```csharp
        var cost = ModelPricing.CostUsd(modelName, usage, DateOnly.FromDateTime(DateTime.UtcNow));
```

Then add this private member directly above `DescribeRepositoryAsync`, above its `/// <summary>`:

```csharp
    /// <summary>
    /// One call's cost, under the identity it was billed as. Each call is priced on its own
    /// because a query can change provider part-way, and pricing the total at the last
    /// provider's rate misprices every earlier call. The response's model string is priced
    /// only for test fakes that report no identity.
    /// </summary>
    private static decimal CostOf(ChatResponse response, DateOnly asOf)
        => response.Pricing is { } pricing
            ? ModelPricing.CostUsd(pricing, response.Usage, asOf)
            : ModelPricing.CostUsd(response.Model, response.Usage, asOf);
```

`modelName` is still assigned and still reported as `AgentMetadata.Model`. It is recorded, never priced.

- [ ] **Step 12: Run it and watch it pass** (needs Docker)

Run: the same command as Step 10.

Expected: `Passed!  - Failed: 0, Passed: 3`.

- [ ] **Step 13: Run the whole affected test projects**

```bash
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceTool"
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

- `dotnet build ReleaseLens.sln`: `0 Warning(s)`, `0 Error(s)`. `TreatWarningsAsErrors` is on. This held on the scratch copy.
- The second command needs no Docker. It runs every Llm test outside the Postgres collection; expect `Failed: 0`, with the new `ModelPricingTests` and `ProviderPricingTests` among the passed.
- The third and fourth need Docker. `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` in the Llm project, and `QueryEndpointTests` and `EvidenceEndpointTests` in the Api project, use the `PostgresCollection`. The Api project's `ScriptedChatProvider` reports no `Pricing` and uses `claude-sonnet-5`, so it is still priced by name, as before. `Answer_AccumulatesUsageAcrossIterations` (`CostUsd > 0`) passes for the same reason.

Expected: every run reports `Failed: 0`.

- [ ] **Step 14: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```bash
git add src/ReleaseLens.Llm/Providers/PricingIdentity.cs \
        src/ReleaseLens.Llm/Providers/ModelPricing.cs \
        src/ReleaseLens.Llm/Providers/ChatContracts.cs \
        src/ReleaseLens.Llm/Providers/OpenAiWireFormat.cs \
        src/ReleaseLens.Llm/Providers/OpenAiChatProvider.cs \
        src/ReleaseLens.Llm/Providers/AnthropicChatProvider.cs \
        src/ReleaseLens.Llm/Agent/QueryAgent.cs \
        tests/ReleaseLens.Llm.Tests/ModelPricingTests.cs \
        tests/ReleaseLens.Llm.Tests/ProviderPricingTests.cs \
        tests/ReleaseLens.Llm.Tests/OpenAiWireFormatTests.cs \
        tests/ReleaseLens.Llm.Tests/OpenAiChatProviderTests.cs \
        tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs \
        README.md
git commit -F - <<'EOF'
fix(pricing): price each call by the identity it is billed under

A response's model string is recorded, never priced: a versioned name such as
gpt-4o-2024-08-06 found no rate and cost $0 (F2). Each provider now reports a
PricingIdentity, and ModelPricing keys rates by it, with a cached-input rate
per model; a priced identity with no rate throws, and EnsurePriced names every
such identity for the startup check.

The OpenAI-wire codec reports InputTokens as prompt_tokens minus cached_tokens,
so cached tokens are billed once (F4). As on Anthropic, /query's tokensIn and
the tenant's daily token budget now count uncached input only. Azure
gpt-4.1-mini 2025-04-14 Standard is priced at 0.44 / 0.11 / 1.76 per 1M (Azure
Retail Prices API, australiaeast, read 2026-09-24). gpt-4o and gpt-4o-mini have
no read and dated OpenAI cached-input rate, so their cached tokens are priced
at the input rate, an upper bound (a recorded departure from spec section 4.6).

The README's Ollama snippet sets "Unpriced": true: a local model has no rate,
and a priced identity with no rate now throws instead of costing $0.

QueryAgent sums cost per iteration, including the tool-ceiling final call and
the iterations before a degraded answer, instead of pricing the whole query
at the last provider's rate (the pricing half of F5).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Per-query context, sticky fallback, configurable order (fixes the rest of F5)

This task starts from the tree as Tasks 1–4 leave it:

- `ChatRequest` is `(SystemPrompt, Messages, Tools, MaxTokens)`, with no `Model` (Task 1).
- `AgentOptions` has no `Model`, and `QueryAgent.AnswerAsync` starts `modelName` at `"none"` (Task 1).
- `FallbackChatProvider`'s class summary names `ContentFilteredException` (Task 3).
- `QueryAgent` sums `cost` per call through `CostOf` (Task 4).

It adds the per-query `QueryContext` and puts it on every request `QueryAgent` sends. `FallbackChatProvider` uses it to start at the provider that answered the query's previous call, and never goes back to an earlier one. It also adds `ChatOptions` and `ChatProviderSelection`, which turn a `Chat:Providers` list into providers and fail loudly on a bad list, and `metadata.providers`.

`Program.cs` keeps its fixed `[Anthropic, OpenAI]` chain in this task. Task 10 wires `ChatOptions` and `ChatProviderSelection` into startup. Nothing here waits on a 429 either; Task 9 spends `QueryContext.RateLimitWaitRemaining`.

**Files:**
- Create: `src/ReleaseLens.Llm/Providers/QueryContext.cs`
- Create: `src/ReleaseLens.Llm/Providers/ChatProviderSelection.cs` (`ChatOptions`, `ChatProviderSelection`)
- Modify: `src/ReleaseLens.Llm/Providers/ChatContracts.cs` (record `ChatRequest`: gains a body with `Context`)
- Modify: `src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs` (whole file: `CompleteAsync` rewritten, new private `StartIndex`; `AllProvidersUnavailableException`, the constructor and the Task 3 class summary unchanged)
- Modify: `src/ReleaseLens.Llm/Agent/AgentModels.cs` (`AgentOptions`: new `RateLimitWaitBudgetMs`; `AgentMetadata`: new trailing `Providers`)
- Modify: `src/ReleaseLens.Llm/Agent/QueryAgent.cs` (`AnswerAsync`: seven anchored edits; new private `RecordAnswering`, placed directly after `AnswerAsync`)
- Modify: `src/ReleaseLens.Api/Contracts.cs` (`QueryMetadataDto`: new trailing `Providers`)
- Modify: `src/ReleaseLens.Api/Program.cs` (the `/query` handler's `new QueryMetadataDto(...)`, last argument)
- Test: `tests/ReleaseLens.Llm.Tests/QueryContextTests.cs` (new)
- Test: `tests/ReleaseLens.Llm.Tests/FallbackChatProviderStickinessTests.cs` (new; T-F4)
- Test: `tests/ReleaseLens.Llm.Tests/ChatProviderSelectionTests.cs` (new; T-A1, Review Focus 2)
- Test: `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs` (new members at the end of the class; the providers half of T-A2)
- Test: `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs` (two new tests before `Swagger_IsServed`; the providers half of T-A2 at the wire)

Call sites were checked by grepping `src` and `tests` for `new ChatRequest(`, `ChatRequest(`, `new AgentMetadata(`, `new QueryMetadataDto(`, `new FallbackChatProvider(`, `AgentOptions` and `IChatProvider`.

- **`ChatRequest`** gains an init-only property, not a positional parameter, so every existing construction compiles unchanged. That covers the two in `QueryAgent`, the `Request()` helpers in the provider and fallback tests, and Task 2's and Task 3's tests.
- **`AgentMetadata`** is constructed only in `QueryAgent.AnswerAsync`, twice. **`QueryMetadataDto`** is constructed only in `Program.cs`. Both gain an optional trailing parameter.
- **`FallbackChatProvider`**'s constructor and `Name` do not change, so `Program.cs` and every test that builds one are untouched.
- **`IChatProvider`** does not change, so the fakes need no edit: `ScriptedChatProvider` and `SpyChatProvider` in `tests/ReleaseLens.Api.Tests`, and the private `ScriptedProvider` classes in `QueryAgentTests` and `FallbackChatProviderTests`.
- **`AgentOptions`** is only ever built with an object initialiser or bound from configuration, so a new property with a default breaks nothing.

**Interfaces:**
- Consumes:
  - `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens)` (Task 1)
  - `AgentOptions` with `SectionName`, `MaxIterations`, `MaxTokens`, `SeedRetrievalK` (Task 1)
  - `ChatResponse(string? Text, IReadOnlyList<ToolCall> ToolCalls, TokenUsage Usage, string StopReason, string Model, string Provider, PricingIdentity? Pricing = null)` (Task 4). The fakes here pass six arguments.
  - `ContentFilteredException`, named in the `FallbackChatProvider` summary (Task 3)
  - `QueryAgent.AnswerAsync`'s locals `cost` and `pricedOn`, and its `CostOf` calls (Task 4). No edit below touches them.
- Produces (contract names, exactly):
  - `public sealed class QueryContext(TimeSpan rateLimitWaitBudget)`, with `public string? LastProvider { get; set; }`, `public TimeSpan RateLimitWaitRemaining { get; private set; }` and `public bool TrySpendWait(TimeSpan wait)`. `TrySpendWait` returns true and subtracts when `wait <= RateLimitWaitRemaining`; otherwise it returns false and changes nothing. It throws `ArgumentOutOfRangeException` for a negative `wait`; a zero wait is accepted.
  - `ChatRequest`: `public QueryContext? Context { get; init; }`
  - `public sealed class ChatOptions { public const string SectionName = "Chat"; public List<string> Providers { get; set; } = ["anthropic", "openai"]; }`
  - `public static class ChatProviderSelection { public static IReadOnlyList<IChatProvider> Select(IReadOnlyList<string> names, IReadOnlyDictionary<string, Func<IChatProvider>> factories); }`. It throws `InvalidOperationException` containing `Valid providers: <sorted, comma-separated keys>.` on an empty list, an unknown or differently cased name, or a duplicate. It checks every name before running any factory, and runs only the factories of the names listed.
  - `AgentOptions.RateLimitWaitBudgetMs` (default `3000`)
  - `AgentMetadata(..., IReadOnlyList<string> UnresolvedCitationMarkers, IReadOnlyList<string>? Providers = null)`. `QueryAgent` always sets it: distinct providers, in first-answer order, empty when none answered.
  - `QueryMetadataDto(..., long ElapsedMs, IReadOnlyList<string>? Providers = null)`. `Program.cs` always passes a list, so `metadata.providers` is always a JSON array.
  - `FallbackChatProvider` behaviour:
    - With no `Context`, it behaves exactly as before.
    - With a `Context`, it starts at the member named `LastProvider`, or at index 0 when that is null or not in the chain. It goes forward to the end only, and sets `LastProvider` to the member that answered.
  - In `QueryAgent.AnswerAsync`, the locals `context` (the query's `QueryContext`) and `providers` (`List<string>`). Task 6's filtered path passes `Providers: providers`.

- [ ] **Step 1: Write the failing test for `QueryContext`**

Create `tests/ReleaseLens.Llm.Tests/QueryContextTests.cs`:

```csharp
using System;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class QueryContextTests
{
    [Fact]
    public void New_HasTheWholeBudget_AndNoProvider()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.Equal(TimeSpan.FromSeconds(3), query.RateLimitWaitRemaining);
        Assert.Null(query.LastProvider);
    }

    [Fact]
    public void TrySpendWait_ThatFits_SpendsIt()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.True(query.TrySpendWait(TimeSpan.FromMilliseconds(1200)));
        Assert.Equal(TimeSpan.FromMilliseconds(1800), query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_OfExactlyWhatRemains_SpendsItAll()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.True(query.TrySpendWait(TimeSpan.FromSeconds(3)));
        Assert.Equal(TimeSpan.Zero, query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_ThatDoesNotFit_SpendsNothing()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));
        Assert.True(query.TrySpendWait(TimeSpan.FromSeconds(2)));

        Assert.False(query.TrySpendWait(TimeSpan.FromMilliseconds(1001)));
        Assert.Equal(TimeSpan.FromSeconds(1), query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_Negative_Throws_AndSpendsNothing()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.Throws<ArgumentOutOfRangeException>(() => query.TrySpendWait(TimeSpan.FromMilliseconds(-1)));
        Assert.Equal(TimeSpan.FromSeconds(3), query.RateLimitWaitRemaining);
    }
}
```

- [ ] **Step 2: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryContextTests"
```

Expected: the build fails with `QueryContextTests.cs(12,25): error CS0246: The type or namespace name 'QueryContext' could not be found`, repeated for each test. The compiler also reports `CS0619` on `Assert.Throws` at line 51. That is a side effect of the missing type, and it goes away with it.

- [ ] **Step 3: Implement `QueryContext` and `ChatRequest.Context`**

Create `src/ReleaseLens.Llm/Providers/QueryContext.cs`:

```csharp
namespace ReleaseLens.Llm.Providers;

/// <summary>
/// State that belongs to one query rather than to a provider: which provider answered last,
/// and how much of the query's 429 wait budget is left. <c>QueryAgent</c> creates one per
/// query and puts it on every <see cref="ChatRequest"/>, so the providers and the fallback
/// chain stay stateless singletons and two concurrent queries never share a sticky provider
/// or a budget.
/// </summary>
/// <remarks>
/// Not thread-safe, and it does not need to be: a query's calls run one after another.
/// </remarks>
public sealed class QueryContext(TimeSpan rateLimitWaitBudget)
{
    /// <summary>
    /// The <see cref="IChatProvider.Name"/> of the chain member that answered this query's most
    /// recent call, or null before any has. <see cref="FallbackChatProvider"/> reads and writes it.
    /// </summary>
    public string? LastProvider { get; set; }

    public TimeSpan RateLimitWaitRemaining { get; private set; } = rateLimitWaitBudget;

    /// <summary>
    /// Spends <paramref name="wait"/> from the budget if all of it fits, and reports whether it
    /// did. A wait that does not fit leaves the budget untouched: starting a wait the query
    /// cannot afford to finish buys nothing.
    /// </summary>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="wait"/> is negative.</exception>
    public bool TrySpendWait(TimeSpan wait)
    {
        // A negative wait would refill the budget. A caller treats an unusable retry header as
        // absent before it gets here, so a negative value reaching this is a caller bug.
        ArgumentOutOfRangeException.ThrowIfLessThan(wait, TimeSpan.Zero);

        if (wait > RateLimitWaitRemaining)
        {
            return false;
        }

        RateLimitWaitRemaining -= wait;
        return true;
    }
}
```

In `src/ReleaseLens.Llm/Providers/ChatContracts.cs`, replace the last two lines of the `ChatRequest` record:

```csharp
    IReadOnlyList<ToolDefinition> Tools,
    int MaxTokens);
```

with:

```csharp
    IReadOnlyList<ToolDefinition> Tools,
    int MaxTokens)
{
    /// <summary>
    /// The query this call belongs to. Null means a call with no query around it, which the
    /// fallback chain treats as it always did: start at the top, remember nothing.
    /// </summary>
    public QueryContext? Context { get; init; }
}
```

The XML summary Task 1 put above the record stays.

- [ ] **Step 4: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryContextTests"
```

Expected: `Passed!  - Failed:     0, Passed:     5`.

- [ ] **Step 5: Write the failing test for the sticky chain (T-F4)**

Create `tests/ReleaseLens.Llm.Tests/FallbackChatProviderStickinessTests.cs`:

- The first test is T-F4 exactly. With the chain [A, B], A fails on the first call and B answers. The second call goes to B and never to A. When B then fails, the chain does not go back to A.
- `Complete_AConcurrentQueryOnTheSameChain_StillStartsAtTheTop` is the concurrent-query half. Two queries share the chain instance, with their calls interleaved. The second query still starts at A while the first stays on B.

```csharp
using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The fallback chain across the several calls of one query (T-F4). Every provider appends its
/// name to one shared log when called, so each test asserts the exact order the chain tried
/// them in, not just who answered.
/// </summary>
public class FallbackChatProviderStickinessTests
{
    /// <summary>
    /// Answers or fails per call, in the order <paramref name="healthy"/> gives, and throws if
    /// called more often than that, so an unexpected extra call fails the test loudly.
    /// </summary>
    private sealed class ScriptedProvider(string name, List<string> log, params bool[] healthy) : IChatProvider
    {
        private int _next;

        public string Name => name;
        public int Calls => _next;

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            log.Add(name);

            if (_next >= healthy.Length)
            {
                throw new InvalidOperationException($"{name} was called more often than scripted.");
            }

            if (!healthy[_next++])
            {
                throw new ProviderUnavailableException(name, $"{name} is down");
            }

            return Task.FromResult(new ChatResponse(
                "answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", name));
        }
    }

    private static FallbackChatProvider Chain(params IChatProvider[] providers) =>
        new(providers, NullLogger<FallbackChatProvider>.Instance);

    private static ChatRequest Request(QueryContext? context) =>
        new("system", [ChatMessage.User("q")], [], 512) { Context = context };

    private static QueryContext NewQuery() => new(TimeSpan.FromSeconds(3));

    [Fact]
    public async Task Complete_WithAContext_StaysOnTheProviderThatAnswered_AndNeverReturnsToAnEarlierOne()
    {
        var log = new List<string>();

        // A is down for the first call only. From then on it would answer, so any restart at the
        // top of the chain shows up as a call to A.
        var a = new ScriptedProvider("a", log, false, true, true);
        var b = new ScriptedProvider("b", log, true, true, false);
        var chain = Chain(a, b);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        var first = await chain.CompleteAsync(Request(query), ct);
        Assert.Equal("b", first.Provider);
        Assert.Equal("b", query.LastProvider);

        var second = await chain.CompleteAsync(Request(query), ct);
        Assert.Equal("b", second.Provider);

        // B now fails. Nothing follows B in this chain, and A is not retried.
        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(Request(query), ct));

        Assert.Equal(["b"], exhausted.AttemptedProviders);
        Assert.Equal(["a", "b", "b", "b"], log);
        Assert.Equal(1, a.Calls);
    }

    [Fact]
    public async Task Complete_WhenTheStickyProviderFails_MovesOnPastIt_NotBackToTheTop()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, false, true, true);
        var b = new ScriptedProvider("b", log, true, false);
        var c = new ScriptedProvider("c", log, true, true);
        var chain = Chain(a, b, c);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(query), ct)).Provider);
        Assert.Equal("c", (await chain.CompleteAsync(Request(query), ct)).Provider);
        Assert.Equal("c", query.LastProvider);
        Assert.Equal("c", (await chain.CompleteAsync(Request(query), ct)).Provider);

        Assert.Equal(["a", "b", "b", "c", "c"], log);
    }

    [Fact]
    public async Task Complete_AConcurrentQueryOnTheSameChain_StillStartsAtTheTop()
    {
        var log = new List<string>();

        // A is down for query 1's first call and healthy after that.
        var a = new ScriptedProvider("a", log, false, true);
        var b = new ScriptedProvider("b", log, true, true);
        var chain = Chain(a, b);
        var query1 = NewQuery();
        var query2 = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(query1), ct)).Provider);

        // Query 2 runs while query 1 is still in progress. It shares the chain instance, not
        // query 1's position in it.
        Assert.Equal("a", (await chain.CompleteAsync(Request(query2), ct)).Provider);
        Assert.Equal("a", query2.LastProvider);

        // And query 2 answering on A did not pull query 1 back to A.
        Assert.Equal("b", (await chain.CompleteAsync(Request(query1), ct)).Provider);
        Assert.Equal("b", query1.LastProvider);

        Assert.Equal(["a", "b", "a", "b"], log);
    }

    [Fact]
    public async Task Complete_WithNoContext_StartsAtTheTopEveryTime()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, false, true);
        var b = new ScriptedProvider("b", log, true);
        var chain = Chain(a, b);
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(null), ct)).Provider);
        Assert.Equal("a", (await chain.CompleteAsync(Request(null), ct)).Provider);

        Assert.Equal(["a", "b", "a"], log);
    }

    [Fact]
    public async Task Complete_WithALastProviderNotInTheChain_StartsAtTheTop()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, true);
        var b = new ScriptedProvider("b", log);
        var query = NewQuery();
        query.LastProvider = "retired";

        var response = await Chain(a, b).CompleteAsync(Request(query), TestContext.Current.CancellationToken);

        Assert.Equal("a", response.Provider);
        Assert.Equal("a", query.LastProvider);
        Assert.Equal(["a"], log);
    }

    [Fact]
    public async Task Complete_WhenEveryRemainingProviderFails_LeavesTheStickyProviderUnchanged()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, true, false);
        var b = new ScriptedProvider("b", log, false);
        var chain = Chain(a, b);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        await chain.CompleteAsync(Request(query), ct);

        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(Request(query), ct));

        Assert.Equal(["a", "b"], exhausted.AttemptedProviders);
        Assert.Equal("a", query.LastProvider);
    }
}
```

- [ ] **Step 6: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProviderStickinessTests"
```

Expected: it builds, because `Context` exists after Step 3. Then `Failed: 5, Passed: 1`. `Complete_WithNoContext_StartsAtTheTopEveryTime` passes, because that is today's behaviour. The five failures are all `Assert.Equal() Failure: Strings differ`:

| Test | Expected | Actual |
|---|---|---|
| `Complete_WithAContext_StaysOnTheProviderThatAnswered_AndNeverReturnsToAnEarlierOne` | `"b"` | `null` (`query.LastProvider` is never set) |
| `Complete_WhenTheStickyProviderFails_MovesOnPastIt_NotBackToTheTop` | `"c"` | `"a"` (the second call restarts at the top) |
| `Complete_AConcurrentQueryOnTheSameChain_StillStartsAtTheTop` | `"a"` | `null` |
| `Complete_WithALastProviderNotInTheChain_StartsAtTheTop` | `"a"` | `"retired"` |
| `Complete_WhenEveryRemainingProviderFails_LeavesTheStickyProviderUnchanged` | `"a"` | `null` |

- [ ] **Step 7: Implement the sticky chain**

Replace the whole of `src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs` with the file below. `AllProvidersUnavailableException`, the class summary as Task 3 left it, the fields, `Name` and the constructor are unchanged. `CompleteAsync` is rewritten, and `StartIndex` is new.

```csharp
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Llm.Providers;

/// <summary>
/// Every provider was unavailable. The caller degrades to returning retrieved
/// evidence unsynthesised rather than failing the request outright.
/// </summary>
public sealed class AllProvidersUnavailableException(IReadOnlyList<string> attemptedProviders)
    : Exception($"All providers unavailable: {string.Join(", ", attemptedProviders)}")
{
    public IReadOnlyList<string> AttemptedProviders { get; } = attemptedProviders;
}

/// <summary>
/// Tries each provider in order. Only <see cref="ProviderUnavailableException"/> falls
/// through — a malformed request is our bug and must surface on the first provider
/// rather than being retried at cost against the second, and a
/// <see cref="ContentFilteredException"/> must not be answered elsewhere, because that
/// would route around the filter.
/// </summary>
public sealed class FallbackChatProvider : IChatProvider
{
    private readonly IReadOnlyList<IChatProvider> _providers;
    private readonly ILogger<FallbackChatProvider> _logger;

    public string Name => "fallback";

    public FallbackChatProvider(IReadOnlyList<IChatProvider> providers, ILogger<FallbackChatProvider> logger)
    {
        if (providers.Count == 0)
        {
            throw new ArgumentException("At least one provider is required.", nameof(providers));
        }

        _providers = providers;
        _logger = logger;
    }

    /// <remarks>
    /// With a <see cref="ChatRequest.Context"/>, the chain starts at the provider that answered
    /// the query's previous call and only moves forward from there: a provider that failed
    /// earlier in the query is not tried again, so one answer mixes providers only when one
    /// fails mid-query. The position lives on the context, not here, because this instance is
    /// shared by every concurrent query. Without a context every call starts at the top.
    /// </remarks>
    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var context = request.Context;
        var start = StartIndex(context?.LastProvider);
        var attempted = new List<string>(_providers.Count - start);

        for (var i = start; i < _providers.Count; i++)
        {
            var provider = _providers[i];
            attempted.Add(provider.Name);

            try
            {
                var response = await provider.CompleteAsync(request, cancellationToken);

                if (context is not null)
                {
                    context.LastProvider = provider.Name;
                }

                return response;
            }
            catch (ProviderUnavailableException unavailable)
            {
                _logger.LogWarning(unavailable,
                    "Provider {Provider} unavailable; falling through", provider.Name);
            }
        }

        throw new AllProvidersUnavailableException(attempted);
    }

    /// <summary>
    /// The chain position of <paramref name="lastProvider"/>, or the top when there is none or
    /// it is not in this chain.
    /// </summary>
    private int StartIndex(string? lastProvider)
    {
        if (lastProvider is null)
        {
            return 0;
        }

        for (var i = 0; i < _providers.Count; i++)
        {
            if (string.Equals(_providers[i].Name, lastProvider, StringComparison.Ordinal))
            {
                return i;
            }
        }

        return 0;
    }
}
```

- [ ] **Step 8: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~FallbackChatProvider"
```

Expected: `Passed!  - Failed: 0, Passed: 13`: the 6 new `FallbackChatProviderStickinessTests` and the 7 `FallbackChatProviderTests` that Tasks 1 and 3 leave. The existing tests send no `Context`, so they exercise the unchanged no-context path.

- [ ] **Step 9: Write the failing test for provider selection (T-A1, Review Focus 2)**

Create `tests/ReleaseLens.Llm.Tests/ChatProviderSelectionTests.cs`.

- **T-A1:** `Select_ReturnsTheProvidersInTheConfiguredOrder` checks that the order is configuration. `Select_WithOneEntry_BuildsAChainThatNeverFallsThrough` checks that a one-entry list never falls through.
- **Review Focus 2** has one test per bad input: misspelled, differently cased (against both an ordinal and a case-insensitive dictionary), duplicated, and empty. Each asserts that the message names the valid providers and that no factory ran.

```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// <c>Chat:Providers</c> is configuration (T-A1), and a list that is wrong in any way stops
/// startup with a message naming the valid providers (Review Focus 2).
/// </summary>
public class ChatProviderSelectionTests
{
    private const string ValidList = "Valid providers: anthropic, azure-openai, openai.";

    private sealed class DownProvider(string name) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            throw new ProviderUnavailableException(name, $"{name} is down");
        }
    }

    /// <summary>The three real provider names, each factory recording that it ran.</summary>
    private static Dictionary<string, Func<IChatProvider>> Factories(List<string> built) => new()
    {
        ["anthropic"] = () => { built.Add("anthropic"); return new DownProvider("anthropic"); },
        ["openai"] = () => { built.Add("openai"); return new DownProvider("openai"); },
        ["azure-openai"] = () => { built.Add("azure-openai"); return new DownProvider("azure-openai"); }
    };

    private static InvalidOperationException AssertRejected(IReadOnlyList<string> names)
    {
        var built = new List<string>();

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(names, Factories(built)));

        Assert.Contains(ValidList, rejected.Message, StringComparison.Ordinal);

        // Validation runs before any factory, so a bad list constructs nothing.
        Assert.Empty(built);
        return rejected;
    }

    [Fact]
    public void Select_ReturnsTheProvidersInTheConfiguredOrder()
    {
        var built = new List<string>();

        var selected = ChatProviderSelection.Select(["openai", "anthropic"], Factories(built));

        Assert.Equal(["openai", "anthropic"], selected.Select(p => p.Name).ToArray());
        Assert.Equal(["openai", "anthropic"], built);
    }

    // T-A1
    [Fact]
    public async Task Select_WithOneEntry_BuildsAChainThatNeverFallsThrough()
    {
        var built = new List<string>();
        var selected = ChatProviderSelection.Select(["azure-openai"], Factories(built));
        var chain = new FallbackChatProvider(selected, NullLogger<FallbackChatProvider>.Instance);

        var request = new ChatRequest("system", [ChatMessage.User("q")], [], 512)
        {
            Context = new QueryContext(TimeSpan.FromSeconds(3))
        };

        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(request, TestContext.Current.CancellationToken));

        Assert.Equal(["azure-openai"], exhausted.AttemptedProviders);
        Assert.Equal(1, ((DownProvider)selected[0]).Calls);

        // The providers that were not listed were never even constructed.
        Assert.Equal(["azure-openai"], built);
    }

    [Fact]
    public void Select_AMisspelledName_Throws()
    {
        var rejected = AssertRejected(["openai", "antropic"]);

        Assert.Contains("'antropic'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_ADifferentlyCasedName_Throws()
    {
        var rejected = AssertRejected(["OpenAI"]);

        Assert.Contains("'OpenAI'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_ADifferentlyCasedName_ThrowsEvenAgainstACaseInsensitiveDictionary()
    {
        var built = new List<string>();
        var lenient = new Dictionary<string, Func<IChatProvider>>(Factories(built), StringComparer.OrdinalIgnoreCase);

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(["OpenAI"], lenient));

        Assert.Contains(ValidList, rejected.Message, StringComparison.Ordinal);
        Assert.Empty(built);
    }

    [Fact]
    public void Select_ADuplicatedName_Throws()
    {
        var rejected = AssertRejected(["openai", "anthropic", "openai"]);

        Assert.Contains("'openai' more than once", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_AnEmptyList_Throws()
    {
        var rejected = AssertRejected([]);

        Assert.Contains("Chat:Providers is empty", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_AFactoryWhoseProviderReportsAnotherName_Throws()
    {
        var factories = new Dictionary<string, Func<IChatProvider>>
        {
            ["openai"] = () => new DownProvider("anthropic")
        };

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(["openai"], factories));

        Assert.Contains("'openai'", rejected.Message, StringComparison.Ordinal);
        Assert.Contains("'anthropic'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ChatOptions_DefaultsToAnthropicThenOpenAi()
    {
        Assert.Equal(["anthropic", "openai"], new ChatOptions().Providers);
    }
}
```

- [ ] **Step 10: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ChatProviderSelectionTests"
```

Expected: the build fails with `ChatProviderSelectionTests.cs(45,19): error CS0103: The name 'ChatProviderSelection' does not exist in the current context`, repeated at every `Select` call, and `ChatProviderSelectionTests.cs(151,51): error CS0246: The type or namespace name 'ChatOptions' could not be found`.

- [ ] **Step 11: Implement `ChatOptions` and `ChatProviderSelection`**

Create `src/ReleaseLens.Llm/Providers/ChatProviderSelection.cs`. The binding remark on `ChatOptions.Providers` was checked against `Microsoft.Extensions.Configuration.Binder` 10.0.0, the version `ReleaseLens.Llm` resolves transitively (it is not pinned in `Directory.Packages.props`; the API uses the ASP.NET Core shared framework's copy), with a throwaway console project.

```csharp
namespace ReleaseLens.Llm.Providers;

public sealed class ChatOptions
{
    public const string SectionName = "Chat";

    /// <summary>Provider names, in the order the fallback chain tries them.</summary>
    /// <remarks>
    /// Configuration binding adds configured entries to this default instead of replacing it:
    /// binding <c>["azure-openai"]</c> into it yields anthropic, openai, azure-openai, and a
    /// configured empty array leaves the default as it is (checked against
    /// Microsoft.Extensions.Configuration.Binder 10.0.0).
    /// </remarks>
    public List<string> Providers { get; set; } = ["anthropic", "openai"];
}

public static class ChatProviderSelection
{
    /// <summary>
    /// Resolves the configured names, in order, to providers. Every name is checked before any
    /// factory runs, so a bad list builds nothing, and a provider that is not listed is never
    /// built at all.
    /// </summary>
    /// <remarks>
    /// Matching is exact and ordinal because the names are the lowercase identifiers recorded
    /// on spans and usage rows. A near-miss fails startup rather than being skipped: a chain
    /// that silently lost a provider looks healthy until the day the others are down.
    /// </remarks>
    /// <exception cref="InvalidOperationException">
    /// The list is empty, names an unknown provider (a misspelling or a different case), names
    /// one twice, or a factory builds a provider whose <see cref="IChatProvider.Name"/> is not
    /// the key it was registered under.
    /// </exception>
    public static IReadOnlyList<IChatProvider> Select(
        IReadOnlyList<string> names, IReadOnlyDictionary<string, Func<IChatProvider>> factories)
    {
        const string setting = ChatOptions.SectionName + ":Providers";

        // Rebuilt with an ordinal comparer so a case-insensitive dictionary handed in by a caller
        // cannot quietly accept "OpenAI".
        var byName = factories.ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);
        var valid = "Valid providers: " + string.Join(", ", byName.Keys.Order(StringComparer.Ordinal)) + ".";

        if (names.Count == 0)
        {
            throw new InvalidOperationException($"{setting} is empty; it must name at least one provider. {valid}");
        }

        var seen = new HashSet<string>(StringComparer.Ordinal);

        foreach (var name in names)
        {
            if (!byName.ContainsKey(name))
            {
                throw new InvalidOperationException(
                    $"{setting} names '{name}', which is not a provider. Names are exact and lowercase. {valid}");
            }

            if (!seen.Add(name))
            {
                throw new InvalidOperationException($"{setting} names '{name}' more than once. {valid}");
            }
        }

        var providers = new List<IChatProvider>(names.Count);

        foreach (var name in names)
        {
            var provider = byName[name]();

            if (!string.Equals(provider.Name, name, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    $"The provider registered as '{name}' reports its name as '{provider.Name}'.");
            }

            providers.Add(provider);
        }

        return providers;
    }
}
```

- [ ] **Step 12: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ChatProviderSelectionTests"
```

Expected: `Passed!  - Failed:     0, Passed:     9`.

- [ ] **Step 13: Write the failing test for the agent (the providers half of T-A2)**

In `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs`, insert the members below at the end of the class. They go after the closing brace of `Answer_AllProvidersDown_StillCarriesTheTextOfWhatItCites`, which is the last test in the file today, and before the class's own closing brace.

They reuse the class's `ScriptedProvider`, `SeedAsync`, `Text`, `CallTool` and `Build`. The file's existing usings already cover `NullLogger`, `ReleaseLens.Llm.Providers` and LINQ.

- `Answer_ListsEachAnsweringProviderOnce_InTheOrderTheyFirstAnswered` is the providers half of T-A2.
- `Answer_ThroughTheFallbackChain_StaysOnTheProviderThatAnsweredFirst` is T-F4 end to end, through a real `FallbackChatProvider`.
- `Answer_TheCeilingsFinalCall_CountsAsAnAnsweringProvider` pins that the tool-ceiling's final no-tools call is recorded. Today it updates neither `providerName` nor `modelName`, so a final call answered by another provider was reported under the loop's.

```csharp
    /// <summary>
    /// A chain member with a name of its own, for the tests that put a real
    /// <see cref="FallbackChatProvider"/> in front of the agent. A null entry in the script is
    /// an outage.
    /// </summary>
    private sealed class NamedProvider(string name, Queue<Func<ChatResponse>?> script) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;

            if (script.Count == 0)
            {
                throw new InvalidOperationException($"{name} ran out of responses.");
            }

            var next = script.Dequeue()
                ?? throw new ProviderUnavailableException(name, $"{name} is down");

            return Task.FromResult(next() with { Provider = name });
        }
    }

    // T-A2, the providers half
    [Fact]
    public async Task Answer_ListsEachAnsweringProviderOnce_InTheOrderTheyFirstAnswered()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-order");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}""") with { Provider = "openai" },
            () => CallTool("search_commits", """{"query":"planner"}""") with { Provider = "anthropic" },
            () => Text("Fixed in [E1].") with { Provider = "openai" }
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(["openai", "anthropic"], answer.Metadata.Providers);

        // `provider` keeps its meaning: whoever answered the final iteration.
        Assert.Equal("openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_TheCeilingsFinalCall_CountsAsAnAnsweringProvider()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        script.Enqueue(() => Text("Reached the tool limit: [E1].") with { Provider = "openai", Model = "gpt-4o" });

        var answer = await Build(new ScriptedProvider(script)).AnswerAsync(
            scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(["scripted", "openai"], answer.Metadata.Providers);
        Assert.Equal("openai", answer.Metadata.Provider);
        Assert.Equal("gpt-4o", answer.Metadata.Model);
    }

    [Fact]
    public async Task Answer_GivesEveryCallOfAQuery_TheSameContext_AndEachQueryItsOwn()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-context");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // Query 1 hits the iteration ceiling, so its requests include the final no-tools call;
        // query 2 answers at once.
        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        script.Enqueue(() => Text("Reached the tool limit: [E1]."));
        script.Enqueue(() => Text("Second query: [E1]."));

        var provider = new ScriptedProvider(script);
        var agent = Build(provider);

        await agent.AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);
        await agent.AnswerAsync(scope, "planner again?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(8, provider.Requests.Count);

        var first = Assert.IsType<QueryContext>(provider.Requests[0].Context);
        Assert.All(provider.Requests.Take(7), r => Assert.Same(first, r.Context));

        var second = Assert.IsType<QueryContext>(provider.Requests[7].Context);
        Assert.NotSame(first, second);

        // The budget comes from AgentOptions.RateLimitWaitBudgetMs, whose default is 3000.
        Assert.Equal(TimeSpan.FromMilliseconds(3000), second.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task Answer_ThroughTheFallbackChain_StaysOnTheProviderThatAnsweredFirst()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-sticky");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // A is down for the first iteration and healthy after. Without the query's context the
        // chain would restart at A on iteration 2, and A would answer it.
        var a = new NamedProvider("a", new Queue<Func<ChatResponse>?>(
            [null, () => Text("A answered [E1].")]));
        var b = new NamedProvider("b", new Queue<Func<ChatResponse>?>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => Text("B answered [E1].")
        ]));

        var chain = new FallbackChatProvider([a, b], NullLogger<FallbackChatProvider>.Instance);

        var answer = await Build(chain).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal("B answered [E1].", answer.Answer);
        Assert.Equal(["b"], answer.Metadata.Providers);
        Assert.Equal("b", answer.Metadata.Provider);
        Assert.Equal(1, a.Calls);
        Assert.Equal(2, b.Calls);
    }

    [Fact]
    public async Task Answer_AllProvidersDownMidQuery_StillListsTheProviderThatAnswered()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => throw new AllProvidersUnavailableException(["anthropic", "openai"])
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.Equal(["scripted"], answer.Metadata.Providers);
    }

    [Fact]
    public async Task Answer_AllProvidersDownFromTheStart_ListsNoProviders()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-none");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw new AllProvidersUnavailableException(["anthropic", "openai"])]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.NotNull(answer.Metadata.Providers);
        Assert.Empty(answer.Metadata.Providers);
    }
```

- [ ] **Step 14: Run it and watch it fail**

```
dotnet build tests/ReleaseLens.Llm.Tests
```

Expected: the build fails with `error CS1061: 'AgentMetadata' does not contain a definition for 'Providers'`, at each `answer.Metadata.Providers` in the new members.

- [ ] **Step 15: Implement the per-query context and `metadata.providers` in the agent**

In `src/ReleaseLens.Llm/Agent/AgentModels.cs`, replace the end of `AgentOptions`:

```csharp
    public int MaxIterations { get; set; } = 6;
    public int MaxTokens { get; set; } = 2048;
    public int SeedRetrievalK { get; set; } = 8;
}
```

with:

```csharp
    public int MaxIterations { get; set; } = 6;
    public int MaxTokens { get; set; } = 2048;
    public int SeedRetrievalK { get; set; } = 8;

    /// <summary>
    /// The most one query may spend waiting out 429 responses, across all of its calls. It
    /// travels on each request's <see cref="QueryContext"/>; a provider that waits on a 429
    /// spends from it.
    /// </summary>
    public int RateLimitWaitBudgetMs { get; set; } = 3000;
}
```

Then, in the same file, replace the end of `AgentMetadata`:

```csharp
    int AccumulatedCitationCount,
    IReadOnlyList<string> UnresolvedCitationMarkers);
```

with:

```csharp
    int AccumulatedCitationCount,
    IReadOnlyList<string> UnresolvedCitationMarkers,
    // Every provider that answered a call in this query, each once, in the order it first
    // answered. `Provider` is only the one that answered last, so without this an answer
    // written partly by one provider and partly by another reads as a single-provider answer.
    IReadOnlyList<string>? Providers = null);
```

The file already has `using ReleaseLens.Llm.Providers;`, which the `QueryContext` cref needs.

In `src/ReleaseLens.Llm/Agent/QueryAgent.cs`, make these seven edits inside `AnswerAsync`, then add one method. Every anchor below was checked to occur exactly once, in the file both as Task 1 leaves it and after Task 4's five edits on top of it. None of them overlaps a line Task 4 changes, so the `cost` lines Task 4 added stay exactly where they are.

(1) Replace:

```csharp
        try
        {
            while (iterations < options.MaxIterations)
```

with:

```csharp
        // One per query, never shared: it carries the sticky provider and the remaining 429
        // budget, and the provider chain is a singleton that concurrent queries both use.
        var context = new QueryContext(TimeSpan.FromMilliseconds(options.RateLimitWaitBudgetMs));
        var providers = new List<string>();

        try
        {
            while (iterations < options.MaxIterations)
```

(2) Replace the loop's request:

```csharp
                var response = await provider.CompleteAsync(new ChatRequest(
                    systemPrompt,
                    messages,
                    tools.Definitions,
                    options.MaxTokens), cancellationToken);
```

with:

```csharp
                var response = await provider.CompleteAsync(new ChatRequest(
                    systemPrompt,
                    messages,
                    tools.Definitions,
                    options.MaxTokens) { Context = context }, cancellationToken);
```

(3) Replace:

```csharp
                modelName = response.Model;

                iterationActivity?.SetTag("tokens_in", response.Usage.InputTokens);
```

with:

```csharp
                modelName = response.Model;
                RecordAnswering(providers, response.Provider);

                iterationActivity?.SetTag("tokens_in", response.Usage.InputTokens);
```

(4) Replace the tool-ceiling's final request:

```csharp
                    var final = await provider.CompleteAsync(new ChatRequest(
                        systemPrompt,
                        messages, [], options.MaxTokens), cancellationToken);
```

with:

```csharp
                    var final = await provider.CompleteAsync(new ChatRequest(
                        systemPrompt,
                        messages, [], options.MaxTokens) { Context = context }, cancellationToken);
```

(5) In the same block, replace the single line:

```csharp
                    answerText = final.Text ?? string.Empty;
```

with:

```csharp
                    // The final call answered the final iteration, so it is the provider and
                    // model reported, and with a fallback chain it need not be the loop's.
                    providerName = final.Provider;
                    modelName = final.Model;
                    RecordAnswering(providers, final.Provider);
                    answerText = final.Text ?? string.Empty;
```

After Task 4 this block reads `usage += final.Usage;`, then `cost += CostOf(final, pricedOn);`, then these lines.

(6) In the `catch (AllProvidersUnavailableException unavailable)` block, replace the last argument line of `new AgentMetadata(...)`:

```csharp
                    seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, []));
```

with:

```csharp
                    seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, [],
                    Providers: providers));
```

(7) Replace the end of `AnswerAsync`:

```csharp
            seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, unresolved));
    }
```

with the lines below. They end `AnswerAsync` and add `RecordAnswering` directly after it. That puts it above Task 4's `CostOf`, which Task 4 placed directly above `DescribeRepositoryAsync`.

```csharp
            seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, unresolved,
            Providers: providers));
    }

    /// <summary>
    /// Adds <paramref name="provider"/> the first time it answers, so the list keeps
    /// first-answer order and a provider that answers every iteration appears once.
    /// </summary>
    private static void RecordAnswering(List<string> providers, string provider)
    {
        if (!providers.Contains(provider))
        {
            providers.Add(provider);
        }
    }
```

- [ ] **Step 16: Run it and watch it pass**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryAgentTests"
```

Expected:
- the build: `Build succeeded.`, `0 Warning(s)`, `0 Error(s)`
- the tests: `Failed: 0`. That is the 6 new tests and every existing `QueryAgentTests` test, Task 4's included.

`QueryAgentTests` is in the Postgres Testcontainers collection, so this step **needs Docker**. When this task was drafted, the new members compiled cleanly against a scratch copy with stand-ins for Tasks 1 and 3, but they were not run, because Docker was not available there. Run them before committing.

- [ ] **Step 17: Write the failing test at the wire**

In `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs`, insert these two tests directly before `Swagger_IsServed`, the last test in the file. They use the file's existing `SeedCommitsAsync`, `ScriptedQueryAsync`, `Query` and `_client`, and its existing usings: `System.Linq`, `System.Net`, `System.Net.Http.Json` and `System.Text.Json`.

```csharp
    // T-A2, the providers half, at the wire
    [Fact]
    public async Task Query_Metadata_ListsTheProvidersThatAnswered()
    {
        await SeedCommitsAsync(2);

        var body = await ScriptedQueryAsync("Fixed in [E1].", includeEvidence: null);
        var metadata = body.GetProperty("metadata");

        Assert.Equal(["scripted"],
            metadata.GetProperty("providers").EnumerateArray().Select(p => p.GetString()!).ToArray());
        Assert.Equal("scripted", metadata.GetProperty("provider").GetString());
    }

    [Fact]
    public async Task Query_WhenNoProviderAnswers_ListsNoProviders()
    {
        // The test environment points both providers at an unroutable address, so nothing answers.
        var response = await _client.SendAsync(Query("What changed recently?", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var metadata = (await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken))
            .GetProperty("metadata");

        Assert.True(metadata.GetProperty("degraded").GetBoolean());
        Assert.Equal(JsonValueKind.Array, metadata.GetProperty("providers").ValueKind);
        Assert.Empty(metadata.GetProperty("providers").EnumerateArray());
    }
```

- [ ] **Step 18: Run it and watch it fail**

```
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~QueryEndpointTests.Query_Metadata_ListsTheProvidersThatAnswered|FullyQualifiedName~QueryEndpointTests.Query_WhenNoProviderAnswers_ListsNoProviders"
```

Expected: it builds; `Failed: 2`. Both tests fail with `System.Collections.Generic.KeyNotFoundException : The given key was not present in the dictionary.` at `metadata.GetProperty("providers")`, because `QueryMetadataDto` has no such field yet. This step **needs Docker**, because `QueryEndpointTests` uses the Postgres collection.

- [ ] **Step 19: Implement the DTO field**

In `src/ReleaseLens.Api/Contracts.cs`, replace the end of `QueryMetadataDto`:

```csharp
    IReadOnlyList<string> UnresolvedCitationMarkers,
    long ElapsedMs);
```

with:

```csharp
    IReadOnlyList<string> UnresolvedCitationMarkers,
    long ElapsedMs,
    // Every provider that answered, once each, in first-answer order; `Provider` is the one
    // that answered last. Empty when none did.
    IReadOnlyList<string>? Providers = null);
```

In `src/ReleaseLens.Api/Program.cs`, in the `/query` handler, replace the last two arguments of `new QueryMetadataDto(`:

```csharp
            answer.Metadata.UnresolvedCitationMarkers,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds)));
```

with:

```csharp
            answer.Metadata.UnresolvedCitationMarkers,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
            answer.Metadata.Providers ?? [])));
```

`QueryAgent` always sets `Providers`. The `?? []` keeps `metadata.providers` a JSON array, never `null`, for any other `AgentMetadata` producer.

- [ ] **Step 20: Run it and watch it pass**

```
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~QueryEndpointTests.Query_Metadata_ListsTheProvidersThatAnswered|FullyQualifiedName~QueryEndpointTests.Query_WhenNoProviderAnswers_ListsNoProviders"
```

Expected: `Passed!  - Failed:     0, Passed:     2`. This needs Docker.

- [ ] **Step 21: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected: the build is `Build succeeded.` with `0 Warning(s)`, and both test projects report `Failed: 0`.

Both test projects **need Docker**:
- In `ReleaseLens.Llm.Tests`, `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` use the Postgres Testcontainers collection (`PostgresCollection`).
- In `ReleaseLens.Api.Tests`, `QueryEndpointTests` and `EvidenceEndpointTests` use it.

Without Docker, run everything in the Llm project outside that collection:

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceToolTests&FullyQualifiedName!~EvidenceToolBoundsTests"
```

Expected: `Failed: 0`, with this task's new non-Postgres classes among the passed: the 5 `QueryContextTests`, the 6 `FallbackChatProviderStickinessTests` and the 9 `ChatProviderSelectionTests`. Say in the task report that the Postgres-backed tests were not run; they run in CI on the pull request, and the task is not complete until they have run green somewhere.

- [ ] **Step 22: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/QueryContext.cs src/ReleaseLens.Llm/Providers/ChatProviderSelection.cs src/ReleaseLens.Llm/Providers/ChatContracts.cs src/ReleaseLens.Llm/Providers/FallbackChatProvider.cs src/ReleaseLens.Llm/Agent/AgentModels.cs src/ReleaseLens.Llm/Agent/QueryAgent.cs src/ReleaseLens.Api/Contracts.cs src/ReleaseLens.Api/Program.cs tests/ReleaseLens.Llm.Tests/QueryContextTests.cs tests/ReleaseLens.Llm.Tests/FallbackChatProviderStickinessTests.cs tests/ReleaseLens.Llm.Tests/ChatProviderSelectionTests.cs tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs
git commit -F- <<'EOF'
fix(llm): keep a query on the provider that answered it (F5)

The fallback chain restarted at the top on every agent iteration, so one
answer could mix providers even when nothing failed after the first call.
QueryAgent now creates a QueryContext per query and sends it on every
request. FallbackChatProvider starts at the provider that answered the
query's previous call and only moves forward, so a query changes provider
only when one fails mid-query. The chain stays a stateless singleton;
concurrent queries each carry their own context.

metadata.providers lists every provider that answered, once each, in
first-answer order. The tool-ceiling's final call now counts as the final
iteration's answer. ChatOptions and ChatProviderSelection resolve an
ordered Chat:Providers list, failing on an empty list, an unknown or
differently cased name, or a duplicate. Startup wiring comes later.
Agent:RateLimitWaitBudgetMs (3000) sets each query's 429 wait budget.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

(In PowerShell, use `git commit -m @'` … `'@` with the same message, with the closing `'@` at column 0.)

---

### Task 6: The filtered outcome, end to end

A blocked prompt or completion becomes its own answer. `/query` returns HTTP 200 with a fixed answer text, cites nothing, and is not marked degraded. It carries an additive `metadata.filtered = { stage, provider }`. The usage and cost cover every call that ran, including the filtered completion's own usage, and that usage is recorded against the tenant's daily budget. `/evidence/*` does not change, and this task pins its wire shape so it cannot drift silently.

This task starts from the tree as Tasks 1 to 5 leave it:
- Task 3: `ContentFilteredException` exists, and `FallbackChatProvider` does not catch it.
- Task 4: `QueryAgent.AnswerAsync` has the locals `cost` and `pricedOn`, and the private `CostOf(ChatResponse, DateOnly)`.
- Task 5: `AnswerAsync` builds a `QueryContext` in the local `context` and records the answering providers in the local `List<string> providers`. `AgentMetadata` and `QueryMetadataDto` end in `IReadOnlyList<string>? Providers = null`, and `Program.cs` passes `answer.Metadata.Providers ?? []` as the DTO's last argument.

Every code block below was compiled against a scratch copy of the repository, with stand-ins for Tasks 1 to 5 written to the contract, using Task 4's own `ModelPricing` and `QueryAgent` edits: `dotnet build ReleaseLens.sln` gave 0 warnings and 0 errors. The red-phase compile errors in Step 4 were reproduced there. Docker was not available, so no Postgres-collection test (every test this task adds) was *run*. They were only compiled.

**Files:**
- Create: `tests/ReleaseLens.Api.Tests/FilteringChatProvider.cs`
- Modify: `src/ReleaseLens.Llm/Agent/AgentModels.cs` (`AgentMetadata`'s last parameter; new record `FilteredOutcome` after it)
- Modify: `src/ReleaseLens.Llm/Agent/QueryAgent.cs` (new constant `FilteredAnswer` after `ActivitySource`; new `catch (ContentFilteredException filtered)` in `AnswerAsync`, before `catch (AllProvidersUnavailableException unavailable)`)
- Modify: `src/ReleaseLens.Api/Contracts.cs` (`QueryMetadataDto`'s last parameter; new record `FilteredOutcomeDto` after it)
- Modify: `src/ReleaseLens.Api/Program.cs` (the `/query` handler's `new QueryMetadataDto(...)`, last argument)
- Test: `tests/ReleaseLens.Api.Tests/EvidenceEndpointTests.cs` (new members at the end of the class)
- Test: `tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs` (new members at the end of the class)
- Test: `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs` (new members before `Swagger_IsServed`)

Call sites checked. `grep -rn "AgentMetadata(\|QueryMetadataDto(" src tests` finds these construction sites, and nothing else:
- `AgentMetadata`: built only in `QueryAgent.AnswerAsync`, twice.
- `QueryMetadataDto`: built only in `Program.cs`.

Both new parameters are optional and trailing, so neither existing construction breaks. No test fake builds either record. `ContentFilteredException` is not caught anywhere in `src` before this task.

**Interfaces:**
- Consumes:
  - Task 3: `public enum ContentFilterStage { Prompt, Completion }`; `ContentFilteredException` with `ProviderName`, `Stage`, `Usage`; `FallbackChatProvider` does not catch it.
  - Task 4: `public sealed class ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage, PricingIdentity? pricing = null)` with `public PricingIdentity? Pricing { get; }`; `public sealed record PricingIdentity(string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false)`; `ModelPricing.CostUsd(PricingIdentity identity, TokenUsage usage, DateOnly asOf)` and `ModelPricing.CostUsd(string model, TokenUsage usage, DateOnly asOf)`; the rate for `("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard")` (0.44 input, 0.11 cached input, 1.76 output, USD per 1M); `ChatResponse(..., PricingIdentity? Pricing = null)`; in `QueryAgent.AnswerAsync` the locals `cost` (`decimal`) and `pricedOn` (`DateOnly`) and `private static decimal CostOf(ChatResponse response, DateOnly asOf)`.
  - Task 5: `AgentMetadata(..., IReadOnlyList<string>? Providers = null)`; `QueryMetadataDto(..., long ElapsedMs, IReadOnlyList<string>? Providers = null)`; in `QueryAgent.AnswerAsync` the locals `context` (`QueryContext`) and `providers` (`List<string>`: distinct answering providers, in first-answer order); `Program.cs` passes `answer.Metadata.Providers ?? []`.
- Produces:
  - `public sealed record FilteredOutcome(string Stage, string Provider);` (namespace `ReleaseLens.Llm.Agent`; `Stage` is `"prompt"` or `"completion"`)
  - `AgentMetadata` gains the trailing `FilteredOutcome? Filtered = null`
  - `public sealed record FilteredOutcomeDto(string Stage, string Provider);` (namespace `ReleaseLens.Api`)
  - `QueryMetadataDto` gains the trailing `[property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] FilteredOutcomeDto? Filtered = null`; the JSON has no `filtered` key unless the request was filtered
  - `QueryAgent.AnswerAsync` returns, for a `ContentFilteredException` from any call (loop or final no-tools call), `AgentAnswer` with the answer exactly `"The request was blocked by the model provider's content filter, so no answer was produced."`, no citations, `Degraded: false`, `Filtered = new FilteredOutcome(stage, ex.ProviderName)`, `Provider = ex.ProviderName`, `Providers` = the providers that answered, and usage and cost including the exception's usage

- [ ] **Step 1: Write the contract test for `/evidence/*` (T-A3)**

Today's tests in `EvidenceEndpointTests.cs` check only some values, not the full shape:
- `ListTools_ReturnsEverySixToolWithASchema` reads `name`, `description` and `inputSchema`.
- `Execute_CountEvidence_ReportsKindComputed` reads `kind` and `citations`.
- `Execute_SearchCommits_ReportsBoundsOnTheWire_AndCarriesNoCoverage` reads `bounds.matched` and `bounds.returned`. It accepts `coverage` being either absent or null.
- The other tests read `isError`, `content`, and a citation's `type`, `key` and `url`.

None of them would fail if a key were added, renamed or dropped. None would fail if a null `bounds` or `coverage` started being omitted, which `releaselens-mcp` would see. So the missing piece is one test that pins every key.

Append these members to the end of `EvidenceEndpointTests`, after `Execute_CannotReachAnotherTenantsEvidence` and before the class's closing brace. The file's existing usings cover everything used here.

```csharp

    private static string[] Keys(JsonElement element)
        => [.. element.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal)];

    /// <summary>
    /// T-A3. releaselens-mcp reads these responses, so their shape is a contract: every key,
    /// not only the ones some other test happens to read. The tests above pin values; this
    /// pins that no key is added, renamed or dropped, including that an absent bounds or
    /// coverage is written as null rather than left out.
    /// </summary>
    [Fact]
    public async Task EvidenceContract_ResponseShapes_AreExactlyTheCurrentOnes()
    {
        var list = await _client.SendAsync(
            Authorised(HttpMethod.Get, "/evidence/tools"), TestContext.Current.CancellationToken);
        list.EnsureSuccessStatusCode();

        var listBody = await list.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(["tools"], Keys(listBody));

        foreach (var tool in listBody.GetProperty("tools").EnumerateArray())
        {
            Assert.Equal(["description", "inputSchema", "name"], Keys(tool));
        }

        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunkRepository = new ChunkRepository();

        await using (var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken))
        {
            var commit = new CommitEvidence(_tenantId, "sha_contract_shape",
                "fix: resolve the gizmoshape defect", "Author", "author@example.invalid",
                DateTimeOffset.UtcNow, DateTimeOffset.UtcNow,
                "https://example.invalid/commit/sha_contract_shape", []);

            await new EvidenceRepository().UpsertCommitsAsync(scope, [commit], TestContext.Current.CancellationToken);

            var chunks = chunker.Chunk(commit);
            var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
            await chunkRepository.UpsertEmbeddingsAsync(scope,
                [.. ids.Zip(chunks.Select(c => PlaceholderVector(c.Content)))], "placeholder",
                TestContext.Current.CancellationToken);

            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        string[] resultKeys = ["bounds", "citations", "content", "coverage", "excerpts", "isError", "kind"];

        // An evidence result: bounds set, coverage null, and one citation and excerpt to read.
        var search = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/search_commits", """{"query":"gizmoshape","limit":10}"""),
            TestContext.Current.CancellationToken);
        search.EnsureSuccessStatusCode();

        var evidence = await search.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(resultKeys, Keys(evidence));
        Assert.Equal("evidence", evidence.GetProperty("kind").GetString());
        Assert.Equal(["matched", "returned", "truncated"], Keys(evidence.GetProperty("bounds")));
        Assert.Equal(JsonValueKind.Null, evidence.GetProperty("coverage").ValueKind);

        var citation = Assert.Single(evidence.GetProperty("citations").EnumerateArray());
        Assert.Equal(["key", "title", "type", "url"], Keys(citation));

        var excerpt = evidence.GetProperty("excerpts").EnumerateArray().First();
        Assert.Equal(["key", "text", "type"], Keys(excerpt));

        // A computed result: coverage set, bounds null, no citations and no excerpts.
        var count = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/count_evidence", """{"entity_type":"release","date_field":"published"}"""),
            TestContext.Current.CancellationToken);
        count.EnsureSuccessStatusCode();

        var computed = await count.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(resultKeys, Keys(computed));
        Assert.Equal("computed", computed.GetProperty("kind").GetString());
        Assert.Equal(JsonValueKind.Null, computed.GetProperty("bounds").ValueKind);
        Assert.Equal(["completeForWindow", "earliest", "latest"], Keys(computed.GetProperty("coverage")));
        Assert.Empty(computed.GetProperty("citations").EnumerateArray());
        Assert.Empty(computed.GetProperty("excerpts").EnumerateArray());
    }
```

- [ ] **Step 2: Run it, see it pass, then see it fail on purpose** (needs Docker: `EvidenceEndpointTests` is in the Postgres Testcontainers collection)

This test pins current behaviour, so the first run must pass:

Run: `dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~EvidenceEndpointTests.EvidenceContract_ResponseShapes_AreExactlyTheCurrentOnes"`

Expected: `Passed!  - Failed: 0, Passed: 1`.

Next, prove it catches a shape change. In `src/ReleaseLens.Api/Contracts.cs`, in `EvidenceToolResultResponse`, temporarily replace:

```csharp
    EvidenceBoundsDto? Bounds = null,
```

with:

```csharp
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] EvidenceBoundsDto? Bounds = null,
```

Run the same command. Expected: it fails at `Assert.Equal(resultKeys, Keys(computed))` with `Assert.Equal() Failure: Collections differ`, because the computed result no longer has `bounds`. Then undo the change:

```bash
git checkout -- src/ReleaseLens.Api/Contracts.cs
```

That is safe at this point: this task has not touched `Contracts.cs` yet, so the checkout restores exactly what Task 5 committed.

- [ ] **Step 3: Write the failing agent tests (T-C6)**

Append these members to the end of `QueryAgentTests`, after the last test and before the class's closing brace. They reuse the class's `ScriptedProvider`, `SeedAsync`, `CallTool` and `Build`. `CallTool` gives 800 in and 30 out, as `claude-sonnet-5` with no pricing identity. The file's existing usings cover everything used here.

A scripted entry that throws stands in for the exception. `ScriptedProvider` calls the entry inside `CompleteAsync`, so the agent sees the exception come out of `CompleteAsync`, which is where a real provider throws it.

```csharp

    private const string FilteredAnswer =
        "The request was blocked by the model provider's content filter, so no answer was produced.";

    private static readonly PricingIdentity AzureStandard =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    /// <summary>1,000 uncached in, 50 out: 0.000528 at Azure's 0.44 / 1.76.</summary>
    private static readonly TokenUsage AzureToolUsage = new(1000, 50, 0, 0);

    /// <summary>
    /// 2,000 uncached in, 500 cached in, 100 out: 0.001111 at Azure's 0.44 / 0.11 / 1.76. The
    /// cached tokens make the filtered call's own rate visible in the total.
    /// </summary>
    private static readonly TokenUsage FilteredCompletionUsage = new(2000, 100, 500, 0);

    // A versioned model string, as a real Azure response may carry: recorded, never priced.
    private static ChatResponse AzureCallTool() =>
        CallTool("search_commits", """{"query":"planner"}""") with
        {
            Usage = AzureToolUsage,
            Model = "gpt-4.1-mini-2025-04-14",
            Provider = "azure-openai",
            Pricing = AzureStandard
        };

    private static ContentFilteredException AzureFiltered(ContentFilterStage stage, TokenUsage usage) =>
        new("azure-openai", stage, usage, AzureStandard);

    // T-C6
    [Fact]
    public async Task Answer_NormalIterationThenFilteredCompletion_CountsAndPricesBothIterations()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-completion");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            AzureCallTool,
            () => throw AzureFiltered(ContentFilterStage.Completion, FilteredCompletionUsage)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Empty(answer.Citations);
        Assert.False(answer.Metadata.Degraded);
        Assert.Null(answer.Metadata.DegradedReason);
        Assert.Equal(new FilteredOutcome("completion", "azure-openai"), answer.Metadata.Filtered);

        Assert.Equal(2, provider.Requests.Count);
        Assert.Equal(2, answer.Metadata.Iterations);
        Assert.Equal(["search_commits"], answer.Metadata.ToolsCalled);
        Assert.Equal(["azure-openai"], answer.Metadata.Providers!);
        Assert.Equal("azure-openai", answer.Metadata.Provider);

        Assert.Equal(AzureToolUsage + FilteredCompletionUsage, answer.Metadata.Usage);

        // 0.000528 for the answered iteration plus 0.001111 for the filtered one. Stopping at
        // the iteration before the filter would give 0.000528.
        Assert.Equal(0.001639m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_FilteredFinalNoToolsCall_IsCaughtCountedAndPriced()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-final");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // Six tool-calling iterations reach the ceiling; the no-tools call the ceiling makes on
        // the way out is the one that is filtered.
        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(AzureCallTool);
        }

        script.Enqueue(() => throw AzureFiltered(ContentFilterStage.Completion, FilteredCompletionUsage));

        var provider = new ScriptedProvider(script);

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Empty(answer.Citations);
        Assert.Equal(new FilteredOutcome("completion", "azure-openai"), answer.Metadata.Filtered);
        Assert.Equal(7, provider.Requests.Count);
        Assert.Empty(provider.Requests[^1].Tools);
        Assert.Equal(6, answer.Metadata.Iterations);

        // Six answered iterations at 0.000528 each, plus the filtered final call's 0.001111.
        Assert.Equal(new TokenUsage(8000, 400, 500, 0), answer.Metadata.Usage);
        Assert.Equal(0.004279m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_FilteredPromptOnTheFirstCall_ReturnsTheFilteredAnswerAndCostsNothing()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-prompt");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw AzureFiltered(ContentFilterStage.Prompt, TokenUsage.Zero)]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Equal(new FilteredOutcome("prompt", "azure-openai"), answer.Metadata.Filtered);
        Assert.False(answer.Metadata.Degraded);

        // Seed evidence was retrieved and shown to the model, but the answer rests on none of
        // it, so none of it is returned as a citation.
        Assert.True(answer.Metadata.AccumulatedCitationCount >= 1,
            "seed retrieval must have found the commit, or the empty citation list proves nothing");
        Assert.Empty(answer.Citations);

        Assert.Single(provider.Requests);
        Assert.Equal(TokenUsage.Zero, answer.Metadata.Usage);
        Assert.Equal(0m, answer.Metadata.CostUsd);

        // No provider answered: the only call was refused.
        Assert.Empty(answer.Metadata.Providers!);
        Assert.Equal("azure-openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_FilteredWithoutAPricingIdentity_IsPricedByTheLastRecordedModelName()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-by-name");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // A test fake may report no identity. The filtered call is then priced by the name of
        // the model the query last recorded, as CostOf prices such a response.
        var filteredUsage = new TokenUsage(2000, 100, 0, 0);
        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => throw new ContentFilteredException("scripted", ContentFilterStage.Completion, filteredUsage)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var expected = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(800, 30, 0, 0), today)
                     + ModelPricing.CostUsd("claude-sonnet-5", filteredUsage, today);

        Assert.True(expected > 0m, "claude-sonnet-5 must have a name-based rate, or this test proves nothing");
        Assert.Equal(expected, answer.Metadata.CostUsd);
        Assert.Equal("claude-sonnet-5", answer.Metadata.Model);
    }
```

The arithmetic, at the Azure rates Task 4 adds (USD per 1M tokens: 0.44 input, 0.11 cached input, 1.76 output):
- An answered iteration: 1,000 × 0.44 + 50 × 1.76 = 528.
- The filtered call: 2,000 × 0.44 + 500 × 0.11 + 100 × 1.76 = 1,111.
- T-C6 is therefore 1,639 per million tokens, i.e. $0.001639.
- The ceiling test is 6 × 528 + 1,111 = 4,279, i.e. $0.004279.

None of these rates depend on the date. The last test compares against `ModelPricing` itself rather than a literal, because `claude-sonnet-5`'s rate does depend on the date.

- [ ] **Step 4: Run them and watch them fail**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~QueryAgentTests.Answer_NormalIterationThenFilteredCompletion_CountsAndPricesBothIterations|FullyQualifiedName~QueryAgentTests.Answer_FilteredFinalNoToolsCall_IsCaughtCountedAndPriced|FullyQualifiedName~QueryAgentTests.Answer_FilteredPromptOnTheFirstCall_ReturnsTheFilteredAnswerAndCostsNothing|FullyQualifiedName~QueryAgentTests.Answer_FilteredWithoutAPricingIdentity_IsPricedByTheLastRecordedModelName"`

Expected: the build fails with these two errors, one pair per `Assert.Equal(new FilteredOutcome(...), answer.Metadata.Filtered)`:
- `error CS0246: The type or namespace name 'FilteredOutcome' could not be found`
- `error CS1061: 'AgentMetadata' does not contain a definition for 'Filtered'`

- [ ] **Step 5: Implement the filtered outcome in the agent**

In `src/ReleaseLens.Llm/Agent/AgentModels.cs`, `AgentMetadata`'s last parameter is currently Task 5's `IReadOnlyList<string>? Providers = null);`. Replace that line with the following. The new record goes directly after `AgentMetadata`, before the `/// <summary>` of `CitedEvidence`:

```csharp
    IReadOnlyList<string>? Providers = null,
    // Set only when a provider's content filter blocked the prompt or the completion. The
    // answer is then the fixed filtered answer rather than anything the model wrote.
    FilteredOutcome? Filtered = null);

/// <summary>
/// A provider's content filter blocked the request. <paramref name="Stage"/> is <c>prompt</c>
/// or <c>completion</c>; <paramref name="Provider"/> is the provider whose filter it was.
/// </summary>
public sealed record FilteredOutcome(string Stage, string Provider);
```

Task 5's three-line comment above `Providers` stays where it is.

In `src/ReleaseLens.Llm/Agent/QueryAgent.cs`, make two edits.

(1) Replace:

```csharp
    public static readonly ActivitySource ActivitySource = new("ReleaseLens.Agent");

    [GeneratedRegex(@"\[E(\d+)\]")]
```

with:

```csharp
    public static readonly ActivitySource ActivitySource = new("ReleaseLens.Agent");

    // Fixed rather than model-written: a filtered completion can carry partial text, and none
    // of it is returned.
    private const string FilteredAnswer =
        "The request was blocked by the model provider's content filter, so no answer was produced.";

    [GeneratedRegex(@"\[E(\d+)\]")]
```

(2) In `AnswerAsync`, insert this block directly before the line `        catch (AllProvidersUnavailableException unavailable)`. It closes the same `try`, which covers both the loop's `CompleteAsync` and the tool-ceiling final no-tools call, so one handler catches a filter from either:

```csharp
        catch (ContentFilteredException filtered)
        {
            var stage = filtered.Stage.ToString().ToLowerInvariant();

            // Not an outage: FallbackChatProvider lets this through, because answering from
            // another provider would route around the filter. The blocked call can still report
            // usage (a filtered completion does; a filtered prompt reports none), so it is
            // counted and priced like any other call, and the endpoint charges it to the
            // tenant's daily budget with the rest.
            logger.LogWarning(
                "{Provider} content filter blocked the {Stage}; returning the filtered answer",
                filtered.ProviderName, stage);

            usage += filtered.Usage;

            // By name only for test fakes that report no identity, as CostOf does.
            cost += filtered.Pricing is { } filteredPricing
                ? ModelPricing.CostUsd(filteredPricing, filtered.Usage, pricedOn)
                : ModelPricing.CostUsd(modelName, filtered.Usage, pricedOn);

            activity?.SetTag("filtered", true);
            activity?.SetTag("filter_stage", stage);
            activity?.SetTag("tokens_in", usage.InputTokens);
            activity?.SetTag("tokens_out", usage.OutputTokens);
            activity?.SetTag("cost_usd", (double)cost);
            activity?.SetTag("iterations", iterations);

            return new AgentAnswer(
                FilteredAnswer,
                // Nothing is cited. The fixed answer rests on no evidence, and returning the
                // seed artefacts would present them as support for an answer never written.
                [],
                new AgentMetadata(
                    iterations, toolsCalled, usage, cost,
                    // The blocked call was the query's last, so its provider is the one
                    // reported. It returned no response to read a model string from, so the
                    // model recorded is the one it is priced as, when it has a pricing identity.
                    filtered.ProviderName,
                    filtered.Pricing?.Model ?? modelName,
                    Degraded: false, DegradedReason: null,
                    seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, [],
                    // Only the providers that answered. The one whose filter blocked the call is
                    // named in Filtered, and is listed here only if it answered earlier.
                    Providers: providers,
                    Filtered: new FilteredOutcome(stage, filtered.ProviderName)));
        }
```

Nothing else in `AnswerAsync` changes. `usage`, `cost`, `pricedOn`, `modelName`, `iterations`, `toolsCalled`, `providers`, `seed` and `citations` are all declared before the `try`, so they are in scope here. A filtered call never adds to `providers`, because Task 5 records a provider only when `CompleteAsync` returns. `UnresolvedCitationMarkers` is empty because the fixed answer contains no markers.

- [ ] **Step 6: Run them and watch them pass** (needs Docker)

Run: the same command as Step 4.

Expected: `Passed!  - Failed: 0, Passed: 4`.

- [ ] **Step 7: Write the failing API tests (the filtered half of T-A2)**

Create `tests/ReleaseLens.Api.Tests/FilteringChatProvider.cs`:

```csharp
using System.Text.Json;
using ReleaseLens.Llm.Providers;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Answers its first <paramref name="answeredCalls"/> calls with a <c>count_evidence</c> tool
/// call, then throws the content-filter outcome at <paramref name="stage"/>, which is what the
/// Azure provider does for a filtered prompt, and any OpenAI-wire provider for a filtered
/// completion. <c>count_evidence</c> is used because it only reads the database: no embedding,
/// no network.
/// </summary>
public sealed class FilteringChatProvider(ContentFilterStage stage, int answeredCalls) : IChatProvider
{
    private static readonly PricingIdentity Azure = new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    private static readonly JsonElement CountArguments =
        JsonDocument.Parse("""{"entity_type":"release","date_field":"published"}""").RootElement.Clone();

    private int _calls;

    public string Name => "azure-openai";

    public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var call = Interlocked.Increment(ref _calls);

        if (call <= answeredCalls)
        {
            return Task.FromResult(new ChatResponse(
                null,
                [new ToolCall($"call_{call}", "count_evidence", CountArguments)],
                new TokenUsage(10, 5, 0, 0), "tool_calls", "gpt-4.1-mini-2025-04-14", Name, Azure));
        }

        // A filtered prompt is an HTTP 400 that carries no usage (spec 4.5); a filtered
        // completion is a 200 that reports what it consumed.
        throw new ContentFilteredException(
            Name, stage, stage == ContentFilterStage.Prompt ? TokenUsage.Zero : new TokenUsage(20, 7, 0, 0), Azure);
    }
}
```

In `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs`, insert these members directly before the `[Fact]` line of `Swagger_IsServed`, after `Query_WhenDegraded_StillCarriesEvidenceTextWhenAsked` and after any members Task 5 added there. The file's existing usings cover everything used here.

```csharp
    private const string FilteredAnswer =
        "The request was blocked by the model provider's content filter, so no answer was produced.";

    /// <summary>
    /// Runs one query against a host whose only chat provider is <paramref name="provider"/>,
    /// and hands back the parsed body of the 200 it must return.
    /// </summary>
    private async Task<JsonElement> QueryWithAsync(IChatProvider provider)
    {
        await using var hosted = _factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IChatProvider>();
                services.AddSingleton<IChatProvider>(provider);
            }));

        using var client = hosted.CreateClient();

        var response = await client.SendAsync(Query("planner", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
    }

    /// <summary>
    /// T-A2, the filtered half. A filtered completion is its own outcome on the wire: HTTP 200,
    /// the fixed answer, nothing cited, not degraded, and metadata.filtered naming the stage and
    /// the provider, beside metadata.providers listing who answered before the filter fired.
    /// </summary>
    [Fact]
    public async Task Query_WhenTheCompletionIsFiltered_ReportsFilteredAndTheProvidersThatAnswered()
    {
        var body = await QueryWithAsync(new FilteringChatProvider(ContentFilterStage.Completion, answeredCalls: 1));

        Assert.Equal(FilteredAnswer, body.GetProperty("answer").GetString());
        Assert.Empty(body.GetProperty("citations").EnumerateArray());

        var metadata = body.GetProperty("metadata");
        Assert.False(metadata.GetProperty("degraded").GetBoolean());

        var filtered = metadata.GetProperty("filtered");
        Assert.Equal(["provider", "stage"],
            filtered.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).ToArray());
        Assert.Equal("completion", filtered.GetProperty("stage").GetString());
        Assert.Equal("azure-openai", filtered.GetProperty("provider").GetString());

        Assert.Equal(["azure-openai"],
            metadata.GetProperty("providers").EnumerateArray().Select(p => p.GetString()!).ToArray());

        // Both calls' tokens: the answered tool call's (10 in, 5 out) and the filtered
        // completion's (20 in, 7 out).
        Assert.Equal(30, metadata.GetProperty("tokensIn").GetInt32());
        Assert.Equal(12, metadata.GetProperty("tokensOut").GetInt32());
        Assert.True(metadata.GetProperty("costUsd").GetDecimal() > 0m, "a filtered Azure call is not free");

        // And the filtered answer's usage counts against the tenant's daily budget like any other.
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        await using var check = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);
        var recorded = await new TokenUsageRepository().GetTodayAsync(
            check, DateOnly.FromDateTime(DateTime.UtcNow), TestContext.Current.CancellationToken);

        Assert.Equal(30, recorded.TokensIn);
        Assert.Equal(12, recorded.TokensOut);
        Assert.True(recorded.CostUsd > 0m);
    }

    [Fact]
    public async Task Query_WhenThePromptIsFiltered_ReportsStagePromptAndNoAnsweringProvider()
    {
        var body = await QueryWithAsync(new FilteringChatProvider(ContentFilterStage.Prompt, answeredCalls: 0));

        Assert.Equal(FilteredAnswer, body.GetProperty("answer").GetString());

        var metadata = body.GetProperty("metadata");
        Assert.False(metadata.GetProperty("degraded").GetBoolean());
        Assert.Equal("prompt", metadata.GetProperty("filtered").GetProperty("stage").GetString());
        Assert.Equal("azure-openai", metadata.GetProperty("filtered").GetProperty("provider").GetString());

        // No provider answered: the only call was refused.
        Assert.Equal(0, metadata.GetProperty("providers").GetArrayLength());
        Assert.Equal(0, metadata.GetProperty("tokensIn").GetInt32());
        Assert.Equal(0m, metadata.GetProperty("costUsd").GetDecimal());
    }

    /// <summary>
    /// The field is additive: a response that was not filtered carries no "filtered" key at
    /// all, so every existing consumer sees exactly the shape it saw before. Checked on a
    /// synthesised answer and on the degraded one.
    /// </summary>
    [Fact]
    public async Task Query_WhenNothingIsFiltered_OmitsTheFilteredField()
    {
        var answered = await QueryWithAsync(new ScriptedChatProvider("Nothing in the evidence answers this."));
        Assert.False(answered.GetProperty("metadata").TryGetProperty("filtered", out _));

        // The default host's providers point at an unroutable address, so this is the real
        // degraded path.
        var response = await _client.SendAsync(Query("planner", _apiKey), TestContext.Current.CancellationToken);
        var degraded = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        Assert.True(degraded.GetProperty("metadata").GetProperty("degraded").GetBoolean());
        Assert.False(degraded.GetProperty("metadata").TryGetProperty("filtered", out _));
    }
```

- [ ] **Step 8: Run them and watch them fail** (needs Docker)

Run: `dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~QueryEndpointTests.Query_WhenTheCompletionIsFiltered_ReportsFilteredAndTheProvidersThatAnswered|FullyQualifiedName~QueryEndpointTests.Query_WhenThePromptIsFiltered_ReportsStagePromptAndNoAnsweringProvider|FullyQualifiedName~QueryEndpointTests.Query_WhenNothingIsFiltered_OmitsTheFilteredField"`

Expected: it builds. `Query_WhenTheCompletionIsFiltered_…` and `Query_WhenThePromptIsFiltered_…` fail with `System.Collections.Generic.KeyNotFoundException` at `metadata.GetProperty("filtered")`. The agent already returns the filtered answer, but `QueryMetadataDto` has no `Filtered` yet, so the key never reaches the JSON.

`Query_WhenNothingIsFiltered_OmitsTheFilteredField` passes already, since there is no `filtered` key anywhere yet. It is here to guard Step 9: a DTO field without `JsonIgnore` would write `"filtered": null` into every response.

- [ ] **Step 9: Carry the outcome onto the wire**

In `src/ReleaseLens.Api/Contracts.cs`, `QueryMetadataDto`'s last parameter is currently Task 5's `IReadOnlyList<string>? Providers = null);`. Replace that line with the following. The new record goes directly after `QueryMetadataDto`, before `public sealed record QueryResponse(`. `System.Text.Json.Serialization` is already imported:

```csharp
    IReadOnlyList<string>? Providers = null,
    // Present only when a provider's content filter blocked the request. Absent otherwise, not
    // null, so every response that was not filtered keeps exactly the shape it had before.
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    FilteredOutcomeDto? Filtered = null);

/// <summary>
/// A content filter blocked the request: <paramref name="Stage"/> is <c>prompt</c> or
/// <c>completion</c>, and <paramref name="Provider"/> is the provider whose filter it was.
/// </summary>
public sealed record FilteredOutcomeDto(string Stage, string Provider);
```

In `src/ReleaseLens.Api/Program.cs`, in the `/query` handler's `new QueryMetadataDto(`, replace the last two arguments, which Task 5 left as:

```csharp
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
            answer.Metadata.Providers ?? [])));
```

with:

```csharp
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
            answer.Metadata.Providers ?? [],
            answer.Metadata.Filtered is { } filtered
                ? new FilteredOutcomeDto(filtered.Stage, filtered.Provider)
                : null)));
```

`usageRepository.RecordAsync` above it already records `answer.Metadata.Usage` and `answer.Metadata.CostUsd` for every answer, so the filtered answer's usage is charged to the tenant's daily budget with no further change.

- [ ] **Step 10: Run them and watch them pass** (needs Docker)

Run: the same command as Step 8.

Expected: `Passed!  - Failed: 0, Passed: 3`.

- [ ] **Step 11: Run the whole affected test projects**

```bash
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

- `dotnet build ReleaseLens.sln`: expect `0 Warning(s)` and `0 Error(s)`. `TreatWarningsAsErrors` is on.
- `tests/ReleaseLens.Llm.Tests`: needs Docker for `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests` (the Postgres collection). Without Docker, `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceTool"` runs the rest. This task changes nothing those depend on.
- `tests/ReleaseLens.Api.Tests`: needs Docker for `QueryEndpointTests` and `EvidenceEndpointTests` (the Postgres collection). Without Docker only `GoldenSetValidationTests` runs: `dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~GoldenSetValidationTests"`.
- The existing `/query` tests still pass. None of them is filtered, so their JSON has no `filtered` key.

Expected: every project `Failed: 0`.

- [ ] **Step 12: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```bash
git add src/ReleaseLens.Llm/Agent/AgentModels.cs \
        src/ReleaseLens.Llm/Agent/QueryAgent.cs \
        src/ReleaseLens.Api/Contracts.cs \
        src/ReleaseLens.Api/Program.cs \
        tests/ReleaseLens.Llm.Tests/QueryAgentTests.cs \
        tests/ReleaseLens.Api.Tests/FilteringChatProvider.cs \
        tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs \
        tests/ReleaseLens.Api.Tests/EvidenceEndpointTests.cs
git commit -F - <<'EOF'
feat(agent): report a content-filtered request as its own outcome

A ContentFilteredException from any call of a query, the tool-ceiling final
call included, now ends the query with HTTP 200, a fixed answer, no
citations, and metadata.filtered = { stage, provider }. It is neither
degraded nor ReleaseLens's own error, and no other provider is tried.

The filtered call's reported usage is added to the query's usage and priced
at its own pricing identity, so the answer's cost and the tenant's daily
budget include it (a filtered prompt reports none). metadata.filtered is
omitted when absent, so every unfiltered response keeps its shape.

A contract test now pins every key of the /evidence/* responses, which
releaselens-mcp reads, including null bounds and coverage.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: Entra token plumbing

**Files:**
- Modify: `Directory.Packages.props` — in `<ItemGroup Label="Hosting">`, the `Microsoft.Extensions.Logging.Abstractions` `PackageVersion` moves from `10.0.0` to `10.0.3` (with a comment); a new `<ItemGroup Label="Identity">` holding `Azure.Identity` `1.21.0` goes between the `Hosting` and `Telemetry` item groups. No other pin moves (see Step 3a for why).
- Modify: `src/ReleaseLens.Llm/ReleaseLens.Llm.csproj` — the `PackageReference` item group gains `Azure.Identity`
- Modify: `tests/ReleaseLens.Llm.Tests/ReleaseLens.Llm.Tests.csproj` — the `PackageReference` item group gains `Microsoft.Extensions.TimeProvider.Testing` (already pinned centrally at `9.5.0`; today it reaches this project only transitively through `ReleaseLens.Ingestion.Tests`)
- Create: `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiOptions.cs`
- Create: `src/ReleaseLens.Llm/Providers/Azure/EntraTokenCache.cs`
- Create: `src/ReleaseLens.Llm/Providers/Azure/EntraTokenHandler.cs`
- Create: `src/ReleaseLens.Llm/Providers/Azure/AzureCredentialFactory.cs`
- Create: `tests/ReleaseLens.Llm.Tests/FakeTokenCredential.cs` (test helper; Task 8 can reuse it for the Azure provider's client)
- Test: `tests/ReleaseLens.Llm.Tests/EntraTokenCacheTests.cs`
- Test: `tests/ReleaseLens.Llm.Tests/EntraTokenHandlerTests.cs`
- Test: `tests/ReleaseLens.Llm.Tests/AzureCredentialFactoryTests.cs`

No existing call site changes: every type here is new, and no C# file in the repository uses an Azure namespace today (`grep -rn "using Azure" src tests --include=*.cs` finds nothing before this task).

**Interfaces:**
- Consumes (existing code that Tasks 1–6 do not reshape):
  - `internal static class ProviderHttp` → `public static Task<HttpResponseMessage> SendAsync(Func<Task<HttpResponseMessage>> send, string providerName, CancellationToken cancellationToken)`, which maps `HttpRequestException` to `ProviderUnavailableException`
  - `public sealed class ProviderUnavailableException(string providerName, string message, Exception? inner = null)` with `ProviderName`
  - `StubHttpMessageHandler` (`tests/ReleaseLens.Ingestion.Tests`): `Enqueue`, `EnqueueJson`, `Requests`
- Produces (copied from the contract):
  ```csharp
  // ---- Azure/AzureOpenAiOptions.cs (Task 7; namespace ReleaseLens.Llm.Providers.Azure) ----
  public enum AzureCredentialKind { AzureCli, ManagedIdentity }
  public sealed class AzureOpenAiOptions
  {
      public const string SectionName = "AzureOpenAi";
      public string BaseUrl { get; set; } = string.Empty;          // e.g. https://<subdomain>.openai.azure.com/openai/v1/
      public string Deployment { get; set; } = string.Empty;
      public string Model { get; set; } = "gpt-4.1-mini";          // pricing identity only; never sent
      public string ModelVersion { get; set; } = "2025-04-14";
      public string DeploymentType { get; set; } = "Standard";
      public string TokenScope { get; set; } = "https://ai.azure.com/.default";
      public AzureCredentialKind Credential { get; set; } = AzureCredentialKind.AzureCli;
      public string? TenantId { get; set; }
      public string? ClientId { get; set; }                         // Program.cs fills it from AZURE_CLIENT_ID
  }

  // ---- Azure/EntraTokenCache.cs, EntraTokenHandler.cs, AzureCredentialFactory.cs (Task 7) ----
  public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)
  {
      public static readonly TimeSpan RefreshBeforeExpiry = TimeSpan.FromMinutes(5);
      public ValueTask<string> GetTokenAsync(CancellationToken cancellationToken);
  }
  public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler { }
  // Sets Authorization: Bearer <token> on every request; an AuthenticationFailedException
  // (CredentialUnavailableException derives from it) is rethrown as HttpRequestException, which
  // ProviderHttp already maps to ProviderUnavailableException.
  public static class AzureCredentialFactory
  {
      // Throws InvalidOperationException on ManagedIdentity with no ClientId (the message names
      // AZURE_CLIENT_ID) and on AzureCli with a null, empty or whitespace TenantId (the message
      // names AzureOpenAi:TenantId).
      public static TokenCredential Create(AzureOpenAiOptions options);

      // What Create builds the ManagedIdentityCredential with: the user-assigned client ID, and
      // Retry capped at MaxRetries 1, RetryMode.Fixed, Delay and MaxDelay 200 ms.
      internal static ManagedIdentityCredentialOptions ManagedIdentityOptions(AzureOpenAiOptions options);
  }
  ```
  - Test helper for Task 8: `internal sealed class FakeTokenCredential : TokenCredential` in `ReleaseLens.Llm.Tests`, with `Returns(string token, DateTimeOffset expiresOn)`, `Throws(Exception failure)`, `RequestedScopes`, `Calls`, `Gate`.

Verified against the real packages (Azure.Identity 1.21.0 restored into a scratch project and into a copy of this repository, then built with `TreatWarningsAsErrors`): in 1.21.0 the `Azure.Identity` types are compiled into the `Azure.Core` 1.53.0 assembly, and the `Azure.Identity` package forwards to them, so the namespaces stay `Azure.Identity` and `Azure.Core`. `ManagedIdentityCredential(string clientId, TokenCredentialOptions)` and `(ResourceIdentifier, TokenCredentialOptions)` are `[Obsolete]`; `ManagedIdentityCredential(ManagedIdentityCredentialOptions)` and `ManagedIdentityCredentialOptions(ManagedIdentityId managedIdentityId = null)` are not. `ManagedIdentityId.FromUserAssignedClientId("")` does not throw. `ClientOptions.Retry` is a `RetryOptions` with settable `MaxRetries`, `Mode` (`Fixed`, `Exponential`), `Delay`, `MaxDelay`; a new `ManagedIdentityCredentialOptions` defaults to 3 retries, `Exponential`, 0.8 s delay, 1 min max delay. `AzureCliCredentialOptions.TenantId` is settable and a null tenant does not throw. `CredentialUnavailableException : AuthenticationFailedException : Exception`. `TokenCredential`'s abstract members are `GetToken` and `GetTokenAsync(TokenRequestContext, CancellationToken)` returning `ValueTask<AccessToken>`. `FakeTimeProvider` 9.5.0 has `SetUtcNow` and `Advance`.

- [ ] **Step 1: Write the failing test** — the credential fake, then the cache tests (T-P1's scope half, T-P2 at the cache, Review Focus 3).

`tests/ReleaseLens.Llm.Tests/FakeTokenCredential.cs`:

```csharp
using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Azure.Core;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// Replays scripted tokens or failures in order and records the scopes each call asked for.
/// Setting <see cref="Gate"/> holds every call open until the test releases it, which is how
/// a test puts two callers at the refresh point at the same moment.
/// </summary>
internal sealed class FakeTokenCredential : TokenCredential
{
    private readonly Queue<Func<AccessToken>> _script = new();
    private readonly List<string[]> _requestedScopes = [];
    private readonly Lock _lock = new();

    public TaskCompletionSource? Gate { get; set; }

    public IReadOnlyList<string[]> RequestedScopes
    {
        get
        {
            lock (_lock)
            {
                return [.. _requestedScopes];
            }
        }
    }

    public int Calls => RequestedScopes.Count;

    public FakeTokenCredential Returns(string token, DateTimeOffset expiresOn)
    {
        _script.Enqueue(() => new AccessToken(token, expiresOn));
        return this;
    }

    public FakeTokenCredential Throws(Exception failure)
    {
        _script.Enqueue(() => throw failure);
        return this;
    }

    public override async ValueTask<AccessToken> GetTokenAsync(
        TokenRequestContext requestContext, CancellationToken cancellationToken)
    {
        Func<AccessToken>? next;
        lock (_lock)
        {
            _requestedScopes.Add(requestContext.Scopes);
            if (!_script.TryDequeue(out next))
            {
                throw new InvalidOperationException(
                    $"The credential was asked for token number {_requestedScopes.Count}, but only {_requestedScopes.Count - 1} were scripted.");
            }
        }

        if (Gate is { } gate)
        {
            await gate.Task.WaitAsync(cancellationToken);
        }

        return next();
    }

    public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
        => throw new NotSupportedException("The token cache must only use the async path.");
}
```

`tests/ReleaseLens.Llm.Tests/EntraTokenCacheTests.cs`:

```csharp
using System;
using System.Threading.Tasks;
using Azure.Identity;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class EntraTokenCacheTests
{
    private const string Scope = "https://ai.azure.com/.default";
    private static readonly DateTimeOffset Start = new(2026, 9, 24, 9, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset FirstExpiry = Start.AddHours(1);

    [Fact]
    public void Options_DefaultTokenScope_IsTheV1Scope()
    {
        Assert.Equal(Scope, new AzureOpenAiOptions().TokenScope);
    }

    [Fact]
    public async Task GetToken_AsksTheCredentialForTheConfiguredScope()
    {
        var credential = new FakeTokenCredential().Returns("token-1", FirstExpiry);
        var cache = new EntraTokenCache(credential, "api://gateway.example/.default", new FakeTimeProvider(Start));

        var token = await cache.GetTokenAsync(TestContext.Current.CancellationToken);

        Assert.Equal("token-1", token);
        Assert.Equal(["api://gateway.example/.default"], Assert.Single(credential.RequestedScopes));
    }

    [Fact]
    public async Task GetToken_ReusesTheTokenUntilFiveMinutesBeforeExpiry_ThenRefreshes()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", FirstExpiry)
            .Returns("token-2", FirstExpiry.AddHours(1));
        var cache = new EntraTokenCache(credential, Scope, time);

        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));

        time.SetUtcNow(FirstExpiry - EntraTokenCache.RefreshBeforeExpiry - TimeSpan.FromTicks(1));
        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
        Assert.Equal(1, credential.Calls);

        time.Advance(TimeSpan.FromTicks(1));
        Assert.Equal("token-2", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task GetToken_TwoCallersAtTheRefreshPoint_CauseExactlyOneRefresh()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", FirstExpiry)
            .Returns("token-2", FirstExpiry.AddHours(1));
        var cache = new EntraTokenCache(credential, Scope, time);
        await cache.GetTokenAsync(TestContext.Current.CancellationToken);

        time.SetUtcNow(FirstExpiry - EntraTokenCache.RefreshBeforeExpiry);
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        credential.Gate = gate;

        // The first caller is inside the credential when the second arrives, so the second
        // can only wait for the refresh already in flight.
        var first = cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask();
        var second = cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask();
        Assert.False(first.IsCompleted);
        Assert.False(second.IsCompleted);

        gate.SetResult();
        var tokens = await Task.WhenAll(first, second);

        Assert.Equal(["token-2", "token-2"], tokens);
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task GetToken_AfterTheCredentialFails_TheNextCallAsksAgain()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."))
            .Returns("token-1", FirstExpiry);
        var cache = new EntraTokenCache(credential, Scope, new FakeTimeProvider(Start));

        await Assert.ThrowsAsync<CredentialUnavailableException>(
            () => cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask());

        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
    }
}
```

- [ ] **Step 2: Run it and watch it fail**

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~EntraTokenCacheTests"
```

Expected: the build fails. The compile errors are `CS0246: The type or namespace name 'Azure' could not be found` (the `using Azure.Core;` / `using Azure.Identity;` lines, since no project references the package yet), `CS0246` for `TokenCredential`, `AccessToken` and `TokenRequestContext`, and `CS0234: The type or namespace name 'Azure' does not exist in the namespace 'ReleaseLens.Llm.Providers'`. `FakeTimeProvider` itself resolves (it already arrives transitively through `ReleaseLens.Ingestion.Tests`).

- [ ] **Step 3: Implement** — the packages, the options, and the cache.

**3a. `Directory.Packages.props`.** Replace the `Hosting` item group and add the `Identity` group right after it, so that part of the file reads:

```xml
  <ItemGroup Label="Hosting">
    <PackageVersion Include="Microsoft.Extensions.Hosting" Version="10.0.0" />
    <PackageVersion Include="Microsoft.Extensions.Http" Version="10.0.0" />
    <!--
      10.0.3, not 10.0.0: Azure.Identity 1.21.0 depends on Azure.Core 1.53.0, which depends on
      Microsoft.Extensions.Hosting.Abstractions 10.0.3, which needs this package at 10.0.3 or
      later. With transitive pinning on, a 10.0.0 pin is a downgrade (NU1109) and restore fails.
    -->
    <PackageVersion Include="Microsoft.Extensions.Logging.Abstractions" Version="10.0.3" />
    <PackageVersion Include="Microsoft.Extensions.Http.Resilience" Version="9.5.0" />
    <PackageVersion Include="Microsoft.AspNetCore.OpenApi" Version="10.0.0" />
    <PackageVersion Include="Microsoft.OpenApi" Version="2.7.5" />
    <PackageVersion Include="Swashbuckle.AspNetCore.SwaggerUI" Version="7.2.0" />
  </ItemGroup>
  <ItemGroup Label="Identity">
    <PackageVersion Include="Azure.Identity" Version="1.21.0" />
  </ItemGroup>
```

Why exactly this one pin: with only `Azure.Identity` added, `dotnet restore ReleaseLens.sln` fails in `ReleaseLens.Llm.Tests` and `ReleaseLens.Api.Tests` with `NU1109: Detected package downgrade: Microsoft.Extensions.Logging.Abstractions from 10.0.3 to centrally defined 10.0.0`, via `Azure.Identity 1.21.0 -> Azure.Core 1.53.0 -> Microsoft.Extensions.Hosting.Abstractions 10.0.3 -> Microsoft.Extensions.Logging.Abstractions (>= 10.0.3)`. With that pin at `10.0.3` the whole solution restores and builds with no warning. `Microsoft.Extensions.Hosting` and `Microsoft.Extensions.Http` stay at `10.0.0`: their dependencies are minimums, and NuGet resolves the higher `10.0.3` abstractions without a downgrade. `Azure.Core` is not pinned: `Azure.Identity` 1.21.0 asks for `>= 1.53.0`, and NuGet takes 1.53.0. (Azure.Core 1.62.0, the latest, would require the `Microsoft.Extensions.*.Abstractions` at `10.0.10`; nothing here asks for it.) `dotnet list ReleaseLens.sln package --vulnerable --include-transitive` reports no vulnerable package in any project.

**3b. `src/ReleaseLens.Llm/ReleaseLens.Llm.csproj`.** The package item group becomes:

```xml
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Http" />
    <PackageReference Include="Microsoft.Extensions.Logging.Abstractions" />
    <PackageReference Include="Azure.Identity" />
  </ItemGroup>
```

**3c. `tests/ReleaseLens.Llm.Tests/ReleaseLens.Llm.Tests.csproj`.** The package item group becomes:

```xml
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" />
    <PackageReference Include="xunit.v3" />
    <PackageReference Include="xunit.runner.visualstudio" />
    <PackageReference Include="Microsoft.Extensions.TimeProvider.Testing" />
  </ItemGroup>
```

**3d. `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiOptions.cs`** (new):

```csharp
namespace ReleaseLens.Llm.Providers.Azure;

public enum AzureCredentialKind { AzureCli, ManagedIdentity }

public sealed class AzureOpenAiOptions
{
    public const string SectionName = "AzureOpenAi";

    /// <summary>The v1 endpoint, e.g. https://&lt;subdomain&gt;.openai.azure.com/openai/v1/.</summary>
    public string BaseUrl { get; set; } = string.Empty;

    public string Deployment { get; set; } = string.Empty;

    /// <summary>
    /// The pricing identity only, never sent: Azure routes on the deployment name, and the
    /// rate is looked up by model, version and deployment type.
    /// </summary>
    public string Model { get; set; } = "gpt-4.1-mini";

    public string ModelVersion { get; set; } = "2025-04-14";

    public string DeploymentType { get; set; } = "Standard";

    /// <summary>
    /// Configurable because an API Management gateway in front of the account would change
    /// it, as it would the base URL.
    /// </summary>
    public string TokenScope { get; set; } = "https://ai.azure.com/.default";

    public AzureCredentialKind Credential { get; set; } = AzureCredentialKind.AzureCli;

    /// <summary>The tenant <see cref="AzureCredentialKind.AzureCli"/> asks for its token in.</summary>
    public string? TenantId { get; set; }

    /// <summary>
    /// The user-assigned identity's client ID, for <see cref="AzureCredentialKind.ManagedIdentity"/>.
    /// In the container it comes from AZURE_CLIENT_ID.
    /// </summary>
    public string? ClientId { get; set; }
}
```

**3e. `src/ReleaseLens.Llm/Providers/Azure/EntraTokenCache.cs`** (new):

```csharp
using Azure.Core;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Holds the Entra token for the process. A singleton rather than state on the handler,
/// because <c>IHttpClientFactory</c> recycles handlers; and needed at all because
/// <c>AzureCliCredential</c> does not cache, so without it every agent iteration would start
/// an <c>az</c> process.
/// </summary>
public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)
{
    public static readonly TimeSpan RefreshBeforeExpiry = TimeSpan.FromMinutes(5);

    private readonly TokenRequestContext _context = new([scope]);
    private readonly SemaphoreSlim _refresh = new(1, 1);

    // A reference rather than the AccessToken struct, so a reader on another thread sees
    // either the old token or the new one, never half of each.
    private volatile CachedToken? _cached;

    public ValueTask<string> GetTokenAsync(CancellationToken cancellationToken)
    {
        var cached = _cached;
        return cached is not null && time.GetUtcNow() < cached.RefreshAt
            ? ValueTask.FromResult(cached.Token)
            : new ValueTask<string>(RefreshAsync(cancellationToken));
    }

    private async Task<string> RefreshAsync(CancellationToken cancellationToken)
    {
        await _refresh.WaitAsync(cancellationToken);
        try
        {
            // A caller that queued behind the refresh finds the new token here instead of
            // asking the credential a second time.
            var cached = _cached;
            if (cached is not null && time.GetUtcNow() < cached.RefreshAt)
            {
                return cached.Token;
            }

            var token = await credential.GetTokenAsync(_context, cancellationToken);
            _cached = new CachedToken(token.Token, token.ExpiresOn - RefreshBeforeExpiry);
            return token.Token;
        }
        finally
        {
            _refresh.Release();
        }
    }

    private sealed record CachedToken(string Token, DateTimeOffset RefreshAt);
}
```

- [ ] **Step 4: Run it and watch it pass**

```bash
dotnet restore ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~EntraTokenCacheTests"
```

Expected: restore reports no `NU1109` and no `NU190x` audit warning; `Passed!  - Failed: 0, Passed: 5`. The Review Focus 3 test (`GetToken_TwoCallersAtTheRefreshPoint_CauseExactlyOneRefresh`) was checked against a mutant with the second, inside-the-semaphore expiry check removed: it fails with `The credential was asked for token number 3, but only 2 were scripted.` A mutant using `<=` for the cache hit fails the boundary test.

- [ ] **Step 5: Write the failing test** — the handler (T-P1 at the wire, T-P2 across requests, T-P3).

`tests/ReleaseLens.Llm.Tests/EntraTokenHandlerTests.cs`:

```csharp
using System;
using System.Net.Http;
using System.Threading.Tasks;
using Azure.Identity;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class EntraTokenHandlerTests
{
    private const string Scope = "https://ai.azure.com/.default";
    private static readonly DateTimeOffset Start = new(2026, 9, 24, 9, 0, 0, TimeSpan.Zero);

    private static HttpClient Client(FakeTokenCredential credential, TimeProvider time, StubHttpMessageHandler stub) =>
        new(new EntraTokenHandler(new EntraTokenCache(credential, Scope, time)) { InnerHandler = stub })
        {
            BaseAddress = new Uri("https://example-subdomain.openai.azure.com/openai/v1/")
        };

    private static Task<HttpResponseMessage> Post(HttpClient client) =>
        client.PostAsync("chat/completions", new StringContent("{}"), TestContext.Current.CancellationToken);

    [Fact]
    public async Task Send_AttachesTheCredentialsToken_ForTheConfiguredScope()
    {
        var credential = new FakeTokenCredential().Returns("token-1", Start.AddHours(1));
        var stub = new StubHttpMessageHandler().EnqueueJson("{}");

        await Post(Client(credential, new FakeTimeProvider(Start), stub));

        var authorization = stub.Requests[0].Headers.Authorization!;
        Assert.Equal("Bearer", authorization.Scheme);
        Assert.Equal("token-1", authorization.Parameter);
        Assert.Equal([Scope], Assert.Single(credential.RequestedScopes));
    }

    [Fact]
    public async Task Send_ReusesTheTokenAcrossRequests_AndRefreshesItBeforeExpiry()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", Start.AddHours(1))
            .Returns("token-2", Start.AddHours(2));
        var stub = new StubHttpMessageHandler().EnqueueJson("{}").EnqueueJson("{}").EnqueueJson("{}");
        var client = Client(credential, time, stub);

        await Post(client);
        await Post(client);
        time.Advance(TimeSpan.FromMinutes(55));
        await Post(client);

        Assert.Equal(
            ["token-1", "token-1", "token-2"],
            stub.Requests.ConvertAll(request => request.Headers.Authorization!.Parameter));
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task Send_CredentialUnavailable_ThrowsHttpRequestException_AndSendsNothing()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."));
        var stub = new StubHttpMessageHandler();

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Post(Client(credential, new FakeTimeProvider(Start), stub)));

        Assert.IsType<CredentialUnavailableException>(failure.InnerException);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Send_AuthenticationFailed_ThrowsHttpRequestException()
    {
        var credential = new FakeTokenCredential()
            .Throws(new AuthenticationFailedException("The managed identity endpoint returned 400."));

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Post(Client(credential, new FakeTimeProvider(Start), new StubHttpMessageHandler())));

        Assert.IsType<AuthenticationFailedException>(failure.InnerException);
    }

    [Fact]
    public async Task Send_CredentialUnavailable_ThroughProviderHttp_IsProviderUnavailable()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."));
        var client = Client(credential, new FakeTimeProvider(Start), new StubHttpMessageHandler());

        var failure = await Assert.ThrowsAsync<ProviderUnavailableException>(() => ProviderHttp.SendAsync(
            () => Post(client), "azure-openai", TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", failure.ProviderName);
        Assert.IsType<HttpRequestException>(failure.InnerException);
    }
}
```

- [ ] **Step 6: Run it and watch it fail**

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~EntraTokenHandlerTests"
```

Expected: the build fails with `CS0246: The type or namespace name 'EntraTokenHandler' could not be found`.

- [ ] **Step 7: Implement** — `src/ReleaseLens.Llm/Providers/Azure/EntraTokenHandler.cs` (new):

```csharp
using System.Net.Http.Headers;
using Azure.Identity;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Attaches the Entra bearer token to every request on the Azure provider's client.
/// </summary>
public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler
{
    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        string token;
        try
        {
            token = await cache.GetTokenAsync(cancellationToken);
        }
        catch (AuthenticationFailedException failure)
        {
            // No token leaves the provider as unusable as a 401 would. As an
            // HttpRequestException it becomes ProviderUnavailableException in ProviderHttp,
            // so the query degrades instead of escaping as an HTTP 500.
            throw new HttpRequestException($"Could not get an Entra token: {failure.Message}", failure);
        }

        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await base.SendAsync(request, cancellationToken);
    }
}
```

- [ ] **Step 8: Run it and watch it pass**

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~EntraTokenHandlerTests"
```

Expected: `Passed!  - Failed: 0, Passed: 5`.

- [ ] **Step 9: Write the failing test** — the credential factory (T-P3a, the two credential kinds, the pinned tenant, and the managed-identity retry cap). `ManagedIdentityOptions` is `internal`; the test project reaches it through the Llm project's existing `<InternalsVisibleTo Include="ReleaseLens.Llm.Tests" />`.

`tests/ReleaseLens.Llm.Tests/AzureCredentialFactoryTests.cs`:

```csharp
using System;
using Azure.Core;
using Azure.Identity;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class AzureCredentialFactoryTests
{
    private const string ClientId = "00000000-0000-0000-0000-000000000001";
    private const string TenantId = "00000000-0000-0000-0000-000000000002";

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Create_ManagedIdentityWithoutAClientId_Throws(string? clientId)
    {
        var failure = Assert.Throws<InvalidOperationException>(() => AzureCredentialFactory.Create(
            new AzureOpenAiOptions { Credential = AzureCredentialKind.ManagedIdentity, ClientId = clientId }));

        Assert.Contains("AZURE_CLIENT_ID", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Create_ManagedIdentityWithAClientId_IsAManagedIdentityCredential()
    {
        var credential = AzureCredentialFactory.Create(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.ManagedIdentity,
            ClientId = ClientId
        });

        Assert.IsType<ManagedIdentityCredential>(credential);
    }

    // Spec 4.7: capped at one retry with a short delay, because Azure.Core's default is three
    // retries with exponential backoff, a wait the per-query 429 budget knows nothing about.
    [Fact]
    public void ManagedIdentityOptions_CapTheRetryAtOneWithAShortFixedDelay()
    {
        var retry = AzureCredentialFactory.ManagedIdentityOptions(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.ManagedIdentity,
            ClientId = ClientId
        }).Retry;

        Assert.Equal(1, retry.MaxRetries);
        Assert.Equal(RetryMode.Fixed, retry.Mode);
        Assert.Equal(TimeSpan.FromMilliseconds(200), retry.Delay);
        Assert.Equal(TimeSpan.FromMilliseconds(200), retry.MaxDelay);
    }

    [Fact]
    public void Create_AzureCli_IsAnAzureCliCredential()
    {
        var credential = AzureCredentialFactory.Create(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.AzureCli,
            TenantId = TenantId
        });

        Assert.IsType<AzureCliCredential>(credential);
    }

    // Spec 4.7: the Azure CLI credential with the tenant pinned. Without one, az answers for
    // whichever tenant it is signed in to.
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Create_AzureCliWithoutATenantId_Throws(string? tenantId)
    {
        var failure = Assert.Throws<InvalidOperationException>(() => AzureCredentialFactory.Create(
            new AzureOpenAiOptions { Credential = AzureCredentialKind.AzureCli, TenantId = tenantId }));

        Assert.Contains("AzureOpenAi:TenantId", failure.Message, StringComparison.Ordinal);
    }
}
```

- [ ] **Step 10: Run it and watch it fail**

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureCredentialFactoryTests"
```

Expected: the build fails with `CS0103: The name 'AzureCredentialFactory' does not exist in the current context`.

- [ ] **Step 11: Implement** — `src/ReleaseLens.Llm/Providers/Azure/AzureCredentialFactory.cs` (new). It uses the non-obsolete `ManagedIdentityCredential(ManagedIdentityCredentialOptions)` constructor; the `(string clientId, ...)` overload is `[Obsolete]` and would fail the build (CS0618 under `TreatWarningsAsErrors`). The retry cap is set through `ManagedIdentityCredentialOptions.Retry`, as the spec (§4.7) says, in the `internal` `ManagedIdentityOptions`, which `Create` calls and the test reads, because a built credential does not expose its options. Whether the cap also bounds any retry MSAL performs inside the managed-identity flow could not be checked without an Azure host and stays unverified, and the comment says so. The Azure CLI credential is refused without a tenant: `AzureCliCredentialOptions` accepts a null tenant without complaint (see the verification note above), and the spec pins it.

```csharp
using Azure.Core;
using Azure.Identity;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Picks the credential by configuration. <c>DefaultAzureCredential</c> is deliberately not an
/// option: its chain can pick up the wrong account on a machine signed in to more than one
/// tenant, and a wrong identity that still gets a token looks like a working system.
/// </summary>
public static class AzureCredentialFactory
{
    public static TokenCredential Create(AzureOpenAiOptions options) => options.Credential switch
    {
        AzureCredentialKind.ManagedIdentity => new ManagedIdentityCredential(ManagedIdentityOptions(options)),
        AzureCredentialKind.AzureCli => CreateAzureCli(options),
        _ => throw new InvalidOperationException(
            $"AzureOpenAi:Credential '{options.Credential}' is not one of {string.Join(", ", Enum.GetNames<AzureCredentialKind>())}.")
    };

    /// <summary>
    /// The options the managed-identity credential is built with. Internal so the retry cap can
    /// be tested: a built credential does not expose its options.
    /// </summary>
    internal static ManagedIdentityCredentialOptions ManagedIdentityOptions(AzureOpenAiOptions options)
    {
        // FromUserAssignedClientId accepts an empty string, and without an ID the credential
        // asks for a system-assigned identity, which this app does not have. Either way the
        // failure would surface at the first query instead of here, where it is clear.
        if (string.IsNullOrWhiteSpace(options.ClientId))
        {
            throw new InvalidOperationException(
                "AzureOpenAi:Credential is ManagedIdentity but no client ID is set. "
                + "The container must provide AZURE_CLIENT_ID for its user-assigned identity.");
        }

        var credentialOptions = new ManagedIdentityCredentialOptions(
            ManagedIdentityId.FromUserAssignedClientId(options.ClientId));

        // Azure.Core's defaults here are three retries with exponential backoff from 0.8 s: a
        // wait the per-query 429 budget knows nothing about. One quick retry covers a blip;
        // anything longer is an outage, and the query degrades. Whether MSAL's own
        // managed-identity retry adds to this is unverified.
        credentialOptions.Retry.MaxRetries = 1;
        credentialOptions.Retry.Mode = RetryMode.Fixed;
        credentialOptions.Retry.Delay = TimeSpan.FromMilliseconds(200);
        credentialOptions.Retry.MaxDelay = TimeSpan.FromMilliseconds(200);

        return credentialOptions;
    }

    private static AzureCliCredential CreateAzureCli(AzureOpenAiOptions options)
    {
        // Without a tenant, az issues the token from whichever tenant it is signed in to, which
        // on a machine signed in to more than one can be the wrong one, and that shows only when
        // the first query is refused. A missing setting here fails at startup instead.
        if (string.IsNullOrWhiteSpace(options.TenantId))
        {
            throw new InvalidOperationException(
                "AzureOpenAi:Credential is AzureCli but AzureOpenAi:TenantId is not set. "
                + "Set it to the tenant of the Azure OpenAI account.");
        }

        return new AzureCliCredential(new AzureCliCredentialOptions { TenantId = options.TenantId });
    }
}
```

- [ ] **Step 12: Run it and watch it pass**

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureCredentialFactoryTests"
```

Expected: `Passed!  - Failed: 0, Passed: 9` (two three-row theories and three facts). Constructing either credential makes no network call and does not start `az`.

- [ ] **Step 13: Run the whole affected test projects**

`Directory.Packages.props` changes a pin every project restores against, so build the whole solution first, then run the two test projects whose package graph changed.

```bash
dotnet build ReleaseLens.sln
dotnet list ReleaseLens.sln package --vulnerable --include-transitive
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected: `Build succeeded.` with 0 warnings; every project reports "has no vulnerable packages"; both test runs `Failed: 0`. **Docker is required** for the Postgres Testcontainers collection: in `ReleaseLens.Llm.Tests` that is `EvidenceToolTests`, `EvidenceToolBoundsTests` and `QueryAgentTests`; in `ReleaseLens.Api.Tests`, `EvidenceEndpointTests` and `QueryEndpointTests`. Without Docker, run the rest and treat the Docker-bound classes as not run, not passed:

```bash
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~EvidenceTool&FullyQualifiedName!~QueryAgentTests"
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName!~EvidenceEndpointTests&FullyQualifiedName!~QueryEndpointTests"
```

The 19 tests this task adds need no Docker.

- [ ] **Step 14: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```bash
git add Directory.Packages.props \
  src/ReleaseLens.Llm/ReleaseLens.Llm.csproj \
  src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiOptions.cs \
  src/ReleaseLens.Llm/Providers/Azure/EntraTokenCache.cs \
  src/ReleaseLens.Llm/Providers/Azure/EntraTokenHandler.cs \
  src/ReleaseLens.Llm/Providers/Azure/AzureCredentialFactory.cs \
  tests/ReleaseLens.Llm.Tests/ReleaseLens.Llm.Tests.csproj \
  tests/ReleaseLens.Llm.Tests/FakeTokenCredential.cs \
  tests/ReleaseLens.Llm.Tests/EntraTokenCacheTests.cs \
  tests/ReleaseLens.Llm.Tests/EntraTokenHandlerTests.cs \
  tests/ReleaseLens.Llm.Tests/AzureCredentialFactoryTests.cs
git commit -F - <<'EOF'
feat(llm): Entra token plumbing for the Azure OpenAI provider

Adds Azure.Identity 1.21.0 and the options, token cache, bearer-token handler and
credential factory the Azure provider will use. The cache refreshes five minutes before
expiry and lets exactly one caller refresh at a time; a credential failure becomes an
HttpRequestException, so ProviderHttp reports the provider unavailable instead of the
query failing with a 500. Managed identity needs a user-assigned client ID and retries
once after a fixed 200 ms (a test pins the cap); the Azure CLI credential needs
AzureOpenAi:TenantId and is pinned to it. DefaultAzureCredential is not used.

Microsoft.Extensions.Logging.Abstractions moves from 10.0.0 to 10.0.3, because Azure.Core
1.53.0 needs it and transitive pinning otherwise fails restore with NU1109.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: The Azure provider

This task adds `AzureOpenAiChatProvider`: Azure OpenAI through its v1 endpoint, speaking the OpenAI wire format through the shared `OpenAiWireFormat` codec. It starts from the state Tasks 1–7 leave:
- `ChatRequest` has no `Model` and carries `Context`.
- `OpenAiWireFormat.Parse` takes `PricingIdentity? pricing` and throws `ContentFilteredException(..., Completion, usage, pricing)` on `finish_reason: content_filter`.
- `ContentFilteredException` has its `pricing` parameter.
- `PricingIdentity` and `IPricedChatProvider` exist.
- `AzureOpenAiOptions`, `EntraTokenCache` and `EntraTokenHandler` exist in `ReleaseLens.Llm.Providers.Azure`.
- The test project references `Microsoft.Extensions.TimeProvider.Testing`.

What is Azure's own, and lives in this class:
- **The URL.** The constructor sets `client.BaseAddress` from `options.BaseUrl`, adding a trailing slash if it is missing, and only when the client has none. It posts relative `chat/completions`, with no `api-version`.
- **The model.** The payload's `model` is `options.Deployment` (`OpenAiWireFormat.BuildPayload(request, options.Deployment)`). `options.Model` names the pricing identity only.
- **Authentication.** The provider sets no `Authorization`, because `EntraTokenHandler` on its `HttpClient` does that. It never sets an `api-key` header.
- **A filtered prompt.** On a 400 whose body has `code == "content_filter"` at `error.code` or at the top-level `code`, it throws `ContentFilteredException(Name, Prompt, TokenUsage.Zero, Pricing)`. Every other 400 goes through `ProviderHttp` as today (`InvalidOperationException`). `innererror` and `inner_error` are never read.
- **The filter did not run.** A 200 whose `choices[0].content_filter_results` contains an `error` object is **not** filtered. The provider logs it at Information and returns the answer.

**Not in this task: the 429 wait.** Task 9 adds it. In this task a 429 goes through `ProviderHttp.ThrowIfNotSuccessAsync` exactly as on the other providers, so it becomes `ProviderUnavailableException`. The `TimeProvider` is taken and stored now, so that the constructor has its final shape, but nothing reads it until Task 9.

The contract writes the class with a primary constructor. This task uses an ordinary constructor with the same four parameters in the same order. A primary-constructor parameter that is never read is `error CS9113: Parameter 'time' is unread` under `TreatWarningsAsErrors` (checked against the real compiler). An unread `private readonly` field assigned from a constructor parameter raises nothing. Callers see the same signature either way.

**Files:**
- Create: `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs`
- Create: `tests/ReleaseLens.Llm.Tests/AzureOpenAiChatProviderTests.cs`
- Create: `tests/ReleaseLens.Llm.Tests/WireEquivalenceTests.cs`
- Test: `tests/ReleaseLens.Llm.Tests/AzureOpenAiChatProviderTests.cs` (T-P4, T-P5, T-P6, T-P12, T-P13, T-P14, T-P15, T-P16), `tests/ReleaseLens.Llm.Tests/WireEquivalenceTests.cs` (T-P7)

No existing file changes. The class is new and nothing constructs it yet: Task 10 registers it in `Program.cs`. A grep of `src` and `tests` for `AzureOpenAiChatProvider` and `WireEquivalence` finds nothing before this task. The provider uses `ProviderHttp` and `OpenAiWireFormat`, which are `internal`. It is in the same assembly, so it can reach them, and the tests reach them through the existing `<InternalsVisibleTo Include="ReleaseLens.Llm.Tests" />`. `Azure.Core`'s `TokenCredential`, used by the T-P7 fake credential, reaches the test project transitively through the `ReleaseLens.Llm` project reference that Task 7 gave `Azure.Identity`.

**Interfaces:**
- Consumes:
  - `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens) { public QueryContext? Context { get; init; } }` (Tasks 1, 5)
  - `public sealed record ChatResponse(string? Text, IReadOnlyList<ToolCall> ToolCalls, TokenUsage Usage, string StopReason, string Model, string Provider, PricingIdentity? Pricing = null)` (Task 4)
  - `TokenUsage.Zero`; `public enum ContentFilterStage { Prompt, Completion }`; `public sealed class ContentFilteredException(string providerName, ContentFilterStage stage, TokenUsage usage, PricingIdentity? pricing = null)` with `ProviderName`, `Stage`, `Usage`, `Pricing` (Tasks 3, 4)
  - `public sealed record PricingIdentity(string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false)`; `public interface IPricedChatProvider : IChatProvider { PricingIdentity Pricing { get; } }` (Task 4)
  - `internal static class OpenAiWireFormat` with `public static JsonObject BuildPayload(ChatRequest request, string model)` and `public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel, PricingIdentity? pricing)`. `Parse` throws `ContentFilteredException(providerName, Completion, usage, pricing)` on `finish_reason == "content_filter"`, and `InvalidOperationException` naming the provider when `choices` is missing or empty or `usage` is not an object; every fixture below that reaches `Parse` carries a `usage` object (Tasks 2–4)
  - `ProviderHttp.JsonOptions`, `ProviderHttp.SendAsync`, `ProviderHttp.ThrowIfNotSuccessAsync` (existing, unchanged)
  - `public sealed class AzureOpenAiOptions` with `BaseUrl`, `Deployment`, `Model`, `ModelVersion`, `DeploymentType` (Task 7)
  - `public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)`, `public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler` (Task 7; used only by T-P7)
  - `OpenAiChatProvider(HttpClient client, OpenAiOptions options)`, `FallbackChatProvider(IReadOnlyList<IChatProvider> providers, ILogger<FallbackChatProvider> logger)` (existing; used only by tests)
- Produces:
  - `public sealed class AzureOpenAiChatProvider : IPricedChatProvider` in namespace `ReleaseLens.Llm.Providers.Azure`, with constructor `(HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger)`, `public string Name => "azure-openai";`, `public PricingIdentity Pricing { get; }` = `("azure-openai", options.Model, options.ModelVersion, options.DeploymentType)`, and `public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)`.
  - For Task 9: the private fields `_client`, `_options`, `_time`, `_logger`. Inside `CompleteAsync`'s `using (response)` block, the filtered-prompt check comes first, then `ProviderHttp.ThrowIfNotSuccessAsync`. Task 9's 429 wait goes before that call. The `TimeProvider` is `_time`.
  - For Task 10: the constructor leaves a `BaseAddress` the client already has, so a typed client configured in `Program.cs` keeps its own.

- [ ] **Step 1: Write the failing tests**

Create `tests/ReleaseLens.Llm.Tests/AzureOpenAiChatProviderTests.cs`. Each spec test maps to one or more tests:

| Spec | Test |
|---|---|
| T-P16 | `Name_IsAzureOpenAi_AndItIsPricedAsTheConfiguredDeployment`, `Complete_ReportsAzureOpenAiAsTheAnsweringProvider` |
| T-P4 | `Complete_PostsToTheV1ChatCompletionsUrl_WithNoApiVersion` (both trailing-slash forms), and `Complete_KeepsABaseAddressTheClientAlreadyHas` for the "only when the client has none" rule |
| T-P5 | `Complete_SendsTheDeploymentNameAsTheModel` |
| T-P6 | `Complete_SetsNoApiKeyHeaderAndNoAuthorizationOfItsOwn` |
| T-P12 | `Complete_FilteredPrompt_ThrowsContentFilteredAtThePromptStage` and `Complete_FilteredPrompt_IsNotAnsweredByTheNextProvider`, each over the three fixtures |
| T-P13 | `Complete_FilteredCompletion_ThrowsContentFilteredAtTheCompletionStage_AndIsNotAnsweredElsewhere` |
| T-P14 | `Complete_FilterDidNotRun_ReturnsTheAnswer_AndRecordsIt`, plus `Complete_FilterRan_RecordsNothing` so the record is shown to be conditional |
| T-P15 | `Complete_BadRequestThatIsNotAFilteredPrompt_IsStillOurOwnBug`, over four bodies: a plain invalid-request error, `content_filter` only under `innererror`, `content_filter` only in the message text, and a body that is not JSON |

The fixtures' usage has no `cached_tokens`, so the expected `TokenUsage` does not depend on Task 4's cached-token normalisation.

```csharp
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class AzureOpenAiChatProviderTests
{
    private const string BaseUrl = "https://releaselens-aoai.openai.azure.com/openai/v1/";
    private const string Deployment = "releaselens-chat";

    private static AzureOpenAiOptions Options(string baseUrl = BaseUrl) => new()
    {
        BaseUrl = baseUrl,
        Deployment = Deployment,
        Model = "gpt-4.1-mini",
        ModelVersion = "2025-04-14",
        DeploymentType = "Standard"
    };

    // No BaseAddress on the client: the provider sets it from the options, as it will when
    // IHttpClientFactory hands it a fresh client.
    private static AzureOpenAiChatProvider Create(
        HttpMessageHandler handler, AzureOpenAiOptions? options = null, ILogger<AzureOpenAiChatProvider>? logger = null) =>
        new(new HttpClient(handler), options ?? Options(), new FakeTimeProvider(),
            logger ?? NullLogger<AzureOpenAiChatProvider>.Instance);

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    private sealed class CountingProvider(string name) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(new ChatResponse("answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", name));
        }
    }

    private sealed class RecordingLogger<T> : ILogger<T>
    {
        public List<(LogLevel Level, string Message)> Entries { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter)
            => Entries.Add((logLevel, formatter(state, exception)));
    }

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." },
        "content_filter_results": { "hate": { "filtered": false, "severity": "safe" } } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
    }
    """;

    // The legacy documented shape: the code inside an error envelope.
    private const string FilteredPromptInEnvelope = """
    {
      "error": {
        "message": "The response was filtered due to the prompt triggering Azure OpenAI's content management policy.",
        "type": null,
        "param": "prompt",
        "code": "content_filter",
        "status": 400,
        "innererror": {
          "code": "ResponsibleAIPolicyViolation",
          "content_filter_result": { "violence": { "filtered": true, "severity": "high" } }
        }
      }
    }
    """;

    // The v1 OpenAPI error schema has no envelope; its diagnostics are under inner_error.
    private const string FilteredPromptTopLevelInnerUnderscore = """
    {
      "code": "content_filter",
      "message": "The response was filtered due to the prompt triggering Azure OpenAI's content management policy.",
      "param": "prompt",
      "type": null,
      "inner_error": {
        "code": "ResponsibleAIPolicyViolation",
        "content_filter_results": { "violence": { "filtered": true, "severity": "high" } }
      }
    }
    """;

    // No envelope, and the other spelling of the diagnostics, with a different inner code.
    private const string FilteredPromptTopLevelInnerError = """
    {
      "code": "content_filter",
      "message": "The prompt was filtered.",
      "innererror": { "code": "ContentFiltered" }
    }
    """;

    [Fact]
    public void Name_IsAzureOpenAi_AndItIsPricedAsTheConfiguredDeployment()
    {
        var provider = Create(new StubHttpMessageHandler());

        Assert.Equal("azure-openai", provider.Name);
        Assert.Equal(new PricingIdentity("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard"), provider.Pricing);
    }

    [Fact]
    public async Task Complete_ReportsAzureOpenAiAsTheAnsweringProvider()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        var provider = Create(handler);
        var response = await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal(provider.Pricing, response.Pricing);
    }

    [Theory]
    [InlineData("https://releaselens-aoai.openai.azure.com/openai/v1/")]
    [InlineData("https://releaselens-aoai.openai.azure.com/openai/v1")]
    public async Task Complete_PostsToTheV1ChatCompletionsUrl_WithNoApiVersion(string baseUrl)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(handler, Options(baseUrl)).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = handler.Requests[0];
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("https://releaselens-aoai.openai.azure.com/openai/v1/chat/completions", request.RequestUri!.ToString());
        Assert.Empty(request.RequestUri.Query);
    }

    [Fact]
    public async Task Complete_KeepsABaseAddressTheClientAlreadyHas()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var client = new HttpClient(handler) { BaseAddress = new Uri("https://apim.example.test/aoai/v1/") };

        await new AzureOpenAiChatProvider(client, Options(), new FakeTimeProvider(), NullLogger<AzureOpenAiChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("https://apim.example.test/aoai/v1/chat/completions", handler.Requests[0].RequestUri!.ToString());
    }

    [Fact]
    public async Task Complete_SendsTheDeploymentNameAsTheModel()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        // Options().Model is gpt-4.1-mini, which names the pricing identity and is never sent.
        Assert.Equal(Deployment, document.RootElement.GetProperty("model").GetString());
    }

    [Fact]
    public async Task Complete_SetsNoApiKeyHeaderAndNoAuthorizationOfItsOwn()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var client = new HttpClient(handler);

        await new AzureOpenAiChatProvider(client, Options(), new FakeTimeProvider(), NullLogger<AzureOpenAiChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        // With no EntraTokenHandler in the pipeline, nothing may authenticate the request:
        // the token is the handler's job, and a key is never sent at all.
        var request = handler.Requests[0];
        Assert.Null(request.Headers.Authorization);
        Assert.False(request.Headers.Contains("api-key"));
        Assert.Null(client.DefaultRequestHeaders.Authorization);
        Assert.False(client.DefaultRequestHeaders.Contains("api-key"));
    }

    [Theory]
    [InlineData(FilteredPromptInEnvelope)]
    [InlineData(FilteredPromptTopLevelInnerUnderscore)]
    [InlineData(FilteredPromptTopLevelInnerError)]
    public async Task Complete_FilteredPrompt_ThrowsContentFilteredAtThePromptStage(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);
        var provider = Create(handler);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Prompt, exception.Stage);
        Assert.Equal(TokenUsage.Zero, exception.Usage);
        Assert.Equal(provider.Pricing, exception.Pricing);
    }

    [Theory]
    [InlineData(FilteredPromptInEnvelope)]
    [InlineData(FilteredPromptTopLevelInnerUnderscore)]
    [InlineData(FilteredPromptTopLevelInnerError)]
    public async Task Complete_FilteredPrompt_IsNotAnsweredByTheNextProvider(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);
        var next = new CountingProvider("anthropic");

        await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([Create(handler), next], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(0, next.Calls);
    }

    [Fact]
    public async Task Complete_FilteredCompletion_ThrowsContentFilteredAtTheCompletionStage_AndIsNotAnsweredElsewhere()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "content_filter",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the" },
            "content_filter_results": { "violence": { "filtered": true, "severity": "high" } } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 30 }
        }
        """);
        var azure = Create(handler);
        var next = new CountingProvider("anthropic");

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([azure, next], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Completion, exception.Stage);
        Assert.Equal(new TokenUsage(1200, 30, 0, 0), exception.Usage);
        Assert.Equal(azure.Pricing, exception.Pricing);
        Assert.Equal(0, next.Calls);
    }

    [Fact]
    public async Task Complete_FilterDidNotRun_ReturnsTheAnswer_AndRecordsIt()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." },
            "content_filter_results": { "error": { "code": "content_filter_error", "message": "The contents are not filtered" } } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
        }
        """);
        var logger = new RecordingLogger<AzureOpenAiChatProvider>();

        var response = await Create(handler, logger: logger).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal("stop", response.StopReason);
        var entry = Assert.Single(logger.Entries);
        Assert.Equal(LogLevel.Information, entry.Level);
        Assert.Contains("content_filter_error", entry.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Complete_FilterRan_RecordsNothing()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var logger = new RecordingLogger<AzureOpenAiChatProvider>();

        await Create(handler, logger: logger).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Empty(logger.Entries);
    }

    [Theory]
    [InlineData("""{ "error": { "message": "Invalid value for 'max_tokens'.", "type": "invalid_request_error", "param": "max_tokens", "code": "invalid_value" } }""")]
    [InlineData("""{ "code": "invalid_value", "message": "Invalid value for 'max_tokens'.", "innererror": { "code": "content_filter" } }""")]
    [InlineData("""{ "error": { "message": "content_filter", "code": null } }""")]
    [InlineData("Bad Request")]
    public async Task Complete_BadRequestThatIsNotAFilteredPrompt_IsStillOurOwnBug(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("(400)", exception.Message, StringComparison.Ordinal);
    }
}
```

Create `tests/ReleaseLens.Llm.Tests/WireEquivalenceTests.cs` (T-P7):
- The conversation has a system prompt, a user turn, two tool calls, one successful and one failed tool result, and two tool definitions.
- It goes through `OpenAiChatProvider` with an API key, and through `AzureOpenAiChatProvider` behind `EntraTokenHandler` with a fake `TokenCredential`.
- The body test parses both captured bodies, removes `model`, and compares the JSON strings. It then checks the raw bytes with the model value swapped.
- The header test compares every request and content header, as `name: value`, apart from `Authorization`. That is stricter than comparing names alone.

```csharp
using System;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Azure.Core;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The claim at the wire: going keyless on Azure changed how requests are authenticated, not
/// what is sent. Both providers are driven with the same conversation and their requests are
/// compared as captured.
/// </summary>
public class WireEquivalenceTests
{
    private const string OpenAiModel = "gpt-4.1-mini";
    private const string AzureDeployment = "releaselens-chat";

    private sealed class FixedCredential(string token, DateTimeOffset expiresOn) : TokenCredential
    {
        public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => new(token, expiresOn);

        public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => new(new AccessToken(token, expiresOn));
    }

    private const string Answer = """
    {
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner; release v9.9 does not exist." } }],
      "usage": { "prompt_tokens": 1500, "completion_tokens": 20 }
    }
    """;

    // Every part of the wire format at once: a system prompt, a user turn, an assistant turn
    // with two tool calls, one successful and one failed tool result, and a tool definition.
    private static ChatRequest Conversation() => new(
        "You answer questions about a repository.",
        [
            ChatMessage.User("What changed in release 1.30, and in v9.9?"),
            ChatMessage.AssistantToolCalls(
            [
                new ToolCall("call_01", "search_commits", JsonDocument.Parse("""{"query":"release 1.30"}""").RootElement),
                new ToolCall("call_02", "diff_between_releases",
                    JsonDocument.Parse("""{"from_tag":"v9.8","to_tag":"v9.9"}""").RootElement)
            ]),
            ChatMessage.UserToolResults(
            [
                new ToolResult("call_01", "3 commits found", false),
                new ToolResult("call_02", "No release tagged 'v9.9' exists in the indexed evidence.", true)
            ])
        ],
        [
            new ToolDefinition("search_commits", "Search commits.",
                JsonDocument.Parse("""{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}""").RootElement),
            new ToolDefinition("diff_between_releases", "Compare two releases.",
                JsonDocument.Parse("""{"type":"object","properties":{"from_tag":{"type":"string"},"to_tag":{"type":"string"}}}""").RootElement)
        ],
        1024);

    private static async Task<HttpRequestMessage> SendThroughOpenAi(ChatRequest request)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(Answer);
        var provider = new OpenAiChatProvider(
            new HttpClient(handler),
            new OpenAiOptions { ApiKey = "sk-test", Model = OpenAiModel, BaseUrl = "https://api.openai.com/v1/" });

        await provider.CompleteAsync(request, TestContext.Current.CancellationToken);
        return Assert.Single(handler.Requests);
    }

    private static async Task<HttpRequestMessage> SendThroughAzure(ChatRequest request)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(Answer);
        var time = new FakeTimeProvider();
        var cache = new EntraTokenCache(
            new FixedCredential("entra-access-token", time.GetUtcNow().AddHours(1)), "https://ai.azure.com/.default", time);

        var provider = new AzureOpenAiChatProvider(
            new HttpClient(new EntraTokenHandler(cache) { InnerHandler = handler }),
            new AzureOpenAiOptions
            {
                BaseUrl = "https://releaselens-aoai.openai.azure.com/openai/v1/",
                Deployment = AzureDeployment
            },
            time,
            NullLogger<AzureOpenAiChatProvider>.Instance);

        await provider.CompleteAsync(request, TestContext.Current.CancellationToken);
        return Assert.Single(handler.Requests);
    }

    private static async Task<string> Body(HttpRequestMessage request)
        => await request.Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);

    private static string WithoutModel(string body)
    {
        var node = JsonNode.Parse(body)!.AsObject();
        Assert.True(node.Remove("model"));
        return node.ToJsonString();
    }

    private static string[] HeadersExceptAuthorization(HttpRequestMessage request)
        => request.Headers
            .Concat(request.Content!.Headers)
            .Where(header => !string.Equals(header.Key, "Authorization", StringComparison.OrdinalIgnoreCase))
            .Select(header => $"{header.Key}: {string.Join(", ", header.Value)}")
            .Order(StringComparer.OrdinalIgnoreCase)
            .ToArray();

    [Fact]
    public async Task SameConversation_AzureAndOpenAiSendTheSameBody_ExceptTheModel()
    {
        var request = Conversation();

        var openAiBody = await Body(await SendThroughOpenAi(request));
        var azureBody = await Body(await SendThroughAzure(request));

        Assert.Equal(WithoutModel(openAiBody), WithoutModel(azureBody));

        // Stronger than the parsed comparison: with the model value swapped, the bytes match.
        Assert.Equal(openAiBody, azureBody.Replace(
            $"\"model\":\"{AzureDeployment}\"", $"\"model\":\"{OpenAiModel}\"", StringComparison.Ordinal));
    }

    [Fact]
    public async Task SameConversation_AzureAndOpenAiSendTheSameHeaders_ExceptAuthorization()
    {
        var request = Conversation();

        var openAi = await SendThroughOpenAi(request);
        var azure = await SendThroughAzure(request);

        Assert.Equal(HeadersExceptAuthorization(openAi), HeadersExceptAuthorization(azure));

        // Both authenticate with a bearer token; only its source differs.
        Assert.Equal("Bearer", openAi.Headers.Authorization!.Scheme);
        Assert.Equal("Bearer", azure.Headers.Authorization!.Scheme);
        Assert.Equal("entra-access-token", azure.Headers.Authorization.Parameter);
        Assert.False(azure.Headers.Contains("api-key"));
    }
}
```

- [ ] **Step 2: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiChatProviderTests|FullyQualifiedName~WireEquivalenceTests"
```

Expected: the build fails with `error CS0246: The type or namespace name 'AzureOpenAiChatProvider' could not be found` at `AzureOpenAiChatProviderTests.cs(34,20)` and `(35,81)`, the `Create` helper's signature. The compiler lists no error in `WireEquivalenceTests.cs` at this point: it stops at declaration errors, before binding method bodies. `using ReleaseLens.Llm.Providers.Azure;` itself resolves, because Task 7 created that namespace.

- [ ] **Step 3: Implement the provider, without the filter handling**

Create `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs`:

```csharp
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Azure OpenAI through its v1 endpoint, which speaks the OpenAI chat-completions wire format,
/// so the request body and the response go through <see cref="OpenAiWireFormat"/> exactly as
/// OpenAI's do. What is Azure's own lives here: the deployment name in <c>model</c>, the
/// filtered-prompt 400, and the filter-did-not-run marker on a 200.
/// </summary>
/// <remarks>
/// Authentication is not done here. <see cref="EntraTokenHandler"/> on this provider's
/// <see cref="HttpClient"/> attaches the Entra token, and no API key header is ever set,
/// because the account has key authentication turned off.
/// </remarks>
public sealed class AzureOpenAiChatProvider : IPricedChatProvider
{
    private readonly HttpClient _client;
    private readonly AzureOpenAiOptions _options;
    private readonly TimeProvider _time;
    private readonly ILogger<AzureOpenAiChatProvider> _logger;

    public string Name => "azure-openai";

    public PricingIdentity Pricing { get; }

    public AzureOpenAiChatProvider(
        HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger)
    {
        _client = client;
        _options = options;
        _time = time;
        _logger = logger;

        Pricing = new PricingIdentity("azure-openai", options.Model, options.ModelVersion, options.DeploymentType);

        // Without the trailing slash, "chat/completions" resolves against ".../openai/" and the
        // "v1" segment is silently dropped.
        _client.BaseAddress ??= new Uri(options.BaseUrl.EndsWith('/') ? options.BaseUrl : options.BaseUrl + "/");
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        // The v1 endpoint takes no api-version query parameter, and its "model" is the
        // deployment name, never the model name.
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Deployment);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return OpenAiWireFormat.Parse(document.RootElement, Name, _options.Deployment, Pricing);
        }
    }
}
```

- [ ] **Step 4: Run them and watch the filter tests fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiChatProviderTests|FullyQualifiedName~WireEquivalenceTests"
```

Expected: `Failed!  - Failed: 7, Passed: 15, Total: 22`.
- The six rows of `Complete_FilteredPrompt_ThrowsContentFilteredAtThePromptStage` and `Complete_FilteredPrompt_IsNotAnsweredByTheNextProvider` fail with `Assert.Throws() Failure: Exception type was not an exact match`, `Expected: typeof(ReleaseLens.Llm.Providers.ContentFilteredException)`, `Actual: typeof(System.InvalidOperationException)`. The 400 is still classed as our own bug.
- `Complete_FilterDidNotRun_ReturnsTheAnswer_AndRecordsIt` fails with `Assert.Single() Failure: The collection was empty`.

The two `WireEquivalenceTests` pass already. That is expected: both providers now go through the one codec, which is what T-P7 checks and what keeps it green later. `Complete_FilteredCompletion_...` also passes already, because the codec (Task 3) throws the completion-stage exception. It stays as the Azure-path guard that T-P13 asks for.

- [ ] **Step 5: Implement the filtered prompt**

In `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs`:

1. Add `using System.Net;` as the first line of the using block, which becomes:

```csharp
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;
```

2. Replace `CompleteAsync` with:

```csharp
    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        // The v1 endpoint takes no api-version query parameter, and its "model" is the
        // deployment name, never the model name.
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Deployment);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            if (response.StatusCode == HttpStatusCode.BadRequest
                && IsFilteredPrompt(await response.Content.ReadAsStringAsync(cancellationToken)))
            {
                // A rejected prompt produced no completion, so there is no usage to report.
                throw new ContentFilteredException(Name, ContentFilterStage.Prompt, TokenUsage.Zero, Pricing);
            }

            // The body is buffered, so ProviderHttp can read it again for its message.
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return OpenAiWireFormat.Parse(document.RootElement, Name, _options.Deployment, Pricing);
        }
    }
```

3. Add these two methods directly after `CompleteAsync`, before the class's closing brace:

```csharp
    /// <summary>
    /// A filtered prompt is a 400 whose <c>code</c> is <c>content_filter</c>, either inside an
    /// <c>error</c> envelope or at the top level. Which of the two the v1 endpoint sends is
    /// unverified (the only documented example is the legacy, enveloped one), so both are
    /// accepted. <c>innererror</c> and <c>inner_error</c> are diagnostics only and never read.
    /// </summary>
    private static bool IsFilteredPrompt(string body)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(body);
        }
        catch (JsonException)
        {
            return false;
        }

        using (document)
        {
            var root = document.RootElement;

            return IsContentFilterCode(root)
                || (root.ValueKind == JsonValueKind.Object
                    && root.TryGetProperty("error", out var error)
                    && IsContentFilterCode(error));
        }
    }

    private static bool IsContentFilterCode(JsonElement element)
        => element.ValueKind == JsonValueKind.Object
           && element.TryGetProperty("code", out var code)
           && code.ValueKind == JsonValueKind.String
           && code.ValueEquals("content_filter");
```

- [ ] **Step 6: Run them and watch the last one fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiChatProviderTests|FullyQualifiedName~WireEquivalenceTests"
```

Expected: `Failed!  - Failed: 1, Passed: 21, Total: 22`. The one failure is `Complete_FilterDidNotRun_ReturnsTheAnswer_AndRecordsIt`, with `Assert.Single() Failure: The collection was empty`. All four rows of `Complete_BadRequestThatIsNotAFilteredPrompt_IsStillOurOwnBug` pass, including `content_filter` under `innererror` and the non-JSON body.

- [ ] **Step 7: Record a filter that did not run**

In the same file:

1. Replace `CompleteAsync` with the version below. Only the end of the `using (response)` block changes. The response is parsed first, so a completion that is both filtered and marked with a filter error throws from `Parse` and never gets logged as returned.

```csharp
    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        // The v1 endpoint takes no api-version query parameter, and its "model" is the
        // deployment name, never the model name.
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Deployment);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            if (response.StatusCode == HttpStatusCode.BadRequest
                && IsFilteredPrompt(await response.Content.ReadAsStringAsync(cancellationToken)))
            {
                // A rejected prompt produced no completion, so there is no usage to report.
                throw new ContentFilteredException(Name, ContentFilterStage.Prompt, TokenUsage.Zero, Pricing);
            }

            // The body is buffered, so ProviderHttp can read it again for its message.
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            var answer = OpenAiWireFormat.Parse(document.RootElement, Name, _options.Deployment, Pricing);

            if (TryGetFilterError(document.RootElement, out var filterError))
            {
                // The filter did not run on this completion. Nothing was filtered, so the answer
                // stands, but it went out unchecked, which is worth a record.
                _logger.LogInformation(
                    "{Provider} content filter did not run on the completion; returning the answer. Filter error: {FilterError}",
                    Name, filterError);
            }

            return answer;
        }
    }
```

2. Add this method as the last member of the class, after `IsContentFilterCode` and before the class's closing brace:

```csharp
    /// <summary>
    /// <c>content_filter_results.error</c> on a choice means the filter failed to run, not
    /// that it blocked anything.
    /// </summary>
    private static bool TryGetFilterError(JsonElement root, out string filterError)
    {
        // Parse has already proved choices is a non-empty array.
        if (root.GetProperty("choices")[0] is { ValueKind: JsonValueKind.Object } choice
            && choice.TryGetProperty("content_filter_results", out var results)
            && results.ValueKind == JsonValueKind.Object
            && results.TryGetProperty("error", out var error)
            && error.ValueKind == JsonValueKind.Object)
        {
            filterError = error.GetRawText();
            return true;
        }

        filterError = string.Empty;
        return false;
    }
```

- [ ] **Step 8: Run them and watch them pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiChatProviderTests|FullyQualifiedName~WireEquivalenceTests"
```

Expected: `Passed!  - Failed: 0, Passed: 22, Total: 22`: 20 in `AzureOpenAiChatProviderTests` (8 facts, plus theory rows 2 + 3 + 3 + 4) and 2 in `WireEquivalenceTests`.

- [ ] **Step 9: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceToolTests&FullyQualifiedName!~EvidenceToolBoundsTests"
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected:
- The build reports `0 Warning(s)` and `0 Error(s)` (`TreatWarningsAsErrors` is on).
- Every test passes.
- The second command needs no Docker. It runs every Llm test outside the Postgres Testcontainers collection, including both new classes and the existing `OpenAiChatProviderTests`, `OpenAiWireFormatTests`, `FallbackChatProviderTests` and `ModelPricingTests`.
- The third and fourth need Docker running. `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests`, and the Api project's `/query` and `/evidence` endpoint tests, use the `PostgresCollection`. Nothing in the Api project references the new class until Task 10; it is run because it compiles against `ReleaseLens.Llm`.

- [ ] **Step 10: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs tests/ReleaseLens.Llm.Tests/AzureOpenAiChatProviderTests.cs tests/ReleaseLens.Llm.Tests/WireEquivalenceTests.cs
git commit -m "feat(llm): Azure OpenAI chat provider on the v1 endpoint" -m "AzureOpenAiChatProvider speaks the OpenAI wire format through the shared
codec, sends its deployment name as the model, posts to the v1
chat/completions URL with no api-version, and sets no key header: the
Entra token comes from EntraTokenHandler. A 400 whose code is
content_filter, in an error envelope or at the top level, is a filtered
prompt and never falls through; any other 400 is still our own bug. A
200 whose content_filter_results carries an error is returned and logged,
not treated as filtered. A test shows the Azure body equals the OpenAI
body except for model, and the headers differ only in Authorization.
The 429 wait is not in this commit." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The 429 wait budget

On a 429, `AzureOpenAiChatProvider` now reads how long Azure advises waiting. If that wait fits what is left of the query's budget, it waits through `TimeProvider` and sends the same request to the same provider once more (spec §4.4). Any other 429 goes through `ProviderHttp.ThrowIfNotSuccessAsync` as it does after Task 8, so it becomes `ProviderUnavailableException` and the chain falls through at once. That covers a second 429, a wait that does not fit, a response with no usable header, and a call with no `QueryContext`.

The rules, as implemented and tested here:
- **Which header.** `retry-after-ms` (milliseconds) when it is usable. Otherwise `retry-after`, as whole seconds only; its HTTP-date form is ignored. When both are usable, `retry-after-ms` wins.
- **What is usable (Review Focus 1, decided here).** Exactly one header value that is a plain non-negative integer and fits an `int`. It is parsed with `int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, ...)`, which allows ASCII digits only. Anything else counts as absent and is never rounded, trimmed or clamped: a decimal (`1.5`), a sign (`-5`, `+200`), whitespace (` 200 `), a unit (`200ms`), an empty value, two values, or a number past `int.MaxValue` (`99999999999`). So a malformed `retry-after-ms` falls back to `retry-after`, and when that is unusable too, the call falls through. A well-formed value that is simply too long (up to `int.MaxValue` ms, about 24.8 days) is parsed but falls through as well, because it does not fit the budget.
- **The budget.** `QueryContext.TrySpendWait(wait)` (Task 5) takes the wait from the query's budget before the wait starts. If the wait does not fit, the budget is left unchanged. The budget belongs to the `QueryContext`, never to the provider, so two concurrent queries on the one singleton provider each have their own. `TrySpendWait` throws on a negative wait. That cannot happen here, because `NumberStyles.None` never parses a sign.
- **No context, no wait.** With `request.Context == null` the provider never waits.
- **One retry.** The retry's response is returned as is. A second 429 becomes `ProviderUnavailableException`, and there is no loop.
- **Zero.** `retry-after-ms: 0` is a usable wait of zero. `Task.Delay(TimeSpan.Zero, time, ct)` completes at once without creating a timer (checked), so the retry is immediate. No test pins this.
- **Cancellation.** If the caller's token is cancelled during the wait, `Task.Delay` throws `TaskCanceledException`. That propagates as the caller's cancellation, not as "unavailable", and no retry is sent.

The request body is built once and posted twice. `PostAsJsonAsync` makes new content from the same `JsonObject` on each call, so the retry's bytes equal the first request's (a test asserts this).

The wait is scheduled on the provider's `_time`, which Task 8 stored and nothing read until now. The tests wrap a `FakeTimeProvider` in a small `TimeProvider` that records every `CreateTimer` call. That record tells a test when the delay has really been scheduled, so it can advance the clock without a race, and it lets a test prove that no delay was scheduled. It also turns "the provider never waited" and "the provider waited when it should not have" into test failures, where they would otherwise hang the test.

**Files:**
- Modify: `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs`, as Task 8 left it:
  - the using block, which gains `System.Globalization` and `System.Text.Json.Nodes`
  - the class `<summary>`, which gains "and the bounded wait on a 429"
  - one statement in `CompleteAsync`: the `ProviderHttp.SendAsync(...)` call that produces `response`
  - four new private methods, `SendAsync`, `PostAsync`, `ReadAdvisedWait` and `TryReadPlainInteger`, inserted directly after `CompleteAsync` and before `IsFilteredPrompt`
- Create: `tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs`
- Test: `tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs` (T-P8, T-P8a, T-P8b, T-P9, T-P10, T-P11, Review Focus 1)

No call site changes. The constructor and every public member keep the shapes Task 8 gave them. `AzureOpenAiChatProvider` is constructed only in Task 8's `AzureOpenAiChatProviderTests.cs` and `WireEquivalenceTests.cs`, and, from Task 10, in `Program.cs`. None of Task 8's tests sets `ChatRequest.Context`, so with no context the provider never waits and their behaviour is unchanged. All 22 of them were run against this task's provider and pass. The test project already references `Microsoft.Extensions.TimeProvider.Testing` (Task 7) and `ReleaseLens.Ingestion.Tests` (for `StubHttpMessageHandler`).

**Interfaces:**
- Consumes:
  - `public sealed class QueryContext(TimeSpan rateLimitWaitBudget)` with `public TimeSpan RateLimitWaitRemaining { get; private set; }` and `public bool TrySpendWait(TimeSpan wait)` ("true and subtracts when wait <= remaining; otherwise false, unchanged"; Task 5's version also throws `ArgumentOutOfRangeException` on a negative wait) (Task 5)
  - `public sealed record ChatRequest(string SystemPrompt, IReadOnlyList<ChatMessage> Messages, IReadOnlyList<ToolDefinition> Tools, int MaxTokens) { public QueryContext? Context { get; init; } }` (Tasks 1, 5)
  - `public sealed class AzureOpenAiChatProvider(HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger) : IPricedChatProvider`, with `Name => "azure-openai"` and its private fields `_client`, `_options`, `_time` and `_logger` (Task 8)
  - `internal static class OpenAiWireFormat` with `public static JsonObject BuildPayload(ChatRequest request, string model)` (Task 2)
  - `ProviderHttp.SendAsync`, `ProviderHttp.ThrowIfNotSuccessAsync` and `ProviderHttp.JsonOptions` (existing, unchanged). A 429 maps to `ProviderUnavailableException`.
  - `FallbackChatProvider(IReadOnlyList<IChatProvider> providers, ILogger<FallbackChatProvider> logger)` (Task 5 behaviour; used only by tests)
  - `public sealed class AzureOpenAiOptions` with `BaseUrl` and `Deployment` (Task 7)
- Produces:
  - `AzureOpenAiChatProvider.CompleteAsync(ChatRequest request, CancellationToken cancellationToken)` with the §4.4 behaviour above. No new public member.
  - For Task 10: the wait runs on the `TimeProvider` passed to the constructor, so `Program.cs` must register or pass `TimeProvider.System`. The budget comes from `AgentOptions.RateLimitWaitBudgetMs` through the `QueryContext` that `QueryAgent` creates (Task 5); the provider reads no configuration for it.

- [ ] **Step 1: Write the failing tests**

Create `tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs`. How the spec's tests map to test methods:

| Spec | Test |
|---|---|
| T-P8 | `RetryAfterMs_WithinBudget_WaitsThatLongAndRetriesTheSameProviderOnce`, through a `FallbackChatProvider` whose second member must not be called; plus `SecondRateLimit_FallsThroughWithoutASecondWait` for "once" |
| T-P8a | `RetryAfterSeconds_WithoutRetryAfterMs_IsHonoured` |
| T-P8b | `BothHeaders_RetryAfterMsWins` |
| T-P9 | `WaitLongerThanWhatRemains_FallsThroughWithoutWaiting`: part of the budget is already spent, the next provider answers, and no timer is created |
| T-P10 | `NoUsableRetryHeader_FallsThroughWithoutWaiting`, over no header, an HTTP-date `retry-after`, `1.5` and `-1`; plus `NoQueryContext_NeverWaits` |
| T-P11 | `Budget_IsPerQuery_NotPerProvider`: query B starts while query A's wait is pending, on the same provider instance |
| Review Focus 1 | `MalformedRetryAfterMs_IsTreatedAsAbsent`, over `1.5`, `-5`, `99999999999`, ` 200 `, `+200`, `200ms` and the empty string; plus `MalformedRetryAfterMs_FallsBackToRetryAfter` |
| (§4.4, no ID) | `CancellationDuringTheWait_StopsWithoutRetrying` |

```csharp
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The 429 wait (spec §4.4): honour <c>retry-after-ms</c>, then whole-second <c>retry-after</c>,
/// only within what is left of the query's budget, and retry the same provider once.
/// </summary>
public class AzureOpenAiRateLimitTests
{
    private const string RateLimitedBody = """{"error":{"code":"429","message":"Rate limit is exceeded."}}""";

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
    }
    """;

    /// <summary>
    /// A <see cref="FakeTimeProvider"/> that also records every timer asked of it. A test must
    /// know the delay has been scheduled before it advances the clock: advancing first would
    /// leave the delay due after the new "now", and the test would hang. The record also lets a
    /// test prove no delay was scheduled at all.
    /// </summary>
    private sealed class ObservedTimeProvider : TimeProvider
    {
        private readonly List<TimeSpan> _dueTimes = [];
        private readonly SemaphoreSlim _scheduled = new(0);

        public FakeTimeProvider Clock { get; } = new();

        public IReadOnlyList<TimeSpan> DueTimes
        {
            get { lock (_dueTimes) { return [.. _dueTimes]; } }
        }

        /// <summary>
        /// Completes once <paramref name="call"/> has scheduled a timer. If the call finishes
        /// first, which is what a provider that never waits does, the test fails instead of
        /// hanging.
        /// </summary>
        public async Task WaitForTimerAsync(Task call, CancellationToken cancellationToken)
        {
            var scheduled = _scheduled.WaitAsync(cancellationToken);
            if (await Task.WhenAny(scheduled, call) == scheduled)
            {
                return;
            }

            await call;
            Assert.Fail("The call completed without scheduling a wait.");
        }

        /// <summary>
        /// Awaits <paramref name="call"/>, failing the test instead of hanging if the call
        /// schedules a wait first.
        /// </summary>
        public async Task<T> WithoutWaitingAsync<T>(Task<T> call, CancellationToken cancellationToken)
        {
            var scheduled = _scheduled.WaitAsync(cancellationToken);
            if (await Task.WhenAny(scheduled, call) == scheduled)
            {
                Assert.Fail("The call scheduled a wait.");
            }

            return await call;
        }

        public override DateTimeOffset GetUtcNow() => Clock.GetUtcNow();
        public override long GetTimestamp() => Clock.GetTimestamp();
        public override long TimestampFrequency => Clock.TimestampFrequency;
        public override TimeZoneInfo LocalTimeZone => Clock.LocalTimeZone;

        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
        {
            var timer = Clock.CreateTimer(callback, state, dueTime, period);
            lock (_dueTimes)
            {
                _dueTimes.Add(dueTime);
            }

            _scheduled.Release();
            return timer;
        }
    }

    private sealed class CountingProvider(string name) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(new ChatResponse("answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", name));
        }
    }

    private static AzureOpenAiChatProvider Create(StubHttpMessageHandler handler, TimeProvider time) =>
        new(new HttpClient(handler),
            new AzureOpenAiOptions
            {
                BaseUrl = "https://releaselens-test.openai.azure.com/openai/v1/",
                Deployment = "releaselens-chat"
            },
            time,
            NullLogger<AzureOpenAiChatProvider>.Instance);

    private static ChatRequest Request(QueryContext? context) => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024)
    {
        Context = context
    };

    private static QueryContext Budget(int milliseconds) => new(TimeSpan.FromMilliseconds(milliseconds));

    private static StubHttpMessageHandler RateLimited(
        StubHttpMessageHandler handler, string? retryAfterMs = null, string? retryAfter = null)
        => handler.Enqueue(HttpStatusCode.TooManyRequests, RateLimitedBody, response =>
        {
            if (retryAfterMs is not null)
            {
                response.Headers.TryAddWithoutValidation("retry-after-ms", retryAfterMs);
            }

            if (retryAfter is not null)
            {
                response.Headers.TryAddWithoutValidation("retry-after", retryAfter);
            }
        });

    [Fact]
    public async Task RetryAfterMs_WithinBudget_WaitsThatLongAndRetriesTheSameProviderOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1200").EnqueueJson(TextResponse);
        var secondary = new CountingProvider("anthropic");
        var chain = new FallbackChatProvider([Create(handler, time), secondary], NullLogger<FallbackChatProvider>.Instance);
        var context = Budget(3000);

        var completion = chain.CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(1200)], time.DueTimes);
        Assert.Single(handler.Requests);

        time.Clock.Advance(TimeSpan.FromMilliseconds(1199));
        Assert.Single(handler.Requests);
        Assert.False(completion.IsCompleted);

        time.Clock.Advance(TimeSpan.FromMilliseconds(1));
        var response = await completion;

        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(handler.Requests[0].RequestUri, handler.Requests[1].RequestUri);
        Assert.Equal(
            await handler.Requests[0].Content!.ReadAsStringAsync(ct),
            await handler.Requests[1].Content!.ReadAsStringAsync(ct));
        Assert.Equal(0, secondary.Calls);
        Assert.Equal(TimeSpan.FromMilliseconds(1800), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task RetryAfterSeconds_WithoutRetryAfterMs_IsHonoured()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfter: "2").EnqueueJson(TextResponse);
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromSeconds(2)], time.DueTimes);
        Assert.Single(handler.Requests);

        time.Clock.Advance(TimeSpan.FromSeconds(2));
        var response = await completion;

        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(TimeSpan.FromSeconds(1), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task BothHeaders_RetryAfterMsWins()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "400", retryAfter: "2")
            .EnqueueJson(TextResponse);
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(400)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromMilliseconds(400));
        await completion;

        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(TimeSpan.FromMilliseconds(2600), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task WaitLongerThanWhatRemains_FallsThroughWithoutWaiting()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1500");
        var secondary = new CountingProvider("anthropic");
        var chain = new FallbackChatProvider([Create(handler, time), secondary], NullLogger<FallbackChatProvider>.Instance);

        // 1500 ms would fit the full 3 s budget but not the 1 s left of it.
        var context = Budget(3000);
        Assert.True(context.TrySpendWait(TimeSpan.FromSeconds(2)));

        var response = await time.WithoutWaitingAsync(chain.CompleteAsync(Request(context), ct), ct);

        Assert.Equal("anthropic", response.Provider);
        Assert.Single(handler.Requests);
        Assert.Equal(1, secondary.Calls);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(1), context.RateLimitWaitRemaining);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("Wed, 21 Oct 2026 07:28:00 GMT")]
    [InlineData("1.5")]
    [InlineData("-1")]
    public async Task NoUsableRetryHeader_FallsThroughWithoutWaiting(string? retryAfter)
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfter: retryAfter);
        var context = Budget(3000);

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context), ct), ct));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(3), context.RateLimitWaitRemaining);
    }

    /// <summary>
    /// Review Focus 1. Only a plain non-negative integer within <see cref="int"/> is usable;
    /// anything else is absent, so the call falls through at once instead of waiting a rounded,
    /// clamped or unbounded time. Surrounding whitespace never reaches the provider from a real
    /// HTTP/1.1 response, because SocketsHttpHandler strips it; the row is here because the stub
    /// stores the value as given.
    /// </summary>
    [Theory]
    [InlineData("1.5")]
    [InlineData("-5")]
    [InlineData("99999999999")]
    [InlineData(" 200 ")]
    [InlineData("+200")]
    [InlineData("200ms")]
    [InlineData("")]
    public async Task MalformedRetryAfterMs_IsTreatedAsAbsent(string retryAfterMs)
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: retryAfterMs);
        var context = Budget(3000);

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context), ct), ct));

        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(3), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task MalformedRetryAfterMs_FallsBackToRetryAfter()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1.5", retryAfter: "1")
            .EnqueueJson(TextResponse);

        var completion = Create(handler, time).CompleteAsync(Request(Budget(3000)), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromSeconds(1)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromSeconds(1));
        await completion;

        Assert.Equal(2, handler.Requests.Count);
    }

    [Fact]
    public async Task SecondRateLimit_FallsThroughWithoutASecondWait()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(RateLimited(new StubHttpMessageHandler(), retryAfterMs: "100"), retryAfterMs: "100");
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);
        time.Clock.Advance(TimeSpan.FromMilliseconds(100));

        await Assert.ThrowsAsync<ProviderUnavailableException>(() => time.WithoutWaitingAsync(completion, ct));

        Assert.Equal(2, handler.Requests.Count);
        Assert.Single(time.DueTimes);
        Assert.Equal(TimeSpan.FromMilliseconds(2900), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task NoQueryContext_NeverWaits()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "100");

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context: null), ct), ct));

        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
    }

    [Fact]
    public async Task Budget_IsPerQuery_NotPerProvider()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();

        // Responses are served in request order: query A's 429, query B's 429, B's retry (its
        // wait is shorter, so it fires first), A's retry, then A's later 429.
        var handler = RateLimited(RateLimited(new StubHttpMessageHandler(), retryAfterMs: "2500"), retryAfterMs: "2000")
            .EnqueueJson(TextResponse)
            .EnqueueJson(TextResponse);
        RateLimited(handler, retryAfterMs: "2500");

        var provider = Create(handler, time);
        var queryA = Budget(3000);
        var queryB = Budget(3000);

        var firstA = provider.CompleteAsync(Request(queryA), ct);
        await time.WaitForTimerAsync(firstA, ct);

        // A has already spent 2500 of its 3000 ms; B, on the same provider instance, still
        // has all of its own.
        var firstB = provider.CompleteAsync(Request(queryB), ct);
        await time.WaitForTimerAsync(firstB, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(2500), TimeSpan.FromMilliseconds(2000)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromMilliseconds(2000));
        await firstB;
        time.Clock.Advance(TimeSpan.FromMilliseconds(500));
        await firstA;

        Assert.Equal(TimeSpan.FromMilliseconds(500), queryA.RateLimitWaitRemaining);
        Assert.Equal(TimeSpan.FromMilliseconds(1000), queryB.RateLimitWaitRemaining);

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(provider.CompleteAsync(Request(queryA), ct), ct));

        Assert.Equal(5, handler.Requests.Count);
        Assert.Equal(2, time.DueTimes.Count);
        Assert.Equal(TimeSpan.FromMilliseconds(500), queryA.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task CancellationDuringTheWait_StopsWithoutRetrying()
    {
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1000");
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);

        var completion = Create(handler, time).CompleteAsync(Request(Budget(3000)), cancellation.Token);
        await time.WaitForTimerAsync(completion, TestContext.Current.CancellationToken);
        await cancellation.CancelAsync();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () => await completion);
        Assert.Single(handler.Requests);
    }
}
```

- [ ] **Step 2: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiRateLimitTests"
```

Expected: the build succeeds, because the tests use no member that Task 8 lacks. The run ends `Failed!  - Failed: 7, Passed: 13, Total: 20`.

The seven tests that need a wait fail:
- `RetryAfterMs_WithinBudget_WaitsThatLongAndRetriesTheSameProviderOnce` fails with `The call completed without scheduling a wait.`, because the chain fell through to `anthropic`.
- `RetryAfterSeconds_WithoutRetryAfterMs_IsHonoured`, `BothHeaders_RetryAfterMsWins`, `MalformedRetryAfterMs_FallsBackToRetryAfter`, `SecondRateLimit_FallsThroughWithoutASecondWait`, `Budget_IsPerQuery_NotPerProvider` and `CancellationDuringTheWait_StopsWithoutRetrying` fail with `ReleaseLens.Llm.Providers.ProviderUnavailableException : azure-openai returned 429: {"error":{"code":"429","message":"Rate limit is exceeded."}}`.

The 13 fall-through tests already pass: `WaitLongerThanWhatRemains_...`, the 4 rows of `NoUsableRetryHeader_...`, the 7 rows of `MalformedRetryAfterMs_IsTreatedAsAbsent` and `NoQueryContext_NeverWaits`. That is expected. Task 8's provider falls through on every 429, and these tests pin that the falling through survives once waiting exists. They were checked against deliberately broken versions of this task's implementation, and each version was caught:
- ignoring the budget: `WaitLongerThanWhatRemains_...` failed with `The call scheduled a wait.`
- parsing with `NumberStyles.Integer | NumberStyles.AllowDecimalPoint`: the `-5`, ` 200 ` and `+200` rows failed, and so did the `-1` row of `NoUsableRetryHeader_...`
- waiting with no context: `NoQueryContext_NeverWaits` failed
- letting `retry-after` win: `BothHeaders_RetryAfterMsWins` failed
- retrying recursively: `SecondRateLimit_...` failed

- [ ] **Step 3: Implement the wait**

In `src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs`:

1. Replace the using block with:

```csharp
using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
```

2. Replace the class's `<summary>` with this one. The `<remarks>` below it stays as it is:

```csharp
/// <summary>
/// Azure OpenAI through its v1 endpoint, which speaks the OpenAI chat-completions wire format,
/// so the request body and the response go through <see cref="OpenAiWireFormat"/> exactly as
/// OpenAI's do. What is Azure's own lives here: the deployment name in <c>model</c>, the
/// filtered-prompt 400, the filter-did-not-run marker on a 200, and the bounded wait on a 429.
/// </summary>
```

3. Replace `CompleteAsync` with the version below. Compared with Task 8's final version (its Step 7), only one statement changes. The three-line `var response = await ProviderHttp.SendAsync(() => _client.PostAsJsonAsync(...), Name, cancellationToken);` becomes `var response = await SendAsync(payload, request.Context, cancellationToken);`. Everything inside `using (response)` is Task 8's, unchanged. If Task 8's body differs from this in any other line, keep Task 8's and change only that statement.

```csharp
    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        // The v1 endpoint takes no api-version query parameter, and its "model" is the
        // deployment name, never the model name.
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Deployment);

        var response = await SendAsync(payload, request.Context, cancellationToken);

        using (response)
        {
            if (response.StatusCode == HttpStatusCode.BadRequest
                && IsFilteredPrompt(await response.Content.ReadAsStringAsync(cancellationToken)))
            {
                // A rejected prompt produced no completion, so there is no usage to report.
                throw new ContentFilteredException(Name, ContentFilterStage.Prompt, TokenUsage.Zero, Pricing);
            }

            // The body is buffered, so ProviderHttp can read it again for its message.
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            var answer = OpenAiWireFormat.Parse(document.RootElement, Name, _options.Deployment, Pricing);

            if (TryGetFilterError(document.RootElement, out var filterError))
            {
                // The filter did not run on this completion. Nothing was filtered, so the answer
                // stands, but it went out unchecked, which is worth a record.
                _logger.LogInformation(
                    "{Provider} content filter did not run on the completion; returning the answer. Filter error: {FilterError}",
                    Name, filterError);
            }

            return answer;
        }
    }
```

4. Insert these four methods directly after `CompleteAsync` and before the `<summary>` of `IsFilteredPrompt`:

```csharp
    /// <summary>
    /// Sends the request, and on a 429 whose advised wait fits what is left of the query's
    /// budget, waits that long and sends it once more. Every other response, including a 429
    /// that cannot be waited out and a second 429, is returned for the caller to map as usual,
    /// so it becomes <see cref="ProviderUnavailableException"/> and the chain falls through.
    /// </summary>
    /// <remarks>
    /// The budget belongs to the query, not to this provider, which every concurrent query
    /// shares. Without a <see cref="QueryContext"/> there is no budget, so the call never waits.
    /// </remarks>
    private async Task<HttpResponseMessage> SendAsync(
        JsonObject payload, QueryContext? context, CancellationToken cancellationToken)
    {
        var response = await PostAsync(payload, cancellationToken);

        if (response.StatusCode != HttpStatusCode.TooManyRequests
            || context is null
            || ReadAdvisedWait(response) is not { } wait
            || !context.TrySpendWait(wait))
        {
            return response;
        }

        response.Dispose();

        _logger.LogInformation(
            "{Provider} returned 429; waiting the advised {WaitMs} ms before its one retry, {RemainingMs} ms of the query's wait budget left",
            Name, wait.TotalMilliseconds, context.RateLimitWaitRemaining.TotalMilliseconds);

        await Task.Delay(wait, _time, cancellationToken);

        return await PostAsync(payload, cancellationToken);
    }

    private Task<HttpResponseMessage> PostAsync(JsonObject payload, CancellationToken cancellationToken)
        => ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

    /// <summary>
    /// The wait a 429 advises: <c>retry-after-ms</c> in milliseconds when it is usable,
    /// otherwise <c>retry-after</c> in whole seconds, otherwise null. Microsoft's Azure OpenAI
    /// header table lists <c>retry-after-ms</c> and not <c>retry-after</c>, so the second is only
    /// a fallback, and its HTTP-date form is ignored.
    /// </summary>
    /// <remarks>
    /// A usable value is one header value that is a plain non-negative integer that fits an
    /// <see cref="int"/>. Anything else (a decimal, a sign, whitespace, a unit, a list, or a
    /// number past <see cref="int.MaxValue"/>) is treated as absent rather than rounded, trimmed
    /// or clamped, so the call falls through at once. A well-formed value too long for the
    /// budget falls through as well, because it does not fit.
    /// </remarks>
    private static TimeSpan? ReadAdvisedWait(HttpResponseMessage response)
    {
        if (TryReadPlainInteger(response, "retry-after-ms", out var milliseconds))
        {
            return TimeSpan.FromMilliseconds(milliseconds);
        }

        if (TryReadPlainInteger(response, "retry-after", out var seconds))
        {
            return TimeSpan.FromSeconds(seconds);
        }

        return null;
    }

    private static bool TryReadPlainInteger(HttpResponseMessage response, string header, out int value)
    {
        value = 0;

        // NonValidated gives the header text as received; the typed Headers.RetryAfter would
        // also accept an HTTP date. NumberStyles.None allows digits only: no sign, decimal
        // point or whitespace.
        return response.Headers.NonValidated.TryGetValues(header, out var values)
            && values.Count == 1
            && int.TryParse(values.Single(), NumberStyles.None, CultureInfo.InvariantCulture, out value);
    }
```

`values.Single()` needs `System.Linq`, which `ImplicitUsings` already supplies. `HeaderStringValues`, the type of `values`, implements `IReadOnlyCollection<string>`. The `TimeSpan.FromMilliseconds(int)` and `TimeSpan.FromSeconds(int)` calls bind without warnings on .NET 10, and `int.MaxValue` seconds is still inside `TimeSpan`'s range.

- [ ] **Step 4: Run them and watch them pass**

```
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~AzureOpenAiRateLimitTests|FullyQualifiedName~AzureOpenAiChatProviderTests|FullyQualifiedName~WireEquivalenceTests"
```

Expected: `Passed!  - Failed: 0, Passed: 42, Total: 42`. That is 20 in `AzureOpenAiRateLimitTests` (9 facts, plus theory rows 4 + 7) and Task 8's 22, which stay green.

- [ ] **Step 5: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName!~QueryAgentTests&FullyQualifiedName!~EvidenceToolTests&FullyQualifiedName!~EvidenceToolBoundsTests"
dotnet test tests/ReleaseLens.Llm.Tests
dotnet test tests/ReleaseLens.Api.Tests
```

Expected:
- The build reports `0 Warning(s)` and `0 Error(s)`. `TreatWarningsAsErrors` is on.
- Every test run reports `Failed: 0`.
- The second command needs no Docker. It runs every Llm test outside the Postgres Testcontainers collection, including this task's class and Task 8's.
- The third and fourth need Docker running. `QueryAgentTests`, `EvidenceToolTests` and `EvidenceToolBoundsTests`, and the Api project's `/query` and `/evidence` endpoint tests, use the `PostgresCollection`.
- The Api project is run because it compiles against `ReleaseLens.Llm`. Nothing in it constructs the Azure provider until Task 10.

- [ ] **Step 6: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Llm/Providers/Azure/AzureOpenAiChatProvider.cs tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs
git commit -m "feat(llm): wait out an Azure 429 within the query's budget" -m "On a 429 the Azure provider reads retry-after-ms, or failing that
retry-after as whole seconds, and when the advised wait fits what is left
of the query's QueryContext budget it waits through TimeProvider and
retries the same provider once. A second 429, a wait that does not fit,
no usable header, or no QueryContext falls through at once as before.
Only a plain non-negative integer is a usable header value: a decimal, a
sign, whitespace, a unit or a number past int.MaxValue is treated as
absent, never rounded or clamped. The budget lives on the query, so
concurrent queries on the one provider instance do not share it." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Wiring

`Program.cs` stops hard-coding `[Anthropic, OpenAI]`. It builds the chain by name from `Chat:Providers`, registers the Azure provider's typed client with `EntraTokenHandler`, fills `AzureOpenAiOptions.ClientId` from `AZURE_CLIENT_ID`, and checks every selected provider's pricing identity with `ModelPricing.EnsurePriced` before the app serves anything. `appsettings.json` gains the `AzureOpenAi` section. It gets no `Chat` section: `Chat:Providers` lives only in environment-specific configuration.

This task starts from the tree Tasks 1–9 leave:
- `ChatOptions` and `ChatProviderSelection` exist (Task 5). `Select` checks every name before it runs any factory, and its message ends `Valid providers: <sorted keys>.`
- `PricingIdentity`, `IPricedChatProvider`, `ModelPricing.EnsurePriced` and `OpenAiOptions.Unpriced` exist (Task 4). `EnsurePriced` names each identity that has no rate, for example `openai gpt-no-rate-test`.
- `AzureOpenAiOptions`, `EntraTokenCache`, `EntraTokenHandler` and `AzureCredentialFactory` exist (Task 7). `Azure.Identity` is a `PackageReference` of `ReleaseLens.Llm`, so `Azure.Core` reaches `ReleaseLens.Api` through the project reference.
- `AzureOpenAiChatProvider` exists (Tasks 8–9).
- Tasks 5 and 6 changed only the last arguments of `new QueryMetadataDto(...)` in `Program.cs`. No earlier task touches the lines this task replaces.

What this task has to get right, each checked against the real packages in a scratch copy of the repository:
- **Binding appends to the default list.** `section.Bind(new ChatOptions())` with `["anthropic", "openai"]` in configuration yields `anthropic, openai, anthropic, openai` (`Microsoft.Extensions.Configuration.Binder` 10.0.0), and `Select` would then fail on the duplicate. So `Program.cs` reads `Chat:Providers` on its own with `Get<List<string>>()`. That returns `null` when nothing configures the list, and then `ChatOptions`' default applies. A configured empty array also returns `null`, because it produces no configuration keys.
- **`Chat:Providers` is not written into `appsettings.json`.** Configuration arrays overlay index by index, and no higher layer can remove an index. With `["anthropic", "openai"]` in the file, `Chat__Providers__0=azure-openai` from the environment yields `azure-openai, openai`, so the one-entry lists spec §4.3 needs (the deployed app's `["azure-openai"]`, and each evaluation arm's single provider) could not be set from environment variables. With no file entry, `ChatOptions`' default (`anthropic`, `openai`) gives the same local chain, and `Chat__Providers__0` alone yields exactly one provider (observed with the binder probe, and pinned end to end by the new `QueryEndpointTests` test in Step 1). Every index a run needs is set by that run's own configuration.
- **Only a listed provider is built.** `ChatProviderSelection.Select` runs only the factories whose names are listed. The token cache and the credential are singletons, so they are built only when something resolves them, and only the Azure client's handler does. A configuration that does not list `azure-openai` therefore never builds a credential. A test pins this with a credential that throws when built. This relies on `Select` validating every name before it runs any factory, and running only the listed ones (Task 5).
- **An empty Azure setting fails at startup, naming the setting.** Task 8's constructor would turn an empty `BaseUrl` into `new Uri("/")`, which throws a `UriFormatException` that names no setting. An empty `Deployment` would only fail when the first query reached Azure. The `azure-openai` factory checks both first. This check is private to `Program.cs`.
- **`TenantId` is left out of `appsettings.json`.** Task 7's `AzureCredentialFactory` refuses the Azure CLI credential when `AzureOpenAi:TenantId` is null, empty or whitespace, naming the setting. Left out of the file, it stays `null`, so a run that lists `azure-openai` with `AzureCli` fails startup until that run's own configuration pins the tenant, which is the intended "pin it or fail" behaviour. A run that does not list `azure-openai` never builds the credential, so it is unaffected. A blank `"TenantId": ""` in the file would fail the same way and only look like a setting. The startup tests that use the CLI credential set a GUID-shaped `AzureOpenAi:TenantId`.
- **The exception comes out unwrapped.** An exception thrown in `Program`'s top-level code after `builder.Build()` reaches `WebApplicationFactory<Program>.Services` as itself. The tests catch it with `Assert.Throws<InvalidOperationException>`.

`appsettings.Testing.json` needs no change. It points Anthropic and OpenAI at `http://127.0.0.1:9/`, and the default chain is still those two, so the existing API tests keep their degraded-path behaviour. Azure is not in the default chain, so the test environment needs no Azure settings.

Call sites: `grep -rn "FallbackChatProvider(\|AddHttpClient<\|IChatProvider>" src tests --include=*.cs` finds the one `FallbackChatProvider` construction in `Program.cs` (replaced here), the tests in `tests/ReleaseLens.Llm.Tests` that build their own, and the `QueryEndpointTests` cases that swap `IChatProvider` for `SpyChatProvider` or `ScriptedChatProvider` with `RemoveAll<IChatProvider>()`. Those cases still work. They replace the chain that answers, and the startup check still resolves `SelectedChatProviders`, which is built from the real Anthropic and OpenAI clients. Nothing else in `src` or `tests` reads `AZURE_CLIENT_ID` or registers a `TimeProvider` in the API host.

**Files:**
- Modify: `src/ReleaseLens.Api/Program.cs`. Three edits: the file's start, from the `using` directives through the `IChatProvider` registration (lines 1–58 today); a block inserted after `var app = builder.Build();`; and a record appended after `public partial class Program;`
- Modify: `src/ReleaseLens.Api/appsettings.json` (whole file; the `AzureOpenAi` section is new, and there is no `Chat` section)
- Create: `tests/ReleaseLens.Api.Tests/StartupConfigurationTests.cs`
- Modify: `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs` (two `using` directives; a nested credential class and one test inserted before `Swagger_IsServed`)
- Test: `tests/ReleaseLens.Api.Tests/StartupConfigurationTests.cs` (the startup half of T-C3 and T-P3a, and Review Focus 2 through real configuration)
- Test: `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs` (T-A1 through real configuration, and T-P3 end to end through `/query`)

**Interfaces:**
- Consumes:
  - Task 4: `public sealed record PricingIdentity(string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false);` `public interface IPricedChatProvider : IChatProvider { PricingIdentity Pricing { get; } }` `public static void EnsurePriced(IEnumerable<PricingIdentity> identities, DateOnly asOf); // throws InvalidOperationException`. `OpenAiOptions` gains `bool Unpriced` (default `false`). `AnthropicChatProvider` → `("anthropic", options.Model)`; `OpenAiChatProvider` → `("openai", options.Model, Unpriced: options.Unpriced)`.
  - Task 5: `public sealed class ChatOptions { public const string SectionName = "Chat"; public List<string> Providers { get; set; } = ["anthropic", "openai"]; }` and `public static IReadOnlyList<IChatProvider> Select(IReadOnlyList<string> names, IReadOnlyDictionary<string, Func<IChatProvider>> factories);`. `FallbackChatProvider(IReadOnlyList<IChatProvider> providers, ILogger<FallbackChatProvider> logger)` is unchanged.
  - Task 7: `public enum AzureCredentialKind { AzureCli, ManagedIdentity }`; `AzureOpenAiOptions` with `SectionName = "AzureOpenAi"`, `BaseUrl`, `Deployment`, `Model = "gpt-4.1-mini"`, `ModelVersion = "2025-04-14"`, `DeploymentType = "Standard"`, `TokenScope = "https://ai.azure.com/.default"`, `Credential = AzureCredentialKind.AzureCli`, `TenantId`, `ClientId`; `public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)`; `public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler`; `public static TokenCredential Create(AzureOpenAiOptions options); // throws InvalidOperationException on ManagedIdentity with no ClientId (naming AZURE_CLIENT_ID) and on AzureCli with no TenantId (naming AzureOpenAi:TenantId)`
  - Tasks 8–9: `public sealed class AzureOpenAiChatProvider(HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger) : IPricedChatProvider`, `Name => "azure-openai"`, `Pricing` = `("azure-openai", Model, ModelVersion, DeploymentType)`
- Produces (nothing later in plan 1 depends on it; plans 2 and 3 configure it):
  - Configuration keys the app reads: `Chat:Providers` (ordered list of `anthropic`, `openai`, `azure-openai`); `AzureOpenAi:BaseUrl`, `AzureOpenAi:Deployment`, `AzureOpenAi:Model`, `AzureOpenAi:ModelVersion`, `AzureOpenAi:DeploymentType`, `AzureOpenAi:TokenScope`, `AzureOpenAi:Credential`, `AzureOpenAi:TenantId`; and the environment variable `AZURE_CLIENT_ID`, which always overwrites `AzureOpenAi:ClientId`
  - Startup fails with `InvalidOperationException` on an unknown, duplicated or empty provider list; on a selected priced provider with no rate; when `azure-openai` is listed but `AzureOpenAi:BaseUrl` or `AzureOpenAi:Deployment` is empty; when `azure-openai` is listed with `ManagedIdentity` and no `AZURE_CLIENT_ID`; and when `azure-openai` is listed with `AzureCli` and no `AzureOpenAi:TenantId`
  - `internal sealed record SelectedChatProviders(IReadOnlyList<IChatProvider> Providers)` in `Program.cs`, private to the API

- [ ] **Step 1: Write the failing tests**

**1a.** Create `tests/ReleaseLens.Api.Tests/StartupConfigurationTests.cs`. It uses no new type, so it compiles against the tree as Task 9 leaves it.

```csharp
using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Configurations the app must refuse to start with, and the ones next to them that it must
/// accept. Each refused one would otherwise start cleanly and fail, or answer from the wrong
/// chain, only when the first query arrives.
/// </summary>
/// <remarks>
/// Nothing here touches the database, but the class still joins the Postgres collection:
/// Program reads RELEASELENS_DB and the provider keys from the process environment, which
/// every class in that collection sets, and AZURE_CLIENT_ID is cleared here, so running
/// beside them would race.
/// </remarks>
[Collection(nameof(PostgresCollection))]
public class StartupConfigurationTests(PostgresFixture fixture) : IAsyncLifetime
{
    private string? _clientIdBefore;

    public ValueTask InitializeAsync()
    {
        Environment.SetEnvironmentVariable("RELEASELENS_DB", fixture.ConnectionString);
        Environment.SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-test");
        Environment.SetEnvironmentVariable("OPENAI_API_KEY", "sk-test");

        // A developer machine may have AZURE_CLIENT_ID set for something else; the managed
        // identity cases below need it absent.
        _clientIdBefore = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
        Environment.SetEnvironmentVariable("AZURE_CLIENT_ID", null);

        return ValueTask.CompletedTask;
    }

    public ValueTask DisposeAsync()
    {
        Environment.SetEnvironmentVariable("AZURE_CLIENT_ID", _clientIdBefore);
        return ValueTask.CompletedTask;
    }

    // UseSetting is already visible to the options Program reads before Build. appsettings.json
    // has no Chat:Providers, so a case's list is exactly the indexes it sets, and a case that
    // sets none runs ChatOptions' default (anthropic, openai). The cases that name two providers
    // set index 0 and index 1 because each index is its own key. The Azure cases put anthropic
    // after azure-openai, so each check is made with Azure beside another priced provider.
    private static WebApplicationFactory<Program> Factory(IReadOnlyDictionary<string, string> settings) =>
        new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
        {
            builder.UseEnvironment("Testing");

            foreach (var (key, value) in settings)
            {
                builder.UseSetting(key, value);
            }
        });

    // Reading Services starts the host, which runs Program to its end, startup checks included.
    private static void Start(WebApplicationFactory<Program> factory) => _ = factory.Services;

    private static Dictionary<string, string> AzureConfigured(string first, string second) => new()
    {
        ["Chat:Providers:0"] = first,
        ["Chat:Providers:1"] = second,
        ["AzureOpenAi:BaseUrl"] = "http://127.0.0.1:9/openai/v1/",
        ["AzureOpenAi:Deployment"] = "gpt-4.1-mini-test",

        // The Azure CLI credential (the default) is refused without a pinned tenant.
        ["AzureOpenAi:TenantId"] = "00000000-0000-0000-0000-000000000002"
    };

    [Fact]
    public async Task Chat_UnknownProviderName_FailsStartup_NamingTheValidProviders()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["Chat:Providers:0"] = "anthropc",
            ["Chat:Providers:1"] = "openai"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("'anthropc'", exception.Message, StringComparison.Ordinal);

        // The list comes from the keys Program registers, so this also pins that all three
        // providers are wired.
        Assert.Contains("Valid providers: anthropic, azure-openai, openai.", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OpenAi_ModelWithNoRate_FailsStartup()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["OpenAi:Model"] = "gpt-no-rate-test"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OpenAi_ModelWithNoRate_MarkedUnpriced_Starts()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["OpenAi:Model"] = "gpt-no-rate-test",
            ["OpenAi:Unpriced"] = "true"
        });

        Start(factory);
    }

    [Fact]
    public async Task Azure_NotListed_NeverBuildsACredential()
    {
        // With no client ID, building this credential throws. The app starting proves that a
        // chain without azure-openai never builds it.
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["AzureOpenAi:Credential"] = "ManagedIdentity"
        });

        Start(factory);
    }

    [Fact]
    public async Task Azure_Listed_WithManagedIdentityAndNoClientId_FailsStartup()
    {
        // The same settings with the Azure CLI credential start (the last test), so the
        // credential is what fails here.
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings["AzureOpenAi:Credential"] = "ManagedIdentity";

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AZURE_CLIENT_ID", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithTheAzureCliCredentialAndNoTenantId_FailsStartup_NamingTheSetting()
    {
        // The same settings with a tenant start (the last test), so the missing tenant is what
        // fails here.
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings.Remove("AzureOpenAi:TenantId");

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AzureOpenAi:TenantId", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithoutBaseUrlOrDeployment_FailsStartup_NamingBoth()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["Chat:Providers:0"] = "azure-openai",
            ["Chat:Providers:1"] = "anthropic"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AzureOpenAi:BaseUrl", exception.Message, StringComparison.Ordinal);
        Assert.Contains("AzureOpenAi:Deployment", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithADeploymentTypeThatHasNoRate_FailsStartup()
    {
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings["AzureOpenAi:DeploymentType"] = "GlobalStandard";

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("GlobalStandard", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithTheAzureCliCredential_Starts()
    {
        // AzureCliCredential runs az only when asked for a token, and nothing asks at startup.
        await using var factory = Factory(AzureConfigured("azure-openai", "anthropic"));

        Start(factory);
    }
}
```

**1b.** In `tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs`, add these two directives directly after `using System.Threading.Tasks;`:

```csharp
using Azure.Core;
using Azure.Identity;
```

Both namespaces reach the test project through `ReleaseLens.Api`'s reference to `ReleaseLens.Llm`, which references `Azure.Identity` (Task 7). Then insert the members below directly before the `[Fact]` line of `Swagger_IsServed`, after the members Tasks 5 and 6 added there. Like 1a, they use no new type, so they compile against the tree as Task 9 leaves it. The test replaces only the `TokenCredential` singleton, so the chain's one provider is the real `AzureOpenAiChatProvider`, with the real `EntraTokenHandler` and `EntraTokenCache`. The credential throws `CredentialUnavailableException`; the handler rethrows it as `HttpRequestException`, `ProviderHttp` turns that into `ProviderUnavailableException`, and with nothing after `azure-openai` in the chain the query degrades. The reason names every provider tried, so `azure-openai.` alone proves that nothing from `ChatOptions`' default or a file entry was added. `QueryAgent` writes it as `All providers unavailable: {names}. Returning retrieved evidence without synthesis.`, and a degraded answer lists no providers.

```csharp
    private sealed class UnavailableCredential : TokenCredential
    {
        public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => throw new CredentialUnavailableException("No credential in tests.");

        public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => throw new CredentialUnavailableException("No credential in tests.");
    }

    /// <summary>
    /// T-A1 through real configuration and T-P3 end to end. Chat:Providers:0 alone gives a
    /// one-provider chain, with nothing inherited from a default or a file entry, and a
    /// credential that cannot produce a token degrades the answer instead of failing it.
    /// </summary>
    [Fact]
    public async Task Query_OnlyAzureConfigured_CredentialFails_DegradesNamingOnlyAzure()
    {
        await using var hosted = _factory.WithWebHostBuilder(builder =>
        {
            builder.UseSetting("Chat:Providers:0", "azure-openai");
            builder.UseSetting("AzureOpenAi:BaseUrl", "http://127.0.0.1:9/openai/v1/");
            builder.UseSetting("AzureOpenAi:Deployment", "gpt-4.1-mini-test");
            builder.UseSetting("AzureOpenAi:TenantId", "00000000-0000-0000-0000-000000000002");
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<TokenCredential>();
                services.AddSingleton<TokenCredential>(new UnavailableCredential());
            });
        });

        using var client = hosted.CreateClient();
        var response = await client.SendAsync(Query("planner", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var metadata = (await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken))
            .GetProperty("metadata");
        Assert.True(metadata.GetProperty("degraded").GetBoolean());
        Assert.StartsWith("All providers unavailable: azure-openai. ",
            metadata.GetProperty("degradedReason").GetString(), StringComparison.Ordinal);
        Assert.Empty(metadata.GetProperty("providers").EnumerateArray());
    }
```

- [ ] **Step 2: Run them and watch them fail**

```
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~StartupConfigurationTests"
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~QueryEndpointTests.Query_OnlyAzureConfigured_CredentialFails_DegradesNamingOnlyAzure"
```

Expected from the first: it builds, then `Failed: 6, Passed: 3`. Before this task `Program.cs` ignores `Chat:Providers` and `AzureOpenAi`, and checks no rate. So the six `..._FailsStartup...` tests fail with `Assert.Throws() Failure: No exception was thrown`. The other three already pass: the two `..._Starts` tests and `Azure_NotListed_NeverBuildsACredential`. They are guards for Step 3. For example, binding `ChatOptions` directly would add `Chat:Providers:0`/`:1` to the default list. `Azure_Listed_WithTheAzureCliCredential_Starts` would then see anthropic, openai, azure-openai, anthropic and fail on the duplicate name. The default configuration sets no `Chat:Providers`, so it would still start.

Expected from the second: `Failed: 1`, at `Assert.StartsWith`. Before Step 3 the chain is still the hard-coded `anthropic, openai`, both pointed at `http://127.0.0.1:9/` by `appsettings.Testing.json`, so the answer degrades with the reason `All providers unavailable: anthropic, openai. ...`.

Both classes are in the Postgres Testcontainers collection, so this step **needs Docker**. Without Docker, all nine `StartupConfigurationTests` and the new `QueryEndpointTests` test fail with `Collection fixture type 'ReleaseLens.Storage.Tests.PostgresFixture' threw in its constructor`.

- [ ] **Step 3: Implement**

**3a.** In `src/ReleaseLens.Api/Program.cs`, replace everything from the first line through the `IChatProvider` registration with the block below. Today that is lines 1–58. The replaced text ends with:

```csharp
builder.Services.AddSingleton<IChatProvider>(sp => new FallbackChatProvider(
    [sp.GetRequiredService<AnthropicChatProvider>(), sp.GetRequiredService<OpenAiChatProvider>()],
    sp.GetRequiredService<ILogger<FallbackChatProvider>>()));
```

The `builder.Services.AddSingleton(sp => new ToolRegistry(` line that follows it stays as it is.

The new block:

```csharp
using System.Diagnostics;
using System.Text.Json;
using Azure.Core;
using Microsoft.AspNetCore.Diagnostics;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using ReleaseLens.Api;
using ReleaseLens.Api.Auth;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Core.Telemetry;
using ReleaseLens.Embedding;
using ReleaseLens.Llm.Agent;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using ReleaseLens.Llm.Tools;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;

var builder = WebApplication.CreateBuilder(args);

var connectionString = Environment.GetEnvironmentVariable("RELEASELENS_DB")
    ?? throw new InvalidOperationException("RELEASELENS_DB is not set.");

var anthropicOptions = builder.Configuration.GetSection(AnthropicOptions.SectionName).Get<AnthropicOptions>()
    ?? new AnthropicOptions();
anthropicOptions.ApiKey = Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY") ?? string.Empty;

var openAiOptions = builder.Configuration.GetSection(OpenAiOptions.SectionName).Get<OpenAiOptions>()
    ?? new OpenAiOptions();
openAiOptions.ApiKey = Environment.GetEnvironmentVariable("OPENAI_API_KEY") ?? string.Empty;

var azureOpenAiOptions = builder.Configuration.GetSection(AzureOpenAiOptions.SectionName).Get<AzureOpenAiOptions>()
    ?? new AzureOpenAiOptions();
azureOpenAiOptions.ClientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");

// Binding into ChatOptions would add the configured names to its default list rather than
// replace it, so ["anthropic", "openai"] would come out with both names twice. The list is
// read on its own, and the default applies only when nothing configures it.
var chatOptions = new ChatOptions();
chatOptions.Providers = builder.Configuration
    .GetSection($"{ChatOptions.SectionName}:{nameof(ChatOptions.Providers)}")
    .Get<List<string>>() ?? chatOptions.Providers;

var agentOptions = builder.Configuration.GetSection(AgentOptions.SectionName).Get<AgentOptions>() ?? new AgentOptions();
var embedderOptions = builder.Configuration.GetSection(EmbedderOptions.SectionName).Get<EmbedderOptions>()
    ?? new EmbedderOptions();

builder.Services.AddSingleton(anthropicOptions);
builder.Services.AddSingleton(openAiOptions);
builder.Services.AddSingleton(azureOpenAiOptions);
builder.Services.AddSingleton(chatOptions);
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton(agentOptions);
builder.Services.AddSingleton(embedderOptions);

builder.Services.AddSingleton(new TenantConnectionFactory(connectionString));
builder.Services.AddSingleton<TenantRepository>();
builder.Services.AddSingleton<ApiKeyRepository>();
builder.Services.AddSingleton<ApiKeyAuthenticator>();
builder.Services.AddSingleton<TokenUsageRepository>();
builder.Services.AddSingleton<EvidenceRepository>();
builder.Services.AddSingleton<EvidenceQueries>();
builder.Services.AddSingleton<HybridRetriever>();
builder.Services.AddSingleton<IEmbedder>(sp => new OnnxEmbedder(sp.GetRequiredService<EmbedderOptions>()));

builder.Services.AddHttpClient<AnthropicChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));
builder.Services.AddHttpClient<OpenAiChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));

// Nothing resolves the credential or the token cache except the Azure client's handler, and
// nothing resolves that client unless Chat:Providers lists azure-openai, so a chain without
// Azure never builds a credential.
builder.Services.AddSingleton<TokenCredential>(_ => AzureCredentialFactory.Create(azureOpenAiOptions));
builder.Services.AddSingleton(sp => new EntraTokenCache(
    sp.GetRequiredService<TokenCredential>(), azureOpenAiOptions.TokenScope, sp.GetRequiredService<TimeProvider>()));
builder.Services.AddHttpClient<AzureOpenAiChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2))
    .AddHttpMessageHandler(sp => new EntraTokenHandler(sp.GetRequiredService<EntraTokenCache>()));

builder.Services.AddSingleton(sp => new SelectedChatProviders(ChatProviderSelection.Select(
    chatOptions.Providers,
    new Dictionary<string, Func<IChatProvider>>
    {
        ["anthropic"] = () => sp.GetRequiredService<AnthropicChatProvider>(),
        ["openai"] = () => sp.GetRequiredService<OpenAiChatProvider>(),
        ["azure-openai"] = () =>
        {
            // Checked here rather than left to the provider: an empty BaseUrl would surface as
            // a UriFormatException that names no setting, and an empty Deployment only when
            // the first query reached Azure.
            var empty = new[]
                {
                    (Name: nameof(AzureOpenAiOptions.BaseUrl), Value: azureOpenAiOptions.BaseUrl),
                    (Name: nameof(AzureOpenAiOptions.Deployment), Value: azureOpenAiOptions.Deployment)
                }
                .Where(setting => string.IsNullOrWhiteSpace(setting.Value))
                .Select(setting => $"{AzureOpenAiOptions.SectionName}:{setting.Name}")
                .ToList();

            if (empty.Count > 0)
            {
                throw new InvalidOperationException(
                    $"Chat:Providers lists azure-openai, but these settings are empty: {string.Join(", ", empty)}.");
            }

            return sp.GetRequiredService<AzureOpenAiChatProvider>();
        }
    })));

builder.Services.AddSingleton<IChatProvider>(sp => new FallbackChatProvider(
    sp.GetRequiredService<SelectedChatProviders>().Providers,
    sp.GetRequiredService<ILogger<FallbackChatProvider>>()));
```

What changes, compared with the replaced lines:
- two `using` directives: `Azure.Core` for `TokenCredential`, and `ReleaseLens.Llm.Providers.Azure`
- `azureOpenAiOptions` and `chatOptions`
- three singletons: those two options objects, and `TimeProvider.System`
- the credential, the token cache and the Azure typed client
- `SelectedChatProviders`
- the `FallbackChatProvider` registration, now built over `SelectedChatProviders`

Everything else in the block is unchanged.

**3b.** In the same file, replace:

```csharp
var app = builder.Build();

app.MapOpenApi();
```

with:

```csharp
var app = builder.Build();

// Building the chain here rather than on the first query is what turns a misnamed provider, an
// incomplete AzureOpenAi section or a model with no rate into a failed startup.
ModelPricing.EnsurePriced(
    app.Services.GetRequiredService<SelectedChatProviders>().Providers
        .OfType<IPricedChatProvider>()
        .Select(provider => provider.Pricing),
    DateOnly.FromDateTime(app.Services.GetRequiredService<TimeProvider>().GetUtcNow().UtcDateTime));

app.MapOpenApi();
```

**3c.** At the end of the same file, replace:

```csharp
/// <summary>Exposed so WebApplicationFactory&lt;Program&gt; can host the app in tests.</summary>
public partial class Program;
```

with:

```csharp
/// <summary>Exposed so WebApplicationFactory&lt;Program&gt; can host the app in tests.</summary>
public partial class Program;

/// <summary>
/// The providers Chat:Providers names, in order: the fallback chain, and the set startup
/// checks for a rate. One instance, so the chain that answers is the one that was checked.
/// </summary>
internal sealed record SelectedChatProviders(IReadOnlyList<IChatProvider> Providers);
```

**3d.** Replace `src/ReleaseLens.Api/appsettings.json` with the file below. Compared with the file Task 1 leaves, it gains the `AzureOpenAi` section; every other line is unchanged. It has no `Chat` section, for the reason given at the top of this task. `BaseUrl` and `Deployment` are empty on purpose, because each deployment supplies its own. `TenantId` is left out, for the reason given at the top of this task. The other `AzureOpenAi` values repeat `AzureOpenAiOptions`' defaults, so the file shows what the app runs with.

```json
{
  "Logging": { "LogLevel": { "Default": "Information", "Microsoft.AspNetCore": "Warning" } },
  "AllowedHosts": "*",
  "Anthropic": { "Model": "claude-sonnet-5", "BaseUrl": "https://api.anthropic.com/", "MaxTokens": 2048 },
  "OpenAi": { "Model": "gpt-4o", "BaseUrl": "https://api.openai.com/v1/", "MaxTokens": 2048 },
  "AzureOpenAi": {
    "BaseUrl": "",
    "Deployment": "",
    "Model": "gpt-4.1-mini",
    "ModelVersion": "2025-04-14",
    "DeploymentType": "Standard",
    "TokenScope": "https://ai.azure.com/.default",
    "Credential": "AzureCli"
  },
  "Agent": { "MaxIterations": 6, "MaxTokens": 2048, "SeedRetrievalK": 8 },
  "Embedding": { "MaxSequenceLength": 512, "BatchSize": 32 }
}
```

- [ ] **Step 4: Run them and watch them pass**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~StartupConfigurationTests"
dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~QueryEndpointTests.Query_OnlyAzureConfigured_CredentialFails_DegradesNamingOnlyAzure"
```

Expected: the build reports `Build succeeded.`, `0 Warning(s)` and `0 Error(s)`. The first test run reports `Passed!  - Failed:     0, Passed:     9`, and the second `Passed!  - Failed:     0, Passed:     1`. Both **need Docker**; where Docker is absent they run in CI on the pull request, and the task is not complete until they have run green somewhere.

When this task was drafted, eight of these tests (all but the `TenantId` one, which Task 7's tenant guard brought in later) ran green outside the Postgres collection, against a scratch copy with the same `RELEASELENS_DB` handling but a dummy connection string. That copy had:
- the real Task 4 and Task 5 code, and Task 7's Azure files, taken from their task text
- a stand-in for `AzureOpenAiChatProvider` with Task 8's constructor
- `Azure.Identity` 1.21.0 with Task 7's pin bump

They also passed under the `Development` environment, where the host validates the container on build. They were not run inside the Postgres collection, because Docker was not available. The `QueryEndpointTests` test was compiled but not run, for the same reason: it needs the database for its tenant and API key.

- [ ] **Step 5: Run the whole affected test projects**

```
dotnet build ReleaseLens.sln
dotnet test tests/ReleaseLens.Api.Tests
dotnet test ReleaseLens.sln
```

Expected: `0 Warning(s)`, and `Failed: 0` in every project. This is plan 1's last task, so the last command runs the whole suite once.

The existing API tests already prove that the app starts with the default configuration. `QueryEndpointTests.Health_IsAnonymous`, like every test in `QueryEndpointTests` and `EvidenceEndpointTests` except `Query_OnlyAzureConfigured_CredentialFails_DegradesNamingOnlyAzure` (which sets `Chat:Providers:0` and `AzureOpenAi`), boots `Program` with `appsettings.json` and `appsettings.Testing.json` unmodified. After this task, that boot runs `ChatProviderSelection.Select` over `["anthropic", "openai"]` and `EnsurePriced` over `("anthropic", "claude-sonnet-5")` and `("openai", "gpt-4o")`. `Query_WhenBothProvidersAreUnreachable_Returns200Degraded` still shows the chain degrading when both providers are unreachable. So no separate "default configuration starts" test is added.

**Docker:**
- `ReleaseLens.Api.Tests` needs it: `QueryEndpointTests`, `EvidenceEndpointTests` and the new `StartupConfigurationTests` are all in `PostgresCollection`.
- `dotnet test ReleaseLens.sln` needs it too, for the Postgres-backed tests in `ReleaseLens.Llm.Tests`, `ReleaseLens.Storage.Tests` and `ReleaseLens.Ingestion.Tests`.
- Without Docker, only `GoldenSetValidationTests` in the API project can run: `dotnet test tests/ReleaseLens.Api.Tests --filter "FullyQualifiedName~GoldenSetValidationTests"`. Say in the task report that the Postgres-backed tests were not run.

- [ ] **Step 6: Commit**

Plain `git commit`: the repository's own identity is already `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, so pass no `--author` and run no `git config`. Do not push.

```
git add src/ReleaseLens.Api/Program.cs src/ReleaseLens.Api/appsettings.json tests/ReleaseLens.Api.Tests/StartupConfigurationTests.cs tests/ReleaseLens.Api.Tests/QueryEndpointTests.cs
git commit -F- <<'EOF'
feat(api): build the chat chain from Chat:Providers, with Azure OpenAI

Program.cs no longer hard-codes Anthropic then OpenAI. Chat:Providers
names the chain in order; anthropic, openai and azure-openai are the
valid names. The Azure provider's typed client carries EntraTokenHandler.
Its token cache and credential are singletons that are built only when
azure-openai is listed. AzureOpenAi:ClientId always comes from
AZURE_CLIENT_ID.

The chain is built at startup, and every selected provider's pricing
identity must have a rate. So the app refuses to start on:
- an unknown or duplicated provider name
- a priced model with no rate
- azure-openai listed with an empty BaseUrl or Deployment
- azure-openai listed with a managed identity and no client ID
- azure-openai listed with the Azure CLI credential and no tenant

A /query test sets Chat:Providers:0=azure-openai alone and a credential
that cannot produce a token: the chain is azure-openai only, and the
answer degrades instead of failing (T-A1 through configuration, T-P3 end
to end).

Chat:Providers is read on its own: binding it into ChatOptions would add
the configured names to the default list and fail on the duplicates. It
is not written into appsettings.json, because configuration arrays
overlay index by index and a two-entry file list would make a one-entry
list impossible to set from the environment; with no entry, ChatOptions'
default gives the same local chain.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

(In PowerShell, use `git commit -m @'` … `'@` with the same message, with the closing `'@` at column 0.)

**Note for plans 2 and 3.** `Chat:Providers` lives only in environment-specific configuration; set every index you need. For the deployed app that is `Chat__Providers__0=azure-openai` alone, and for each evaluation arm its one provider. With nothing set, the chain is `ChatOptions`' default, `anthropic` then `openai`.

`openai gpt-4.1-mini` has no rate in `ModelPricing`, so an arm-O process (`Chat__Providers__0=openai`, `OpenAi__Model=gpt-4.1-mini`) fails `EnsurePriced` at startup. That is intended. Before the dry run, OpenAI's input, cached-input and output rates for gpt-4.1-mini are read from OpenAI's pricing page, dated (spec §4.6, §11), and added as one line in `ResolveOpenAi` with a comment giving the date. Its cached-input rate is the one on that same page, not the input rate repeated, so arm O is billed at its own cached rate.

The Azure rate is not a configuration key. It lives in one place, the `azure-openai` arm of `ModelPricing.Resolve` in `src/ReleaseLens.Llm/Providers/ModelPricing.cs` (USD per 1M tokens: 0.44 input, 0.11 cached input, 1.76 output), and `AzureOpenAi:Model`, `AzureOpenAi:ModelVersion` and `AzureOpenAi:DeploymentType` select it. Spec §4.6 places rates in ModelPricing, and §6.1 asks the smoke check to read the rate "from the app's configuration rather than a second copy". How the check reads it without a second copy is plan 2's decision, to be put to the owner. From Task 4, `metadata.tokensIn` is uncached input and `metadata.cacheReadInputTokens` is cached input (no cache writes on the OpenAI wire), so the recomputed cost is `(tokensIn * input + cacheReadInputTokens * cachedInput + tokensOut * output) / 1e6`, compared to six decimal places. The deployed app needs `Chat__Providers__0=azure-openai`, `AzureOpenAi__Credential=ManagedIdentity`, a non-empty `AZURE_CLIENT_ID`, `AzureOpenAi__BaseUrl` and `AzureOpenAi__Deployment`. The default credential, AzureCli, refuses to start without `AzureOpenAi:TenantId`.

---
