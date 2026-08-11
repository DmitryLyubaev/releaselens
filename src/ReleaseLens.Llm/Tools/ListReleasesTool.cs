using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

/// <summary>
/// Lists release tags with their publication dates, straight from the releases table.
/// </summary>
/// <remarks>
/// The enumeration counterpart to <see cref="CountEvidenceTool"/>. "List every Java
/// release tag ever published" is unanswerable by semantic search at any sane k — the
/// only way to be sure the list is complete is to stop retrieving and enumerate. This
/// tool returns the rows, so the cost is the size of the answer rather than the size of
/// the corpus that had to be read to find it.
/// </remarks>
public sealed class ListReleasesTool(EvidenceQueries queries) : IEvidenceTool
{
    private const int DefaultLimit = 50;
    private const int MaximumLimit = 300;

    public string Name => "list_releases";

    public string Description =>
        "List release tags with their publication dates, newest first, optionally filtered by " +
        "tag prefix and date. Enumerates the releases table directly, so it is the only way to " +
        "answer 'list every release' or 'which releases exist' completely - search returns a " +
        "sample and cannot tell you it missed one. Tags in this repository are prefixed by " +
        "product line, so tag_prefix values look like 'java-', 'dotnet-', 'python-' or " +
        "'vectordata-'. Says explicitly when the result was capped, and reports the date range " +
        "the corpus covers. For a bare number of releases use count_evidence instead.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema($$"""
        {
          "type": "object",
          "properties": {
            "tag_prefix": { "type": "string", "description": "Optional. Only tags starting with this literal text, e.g. 'java-'. Matched as a prefix, not a wildcard pattern." },
            "since":      { "type": "string", "description": "Optional ISO-8601 date. Inclusive lower bound on the publication date, UTC." },
            "until":      { "type": "string", "description": "Optional ISO-8601 date. Upper bound on the publication date, UTC. A plain date such as 2024-12-31 includes the whole of that day." },
            "limit":      { "type": "integer", "description": "How many releases to return. Default {{DefaultLimit}}, maximum {{MaximumLimit}}." }
          }
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        if (!AggregateWindow.TryRead(arguments, "since", isUpperBound: false, out var since, out var sinceError))
        {
            return ToolExecutionResult.Error(sinceError!);
        }

        if (!AggregateWindow.TryRead(arguments, "until", isUpperBound: true, out var until, out var untilError))
        {
            return ToolExecutionResult.Error(untilError!);
        }

        if (since is not null && until is not null && until.Value <= since.Value)
        {
            return ToolExecutionResult.Error(
                $"The window is empty: until ({AggregateWindow.Format(until.Value)}) is not after " +
                $"since ({AggregateWindow.Format(since.Value)}). Check the order of the dates.");
        }

        var tagPrefix = JsonArgs.String(arguments, "tag_prefix");
        if (string.IsNullOrEmpty(tagPrefix))
        {
            tagPrefix = null;
        }

        var limit = Math.Clamp(JsonArgs.Int(arguments, "limit") ?? DefaultLimit, 1, MaximumLimit);

        // Counted separately from the page. Inferring "there may be more" from
        // rows.Count == limit is wrong in both directions: it cries truncation on an
        // exact fit, and it can never say how much was left behind.
        var matching = await queries.CountReleasesAsync(
            scope, tagPrefix, since?.Value, until?.Value, cancellationToken);

        var releases = await queries.ListReleasesAsync(
            scope, tagPrefix, since?.Value, until?.Value, limit, cancellationToken);

        var coverage = await queries.GetCoverageAsync(
            scope, EvidenceDateFields.Find(EntityType.Release, "published")!, cancellationToken);

        var filters = Describe(tagPrefix, since, until);

        if (releases.Count == 0)
        {
            var empty = new StringBuilder()
                .Append("No releases match ").Append(filters).AppendLine(".");

            AggregateWindow.AppendCoverage(empty, "release.published", coverage, since, until);
            return ToolExecutionResult.Ok(empty.ToString(), []);
        }

        var content = new StringBuilder()
            .Append("Showing ").Append(releases.Count.ToString(CultureInfo.InvariantCulture))
            .Append(" of ").Append(matching.ToString(CultureInfo.InvariantCulture))
            .Append(" releases matching ").Append(filters)
            .AppendLine(", newest first:").AppendLine();

        var citations = new List<EvidenceCitation>(releases.Count);

        foreach (var release in releases)
        {
            content.Append("  ")
                   .Append(release.PublishedAt is { } published
                       ? published.ToUniversalTime().ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
                       : "(unpublished)")
                   .Append("  ").AppendLine(release.Tag);

            citations.Add(new EvidenceCitation(
                EntityType.Release, release.Tag,
                string.IsNullOrWhiteSpace(release.Name) ? release.Tag : release.Name!,
                $"releases/tag/{release.Tag}"));
        }

        // Requirement, not decoration: a list that stops at the cap and does not say so
        // reads as the complete set, and every claim built on it ("there were four", "the
        // earliest is X") inherits the error without any sign that it happened.
        if (matching > releases.Count)
        {
            content.AppendLine()
                   .Append("TRUNCATED: this list was capped at limit=")
                   .Append(limit.ToString(CultureInfo.InvariantCulture))
                   .Append(". ").Append((matching - releases.Count).ToString(CultureInfo.InvariantCulture))
                   .AppendLine(" older matching release(s) are not shown.")
                   .AppendLine(
                       "Do not describe this list as complete, and do not count it as a total. " +
                       "Raise limit, narrow tag_prefix or the date window, or use count_evidence for the total.");
        }
        else
        {
            content.AppendLine()
                   .AppendLine("This is the complete set of releases matching those filters in the corpus.");
        }

        AggregateWindow.AppendCoverage(content, "release.published", coverage, since, until);

        return ToolExecutionResult.Ok(content.ToString(), citations);
    }

    private static string Describe(string? tagPrefix, WindowBound? since, WindowBound? until)
    {
        var parts = new List<string>(3);

        parts.Add(tagPrefix is null ? "any tag" : $"tag prefix '{tagPrefix}'");

        if (since is not null)
        {
            parts.Add($"published on or after {AggregateWindow.Format(since.Value)}");
        }

        if (until is not null)
        {
            parts.Add($"published before {AggregateWindow.Format(until.Value)}");
        }

        return string.Join(", ", parts);
    }
}
