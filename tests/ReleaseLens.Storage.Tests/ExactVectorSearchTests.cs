using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Dapper;
using Npgsql;
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
        await SeedBulkVectorsAsync(factory, tenantId, BulkRows);

        // Control: the same SQL, without the class's setting, and with the scans that compete
        // with the index priced out. The planner must choose the HNSW index here. Otherwise a
        // plan without the index below could simply mean the table was too small to bother with
        // an index, and would show nothing about the setting.
        await using (var control = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await control.Connection.ExecuteAsync(new CommandDefinition(
                "set local enable_seqscan = off; set local enable_bitmapscan = off",
                transaction: control.Transaction, cancellationToken: TestContext.Current.CancellationToken));

            Assert.Contains("embeddings_hnsw_idx", await ExplainAsync(control), StringComparison.Ordinal);
        }

        // The same two statements SearchAsync sends, in the same transaction. enable_seqscan
        // stays at its default: enable_indexscan = off is a cost penalty, not a ban, and
        // turning sequential scans off too would set the two penalties against each other.
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        await scope.Connection.ExecuteAsync(new CommandDefinition(
            ExactVectorSearch.DisableIndexScanSql,
            transaction: scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

        var plan = await ExplainAsync(scope);
        Assert.NotEmpty(plan);
        Assert.DoesNotContain("embeddings_hnsw_idx", plan, StringComparison.Ordinal);
    }

    /// <summary>
    /// Enough rows, with statistics, that an ordered HNSW walk is cheaper than reading the
    /// tenant's rows through <c>embeddings_tenant_idx</c> and sorting them. On a few unanalysed
    /// rows that btree path is estimated as almost free, and the control could choose it.
    /// </summary>
    private const int BulkRows = 5_000;

    private async Task SeedBulkVectorsAsync(TenantConnectionFactory factory, Guid tenantId, int count)
    {
        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            // Seeded, so the same vectors go in on every run. The vector subquery refers to the
            // outer row only so that it is re-evaluated per row instead of once for them all.
            await scope.Connection.ExecuteAsync(new CommandDefinition(
                "select setseed(0.42)",
                transaction: scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

            await scope.Connection.ExecuteAsync(new CommandDefinition(
                """
                with chunks as (
                    insert into evidence_chunks (tenant_id, entity_type, entity_key, chunk_index, content, token_count)
                    select @tenantId, 'commit', 'bulk_' || g, 0, 'bulk chunk ' || g, 3
                    from generate_series(1, @count) g
                    returning chunk_id
                )
                insert into embeddings (chunk_id, tenant_id, model, dim, embedding)
                select chunk_id, @tenantId, 'test-model', 384,
                       (select array_agg(random()::real) from generate_series(1, 384) where chunk_id > 0)::vector
                from chunks
                """,
                new { tenantId, count },
                scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        // ANALYZE needs the table owner, so it runs as the container's bootstrap role rather
        // than inside a tenant scope, where releaselens_app would skip it with a warning.
        await using var owner = new NpgsqlConnection(fixture.ConnectionString);
        await owner.OpenAsync(TestContext.Current.CancellationToken);
        await owner.ExecuteAsync(new CommandDefinition(
            "analyze embeddings, evidence_chunks", cancellationToken: TestContext.Current.CancellationToken));
    }

    private static async Task<string> ExplainAsync(TenantScope scope)
    {
        var lines = await scope.Connection.QueryAsync<string>(new CommandDefinition(
            "explain " + ExactVectorSearch.SearchSql, new { q = new Vector(Query), k = 4 },
            scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));
        return string.Join('\n', lines);
    }
}
