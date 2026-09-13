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

        // Bounds are chunk counts over the TEXT arm's population - see the remarks on
        // ToolResultBounds, and TextMatchedCount below for why the blended page is not that
        // population. result.TextMatchCount is already chunk-level and exact (see its own doc
        // comment on RetrievalResult), so nothing here is recomputed.
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

        // One population, not two. Matched is result.TextMatchCount, which counts ONLY the
        // text arm; Returned must therefore be the returned chunks that the text arm also
        // matched, never result.Chunks.Count. Chunks.Count is the blended page - the vector
        // arm full-outer-joined onto the text arm - and HybridRetriever's vec CTE has no
        // distance threshold, so on a real corpus it always contributes a full candidate pool
        // and the page fills to k regardless of how few chunks matched lexically. Pairing that
        // against a text-only match count published "returned: 8, matched: 3" for any query
        // with fewer than k lexical matches, which is the ordinary case and the entire reason
        // a vector arm exists: eight artefacts handed over with the claim that three exist in
        // the whole corpus. The published statement is now "of the N chunks matching your text
        // query, you were given M", and matched >= returned holds by construction. Chunks the
        // vector arm alone found are still returned - they are extra context, not part of that
        // ratio.
        var textMatchedAndReturned = TextMatchedCount(result.Chunks);
        var bounds = new ToolResultBounds(
            textMatchedAndReturned, result.TextMatchCount,
            result.TextMatchCount > textMatchedAndReturned);

        return ToolExecutionResult.Ok(content.ToString(), Deduplicate(citations), excerpts) with
        {
            Bounds = bounds
        };
    }

    /// <summary>
    /// How many of the returned chunks the full-text arm matched — the <c>Returned</c> half of
    /// this tool's bounds, in the same population as <c>RetrievalResult.TextMatchCount</c>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// <c>TextScore &gt; 0</c> is the discriminator, and it is exact rather than a heuristic:
    /// in <c>HybridRetriever</c>'s <c>merged</c> CTE the join projects
    /// <c>coalesce(f.text_score, 0) as text_score</c>, so a chunk the vector arm found alone
    /// carries a hard zero rather than a null, and <c>normalised</c> passes that value straight
    /// through to the <c>TextScore</c> column this reads. A chunk with a non-zero TextScore
    /// came from the <c>fts</c> CTE, which is exactly the population
    /// <c>TextMatchCount</c> counts.
    /// </para>
    /// <para>
    /// The one shape that undercounts is a query whose tsquery matches only by negation
    /// (<c>websearch_to_tsquery('english', '-foo')</c>): <c>ts_rank_cd</c> has no cover to
    /// score and returns 0 for a row that did match. That errs low — <c>Returned</c> below
    /// <c>Matched</c>, which understates what the caller was handed rather than overstating
    /// what the corpus holds — so it stays on the safe side of the one thing these bounds
    /// exist to prevent.
    /// </para>
    /// </remarks>
    internal static int TextMatchedCount(IReadOnlyList<RetrievedChunk> chunks)
        => chunks.Count(c => c.TextScore > 0);

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
