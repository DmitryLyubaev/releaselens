using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
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
        new("system", [ChatMessage.User("q")], [], "model", 512);

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
    public void Construct_WithNoProviders_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new FallbackChatProvider([], NullLogger<FallbackChatProvider>.Instance));
    }
}
