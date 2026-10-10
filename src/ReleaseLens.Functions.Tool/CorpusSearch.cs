using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tool;

/// <summary>One hit as the tool returns it.</summary>
/// <param name="Artefact">The artefact's ID, for example <c>issue:42</c>.</param>
/// <param name="Type">The part of <paramref name="Artefact"/> before the first colon.</param>
/// <param name="Excerpt">The chunk's text, at most <see cref="CorpusSearch.MaxExcerptLength"/> characters.</param>
/// <param name="Score">The semantic reranker's score.</param>
public sealed record ToolHit(string Artefact, string Type, string Excerpt, double Score);

/// <summary>
/// A caller's mistake, as opposed to a failure of the service: its message is fixed text that is safe
/// to show the caller as it is.
/// </summary>
public sealed class ToolInputException(string message) : Exception(message);

/// <summary>
/// What <c>search_corpus</c> does: embed the query, then run hybrid search with the semantic ranker.
/// The tool class only adapts this to the MCP extension.
/// </summary>
public sealed class CorpusSearch(EmbeddingsClient embeddings, SearchIndexClient index)
{
    public const int DefaultTop = 5;
    public const int MaxTop = 10;
    public const int MaxExcerptLength = 500;
    public const int MaxQueryLength = 2000;

    /// <summary>
    /// The best <paramref name="top"/> chunks for <paramref name="query"/> (5 when null, clamped to
    /// 1 through 10), best first. The query is cut to 2,000 characters before it is embedded.
    /// </summary>
    /// <exception cref="ToolInputException">The query is empty or only whitespace.</exception>
    public async Task<IReadOnlyList<ToolHit>> SearchAsync(string query, int? top, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(query))
        {
            throw new ToolInputException("query must not be empty");
        }

        var text = Cut(query.Trim(), MaxQueryLength);
        var count = Math.Clamp(top ?? DefaultTop, 1, MaxTop);

        var vectors = await embeddings.EmbedAsync([text], cancellationToken);
        var hits = await index.HybridSemanticAsync(text, vectors[0], count, cancellationToken);

        return [.. hits.Select(hit => new ToolHit(hit.Artefact, TypeOf(hit.Artefact), Cut(hit.Content, MaxExcerptLength), hit.Score))];
    }

    private static string TypeOf(string artefact)
    {
        var colon = artefact.IndexOf(':', StringComparison.Ordinal);
        return colon > 0 ? artefact[..colon] : "unknown";
    }

    /// <summary>At most <paramref name="length"/> characters, never ending on the first half of a surrogate pair.</summary>
    private static string Cut(string text, int length)
    {
        if (text.Length <= length)
        {
            return text;
        }

        return char.IsHighSurrogate(text[length - 1]) ? text[..(length - 1)] : text[..length];
    }
}
