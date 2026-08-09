using Dapper;
using Npgsql;
using Pgvector.Dapper;
using Pgvector.Npgsql;

namespace ReleaseLens.Storage;

/// <summary>
/// Every data access in the application goes through here. Opening a scope starts a
/// transaction and sets the tenant for its lifetime, which is what activates the
/// row-level security policies. There is no way to read evidence without a tenant.
/// </summary>
public sealed class TenantConnectionFactory : IAsyncDisposable
{
    private readonly NpgsqlDataSource _dataSource;

    static TenantConnectionFactory()
    {
        // Npgsql's UseVector() below teaches the ADO.NET layer about the vector column type,
        // but Dapper binds parameters through its own type lookup before an NpgsqlParameter
        // ever exists, so a bare Vector parameter fails with NotSupportedException unless
        // Dapper is separately told how to handle it. Registered once, process-wide, here,
        // since this is the one place every Dapper call in the application passes through.
        SqlMapper.AddTypeHandler(new VectorTypeHandler());

        // Same gap as Vector, for DateOnly: Dapper's static type map predates it, so a bare
        // DateOnly parameter (token_usage.usage_date) throws NotSupportedException without
        // this registered first.
        SqlMapper.AddTypeHandler(new DateOnlyTypeHandler());
    }

    public TenantConnectionFactory(string connectionString)
    {
        var builder = new NpgsqlDataSourceBuilder(connectionString);
        builder.UseVector();
        _dataSource = builder.Build();
    }

    public async Task<TenantScope> OpenAsync(Guid tenantId, CancellationToken cancellationToken)
    {
        var connection = await _dataSource.OpenConnectionAsync(cancellationToken);
        try
        {
            var transaction = await connection.BeginTransactionAsync(cancellationToken);

            // Drop the connecting role's RLS bypass for the life of this transaction.
            // Postgres evaluates policies against current_user and exempts superusers and
            // BYPASSRLS roles unconditionally — FORCE closes only the table-owner exemption —
            // so without this the policies in migration 006 never fire against the container's
            // bootstrap superuser. SET LOCAL is transaction-scoped: Postgres restores the
            // previous role on commit or rollback, so a pooled connection cannot leak it.
            await connection.ExecuteAsync(
                "set local role releaselens_app", transaction: transaction);

            // set_config with is_local = true scopes the setting to this transaction,
            // so a pooled connection never carries a tenant into its next borrower.
            await connection.ExecuteAsync(
                "select set_config('releaselens.tenant_id', @tenantId, true)",
                new { tenantId = tenantId.ToString() }, transaction);

            return new TenantScope(connection, transaction, tenantId);
        }
        catch
        {
            await connection.DisposeAsync();
            throw;
        }
    }

    /// <summary>
    /// For the authentication path only, which must read <c>tenants</c> and
    /// <c>api_keys</c> before a tenant is known. Neither table is under RLS.
    /// </summary>
    /// <remarks>
    /// This path runs as the privileged connecting role — there is no transaction here to
    /// scope a SET LOCAL ROLE to, and a plain SET ROLE would persist on a pooled connection.
    /// That is acceptable only because the two tables it touches are deliberately outside
    /// RLS. Never widen this method to read evidence: use <see cref="OpenAsync"/>.
    /// </remarks>
    public async Task<NpgsqlConnection> OpenUntenantedAsync(CancellationToken cancellationToken)
        => await _dataSource.OpenConnectionAsync(cancellationToken);

    public ValueTask DisposeAsync() => _dataSource.DisposeAsync();
}

public sealed class TenantScope(NpgsqlConnection connection, NpgsqlTransaction transaction, Guid tenantId)
    : IAsyncDisposable
{
    public NpgsqlConnection Connection { get; } = connection;
    public NpgsqlTransaction Transaction { get; } = transaction;
    public Guid TenantId { get; } = tenantId;

    public Task CommitAsync(CancellationToken cancellationToken) => Transaction.CommitAsync(cancellationToken);

    public async ValueTask DisposeAsync()
    {
        await Transaction.DisposeAsync();
        await Connection.DisposeAsync();
    }
}
