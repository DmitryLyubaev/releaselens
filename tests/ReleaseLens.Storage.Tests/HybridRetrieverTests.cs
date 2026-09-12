using System;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using Dapper;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class HybridRetrieverTests(PostgresFixture fixture)
{
    private static float[] UnitVector(Func<int, float> generator)
    {
        var v = new float[384];
        for (var i = 0; i < v.Length; i++)
        {
            v[i] = generator(i);
        }

        var magnitude = MathF.Sqrt(v.Sum(x => x * x));
        for (var i = 0; i < v.Length; i++)
        {
            v[i] /= magnitude;
        }

        return v;
    }

    private static readonly float[] NearVector = UnitVector(i => 1f + (i * 0.0001f));
    private static readonly float[] FarVector = UnitVector(i => i % 2 == 0 ? 1f : -1f);

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var chunks = new ChunkRepository();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await chunks.UpsertChunksAsync(scope,
        [
            new Chunk(tenantId, EntityType.Commit, "sha_near", 0,
                "[commit sha_near] fix planner null reference exception in function calling", 12),
            new Chunk(tenantId, EntityType.Commit, "sha_far", 0,
                "[commit sha_far] update documentation for macOS installation steps", 10),
            new Chunk(tenantId, EntityType.Issue, "4211", 0,
                "[issue #4211 closed] planner null reference exception reported by users", 11)
        ], TestContext.Current.CancellationToken);

        await chunks.UpsertEmbeddingsAsync(scope,
            [(ids[0], NearVector), (ids[1], FarVector), (ids[2], NearVector)],
            "test-model", TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    [Fact]
    public async Task Retrieve_RanksTheVectorNeighbourAboveTheDistantOne()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-rank");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner null reference", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        Assert.Equal("sha_far", result.Chunks[^1].EntityKey);
    }

    [Fact]
    public async Task Retrieve_ReturnsBothArmScoresAndTheBlend()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-scores");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner null reference exception", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        var top = result.Chunks[0];
        Assert.InRange(top.VectorScore, -1.0, 1.0);
        Assert.True(top.TextScore >= 0);
        Assert.True(top.BlendedScore > 0);
    }

    [Fact]
    public async Task Retrieve_FullTextOnlyMatch_StillAppears()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-fts-only");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // FarVector content is the only chunk mentioning macOS.
        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("macOS installation", NearVector, K: 5),
            TestContext.Current.CancellationToken);

        Assert.Contains(result.Chunks, c => c.EntityKey == "sha_far");
    }

    [Fact]
    public async Task Retrieve_EntityTypeFilter_RestrictsResults()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-filter");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner", NearVector, K: 5) { EntityTypeFilter = EntityType.Issue },
            TestContext.Current.CancellationToken);

        Assert.All(result.Chunks, c => Assert.Equal(EntityType.Issue, c.Type));
    }

    [Fact]
    public async Task Retrieve_FewerThanK_IsReportedNotSilentlyDropped()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-short");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner", NearVector, K: 50),
            TestContext.Current.CancellationToken);

        Assert.True(result.FewerThanRequested);
        Assert.Equal(50, result.RequestedK);
        Assert.True(result.Chunks.Count < 50);
        Assert.NotNull(result.Note);
        Assert.Contains("fewer", result.Note, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Retrieve_ExactlyKAvailable_IsNotMarkedShort()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-exact");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner exception documentation macOS", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        Assert.Equal(3, result.Chunks.Count);
        Assert.False(result.FewerThanRequested);
        Assert.Null(result.Note);
    }

    [Fact]
    public async Task Retrieve_IsTenantScoped()
    {
        var (factory, tenantA) = await SeedAsync("retrieve-tenant-a");
        var (_, tenantB) = await SeedAsync("retrieve-tenant-b");

        await using var scope = await factory.OpenAsync(tenantB, TestContext.Current.CancellationToken);
        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner", NearVector, K: 50),
            TestContext.Current.CancellationToken);

        Assert.All(result.Chunks, c => Assert.NotEqual(tenantA, tenantB));
        Assert.True(result.Chunks.Count <= 3, "tenant B should only see its own three chunks");
    }

    [Fact]
    public async Task Retrieve_SinceFilter_ExcludesCommitsOutsideTheWindow()
    {
        // The date and path predicates resolve against the `commits` table rather than the
        // chunk, so a chunk whose entity_key has no matching commit row is excluded by them
        // entirely. Nothing else in this suite exercises these filters, and Task 15's
        // search_commits tool exposes all three to the model.
        var (factory, tenantId) = await SeedAsync("retrieve-since");

        await using (var seed = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await new EvidenceRepository().UpsertCommitsAsync(seed,
            [
                new CommitEvidence(tenantId, "sha_near", "fix planner", "A", "a@example.com",
                    new DateTimeOffset(2026, 6, 1, 0, 0, 0, TimeSpan.Zero),
                    new DateTimeOffset(2026, 6, 1, 0, 0, 0, TimeSpan.Zero),
                    "https://example.invalid/near",
                    [new FileChange("dotnet/src/Planner.cs", "modified", 1, 0)]),
                new CommitEvidence(tenantId, "sha_far", "update documentation", "A", "a@example.com",
                    new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
                    new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
                    "https://example.invalid/far", [])
            ], TestContext.Current.CancellationToken);

            await seed.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner documentation", NearVector, K: 10)
            {
                Since = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero)
            },
            TestContext.Current.CancellationToken);

        Assert.Contains(result.Chunks, c => c.EntityKey == "sha_near");
        Assert.DoesNotContain(result.Chunks, c => c.EntityKey == "sha_far");
    }

    [Fact]
    public async Task Retrieve_PathFilter_MatchesOnAChangedFilePath()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-path");

        await using (var seed = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await new EvidenceRepository().UpsertCommitsAsync(seed,
            [
                new CommitEvidence(tenantId, "sha_near", "fix planner", "A", "a@example.com",
                    DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch, "https://example.invalid/near",
                    [new FileChange("dotnet/src/Planner.cs", "modified", 1, 0)]),
                new CommitEvidence(tenantId, "sha_far", "update documentation", "A", "a@example.com",
                    DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch, "https://example.invalid/far",
                    [new FileChange("docs/README.md", "modified", 1, 0)])
            ], TestContext.Current.CancellationToken);

            await seed.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner documentation", NearVector, K: 10) { PathFilter = "Planner.cs" },
            TestContext.Current.CancellationToken);

        Assert.Contains(result.Chunks, c => c.EntityKey == "sha_near");
        Assert.DoesNotContain(result.Chunks, c => c.EntityKey == "sha_far");
    }

    [Fact]
    public async Task Pgvector_IsNewEnoughForIterativeScan()
    {
        // RetrieveAsync sets hnsw.iterative_scan, which exists only in pgvector 0.8+. Both the
        // compose file and the test fixture pin the image by tag rather than digest, so a
        // future pull could resolve to an older build — and retrieval would then fail at run
        // time, in the query path, rather than here with a message that says why.
        var (factory, tenantId) = await SeedAsync("pgvector-version");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var version = await scope.Connection.ExecuteScalarAsync<string>(new CommandDefinition(
            "select extversion from pg_extension where extname = 'vector'",
            transaction: scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

        Assert.NotNull(version);

        var parts = version.Split('.');
        var major = int.Parse(parts[0], CultureInfo.InvariantCulture);
        var minor = int.Parse(parts[1], CultureInfo.InvariantCulture);

        Assert.True(major > 0 || minor >= 8,
            $"pgvector {version} predates hnsw.iterative_scan, which HybridRetriever depends on");
    }

    [Fact]
    public async Task Retrieve_EmptyQueryText_FallsBackToVectorOnly()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-empty-text");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("   ", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        Assert.NotEmpty(result.Chunks);
        Assert.All(result.Chunks, c => Assert.Equal(0, c.TextScore));
    }

    /// <summary>
    /// Seeds chunks that all contain <paramref name="keyword"/>, with no embeddings at
    /// all. Without an embedded vector the vec CTE never surfaces them, so the returned
    /// chunks and the reported TextMatchCount both trace back to the fts arm alone --
    /// there is nothing here for the vector arm to contribute or to interfere with.
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedTextOnlyAsync(
        string slug, int count, string keyword)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var chunks = new ChunkRepository();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var toInsert = Enumerable.Range(0, count)
            .Select(i => new Chunk(tenantId, EntityType.Commit, $"bulk_{i}", 0,
                $"[commit bulk_{i}] {keyword} shows up in this chunk describing routine maintenance work", 14))
            .ToList();

        await chunks.UpsertChunksAsync(scope, toInsert, TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        return (factory, tenantId);
    }

    [Fact]
    public async Task Retrieve_TextMatchesExceedPool_ReportsCountAboveThePoolAndNotesTheCap()
    {
        // 60 comfortably clears the 50-row floor of CandidatePoolSize (Math.Max(50, K*4) at
        // K=8), so this exercises "more text matches than the blend can see" without needing
        // a fabricated pool size. The four-figure case has its own test below.
        var (factory, tenantId) = await SeedTextOnlyAsync("retrieve-text-capped", 60, "gizmotron");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("gizmotron", NearVector, K: 8),
            TestContext.Current.CancellationToken);

        // Asserting the exact count (not just ">") makes this fail loudly if the seeded
        // content ever stops matching the tsquery, rather than passing vacuously because
        // nothing matched.
        Assert.Equal(60, result.TextMatchCount);
        Assert.True(result.TextMatchCount > result.CandidatePoolSize);
        Assert.NotNull(result.Note);
        Assert.Contains("capped", result.Note, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Retrieve_TextMatchesBelowPool_ReportsTrueCountWithNoCappingClaim()
    {
        var (factory, tenantId) = await SeedTextOnlyAsync("retrieve-text-uncapped", 5, "widgetronic");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("widgetronic", NearVector, K: 5),
            TestContext.Current.CancellationToken);

        Assert.Equal(5, result.TextMatchCount);
        Assert.True(result.TextMatchCount < result.CandidatePoolSize);
        Assert.False(result.FewerThanRequested);
        Assert.Null(result.Note);
    }

    /// <summary>
    /// The count was once taken by a separate CTE that stopped at 1001 rows and reported
    /// 1000 with a lower-bound flag, so every figure above a thousand was the same figure.
    /// It is now taken by count(*) over () on the scan that ranks the pool, which counts
    /// every match. Only a four-figure seed can tell those two implementations apart.
    /// </summary>
    [Fact]
    public async Task Retrieve_TextMatchesExceedTheOldCountingCap_ReportsTheExactCount()
    {
        var (factory, tenantId) = await SeedTextOnlyAsync("retrieve-text-above-cap", 1200, "thingamajig");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("thingamajig", NearVector, K: 8),
            TestContext.Current.CancellationToken);

        Assert.Equal(1200, result.TextMatchCount);
        Assert.NotNull(result.Note);
        Assert.Contains("1200", result.Note, StringComparison.Ordinal);
        Assert.DoesNotContain("at least", result.Note, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Retrieve_NoTextQuery_ReportsZeroMatchCountAndNoCappingClaim()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-no-text-count");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("   ", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        Assert.Equal(0, result.TextMatchCount);
        Assert.Null(result.Note);
    }
}
