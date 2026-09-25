using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class FallbackChatProviderTests
{
    private sealed class ScriptedProvider(string name, Func<ChatResponse> behaviour) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(behaviour());
        }
    }

    private static ChatResponse Ok(string provider) =>
        new("answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", provider);

    private static ChatRequest Request() =>
        new("system", [ChatMessage.User("q")], [], 512);

    [Fact]
    public async Task Complete_PrimaryHealthy_NeverCallsTheSecondary()
    {
        var primary = new ScriptedProvider("anthropic", () => Ok("anthropic"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        var response = await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("anthropic", response.Provider);
        Assert.Equal(1, primary.Calls);
        Assert.Equal(0, secondary.Calls);
    }

    [Fact]
    public async Task Complete_PrimaryUnavailable_FallsThroughToTheSecondary()
    {
        var primary = new ScriptedProvider("anthropic",
            () => throw new ProviderUnavailableException("anthropic", "overloaded"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        var response = await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("openai", response.Provider);
        Assert.Equal(1, secondary.Calls);
    }

    /// <summary>
    /// T-F1. The chain hands every provider the same request, so a model named on the request
    /// reached whichever provider answered: an Anthropic outage sent "claude-sonnet-5" to
    /// OpenAI. Real providers over stubbed HTTP, because the bug was in the body on the wire.
    /// </summary>
    [Fact]
    public async Task Complete_AnthropicUnavailable_OpenAiIsSentItsOwnConfiguredModel()
    {
        var anthropicHttp = new StubHttpMessageHandler().Enqueue(
            HttpStatusCode.ServiceUnavailable, """{"type":"error","error":{"type":"overloaded_error"}}""");
        var openAiHttp = new StubHttpMessageHandler().EnqueueJson("""
            {
              "id": "chatcmpl-1",
              "model": "gpt-4o",
              "choices": [{ "index": 0, "finish_reason": "stop",
                "message": { "role": "assistant", "content": "answered by openai" } }],
              "usage": { "prompt_tokens": 10, "completion_tokens": 5 }
            }
            """);

        var anthropic = new AnthropicChatProvider(
            new HttpClient(anthropicHttp) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = "claude-sonnet-5" });
        var openAiOptions = new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o" };
        var openAi = new OpenAiChatProvider(
            new HttpClient(openAiHttp) { BaseAddress = new Uri(openAiOptions.BaseUrl) }, openAiOptions);

        var response = await new FallbackChatProvider([anthropic, openAi], NullLogger<FallbackChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("openai", response.Provider);
        Assert.Single(anthropicHttp.Requests);

        var body = await Assert.Single(openAiHttp.Requests).Content!
            .ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        Assert.Equal(openAiOptions.Model, document.RootElement.GetProperty("model").GetString());
    }

    [Fact]
    public async Task Complete_AllUnavailable_ThrowsAllProvidersUnavailable()
    {
        var primary = new ScriptedProvider("anthropic",
            () => throw new ProviderUnavailableException("anthropic", "overloaded"));
        var secondary = new ScriptedProvider("openai",
            () => throw new ProviderUnavailableException("openai", "502"));

        var exception = await Assert.ThrowsAsync<AllProvidersUnavailableException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(["anthropic", "openai"], exception.AttemptedProviders);
    }

    [Fact]
    public async Task Complete_NonTransientError_DoesNotFallThrough()
    {
        // A 400 is our bug. Retrying it on another provider hides it and wastes money.
        var primary = new ScriptedProvider("anthropic",
            () => throw new InvalidOperationException("malformed tool schema"));
        var secondary = new ScriptedProvider("openai", () => Ok("openai"));

        await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(0, secondary.Calls);
    }

    [Fact]
    public async Task Complete_ContentFiltered_DoesNotFallThrough()
    {
        // Answering a filtered request on another provider would route around the filter.
        var primary = new ScriptedProvider("openai",
            () => throw new ContentFilteredException("openai", ContentFilterStage.Completion, new TokenUsage(900, 12, 0, 0)));
        var secondary = new ScriptedProvider("anthropic", () => Ok("anthropic"));

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([primary, secondary], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("openai", exception.ProviderName);
        Assert.Equal(1, primary.Calls);
        Assert.Equal(0, secondary.Calls);
    }

    [Fact]
    public void Construct_WithNoProviders_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new FallbackChatProvider([], NullLogger<FallbackChatProvider>.Instance));
    }
}
