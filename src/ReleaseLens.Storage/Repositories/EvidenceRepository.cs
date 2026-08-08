using Dapper;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Repositories;

/// <summary>
/// Every statement here is an upsert, because ingestion is resumable and will
/// legitimately re-present evidence it has already written after a restart.
/// </summary>
public sealed class EvidenceRepository
{
    public async Task<int> UpsertCommitsAsync(
        TenantScope scope, IReadOnlyList<CommitEvidence> commits, CancellationToken cancellationToken)
    {
        if (commits.Count == 0)
        {
            return 0;
        }

        var written = await scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into commits (tenant_id, sha, message, author_name, author_email, authored_at, committed_at, url)
            values (@TenantId, @Sha, @Message, @AuthorName, @AuthorEmail, @AuthoredAt, @CommittedAt, @Url)
            on conflict (tenant_id, sha) do update set
                message = excluded.message,
                author_name = excluded.author_name,
                author_email = excluded.author_email,
                authored_at = excluded.authored_at,
                committed_at = excluded.committed_at,
                url = excluded.url
            """,
            commits.Select(c => new
            {
                c.TenantId, c.Sha, c.Message, c.AuthorName, c.AuthorEmail, c.AuthoredAt, c.CommittedAt, c.Url
            }),
            scope.Transaction, cancellationToken: cancellationToken));

        var files = commits.SelectMany(c => c.Files.Select(f => new
        {
            c.TenantId, c.Sha, f.Path, f.Status, f.Additions, f.Deletions
        })).ToList();

        if (files.Count > 0)
        {
            await scope.Connection.ExecuteAsync(new CommandDefinition(
                """
                insert into files_changed (tenant_id, sha, path, status, additions, deletions)
                values (@TenantId, @Sha, @Path, @Status, @Additions, @Deletions)
                on conflict (tenant_id, sha, path) do update set
                    status = excluded.status,
                    additions = excluded.additions,
                    deletions = excluded.deletions
                """,
                files, scope.Transaction, cancellationToken: cancellationToken));
        }

        return written;
    }

    public async Task<CommitEvidence?> GetCommitAsync(TenantScope scope, string sha, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<CommitRow>(new CommandDefinition(
            """
            select tenant_id, sha, message, author_name, author_email, authored_at, committed_at, url
            from commits where sha = @sha
            """,
            new { sha }, scope.Transaction, cancellationToken: cancellationToken));

        if (row is null)
        {
            return null;
        }

        var files = (await scope.Connection.QueryAsync<FileRow>(new CommandDefinition(
            "select path, status, additions, deletions from files_changed where sha = @sha order by path",
            new { sha }, scope.Transaction, cancellationToken: cancellationToken)))
            .Select(f => new FileChange(f.path, f.status, f.additions, f.deletions))
            .ToList();

        return new CommitEvidence(
            row.tenant_id, row.sha, row.message, row.author_name, row.author_email,
            row.authored_at, row.committed_at, row.url, files);
    }

    public Task<int> UpsertIssuesAsync(
        TenantScope scope, IReadOnlyList<IssueEvidence> issues, CancellationToken cancellationToken)
    {
        if (issues.Count == 0)
        {
            return Task.FromResult(0);
        }

        return scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into issues (tenant_id, number, title, body, state, labels, author, created_at, closed_at, url)
            values (@TenantId, @Number, @Title, @Body, @State, @Labels, @Author, @CreatedAt, @ClosedAt, @Url)
            on conflict (tenant_id, number) do update set
                title = excluded.title, body = excluded.body, state = excluded.state,
                labels = excluded.labels, author = excluded.author, created_at = excluded.created_at,
                closed_at = excluded.closed_at, url = excluded.url
            """,
            issues.Select(i => new
            {
                i.TenantId, i.Number, i.Title, i.Body, i.State,
                Labels = i.Labels.ToArray(), i.Author, i.CreatedAt, i.ClosedAt, i.Url
            }),
            scope.Transaction, cancellationToken: cancellationToken));
    }

    public async Task<IssueEvidence?> GetIssueAsync(TenantScope scope, int number, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<IssueRow>(new CommandDefinition(
            """
            select tenant_id, number, title, body, state, labels, author, created_at, closed_at, url
            from issues where number = @number
            """,
            new { number }, scope.Transaction, cancellationToken: cancellationToken));

        return row is null
            ? null
            : new IssueEvidence(row.tenant_id, row.number, row.title, row.body, row.state,
                row.labels, row.author, row.created_at, row.closed_at, row.url);
    }

    public Task<int> UpsertPullRequestsAsync(
        TenantScope scope, IReadOnlyList<PullRequestEvidence> pullRequests, CancellationToken cancellationToken)
    {
        if (pullRequests.Count == 0)
        {
            return Task.FromResult(0);
        }

        return scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into pull_requests (tenant_id, number, title, body, state, merged_at, merge_commit_sha,
                                       base_ref, head_ref, author, created_at, url)
            values (@TenantId, @Number, @Title, @Body, @State, @MergedAt, @MergeCommitSha,
                    @BaseRef, @HeadRef, @Author, @CreatedAt, @Url)
            -- Every column the record carries is refreshed. base_ref and head_ref in particular
            -- are not immutable: a PR retargeted to a different base branch would otherwise keep
            -- the stale ref forever, because the pulls endpoint takes no `since` filter and is
            -- therefore re-read in full on every run.
            on conflict (tenant_id, number) do update set
                title = excluded.title, body = excluded.body, state = excluded.state,
                merged_at = excluded.merged_at, merge_commit_sha = excluded.merge_commit_sha,
                base_ref = excluded.base_ref, head_ref = excluded.head_ref,
                author = excluded.author, created_at = excluded.created_at, url = excluded.url
            """,
            pullRequests, scope.Transaction, cancellationToken: cancellationToken));
    }

    public Task<int> UpsertReleasesAsync(
        TenantScope scope, IReadOnlyList<ReleaseEvidence> releases, CancellationToken cancellationToken)
    {
        if (releases.Count == 0)
        {
            return Task.FromResult(0);
        }

        return scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into releases (tenant_id, tag, name, body, published_at, target_commitish, url)
            values (@TenantId, @Tag, @Name, @Body, @PublishedAt, @TargetCommitish, @Url)
            on conflict (tenant_id, tag) do update set
                name = excluded.name, body = excluded.body, published_at = excluded.published_at,
                target_commitish = excluded.target_commitish, url = excluded.url
            """,
            releases, scope.Transaction, cancellationToken: cancellationToken));
    }

    public async Task<ReleaseEvidence?> GetReleaseAsync(TenantScope scope, string tag, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<ReleaseRow>(new CommandDefinition(
            "select tenant_id, tag, name, body, published_at, target_commitish, url from releases where tag = @tag",
            new { tag }, scope.Transaction, cancellationToken: cancellationToken));

        return row is null
            ? null
            : new ReleaseEvidence(row.tenant_id, row.tag, row.name, row.body,
                row.published_at, row.target_commitish, row.url);
    }

    // Dapper materialisation targets. Lower-case names match the column names exactly,
    // which keeps the SQL free of "as Xxx" aliases on the read paths.
    //
    // These use init properties rather than a positional constructor. Npgsql reports a
    // timestamptz column as System.DateTime at the reader-schema level — even though the
    // bound value converts cleanly to DateTimeOffset — so Dapper's constructor matching for
    // a positional record can never find a matching constructor and throws at runtime.
    // Property-setter materialisation performs the DateTime -> DateTimeOffset conversion
    // correctly. Any row type carrying a DateTimeOffset needs this shape; FileRow below has
    // none, so it stays positional.
    private sealed record CommitRow
    {
        public Guid tenant_id { get; init; }
        public string sha { get; init; } = "";
        public string message { get; init; } = "";
        public string? author_name { get; init; }
        public string? author_email { get; init; }
        public DateTimeOffset authored_at { get; init; }
        public DateTimeOffset committed_at { get; init; }
        public string url { get; init; } = "";
    }

    private sealed record FileRow(string path, string status, int additions, int deletions);

    private sealed record IssueRow
    {
        public Guid tenant_id { get; init; }
        public int number { get; init; }
        public string title { get; init; } = "";
        public string body { get; init; } = "";
        public string state { get; init; } = "";
        public string[] labels { get; init; } = [];
        public string? author { get; init; }
        public DateTimeOffset created_at { get; init; }
        public DateTimeOffset? closed_at { get; init; }
        public string url { get; init; } = "";
    }

    private sealed record ReleaseRow
    {
        public Guid tenant_id { get; init; }
        public string tag { get; init; } = "";
        public string? name { get; init; }
        public string body { get; init; } = "";
        public DateTimeOffset? published_at { get; init; }
        public string? target_commitish { get; init; }
        public string url { get; init; } = "";
    }
}
