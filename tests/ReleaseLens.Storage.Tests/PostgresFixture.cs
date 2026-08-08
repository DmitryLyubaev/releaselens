using System.Threading.Tasks;
using Testcontainers.PostgreSql;
using Xunit;

namespace ReleaseLens.Storage.Tests;

public sealed class PostgresFixture : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder()
        .WithImage("pgvector/pgvector:pg17")
        .WithDatabase("releaselens_test")
        .WithUsername("releaselens")
        .WithPassword("releaselens_test_only")
        .Build();

    public string ConnectionString => _container.GetConnectionString();

    public async ValueTask InitializeAsync()
    {
        await _container.StartAsync();
        await SchemaMigrator.MigrateAsync(ConnectionString, TestContext.Current.CancellationToken);
    }

    public async ValueTask DisposeAsync() => await _container.DisposeAsync();
}

[CollectionDefinition(nameof(PostgresCollection))]
public sealed class PostgresCollection : ICollectionFixture<PostgresFixture>;
