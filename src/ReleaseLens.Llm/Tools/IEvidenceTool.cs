using System.Globalization;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;

namespace ReleaseLens.Llm.Tools;

/// <summary>One artefact the answer is allowed to cite, produced by the tool that found it.</summary>
public sealed record EvidenceCitation(EntityType Type, string EntityKey, string Title, string Url);

public sealed record ToolExecutionResult(string Content, IReadOnlyList<EvidenceCitation> Citations, bool IsError)
{
    public static ToolExecutionResult Ok(string content, IReadOnlyList<EvidenceCitation> citations)
        => new(content, citations, false);

    /// <summary>
    /// Errors go back to the model as content, not as exceptions. A tool that throws
    /// ends the turn; a tool that reports "no release tagged v9.9" lets the model recover.
    /// </summary>
    public static ToolExecutionResult Error(string message) => new(message, [], true);
}

public interface IEvidenceTool
{
    string Name { get; }
    string Description { get; }
    JsonElement JsonSchema { get; }

    Task<ToolExecutionResult> ExecuteAsync(TenantScope scope, JsonElement arguments, CancellationToken cancellationToken);
}

internal static class JsonArgs
{
    public static string? String(JsonElement arguments, string name)
        => arguments.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    public static int? Int(JsonElement arguments, string name)
        => arguments.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : null;

    /// <summary>
    /// Reads an array-of-strings argument, distinguishing "absent" from "present but
    /// unusable". Returns false with a message for the latter.
    /// </summary>
    /// <remarks>
    /// Deliberately strict, for the same reason <see cref="AggregateWindow.TryRead"/> is:
    /// this feeds a filter on a count. Shrugging off a malformed or empty <c>labels</c>
    /// would answer "how many bugs" with the number of issues of every kind — a precise,
    /// checkable-looking figure for a question nobody asked. An empty array is rejected
    /// rather than read as "no filter", because a caller that wrote the argument at all
    /// meant to narrow something.
    /// </remarks>
    public static bool TryStringArray(
        JsonElement arguments, string name, out string[]? values, out string? error)
    {
        values = null;
        error = null;

        if (!arguments.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        // A bare string is the likeliest malformed shape a model produces for an array
        // argument, so it is worth accepting rather than bouncing a recoverable turn.
        if (element.ValueKind == JsonValueKind.String)
        {
            var single = element.GetString();
            if (string.IsNullOrWhiteSpace(single))
            {
                error = $"'{name}' was empty. Omit it entirely to apply no filter.";
                return false;
            }

            values = [single];
            return true;
        }

        if (element.ValueKind != JsonValueKind.Array)
        {
            error = $"'{name}' must be an array of strings, for example [\"bug\"].";
            return false;
        }

        var items = new List<string>(element.GetArrayLength());
        foreach (var item in element.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(item.GetString()))
            {
                error = $"'{name}' must contain only non-empty strings, for example [\"bug\"].";
                return false;
            }

            items.Add(item.GetString()!);
        }

        if (items.Count == 0)
        {
            error = $"'{name}' was an empty array, which filters nothing. " +
                    "Omit it entirely to apply no filter, or name at least one label.";
            return false;
        }

        values = [.. items];
        return true;
    }

    /// <summary>
    /// Reads a date argument as an instant in UTC, returning null when it is absent or
    /// unparseable.
    /// </summary>
    /// <remarks>
    /// <para>
    /// The styles are not optional and are the same pair <see cref="AggregateWindow.TryRead"/>
    /// uses. Default parsing reads a bare "2024-01-01" in the process's local zone, so the
    /// same question put to a container in Sydney and one in London covers two windows
    /// eleven hours apart — and a container's zone is rarely the one anyone reasoned about.
    /// Every caller sends this value straight into a <c>timestamptz</c> comparison, where
    /// the shift is silent: the query still returns rows, and they are the wrong ones only
    /// near the edges.
    /// </para>
    /// <para>
    /// Returning null for unparseable text is deliberately kept. These callers are
    /// retrieval tools, where a dropped date filter widens the result set and the model
    /// reads what came back; <see cref="AggregateWindow.TryRead"/> errors instead because a
    /// dropped filter on a count cannot be seen in the number.
    /// </para>
    /// </remarks>
    public static DateTimeOffset? Date(JsonElement arguments, string name)
        => String(arguments, name) is { } text
           && DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture,
               DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed)
            ? parsed
            : null;

    public static JsonElement Schema(string json) => JsonDocument.Parse(json).RootElement.Clone();
}
