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

/// <summary>
/// The counts a tool prints in its prose and the counts it reports as structure must be the
/// same counts. An external consumer reads only the structure; a human reads only the prose.
/// If they can disagree, one of the two audiences is being misled and nothing fails.
/// </summary>
[Collection(nameof(PostgresCollection))]
public class EvidenceToolBoundsTests(PostgresFixture fixture)
{
    private readonly FakeEmbedder _embedder = new();

    private static JsonElement Args(string json) => JsonDocument.Parse(json).RootElement;

    /// <summary>
    /// Seeds <paramref name="count"/> distinct one-commit-one-chunk artefacts, each
    /// containing <paramref name="term"/> as an ordinary word in its message and nothing
    /// else that would coincidentally match it. A commit subject line is short enough that
    /// <see cref="EvidenceChunker"/> never splits it, so this seeds exactly
    /// <paramref name="count"/> chunks - the search_commits bounds tests need that
    /// equivalence to hold so the seeded artefact count and the chunk-level match count
    /// coincide.
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedCommitsMatchingAsync(
        string term, int count)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        var chunkRepository = new ChunkRepository();
        var chunker = new EvidenceChunker(ChunkOptions.Default);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var baseDate = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
        var commits = Enumerable.Range(0, count)
            .Select(i => new CommitEvidence(
                tenantId,
                $"sha_{i:D4}_{Guid.NewGuid():n}"[..40],
                $"fix: resolve the {term} defect number {i}",
                "Author", "author@example.invalid",
                baseDate.AddMinutes(i), baseDate.AddMinutes(i),
                $"https://example.invalid/commit/{i}", []))
            .ToArray();

        await evidence.UpsertCommitsAsync(scope, commits, TestContext.Current.CancellationToken);

        var chunks = commits.SelectMany(chunker.Chunk).ToList();
        var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. chunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private async Task<ToolExecutionResult> ExecuteSearchCommitsAsync(string query, int limit, int seeded)
    {
        var (factory, tenantId) = await SeedCommitsMatchingAsync(query, seeded);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var tool = new SearchCommitsTool(new HybridRetriever(), _embedder);
        return await tool.ExecuteAsync(
            scope,
            Args($$"""{"query":"{{query}}","limit":{{limit}}}"""),
            TestContext.Current.CancellationToken);
    }

    /// <summary>
    /// Seeds <paramref name="singleChunkCount"/> one-chunk commits plus exactly one commit
    /// whose body is deliberately long enough for <see cref="EvidenceChunker"/> to split it
    /// into more than one chunk - every produced chunk still carries <paramref name="term"/>
    /// because <see cref="EvidenceChunker"/> repeats the commit's header (which includes the
    /// subject line) onto every segment.
    /// </summary>
    /// <remarks>
    /// Without this, <c>chunks.Count</c> and the distinct-commit count are always equal in
    /// this test file (one commit, one chunk, throughout), so a regression that swapped
    /// <c>SearchCommitsTool</c>'s chunk-level <c>Returned</c>/<c>Matched</c> for an
    /// artefact-level (deduplicated citation) count would pass every other test here. This
    /// seed exists specifically to make that swap fail - see
    /// <see cref="SearchCommits_WhenAnArtefactSpansMultipleChunks_BoundsCountsChunksNotArtefacts"/>.
    /// </remarks>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId, int TotalChunkCount, int MultiCommitChunkCount)>
        SeedCommitsWithOneMultiChunkArtefactAsync(string term, int singleChunkCount)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-multi-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        var chunkRepository = new ChunkRepository();
        var chunker = new EvidenceChunker(ChunkOptions.Default);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var baseDate = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

        var singleChunkCommits = Enumerable.Range(0, singleChunkCount)
            .Select(i => new CommitEvidence(
                tenantId, $"sha_s{i:D4}_{Guid.NewGuid():n}"[..40],
                $"fix: resolve the {term} defect number {i}",
                "Author", "author@example.invalid",
                baseDate.AddMinutes(i), baseDate.AddMinutes(i),
                $"https://example.invalid/commit/s{i}", []))
            .ToArray();

        // ChunkOptions.Default gives a per-chunk budget of roughly 1100 characters after the
        // header is subtracted (MaxChars 1152 minus a short header line). 40 repeated
        // sentences of ~75 characters each run to roughly 3000 characters, with plain ". "
        // sentence breaks for Segment to cut on - measured to split into 4 chunks, not
        // assumed; see the assertion below.
        var longBody = string.Concat(Enumerable.Range(0, 40)
            .Select(i => $"This paragraph goes on at length about the {term} defect, sentence {i}. "));

        var multiChunkCommit = new CommitEvidence(
            tenantId, $"sha_multi_{Guid.NewGuid():n}"[..40],
            $"fix: resolve the {term} defect across several files\n{longBody}",
            "Author", "author@example.invalid",
            baseDate.AddMinutes(singleChunkCount + 1), baseDate.AddMinutes(singleChunkCount + 1),
            "https://example.invalid/commit/multi", []);

        var multiChunks = chunker.Chunk(multiChunkCommit);

