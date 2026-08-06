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
    IReadOnlyList<ToolResult>? Results)
{
    public static ChatMessage User(string text) => new(ChatRole.User, text, null, null);
    public static ChatMessage Assistant(string text) => new(ChatRole.Assistant, text, null, null);
    public static ChatMessage AssistantToolCalls(IReadOnlyList<ToolCall> calls) => new(ChatRole.Assistant, null, calls, null);

    /// <summary>Tool results are sent back in the user turn for Anthropic and as tool-role messages for OpenAI.</summary>
    public static ChatMessage ToolResults(IReadOnlyList<ToolResult> results) => new(ChatRole.User, null, null, results);
}

public sealed record ChatRequest(
    string SystemPrompt,
    IReadOnlyList<ChatMessage> Messages,
    IReadOnlyList<ToolDefinition> Tools,
    string Model,
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

public sealed record ChatResponse(
    string? Text,
    IReadOnlyList<ToolCall> ToolCalls,
    TokenUsage Usage,
    string StopReason,
    string Model,
    string Provider);

/// <summary>
/// Thrown when a provider cannot serve the request — network failure, 5xx, or 429.
/// The fallback chain (Task 14) catches exactly this and nothing else.
/// </summary>
public sealed class ProviderUnavailableException(string providerName, string message, Exception? inner = null)
    : Exception(message, inner)
{
    public string ProviderName { get; } = providerName;
}
