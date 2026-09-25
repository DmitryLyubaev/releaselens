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
