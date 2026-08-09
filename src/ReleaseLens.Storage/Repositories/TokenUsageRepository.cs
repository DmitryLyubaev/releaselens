using Dapper;

namespace ReleaseLens.Storage.Repositories;

public sealed record DailyUsage(long TokensIn, long TokensOut, decimal CostUsd)
{
    public long Total => TokensIn + TokensOut;
}

public sealed class TokenUsageRepository
{
    public async Task<DailyUsage> GetTodayAsync(TenantScope scope, DateOnly today, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<DailyUsage>(new CommandDefinition(
            """
            select tokens_in as TokensIn, tokens_out as TokensOut, cost_usd as CostUsd
            from token_usage where usage_date = @today
            """,
            new { today }, scope.Transaction, cancellationToken: cancellationToken));

        return row ?? new DailyUsage(0, 0, 0m);
    }

    public Task RecordAsync(
        TenantScope scope, DateOnly today, long tokensIn, long tokensOut, decimal costUsd,
        CancellationToken cancellationToken)
        => scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into token_usage (tenant_id, usage_date, tokens_in, tokens_out, cost_usd)
            values (@TenantId, @today, @tokensIn, @tokensOut, @costUsd)
            on conflict (tenant_id, usage_date) do update set
                tokens_in = token_usage.tokens_in + excluded.tokens_in,
                tokens_out = token_usage.tokens_out + excluded.tokens_out,
                cost_usd = token_usage.cost_usd + excluded.cost_usd
            """,
            new { scope.TenantId, today, tokensIn, tokensOut, costUsd },
            scope.Transaction, cancellationToken: cancellationToken));
}
