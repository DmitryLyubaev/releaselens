using System.Globalization;

namespace ReleaseLens.Core.Evidence;

/// <summary>Identifies one piece of evidence within a tenant. Stable across ingestion runs.</summary>
public readonly record struct EvidenceKey(EntityType Type, string Value)
{
    public override string ToString() => $"{Type.ToWireName()}:{Value}";
}

public interface IEvidenceRecord
{
    Guid TenantId { get; }
    EvidenceKey Key { get; }
    string Url { get; }
}

public sealed record FileChange(string Path, string Status, int Additions, int Deletions);

public sealed record CommitEvidence(
    Guid TenantId,
    string Sha,
    string Message,
    string? AuthorName,
    string? AuthorEmail,
    DateTimeOffset AuthoredAt,
    DateTimeOffset CommittedAt,
    string Url,
    IReadOnlyList<FileChange> Files) : IEvidenceRecord
{
    public EvidenceKey Key => new(EntityType.Commit, Sha);
}

public sealed record IssueEvidence(
    Guid TenantId,
    int Number,
    string Title,
    string Body,
    string State,
    IReadOnlyList<string> Labels,
    string? Author,
    DateTimeOffset CreatedAt,
    DateTimeOffset? ClosedAt,
    string Url) : IEvidenceRecord
{
    public EvidenceKey Key => new(EntityType.Issue, Number.ToString(CultureInfo.InvariantCulture));
}

public sealed record PullRequestEvidence(
    Guid TenantId,
    int Number,
    string Title,
    string Body,
    string State,
    DateTimeOffset? MergedAt,
    string? MergeCommitSha,
    string BaseRef,
    string HeadRef,
    string? Author,
    DateTimeOffset CreatedAt,
    string Url) : IEvidenceRecord
{
    public EvidenceKey Key => new(EntityType.PullRequest, Number.ToString(CultureInfo.InvariantCulture));
}

public sealed record ReleaseEvidence(
    Guid TenantId,
    string Tag,
    string? Name,
    string Body,
    DateTimeOffset? PublishedAt,
    string? TargetCommitish,
    string Url) : IEvidenceRecord
{
    public EvidenceKey Key => new(EntityType.Release, Tag);
}
