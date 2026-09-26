using System;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Azure.Core;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The claim at the wire: going keyless on Azure changed how requests are authenticated, not
/// what is sent. Both providers are driven with the same conversation and their requests are
/// compared as captured.
/// </summary>
public class WireEquivalenceTests
{
    private const string OpenAiModel = "gpt-4.1-mini";
    private const string AzureDeployment = "releaselens-chat";

    private sealed class FixedCredential(string token, DateTimeOffset expiresOn) : TokenCredential
    {
        public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => new(token, expiresOn);

        public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken)
            => new(new AccessToken(token, expiresOn));
    }

    private const string Answer = """
    {
      "model": "gpt-4.1-mini-2025-04-14",
      "choices": [{ "index": 0, "finish_reason": "stop",
        "message": { "role": "assistant", "content": "Release 1.30 fixed the planner; release v9.9 does not exist." } }],
      "usage": { "prompt_tokens": 1500, "completion_tokens": 20 }
    }
    """;

    // Most of the wire format at once: a system prompt, a user turn, an assistant turn with two
    // tool calls, one successful and one failed tool result, and two tool definitions (no
    // assistant text turn).
    private static ChatRequest Conversation() => new(
        "You answer questions about a repository.",
        [
            ChatMessage.User("What changed in release 1.30, and in v9.9?"),
            ChatMessage.AssistantToolCalls(
            [
                new ToolCall("call_01", "search_commits", JsonDocument.Parse("""{"query":"release 1.30"}""").RootElement),
                new ToolCall("call_02", "diff_between_releases",
                    JsonDocument.Parse("""{"from_tag":"v9.8","to_tag":"v9.9"}""").RootElement)
            ]),
            ChatMessage.UserToolResults(
            [
                new ToolResult("call_01", "3 commits found", false),
                new ToolResult("call_02", "No release tagged 'v9.9' exists in the indexed evidence.", true)
            ])
        ],
        [
            new ToolDefinition("search_commits", "Search commits.",
                JsonDocument.Parse("""{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}""").RootElement),
            new ToolDefinition("diff_between_releases", "Compare two releases.",
                JsonDocument.Parse("""{"type":"object","properties":{"from_tag":{"type":"string"},"to_tag":{"type":"string"}}}""").RootElement)
        ],
        1024);

    private static async Task<HttpRequestMessage> SendThroughOpenAi(ChatRequest request)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(Answer);
        var provider = new OpenAiChatProvider(
            new HttpClient(handler),
            new OpenAiOptions { ApiKey = "sk-test", Model = OpenAiModel, BaseUrl = "https://api.openai.com/v1/" });

        await provider.CompleteAsync(request, TestContext.Current.CancellationToken);
        return Assert.Single(handler.Requests);
    }

    private static async Task<HttpRequestMessage> SendThroughAzure(ChatRequest request)
    {
        var handler = new StubHttpMessageHandler().EnqueueJson(Answer);
        var time = new FakeTimeProvider();
        var cache = new EntraTokenCache(
            new FixedCredential("entra-access-token", time.GetUtcNow().AddHours(1)), "https://ai.azure.com/.default", time);

        var provider = new AzureOpenAiChatProvider(
            new HttpClient(new EntraTokenHandler(cache) { InnerHandler = handler }),
            new AzureOpenAiOptions
            {
                BaseUrl = "https://releaselens-aoai.openai.azure.com/openai/v1/",
                Deployment = AzureDeployment
            },
            time,
            NullLogger<AzureOpenAiChatProvider>.Instance);

        await provider.CompleteAsync(request, TestContext.Current.CancellationToken);
        return Assert.Single(handler.Requests);
    }

    private static async Task<string> Body(HttpRequestMessage request)
        => await request.Content!.ReadAsStringAsync(TestContext.Current.CancellationToken);

    private static string WithoutModel(string body)
    {
        var node = JsonNode.Parse(body)!.AsObject();
        Assert.True(node.Remove("model"));
        return node.ToJsonString();
    }

    private static string[] HeadersExceptAuthorization(HttpRequestMessage request)
        => request.Headers
            .Concat(request.Content!.Headers)
            .Where(header => !string.Equals(header.Key, "Authorization", StringComparison.OrdinalIgnoreCase))
            .Select(header => $"{header.Key}: {string.Join(", ", header.Value)}")
            .Order(StringComparer.OrdinalIgnoreCase)
            .ToArray();

    [Fact]
    public async Task SameConversation_AzureAndOpenAiSendTheSameBody_ExceptTheModel()
    {
        var request = Conversation();

        var openAiBody = await Body(await SendThroughOpenAi(request));
        var azureBody = await Body(await SendThroughAzure(request));

        Assert.Equal(WithoutModel(openAiBody), WithoutModel(azureBody));

        // Stronger than the parsed comparison: with the model value swapped, the bytes match.
        Assert.Equal(openAiBody, azureBody.Replace(
            $"\"model\":\"{AzureDeployment}\"", $"\"model\":\"{OpenAiModel}\"", StringComparison.Ordinal));
    }

    [Fact]
    public async Task SameConversation_AzureAndOpenAiSendTheSameHeaders_ExceptAuthorization()
    {
        var request = Conversation();

        var openAi = await SendThroughOpenAi(request);
        var azure = await SendThroughAzure(request);

        Assert.Equal(HeadersExceptAuthorization(openAi), HeadersExceptAuthorization(azure));

        // Both authenticate with a bearer token; only its source differs.
        Assert.Equal("Bearer", openAi.Headers.Authorization!.Scheme);
        Assert.Equal("Bearer", azure.Headers.Authorization!.Scheme);
        Assert.Equal("entra-access-token", azure.Headers.Authorization.Parameter);
        Assert.False(azure.Headers.Contains("api-key"));
    }
}
