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

        var issueKey = issue.Number.ToString(CultureInfo.InvariantCulture);
        var rendered = content.ToString();

        // The whole rendered issue is this artefact's excerpt, header included. The header
        // carries the state, labels, author and dates, and those are exactly the claims a
        // groundedness check on an issue answer has to verify — dropping them and quoting
        // only the body would make "reported by Alice on 2024-03-02" unverifiable.
        //
        // Bounds(1, 1, false): the query named one issue by number, exactly one matched (the
        // one just fetched), and nothing was omitted. This is the same shape every other
        // evidence tool reports, just with the trivial single-artefact counts a direct lookup
        // always has — not an exemption from the contract. Its absence here (found live,
        // Task 7 of the releaselens-mcp plan) was the one evidence tool that never set Bounds
        // at all: every consumer downstream that treats a bounds-less evidence result as "no
        // count was ever reported" — which is the whole point of requiring bounds — read this
        // tool's success as if it were that failure.
        return ToolExecutionResult.Ok(
            rendered,
            [new EvidenceCitation(EntityType.Issue, issueKey, issue.Title, $"issues/{issue.Number}")],
            [new EvidenceExcerpt(EntityType.Issue, issueKey, rendered)]) with
        {
            Bounds = new ToolResultBounds(1, 1, false)
        };
    }
}
