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

public class AnthropicChatProviderTests
{
    private static AnthropicChatProvider Create(StubHttpMessageHandler handler, string model = "claude-sonnet-5") =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.anthropic.com/") },
            new AnthropicOptions { ApiKey = "sk-ant-test", Model = model });

    private static ChatRequest Request(params ToolDefinition[] tools) => new(
        SystemPrompt: "You answer questions about a repository.",
        Messages: [ChatMessage.User("What changed in release 1.30?")],
        Tools: tools,
        MaxTokens: 1024);

    private static ToolDefinition SearchTool() => new(
        "search_commits",
        "Search commits by natural-language query.",
        JsonDocument.Parse("""
            {"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}
            """).RootElement.Clone());

    private const string TextResponse = """
    {
      "id": "msg_01",
      "model": "claude-sonnet-5",
      "stop_reason": "end_turn",
      "content": [{ "type": "text", "text": "Release 1.30 fixed the planner." }],
      "usage": { "input_tokens": 1200, "output_tokens": 45, "cache_read_input_tokens": 1024, "cache_creation_input_tokens": 0 }
    }
    """;

    private const string ToolUseResponse = """
    {
      "id": "msg_02",
      "model": "claude-sonnet-5",
      "stop_reason": "tool_use",
      "content": [
        { "type": "text", "text": "Let me search." },
        { "type": "tool_use", "id": "toolu_01", "name": "search_commits", "input": { "query": "release 1.30" } }
      ],
      "usage": { "input_tokens": 900, "output_tokens": 60, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 512 }
    }
    """;

    [Fact]
    public void Name_IsAnthropic()
    {
        Assert.Equal("anthropic", Create(new StubHttpMessageHandler()).Name);
    }

    [Fact]
    public async Task Complete_SendsApiKeyAndVersionHeaders()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var request = handler.Requests[0];
        Assert.Equal("sk-ant-test", request.Headers.GetValues("x-api-key").Single());
        Assert.Equal("2023-06-01", request.Headers.GetValues("anthropic-version").Single());
        Assert.Equal("/v1/messages", request.RequestUri!.AbsolutePath);
    }

    [Fact]
    public async Task Complete_SendsTheModelItIsConfiguredWith()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler, model: "claude-haiku-4-5-20251001")
            .CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        Assert.Equal("claude-haiku-4-5-20251001", document.RootElement.GetProperty("model").GetString());
    }

    [Fact]
    public async Task Complete_ReturnsTextAndUsageIncludingCacheReads()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        var response = await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Equal(1200, response.Usage.InputTokens);
        Assert.Equal(45, response.Usage.OutputTokens);
        Assert.Equal(1024, response.Usage.CacheReadInputTokens);
        Assert.Equal("end_turn", response.StopReason);
        Assert.Empty(response.ToolCalls);
    }

    [Fact]
    public async Task Complete_ParsesToolUseBlocks()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(ToolUseResponse);
        var response = await Create(handler).CompleteAsync(Request(SearchTool()), TestContext.Current.CancellationToken);

        var call = Assert.Single(response.ToolCalls);
        Assert.Equal("toolu_01", call.Id);
        Assert.Equal("search_commits", call.Name);
        Assert.Equal("release 1.30", call.Arguments.GetProperty("query").GetString());
        Assert.Equal("tool_use", response.StopReason);
    }

    [Fact]
    public async Task Complete_SerialisesToolsInAnthropicShape()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(SearchTool()), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var tool = document.RootElement.GetProperty("tools")[0];
        Assert.Equal("search_commits", tool.GetProperty("name").GetString());
        Assert.True(tool.TryGetProperty("input_schema", out _));
    }

    [Fact]
    public async Task Complete_MarksTheSystemPromptForCaching()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);
        await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var system = document.RootElement.GetProperty("system")[0];
        Assert.Equal("ephemeral", system.GetProperty("cache_control").GetProperty("type").GetString());
    }

    [Fact]
    public async Task Complete_SerialisesToolResultsAsUserContentBlocks()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(TextResponse);

        var request = new ChatRequest(
            "system",
            [
                ChatMessage.User("question"),
                ChatMessage.AssistantToolCalls([new ToolCall("toolu_01", "search_commits",
                    JsonDocument.Parse("""{"query":"x"}""").RootElement)]),
                ChatMessage.UserToolResults([new ToolResult("toolu_01", "3 commits found", IsError: false)])
            ],
            [SearchTool()], 1024);

        await Create(handler).CompleteAsync(request, TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);

        var messages = document.RootElement.GetProperty("messages");
        var last = messages[messages.GetArrayLength() - 1];

        Assert.Equal("user", last.GetProperty("role").GetString());
        var block = last.GetProperty("content")[0];
        Assert.Equal("tool_result", block.GetProperty("type").GetString());
        Assert.Equal("toolu_01", block.GetProperty("tool_use_id").GetString());
    }

    [Fact]
    public async Task Complete_HttpError_ThrowsProviderUnavailable()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.ServiceUnavailable, "{\"error\":\"overloaded\"}");

        var exception = await Assert.ThrowsAsync<ProviderUnavailableException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));

        Assert.Equal("anthropic", exception.ProviderName);
    }

    [Fact]
    public async Task Complete_RateLimited_ThrowsProviderUnavailable()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.TooManyRequests, "{}");

        await Assert.ThrowsAsync<ProviderUnavailableException>(async () =>
            await Create(handler).CompleteAsync(Request(), TestContext.Current.CancellationToken));
    }
}
