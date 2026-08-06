using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ReleaseLens.Llm.Providers;

public sealed class AnthropicOptions
{
    public const string SectionName = "Anthropic";

    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = "claude-sonnet-5";
    public string BaseUrl { get; set; } = "https://api.anthropic.com/";
    public int MaxTokens { get; set; } = 2048;
}

public sealed class AnthropicChatProvider : IChatProvider
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _client;
    private readonly AnthropicOptions _options;

    public string Name => "anthropic";

    public AnthropicChatProvider(HttpClient client, AnthropicOptions options)
    {
        _client = client;
        _options = options;

        _client.BaseAddress ??= new Uri(options.BaseUrl);
        _client.DefaultRequestHeaders.TryAddWithoutValidation("x-api-key", options.ApiKey);
        _client.DefaultRequestHeaders.TryAddWithoutValidation("anthropic-version", "2023-06-01");
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var payload = BuildPayload(request);

        HttpResponseMessage response;
        try
        {
            response = await _client.PostAsJsonAsync("/v1/messages", payload, JsonOptions, cancellationToken);
        }
        catch (HttpRequestException transport)
        {
            throw new ProviderUnavailableException(Name, "Anthropic transport failure.", transport);
        }
        catch (TaskCanceledException timeout) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ProviderUnavailableException(Name, "Anthropic request timed out.", timeout);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(cancellationToken);

                // 5xx and 429 are "try the other provider"; 4xx is our bug and must surface.
                if (response.StatusCode is HttpStatusCode.TooManyRequests || (int)response.StatusCode >= 500)
                {
                    throw new ProviderUnavailableException(Name, $"Anthropic returned {(int)response.StatusCode}: {body}");
                }

                throw new InvalidOperationException($"Anthropic rejected the request ({(int)response.StatusCode}): {body}");
            }

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return Parse(document.RootElement);
        }
    }

    private JsonObject BuildPayload(ChatRequest request)
    {
        var payload = new JsonObject
        {
            ["model"] = request.Model,
            ["max_tokens"] = request.MaxTokens,

            // System prompt as a block array so cache_control can be attached. Whether it
            // actually caches depends on the model's minimum prefix — check
            // usage.cache_read_input_tokens rather than assuming.
            ["system"] = new JsonArray
            {
                new JsonObject
                {
                    ["type"] = "text",
                    ["text"] = request.SystemPrompt,
                    ["cache_control"] = new JsonObject { ["type"] = "ephemeral" }
                }
            }
        };

        if (request.Tools.Count > 0)
        {
            var tools = new JsonArray();
            foreach (var tool in request.Tools)
            {
                tools.Add(new JsonObject
                {
                    ["name"] = tool.Name,
                    ["description"] = tool.Description,
                    ["input_schema"] = JsonNode.Parse(tool.JsonSchema.GetRawText())
                });
            }

            payload["tools"] = tools;
        }

        var messages = new JsonArray();
        foreach (var message in request.Messages)
        {
            messages.Add(BuildMessage(message));
        }

        payload["messages"] = messages;
        return payload;
    }

    private static JsonObject BuildMessage(ChatMessage message)
    {
        var content = new JsonArray();

        if (message.Results is { Count: > 0 })
        {
            foreach (var result in message.Results)
            {
                content.Add(new JsonObject
                {
                    ["type"] = "tool_result",
                    ["tool_use_id"] = result.ToolCallId,
                    ["content"] = result.Content,
                    ["is_error"] = result.IsError
                });
            }

            return new JsonObject { ["role"] = "user", ["content"] = content };
        }

        if (!string.IsNullOrEmpty(message.Text))
        {
            content.Add(new JsonObject { ["type"] = "text", ["text"] = message.Text });
        }

        if (message.ToolCalls is { Count: > 0 })
        {
            foreach (var call in message.ToolCalls)
            {
                content.Add(new JsonObject
                {
                    ["type"] = "tool_use",
                    ["id"] = call.Id,
                    ["name"] = call.Name,
                    ["input"] = JsonNode.Parse(call.Arguments.GetRawText())
                });
            }
        }

        return new JsonObject
        {
            ["role"] = message.Role == ChatRole.Assistant ? "assistant" : "user",
            ["content"] = content
        };
    }

    private ChatResponse Parse(JsonElement root)
    {
        string? text = null;
        var toolCalls = new List<ToolCall>();

        foreach (var block in root.GetProperty("content").EnumerateArray())
        {
            switch (block.GetProperty("type").GetString())
            {
                case "text":
                    text = text is null
                        ? block.GetProperty("text").GetString()
                        : text + "\n" + block.GetProperty("text").GetString();
                    break;

                case "tool_use":
                    toolCalls.Add(new ToolCall(
                        block.GetProperty("id").GetString()!,
                        block.GetProperty("name").GetString()!,
                        block.GetProperty("input").Clone()));
                    break;
            }
        }

        var usage = root.GetProperty("usage");

        return new ChatResponse(
            text,
            toolCalls,
            new TokenUsage(
                ReadInt(usage, "input_tokens"),
                ReadInt(usage, "output_tokens"),
                ReadInt(usage, "cache_read_input_tokens"),
                ReadInt(usage, "cache_creation_input_tokens")),
            root.GetProperty("stop_reason").GetString() ?? "unknown",
            root.GetProperty("model").GetString() ?? _options.Model,
            Name);
    }

    private static int ReadInt(JsonElement element, string name)
        => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : 0;
}
