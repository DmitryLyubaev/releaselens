namespace ReleaseLens.Llm.Providers;

public interface IChatProvider
{
    /// <summary>Stable lowercase identifier, recorded on every span and usage row.</summary>
    string Name { get; }

    Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken);
}
