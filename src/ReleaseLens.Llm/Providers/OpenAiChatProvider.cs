using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ReleaseLens.Llm.Providers;

public sealed class OpenAiOptions
{
    public const string SectionName = "OpenAi";

    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = "gpt-4o";

    /// <summary>
    /// Any endpoint speaking the OpenAI wire format. Point this at
    /// http://localhost:11434/v1/ for Ollama, or a vLLM or LM Studio address,
    /// and the whole query path runs with no data leaving the network.
    /// </summary>
    public string BaseUrl { get; set; } = "https://api.openai.com/v1/";

    public int MaxTokens { get; set; } = 2048;
}

public sealed class OpenAiChatProvider : IChatProvider
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _client;
    private readonly OpenAiOptions _options;

    public string Name => "openai";

    public OpenAiChatProvider(HttpClient client, OpenAiOptions options)
    {
        _client = client;
        _options = options;

        _client.BaseAddress ??= new Uri(options.BaseUrl);

        // Local runtimes ignore the header, and sending "Bearer " with an empty
        // value can make some of them reject the request outright.
        if (!string.IsNullOrEmpty(options.ApiKey))
        {
            _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", options.ApiKey);
        }
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var payload = BuildPayload(request);

        HttpResponseMessage response;
        try
        {
            response = await _client.PostAsJsonAsync("chat/completions", payload, JsonOptions, cancellationToken);
        }
        catch (HttpRequestException transport)
        {
            throw new ProviderUnavailableException(Name, "OpenAI-compatible transport failure.", transport);
        }
        catch (TaskCanceledException timeout) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ProviderUnavailableException(Name, "OpenAI-compatible request timed out.", timeout);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(cancellationToken);

                if (response.StatusCode is HttpStatusCode.TooManyRequests || (int)response.StatusCode >= 500)
                {
                    throw new ProviderUnavailableException(Name, $"Provider returned {(int)response.StatusCode}: {body}");
                }

                throw new InvalidOperationException($"Provider rejected the request ({(int)response.StatusCode}): {body}");
            }

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return Parse(document.RootElement);
        }
    }

    private JsonObject BuildPayload(ChatRequest request)
    {
        var messages = new JsonArray
        {
            new JsonObject { ["role"] = "system", ["content"] = request.SystemPrompt }
        };

        foreach (var message in request.Messages)
        {
            if (message.ToolResults is { Count: > 0 })
            {
                // Each tool result is its own message in the OpenAI format.
                foreach (var result in message.ToolResults)
                {
                    messages.Add(new JsonObject
                    {
                        ["role"] = "tool",
                        ["tool_call_id"] = result.ToolCallId,
                        ["content"] = result.Content
                    });
                }

                continue;
            }

            var entry = new JsonObject
            {
                ["role"] = message.Role == ChatRole.Assistant ? "assistant" : "user",
                ["content"] = message.Text
            };

            if (message.ToolCalls is { Count: > 0 })
            {
                var calls = new JsonArray();
                foreach (var call in message.ToolCalls)
                {
                    calls.Add(new JsonObject
                    {
                        ["id"] = call.Id,
                        ["type"] = "function",
                        ["function"] = new JsonObject
                        {
                            ["name"] = call.Name,
                            ["arguments"] = call.Arguments.GetRawText()
                        }
                    });
                }

                entry["tool_calls"] = calls;
            }

            messages.Add(entry);
        }

        var payload = new JsonObject
        {
            ["model"] = request.Model,
            ["max_tokens"] = request.MaxTokens,
            ["messages"] = messages
        };

        if (request.Tools.Count > 0)
        {
            var tools = new JsonArray();
            foreach (var tool in request.Tools)
            {
                tools.Add(new JsonObject
                {
                    ["type"] = "function",
                    ["function"] = new JsonObject
                    {
                        ["name"] = tool.Name,
                        ["description"] = tool.Description,
                        ["parameters"] = JsonNode.Parse(tool.JsonSchema.GetRawText())
                    }
                });
            }

            payload["tools"] = tools;
        }

        return payload;
    }

    private ChatResponse Parse(JsonElement root)
    {
        var choice = root.GetProperty("choices")[0];
        var message = choice.GetProperty("message");

        var text = message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String
            ? content.GetString()
            : null;

        var toolCalls = new List<ToolCall>();
        if (message.TryGetProperty("tool_calls", out var calls) && calls.ValueKind == JsonValueKind.Array)
        {
            foreach (var call in calls.EnumerateArray())
            {
                var function = call.GetProperty("function");
                var rawArguments = function.GetProperty("arguments").GetString() ?? "{}";

                // OpenAI sends arguments as a JSON-encoded string, not an object. Clone the
                // parsed element before `parsed` is disposed, or it reads freed memory.
                using var parsed = JsonDocument.Parse(rawArguments);

                toolCalls.Add(new ToolCall(
                    call.GetProperty("id").GetString()!,
                    function.GetProperty("name").GetString()!,
                    parsed.RootElement.Clone()));
            }
        }

        var usage = root.TryGetProperty("usage", out var usageElement) ? usageElement : default;

        var cachedTokens = 0;
        if (usage.ValueKind == JsonValueKind.Object &&
            usage.TryGetProperty("prompt_tokens_details", out var details) &&
            details.TryGetProperty("cached_tokens", out var cached))
        {
            cachedTokens = cached.GetInt32();
        }

        return new ChatResponse(
            text,
            toolCalls,
            new TokenUsage(
                ReadInt(usage, "prompt_tokens"),
                ReadInt(usage, "completion_tokens"),
                cachedTokens,
                0),
            choice.GetProperty("finish_reason").GetString() ?? "unknown",
            root.TryGetProperty("model", out var model) ? model.GetString() ?? _options.Model : _options.Model,
            Name);
    }

    private static int ReadInt(JsonElement element, string name)
        => element.ValueKind == JsonValueKind.Object &&
           element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : 0;
}
