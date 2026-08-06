namespace ReleaseLens.Core.Evidence;

/// <summary>
/// One page of evidence plus the cursor that resumes after it.
/// <paramref name="NotModified"/> is true when the source reported no change
/// (HTTP 304 for GitHub) — the caller should stop, not treat it as "empty".
/// </summary>
public sealed record EvidenceBatch<T>(
    IReadOnlyList<T> Items,
    string? NextCursor,
    string? ETag,
    bool NotModified) where T : IEvidenceRecord
{
    public static EvidenceBatch<T> Unchanged(string? etag) => new([], null, etag, true);
    public static EvidenceBatch<T> Final(IReadOnlyList<T> items, string? etag = null) => new(items, null, etag, false);
}

/// <summary>
/// A source of release and defect evidence. GitHub is the only implementation today.
/// A Jira or Azure DevOps connector implements this and nothing else changes.
/// Implementations must be resumable: given the cursor from a previous batch,
/// the next call returns strictly later evidence.
/// </summary>
public interface IEvidenceSource
{
    /// <summary>Stable identifier for the source, stored on checkpoints. e.g. "github".</summary>
    string SourceName { get; }

    IAsyncEnumerable<EvidenceBatch<CommitEvidence>> ReadCommitsAsync(
        EvidenceCursor cursor, CancellationToken cancellationToken);

    IAsyncEnumerable<EvidenceBatch<IssueEvidence>> ReadIssuesAsync(
        EvidenceCursor cursor, CancellationToken cancellationToken);

    IAsyncEnumerable<EvidenceBatch<PullRequestEvidence>> ReadPullRequestsAsync(
        EvidenceCursor cursor, CancellationToken cancellationToken);

    IAsyncEnumerable<EvidenceBatch<ReleaseEvidence>> ReadReleasesAsync(
        EvidenceCursor cursor, CancellationToken cancellationToken);
}

/// <summary>Where to resume from. Both fields may be null on a first run.</summary>
public sealed record EvidenceCursor(Guid TenantId, string? Value, string? ETag)
{
    public static EvidenceCursor Start(Guid tenantId) => new(tenantId, null, null);
}
