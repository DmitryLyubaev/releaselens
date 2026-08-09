using Dapper;

namespace ReleaseLens.Storage.Repositories;

public sealed record TenantDefinition(
    string Slug,
    string DisplayName,
    string SourceName,
    string RepoOwner,
    string RepoName,
    long DailyTokenBudget);

public sealed record Tenant(
    Guid TenantId,
    string Slug,
    string DisplayName,
    string SourceName,
    string RepoOwner,
    string RepoName,
    long DailyTokenBudget);

public sealed class TenantRepository(TenantConnectionFactory factory)
{
    public async Task<Guid> CreateAsync(TenantDefinition definition, CancellationToken cancellationToken)
    {
        await using var connection = await factory.OpenUntenantedAsync(cancellationToken);

        return await connection.ExecuteScalarAsync<Guid>(new CommandDefinition(
            """
            insert into tenants (tenant_id, slug, display_name, source_name, repo_owner, repo_name, daily_token_budget)
            values (gen_random_uuid(), @Slug, @DisplayName, @SourceName, @RepoOwner, @RepoName, @DailyTokenBudget)
            on conflict (slug) do update set
                display_name = excluded.display_name,
                source_name = excluded.source_name,
                repo_owner = excluded.repo_owner,
                repo_name = excluded.repo_name,
                daily_token_budget = excluded.daily_token_budget
            returning tenant_id
            """,
            definition, cancellationToken: cancellationToken));
    }

    public async Task<Tenant?> FindBySlugAsync(string slug, CancellationToken cancellationToken)
    {
        await using var connection = await factory.OpenUntenantedAsync(cancellationToken);

        return await connection.QuerySingleOrDefaultAsync<Tenant>(new CommandDefinition(
            """
            select tenant_id as TenantId, slug as Slug, display_name as DisplayName,
                   source_name as SourceName, repo_owner as RepoOwner, repo_name as RepoName,
                   daily_token_budget as DailyTokenBudget
            from tenants
            where slug = @slug
            """,
            new { slug }, cancellationToken: cancellationToken));
    }

    public async Task<Tenant?> FindByIdAsync(Guid tenantId, CancellationToken cancellationToken)
    {
        await using var connection = await factory.OpenUntenantedAsync(cancellationToken);

        return await connection.QuerySingleOrDefaultAsync<Tenant>(new CommandDefinition(
            """
            select tenant_id as TenantId, slug as Slug, display_name as DisplayName,
                   source_name as SourceName, repo_owner as RepoOwner, repo_name as RepoName,
                   daily_token_budget as DailyTokenBudget
            from tenants
            where tenant_id = @tenantId
            """,
            new { tenantId }, cancellationToken: cancellationToken));
    }
}
