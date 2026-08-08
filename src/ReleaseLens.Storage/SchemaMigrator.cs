using System.Reflection;
using Dapper;
using Npgsql;

namespace ReleaseLens.Storage;

/// <summary>
/// Applies numbered SQL migrations embedded in this assembly, once each, in filename order.
/// Deliberately dependency-free: sixty lines here beats a migration framework for a schema
/// that is append-only and owned entirely by this project.
/// </summary>
public static class SchemaMigrator
{
    private const string ResourcePrefix = "ReleaseLens.Storage.Migrations.";

    public static async Task<int> MigrateAsync(string connectionString, CancellationToken cancellationToken)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellationToken);

        await connection.ExecuteAsync(
            """
            create table if not exists schema_migrations (
                name        text primary key,
                applied_at  timestamptz not null default now()
            )
            """);

        var applied = (await connection.QueryAsync<string>("select name from schema_migrations"))
            .ToHashSet(StringComparer.Ordinal);

        var assembly = typeof(SchemaMigrator).Assembly;
        var pending = assembly.GetManifestResourceNames()
            .Where(n => n.StartsWith(ResourcePrefix, StringComparison.Ordinal) && n.EndsWith(".sql", StringComparison.Ordinal))
            .OrderBy(n => n, StringComparer.Ordinal)
            .Where(n => !applied.Contains(ShortName(n)))
            .ToList();

        var count = 0;
        foreach (var resource in pending)
        {
            var sql = await ReadResourceAsync(assembly, resource, cancellationToken);

            await using var transaction = await connection.BeginTransactionAsync(cancellationToken);
            await connection.ExecuteAsync(sql, transaction: transaction);
            await connection.ExecuteAsync(
                "insert into schema_migrations (name) values (@name)",
                new { name = ShortName(resource) }, transaction);
            await transaction.CommitAsync(cancellationToken);

            count++;
        }

        return count;
    }

    private static string ShortName(string resourceName) => resourceName[ResourcePrefix.Length..];

    private static async Task<string> ReadResourceAsync(Assembly assembly, string name, CancellationToken cancellationToken)
    {
        await using var stream = assembly.GetManifestResourceStream(name)
            ?? throw new InvalidOperationException($"Embedded migration '{name}' not found.");
        using var reader = new StreamReader(stream);
        return await reader.ReadToEndAsync(cancellationToken);
    }
}
