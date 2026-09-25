using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The 429 wait (spec §4.4): honour <c>retry-after-ms</c>, then whole-second <c>retry-after</c>,
/// only within what is left of the query's budget, and retry the same provider once.
/// </summary>
public class AzureOpenAiRateLimitTests
{
    private const string RateLimitedBody = """{"error":{"code":"429","message":"Rate limit is exceeded."}}""";

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45 }
    }
    """;

    /// <summary>
    /// A <see cref="FakeTimeProvider"/> that also records every timer asked of it. A test must
    /// know the delay has been scheduled before it advances the clock: advancing first would
    /// leave the delay due after the new "now", and the test would hang. The record also lets a
    /// test prove no delay was scheduled at all.
    /// </summary>
    private sealed class ObservedTimeProvider : TimeProvider
    {
        private readonly List<TimeSpan> _dueTimes = [];
        private readonly SemaphoreSlim _scheduled = new(0);

        public FakeTimeProvider Clock { get; } = new();

        public IReadOnlyList<TimeSpan> DueTimes
        {
            get { lock (_dueTimes) { return [.. _dueTimes]; } }
        }

        /// <summary>
        /// Completes once <paramref name="call"/> has scheduled a timer. If the call finishes
        /// first, which is what a provider that never waits does, the test fails instead of
        /// hanging.
        /// </summary>
        public async Task WaitForTimerAsync(Task call, CancellationToken cancellationToken)
        {
            var scheduled = _scheduled.WaitAsync(cancellationToken);
            if (await Task.WhenAny(scheduled, call) == scheduled)
            {
                return;
            }

            await call;
            Assert.Fail("The call completed without scheduling a wait.");
        }

        /// <summary>
        /// Awaits <paramref name="call"/>, failing the test instead of hanging if the call
        /// schedules a wait first.
        /// </summary>
        public async Task<T> WithoutWaitingAsync<T>(Task<T> call, CancellationToken cancellationToken)
        {
            var scheduled = _scheduled.WaitAsync(cancellationToken);
            if (await Task.WhenAny(scheduled, call) == scheduled)
            {
                Assert.Fail("The call scheduled a wait.");
            }

            return await call;
        }

        public override DateTimeOffset GetUtcNow() => Clock.GetUtcNow();
        public override long GetTimestamp() => Clock.GetTimestamp();
        public override long TimestampFrequency => Clock.TimestampFrequency;
        public override TimeZoneInfo LocalTimeZone => Clock.LocalTimeZone;

        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
        {
            var timer = Clock.CreateTimer(callback, state, dueTime, period);
            lock (_dueTimes)
            {
                _dueTimes.Add(dueTime);
            }

            _scheduled.Release();
            return timer;
        }
    }

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

    private static AzureOpenAiChatProvider Create(StubHttpMessageHandler handler, TimeProvider time) =>
        new(new HttpClient(handler),
            new AzureOpenAiOptions
            {
                BaseUrl = "https://releaselens-test.openai.azure.com/openai/v1/",
                Deployment = "releaselens-chat"
            },
            time,
            NullLogger<AzureOpenAiChatProvider>.Instance);

    private static ChatRequest Request(QueryContext? context) => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024)
    {
        Context = context
    };

    private static QueryContext Budget(int milliseconds) => new(TimeSpan.FromMilliseconds(milliseconds));

    private static StubHttpMessageHandler RateLimited(
        StubHttpMessageHandler handler, string? retryAfterMs = null, string? retryAfter = null)
        => handler.Enqueue(HttpStatusCode.TooManyRequests, RateLimitedBody, response =>
        {
            if (retryAfterMs is not null)
            {
                response.Headers.TryAddWithoutValidation("retry-after-ms", retryAfterMs);
            }

            if (retryAfter is not null)
            {
                response.Headers.TryAddWithoutValidation("retry-after", retryAfter);
            }
        });

    [Fact]
    public async Task RetryAfterMs_WithinBudget_WaitsThatLongAndRetriesTheSameProviderOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1200").EnqueueJson(TextResponse);
        var secondary = new CountingProvider("anthropic");
        var chain = new FallbackChatProvider([Create(handler, time), secondary], NullLogger<FallbackChatProvider>.Instance);
        var context = Budget(3000);

        var completion = chain.CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(1200)], time.DueTimes);
        Assert.Single(handler.Requests);

        time.Clock.Advance(TimeSpan.FromMilliseconds(1199));
        Assert.Single(handler.Requests);
        Assert.False(completion.IsCompleted);

        time.Clock.Advance(TimeSpan.FromMilliseconds(1));
        var response = await completion;

        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(handler.Requests[0].RequestUri, handler.Requests[1].RequestUri);
        Assert.Equal(
            await handler.Requests[0].Content!.ReadAsStringAsync(ct),
            await handler.Requests[1].Content!.ReadAsStringAsync(ct));
        Assert.Equal(0, secondary.Calls);
        Assert.Equal(TimeSpan.FromMilliseconds(1800), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task RetryAfterSeconds_WithoutRetryAfterMs_IsHonoured()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfter: "2").EnqueueJson(TextResponse);
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromSeconds(2)], time.DueTimes);
        Assert.Single(handler.Requests);

        time.Clock.Advance(TimeSpan.FromSeconds(2));
        var response = await completion;

        Assert.Equal("azure-openai", response.Provider);
        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(TimeSpan.FromSeconds(1), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task BothHeaders_RetryAfterMsWins()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "400", retryAfter: "2")
            .EnqueueJson(TextResponse);
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(400)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromMilliseconds(400));
        await completion;

        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(TimeSpan.FromMilliseconds(2600), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task WaitLongerThanWhatRemains_FallsThroughWithoutWaiting()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1500");
        var secondary = new CountingProvider("anthropic");
        var chain = new FallbackChatProvider([Create(handler, time), secondary], NullLogger<FallbackChatProvider>.Instance);

        // 1500 ms would fit the full 3 s budget but not the 1 s left of it.
        var context = Budget(3000);
        Assert.True(context.TrySpendWait(TimeSpan.FromSeconds(2)));

        var response = await time.WithoutWaitingAsync(chain.CompleteAsync(Request(context), ct), ct);

        Assert.Equal("anthropic", response.Provider);
        Assert.Single(handler.Requests);
        Assert.Equal(1, secondary.Calls);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(1), context.RateLimitWaitRemaining);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("Wed, 21 Oct 2026 07:28:00 GMT")]
    [InlineData("1.5")]
    [InlineData("-1")]
    public async Task NoUsableRetryHeader_FallsThroughWithoutWaiting(string? retryAfter)
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfter: retryAfter);
        var context = Budget(3000);

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context), ct), ct));

        Assert.Equal("azure-openai", exception.ProviderName);
        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(3), context.RateLimitWaitRemaining);
    }

    /// <summary>
    /// Review Focus 1. Only a plain non-negative integer within <see cref="int"/> is usable;
    /// anything else is absent, so the call falls through at once instead of waiting a rounded,
    /// clamped or unbounded time. Surrounding whitespace never reaches the provider from a real
    /// HTTP/1.1 response, because SocketsHttpHandler strips it; the row is here because the stub
    /// stores the value as given.
    /// </summary>
    [Theory]
    [InlineData("1.5")]
    [InlineData("-5")]
    [InlineData("99999999999")]
    [InlineData(" 200 ")]
    [InlineData("+200")]
    [InlineData("200ms")]
    [InlineData("")]
    public async Task MalformedRetryAfterMs_IsTreatedAsAbsent(string retryAfterMs)
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: retryAfterMs);
        var context = Budget(3000);

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context), ct), ct));

        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
        Assert.Equal(TimeSpan.FromSeconds(3), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task MalformedRetryAfterMs_FallsBackToRetryAfter()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1.5", retryAfter: "1")
            .EnqueueJson(TextResponse);

        var completion = Create(handler, time).CompleteAsync(Request(Budget(3000)), ct);
        await time.WaitForTimerAsync(completion, ct);

        Assert.Equal([TimeSpan.FromSeconds(1)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromSeconds(1));
        await completion;

        Assert.Equal(2, handler.Requests.Count);
    }

    [Fact]
    public async Task SecondRateLimit_FallsThroughWithoutASecondWait()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(RateLimited(new StubHttpMessageHandler(), retryAfterMs: "100"), retryAfterMs: "100");
        var context = Budget(3000);

        var completion = Create(handler, time).CompleteAsync(Request(context), ct);
        await time.WaitForTimerAsync(completion, ct);
        time.Clock.Advance(TimeSpan.FromMilliseconds(100));

        await Assert.ThrowsAsync<ProviderUnavailableException>(() => time.WithoutWaitingAsync(completion, ct));

        Assert.Equal(2, handler.Requests.Count);
        Assert.Single(time.DueTimes);
        Assert.Equal(TimeSpan.FromMilliseconds(2900), context.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task NoQueryContext_NeverWaits()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "100");

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(Create(handler, time).CompleteAsync(Request(context: null), ct), ct));

        Assert.Single(handler.Requests);
        Assert.Empty(time.DueTimes);
    }

    [Fact]
    public async Task Budget_IsPerQuery_NotPerProvider()
    {
        var ct = TestContext.Current.CancellationToken;
        var time = new ObservedTimeProvider();

        // Responses are served in request order: query A's 429, query B's 429, B's retry (its
        // wait is shorter, so it fires first), A's retry, then A's later 429.
        var handler = RateLimited(RateLimited(new StubHttpMessageHandler(), retryAfterMs: "2500"), retryAfterMs: "2000")
            .EnqueueJson(TextResponse)
            .EnqueueJson(TextResponse);
        RateLimited(handler, retryAfterMs: "2500");

        var provider = Create(handler, time);
        var queryA = Budget(3000);
        var queryB = Budget(3000);

        var firstA = provider.CompleteAsync(Request(queryA), ct);
        await time.WaitForTimerAsync(firstA, ct);

        // A has already spent 2500 of its 3000 ms; B, on the same provider instance, still
        // has all of its own.
        var firstB = provider.CompleteAsync(Request(queryB), ct);
        await time.WaitForTimerAsync(firstB, ct);

        Assert.Equal([TimeSpan.FromMilliseconds(2500), TimeSpan.FromMilliseconds(2000)], time.DueTimes);

        time.Clock.Advance(TimeSpan.FromMilliseconds(2000));
        await firstB;
        time.Clock.Advance(TimeSpan.FromMilliseconds(500));
        await firstA;

        Assert.Equal(TimeSpan.FromMilliseconds(500), queryA.RateLimitWaitRemaining);
        Assert.Equal(TimeSpan.FromMilliseconds(1000), queryB.RateLimitWaitRemaining);

        await Assert.ThrowsAsync<ProviderUnavailableException>(() =>
            time.WithoutWaitingAsync(provider.CompleteAsync(Request(queryA), ct), ct));

        Assert.Equal(5, handler.Requests.Count);
        Assert.Equal(2, time.DueTimes.Count);
        Assert.Equal(TimeSpan.FromMilliseconds(500), queryA.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task CancellationDuringTheWait_StopsWithoutRetrying()
    {
        var time = new ObservedTimeProvider();
        var handler = RateLimited(new StubHttpMessageHandler(), retryAfterMs: "1000");
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);

        var completion = Create(handler, time).CompleteAsync(Request(Budget(3000)), cancellation.Token);
        await time.WaitForTimerAsync(completion, TestContext.Current.CancellationToken);
        await cancellation.CancelAsync();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () => await completion);
        Assert.Single(handler.Requests);
    }
}
