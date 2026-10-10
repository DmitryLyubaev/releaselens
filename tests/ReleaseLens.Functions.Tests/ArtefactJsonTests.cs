using System.Text.Json.Nodes;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class ArtefactJsonTests
{
    private static readonly Guid Tenant = Guid.Parse("00000000-0000-0000-0000-000000000001");
    private static readonly DateTimeOffset At = new(2026, 10, 10, 9, 30, 0, TimeSpan.Zero);

    public static TheoryData<IEvidenceRecord, string> Records() => new()
    {
        {
            new CommitEvidence(Tenant, "0123456789abcdef0123456789abcdef01234567", "fix: retry on 429\n\nbody",
                "Ada Example", null, At.AddHours(-1), At, "https://example.com/commit/1",
                [new FileChange("src/a.cs", "modified", 3, 1), new FileChange("src/b.cs", "added", 10, 0)]),
            "commit"
        },
        {
            new IssueEvidence(Tenant, 42, "Crash on start", "It crashes.", "closed", ["bug", "p1"], "ada",
                At.AddDays(-2), At, "https://example.com/issues/42"),
            "issue"
        },
        {
            new PullRequestEvidence(Tenant, 7, "Add retries", "Adds retries.", "merged", At,
                "abc123", "main", "feat/retries", null, At.AddDays(-1), "https://example.com/pull/7"),
            "pull_request"
        },
        {
            new ReleaseEvidence(Tenant, "v1.44.1", null, "Notes", null, null, "https://example.com/releases/v1.44.1"),
            "release"
        },
    };

    [Theory]
    [MemberData(nameof(Records))]
    public void ArtefactJson_RoundTripsEachEntityType(IEvidenceRecord record, string wireName)
    {
        var json = ArtefactJson.Serialize(record);
        var parsed = ArtefactJson.Parse(json);

        // Records compare their lists by reference, so the parsed record is compared by its own JSON
        // and by type.
        Assert.IsType(record.GetType(), parsed);
        Assert.Equal(json, ArtefactJson.Serialize(parsed));
        Assert.Equal(record.Key, parsed.Key);
        var written = JsonNode.Parse(json)!.AsObject();
        Assert.Equal(wireName, (string?)written["entityType"]);
        Assert.Null(written["key"]);
    }

    [Fact]
    public void ArtefactJson_WritesCamelCaseNames()
    {
        var json = ArtefactJson.Serialize(new IssueEvidence(
            Tenant, 1, "t", "b", "open", [], null, At, null, "https://example.com/issues/1"));

        var written = JsonNode.Parse(json)!.AsObject();

        Assert.Contains("tenantId", written.Select(property => property.Key));
        Assert.Contains("createdAt", written.Select(property => property.Key));
        Assert.DoesNotContain("TenantId", written.Select(property => property.Key));
    }

    [Fact]
    public void ArtefactJson_WritesAuthorEmailAsNull()
    {
        var commit = new CommitEvidence(Tenant, "abc", "m", "Ada Example", "ada@example.com", At, At,
            "https://example.com/commit/abc", []);

        var written = JsonNode.Parse(ArtefactJson.Serialize(commit))!.AsObject();

        Assert.True(written.ContainsKey("authorEmail"));
        Assert.Null(written["authorEmail"]);
        Assert.Equal("Ada Example", (string?)written["authorName"]);
        Assert.DoesNotContain("ada@example.com", ArtefactJson.Serialize(commit));
        Assert.Null(((CommitEvidence)ArtefactJson.Parse(ArtefactJson.Serialize(commit))).AuthorEmail);
    }

    private static string ValidIssue() => ArtefactJson.Serialize(new IssueEvidence(
        Tenant, 42, "Crash on start", "It crashes.", "open", ["bug"], "ada", At, null,
        "https://example.com/issues/42"));

    private static string Without(string json, string property)
    {
        var node = JsonNode.Parse(json)!.AsObject();
        node.Remove(property);
        return node.ToJsonString();
    }

    private static string With(string json, string property, JsonNode? value)
    {
        var node = JsonNode.Parse(json)!.AsObject();
        node[property] = value;
        return node.ToJsonString();
    }

    public static TheoryData<string> BadInputs() => new()
    {
        "",
        "not json",
        "{\"entityType\":\"issue\"",                  // truncated
        "[]",
        "null",
        "42",
        "{}",                                          // no entityType
        With(ValidIssue(), "entityType", "branch"),    // unknown type
        With(ValidIssue(), "entityType", "Issue"),     // wire names are case-sensitive
        With(ValidIssue(), "entityType", null),
        With(ValidIssue(), "entityType", 7),
        Without(ValidIssue(), "title"),                // a field missing
        Without(ValidIssue(), "tenantId"),
        Without(ValidIssue(), "number"),
        With(ValidIssue(), "title", null),             // a non-nullable field null
        With(ValidIssue(), "number", "forty-two"),     // the wrong type
        With(ValidIssue(), "tenantId", "not-a-guid"),
        With(ValidIssue(), "createdAt", "yesterday"),
    };

    [Theory]
    [MemberData(nameof(BadInputs))]
    public void ArtefactJson_RejectsBadJsonUnknownTypeAndMissingFields(string json)
    {
        Assert.Throws<InvalidArtefactException>(() => ArtefactJson.Parse(json));
    }

    [Fact]
    public void ArtefactJson_AnInvalidArtefactMessageHoldsNoArtefactContent()
    {
        var json = With(With(ValidIssue(), "body", "SECRET-CONTENT"), "number", "forty-two");

        var failure = Assert.Throws<InvalidArtefactException>(() => ArtefactJson.Parse(json));

        Assert.DoesNotContain("SECRET-CONTENT", failure.Message);
    }

    [Fact]
    public void ArtefactJson_AnUnknownExtraFieldIsIgnored()
    {
        var parsed = ArtefactJson.Parse(With(ValidIssue(), "somethingNew", "x"));

        Assert.Equal(42, ((IssueEvidence)parsed).Number);
    }
}
