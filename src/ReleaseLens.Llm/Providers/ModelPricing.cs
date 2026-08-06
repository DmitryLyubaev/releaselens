namespace ReleaseLens.Llm.Providers;

/// <summary>
/// USD per million tokens. Sonnet 5 introductory pricing runs to 31 August 2026
/// inclusive, then reverts — so cost attribution has to be date-aware or every
/// figure in the eval report goes wrong on 1 September.
/// </summary>
public static class ModelPricing
{
    private static readonly DateOnly SonnetIntroductoryEnds = new(2026, 8, 31);

    private sealed record Rates(decimal InputPerMillion, decimal OutputPerMillion)
    {
        /// <summary>Cache reads are billed at a tenth of fresh input; cache writes at 1.25x.</summary>
        public decimal CacheReadPerMillion => InputPerMillion * 0.1m;
        public decimal CacheWritePerMillion => InputPerMillion * 1.25m;
    }

    public static decimal CostUsd(string model, TokenUsage usage, DateOnly asOf)
    {
        var rates = Resolve(model, asOf);
        if (rates is null)
        {
            return 0m;
        }

        return ((usage.InputTokens * rates.InputPerMillion)
              + (usage.OutputTokens * rates.OutputPerMillion)
              + (usage.CacheReadInputTokens * rates.CacheReadPerMillion)
              + (usage.CacheCreationInputTokens * rates.CacheWritePerMillion)) / 1_000_000m;
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

    private static Rates? Resolve(string model, DateOnly asOf) => model switch
    {
        "claude-opus-5" => new Rates(15m, 75m),
        "claude-sonnet-5" => asOf <= SonnetIntroductoryEnds ? new Rates(2m, 10m) : new Rates(3m, 15m),
        "claude-haiku-4-5-20251001" => new Rates(1m, 5m),
        "claude-fable-5" => new Rates(1m, 5m),
        "gpt-4o" => new Rates(2.5m, 10m),
        "gpt-4o-mini" => new Rates(0.15m, 0.6m),
        _ => null // local models via Ollama/vLLM cost nothing per token
    };
}
