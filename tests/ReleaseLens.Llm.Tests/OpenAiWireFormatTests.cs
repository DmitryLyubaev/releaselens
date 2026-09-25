using System;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class OpenAiWireFormatTests
{
    private static ToolDefinition SearchTool() => new(
        "search_commits", "Search commits.",
        JsonDocument.Parse("""{"type":"object","properties":{"query":{"type":"string"}}}""").RootElement);

    private static ChatRequest ToolRoundTrip(ToolResult result) => new(
        "system",
        [
            ChatMessage.User("What changed in release v9.9?"),
            ChatMessage.AssistantToolCalls([new ToolCall("call_01", "diff_between_releases",
                JsonDocument.Parse("""{"from_tag":"v9.8","to_tag":"v9.9"}""").RootElement)]),
            ChatMessage.UserToolResults([result])
        ],
        [SearchTool()], 1024);

    private static JsonElement LastMessage(JsonObject payload)
    {
        using var document = JsonDocument.Parse(payload.ToJsonString());
        var messages = document.RootElement.GetProperty("messages");
        return messages[messages.GetArrayLength() - 1].Clone();
    }

    [Fact]
    public void BuildPayload_FailedToolResult_KeepsItsErrorInTheContent()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "No release tagged 'v9.9' exists in the indexed evidence.", IsError: true)),
            "gpt-4.1-mini");

        var last = LastMessage(payload);

        Assert.Equal("tool", last.GetProperty("role").GetString());
        Assert.Equal("call_01", last.GetProperty("tool_call_id").GetString());
        Assert.Equal("Error: No release tagged 'v9.9' exists in the indexed evidence.",
            last.GetProperty("content").GetString());
    }

    [Fact]
    public void BuildPayload_SuccessfulToolResult_IsSentAsItIs()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
            "gpt-4.1-mini");

        Assert.Equal("3 commits found", LastMessage(payload).GetProperty("content").GetString());
    }

    [Fact]
    public void BuildPayload_SendsTheModelItIsGiven()
    {
        var payload = OpenAiWireFormat.BuildPayload(
            ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
            "my-deployment");

        Assert.Equal("my-deployment", payload["model"]!.GetValue<string>());
        Assert.Equal(1024, payload["max_tokens"]!.GetValue<int>());
    }

    [Fact]
    public void Parse_ReadsTextFinishReasonModelAndTheGivenProviderName()
    {
        using var document = JsonDocument.Parse("""
        {
          "model": "gpt-4.1-mini-2025-04-14",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "Release 1.30 fixed the planner." } }],
          "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                     "prompt_tokens_details": { "cached_tokens": 1024 } }
        }
        """);

        var response = OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "fallback-model");

        Assert.Equal("Release 1.30 fixed the planner.", response.Text);
        Assert.Empty(response.ToolCalls);
        Assert.Equal("stop", response.StopReason);
        Assert.Equal("gpt-4.1-mini-2025-04-14", response.Model);
        Assert.Equal("azure-openai", response.Provider);
    }

    [Fact]
    public void Parse_NoModelInTheResponse_ReportsTheFallbackModel()
    {
        using var document = JsonDocument.Parse("""
        {
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "ok" } }],
          "usage": { "prompt_tokens": 10, "completion_tokens": 2 }
        }
        """);

        Assert.Equal("fallback-model",
            OpenAiWireFormat.Parse(document.RootElement, "openai", "fallback-model").Model);
    }

    [Fact]
    public void Parse_ReadsToolCallsAndTheirJsonEncodedArguments()
    {
        using var document = JsonDocument.Parse("""
        {
          "model": "gpt-4.1-mini",
          "choices": [{ "index": 0, "finish_reason": "tool_calls",
            "message": { "role": "assistant", "content": null,
              "tool_calls": [{ "id": "call_01", "type": "function",
                "function": { "name": "search_commits", "arguments": "{\"query\":\"release 1.30\"}" } }] } }],
          "usage": { "prompt_tokens": 900, "completion_tokens": 60 }
        }
        """);

        var response = OpenAiWireFormat.Parse(document.RootElement, "openai", "gpt-4.1-mini");

        Assert.Null(response.Text);
        var call = Assert.Single(response.ToolCalls);
        Assert.Equal("call_01", call.Id);
        Assert.Equal("search_commits", call.Name);
        Assert.Equal("release 1.30", call.Arguments.GetProperty("query").GetString());
    }

    [Fact]
    public void ParseUsage_ReadsPromptCompletionAndCachedTokens()
    {
        using var document = JsonDocument.Parse("""
        { "usage": { "prompt_tokens": 1200, "completion_tokens": 45,
                     "prompt_tokens_details": { "cached_tokens": 1024 } } }
        """);

        var usage = OpenAiWireFormat.ParseUsage(document.RootElement);

        Assert.Equal(1200, usage.InputTokens);
        Assert.Equal(45, usage.OutputTokens);
        Assert.Equal(1024, usage.CacheReadInputTokens);
        Assert.Equal(0, usage.CacheCreationInputTokens);
    }

    [Theory]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [], "usage": { "prompt_tokens": 10, "completion_tokens": 0 } }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "usage": { "prompt_tokens": 10, "completion_tokens": 0 } }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": null }""")]
    public void Parse_NoChoices_ThrowsNamingTheProvider(string body)
    {
        using var document = JsonDocument.Parse(body);

        var exception = Assert.Throws<InvalidOperationException>(() =>
            OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "gpt-4.1-mini"));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("no choices", exception.Message, StringComparison.Ordinal);
    }

    // A success with no usage would otherwise count as zero tokens and be priced at $0.
    [Theory]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }] }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }], "usage": null }""")]
    [InlineData("""{ "model": "gpt-4.1-mini", "choices": [{ "index": 0, "finish_reason": "stop", "message": { "role": "assistant", "content": "ok" } }], "usage": "none" }""")]
    public void Parse_NoUsage_ThrowsNamingTheProvider(string body)
    {
        using var document = JsonDocument.Parse(body);

        var exception = Assert.Throws<InvalidOperationException>(() =>
            OpenAiWireFormat.Parse(document.RootElement, "azure-openai", "gpt-4.1-mini"));

        Assert.Contains("azure-openai", exception.Message, StringComparison.Ordinal);
        Assert.Contains("no usage", exception.Message, StringComparison.Ordinal);
    }

    private static OpenAiChatProvider OpenAi(StubHttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("https://api.openai.com/v1/") },
            new OpenAiOptions { ApiKey = "sk-test", Model = "gpt-4.1-mini", BaseUrl = "https://api.openai.com/v1/" });

    [Fact]
    public async Task OpenAiChatProvider_SendsAFailedToolResultWithItsError()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        {
          "model": "gpt-4.1-mini",
          "choices": [{ "index": 0, "finish_reason": "stop",
            "message": { "role": "assistant", "content": "There is no release v9.9." } }],
          "usage": { "prompt_tokens": 300, "completion_tokens": 8 }
        }
        """);

        await OpenAi(handler).CompleteAsync(
            ToolRoundTrip(new ToolResult("call_01", "No release tagged 'v9.9' exists in the indexed evidence.", IsError: true)),
            TestContext.Current.CancellationToken);

        var body = await handler.Requests[0].Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var document = JsonDocument.Parse(body);
        var messages = document.RootElement.GetProperty("messages");

        Assert.Equal("Error: No release tagged 'v9.9' exists in the indexed evidence.",
            messages[messages.GetArrayLength() - 1].GetProperty("content").GetString());
    }

    [Fact]
    public async Task OpenAiChatProvider_EmptyChoices_ThrowsNamingTheProvider()
    {
        var handler = new StubHttpMessageHandler().EnqueueJson("""
        { "model": "gpt-4.1-mini", "choices": [], "usage": { "prompt_tokens": 300, "completion_tokens": 0 } }
        """);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await OpenAi(handler).CompleteAsync(
                ToolRoundTrip(new ToolResult("call_01", "3 commits found", IsError: false)),
                TestContext.Current.CancellationToken));

        Assert.Contains("openai", exception.Message, StringComparison.Ordinal);
    }
}
