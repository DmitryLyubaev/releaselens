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

        await evidence.UpsertReleasesAsync(scope,
        [
            new ReleaseEvidence(tenantId, "v1.0", "1.0", "first",
                new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/v1"),
            new ReleaseEvidence(tenantId, "v2.0", "2.0", "second",
                new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/v2")
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
        new FindRegressionsTool(new EvidenceQueries())
    ]);

    private static JsonElement Args(string json) => JsonDocument.Parse(json).RootElement;

    [Fact]
    public void Registry_ExposesTheFourToolsFromTheSpec()
    {
        var names = BuildRegistry().Definitions.Select(d => d.Name).OrderBy(n => n).ToArray();

        Assert.Equal(["diff_between_releases", "find_regressions", "get_issue", "search_commits"], names);
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
