using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

public sealed class DiffBetweenReleasesTool(EvidenceQueries queries) : IEvidenceTool
{
    public string Name => "diff_between_releases";

    public string Description =>
        "List the commits made between two releases, identified by tag. " +
        "This is a publication-date window over ingested commits, not a git graph walk, " +
        "so commits merged from long-lived branches may appear outside their release.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema("""
        {
          "type": "object",
          "properties": {
            "from_tag": { "type": "string", "description": "The earlier release tag, exactly as published." },
            "to_tag":   { "type": "string", "description": "The later release tag, exactly as published." },
            "limit":    { "type": "integer", "description": "Maximum commits to return. Default 40, maximum 100." }
          },
          "required": ["from_tag", "to_tag"]
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        var fromTag = JsonArgs.String(arguments, "from_tag");
        var toTag = JsonArgs.String(arguments, "to_tag");

        if (string.IsNullOrWhiteSpace(fromTag) || string.IsNullOrWhiteSpace(toTag))
        {
            return ToolExecutionResult.Error("Both 'from_tag' and 'to_tag' are required.");
        }

        var (from, to) = await queries.GetReleaseWindowAsync(scope, fromTag, toTag, cancellationToken);

        if (from is null)
        {
            return ToolExecutionResult.Error($"No release tagged '{fromTag}' exists in the indexed evidence.");
        }

        if (to is null)
        {
            return ToolExecutionResult.Error($"No release tagged '{toTag}' exists in the indexed evidence.");
        }

        if (to <= from)
        {
            return ToolExecutionResult.Error(
                $"Release '{toTag}' was published on or before '{fromTag}'. Check the tag order.");
        }

        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? 40, 1, 100);
        var commits = await queries.GetCommitsBetweenAsync(scope, from.Value, to.Value, limit, cancellationToken);

        if (commits.Count == 0)
        {
            return ToolExecutionResult.Ok($"No commits were recorded between {fromTag} and {toTag}.", []);
        }

        var content = new StringBuilder()
            .Append(commits.Count).Append(" commit(s) between ").Append(fromTag)
            .Append(" (").Append(from.Value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).Append(") and ")
            .Append(toTag)
            .Append(" (").Append(to.Value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).AppendLine("):")
            .AppendLine();

        var citations = new List<EvidenceCitation>(commits.Count);

        foreach (var commit in commits)
        {
            var subject = commit.Message.Split('\n')[0];
            var shortSha = commit.Sha.Length >= 7 ? commit.Sha[..7] : commit.Sha;

            content.Append("  ").Append(shortSha).Append("  ")
                   .Append(commit.CommittedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))
                   .Append("  ").AppendLine(subject);

            citations.Add(new EvidenceCitation(EntityType.Commit, commit.Sha, subject, $"commit/{commit.Sha}"));
        }

        if (commits.Count == limit)
        {
            content.AppendLine().Append("Result limited to ").Append(limit)
                   .AppendLine(" commits; more exist in this window.");
        }

        return ToolExecutionResult.Ok(content.ToString(), citations);
    }
}
