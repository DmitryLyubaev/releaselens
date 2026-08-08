using System;
using System.Linq;
using System.Threading.Tasks;
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
    public async Task Retrieve_FewerThanK_IsReportedNotSilentlyTruncated()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-short");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner", NearVector, K: 50),
            TestContext.Current.CancellationToken);

        Assert.True(result.Truncated);
        Assert.Equal(50, result.RequestedK);
        Assert.True(result.Chunks.Count < 50);
        Assert.NotNull(result.Note);
        Assert.Contains("fewer", result.Note, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Retrieve_ExactlyKAvailable_IsNotMarkedTruncated()
    {
        var (factory, tenantId) = await SeedAsync("retrieve-exact");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var result = await new HybridRetriever().RetrieveAsync(scope,
            new RetrievalRequest("planner exception documentation macOS", NearVector, K: 3),
            TestContext.Current.CancellationToken);

        Assert.Equal(3, result.Chunks.Count);
        Assert.False(result.Truncated);
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
}
