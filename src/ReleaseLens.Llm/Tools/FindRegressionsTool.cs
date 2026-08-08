using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

public sealed class FindRegressionsTool(EvidenceQueries queries) : IEvidenceTool
{
    public string Name => "find_regressions";

    public string Description =>
        "Find issues labelled as bugs or regressions in a given area of the codebase, " +
        "together with the merged pull request that appears to have fixed each one, where one can be identified.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema("""
        {
          "type": "object",
          "properties": {
            "area":  { "type": "string", "description": "Subject area to match against issue titles and bodies, e.g. 'planner'. Pass an empty string for all areas." },
            "since": { "type": "string", "description": "Optional ISO-8601 date. Only issues created on or after this date." },
            "limit": { "type": "integer", "description": "Maximum issues to return. Default 20, maximum 50." }
          },
          "required": ["area"]
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        if (!arguments.TryGetProperty("area", out var areaElement) || areaElement.ValueKind != JsonValueKind.String)
        {
            return ToolExecutionResult.Error(
                "The 'area' argument is required. Pass an empty string to search all areas.");
        }

        var area = areaElement.GetString() ?? string.Empty;
        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? 20, 1, 50);

        var candidates = await queries.FindRegressionCandidatesAsync(
            scope, area, JsonArgs.Date(arguments, "since"), limit, cancellationToken);

        if (candidates.Count == 0)
        {
            return ToolExecutionResult.Ok(
                area.Length == 0
                    ? "No issues labelled bug or regression were found."
                    : $"No issues labelled bug or regression were found matching '{area}'.",
                []);
        }

        var content = new StringBuilder()
            .Append(candidates.Count).AppendLine(" issue(s) labelled bug or regression:").AppendLine();

        var citations = new List<EvidenceCitation>(candidates.Count);

        foreach (var candidate in candidates)
        {
            content.Append("  #").Append(candidate.Number).Append(' ')
                   .Append('[').Append(candidate.State).Append("] ")
                   .Append(candidate.Title)
                   .Append("  (opened ")
                   .Append(candidate.CreatedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))
                   .Append(", labels: ").Append(string.Join(", ", candidate.Labels)).Append(')');

            if (candidate.FixedByPullRequest is { } pr)
            {
                content.Append("  fixed by PR #").Append(pr);
            }

            content.AppendLine();

            citations.Add(new EvidenceCitation(
                EntityType.Issue, candidate.Number.ToString(CultureInfo.InvariantCulture),
                candidate.Title, $"issues/{candidate.Number}"));
        }

        return ToolExecutionResult.Ok(content.ToString(), citations);
    }
}
