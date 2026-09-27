using System;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class ModelPricingTests
{
    private static readonly TokenUsage OneMillionIn = new(1_000_000, 0, 0, 0);
    private static readonly TokenUsage OneMillionOut = new(0, 1_000_000, 0, 0);
    private static readonly DateOnly AfterIntroductoryPricing = new(2026, 9, 24);

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    private static readonly PricingIdentity AzureGlobal =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard");

    [Fact]
    public void Sonnet5_BeforeThirtyFirstAugust2026_UsesIntroductoryInputPricing()
    {
        var cost = ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 8, 30));
        Assert.Equal(2.00m, cost);
    }

    [Fact]
    public void Sonnet5_OnThirtyFirstAugust2026_IsStillIntroductory()
    {
        var cost = ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 8, 31));
        Assert.Equal(2.00m, cost);
    }

    [Fact]
    public void Sonnet5_FromFirstSeptember2026_UsesStandardPricing()
    {
        Assert.Equal(3.00m, ModelPricing.CostUsd("claude-sonnet-5", OneMillionIn, new DateOnly(2026, 9, 1)));
        Assert.Equal(15.00m, ModelPricing.CostUsd("claude-sonnet-5", OneMillionOut, new DateOnly(2026, 9, 1)));
    }

    [Fact]
    public void CacheReads_ArePricedBelowFreshInput()
    {
        var fresh = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(1_000_000, 0, 0, 0), new DateOnly(2026, 9, 1));
        var cached = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(0, 0, 1_000_000, 0), new DateOnly(2026, 9, 1));

        Assert.True(cached < fresh, $"cache read {cached} should cost less than fresh input {fresh}");
    }

    [Fact]
    public void UnknownModel_ReturnsZeroRatherThanThrowing()
    {
        Assert.Equal(0m, ModelPricing.CostUsd("some-local-ollama-model", OneMillionIn, new DateOnly(2026, 9, 1)));
    }

    [Fact]
    public void MinimumCacheablePrefix_IsModelDependentAndNotMonotonic()
    {
        Assert.Equal(512, ModelPricing.MinimumCacheablePrefixTokens("claude-opus-5"));
        Assert.Equal(1024, ModelPricing.MinimumCacheablePrefixTokens("claude-sonnet-5"));
        Assert.Equal(4096, ModelPricing.MinimumCacheablePrefixTokens("claude-haiku-4-5-20251001"));
    }

    // T-C1
    [Fact]
    public void AzureRegionalIdentity_KnownTokens_GiveAnExactUsdFigure()
    {
        // 12,345 x 0.44 + 1,024 x 0.11 + 678 x 1.76 = 5,431.80 + 112.64 + 1,193.28 = 6,737.72 per million.
        var usage = new TokenUsage(InputTokens: 12_345, OutputTokens: 678, CacheReadInputTokens: 1_024, CacheCreationInputTokens: 0);

        Assert.Equal(0.00673772m, ModelPricing.CostUsd(AzureRegional, usage, AfterIntroductoryPricing));
    }

    [Fact]
    public void AzureRegionalIdentity_OneMillionOfEach_MatchesThePublishedRates()
    {
        Assert.Equal(0.44m, ModelPricing.CostUsd(AzureRegional, OneMillionIn, AfterIntroductoryPricing));
        Assert.Equal(0.11m, ModelPricing.CostUsd(AzureRegional, new TokenUsage(0, 0, 1_000_000, 0), AfterIntroductoryPricing));
        Assert.Equal(1.76m, ModelPricing.CostUsd(AzureRegional, OneMillionOut, AfterIntroductoryPricing));
    }

    // T-C1, for the deployment type the app uses by default.
    [Fact]
    public void AzureGlobalStandardIdentity_KnownTokens_GiveAnExactUsdFigure()
    {
        // 12,345 x 0.40 + 1,024 x 0.10 + 678 x 1.60 = 4,938.00 + 102.40 + 1,084.80 = 6,125.20 per million.
        var usage = new TokenUsage(InputTokens: 12_345, OutputTokens: 678, CacheReadInputTokens: 1_024, CacheCreationInputTokens: 0);

        Assert.Equal(0.0061252m, ModelPricing.CostUsd(AzureGlobal, usage, AfterIntroductoryPricing));
    }

    [Fact]
    public void AzureGlobalStandardIdentity_OneMillionOfEach_MatchesThePublishedRates()
    {
        Assert.Equal(0.40m, ModelPricing.CostUsd(AzureGlobal, OneMillionIn, AfterIntroductoryPricing));
        Assert.Equal(0.10m, ModelPricing.CostUsd(AzureGlobal, new TokenUsage(0, 0, 1_000_000, 0), AfterIntroductoryPricing));
        Assert.Equal(1.60m, ModelPricing.CostUsd(AzureGlobal, OneMillionOut, AfterIntroductoryPricing));
    }

    [Fact]
    public void AnthropicIdentity_KeepsTheNameBasedRatesAndCacheMultipliers()
    {
        var sonnet = new PricingIdentity("anthropic", "claude-sonnet-5");
        var usage = new TokenUsage(1_000_000, 1_000_000, 1_000_000, 1_000_000);

        // 3.00 input + 15.00 output + 0.30 cache read (0.1x) + 3.75 cache write (1.25x).
        Assert.Equal(22.05m, ModelPricing.CostUsd(sonnet, usage, AfterIntroductoryPricing));
        Assert.Equal(ModelPricing.CostUsd("claude-sonnet-5", usage, AfterIntroductoryPricing),
            ModelPricing.CostUsd(sonnet, usage, AfterIntroductoryPricing));
    }

    [Fact]
    public void OpenAiIdentity_PricesCachedTokensAtTheInputRate()
    {
        var gpt4o = new PricingIdentity("openai", "gpt-4o");

        Assert.Equal(2.50m, ModelPricing.CostUsd(gpt4o, new TokenUsage(0, 0, 1_000_000, 0), AfterIntroductoryPricing));
        Assert.Equal(2.50m, ModelPricing.CostUsd(gpt4o, OneMillionIn, AfterIntroductoryPricing));
    }

    [Fact]
    public void UnpricedIdentity_CostsNothing()
    {
        var ollama = new PricingIdentity("openai", "llama3.1", Unpriced: true);

        Assert.Equal(0m, ModelPricing.CostUsd(ollama, OneMillionIn, AfterIntroductoryPricing));
    }

    [Fact]
    public void PricedIdentityWithNoRate_ThrowsRatherThanPricingAtZero()
    {
        var unknown = new PricingIdentity("openai", "gpt-no-rate-test");

        var exception = Assert.Throws<InvalidOperationException>(() =>
            ModelPricing.CostUsd(unknown, OneMillionIn, AfterIntroductoryPricing));

        Assert.Contains("gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void HasRate_IsKeyedByTheWholeIdentity_NotJustTheModel()
    {
        Assert.True(ModelPricing.HasRate(AzureRegional, AfterIntroductoryPricing));
        Assert.True(ModelPricing.HasRate(AzureGlobal, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { DeploymentType = "DataZoneStandard" }, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { Version = "2024-07-18" }, AfterIntroductoryPricing));
        Assert.False(ModelPricing.HasRate(AzureRegional with { Provider = "openai" }, AfterIntroductoryPricing));
    }

    // T-C3
    [Fact]
    public void EnsurePriced_NamesEveryPricedIdentityThatHasNoRate()
    {
        var dataZone = AzureRegional with { DeploymentType = "DataZoneStandard" };
        var openAiNoRate = new PricingIdentity("openai", "gpt-no-rate-test");

        var exception = Assert.Throws<InvalidOperationException>(() => ModelPricing.EnsurePriced(
            [
                new PricingIdentity("anthropic", "claude-sonnet-5"),
                dataZone,
                openAiNoRate,
                new PricingIdentity("openai", "llama3.1", Unpriced: true)
            ],
            AfterIntroductoryPricing));

        Assert.Contains("azure-openai gpt-4.1-mini 2025-04-14 DataZoneStandard", exception.Message, StringComparison.Ordinal);
        Assert.Contains("openai gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("claude-sonnet-5", exception.Message, StringComparison.Ordinal);
        Assert.DoesNotContain("llama3.1", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void EnsurePriced_AcceptsPricedAndExplicitlyUnpricedIdentities()
    {
        ModelPricing.EnsurePriced(
            [
                AzureRegional,
                AzureGlobal,
                new PricingIdentity("anthropic", "claude-sonnet-5"),
                new PricingIdentity("openai", "gpt-4o"),
                new PricingIdentity("openai", "llama3.1", Unpriced: true)
            ],
            AfterIntroductoryPricing);
    }
}
