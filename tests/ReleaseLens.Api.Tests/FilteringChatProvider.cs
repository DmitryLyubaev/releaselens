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
