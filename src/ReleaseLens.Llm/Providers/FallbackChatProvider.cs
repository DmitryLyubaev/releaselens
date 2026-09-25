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

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var attempted = new List<string>(_providers.Count);

        foreach (var provider in _providers)
        {
            attempted.Add(provider.Name);

            try
            {
                return await provider.CompleteAsync(request, cancellationToken);
            }
            catch (ProviderUnavailableException unavailable)
            {
                _logger.LogWarning(unavailable,
                    "Provider {Provider} unavailable; falling through", provider.Name);
            }
        }

        throw new AllProvidersUnavailableException(attempted);
    }
}
