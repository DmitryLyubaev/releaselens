using System.Globalization;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;

namespace ReleaseLens.Llm.Tools;

/// <summary>One artefact the answer is allowed to cite, produced by the tool that found it.</summary>
public sealed record EvidenceCitation(EntityType Type, string EntityKey, string Title, string Url);

/// <summary>
/// One fragment of the evidence text a tool put in front of the model, attributed to the
/// artefact it came from. This is what makes a citation checkable: <c>issue:14111</c> names
/// an artefact, and only its text says whether a claim about it is true.
/// </summary>
/// <remarks>
/// Deliberately NOT a field on <see cref="EvidenceCitation"/>. A citation identifies an
/// artefact and is deduplicated to exactly one entry per artefact, so text riding on it
/// would be discarded for every fragment after the first: a commit split across four chunks
/// would surface only chunk one, and a claim resting on chunk three would read as
/// unsupported by a reader who was shown chunk one. Excerpts are therefore a separate,
/// undeduplicated list — several may share one (Type, EntityKey), and all of them belong to
/// that artefact's marker.
/// </remarks>
public sealed record EvidenceExcerpt(EntityType Type, string EntityKey, string Text);

/// <summary>
/// Whether a tool result is a citable artefact or a computed figure.
/// </summary>
/// <remarks>
/// This cannot be inferred from the citation list: a computed aggregate carries no citations
/// by design, and so does a search that matched nothing. <see cref="Computed"/> means the
/// result is a figure rather than an artefact — <see cref="CountEvidenceTool"/> is its only
/// producer. <see cref="ListReleasesTool"/> runs an exhaustive query rather than a sampled
/// one, but what it returns are citable release rows, so it reports <see cref="Evidence"/>
/// with real citations, the same as any retrieval tool. The kind is therefore a property of
/// what the result *is*, not of whether citations happen to be present, so an external
/// consumer can honour it without reading anything this process keeps to itself.
/// </remarks>
public enum ResultKind
{
    Evidence,
    Computed
}

/// <summary>
/// How much of the matching evidence this result actually contains. Present on evidence
/// results only. A caller outside this process has no prompt telling it not to count the
/// rows it was handed, so the result says how many it was not handed.
/// </summary>
/// <remarks>
/// What one unit of "Returned"/"Matched" means is chosen per tool, not fixed by this record.
/// <see cref="FindRegressionsTool"/>, <see cref="ListReleasesTool"/> and
/// <see cref="DiffBetweenReleasesTool"/> count artefacts (one issue, one release, one
/// commit), because every row each of those tools returns is exactly one artefact, and the
/// corpus-wide match count each already computes (<c>CountRegressionCandidatesAsync</c>,
/// <c>CountReleasesAsync</c>, <c>CountCommitsBetweenAsync</c>) is over that same table with
/// the same filter. <see cref="SearchCommitsTool"/> is different: it collapses several
/// chunks into one citation per artefact, but the only corpus-wide match figure it holds -
/// <c>RetrievalResult.TextMatchCount</c> - counts chunks that matched the full-text query,
/// not distinct artefacts. Pairing a deduplicated citation count against a chunk-level match
/// count would produce a number in no unit at all, so for that tool both
/// <see cref="Returned"/> and <see cref="Matched"/> are chunk counts, not artefact counts.
/// </remarks>
/// <param name="Returned">How many of this tool's unit (see remarks) are in this result.</param>
/// <param name="Matched">How many of that same unit match the query under the same filters,
/// in the whole corpus. Never an estimate and never a lower bound.</param>
/// <param name="Truncated">True when <paramref name="Matched"/> exceeds
/// <paramref name="Returned"/>.</param>
public sealed record ToolResultBounds(int Returned, int Matched, bool Truncated);

/// <summary>
/// What the corpus actually spans, against the window that was asked about. Present on
/// computed results only. The corpus does not cover all of history, so a count can be true
/// of the evidence and false of the repository; <see cref="CompleteForWindow"/> is how a
/// caller tells those apart.
/// </summary>
public sealed record ToolCoverage(
    DateTimeOffset? Earliest, DateTimeOffset? Latest, bool CompleteForWindow);

public sealed record ToolExecutionResult(
    string Content,
    IReadOnlyList<EvidenceCitation> Citations,
    bool IsError,
    IReadOnlyList<EvidenceExcerpt> Excerpts,
    ResultKind Kind = ResultKind.Evidence,
    ToolResultBounds? Bounds = null,
    ToolCoverage? Coverage = null)
{
    /// <summary>
    /// For results whose artefacts carry no attributable text — an empty search, or a
    /// computed aggregate that cites nothing.
    /// </summary>
    public static ToolExecutionResult Ok(string content, IReadOnlyList<EvidenceCitation> citations)
        => new(content, citations, false, []);

    public static ToolExecutionResult Ok(
        string content,
        IReadOnlyList<EvidenceCitation> citations,
        IReadOnlyList<EvidenceExcerpt> excerpts)
        => new(content, citations, false, excerpts);

    /// <summary>
    /// A figure computed over the corpus rather than retrieved from it. Carries no citations
    /// and no excerpts, because a computed figure is not an artefact.
    /// </summary>
    public static ToolExecutionResult Computed(string content)
        => new(content, [], false, [], ResultKind.Computed);

    /// <summary>
    /// Same as <see cref="Computed(string)"/>, plus the corpus coverage the content already
    /// describes in prose. There is deliberately no way to attach a <see cref="ToolCoverage"/>
    /// without also carrying <see cref="ResultKind.Computed"/>: coverage answers "what span of
    /// history is this figure true over", a question that only makes sense for a computed
    /// figure, never for a page of retrieved artefacts.
    /// </summary>
    public static ToolExecutionResult Computed(string content, ToolCoverage coverage)
        => new(content, [], false, [], ResultKind.Computed, Bounds: null, Coverage: coverage);

    /// <summary>
    /// Errors go back to the model as content, not as exceptions. A tool that throws
    /// ends the turn; a tool that reports "no release tagged v9.9" lets the model recover.
    /// </summary>
    public static ToolExecutionResult Error(string message) => new(message, [], true, []);
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
