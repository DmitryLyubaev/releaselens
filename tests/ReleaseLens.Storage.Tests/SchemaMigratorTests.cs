using System.Threading.Tasks;
using Dapper;
using Npgsql;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class SchemaMigratorTests(PostgresFixture fixture)
{
    [Theory]
    [InlineData("tenants")]
    [InlineData("api_keys")]
    [InlineData("commits")]
    [InlineData("files_changed")]
    [InlineData("issues")]
    [InlineData("pull_requests")]
    [InlineData("releases")]
    [InlineData("evidence_chunks")]
    [InlineData("embeddings")]
    [InlineData("ingest_checkpoints")]
    [InlineData("embedding_dead_letter")]
    [InlineData("token_usage")]
    public async Task Migrate_CreatesTable(string table)
    {
        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        var exists = await connection.ExecuteScalarAsync<bool>(
            "select exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = @table)",
            new { table });

        Assert.True(exists, $"table {table} was not created");
    }

    [Fact]
    public async Task Migrate_CreatesHnswIndexOnEmbeddings()
    {
        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        var indexDefinition = await connection.ExecuteScalarAsync<string>(
            "select indexdef from pg_indexes where indexname = 'embeddings_hnsw_idx'");

        Assert.NotNull(indexDefinition);
        Assert.Contains("hnsw", indexDefinition, System.StringComparison.OrdinalIgnoreCase);
        Assert.Contains("vector_cosine_ops", indexDefinition, System.StringComparison.Ordinal);
    }

    [Fact]
    public async Task Migrate_IsIdempotent()
    {
        // The fixture already migrated. A second call must apply nothing, and a third must
        // agree - asserting the return value rather than a hard-coded count keeps this test
        // honest when a later task adds 008_*.sql.
        var secondRun = await SchemaMigrator.MigrateAsync(fixture.ConnectionString, TestContext.Current.CancellationToken);
        var thirdRun = await SchemaMigrator.MigrateAsync(fixture.ConnectionString, TestContext.Current.CancellationToken);

        Assert.Equal(0, secondRun);
        Assert.Equal(0, thirdRun);
    }
}
