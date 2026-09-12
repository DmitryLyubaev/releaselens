using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

public sealed class DiffBetweenReleasesTool(EvidenceQueries queries) : IEvidenceTool
{
    private const int DefaultLimit = 40;
    private const int MaximumLimit = 100;

    public string Name => "diff_between_releases";

    public string Description =>
        "List the commits made between two releases, identified by tag. " +
        "This is a publication-date window over ingested commits, not a git graph walk, " +
        "so commits merged from long-lived branches may appear outside their release. " +
        "Reports how many commits the window holds in total and says explicitly when the " +
        "list was capped, so use the reported total rather than counting the rows shown.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema($$"""
        {
          "type": "object",
          "properties": {
            "from_tag": { "type": "string", "description": "The earlier release tag, exactly as published." },
            "to_tag":   { "type": "string", "description": "The later release tag, exactly as published." },
            "limit":    { "type": "integer", "description": "Maximum commits to return. Default {{DefaultLimit}}, maximum {{MaximumLimit}}. Raising it does not make the list a total; read the reported commit count instead." }
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

        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? DefaultLimit, 1, MaximumLimit);

        // Counted separately from the page, as list_releases and find_regressions do.
        // Inferring truncation from commits.Count == limit is wrong in both directions: on
        // an exact fit it states as fact that more commits exist when the window holds
        // exactly these, and it can never say whether one commit was dropped or a thousand.
        var matching = await queries.CountCommitsBetweenAsync(scope, from.Value, to.Value, cancellationToken);

        var commits = await queries.GetCommitsBetweenAsync(scope, from.Value, to.Value, limit, cancellationToken);

        if (commits.Count == 0)
        {
            // matching is necessarily 0 here too: GetCommitsBetweenAsync and
            // CountCommitsBetweenAsync share CommitWindowFilter, so an empty page means an
            // empty count under the same predicate.
            return ToolExecutionResult.Ok($"No commits were recorded between {fromTag} and {toTag}.", []) with
            {
                Bounds = new ToolResultBounds(0, (int)matching, false)
            };
        }

        var content = new StringBuilder()
            .Append("Showing ").Append(commits.Count.ToString(CultureInfo.InvariantCulture))
            .Append(" of ").Append(matching.ToString(CultureInfo.InvariantCulture))
            .Append(" commit(s) between ").Append(fromTag)
            .Append(" (").Append(FormatDay(from.Value)).Append(") and ")
            .Append(toTag)
            .Append(" (").Append(FormatDay(to.Value)).AppendLine("):")
            .AppendLine();

        var citations = new List<EvidenceCitation>(commits.Count);

        // A row is thin evidence, but it is the evidence: it is the entirety of what this
        // tool showed the model about that commit, so it is what any claim resting on this
        // tool has to be checkable against. Emitting nothing here would leave a cited commit
        // looking like a bare identifier, which is the defect being fixed.
        var excerpts = new List<EvidenceExcerpt>(commits.Count);

        foreach (var commit in commits)
        {
            var subject = commit.Message.Split('\n')[0];
            var shortSha = commit.Sha.Length >= 7 ? commit.Sha[..7] : commit.Sha;
            var row = $"{shortSha}  {FormatDay(commit.CommittedAt)}  {subject}";

            content.Append("  ").AppendLine(row);

            citations.Add(new EvidenceCitation(EntityType.Commit, commit.Sha, subject, $"commit/{commit.Sha}"));
            excerpts.Add(new EvidenceExcerpt(EntityType.Commit, commit.Sha,
                $"{row}  (listed by diff_between_releases between {fromTag} and {toTag})"));
        }

        // Requirement, not decoration, and the cap is stated rather than inferred. The page
        // is ordered oldest first, so what a cap drops is the end of the window - the part a
        // "what changed in this release" question is usually most interested in.
        if (matching > commits.Count)
        {
            content.AppendLine()
                   .Append("TRUNCATED: this list was capped at limit=")
                   .Append(limit.ToString(CultureInfo.InvariantCulture))
                   .Append(". ").Append((matching - commits.Count).ToString(CultureInfo.InvariantCulture))
                   .AppendLine(" later commit(s) in this window are not shown.")
                   .AppendLine(
                       "Do not describe this list as complete, and do not count its rows as a total: " +
                       "the total is the commit count printed above, not the number of lines here.");
        }
        else
        {
            content.AppendLine()
                   .Append("This is every commit recorded between ").Append(fromTag)
                   .Append(" and ").Append(toTag).AppendLine("; the list was not capped.");
        }

        return ToolExecutionResult.Ok(content.ToString(), citations, excerpts) with
        {
            // Same unit throughout: commits.Count and matching are both counts of rows in
            // the commits table under CommitWindowFilter - the count this tool already
            // prints as "Showing X of Y commit(s)" above.
            Bounds = new ToolResultBounds(commits.Count, (int)matching, matching > commits.Count)
        };
    }

    /// <summary>
    /// Dates are rendered in UTC, matching the window the tool says it applied. Rendering in
    /// the process's local zone would put a commit on the wrong side of a release boundary
    /// in the printed output while the SQL had it on the right one.
    /// </summary>
    private static string FormatDay(DateTimeOffset value)
        => value.ToUniversalTime().ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
}
