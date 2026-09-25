using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// <c>Chat:Providers</c> is configuration (T-A1), and a list that is wrong in any way stops
/// startup with a message naming the valid providers (Review Focus 2).
/// </summary>
public class ChatProviderSelectionTests
{
    private const string ValidList = "Valid providers: anthropic, azure-openai, openai.";

    private sealed class DownProvider(string name) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;
            throw new ProviderUnavailableException(name, $"{name} is down");
        }
    }

    /// <summary>The three real provider names, each factory recording that it ran.</summary>
    private static Dictionary<string, Func<IChatProvider>> Factories(List<string> built) => new()
    {
        ["anthropic"] = () => { built.Add("anthropic"); return new DownProvider("anthropic"); },
        ["openai"] = () => { built.Add("openai"); return new DownProvider("openai"); },
        ["azure-openai"] = () => { built.Add("azure-openai"); return new DownProvider("azure-openai"); }
    };

    private static InvalidOperationException AssertRejected(IReadOnlyList<string> names)
    {
        var built = new List<string>();

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(names, Factories(built)));

        Assert.Contains(ValidList, rejected.Message, StringComparison.Ordinal);

        // Validation runs before any factory, so a bad list constructs nothing.
        Assert.Empty(built);
        return rejected;
    }

    [Fact]
    public void Select_ReturnsTheProvidersInTheConfiguredOrder()
    {
        var built = new List<string>();

        var selected = ChatProviderSelection.Select(["openai", "anthropic"], Factories(built));

        Assert.Equal(["openai", "anthropic"], selected.Select(p => p.Name).ToArray());
        Assert.Equal(["openai", "anthropic"], built);
    }

    // T-A1
    [Fact]
    public async Task Select_WithOneEntry_BuildsAChainThatNeverFallsThrough()
    {
        var built = new List<string>();
        var selected = ChatProviderSelection.Select(["azure-openai"], Factories(built));
        var chain = new FallbackChatProvider(selected, NullLogger<FallbackChatProvider>.Instance);

        var request = new ChatRequest("system", [ChatMessage.User("q")], [], 512)
        {
            Context = new QueryContext(TimeSpan.FromSeconds(3))
        };

        var exhausted = await Assert.ThrowsAsync<AllProvidersUnavailableException>(
            async () => await chain.CompleteAsync(request, TestContext.Current.CancellationToken));

        Assert.Equal(["azure-openai"], exhausted.AttemptedProviders);
        Assert.Equal(1, ((DownProvider)selected[0]).Calls);

        // The providers that were not listed were never even constructed.
        Assert.Equal(["azure-openai"], built);
    }

    [Fact]
    public void Select_AMisspelledName_Throws()
    {
        var rejected = AssertRejected(["openai", "antropic"]);

        Assert.Contains("'antropic'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_ADifferentlyCasedName_Throws()
    {
        var rejected = AssertRejected(["OpenAI"]);

        Assert.Contains("'OpenAI'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_ADifferentlyCasedName_ThrowsEvenAgainstACaseInsensitiveDictionary()
    {
        var built = new List<string>();
        var lenient = new Dictionary<string, Func<IChatProvider>>(Factories(built), StringComparer.OrdinalIgnoreCase);

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(["OpenAI"], lenient));

        Assert.Contains(ValidList, rejected.Message, StringComparison.Ordinal);
        Assert.Empty(built);
    }

    [Fact]
    public void Select_ADuplicatedName_Throws()
    {
        var rejected = AssertRejected(["openai", "anthropic", "openai"]);

        Assert.Contains("'openai' more than once", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_AnEmptyList_Throws()
    {
        var rejected = AssertRejected([]);

        Assert.Contains("Chat:Providers is empty", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Select_AFactoryWhoseProviderReportsAnotherName_Throws()
    {
        var factories = new Dictionary<string, Func<IChatProvider>>
        {
            ["openai"] = () => new DownProvider("anthropic")
        };

        var rejected = Assert.Throws<InvalidOperationException>(
            () => ChatProviderSelection.Select(["openai"], factories));

        Assert.Contains("'openai'", rejected.Message, StringComparison.Ordinal);
        Assert.Contains("'anthropic'", rejected.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void ChatOptions_DefaultsToAnthropicThenOpenAi()
    {
        Assert.Equal(["anthropic", "openai"], new ChatOptions().Providers);
    }
}
