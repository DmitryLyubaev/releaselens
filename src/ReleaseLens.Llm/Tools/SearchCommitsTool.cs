using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Embedding;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

public sealed class SearchCommitsTool(HybridRetriever retriever, IEmbedder embedder) : IEvidenceTool
{
    public string Name => "search_commits";

    public string Description =>
        "Search the repository's commits, issues, pull requests and releases by natural-language query. " +
        "Uses hybrid full-text and semantic retrieval. Use this first for any open-ended question.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema("""
        {
          "type": "object",
          "properties": {
            "query":       { "type": "string", "description": "Natural-language search query." },
            "entity_type": { "type": "string", "enum": ["commit", "issue", "pull_request", "release"],
                             "description": "Optional. Restrict results to one kind of evidence." },
            "since":       { "type": "string", "description": "Optional ISO-8601 date. Commits only." },
            "until":       { "type": "string", "description": "Optional ISO-8601 date. Commits only." },
            "path":        { "type": "string", "description": "Optional file path substring. Commits only." },
            "limit":       { "type": "integer", "description": "How many results to return. Default 8, maximum 25." }
          },
          "required": ["query"]
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        var query = JsonArgs.String(arguments, "query");
        if (string.IsNullOrWhiteSpace(query))
        {
            return ToolExecutionResult.Error("The 'query' argument is required and must be a non-empty string.");
        }

        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? 8, 1, 25);

        EntityType? entityType = null;
        if (JsonArgs.String(arguments, "entity_type") is { } wire)
        {
            try
            {
                entityType = EntityTypeExtensions.FromWireName(wire);
            }
            catch (ArgumentOutOfRangeException)
            {
                return ToolExecutionResult.Error(
                    $"'{wire}' is not a valid entity_type. Use commit, issue, pull_request or release.");
            }
        }

        // Validated before embedding: an invalid entity_type should not pay for an
        // embedding call it is about to reject.
        var vector = await embedder.EmbedQueryAsync(query, cancellationToken);

        var result = await retriever.RetrieveAsync(scope, new RetrievalRequest(query, vector, limit)
        {
            EntityTypeFilter = entityType,
            Since = JsonArgs.Date(arguments, "since"),
            Until = JsonArgs.Date(arguments, "until"),
            PathFilter = JsonArgs.String(arguments, "path")
        }, cancellationToken);

        // Bounds are chunk counts, not artefact counts - see the remarks on
        // ToolResultBounds. result.TextMatchCount is already chunk-level and exact (see its
        // own doc comment on RetrievalResult), so nothing here is recomputed.
        if (result.Chunks.Count == 0)
        {
            return ToolExecutionResult.Ok("No matching evidence found for that query.", []) with
            {
                Bounds = new ToolResultBounds(0, result.TextMatchCount, result.TextMatchCount > 0)
            };
        }

        var content = new StringBuilder();
        if (result.Note is { } note)
        {
            content.AppendLine(note).AppendLine();
        }

        var citations = new List<EvidenceCitation>();

        // One excerpt per chunk, NOT per citation: citations collapse to one entry per
        // artefact and the excerpts must not collapse with them, or an artefact retrieved
        // as four chunks would only ever be quotable from its first.
        var excerpts = new List<EvidenceExcerpt>(result.Chunks.Count);

        foreach (var chunk in result.Chunks)
        {
            content.Append(chunk.Content).AppendLine()
                   .Append("  (relevance ")
                   .Append(chunk.BlendedScore.ToString("F3", CultureInfo.InvariantCulture))
                   .AppendLine(")").AppendLine();

            citations.Add(new EvidenceCitation(
                chunk.Type, chunk.EntityKey, FirstLine(chunk.Content), BuildUrl(chunk.Type, chunk.EntityKey)));

            excerpts.Add(new EvidenceExcerpt(chunk.Type, chunk.EntityKey, chunk.Content));
        }

        // Chunk counts, not artefact counts: chunks.Count is what this tool actually
        // returned before the citation list collapsed several chunks into one entry per
        // artefact, and TextMatchCount is the corpus-wide count in that same chunk unit -
        // see the remarks on ToolResultBounds for why an artefact count cannot pair with it.
        var bounds = new ToolResultBounds(
            result.Chunks.Count, result.TextMatchCount, result.TextMatchCount > result.Chunks.Count);

        return ToolExecutionResult.Ok(content.ToString(), Deduplicate(citations), excerpts) with
        {
            Bounds = bounds
        };
    }

    internal static string FirstLine(string content)
    {
        var newline = content.IndexOf('\n');
        var line = newline < 0 ? content : content[..newline];
        if (line.Length <= 120)
        {
            return line;
        }

        // Do not truncate between the halves of a surrogate pair — a lone surrogate is
        // invalid UTF-16 and fails to encode on the way to Postgres or to a provider.
        return line[..(char.IsLowSurrogate(line[120]) ? 119 : 120)];
    }

    /// <summary>
    /// URLs are reconstructed from the tenant's repository rather than stored per chunk.
    /// The tenant on the scope is the indexed repository, so this is unambiguous.
    /// </summary>
    internal static string BuildUrl(EntityType type, string entityKey) => type switch
    {
        EntityType.Commit => $"commit/{entityKey}",
        EntityType.Issue => $"issues/{entityKey}",
        EntityType.PullRequest => $"pull/{entityKey}",
        EntityType.Release => $"releases/tag/{entityKey}",
        _ => entityKey
    };

    internal static IReadOnlyList<EvidenceCitation> Deduplicate(IEnumerable<EvidenceCitation> citations)
        => [.. citations.GroupBy(c => (c.Type, c.EntityKey)).Select(g => g.First())];
}
