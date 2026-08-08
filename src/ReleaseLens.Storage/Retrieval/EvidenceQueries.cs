using Dapper;

namespace ReleaseLens.Storage.Retrieval;

// Init properties, not positional constructors: Npgsql reports timestamptz as
// System.DateTime at the reader-schema level, so Dapper cannot match a positional
// constructor taking DateTimeOffset and throws at runtime. Task 5 hit this; do not
// "tidy" these back into positional records.
public sealed record CommitSummary
{
    public string Sha { get; init; } = "";
    public string Message { get; init; } = "";
    public string? AuthorName { get; init; }
    public DateTimeOffset CommittedAt { get; init; }
    public string Url { get; init; } = "";
}

public sealed record RegressionCandidate
{
    public int Number { get; init; }
    public string Title { get; init; } = "";
    public string State { get; init; } = "";
    public string[] Labels { get; init; } = [];
    public DateTimeOffset CreatedAt { get; init; }
    public string Url { get; init; } = "";
    public int? FixedByPullRequest { get; init; }
}

/// <summary>
/// Relational queries the tools need that are not retrieval. Kept here because
/// Storage owns every line of SQL in the solution.
/// </summary>
public sealed class EvidenceQueries
{
    public async Task<(DateTimeOffset? From, DateTimeOffset? To)> GetReleaseWindowAsync(
        TenantScope scope, string fromTag, string toTag, CancellationToken cancellationToken)
    {
        var rows = (await scope.Connection.QueryAsync<(string tag, DateTimeOffset? published_at)>(
            new CommandDefinition(
                "select tag, published_at from releases where tag = any(@tags)",
                new { tags = new[] { fromTag, toTag } },
                scope.Transaction, cancellationToken: cancellationToken))).ToList();

        return (
            rows.FirstOrDefault(r => r.tag == fromTag).published_at,
            rows.FirstOrDefault(r => r.tag == toTag).published_at);
    }

    /// <summary>
    /// Commits between two release publication dates. This is a date-window query,
    /// not a git graph walk — the ingested evidence does not carry parent links.
    /// Stated plainly in the tool description so the model does not over-claim.
    /// </summary>
    public async Task<IReadOnlyList<CommitSummary>> GetCommitsBetweenAsync(
        TenantScope scope, DateTimeOffset from, DateTimeOffset to, int limit, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<CommitSummary>(new CommandDefinition(
            """
            select sha as Sha, message as Message, author_name as AuthorName,
                   committed_at as CommittedAt, url as Url
            from commits
            where committed_at > @from and committed_at <= @to
            order by committed_at
            limit @limit
            """,
            new { from, to, limit }, scope.Transaction, cancellationToken: cancellationToken))];

    public async Task<IReadOnlyList<RegressionCandidate>> FindRegressionCandidatesAsync(
        TenantScope scope, string area, DateTimeOffset? since, int limit, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<RegressionCandidate>(new CommandDefinition(
            """
            select i.number as Number, i.title as Title, i.state as State, i.labels as Labels,
                   i.created_at as CreatedAt, i.url as Url,
                   (select p.number from pull_requests p
                    where p.merged_at is not null
                      and (p.title ilike '%#' || i.number || '%' or p.body ilike '%#' || i.number || '%')
                    order by p.merged_at limit 1) as FixedByPullRequest
            from issues i
            where i.labels && array['bug', 'regression', 'Bug', 'kind:bug']
              and (@since is null or i.created_at >= @since)
              and (@area = '' or i.title ilike '%' || @area || '%' or i.body ilike '%' || @area || '%')
            order by i.created_at desc
            limit @limit
            """,
            new { area, since, limit }, scope.Transaction, cancellationToken: cancellationToken))];
}
