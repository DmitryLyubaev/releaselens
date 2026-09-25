using System;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// What each provider reports it is billed as, and what the OpenAI-wire codec does to usage
/// before it is priced. The rates themselves are pinned in <see cref="ModelPricingTests"/>.
/// </summary>
public class ProviderPricingTests
{
    private static readonly DateOnly PricedOn = new(2026, 9, 24);

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    private static OpenAiChatProvider OpenAi(StubHttpMessageHandler handler, bool unpriced = false) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o", Unpriced = unpriced });

    private static AnthropicChatProvider Anthropic(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = "claude-sonnet-5" });

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    private static string OpenAiWireResponse(
        string? model, int promptTokens, int completionTokens, int? cachedTokens, string finishReason = "stop")
    {
        var modelField = model is null ? string.Empty : $"\"model\": \"{model}\",";
        var details = cachedTokens is null
            ? string.Empty
            : $", \"prompt_tokens_details\": {{ \"cached_tokens\": {cachedTokens} }}";

        return $$"""
        {
          "id": "chatcmpl-1",
          {{modelField}}
          "choices": [{ "index": 0, "finish_reason": "{{finishReason}}",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
          "usage": { "prompt_tokens": {{promptTokens}}, "completion_tokens": {{completionTokens}}{{details}} }
        }
        """;
    }

    private static ChatResponse ParseAsAzure(string body)
    {
        using var document = JsonDocument.Parse(body);
        return OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "rl-gpt41mini", AzureRegional);
    }

    [Fact]
    public void OpenAi_ReportsItsConfiguredModelAsItsPricingIdentity()
    {
        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), OpenAi(new StubHttpMessageHandler()).Pricing);
    }

    [Fact]
    public void OpenAi_MarkedUnpriced_SaysSoInItsPricingIdentity()
    {
        Assert.Equal(new PricingIdentity("openai", "gpt-4o", Unpriced: true),
            OpenAi(new StubHttpMessageHandler(), unpriced: true).Pricing);
    }

    [Fact]
    public async Task Anthropic_ResponseCarriesTheProvidersPricingIdentity()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "id": "msg_01",
          "model": "claude-sonnet-5",
          "stop_reason": "end_turn",
          "content": [{ "type": "text", "text": "Release 1.30 fixed the planner." }],
          "usage": { "input_tokens": 1200, "output_tokens": 45, "cache_read_input_tokens": 1024, "cache_creation_input_tokens": 0 }
        }
        """);
        var provider = Anthropic(handler);

        var response = await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal(new PricingIdentity("anthropic", "claude-sonnet-5"), provider.Pricing);
        Assert.Equal(provider.Pricing, response.Pricing);

        // Anthropic already reports input_tokens without the cache reads, so nothing is subtracted.
        Assert.Equal(1200, response.Usage.InputTokens);
        Assert.Equal(1024, response.Usage.CacheReadInputTokens);
    }

    // T-F2
    [Fact]
    public async Task OpenAi_VersionedModelNameInTheResponse_IsPricedAtTheConfiguredModelsRate()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o-2024-08-06", promptTokens: 1_000_000, completionTokens: 1_000_000, cachedTokens: null));

        var response = await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("gpt-4o-2024-08-06", response.Model);
        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), response.Pricing);
        Assert.Equal(12.50m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));

        // The defect this replaces: pricing by the echoed name found no rate and returned $0.
        Assert.Equal(0m, ModelPricing.CostUsd(response.Model, response.Usage, PricedOn));
    }

    // T-C4's billed-once half on OpenAI; its cached-rate half is a recorded departure from spec
    // section 4.6, because gpt-4o's OpenAI cached-input rate has not been read and dated.
    [Fact]
    public async Task OpenAi_CachedTokens_AreReportedOutsideInputAndBilledOnce()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024));

        var response = await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal(new TokenUsage(176, 45, 1024, 0), response.Usage);

        // 1,200 prompt tokens at 2.50 plus 45 at 10.00: each prompt token is counted once, the
        // cached 1,024 at the input rate rather than on top of it.
        Assert.Equal(0.00345m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    // T-C4, on a model whose cached rate is known
    [Fact]
    public void AzureWire_CachedTokens_AreBilledOnceAtTheCachedRate()
    {
        var response = ParseAsAzure(
            OpenAiWireResponse("gpt-4.1-mini", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024));

        Assert.Equal(new TokenUsage(176, 45, 1024, 0), response.Usage);

        // 176 x 0.44 + 1,024 x 0.11 + 45 x 1.76 = 77.44 + 112.64 + 79.20 = 269.28 per million.
        Assert.Equal(0.00026928m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    // T-C2
    [Theory]
    [InlineData("gpt-4.1-mini")]
    [InlineData("gpt-4.1-mini-2025-04-14")]
    [InlineData("rl-gpt41mini")]
    [InlineData(null)]
    public void AzureWire_WhateverModelStringTheResponseCarries_ThePriceIsTheSame(string? model)
    {
        var response = ParseAsAzure(
            OpenAiWireResponse(model, promptTokens: 12_345, completionTokens: 678, cachedTokens: 0));

        Assert.Equal(model ?? "rl-gpt41mini", response.Model);
        Assert.Equal(AzureRegional, response.Pricing);

        // 12,345 x 0.44 + 678 x 1.76 = 5,431.80 + 1,193.28 = 6,625.08 per million.
        Assert.Equal(0.00662508m, ModelPricing.CostUsd(response.Pricing!, response.Usage, PricedOn));
    }

    [Fact]
    public void OpenAiWire_CachedTokensAbovePromptTokens_NeverReportNegativeInput()
    {
        var response = ParseAsAzure(
            OpenAiWireResponse("gpt-4.1-mini", promptTokens: 100, completionTokens: 5, cachedTokens: 150));

        Assert.Equal(0, response.Usage.InputTokens);
        Assert.Equal(150, response.Usage.CacheReadInputTokens);
    }

    [Fact]
    public async Task OpenAi_FilteredCompletion_CarriesThePricingIdentityForItsUsage()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(
            OpenAiWireResponse("gpt-4o", promptTokens: 1200, completionTokens: 45, cachedTokens: 1024,
                finishReason: "content_filter"));

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await OpenAi(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(new PricingIdentity("openai", "gpt-4o"), exception.Pricing);
        Assert.Equal(new TokenUsage(176, 45, 1024, 0), exception.Usage);
    }
}
