using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class OpenAiChatProviderTests
{
    private static OpenAiChatProvider Create(StubHttpMessageHandler handler, string baseUrl = "https://api.openai.com/v1/") =>
        new(new HttpClient(handler) { BaseAddress = new Uri(baseUrl) },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4o", BaseUrl = baseUrl });

    private static ChatRequest Request(params ToolDefinition[] tools) => new(
        "You answer questions about a repository.",
        [ChatMessage.User("What changed in release 1.30?")],
        tools, "gpt-4o", 1024);

    private static ToolDefinition SearchTool() => new(
        "search_commits", "Search commits.",
        JsonDocument.Parse("""{"type":"object","properties":{"query":{"type":"string"}}}""").RootElement);

    private const string TextResponse = """
    {
      "id": "chatcmpl-1",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
      "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                 "prompt_tokens_details": { "cached_tokens": 1024 } }
    }
    """;

    private const string ToolCallResponse = """
    {
      "id": "chatcmpl-2",
      "model": "gpt-4o",
      "choices": [{ "index": 0, "finish_reason": "tool_calls",
        "message": { "role": "assistant", "content": null,
          "tool_calls": [{ "id": "call_01", "type": "function",
            "function": { "name": "search_commits", "arguments": "{\"query\":\"release 1.30\"}" } }] } }],
      "usage": { "prompt_tokens": 900, "completion_tokens": 60 }
    }
    """;

    [Fact]
    public void Name_IsOpenAi()
    {
        Assert.Equal("openai", Create(new StubHttpMessageHandler()).Name);
    }

    [Fact]
    public async Task Complete_PostsToChatCompletionsWithABearerToken()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = handler.Requests[0];
        Assert.Equal("Bearer", request.Headers.Authorization!.Scheme);
        Assert.Contains("chat/completions", request.RequestUri!.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Complete_HonoursACustomBaseUrl_WhichIsWhatMakesOllamaWork()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler, "http://localhost:11434/v1/").CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("http://localhost:11434/v1/chat/completions", handler.Requests[0].RequestUri!.ToString());
    }

    [Fact]
    public async Task Complete_MapsSystemPromptToASystemMessage()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var first = document.RootElement.GetProperty("messages")[0];
        Assert.Equal("system", first.GetProperty("role").GetString());
    }

    [Fact]
    public async Task Complete_ReturnsTextAndMapsCachedPromptTokens()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var response = await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal(1200, response.Usage.InputTokens);
        Assert.Equal(45, response.Usage.OutputTokens);
        Assert.Equal(1024, response.Usage.CacheReadInputTokens);
        Assert.Equal("stop", response.StopReason);
    }

    [Fact]
    public async Task Complete_ParsesToolCallsAndTheirJsonEncodedArguments()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(ToolCallResponse);
        var response = await Create(handler).CompleteAsync(Request(SearchTool()), TestContext.Current.CancellationToken);

        var call = Assert.Single(response.ToolCalls);
        Assert.Equal("call_01", call.Id);
        Assert.Equal("search_commits", call.Name);
        Assert.Equal("release 1.30", call.Arguments.GetProperty("query").GetString());
    }

    [Fact]
    public async Task Complete_SerialisesToolsInFunctionShape()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(SearchTool()), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var tool = document.RootElement.GetProperty("tools")[0];
        Assert.Equal("function", tool.GetProperty("type").GetString());
        Assert.Equal("search_commits", tool.GetProperty("function").GetProperty("name").GetString());
    }

    [Fact]
    public async Task Complete_SerialisesToolResultsAsToolRoleMessages()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        var request = new ChatRequest("system",
            [
                ChatMessage.User("question"),
                ChatMessage.AssistantToolCalls([new ToolCall("call_01", "search_commits",
                    JsonDocument.Parse("""{"query":"x"}""").RootElement)]),
                ChatMessage.UserToolResults([new ToolResult("call_01", "3 commits found", false)])
            ],
            [SearchTool()], "gpt-4o", 1024);

        await Create(handler).CompleteAsync(request, TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var messages = document.RootElement.GetProperty("messages");
        var last = messages[messages.GetArrayLength() - 1];

        Assert.Equal("tool", last.GetProperty("role").GetString());
        Assert.Equal("call_01", last.GetProperty("tool_call_id").GetString());
    }

    [Fact]
    public async Task Complete_ServerError_ThrowsProviderUnavailable()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.BadGateway, "{}");

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("openai", exception.ProviderName);
    }
}
