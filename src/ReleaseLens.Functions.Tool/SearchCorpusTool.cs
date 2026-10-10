using System.Text.Json;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Extensions.Mcp;
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Functions.Tool;

/// <summary>
/// The MCP tool <c>search_corpus</c>. It is read-only: the app's identity can query the index and call
/// the embeddings model, and nothing else. A caller's mistake and a failure of the service both reach
/// the caller as a fixed message: no exception text, backend reply or query comes back, and none is
/// logged.
/// </summary>
public sealed class SearchCorpusTool(CorpusSearch search, ILogger<SearchCorpusTool> log)
{
    public const string ToolName = "search_corpus";

    private const string FailedMessage = "search failed";

    // camelCase: artefact, type, excerpt, score.
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    // The ToolInvocationContext is the trigger's own parameter; the arguments arrive through the
    // McpToolProperty parameters, so it is not read.
    [Function(nameof(SearchCorpus))]
    public async Task<string> SearchCorpus(
        [McpToolTrigger(ToolName, "Searches the ReleaseLens corpus of commits, issues, pull requests and releases. Returns the best matching passages, each with its artefact ID, type, an excerpt and a relevance score.")]
        ToolInvocationContext context,
        [McpToolProperty("query", "What to look for, in plain words.", isRequired: true)]
        string query,
        [McpToolProperty("top", "How many passages to return, 1 to 10. Defaults to 5.", isRequired: false)]
        int? top,
        CancellationToken cancellationToken)
    {
        try
        {
            var hits = await search.SearchAsync(query, top, cancellationToken);
            return JsonSerializer.Serialize(hits, Json);
        }
        catch (ToolInputException)
        {
            // Fixed text, written here, safe to show.
            throw;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            // The exception's own message holds the service and the status at most (the clients
            // write it that way), and the query is never part of it or of the log.
            log.LogError(exception, "search_corpus failed.");
            throw new ToolFailedException(FailedMessage);
        }
    }
}

/// <summary>The tool error the caller sees when the search or the embedding fails.</summary>
internal sealed class ToolFailedException(string message) : Exception(message);
