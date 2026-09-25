using System.Text.Json;
using System.Text.Json.Nodes;

namespace ReleaseLens.Llm.Providers;

/// <summary>
/// The OpenAI chat-completions wire format: the request body and the response. One copy,
/// shared by every provider that speaks it, so two providers given the same conversation
/// send the same bytes apart from what they pass as <c>model</c>. What differs between
/// them (address, authentication, status handling) stays in the provider.
/// </summary>
internal static class OpenAiWireFormat
{
    public static JsonObject BuildPayload(ChatRequest request, string model)
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
                        ["content"] = ToolContent(result)
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
            ["model"] = model,
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

    public static TokenUsage ParseUsage(JsonElement root)
    {
        var usage = root.TryGetProperty("usage", out var usageElement) ? usageElement : default;

        var cachedTokens = 0;
        if (usage.ValueKind == JsonValueKind.Object &&
            usage.TryGetProperty("prompt_tokens_details", out var details) &&
            details.TryGetProperty("cached_tokens", out var cached))
        {
            cachedTokens = cached.GetInt32();
        }

        return new TokenUsage(
            ProviderHttp.ReadInt(usage, "prompt_tokens"),
            ProviderHttp.ReadInt(usage, "completion_tokens"),
            cachedTokens,
            0);
    }

    public static ChatResponse Parse(JsonElement root, string providerName, string fallbackModel)
    {
        // A 200 with nothing to read is a malformed reply, not an empty answer. Indexing into
        // it would throw KeyNotFoundException or IndexOutOfRangeException, naming no provider.
        if (!root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no choices.");
        }

        // A success with no usage would count as zero tokens and be priced at $0, with nothing
        // to show that it was not free.
        if (!root.TryGetProperty("usage", out var usageElement) || usageElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException($"{providerName} returned a completion with no usage.");
        }

        var usage = ParseUsage(root);
        var choice = choices[0];

        // Checked before the message is read: a filtered completion can still carry partial
        // text, and none of it may become an answer.
        if (IsContentFiltered(choice))
        {
            throw new ContentFilteredException(providerName, ContentFilterStage.Completion, usage);
        }

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

        return new ChatResponse(
            text,
            toolCalls,
            usage,
            choice.GetProperty("finish_reason").GetString() ?? "unknown",
            root.TryGetProperty("model", out var model) ? model.GetString() ?? fallbackModel : fallbackModel,
            providerName);
    }

    /// <summary>
    /// The chat-completions tool message has no error field, unlike Anthropic's
    /// <c>is_error</c>, so a failed call is marked in its text. Without the prefix a failed
    /// call and a successful one are indistinguishable on the wire.
    /// </summary>
    private static string ToolContent(ToolResult result)
        => result.IsError ? "Error: " + result.Content : result.Content;

    private static bool IsContentFiltered(JsonElement choice)
        => choice.TryGetProperty("finish_reason", out var finishReason)
           && finishReason.ValueKind == JsonValueKind.String
           && finishReason.ValueEquals("content_filter");
}
