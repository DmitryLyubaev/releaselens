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
        var vector = await embedder.EmbedQueryAsync(query, cancellationToken);

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

        var result = await retriever.RetrieveAsync(scope, new RetrievalRequest(query, vector, limit)
        {
            EntityTypeFilter = entityType,
            Since = JsonArgs.Date(arguments, "since"),
            Until = JsonArgs.Date(arguments, "until"),
            PathFilter = JsonArgs.String(arguments, "path")
        }, cancellationToken);

        if (result.Chunks.Count == 0)
        {
            return ToolExecutionResult.Ok("No matching evidence found for that query.", []);
        }

        var content = new StringBuilder();
        if (result.Note is { } note)
        {
            content.AppendLine(note).AppendLine();
        }

        var citations = new List<EvidenceCitation>();

        foreach (var chunk in result.Chunks)
        {
            content.Append(chunk.Content).AppendLine()
                   .Append("  (relevance ")
                   .Append(chunk.BlendedScore.ToString("F3", CultureInfo.InvariantCulture))
                   .AppendLine(")").AppendLine();

            citations.Add(new EvidenceCitation(
                chunk.Type, chunk.EntityKey, FirstLine(chunk.Content), BuildUrl(chunk.Type, chunk.EntityKey)));
        }

        return ToolExecutionResult.Ok(content.ToString(), Deduplicate(citations));
    }

    internal static string FirstLine(string content)
    {
        var newline = content.IndexOf('\n');
        var line = newline < 0 ? content : content[..newline];
        return line.Length <= 120 ? line : line[..120];
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
