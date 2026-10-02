using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Dapper;
using Pgvector;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class ExactVectorSearchTests(PostgresFixture fixture)
{
    private const int Dimensions = 384;

    private static readonly float[] Query = Axis(0);

    /// <summary>
    /// Five vectors with known cosines to <see cref="Query"/>, listed out of rank order so
    /// that insertion order cannot pass for ranking. Each one leans away from the query along
    /// its own axis, so no two of them are parallel. Two non-zero components keep pgvector's
    /// single-precision accumulation well inside the 1e-6 tolerance.
    /// </summary>
    private static readonly (string Sha, double Cosine)[] Seeded =
    [
        ("sha_c050", 0.50),
        ("sha_c095", 0.95),
        ("sha_cm030", -0.30),
        ("sha_c080", 0.80),
        ("sha_c010", 0.10)
    ];

    private static float[] Axis(int index)
    {
        var v = new float[Dimensions];
        v[index] = 1f;
        return v;
    }

    private static float[] AtCosine(double cosine, int awayAxis)
    {
        var v = new float[Dimensions];
        v[0] = (float)cosine;
        v[awayAxis] = (float)Math.Sqrt(1 - (cosine * cosine));
        return v;
    }

    private static double BruteForceCosine(float[] a, float[] b)
    {
        double dot = 0, normA = 0, normB = 0;
        for (var i = 0; i < a.Length; i++)
        {
            dot += (double)a[i] * b[i];
            normA += (double)a[i] * a[i];
            normB += (double)b[i] * b[i];
        }

        return dot / Math.Sqrt(normA * normB);
    }

    private async Task<(TenantConnectionFactory Factory, Guid TenantId, List<(long ChunkId, string Artefact, float[] Vector)> Rows)>
        SeedAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var vectors = Seeded.Select((s, i) => AtCosine(s.Cosine, awayAxis: i + 1)).ToArray();

        var chunks = new ChunkRepository();
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await chunks.UpsertChunksAsync(scope,
            [.. Seeded.Select(s => new Chunk(tenantId, EntityType.Commit, s.Sha, 0, $"[commit {s.Sha}] seeded", 3))],
            TestContext.Current.CancellationToken);

        await chunks.UpsertEmbeddingsAsync(scope,
            [.. ids.Zip(vectors, (id, vector) => (id, vector))],
            "test-model", TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);

        var rows = ids.Select((id, i) => (id, $"commit:{Seeded[i].Sha}", vectors[i])).ToList();
        return (factory, tenantId, rows);
    }

    [Fact]
    public async Task ExactSearch_ReturnsTheTrueNearestNeighbours()
    {
        var (factory, tenantId, rows) = await SeedAsync("exact-search-order");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // k = 4 of 5, so the limit is exercised as well as the order.
        var results = await new ExactVectorSearch().SearchAsync(scope, Query, 4, TestContext.Current.CancellationToken);

        var expected = rows
            .Select(r => (r.ChunkId, r.Artefact, Score: BruteForceCosine(r.Vector, Query)))
            .OrderByDescending(r => r.Score)
            .Take(4)
            .ToList();

        Assert.Equal(expected.Select(e => e.ChunkId), results.Select(r => r.ChunkId));
        Assert.Equal(expected.Select(e => e.Artefact), results.Select(r => r.Artefact));
        Assert.Equal(["commit:sha_c095", "commit:sha_c080", "commit:sha_c050", "commit:sha_c010"],
            results.Select(r => r.Artefact));

        for (var i = 0; i < expected.Count; i++)
        {
            Assert.Equal(expected[i].Score, results[i].Score, tolerance: 1e-6);
        }
    }

    [Fact]
    public async Task ExactSearch_DoesNotUseTheHnswIndex()
    {
        var (factory, tenantId, _) = await SeedAsync("exact-search-plan");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // The same two statements SearchAsync sends, in the same transaction. enable_seqscan
        // stays at its default: enable_indexscan = off is a cost penalty, not a ban, and
        // turning sequential scans off too would set the two penalties against each other.
        await scope.Connection.ExecuteAsync(new CommandDefinition(
            ExactVectorSearch.DisableIndexScanSql,
            transaction: scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

        var lines = await scope.Connection.QueryAsync<string>(new CommandDefinition(
            "explain " + ExactVectorSearch.SearchSql, new { q = new Vector(Query), k = 4 },
            scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));
        var plan = string.Join('\n', lines);

        Assert.NotEmpty(plan);
        Assert.DoesNotContain("embeddings_hnsw_idx", plan, StringComparison.Ordinal);
    }
}
