using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

public sealed class FindRegressionsTool(EvidenceQueries queries) : IEvidenceTool
{
    private const int DefaultLimit = 20;
    private const int MaximumLimit = 50;

    public string Name => "find_regressions";

    public string Description =>
        "Find issues labelled as bugs or regressions in a given area of the codebase, " +
        "together with the merged pull request that appears to have fixed each one, where one can be identified. " +
        "Returns a page of issues, not a total: it reports how many issues matched and says explicitly " +
        "when the list was capped. Never count the rows it returns to answer 'how many' - that counts " +
        "the page limit. Use the reported total, or count_evidence with a labels filter.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema($$"""
        {
          "type": "object",
          "properties": {
            "area":  { "type": "string", "description": "Subject area to match against issue titles and bodies, e.g. 'planner'. Pass an empty string for all areas." },
            "since": { "type": "string", "description": "Optional ISO-8601 date. Only issues created on or after this date." },
            "limit": { "type": "integer", "description": "Maximum issues to return. Default {{DefaultLimit}}, maximum {{MaximumLimit}}. Raising it does not make the list a total; read the reported match count instead." }
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
        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? DefaultLimit, 1, MaximumLimit);
        var since = JsonArgs.Date(arguments, "since");

        // Counted separately from the page, exactly as list_releases does. Inferring
        // truncation from candidates.Count == limit is wrong in both directions: it cries
        // truncation on an exact fit, and it can never say how many were left behind - and
        // "how many bugs are there" is the question this tool is most often reached for, so
        // the right answer has to be present rather than inferable.
        var matching = await queries.CountRegressionCandidatesAsync(scope, area, since, cancellationToken);

        var candidates = await queries.FindRegressionCandidatesAsync(
            scope, area, since, limit, cancellationToken);

        if (candidates.Count == 0)
        {
            return ToolExecutionResult.Ok(
                area.Length == 0
                    ? "No issues labelled bug or regression were found."
                    : $"No issues labelled bug or regression were found matching '{area}'.",
                []);
        }

        var scopeText = area.Length == 0 ? "" : $" matching '{area}'";

        var content = new StringBuilder()
            .Append("Showing ").Append(candidates.Count.ToString(CultureInfo.InvariantCulture))
            .Append(" of ").Append(matching.ToString(CultureInfo.InvariantCulture))
            .Append(" issue(s) labelled bug or regression").Append(scopeText)
            .AppendLine(", newest first:").AppendLine();

        var citations = new List<EvidenceCitation>(candidates.Count);

        // The row is the whole of what this tool showed about that issue, so it is what a
        // claim resting on it must be checkable against. Thin, but not nothing — and
        // nothing is what left the groundedness judge unable to score.
        var excerpts = new List<EvidenceExcerpt>(candidates.Count);

        foreach (var candidate in candidates)
        {
            var row = new StringBuilder()
                .Append('#').Append(candidate.Number).Append(' ')
                .Append('[').Append(candidate.State).Append("] ")
                .Append(candidate.Title)
                .Append("  (opened ")
                .Append(candidate.CreatedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))
                .Append(", labels: ").Append(string.Join(", ", candidate.Labels)).Append(')');

            if (candidate.FixedByPullRequest is { } pr)
            {
                row.Append("  fixed by PR #").Append(pr);
            }

            content.Append("  ").AppendLine(row.ToString());

            var number = candidate.Number.ToString(CultureInfo.InvariantCulture);

            citations.Add(new EvidenceCitation(
                EntityType.Issue, number, candidate.Title, $"issues/{candidate.Number}"));
            excerpts.Add(new EvidenceExcerpt(EntityType.Issue, number,
                $"{row} (listed by find_regressions)"));
        }

        // Requirement, not decoration. A capped list that does not announce the cap reads
        // as the complete set, and the next move after reading it is to count its rows and
        // report that as the number of bugs — a fabricated total presented as fact, with
        // nothing anywhere in the output to show it happened.
        if (matching > candidates.Count)
        {
            content.AppendLine()
                   .Append("TRUNCATED: this list was capped at limit=")
                   .Append(limit.ToString(CultureInfo.InvariantCulture))
                   .Append(". ").Append((matching - candidates.Count).ToString(CultureInfo.InvariantCulture))
                   .AppendLine(" older matching issue(s) are not shown.")
                   .AppendLine(
                       "Do not describe this list as complete, and do not count its rows as a total: " +
                       "the total is the match count printed above, not the number of lines here.");
        }
        else
        {
            content.AppendLine()
                   .Append("This is every issue labelled bug or regression").Append(scopeText)
                   .AppendLine(" in the corpus; the list was not capped.");
        }

        return ToolExecutionResult.Ok(content.ToString(), citations, excerpts);
    }
}
