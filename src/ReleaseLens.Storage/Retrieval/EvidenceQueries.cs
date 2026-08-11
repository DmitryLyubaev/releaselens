using Dapper;
using ReleaseLens.Core.Evidence;

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

public sealed record ReleaseSummary
{
    public string Tag { get; init; } = "";
    public string? Name { get; init; }
    public DateTimeOffset? PublishedAt { get; init; }
    public string Url { get; init; } = "";
}

/// <summary>
/// What the corpus actually holds for one date field: the earliest and latest dates
/// present, and how many records carry that date at all.
/// </summary>
/// <remarks>
/// Every aggregate this class computes is reported alongside its coverage, because a
/// count is only true of the corpus, never of the world. "How many commits in 2023"
/// over a corpus whose commits begin in 2024 is zero, and a bare zero is the most
/// confidently wrong answer this system can give. Coverage is what lets the caller
/// qualify the number or decline instead.
/// </remarks>
public sealed record EvidenceCoverage
{
    public DateTimeOffset? Earliest { get; init; }
    public DateTimeOffset? Latest { get; init; }
    public long Total { get; init; }
}

/// <summary>
/// One (entity type, date field) pair that may be counted over.
/// </summary>
/// <remarks>
/// <para>
/// The date field is never inferred from the entity type. "How many pull requests were
/// merged in 2024" and "how many were opened in 2024" are different questions with
/// different answers over the same table, so a caller that does not say which one it
/// means gets an error rather than a guess.
/// </para>
/// <para>
/// <see cref="Table"/> and <see cref="Column"/> reach the SQL by string interpolation,
/// which is safe only because they are compile-time constants of this assembly. This
/// whitelist is therefore the injection boundary: caller text is matched against
/// <see cref="FieldName"/> and then discarded, and nothing a caller typed is ever
/// interpolated. Do not add a member whose table or column comes from outside.
/// </para>
/// </remarks>
public sealed record EvidenceDateField(EntityType Type, string FieldName, string Table, string Column)
{
    /// <summary>How the predicate is shown to the caller, e.g. <c>pull_requests.merged_at</c>.</summary>
    public string Qualified => $"{Table}.{Column}";
}

public static class EvidenceDateFields
{
    public static readonly IReadOnlyList<EvidenceDateField> All =
    [
        new(EntityType.Commit, "committed", "commits", "committed_at"),
        new(EntityType.Issue, "created", "issues", "created_at"),
        new(EntityType.PullRequest, "created", "pull_requests", "created_at"),
        new(EntityType.PullRequest, "merged", "pull_requests", "merged_at"),
        new(EntityType.Release, "published", "releases", "published_at")
    ];

    public static EvidenceDateField? Find(EntityType type, string fieldName)
        => All.FirstOrDefault(f => f.Type == type && string.Equals(f.FieldName, fieldName, StringComparison.Ordinal));

    public static IReadOnlyList<string> NamesFor(EntityType type)
        => [.. All.Where(f => f.Type == type).Select(f => f.FieldName)];
}

/// <summary>
/// Relational queries the tools need that are not retrieval. Kept here because
/// Storage owns every line of SQL in the solution.
/// </summary>
public sealed class EvidenceQueries
{
    /// <summary>
    /// Counts records whose date field falls in the half-open window [since, until),
    /// optionally restricted to issues carrying at least one of <paramref name="labels"/>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// <c>is not null</c> is not redundant next to the range predicates. Both
    /// <c>merged_at</c> and <c>published_at</c> are nullable, so an unbounded count
    /// over "merged" must mean "pull requests that were actually merged" — without
    /// this predicate an unbounded call would return every row in the table, open
    /// ones included, and report it as a merge count.
    /// </para>
    /// <para>
    /// <paramref name="labels"/> is only meaningful for issues, and the caller is
    /// responsible for rejecting it for anything else — passing it with a non-issue
    /// field throws rather than counting unfiltered, because a quietly dropped filter
    /// returns a precise number for a question nobody asked.
    /// </para>
    /// </remarks>
    public async Task<long> CountAsync(
        TenantScope scope, EvidenceDateField field,
        DateTimeOffset? since, DateTimeOffset? until,
        IReadOnlyList<string>? labels, CancellationToken cancellationToken)
    {
        var filtered = labels is { Count: > 0 };

        if (filtered && field.Type != EntityType.Issue)
        {
            throw new ArgumentException(
                $"Label filtering is only supported for issues, not {field.Type}.", nameof(labels));
        }

        // Spliced in rather than left as a dormant `cardinality(@labels) = 0 or ...`
        // disjunct in one shared statement: only issues have a labels column, and
        // Postgres resolves column names when it parses, so the shared form would fail
        // outright for commits, pull requests and releases on a branch that never runs.
        var labelPredicate = filtered ? $"\n  and {IssueLabelOverlap}" : string.Empty;

        return await scope.Connection.ExecuteScalarAsync<long>(new CommandDefinition(
            $"""
            select count(*) from {field.Table}
            where {field.Column} is not null
              and (@since is null or {field.Column} >= @since)
              and (@until is null or {field.Column} < @until){labelPredicate}
            """,
            new { since, until, labels = filtered ? labels!.ToArray() : Array.Empty<string>() },
            scope.Transaction, cancellationToken: cancellationToken));
    }