        var allCommits = singleChunkCommits.Append(multiChunkCommit).ToArray();
        await evidence.UpsertCommitsAsync(scope, allCommits, TestContext.Current.CancellationToken);

        var allChunks = singleChunkCommits.SelectMany(chunker.Chunk).Concat(multiChunks).ToList();
        var ids = await chunkRepository.UpsertChunksAsync(scope, allChunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. allChunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId, allChunks.Count, multiChunks.Count);
    }

    /// <summary>
    /// Seeds two commits that fix the corpus span for the count_evidence coverage tests:
    /// one committed 2024-01-01 (the earliest) and one committed 2026-06-01 (the latest).
    /// Deliberately NOT 2024-01-02 as an earlier draft of this task assumed - see the report
    /// for why that date does not actually work against <c>2024-01-01</c> as a "since".
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedCommitCoverageCorpusAsync()
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-cov-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        await evidence.UpsertCommitsAsync(scope,
        [
            new CommitEvidence(tenantId, "sha_earliest", "chore: first commit in the corpus", "Alice",
                "a@example.invalid",
                new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
                new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
                "https://example.invalid/commit/earliest", []),
            new CommitEvidence(tenantId, "sha_latest", "chore: last commit in the corpus", "Bob",
                "b@example.invalid",
                new DateTimeOffset(2026, 6, 1, 0, 0, 0, TimeSpan.Zero),
                new DateTimeOffset(2026, 6, 1, 0, 0, 0, TimeSpan.Zero),
                "https://example.invalid/commit/latest", [])
        ], TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private async Task<ToolExecutionResult> ExecuteCountEvidenceAsync(
        string entityType, string since, string until)
    {
        var (factory, tenantId) = await SeedCommitCoverageCorpusAsync();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var tool = new CountEvidenceTool(new EvidenceQueries());
        return await tool.ExecuteAsync(
            scope,
            Args($$"""{"entity_type":"{{entityType}}","date_field":"committed","since":"{{since}}","until":"{{until}}"}"""),
            TestContext.Current.CancellationToken);
    }

    /// <summary>
    /// Seeds two releases, v1.0 and v2.0, one month apart, and <paramref name="commitCount"/>
    /// commits committed strictly inside that window.
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedReleaseWindowAsync(int commitCount)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-diff-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var from = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
        var to = new DateTimeOffset(2026, 2, 1, 0, 0, 0, TimeSpan.Zero);

        await evidence.UpsertReleasesAsync(scope,
        [
            new ReleaseEvidence(tenantId, "v1.0", "1.0", "first", from, "main", "https://example.invalid/v1"),
            new ReleaseEvidence(tenantId, "v2.0", "2.0", "second", to, "main", "https://example.invalid/v2")
        ], TestContext.Current.CancellationToken);

        if (commitCount > 0)
        {
            var commits = Enumerable.Range(0, commitCount)
                .Select(i => new CommitEvidence(
                    tenantId, $"sha_diff{i:D4}_{Guid.NewGuid():n}"[..40],
                    $"chore: window commit number {i}", "Author", "author@example.invalid",
                    from.AddDays(i + 1), from.AddDays(i + 1),
                    $"https://example.invalid/commit/diff{i}", []))
                .ToArray();

            await evidence.UpsertCommitsAsync(scope, commits, TestContext.Current.CancellationToken);
        }

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private async Task<ToolExecutionResult> ExecuteDiffBetweenReleasesAsync(int commitCount, int? limit = null)
    {
        var (factory, tenantId) = await SeedReleaseWindowAsync(commitCount);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var tool = new DiffBetweenReleasesTool(new EvidenceQueries());
        var argsJson = limit is null
            ? """{"from_tag":"v1.0","to_tag":"v2.0"}"""
            : $$"""{"from_tag":"v1.0","to_tag":"v2.0","limit":{{limit}}}""";

        return await tool.ExecuteAsync(scope, Args(argsJson), TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task SearchCommits_WhenMoreMatchedThanReturned_ReportsTruncatedWithBothCounts()
    {
        // Seed enough matching chunks that the candidate pool cannot hold them all, then
        // ask for a small k. Follow the seeding helper in EvidenceToolTests.
        var result = await ExecuteSearchCommitsAsync(query: "gizmotron", limit: 3, seeded: 60);

        Assert.NotNull(result.Bounds);
        Assert.Equal(3, result.Bounds!.Returned);
        Assert.Equal(60, result.Bounds.Matched);
        Assert.True(result.Bounds.Truncated);

        // The structure agrees with the prose rather than contradicting it.
        Assert.Contains("60", result.Content, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SearchCommits_WhenEverythingFits_ReportsNotTruncatedAndEqualCounts()
    {
        var result = await ExecuteSearchCommitsAsync(query: "widgetronic", limit: 10, seeded: 5);

        Assert.NotNull(result.Bounds);
        Assert.Equal(5, result.Bounds!.Returned);
        Assert.Equal(5, result.Bounds.Matched);
        Assert.False(result.Bounds.Truncated);
    }

    [Fact]
    public async Task CountEvidence_ReportsCoverageBoundsMatchingItsOwnProse()
    {
        var result = await ExecuteCountEvidenceAsync(
            entityType: "commit", since: "2024-01-01", until: "2026-01-01");

        Assert.Equal(ResultKind.Computed, result.Kind);
        Assert.NotNull(result.Coverage);
        Assert.NotNull(result.Coverage!.Earliest);
        Assert.True(result.Coverage.CompleteForWindow);
        Assert.Empty(result.Citations);
    }

    [Fact]
    public async Task CountEvidence_WindowStartingBeforeTheCorpus_ReportsIncompleteCoverage()
    {
        // The corpus begins 2024-01-01 for commits (see SeedCommitCoverageCorpusAsync). A
        // 2023 window is answerable about the evidence and false about the repository, and
        // the result must say which.
        var result = await ExecuteCountEvidenceAsync(
            entityType: "commit", since: "2023-01-01", until: "2026-01-01");

        Assert.NotNull(result.Coverage);
        Assert.False(result.Coverage!.CompleteForWindow);
    }

    [Fact]
    public async Task AnEvidenceResult_NeverCarriesCoverage_AndAComputedOneNeverCarriesBounds()
    {
        var search = await ExecuteSearchCommitsAsync(query: "widgetronic", limit: 10, seeded: 5);
        var count = await ExecuteCountEvidenceAsync(
            entityType: "commit", since: "2024-01-01", until: "2026-01-01");

        Assert.Null(search.Coverage);
        Assert.Null(count.Bounds);
    }

    /// <summary>
    /// Reviewer finding on this task: every other search_commits bounds test seeds one chunk
    /// per commit, so chunk-count and distinct-artefact-count are always equal and neither
    /// test can tell a chunk-level Bounds apart from an artefact-level one. This test seeds
    /// one commit that genuinely produces more than one chunk, so the two counts differ, and
    /// asserts the chunk-level numbers specifically.
    /// </summary>
    [Fact]
    public async Task SearchCommits_WhenAnArtefactSpansMultipleChunks_BoundsCountsChunksNotArtefacts()
    {
        const string term = "quantillon";
        const int singleChunkCount = 5;

        var (factory, tenantId, totalChunks, multiCommitChunks) =
            await SeedCommitsWithOneMultiChunkArtefactAsync(term, singleChunkCount);

        // Verified, not assumed: this test only proves what it claims to prove if the long
        // commit body actually split. If EvidenceChunker's budget ever changes and this stops
        // splitting, the test must fail loudly here rather than silently degrade back into
        // the one-chunk-per-commit case Finding 1 flagged.
        Assert.True(multiCommitChunks > 1,
            $"expected the long commit body to split into more than one chunk, but EvidenceChunker " +
            $"produced {multiCommitChunks}. ChunkOptions.Default's budget may have changed - widen " +
            "the seeded body in SeedCommitsWithOneMultiChunkArtefactAsync to restore the split.");

        // The distinct-commit count is not the number this test must observe (the chunk
        // total) - asserted explicitly so a reader does not have to do the arithmetic to see
        // the two diverge. Observed at the time of writing: 6 distinct commits, 9 chunks (the
        // long body split into 4).
        var distinctCommitCount = singleChunkCount + 1;
        Assert.NotEqual(distinctCommitCount, totalChunks);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var tool = new SearchCommitsTool(new HybridRetriever(), _embedder);

        // limit comfortably above totalChunks so k never caps anything - every seeded chunk
        // comes back, and Returned/Matched must both equal the CHUNK total, not the distinct
        // commit total an artefact-counting regression would report instead.
        var result = await tool.ExecuteAsync(
            scope,
            Args($$"""{"query":"{{term}}","limit":50}"""),
            TestContext.Current.CancellationToken);

        Assert.NotNull(result.Bounds);
        Assert.Equal(totalChunks, result.Bounds!.Returned);
        Assert.Equal(totalChunks, result.Bounds.Matched);
        Assert.False(result.Bounds.Truncated);
    }

    [Fact]
    public async Task DiffBetweenReleases_WhenMoreCommitsThanReturned_ReportsTruncatedWithBothCounts()
    {
        var result = await ExecuteDiffBetweenReleasesAsync(commitCount: 4, limit: 2);

        Assert.NotNull(result.Bounds);
        Assert.Equal(2, result.Bounds!.Returned);
        Assert.Equal(4, result.Bounds.Matched);
        Assert.True(result.Bounds.Truncated);
    }

    [Fact]
    public async Task DiffBetweenReleases_NoCommitsInWindow_StillReportsBounds()
    {
        // The empty-window branch (DiffBetweenReleasesTool.cs, before the "Showing X of Y"
        // prose is even built) must carry Bounds too, or an external consumer that treats a
        // missing Bounds on an Evidence result as a tool error would misread a genuinely
        // empty window as a broken tool.
        var result = await ExecuteDiffBetweenReleasesAsync(commitCount: 0);

        Assert.NotNull(result.Bounds);
        Assert.Equal(0, result.Bounds!.Returned);
        Assert.Equal(0, result.Bounds.Matched);
        Assert.False(result.Bounds.Truncated);
    }
}
