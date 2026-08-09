using Dapper;

namespace ReleaseLens.Storage.Repositories;

public sealed record DailyUsage(long TokensIn, long TokensOut, decimal CostUsd)
{
    public long Total => TokensIn + TokensOut;
}

public sealed class TokenUsageRepository
{
    /// <summary>
    /// Reads today's usage and <b>locks the row for the rest of the transaction</b>.
    /// </summary>
    /// <remarks>
    /// The lock is what makes the daily cap a hard stop rather than a suggestion. Under
    /// Postgres's default read-committed isolation, two concurrent requests for the same
    /// tenant would otherwise both read the same pre-write total, both pass the check, and
    /// both spend — the cap could be exceeded by however many requests race through at once.
    /// The insert-then-lock is needed because there is nothing to lock on the first request
    /// of a day; `on conflict do nothing` makes it idempotent.
    /// </remarks>
    public async Task<DailyUsage> GetTodayAsync(TenantScope scope, DateOnly today, CancellationToken cancellationToken)
    {
        await scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into token_usage (tenant_id, usage_date) values (@TenantId, @today)
            on conflict (tenant_id, usage_date) do nothing
            """,
            new { scope.TenantId, today }, scope.Transaction, cancellationToken: cancellationToken));

        var row = await scope.Connection.QuerySingleOrDefaultAsync<DailyUsage>(new CommandDefinition(
            """
            select tokens_in as TokensIn, tokens_out as TokensOut, cost_usd as CostUsd
            from token_usage where usage_date = @today
            for update
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
