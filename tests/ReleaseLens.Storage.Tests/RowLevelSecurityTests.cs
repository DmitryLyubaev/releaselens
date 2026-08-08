using System;
using System.Threading.Tasks;
using Dapper;
using Npgsql;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class RowLevelSecurityTests(PostgresFixture fixture)
{
    private static readonly Guid TenantA = Guid.Parse("aaaaaaaa-0000-0000-0000-000000000001");
    private static readonly Guid TenantB = Guid.Parse("bbbbbbbb-0000-0000-0000-000000000002");

    private async Task SeedAsync()
    {
        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        await connection.OpenAsync(TestContext.Current.CancellationToken);

        await connection.ExecuteAsync(
            """
            insert into tenants (tenant_id, slug, display_name, source_name, repo_owner, repo_name)
            values (@a, 'tenant-a', 'Tenant A', 'github', 'o', 'a'),
                   (@b, 'tenant-b', 'Tenant B', 'github', 'o', 'b')
            on conflict do nothing
            """,
            new { a = TenantA, b = TenantB });

        // This connection is the container's bootstrap superuser, so RLS is bypassed here
        // regardless of the set_config below - the seed is deliberately not the thing under
        // test. The set_config is retained only so each row is written under the tenant it
        // belongs to; it is not what grants the bypass. (Getting that attribution wrong is
        // the exact subtlety this whole test class exists to pin down.)
        foreach (var (tenant, sha) in new[] { (TenantA, "aaa1111"), (TenantB, "bbb2222") })
        {
            await using var transaction = await connection.BeginTransactionAsync(TestContext.Current.CancellationToken);
            await connection.ExecuteAsync(
                "select set_config('releaselens.tenant_id', @tenant, true)",
                new { tenant = tenant.ToString() }, transaction);
            await connection.ExecuteAsync(
                """
                insert into commits (tenant_id, sha, message, authored_at, committed_at, url)
                values (@tenant, @sha, 'seed', now(), now(), 'https://example.invalid')
                on conflict do nothing
                """,
                new { tenant, sha }, transaction);
            await transaction.CommitAsync(TestContext.Current.CancellationToken);
        }
    }

    [Fact]
    public async Task TenantScope_SeesOnlyItsOwnRows()
    {
        await SeedAsync();
        await using var factory = new TenantConnectionFactory(fixture.ConnectionString);

        await using var scope = await factory.OpenAsync(TenantA, TestContext.Current.CancellationToken);
        var shas = await scope.Connection.QueryAsync<string>(
            "select sha from commits", transaction: scope.Transaction);

        Assert.Equal(["aaa1111"], shas);
    }

    [Fact]
    public async Task QueryWithoutTenantFilter_CannotLeakAcrossTenants()
    {
        await SeedAsync();
        await using var factory = new TenantConnectionFactory(fixture.ConnectionString);

        await using var scope = await factory.OpenAsync(TenantB, TestContext.Current.CancellationToken);

        // Deliberately hostile: no WHERE clause at all.
        var count = await scope.Connection.ExecuteScalarAsync<int>(
            "select count(*) from commits", transaction: scope.Transaction);

        Assert.Equal(1, count);
    }

    [Fact]
    public async Task InsertForAnotherTenant_IsRejectedByTheWithCheckPolicy()
    {
        await SeedAsync();
        await using var factory = new TenantConnectionFactory(fixture.ConnectionString);

        await using var scope = await factory.OpenAsync(TenantA, TestContext.Current.CancellationToken);

        var exception = await Assert.ThrowsAsync<PostgresException>(async () =>
            await scope.Connection.ExecuteAsync(
                """
                insert into commits (tenant_id, sha, message, authored_at, committed_at, url)
                values (@tenant, 'sneaky', 'nope', now(), now(), 'https://example.invalid')
                """,
                new { tenant = TenantB }, scope.Transaction));

        Assert.Equal("42501", exception.SqlState);
    }

    [Theory]
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
    public async Task EveryProtectedTable_HasRlsEnabledForcedAndFullyPolicied(string table)
    {
        // Migration 006 maintains three hand-written lists of these ten tables - enable, force,
        // and the policy loop. They agree today, but a table dropped from one list, or an
        // eleventh added by a later migration and forgotten in all three, is a silent tenant
        // isolation hole with a completely green suite. Eight later tasks write to these
        // tables, so the guarantee is enumerated here rather than spot-checked on one table.
        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        await connection.OpenAsync(TestContext.Current.CancellationToken);

        const string relSql = """
            select {0} from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relname = @table
            """;

        Assert.True(
            await connection.ExecuteScalarAsync<bool>(string.Format(relSql, "relrowsecurity"), new { table }),
            $"{table}: row level security is not enabled");
        Assert.True(
            await connection.ExecuteScalarAsync<bool>(string.Format(relSql, "relforcerowsecurity"), new { table }),
            $"{table}: FORCE row level security is not set - policies would not apply to the table owner");

        var qual = await connection.ExecuteScalarAsync<string>(
            "select qual from pg_policies where schemaname = 'public' and tablename = @table", new { table });
        var withCheck = await connection.ExecuteScalarAsync<string>(
            "select with_check from pg_policies where schemaname = 'public' and tablename = @table", new { table });

        Assert.False(string.IsNullOrWhiteSpace(qual), $"{table}: policy has no USING expression - reads are unfiltered");
        Assert.False(string.IsNullOrWhiteSpace(withCheck), $"{table}: policy has no WITH CHECK expression - writes are unchecked");
    }

    [Fact]
    public async Task RoleSwitchedButNoTenantSet_SeesNothing()
    {
        await SeedAsync();

        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        await connection.OpenAsync(TestContext.Current.CancellationToken);
        await using var transaction = await connection.BeginTransactionAsync(TestContext.Current.CancellationToken);

        // The same role switch TenantConnectionFactory performs, deliberately without a tenant.
        await connection.ExecuteAsync("set local role releaselens_app", transaction: transaction);

        var count = await connection.ExecuteScalarAsync<int>(
            "select count(*) from commits", transaction: transaction);

        Assert.Equal(0, count);
    }

    [Fact]
    public async Task WithoutTheRoleSwitch_TheBootstrapRoleBypassesRls()
    {
        await SeedAsync();

        await using var connection = new NpgsqlConnection(fixture.ConnectionString);
        await connection.OpenAsync(TestContext.Current.CancellationToken);

        // This test documents the hazard rather than guarding against it. The container's
        // POSTGRES_USER is the cluster's bootstrap superuser, and Postgres exempts superusers
        // from RLS unconditionally — FORCE ROW LEVEL SECURITY closes only the table-owner
        // exemption. That is precisely why TenantConnectionFactory issues SET LOCAL ROLE, and
        // why migration 007 exists at all.
        //
        // If this ever returns 0, the connecting role has stopped being a superuser and the
        // role switch may have become redundant — do not remove it on that basis without
        // re-verifying against every environment, including Azure.
        var count = await connection.ExecuteScalarAsync<int>("select count(*) from commits");

        Assert.True(count >= 2,
            $"expected the bootstrap superuser to bypass RLS and see every tenant's rows, saw {count}");
    }
}
