using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The fallback chain across the several calls of one query (T-F4). Every provider appends its
/// name to one shared log when called, so each test asserts the exact order the chain tried
/// them in, not just who answered.
/// </summary>
public class FallbackChatProviderStickinessTests
{
    /// <summary>
    /// Answers or fails per call, in the order <paramref name="healthy"/> gives, and throws if
    /// called more often than that, so an unexpected extra call fails the test loudly.
    /// </summary>
    private sealed class ScriptedProvider(string name, List<string> log, params bool[] healthy) : IChatProvider
    {
        private int _next;

        public string Name => name;
        public int Calls => _next;

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            log.Add(name);

            if (_next >= healthy.Length)
            {
                throw new InvalidOperationException($"{name} was called more often than scripted.");
            }

            if (!healthy[_next++])
            {
                throw new ProviderUnavailableException(name, $"{name} is down");
            }

            return Task.FromResult(new ChatResponse(
                "answer", [], new TokenUsage(10, 5, 0, 0), "end_turn", "model", name));
        }
    }

    private static FallbackChatProvider Chain(params IChatProvider[] providers) =>
        new(providers, NullLogger<FallbackChatProvider>.Instance);

    private static ChatRequest Request(QueryContext? context) =>
        new("system", [ChatMessage.User("q")], [], 512) { Context = context };

    private static QueryContext NewQuery() => new(TimeSpan.FromSeconds(3));

    [Fact]
    public async Task Complete_WithAContext_StaysOnTheProviderThatAnswered_AndNeverReturnsToAnEarlierOne()
    {
        var log = new List<string>();

        // A is down for the first call only. From then on it would answer, so any restart at the
        // top of the chain shows up as a call to A.
        var a = new ScriptedProvider("a", log, false, true, true);
        var b = new ScriptedProvider("b", log, true, true, false);
        var chain = Chain(a, b);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        var first = await chain.CompleteAsync(Request(query), ct);
        Assert.Equal("b", first.Provider);
        Assert.Equal("b", query.LastProvider);

        var second = await chain.CompleteAsync(Request(query), ct);
        Assert.Equal("b", second.Provider);

        // B now fails. Nothing follows B in this chain, and A is not retried.
        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(Request(query), ct));

        Assert.Equal(["b"], exhausted.AttemptedProviders);
        Assert.Equal(["a", "b", "b", "b"], log);
        Assert.Equal(1, a.Calls);
    }

    [Fact]
    public async Task Complete_WhenTheStickyProviderFails_MovesOnPastIt_NotBackToTheTop()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, false, true, true);
        var b = new ScriptedProvider("b", log, true, false);
        var c = new ScriptedProvider("c", log, true, true);
        var chain = Chain(a, b, c);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(query), ct)).Provider);
        Assert.Equal("c", (await chain.CompleteAsync(Request(query), ct)).Provider);
        Assert.Equal("c", query.LastProvider);
        Assert.Equal("c", (await chain.CompleteAsync(Request(query), ct)).Provider);

        Assert.Equal(["a", "b", "b", "c", "c"], log);
    }

    [Fact]
    public async Task Complete_AConcurrentQueryOnTheSameChain_StillStartsAtTheTop()
    {
        var log = new List<string>();

        // A is down for query 1's first call and healthy after that.
        var a = new ScriptedProvider("a", log, false, true);
        var b = new ScriptedProvider("b", log, true, true);
        var chain = Chain(a, b);
        var query1 = NewQuery();
        var query2 = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(query1), ct)).Provider);

        // Query 2 runs while query 1 is still in progress. It shares the chain instance, not
        // query 1's position in it.
        Assert.Equal("a", (await chain.CompleteAsync(Request(query2), ct)).Provider);
        Assert.Equal("a", query2.LastProvider);

        // And query 2 answering on A did not pull query 1 back to A.
        Assert.Equal("b", (await chain.CompleteAsync(Request(query1), ct)).Provider);
        Assert.Equal("b", query1.LastProvider);

        Assert.Equal(["a", "b", "a", "b"], log);
    }

    [Fact]
    public async Task Complete_WithNoContext_StartsAtTheTopEveryTime()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, false, true);
        var b = new ScriptedProvider("b", log, true);
        var chain = Chain(a, b);
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal("b", (await chain.CompleteAsync(Request(null), ct)).Provider);
        Assert.Equal("a", (await chain.CompleteAsync(Request(null), ct)).Provider);

        Assert.Equal(["a", "b", "a"], log);
    }

    [Fact]
    public async Task Complete_WithALastProviderNotInTheChain_StartsAtTheTop()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, true);
        var b = new ScriptedProvider("b", log);
        var query = NewQuery();
        query.LastProvider = "retired";

        var response = await Chain(a, b).CompleteAsync(Request(query), TestContext.Current.CancellationToken);

        Assert.Equal("a", response.Provider);
        Assert.Equal("a", query.LastProvider);
        Assert.Equal(["a"], log);
    }

    [Fact]
    public async Task Complete_WhenEveryRemainingProviderFails_LeavesTheStickyProviderUnchanged()
    {
        var log = new List<string>();
        var a = new ScriptedProvider("a", log, true, false);
        var b = new ScriptedProvider("b", log, false);
        var chain = Chain(a, b);
        var query = NewQuery();
        var ct = TestContext.Current.CancellationToken;

        await chain.CompleteAsync(Request(query), ct);

        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(Request(query), ct));

        Assert.Equal(["a", "b"], exhausted.AttemptedProviders);
        Assert.Equal("a", query.LastProvider);
    }
}
