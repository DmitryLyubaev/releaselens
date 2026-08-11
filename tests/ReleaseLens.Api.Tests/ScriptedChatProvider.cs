using ReleaseLens.Llm.Providers;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Returns one fixed answer, so an end-to-end test can pin what the model "said" and assert
/// on what the endpoint does with it. The other API tests deliberately let both real providers
/// fail and exercise the degraded path; a test about citation markers cannot, because the
/// degraded answer is not a synthesised one and cites everything its evidence block shows.
/// </summary>
public sealed class ScriptedChatProvider(string answer) : IChatProvider
{
    public string Name => "scripted";

    public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        => Task.FromResult(new ChatResponse(
            answer, [], new TokenUsage(10, 5, 0, 0), "end_turn", "claude-sonnet-5", Name));
}