    /// <summary>
    /// Array overlap — an issue matches if it carries at least one of the requested
    /// labels — compared case-insensitively.
    /// </summary>
    /// <remarks>
    /// Case-insensitive deliberately. The corpus labels are author-typed and
    /// inconsistently cased (<c>.NET</c>, <c>Build</c>, <c>Ignite</c> alongside
    /// <c>bug</c>, <c>python</c>, <c>stale</c>), while the label reaching this query was
    /// lifted from a natural-language question and will almost always arrive lowercase.
    /// An exact <c>&amp;&amp;</c> would answer "how many Build issues" with 0 while 166
    /// issues carry the label — a confident, checkable-looking zero, which is the exact
    /// failure the aggregate tools exist to remove. Nothing is lost the other way: no two
    /// labels in this corpus differ only by case, so folding case cannot conflate two
    /// distinct labels into one count.
    ///
    /// Both sides are lowered by Postgres rather than one by .NET, so the fold is done
    /// once under one collation and the two sides cannot disagree.
    /// </remarks>
    private const string IssueLabelOverlap =
        """
        array(select lower(mine) from unnest(labels) as mine)
              && array(select lower(wanted) from unnest(@labels) as wanted)
        """;

    public async Task<EvidenceCoverage> GetCoverageAsync(
        TenantScope scope, EvidenceDateField field, CancellationToken cancellationToken)
        => await scope.Connection.QuerySingleAsync<EvidenceCoverage>(new CommandDefinition(
            $"""
            select min({field.Column}) as Earliest,
                   max({field.Column}) as Latest,
                   count({field.Column}) as Total
            from {field.Table}
            """,
            transaction: scope.Transaction, cancellationToken: cancellationToken));

    /// <summary>
    /// How many releases match a listing filter, computed separately from the page
    /// itself so the tool can say "50 of 276" rather than leaving a capped list
    /// looking complete.
    /// </summary>
    public async Task<long> CountReleasesAsync(
        TenantScope scope, string? tagPrefix,
        DateTimeOffset? since, DateTimeOffset? until, CancellationToken cancellationToken)
        => await scope.Connection.ExecuteScalarAsync<long>(new CommandDefinition(
            $"select count(*) from releases where {ReleaseFilter}",
            new { tagPrefix, since, until }, scope.Transaction, cancellationToken: cancellationToken));

    public async Task<IReadOnlyList<ReleaseSummary>> ListReleasesAsync(
        TenantScope scope, string? tagPrefix,
        DateTimeOffset? since, DateTimeOffset? until, int limit, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<ReleaseSummary>(new CommandDefinition(
            $"""
            select tag as Tag, name as Name, published_at as PublishedAt, url as Url
            from releases
            where {ReleaseFilter}
            order by published_at desc nulls last, tag desc
            limit @limit
            """,
            new { tagPrefix, since, until, limit }, scope.Transaction, cancellationToken: cancellationToken))];

