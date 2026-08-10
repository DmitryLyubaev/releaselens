using System.Text.RegularExpressions;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Structural checks only — no database, no model. These catch the ways a golden set
/// rots: duplicate ids, a category typo, a citation written in a shape the harness will
/// never match, or the unanswerable coverage quietly dropping to zero as entries get
/// added.
/// </summary>
public partial class GoldenSetValidationTests
{
    private static readonly string[] ValidCategories =
        ["factual", "temporal", "causal", "aggregation", "unanswerable"];

    /// <summary>
    /// The harness compares citations as literal "type:key" strings built from
    /// <c>EntityType.ToWireName()</c>, so a citation written as "pr:14112" or "PR 14112"
    /// can never match anything and the query silently becomes unpassable.
    /// </summary>
    [GeneratedRegex(@"""(commit|issue|pull_request|release):[^""]+""")]
    private static partial Regex WellFormedCitation();

    [GeneratedRegex(@"^\s*-\s*id:\s*(\S+)")]
    private static partial Regex IdLine();

    [GeneratedRegex(@"^\s*category:\s*(\S+)")]
    private static partial Regex CategoryLine();

    private static string GoldenPath()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null)
        {
            var candidate = Path.Combine(directory.FullName, "eval", "golden", "queries.yaml");
            if (File.Exists(candidate))
            {
                return candidate;
            }

            directory = directory.Parent;
        }

        throw new FileNotFoundException("eval/golden/queries.yaml not found from the test output directory.");
    }

    private static string[] Lines() => File.ReadAllLines(GoldenPath());

    [Fact]
    public void EveryIdIsUnique()
    {
        var ids = Lines()
            .Select(l => IdLine().Match(l))
            .Where(m => m.Success)
            .Select(m => m.Groups[1].Value)
            .ToList();

        Assert.Equal(ids.Count, ids.Distinct().Count());
    }

    [Fact]
    public void EveryCategoryIsValid()
    {
        var categories = Lines()
            .Select(l => CategoryLine().Match(l))
            .Where(m => m.Success)
            .Select(m => m.Groups[1].Value);

        Assert.All(categories, c => Assert.Contains(c, ValidCategories));
    }

    [Fact]
    public void AtLeastSixQueriesAreUnanswerable()
    {
        var unanswerable = Lines().Count(l => Regex.IsMatch(l, @"^\s*category:\s*unanswerable\s*$"));

        Assert.True(unanswerable >= 6,
            $"only {unanswerable} unanswerable queries; the set needs at least 6 to be meaningful");
    }

    [Fact]
    public void TheSetHasBetweenThirtyAndFiftyQueries()
    {
        var count = Lines().Count(l => IdLine().IsMatch(l));

        Assert.InRange(count, 30, 50);
    }

    [Fact]
    public void EveryCitationUsesAKnownEntityPrefix()
    {
        // Citation lists are the only place a bare "word:value" string appears, and they
        // may wrap across lines, so scan the whole document rather than line by line.
        var document = File.ReadAllText(GoldenPath());
        var quoted = Regex.Matches(document, @"""[a-z_]+:[^""]+""");

        Assert.All(quoted, m => Assert.Matches(WellFormedCitation(), m.Value));
    }
}
