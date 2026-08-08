using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;

namespace ReleaseLens.Llm.Tools;

public sealed class GetIssueTool(EvidenceRepository evidence) : IEvidenceTool
{
    public string Name => "get_issue";

    public string Description =>
        "Fetch one issue in full by its number, including body, labels, state and dates. " +
        "Use this when a specific issue number is known or has appeared in earlier search results.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema("""
        {
          "type": "object",
          "properties": {
            "number": { "type": "integer", "description": "The issue number, without a leading #." }
          },
          "required": ["number"]
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        var number = JsonArgs.Int(arguments, "number");
        if (number is null)
        {
            return ToolExecutionResult.Error("The 'number' argument is required and must be an integer.");
        }

        var issue = await evidence.GetIssueAsync(scope, number.Value, cancellationToken);
        if (issue is null)
        {
            return ToolExecutionResult.Error($"No issue #{number} exists in the indexed evidence.");
        }

        var content = new StringBuilder()
            .Append("[issue #").Append(issue.Number).Append(' ').Append(issue.State).Append("] ")
            .AppendLine(issue.Title)
            .Append("labels: ").AppendLine(issue.Labels.Count > 0 ? string.Join(", ", issue.Labels) : "(none)")
            .Append("opened by ").Append(issue.Author ?? "unknown")
            .Append(" on ").AppendLine(issue.CreatedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));

        if (issue.ClosedAt is { } closed)
        {
            content.Append("closed ").AppendLine(closed.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        }

        content.AppendLine().AppendLine(issue.Body);

        return ToolExecutionResult.Ok(content.ToString(),
        [
            new EvidenceCitation(EntityType.Issue, issue.Number.ToString(CultureInfo.InvariantCulture),
                issue.Title, $"issues/{issue.Number}")
        ]);
    }
}
