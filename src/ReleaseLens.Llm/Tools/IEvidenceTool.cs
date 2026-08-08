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

    public static DateTimeOffset? Date(JsonElement arguments, string name)
        => String(arguments, name) is { } text && DateTimeOffset.TryParse(text, out var parsed)
            ? parsed
            : null;

    public static JsonElement Schema(string json) => JsonDocument.Parse(json).RootElement.Clone();
}
