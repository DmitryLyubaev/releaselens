using System.Text.Json;

namespace ReleaseLens.Llm.Providers;

public enum ChatRole { User, Assistant }

public sealed record ToolDefinition(string Name, string Description, JsonElement JsonSchema);

public sealed record ToolCall(string Id, string Name, JsonElement Arguments);

public sealed record ToolResult(string ToolCallId, string Content, bool IsError);

public sealed record ChatMessage(
    ChatRole Role,
    string? Text,
    IReadOnlyList<ToolCall>? ToolCalls,
    IReadOnlyList<ToolResult>? ToolResults)
{
    public static ChatMessage User(string text) => new(ChatRole.User, text, null, null);
    public static ChatMessage Assistant(string text) => new(ChatRole.Assistant, text, null, null);
    public static ChatMessage AssistantToolCalls(IReadOnlyList<ToolCall> calls) => new(ChatRole.Assistant, null, calls, null);

    /// <summary>
    /// Tool results are sent back in the user turn for Anthropic and as tool-role messages
    /// for OpenAI. Named for the turn it produces, parallel to <see cref="AssistantToolCalls"/>
    /// — and it cannot simply be called ToolResults, because a record's positional property
    /// of that name already occupies the identifier (CS0102).
    /// </summary>
    public static ChatMessage UserToolResults(IReadOnlyList<ToolResult> results) => new(ChatRole.User, null, null, results);
}

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

public sealed record TokenUsage(
    int InputTokens,
    int OutputTokens,
    int CacheReadInputTokens,
    int CacheCreationInputTokens)
{
    public static TokenUsage Zero { get; } = new(0, 0, 0, 0);

    public static TokenUsage operator +(TokenUsage a, TokenUsage b) => new(
        a.InputTokens + b.InputTokens,
        a.OutputTokens + b.OutputTokens,
        a.CacheReadInputTokens + b.CacheReadInputTokens,
        a.CacheCreationInputTokens + b.CacheCreationInputTokens);

    public int Total => InputTokens + OutputTokens + CacheReadInputTokens + CacheCreationInputTokens;
}

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

/// <summary>
/// Thrown when a provider cannot serve the request — network failure, 5xx, or 429.
/// The fallback chain (Task 14) catches exactly this and nothing else.
/// </summary>
public sealed class ProviderUnavailableException(string providerName, string message, Exception? inner = null)
    : Exception(message, inner)
{
    public string ProviderName { get; } = providerName;
}

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
