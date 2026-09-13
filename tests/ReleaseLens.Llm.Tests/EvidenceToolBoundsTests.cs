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

    /// <summary>
    /// Seeds <paramref name="matchingCount"/> commits containing <paramref name="term"/> and
    /// <paramref name="nonMatchingCount"/> commits that contain nothing like it - one chunk
    /// each, the same way <see cref="SeedCommitsMatchingAsync"/> does.
    /// </summary>
    /// <remarks>
    /// This is the fixture shape no other bounds test has. Every other one seeds a tenant in
    /// which <em>every</em> chunk matches the search term, so the text arm and the blended page
    /// are the same set of rows and <c>matched == returned</c> holds by construction of the
    /// fixture rather than of the code. Here the corpus is mostly non-matching, and
    /// <c>HybridRetriever</c>'s <c>vec</c> CTE has no distance threshold, so the vector arm
    /// contributes chunks the text arm never matched and the returned page fills to <c>k</c>
    /// regardless. That is the ordinary case on a real corpus, and the only shape in which a
    /// blended <c>Returned</c> paired against a text-arm <c>Matched</c> can be seen to lie.
    /// </remarks>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedMostlyNonMatchingCorpusAsync(
        string term, int matchingCount, int nonMatchingCount)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-vec-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        var chunkRepository = new ChunkRepository();
        var chunker = new EvidenceChunker(ChunkOptions.Default);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var baseDate = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

        var matching = Enumerable.Range(0, matchingCount)
            .Select(i => new CommitEvidence(
                tenantId, $"sha_m{i:D4}_{Guid.NewGuid():n}"[..40],
                $"fix: resolve the {term} defect number {i}",
                "Author", "author@example.invalid",
                baseDate.AddMinutes(i), baseDate.AddMinutes(i),
                $"https://example.invalid/commit/m{i}", []));

        // Nothing in these shares a stem with the seeded term, so the fts CTE cannot match
        // them; only the (thresholdless) vector arm can put them on the page.
        var nonMatching = Enumerable.Range(0, nonMatchingCount)
            .Select(i => new CommitEvidence(
                tenantId, $"sha_n{i:D4}_{Guid.NewGuid():n}"[..40],
                $"chore: bump the pinned toolchain and refresh lockfiles, batch {i}",
                "Author", "author@example.invalid",
                baseDate.AddMinutes(1000 + i), baseDate.AddMinutes(1000 + i),
                $"https://example.invalid/commit/n{i}", []));

        var commits = matching.Concat(nonMatching).ToArray();
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

    /// <summary>
    /// Final-review finding 1. <c>Returned</c> and <c>Matched</c> must count over one
    /// population. <c>Matched</c> is <c>RetrievalResult.TextMatchCount</c>, which counts only
    /// the full-text arm; pairing it against the blended page size published
    /// <c>returned: 8, matched: 3</c> — eight artefacts handed to an agent that was told three
    /// exist in the whole corpus, with the consumer's <c>Math.Max(0, matched - returned)</c>
    /// clamping the contradiction out of sight.
    /// </summary>
    [Fact]
    public async Task SearchCommits_WhenTheVectorArmFillsThePage_ReturnedNeverExceedsMatched()
    {
        const string term = "flubbergasket";
        const int matchingCount = 3;
        const int nonMatchingCount = 20;
        const int limit = 8;

        var (factory, tenantId) =
            await SeedMostlyNonMatchingCorpusAsync(term, matchingCount, nonMatchingCount);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // Verified, not assumed, and it is the assumption the production fix rests on: a chunk
        // the vector arm alone found must carry TextScore == 0 rather than a null coalesced
        // into something else. Read straight off the retriever, before the tool sees it.
        var probe = await new HybridRetriever().RetrieveAsync(
            scope, new RetrievalRequest(term, await _embedder.EmbedQueryAsync(
                term, TestContext.Current.CancellationToken), limit),
            TestContext.Current.CancellationToken);

        Assert.Equal(limit, probe.Chunks.Count);
        Assert.Equal(matchingCount, probe.TextMatchCount);
        Assert.Contains(probe.Chunks, c => c.TextScore == 0);
        Assert.All(probe.Chunks, c => Assert.True(c.TextScore >= 0));

        // The fixture shape itself, asserted rather than described: the page the tool returns
        // is larger than everything the text arm matched in the entire tenant. No other bounds
        // test in this file can reach this state.
        Assert.True(probe.Chunks.Count > probe.TextMatchCount,
            $"the vector arm was expected to fill the page beyond the {probe.TextMatchCount} "
            + $"text matches, but only {probe.Chunks.Count} chunks came back - this test proves "
            + "nothing unless the returned page exceeds the text arm's whole population.");

        var tool = new SearchCommitsTool(new HybridRetriever(), _embedder);
        var result = await tool.ExecuteAsync(
            scope,
            Args($$"""{"query":"{{term}}","limit":{{limit}}}"""),
            TestContext.Current.CancellationToken);

        Assert.NotNull(result.Bounds);
        var bounds = result.Bounds!;

        // Matched is the whole text-arm population under these filters, exactly.
        Assert.Equal(matchingCount, bounds.Matched);

        // The published bounds are coherent: a caller can never be handed more of the matched
        // population than exists in it. This is the assertion the old code fails.
        Assert.True(bounds.Returned <= bounds.Matched,
            $"published bounds claim {bounds.Returned} of {bounds.Matched} returned, which says "
            + "the caller was handed more of the matched population than the corpus contains.");

        Assert.Equal(bounds.Matched > bounds.Returned, bounds.Truncated);

        // And the vector-only chunks really were still handed over: this fix narrows the
        // ratio's population, it does not drop rows from the result.
        Assert.Equal(probe.Chunks.Count, result.Excerpts.Count);
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

    /// <summary>
    /// Found live, not in review: the releaselens-mcp project's Task 7 ran its cross-tenant
    /// test against a real two-tenant stack and every <c>get_issue</c> call came back rejected
    /// downstream, because this was the one evidence tool (of six) that never set Bounds at
    /// all. A direct fetch of one known issue is still an evidence result, and the contract
    /// applies to it the same as to a search: Returned/Matched/Truncated for the trivial
    /// single-artefact case are (1, 1, false), not absent.
    /// </summary>
    [Fact]
    public async Task GetIssue_OnASuccessfulFetch_ReportsTrivialBounds()
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var slug = $"bounds-issue-{Guid.NewGuid():n}"[..24];
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        await using (var seedScope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await evidence.UpsertIssuesAsync(seedScope,
            [
                new IssueEvidence(tenantId, 42, "Something broke", "It broke like this.", "open",
                    [], "reporter", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), null,
                    "https://example.invalid/issues/42")
            ], TestContext.Current.CancellationToken);
            await seedScope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var tool = new GetIssueTool(evidence);
        var result = await tool.ExecuteAsync(
            scope, Args("""{"number":42}"""), TestContext.Current.CancellationToken);

        Assert.False(result.IsError);
        Assert.NotNull(result.Bounds);
        Assert.Equal(1, result.Bounds!.Returned);
        Assert.Equal(1, result.Bounds.Matched);
        Assert.False(result.Bounds.Truncated);
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