    /// <summary>
    /// Shared so the count and the page can never disagree about what "matching" means.
    /// </summary>
    /// <remarks>
    /// <c>starts_with</c> rather than <c>like @prefix || '%'</c>: a tag prefix is a
    /// literal, and several real tags in this corpus contain <c>_</c>, which LIKE reads
    /// as a single-character wildcard. Escaping it correctly is fiddlier than not
    /// using LIKE at all.
    /// </remarks>
    private const string ReleaseFilter =
        """
        (@tagPrefix is null or starts_with(tag, @tagPrefix))
          and (@since is null or published_at >= @since)
          and (@until is null or published_at < @until)
        """;

    // The tuple below carries a DateTimeOffset through Dapper's tuple deserialiser,
    // not through record-constructor matching — that is a different code path from
    // the one Task 5 hit (Npgsql reporting timestamptz as System.DateTime defeats
    // constructor matching for positional records specifically). The tuple path
    // converts correctly and the tests exercising it pass; do not "fix" this into a
    // record on the assumption it reproduces that bug, and do not "fix" it away either.
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
            $$"""
            select i.number as Number, i.title as Title, i.state as State, i.labels as Labels,
                   i.created_at as CreatedAt, i.url as Url,
                   -- Two independent ways a '#number' mention can name the wrong pull
                   -- request. Both matter because FindRegressionsTool reports this as
                   -- unhedged fact ("fixed by PR #N") that the agent loop treats as evidence.
                   --
                   -- 1. Numeric prefix. Anchored on both ends: the leading '#' anchors the
                   --    start, and \M (Postgres end-of-word) stops '#4211' from also
                   --    matching '#42110'. An unanchored ilike '%#4211%' would let issue
                   --    #4211 pick up a PR that only mentions #42110.
                   --
                   -- 2. Time travel. p.merged_at >= i.created_at is not a plausibility
                   --    heuristic, it is a hard impossibility filter: a pull request that
                   --    merged before the issue was even opened cannot be the thing that
                   --    fixed it, whatever its text says. '#number' is a repo-local token,
                   --    so a vendored changelog, a quoted upstream release note or a
                   --    dependabot bump body carrying another tracker's numbering matches
                   --    just as well as a real "Fixes #N". Without this predicate the
                   --    `order by p.merged_at limit 1` below made it worse than a coin
                   --    toss: taking the *earliest* match actively prefers the impossible
                   --    one. Keep the ordering - the earliest *qualifying* PR is the right
                   --    answer once the impossible ones are excluded.
                   (select p.number from pull_requests p
                    where p.merged_at is not null
                      and p.merged_at >= i.created_at
                      and (p.title ~ ('#' || i.number || '\M') or p.body ~ ('#' || i.number || '\M'))
                    order by p.merged_at limit 1) as FixedByPullRequest
            from issues i
            where {{RegressionFilter}}
            order by i.created_at desc
            limit @limit
            """,
            new { area, since, limit }, scope.Transaction, cancellationToken: cancellationToken))];

    /// <summary>
    /// How many issues match the regression filter, computed separately from the page so
    /// the tool can say "20 of 1047" rather than handing back a capped list that reads as
    /// the complete set and inviting the model to count its rows into a total.
    /// </summary>
    public async Task<long> CountRegressionCandidatesAsync(
        TenantScope scope, string area, DateTimeOffset? since, CancellationToken cancellationToken)
        => await scope.Connection.ExecuteScalarAsync<long>(new CommandDefinition(
            $"select count(*) from issues i where {RegressionFilter}",
            new { area, since }, scope.Transaction, cancellationToken: cancellationToken));

    /// <summary>
    /// Shared so the count and the page can never disagree about what "matching" means —
    /// a total computed from a wider or narrower predicate than the list beneath it would
    /// be worse than no total at all.
    /// </summary>
    /// <remarks>
    /// The label list is an explicit enumeration of the case variants seen in this corpus,
    /// not a case-insensitive match. Left as it stands: this is the tool's fixed definition
    /// of "regression", not caller input, so widening it would change what the tool means
    /// rather than fix a caller's guess about casing.
    /// </remarks>
    private const string RegressionFilter =
        """
        i.labels && array['bug', 'regression', 'Bug', 'kind:bug']
              and (@since is null or i.created_at >= @since)
              and (@area = '' or i.title ilike '%' || @area || '%' or i.body ilike '%' || @area || '%')
        """;
}
