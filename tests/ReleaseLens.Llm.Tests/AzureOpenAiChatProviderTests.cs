using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class AzureOpenAiChatProviderTests
{
    private const string BaseUrl = "https://releaselens-aoai.openai.azure.com/openai/v1/";
    private const string Deployment = "releaselens-chat";

    private static AzureOpenAiOptions Options(string baseUrl = BaseUrl) => new()
    {
        BaseUrl = baseUrl,
        Deployment = Deployment,
        Model = "gpt-4.1-mini",
        ModelVersion = "2025-04-14",
        DeploymentType = "Standard"
    };

    // No BaseAddress on the client: the provider sets it from the options, as it will when
    // IHttpClientFactory hands it a fresh client.
    private static AzureOpenAiChatProvider Create(
        HttpMessageHandler handler, AzureOpenAiOptions? options = null, ILogger<AzureOpenAiChatProvider>? logger = null) =>
        new(new HttpClient(handler), options ?? Options(), new FakeTimeProvider(),
            logger ?? NullLogger<AzureOpenAiChatProvider>.Instance);

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    private sealed class CountingProvider(string name) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(new ChatResponse("answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", name));
        }
    }

    private sealed class RecordingLogger<T> : ILogger<T>
    {
        public List<(LogLevel Level, string Message)> Entries { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter)
            => Entries.Add((logLevel, formatter(state, exception)));
    }

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." },
        "content_filter_results": { "hate": { "filtered": false, "severity": "safe" } } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
    }
    """;

    // The legacy documented shape: the code inside an error envelope.
    private const string FilteredPromptInEnvelope = """
    {
      "error": {
        "message": "The response was filtered due to the prompt triggering Azure OpenAI's content management policy.",
        "type": null,
        "param": "prompt",
        "code": "content_filter",
        "status": 400,
        "innererror": {
          "code": "ResponsibleAIPolicyViolation",
          "content_filter_result": { "violence": { "filtered": true, "severity": "high" } }
        }
      }
    }
    """;

    // The v1 OpenAPI error schema has no envelope; its diagnostics are under inner_error.
    private const string FilteredPromptTopLevelInnerUnderscore = """
    {
      "code": "content_filter",
      "message": "The response was filtered due to the prompt triggering Azure OpenAI's content management policy.",
      "param": "prompt",
      "type": null,
      "inner_error": {
        "code": "ResponsibleAIPolicyViolation",
        "content_filter_results": { "violence": { "filtered": true, "severity": "high" } }
      }
    }
    """;

    // No envelope, and the other spelling of the diagnostics, with a different inner code.
    private const string FilteredPromptTopLevelInnerError = """
    {
      "code": "content_filter",
      "message": "The prompt was filtered.",
      "innererror": { "code": "ContentFiltered" }
    }
    """;

    [Fact]
    public void Name_IsAzureOpenAi_AndItIsPricedAsTheConfiguredDeployment()
    {
        var provider = Create(new StubHttpMessageHandler());

        Assert.Equal("azure-openai", provider.Name);
        Assert.Equal(new PricingIdentity("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard"), provider.Pricing);
    }

    [Fact]
    public void DefaultOptions_ArePricedAsAGlobalStandardDeploymentThatHasARate()
    {
        var provider = Create(new StubHttpMessageHandler(), new AzureOpenAiOptions { BaseUrl = BaseUrl, Deployment = Deployment });

        Assert.Equal(new PricingIdentity("azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard"), provider.Pricing);
        Assert.True(ModelPricing.HasRate(provider.Pricing, new DateOnly(2026, 9, 27)));
    }

    [Fact]
    public async Task Complete_ReportsAzureOpenAiAsTheAnsweringProvider()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        var provider = Create(handler);
        var response = await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal(provider.Pricing, response.Pricing);
    }

    [Theory]
    [InlineData("https://releaselens-aoai.openai.azure.com/openai/v1/")]
    [InlineData("https://releaselens-aoai.openai.azure.com/openai/v1")]
    public async Task Complete_PostsToTheV1ChatCompletionsUrl_WithNoApiVersion(string baseUrl)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(handler, Options(baseUrl)).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = handler.Requests[0];
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("https://releaselens-aoai.openai.azure.com/openai/v1/chat/completions", request.RequestUri!.ToString());
        Assert.Empty(request.RequestUri.Query);
    }

    [Fact]
    public async Task Complete_KeepsABaseAddressTheClientAlreadyHas()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var client = new HttpClient(handler) { BaseAddress = new Uri("https://apim.example.test/aoai/v1/") };

        await new AzureOpenAiChatProvider(client, Options(), new FakeTimeProvider(), NullLogger<AzureOpenAiChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("https://apim.example.test/aoai/v1/chat/completions", handler.Requests[0].RequestUri!.ToString());
    }

    [Fact]
    public async Task Complete_SendsTheDeploymentNameAsTheModel()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        // Options().Model is gpt-4.1-mini, which names the pricing identity and is never sent.
        Assert.Equal(Deployment, document.RootElement.GetProperty("model").GetString());
    }

    [Fact]
    public async Task Complete_SetsNoApiKeyHeaderAndNoAuthorizationOfItsOwn()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var client = new HttpClient(handler);

        await new AzureOpenAiChatProvider(client, Options(), new FakeTimeProvider(), NullLogger<AzureOpenAiChatProvider>.Instance)
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        // With no EntraTokenHandler in the pipeline, nothing may authenticate the request:
        // the token is the handler's job, and a key is never sent at all.
        var request = handler.Requests[0];
        Assert.Null(request.Headers.Authorization);
        Assert.False(request.Headers.Contains("api-key"));
        Assert.Null(client.DefaultRequestHeaders.Authorization);
        Assert.False(client.DefaultRequestHeaders.Contains("api-key"));
    }

    [Theory]
    [InlineData(FilteredPromptInEnvelope)]
    [InlineData(FilteredPromptTopLevelInnerUnderscore)]
    [InlineData(FilteredPromptTopLevelInnerError)]
    public async Task Complete_FilteredPrompt_ThrowsContentFilteredAtThePromptStage(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);
        var provider = Create(handler);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await provider.CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Prompt, exception.Stage);
        Assert.Equal(TokenUsage.Zero, exception.Usage);
        Assert.Equal(provider.Pricing, exception.Pricing);
    }

    [Theory]
    [InlineData(FilteredPromptInEnvelope)]
    [InlineData(FilteredPromptTopLevelInnerUnderscore)]
    [InlineData(FilteredPromptTopLevelInnerError)]
    public async Task Complete_FilteredPrompt_IsNotAnsweredByTheNextProvider(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);
        var next = new CountingProvider("anthropic");

        await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([Create(handler), next], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal(0, next.Calls);
    }

    [Fact]
    public async Task Complete_FilteredCompletion_ThrowsContentFilteredAtTheCompletionStage_AndIsNotAnsweredElsewhere()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "content_filter",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the" },
            "content_filter_results": { "violence": { "filtered": true, "severity": "high" } } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 30 }
        }
        """);
        var azure = Create(handler);
        var next = new CountingProvider("anthropic");

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await new FallbackChatProvider([azure, next], NullLogger<FallbackChatProvider>.Instance)
                .CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Completion, exception.Stage);
        Assert.Equal(new TokenUsage(1200, 30, 0, 0), exception.Usage);
        Assert.Equal(azure.Pricing, exception.Pricing);
        Assert.Equal(0, next.Calls);
    }

    [Fact]
    public async Task Complete_FilterDidNotRun_ReturnsTheAnswer_AndRecordsIt()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." },
            "content_filter_results": { "error": { "code": "content_filter_error", "message": "The contents are not filtered" } } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
        }
        """);
        var logger = new RecordingLogger<AzureOpenAiChatProvider>();

        var response = await Create(handler, logger: logger).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal("stop", response.StopReason);
        var entry = Assert.Single(logger.Entries);
        Assert.Equal(LogLevel.Information, entry.Level);
        Assert.Contains("content_filter_error", entry.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Complete_FilterRan_RecordsNothing()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var logger = new RecordingLogger<AzureOpenAiChatProvider>();

        await Create(handler, logger: logger).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Empty(logger.Entries);
    }

    [Theory]
    [InlineData("""{ "error": { "message": "Invalid value for 'max_tokens'.", "type": "invalid_request_error", "param": "max_tokens", "code": "invalid_value" } }""")]
    [InlineData("""{ "code": "invalid_value", "message": "Invalid value for 'max_tokens'.", "innererror": { "code": "content_filter" } }""")]
    [InlineData("""{ "error": { "message": "content_filter", "code": null } }""")]
    [InlineData("Bad Request")]
    public async Task Complete_BadRequestThatIsNotAFilteredPrompt_IsStillOurOwnBug(string body)
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadRequest, body);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("(400)", exception.Message, StringComparison.Ordinal);
    }
}
