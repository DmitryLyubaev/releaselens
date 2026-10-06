using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Agent;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// Gateway mode is configuration only: <c>AzureOpenAi:BaseUrl</c> points at the API Management
/// gateway's <c>/openai/v1/</c> and <c>AzureOpenAi:TokenScope</c> is the gateway app's scope.
/// These tests build the provider the way <c>Program.cs</c> does and pin the behaviour the
/// gateway relies on, so a change to the provider cannot break it unnoticed.
/// </summary>
public class AzureOpenAiGatewayModeTests
{
    private const string GatewayBaseUrl = "https://gateway.example.com/openai/v1/";
    private const string GatewayScope = "api://11111111-1111-1111-1111-111111111111/.default";
    private const string GatewayToken = "gateway-token";
    private static readonly DateTimeOffset Start = new(2026, 10, 6, 9, 0, 0, TimeSpan.Zero);

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
    }
    """;

    private const string BudgetRefusalBody = """{"error":{"code":"403","message":"The token budget is used up."}}""";
    private const string BothTrippedBody = """{"error":{"code":"503","message":"Both backends are unavailable."}}""";
    private const string MinuteBudgetBody = """{"error":{"code":"429","message":"Token rate limit is exceeded."}}""";

    private static AzureOpenAiChatProvider Create(
        StubHttpMessageHandler stub, FakeTokenCredential credential, TimeProvider time) =>
        new(
            new HttpClient(new EntraTokenHandler(new EntraTokenCache(credential, GatewayScope, time)) { InnerHandler = stub }),
            new AzureOpenAiOptions
            {
                BaseUrl = GatewayBaseUrl,
                Deployment = "releaselens-chat",
                TokenScope = GatewayScope
            },
            time,
            NullLogger<AzureOpenAiChatProvider>.Instance);

    private static AzureOpenAiChatProvider Create(StubHttpMessageHandler stub) =>
        Create(stub, new FakeTokenCredential().Returns(GatewayToken, Start.AddHours(1)), new FakeTimeProvider(Start));

    private static ChatRequest Request(QueryContext? context = null) => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024)
    {
        Context = context
    };

    [Fact]
    public async Task Gateway_PostsToTheGatewaysChatCompletions()
    {
        var stub = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(stub).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = Assert.Single(stub.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal(new Uri("https://gateway.example.com/openai/v1/chat/completions"), request.RequestUri);
    }

    [Fact]
    public async Task Gateway_AsksForATokenForTheGatewayScope()
    {
        var credential = new FakeTokenCredential().Returns(GatewayToken, Start.AddHours(1));
        var stub = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(stub, credential, new FakeTimeProvider(Start))
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal([GatewayScope], Assert.Single(credential.RequestedScopes));
        var authorization = Assert.Single(stub.Requests).Headers.Authorization!;
        Assert.Equal("Bearer", authorization.Scheme);
        Assert.Equal(GatewayToken, authorization.Parameter);
    }

    [Fact]
    public async Task Gateway_SendsNoApiKey()
    {
        var stub = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        await Create(stub).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = Assert.Single(stub.Requests);
        Assert.DoesNotContain(request.Headers, header => header.Key.Equals("api-key", StringComparison.OrdinalIgnoreCase));
        Assert.False(request.Content!.Headers.Contains("api-key"));
    }

    [Fact]
    public async Task Gateway_BudgetRefusal403_IsProviderUnavailable()
    {
        var stub = new StubHttpMessageHandler().Enqueue(HttpStatusCode.Forbidden, BudgetRefusalBody);

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            Create(stub).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Single(stub.Requests);
    }

    [Fact]
    public async Task Gateway_BothBackendsTripped503_IsProviderUnavailable()
    {
        var stub = new StubHttpMessageHandler().Enqueue(HttpStatusCode.ServiceUnavailable, BothTrippedBody);

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            Create(stub).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Single(stub.Requests);
    }

    [Fact]
    public async Task Gateway_MinuteBudget429_WithRetryAfterSeconds_WaitsAndRetriesOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new SignallingTimeProvider(Start);
        var stub = new StubHttpMessageHandler()
            .Enqueue(HttpStatusCode.TooManyRequests, MinuteBudgetBody,
                response => response.Headers.TryAddWithoutValidation("Retry-After", "2"))
            .EnqueueJson(TextResponse);
        var provider = Create(stub, new FakeTokenCredential().Returns(GatewayToken, Start.AddHours(1)), time);
        var context = new QueryContext(TimeSpan.FromMilliseconds(new AgentOptions().RateLimitWaitBudgetMs));

        var completion = provider.CompleteAsync(Request(context), ct);

        // The retry waits on the fake clock: until it is advanced past the 2 s, only the 429 was sent.
        await time.WaitForTimerAsync(ct);
        Assert.Single(stub.Requests);
        Assert.False(completion.IsCompleted);

        time.Clock.Advance(TimeSpan.FromSeconds(2));
        var response = await completion;

        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal(2, stub.Requests.Count);
        Assert.Equal(TimeSpan.FromSeconds(1), context.RateLimitWaitRemaining);
    }

    /// <summary>
    /// A <see cref="FakeTimeProvider"/> that signals when a timer is scheduled, so the test
    /// advances the clock only once the retry's wait exists, and never sleeps for real.
    /// </summary>
    private sealed class SignallingTimeProvider(DateTimeOffset start) : TimeProvider
    {
        private readonly SemaphoreSlim _scheduled = new(0);

        public FakeTimeProvider Clock { get; } = new(start);

        public Task WaitForTimerAsync(CancellationToken cancellationToken) => _scheduled.WaitAsync(cancellationToken);

        public override DateTimeOffset GetUtcNow() => Clock.GetUtcNow();
        public override long GetTimestamp() => Clock.GetTimestamp();
        public override long TimestampFrequency => Clock.TimestampFrequency;
        public override TimeZoneInfo LocalTimeZone => Clock.LocalTimeZone;

        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
        {
            var timer = Clock.CreateTimer(callback, state, dueTime, period);
            _scheduled.Release();
            return timer;
        }
    }
}
