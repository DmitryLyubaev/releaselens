using System;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class ModelPricingTests
{
    private static readonly TokenUsage OneMillionIn = new(1_000_000, 0, 0, 0);
    private static readonly TokenUsage OneMillionOut = new(0, 1_000_000, 0, 0);

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
}
