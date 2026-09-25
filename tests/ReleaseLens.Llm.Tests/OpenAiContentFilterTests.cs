using System;
using System.Linq;
using System.Net.Http;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// A completion the provider's content filter stopped is its own outcome. Before this, the
/// OpenAI wire path never read <c>finish_reason</c>, so a filtered completion came back as an
/// ordinary answer with empty text (F3).
/// </summary>
public class OpenAiContentFilterTests
{
    private const string PartialText = "The first half of a sentence the filter cut";

    private static OpenAiChatProvider Create(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o" });

    private static ChatRequest Request() => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        [], 1024);

    // No cached tokens, so the expected usage is the same before and after Task 4 normalises
    // InputTokens to uncached input.
    private const string FilteredCompletion = """
    {
      "id": "chatcmpl-3",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "content_filter",
        "message": { "role": "assistant", "content": null } }],
      "usage": { "prompt_tokens": 900, "completion_tokens": 12 }
    }
    """;

    private const string FilteredCompletionWithPartialText = $$"""
    {
      "id": "chatcmpl-4",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "content_filter",
        "message": { "role": "assistant", "content": "{{PartialText}}" } }],
      "usage": { "prompt_tokens": 900, "completion_tokens": 30 }
    }
    """;

    // T-F3
    [Fact]
    public async Task Complete_FinishReasonContentFilter_ThrowsContentFilteredAtTheCompletionStage()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(FilteredCompletion);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("openai", exception.ProviderName);
        Assert.Equal(ContentFilterStage.Completion, exception.Stage);
        Assert.Equal(new TokenUsage(900, 12, 0, 0), exception.Usage);
        Assert.Equal("openai content filter blocked the completion.", exception.Message);
    }

    // Review Focus 5. The exception is the only thing a filtered completion produces, so
    // proving it holds none of the partial text proves no caller can surface any of it.
    [Fact]
    public async Task Complete_FilteredCompletionWithPartialText_CarriesNoneOfTheText()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(FilteredCompletionWithPartialText);

        var exception = await Assert.ThrowsAsync<ContentFilteredException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        var stringValues = exception.GetType()
            .GetProperties()
            .Where(property => property.PropertyType == typeof(string))
            .Select(property => (string?)property.GetValue(exception));

        Assert.All(stringValues, value => Assert.DoesNotContain(PartialText, value ?? string.Empty, StringComparison.Ordinal));
        Assert.DoesNotContain(PartialText, exception.ToString(), StringComparison.Ordinal);
        Assert.Empty(exception.Data);
        Assert.Null(exception.InnerException);
        Assert.Equal(new TokenUsage(900, 30, 0, 0), exception.Usage);
    }
}
