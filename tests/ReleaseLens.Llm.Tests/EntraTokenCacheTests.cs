using System;
using System.Threading.Tasks;
using Azure.Identity;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class EntraTokenCacheTests
{
    private const string Scope = "https://ai.azure.com/.default";
    private static readonly DateTimeOffset Start = new(2026, 9, 24, 9, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset FirstExpiry = Start.AddHours(1);

    [Fact]
    public void Options_DefaultTokenScope_IsTheV1Scope()
    {
        Assert.Equal(Scope, new AzureOpenAiOptions().TokenScope);
    }

    [Fact]
    public async Task GetToken_AsksTheCredentialForTheConfiguredScope()
    {
        var credential = new FakeTokenCredential().Returns("token-1", FirstExpiry);
        var cache = new EntraTokenCache(credential, "api://gateway.example/.default", new FakeTimeProvider(Start));

        var token = await cache.GetTokenAsync(TestContext.Current.CancellationToken);

        Assert.Equal("token-1", token);
        Assert.Equal(["api://gateway.example/.default"], Assert.Single(credential.RequestedScopes));
    }

    [Fact]
    public async Task GetToken_ReusesTheTokenUntilFiveMinutesBeforeExpiry_ThenRefreshes()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", FirstExpiry)
            .Returns("token-2", FirstExpiry.AddHours(1));
        var cache = new EntraTokenCache(credential, Scope, time);

        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));

        time.SetUtcNow(FirstExpiry - EntraTokenCache.RefreshBeforeExpiry - TimeSpan.FromTicks(1));
        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
        Assert.Equal(1, credential.Calls);

        time.Advance(TimeSpan.FromTicks(1));
        Assert.Equal("token-2", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task GetToken_TwoCallersAtTheRefreshPoint_CauseExactlyOneRefresh()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", FirstExpiry)
            .Returns("token-2", FirstExpiry.AddHours(1));
        var cache = new EntraTokenCache(credential, Scope, time);
        await cache.GetTokenAsync(TestContext.Current.CancellationToken);

        time.SetUtcNow(FirstExpiry - EntraTokenCache.RefreshBeforeExpiry);
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        credential.Gate = gate;

        // The first caller is inside the credential when the second arrives, so the second
        // can only wait for the refresh already in flight.
        var first = cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask();
        var second = cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask();
        Assert.False(first.IsCompleted);
        Assert.False(second.IsCompleted);

        gate.SetResult();
        var tokens = await Task.WhenAll(first, second);

        Assert.Equal(["token-2", "token-2"], tokens);
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task GetToken_AfterTheCredentialFails_TheNextCallAsksAgain()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."))
            .Returns("token-1", FirstExpiry);
        var cache = new EntraTokenCache(credential, Scope, new FakeTimeProvider(Start));

        await Assert.ThrowsAsync<CredentialUnavailableException>(
            () => cache.GetTokenAsync(TestContext.Current.CancellationToken).AsTask());

        Assert.Equal("token-1", await cache.GetTokenAsync(TestContext.Current.CancellationToken));
    }
}
