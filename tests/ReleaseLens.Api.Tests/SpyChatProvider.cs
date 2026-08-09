using ReleaseLens.Llm.Providers;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Records whether the agent ever reached a provider. The budget test needs this because
/// neither the HTTP status nor the usage row can distinguish "refused before spending" from
/// "spent, then refused" — a failed provider call records nothing either way.
/// </summary>
public sealed class SpyChatProvider : IChatProvider
{
    private int _calls;

    public string Name => "spy";
    public int Calls => Volatile.Read(ref _calls);

    public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        Interlocked.Increment(ref _calls);

        // Behaves like a dead provider, so if the endpoint ever does call it the request
        // degrades rather than hanging — the count is what the assertion reads.
        throw new ProviderUnavailableException(Name, "spy provider never answers");
    }
}
