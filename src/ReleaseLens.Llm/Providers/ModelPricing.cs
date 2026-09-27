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

        // Azure Retail Prices API, australiaeast, USD per 1M tokens for input, cached input and
        // output. The OpenAI wire reports no cache writes, so none is charged. Global Standard
        // (read 2026-09-27) is the deployment type the app uses; regional Standard (read
        // 2026-09-24) stays for a subscription that has regional quota.
        { Provider: "azure-openai", Model: "gpt-4.1-mini", Version: "2025-04-14", DeploymentType: "GlobalStandard" }
            => new Rates(0.40m, 0.10m, 0m, 1.60m),
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
