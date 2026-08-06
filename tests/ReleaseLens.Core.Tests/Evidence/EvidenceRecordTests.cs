using System;
using ReleaseLens.Core.Evidence;
using Xunit;

namespace ReleaseLens.Core.Tests.Evidence;

public class EvidenceRecordTests
{
    private static readonly Guid Tenant = Guid.Parse("11111111-1111-1111-1111-111111111111");

    [Fact]
    public void CommitEvidence_Key_IsTypeAndSha()
    {
        var commit = new CommitEvidence(
            TenantId: Tenant,
            Sha: "abc123",
            Message: "fix: null ref in planner",
            AuthorName: "Alice",
            AuthorEmail: "alice@example.com",
            AuthoredAt: new DateTimeOffset(2026, 1, 2, 3, 4, 5, TimeSpan.Zero),
            CommittedAt: new DateTimeOffset(2026, 1, 2, 3, 4, 5, TimeSpan.Zero),
            Url: "https://github.com/o/r/commit/abc123",
            Files: []);

        Assert.Equal(new EvidenceKey(EntityType.Commit, "abc123"), commit.Key);
    }

    [Fact]
    public void IssueEvidence_Key_IsTypeAndNumber()
    {
        var issue = new IssueEvidence(
            TenantId: Tenant,
            Number: 4211,
            Title: "Planner drops tool results",
            Body: "Repro steps...",
            State: "closed",
            Labels: ["bug", "planner"],
            Author: "bob",
            CreatedAt: DateTimeOffset.UnixEpoch,
            ClosedAt: null,
            Url: "https://github.com/o/r/issues/4211");

        Assert.Equal(new EvidenceKey(EntityType.Issue, "4211"), issue.Key);
    }

    [Fact]
    public void EvidenceKey_ToString_IsStableAcrossCalls()
    {
        var key = new EvidenceKey(EntityType.Release, "v1.30.0");
        Assert.Equal("release:v1.30.0", key.ToString());
    }

    [Theory]
    [InlineData(EntityType.Commit, "commit")]
    [InlineData(EntityType.Issue, "issue")]
    [InlineData(EntityType.PullRequest, "pull_request")]
    [InlineData(EntityType.Release, "release")]
    public void EntityType_WireName_MatchesDatabaseCheckConstraint(EntityType type, string expected)
    {
        Assert.Equal(expected, type.ToWireName());
    }

    [Theory]
    [InlineData("commit", EntityType.Commit)]
    [InlineData("pull_request", EntityType.PullRequest)]
    public void EntityType_RoundTripsThroughWireName(string wire, EntityType expected)
    {
        Assert.Equal(expected, EntityTypeExtensions.FromWireName(wire));
    }

    [Fact]
    public void EntityType_FromWireName_RejectsUnknown()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => EntityTypeExtensions.FromWireName("epic"));
    }
}
