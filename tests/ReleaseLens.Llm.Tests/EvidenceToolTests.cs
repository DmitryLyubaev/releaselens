using System;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Tools;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Llm.Tests;

[Collection(nameof(PostgresCollection))]
public class EvidenceToolTests(PostgresFixture fixture)
{
    private readonly FakeEmbedder _embedder = new();

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        var chunks = new ChunkRepository();
        var chunker = new EvidenceChunker(ChunkOptions.Default);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var commits = new[]
        {
            new CommitEvidence(tenantId, "sha_early", "fix: planner null reference", "Alice", "a@example.com",
                new DateTimeOffset(2026, 1, 15, 0, 0, 0, TimeSpan.Zero),
                new DateTimeOffset(2026, 1, 15, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/commit/sha_early",
                [new FileChange("dotnet/src/Planner.cs", "modified", 4, 1)]),
            new CommitEvidence(tenantId, "sha_late", "feat: streaming tool results", "Bob", "b@example.com",
                new DateTimeOffset(2026, 4, 15, 0, 0, 0, TimeSpan.Zero),
                new DateTimeOffset(2026, 4, 15, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/commit/sha_late", [])
        };

        await evidence.UpsertCommitsAsync(scope, commits, TestContext.Current.CancellationToken);

        // The two java- tags carry the prefix-filter and capping tests. They sit between
        // v1.0 and v2.0 deliberately: a prefix filter that silently degraded to "all
        // releases" would still look right if the matching tags were also the newest.
        await evidence.UpsertReleasesAsync(scope,
        [
            new ReleaseEvidence(tenantId, "v1.0", "1.0", "first",
                new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/v1"),
            new ReleaseEvidence(tenantId, "v2.0", "2.0", "second",
                new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/v2"),
            new ReleaseEvidence(tenantId, "java-0.2.8-alpha", "Java 0.2.8", "java eight",
                new DateTimeOffset(2026, 2, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/j8"),
            new ReleaseEvidence(tenantId, "java-0.2.9-alpha", "Java 0.2.9", "java nine",
                new DateTimeOffset(2026, 2, 10, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/j9")
        ], TestContext.Current.CancellationToken);

        await evidence.UpsertIssuesAsync(scope,
        [
            new IssueEvidence(tenantId, 4211, "Planner regression after 2.0", "It broke.", "closed",
                ["bug", "regression"], "carol",
                new DateTimeOffset(2026, 3, 10, 0, 0, 0, TimeSpan.Zero),
                new DateTimeOffset(2026, 3, 20, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/issues/4211"),
            new IssueEvidence(tenantId, 4212, "Docs typo", "Minor.", "closed", ["documentation"], "dave",
                new DateTimeOffset(2026, 3, 11, 0, 0, 0, TimeSpan.Zero), null,
                "https://github.com/microsoft/semantic-kernel/issues/4212")
        ], TestContext.Current.CancellationToken);

        // PR 901 merges earlier and only *mentions* #42110 - a different, longer issue
        // number that happens to start with "#4211". PR 900 merges later and genuinely
        // fixes #4211. An unanchored `ilike '%#4211%'` match treats both as hits and,
        // ordered by merged_at, would report 901 as the fix. An anchored match must
        // report 900.
        await evidence.UpsertPullRequestsAsync(scope,
        [
            new PullRequestEvidence(tenantId, 901, "Unrelated work on #42110", "See #42110 for context.",
                "merged", new DateTimeOffset(2026, 3, 12, 0, 0, 0, TimeSpan.Zero), "sha_pr901",
                "main", "branch-901", "eve",
                new DateTimeOffset(2026, 3, 11, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/pull/901"),
            new PullRequestEvidence(tenantId, 900, "Fixes #4211.", "Fixes #4211.",
                "merged", new DateTimeOffset(2026, 3, 15, 0, 0, 0, TimeSpan.Zero), "sha_pr900",
                "main", "branch-900", "frank",
                new DateTimeOffset(2026, 3, 13, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/pull/900"),
            // Never merged, so merged_at is null. This is what stops an unbounded
            // "how many were merged" from being answered with the row count of the
            // whole table - three pull requests exist, two of them merged.
            new PullRequestEvidence(tenantId, 902, "Still in review", "Not finished.",
                "open", null, null, "main", "branch-902", "grace",
                new DateTimeOffset(2026, 3, 14, 0, 0, 0, TimeSpan.Zero),
                "https://github.com/microsoft/semantic-kernel/pull/902")
        ], TestContext.Current.CancellationToken);

        var allChunks = commits.SelectMany(chunker.Chunk).ToList();
        var ids = await chunks.UpsertChunksAsync(scope, allChunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. allChunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunks.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private ToolRegistry BuildRegistry() => new(
    [
        new SearchCommitsTool(new HybridRetriever(), _embedder),
        new GetIssueTool(new EvidenceRepository()),
        new DiffBetweenReleasesTool(new EvidenceQueries()),
        new FindRegressionsTool(new EvidenceQueries()),
        new CountEvidenceTool(new EvidenceQueries()),
        new ListReleasesTool(new EvidenceQueries())
    ]);

    private static JsonElement Args(string json) => JsonDocument.Parse(json).RootElement;

    [Fact]
    public void Registry_ExposesEveryToolFromTheSpec()
    {
        var names = BuildRegistry().Definitions.Select(d => d.Name).OrderBy(n => n).ToArray();

        Assert.Equal(
            [
                "count_evidence", "diff_between_releases", "find_regressions",
                "get_issue", "list_releases", "search_commits"
            ],
            names);
    }

    [Fact]
    public void Registry_EveryToolHasAnObjectSchemaWithProperties()
    {
        foreach (var definition in BuildRegistry().Definitions)
        {
            Assert.Equal("object", definition.JsonSchema.GetProperty("type").GetString());
            Assert.True(definition.JsonSchema.TryGetProperty("properties", out _),
                $"{definition.Name} has no properties in its schema");
            Assert.False(string.IsNullOrWhiteSpace(definition.Description),
                $"{definition.Name} has no description - the model cannot choose it");
        }
    }

    [Fact]
    public async Task SearchCommits_ReturnsContentAndCitations()
    {
        var (factory, tenantId) = await SeedAsync("tool-search");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("search_commits", scope,
            Args("""{"query":"planner null reference","limit":5}"""), TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.NotEmpty(result.Citations);
        Assert.All(result.Citations, c => Assert.Equal(EntityType.Commit, c.Type));

        // Assert on the citation's full sha, not on rendered content: the display
        // text abbreviates to 7 characters (see EvidenceChunker), so a substring
        // check against "sha_early" would never match the rendered prose. Citations
        // are the contract Task 16 consumes; content is presentation only.
        Assert.Contains(result.Citations, c => c.EntityKey == "sha_early");
    }

    [Fact]
    public async Task GetIssue_ReturnsTheIssueAndCitesIt()
    {
        var (factory, tenantId) = await SeedAsync("tool-issue");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("get_issue", scope,
            Args("""{"number":4211}"""), TestContext.Current.CancellationToken);

        Assert.Contains("Planner regression after 2.0", result.Content, StringComparison.Ordinal);
        var citation = Assert.Single(result.Citations);
        Assert.Equal("4211", citation.EntityKey);
    }

    [Fact]
    public async Task GetIssue_MissingNumber_ReturnsAnErrorResultRatherThanThrowing()
    {
        var (factory, tenantId) = await SeedAsync("tool-issue-missing");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("get_issue", scope,
            Args("""{"number":999999}"""), TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
        Assert.Empty(result.Citations);
    }

    [Fact]
    public async Task DiffBetweenReleases_ReturnsCommitsInTheWindowOnly()
    {
        var (factory, tenantId) = await SeedAsync("tool-diff");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("diff_between_releases", scope,
            Args("""{"from_tag":"v1.0","to_tag":"v2.0"}"""), TestContext.Current.CancellationToken);

        // Assert on citations, not on rendered content. DiffBetweenReleasesTool
        // abbreviates shas to 7 characters for display, so "sha_late" (8 chars)
        // never appears verbatim in result.Content regardless of whether the release
        // window filter actually excluded it - a substring check here is vacuous and
        // proves nothing about the filter. Citations carry the full, unabbreviated
        // sha, so they are the only level at which "excluded from the window" is a
        // meaningful assertion.
        Assert.Contains(result.Citations, c => c.EntityKey == "sha_early");
        Assert.DoesNotContain(result.Citations, c => c.EntityKey == "sha_late");
    }

    [Fact]
    public async Task DiffBetweenReleases_UnknownTag_ReturnsAnErrorResult()
    {
        var (factory, tenantId) = await SeedAsync("tool-diff-unknown");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("diff_between_releases", scope,
            Args("""{"from_tag":"v1.0","to_tag":"v9.9"}"""), TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
        Assert.Contains("v9.9", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task FindRegressions_ReturnsOnlyBugAndRegressionLabelledIssues()
    {
        var (factory, tenantId) = await SeedAsync("tool-regressions");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("find_regressions", scope,
            Args("""{"area":"planner"}"""), TestContext.Current.CancellationToken);

        Assert.Contains("4211", result.Content, StringComparison.Ordinal);
        Assert.DoesNotContain("4212", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task FindRegressions_LinksTheFixingPullRequest_WithoutMatchingALongerIssueNumber()
    {
        var (factory, tenantId) = await SeedAsync("tool-regressions-pr");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("find_regressions", scope,
            Args("""{"area":"planner"}"""), TestContext.Current.CancellationToken);

        Assert.Contains("fixed by PR #900", result.Content, StringComparison.Ordinal);
        Assert.DoesNotContain("#901", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_CountsTheWindowItWasGiven()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-window");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"commit","date_field":"committed","since":"2026-01-01","until":"2026-02-01"}"""),
            TestContext.Current.CancellationToken);

        Assert.False(result.IsError);

        // sha_early (2026-01-15) is in; sha_late (2026-04-15) is out.
        Assert.Contains("Count: 1", result.Content, StringComparison.Ordinal);
    }

    /// <summary>
    /// The distinction the tool exists to protect. Both counts are over the same three
    /// pull requests and the same day; only the date field differs, and the answers are
    /// different. A tool that inferred the field from the entity type would return one of
    /// these numbers for both questions and nothing downstream could tell.
    /// </summary>
    [Fact]
    public async Task CountEvidence_OpenedAndMergedAreDifferentQuestions()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-fields");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var registry = BuildRegistry();

        // PR 900 was opened on 2026-03-13 and merged on 2026-03-15.
        var opened = await registry.ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"pull_request","date_field":"created","since":"2026-03-13","until":"2026-03-13"}"""),
            TestContext.Current.CancellationToken);

        var merged = await registry.ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"pull_request","date_field":"merged","since":"2026-03-13","until":"2026-03-13"}"""),
            TestContext.Current.CancellationToken);

        Assert.Contains("Count: 1", opened.Content, StringComparison.Ordinal);
        Assert.Contains("Count: 0", merged.Content, StringComparison.Ordinal);
    }

    /// <summary>
    /// A plain date as 'until' covers that whole day. Read literally it would cut the
    /// window at midnight, so this window would miss the pull request opened on its
    /// closing date - an undercount indistinguishable from a correct answer.
    /// </summary>
    [Fact]
    public async Task CountEvidence_PlainUntilDateIncludesThatWholeDay()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-endofday");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"pull_request","date_field":"merged","since":"2026-03-01","until":"2026-03-15"}"""),
            TestContext.Current.CancellationToken);

        // PR 901 merged 2026-03-12 and PR 900 merged 2026-03-15, the closing date itself.
        Assert.Contains("Count: 2", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_UnboundedMergedCountExcludesPullRequestsNeverMerged()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-unmerged");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var registry = BuildRegistry();

        var merged = await registry.ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"pull_request","date_field":"merged"}"""),
            TestContext.Current.CancellationToken);

        var opened = await registry.ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"pull_request","date_field":"created"}"""),
            TestContext.Current.CancellationToken);

        Assert.Contains("Count: 2", merged.Content, StringComparison.Ordinal);
        Assert.Contains("Count: 3", opened.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_MismatchedDateFieldForEntity_IsACleanErrorNamingTheValidOnes()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-mismatch");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"commit","date_field":"merged"}"""),
            TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
        Assert.Empty(result.Citations);
        Assert.Contains("date_field='committed'", result.Content, StringComparison.Ordinal);

        // The failure must not carry a number. An error that also reported a count would
        // invite the model to use the count and ignore the error.
        Assert.DoesNotContain("Count:", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_UnknownEntityType_IsACleanError()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-entity");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"deployment","date_field":"created"}"""),
            TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
        Assert.Contains("deployment", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_UnparseableDate_IsAnErrorRatherThanAnIgnoredFilter()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-baddate");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"commit","date_field":"committed","since":"last Tuesday"}"""),
            TestContext.Current.CancellationToken);

        // Dropping the filter would answer a different question with a precise number.
        Assert.True(result.IsError);
        Assert.DoesNotContain("Count:", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_ReportsCoverageAndStatesThePredicateItApplied()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-coverage");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"commit","date_field":"committed","since":"2026-01-01","until":"2026-05-01"}"""),
            TestContext.Current.CancellationToken);

        Assert.Contains("Corpus coverage for commit.committed: 2026-01-15 to 2026-04-15",
            result.Content, StringComparison.Ordinal);
        Assert.Contains("commits.committed_at", result.Content, StringComparison.Ordinal);
        Assert.Contains("committed_at >= 2026-01-01T00:00:00Z", result.Content, StringComparison.Ordinal);
    }

    /// <summary>
    /// The failure this whole feature is most at risk of causing. The corpus holds no
    /// 2025 commits, so the true count is zero and the answer to "how many commits in
    /// 2025" is not zero — it is that the corpus cannot say. The number alone is
    /// indistinguishable from a real zero, so the coverage warning has to be there.
    /// </summary>
    [Fact]
    public async Task CountEvidence_ZeroOutsideCoverage_IsMarkedIncomplete()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-zero");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"commit","date_field":"committed","since":"2025-01-01","until":"2025-12-31"}"""),
            TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.Contains("Count: 0", result.Content, StringComparison.Ordinal);
        Assert.Contains("INCOMPLETE COVERAGE", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CountEvidence_ProducesNoCitationBecauseACountIsNotAnArtefact()
    {
        var (factory, tenantId) = await SeedAsync("tool-count-citations");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("count_evidence", scope,
            Args("""{"entity_type":"release","date_field":"published"}"""),
            TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.Contains("Count: 4", result.Content, StringComparison.Ordinal);
        Assert.Empty(result.Citations);
    }

    [Fact]
    public async Task ListReleases_TagPrefix_ReturnsOnlyMatchingTagsAndCitesEachOne()
    {
        var (factory, tenantId) = await SeedAsync("tool-list-prefix");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("list_releases", scope,
            Args("""{"tag_prefix":"java-"}"""), TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.Equal(2, result.Citations.Count);
        Assert.All(result.Citations, c => Assert.Equal(EntityType.Release, c.Type));
        Assert.All(result.Citations, c => Assert.StartsWith("java-", c.EntityKey, StringComparison.Ordinal));
        Assert.Contains(result.Citations, c => c.EntityKey == "java-0.2.9-alpha");
        Assert.DoesNotContain(result.Citations, c => c.EntityKey == "v2.0");
        Assert.Contains("complete set", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ListReleases_WhenTheCapBites_ItSaysSo()
    {
        var (factory, tenantId) = await SeedAsync("tool-list-capped");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("list_releases", scope,
            Args("""{"tag_prefix":"java-","limit":1}"""), TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.Single(result.Citations);

        // A capped list that does not announce the cap reads as the complete set.
        Assert.Contains("TRUNCATED", result.Content, StringComparison.Ordinal);
        Assert.Contains("Showing 1 of 2", result.Content, StringComparison.Ordinal);
        Assert.DoesNotContain("complete set", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ListReleases_ReportsCoverage()
    {
        var (factory, tenantId) = await SeedAsync("tool-list-coverage");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("list_releases", scope,
            Args("{}"), TestContext.Current.CancellationToken);

        Assert.Contains("Corpus coverage for release.published: 2026-01-01 to 2026-03-01",
            result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ListReleases_NoMatch_StillReportsCoverageRatherThanABareEmptyList()
    {
        var (factory, tenantId) = await SeedAsync("tool-list-nomatch");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("list_releases", scope,
            Args("""{"tag_prefix":"rust-"}"""), TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.Empty(result.Citations);
        Assert.Contains("No releases match", result.Content, StringComparison.Ordinal);
        Assert.Contains("Corpus coverage", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Execute_UnknownTool_ReturnsAnErrorResultRatherThanThrowing()
    {
        var (factory, tenantId) = await SeedAsync("tool-unknown");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("delete_everything", scope,
            Args("{}"), TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
        Assert.Contains("delete_everything", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Execute_MalformedArguments_ReturnsAnErrorResultTheModelCanRecoverFrom()
    {
        var (factory, tenantId) = await SeedAsync("tool-malformed");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await BuildRegistry().ExecuteAsync("get_issue", scope,
            Args("""{"wrong_property":"nonsense"}"""), TestContext.Current.CancellationToken);

        Assert.True(result.IsError);
    }
}
