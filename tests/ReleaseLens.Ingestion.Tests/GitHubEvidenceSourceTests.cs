using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading.Tasks;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Ingestion.GitHub;
using Xunit;

namespace ReleaseLens.Ingestion.Tests;

public class GitHubEvidenceSourceTests
{
    private static readonly Guid Tenant = Guid.Parse("33333333-3333-3333-3333-333333333333");

    private static GitHubEvidenceSource Create(StubHttpMessageHandler handler, GitHubOptions? options = null)
    {
        var client = new HttpClient(handler) { BaseAddress = new Uri("https://api.github.com/") };
        return new GitHubEvidenceSource(
            client,
            options ?? new GitHubOptions
            {
                Owner = "microsoft",
                Repository = "semantic-kernel",
                Token = "ghp_test",
                SinceUtc = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
                FetchCommitFiles = false
            },
            new RateLimitGate(requestsPerHour: 5000, TimeProvider.System));
    }

    private const string OneCommitPage = """
    [
      {
        "sha": "abc1234def5678",
        "html_url": "https://github.com/microsoft/semantic-kernel/commit/abc1234def5678",
        "commit": {
          "message": "fix: null ref in planner\n\nDetails here.",
          "author": { "name": "Alice", "email": "alice@example.com", "date": "2026-03-01T10:00:00Z" },
          "committer": { "date": "2026-03-01T10:05:00Z" }
        }
      }
    ]
    """;

    [Fact]
    public void SourceName_IsGithub()
    {
        Assert.Equal("github", Create(new StubHttpMessageHandler()).SourceName);
    }

    [Fact]
    public async Task ReadCommits_MapsShaMessageAndTimestamps()
    {
        var handler = new StubHttpMessageHandler().EnqueuePage(OneCommitPage, nextLink: null);
        var source = Create(handler);

        var batches = await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken));
        var commit = batches.SelectMany(b => b.Items).Single();

        Assert.Equal("abc1234def5678", commit.Sha);
        Assert.StartsWith("fix: null ref in planner", commit.Message, StringComparison.Ordinal);
        Assert.Equal("Alice", commit.AuthorName);
        Assert.Equal(new DateTimeOffset(2026, 3, 1, 10, 5, 0, TimeSpan.Zero), commit.CommittedAt);
        Assert.Equal(Tenant, commit.TenantId);
    }

    [Fact]
    public async Task ReadCommits_SendsAuthorizationAndApiVersionHeaders()
    {
        var handler = new StubHttpMessageHandler().EnqueuePage(OneCommitPage, nextLink: null);
        var source = Create(handler);

        await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken));

        var request = handler.Requests[0];
        Assert.Equal("Bearer", request.Headers.Authorization!.Scheme);
        Assert.Equal("ghp_test", request.Headers.Authorization.Parameter);
        Assert.Equal("2022-11-28", request.Headers.GetValues("X-GitHub-Api-Version").Single());
        Assert.Contains("ReleaseLens", request.Headers.UserAgent.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ReadCommits_AppliesTheSinceParameterFromOptionsOnAFirstRun()
    {
        var handler = new StubHttpMessageHandler().EnqueuePage(OneCommitPage, nextLink: null);
        var source = Create(handler);

        await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken));

        Assert.Contains("since=2024-01-01T00%3A00%3A00Z", handler.Requests[0].RequestUri!.Query, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ReadCommits_ResumesFromTheCursorRatherThanTheConfiguredStart()
    {
        var handler = new StubHttpMessageHandler().EnqueuePage(OneCommitPage, nextLink: null);
        var source = Create(handler);
        var cursor = new EvidenceCursor(Tenant, "2026-03-01T10:05:00Z", null);

        await Collect(source.ReadCommitsAsync(cursor, TestContext.Current.CancellationToken));

        Assert.Contains("since=2026-03-01T10%3A05%3A00Z", handler.Requests[0].RequestUri!.Query, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ReadCommits_FollowsTheLinkHeaderToTheNextPage()
    {
        var handler = new StubHttpMessageHandler()
            .EnqueuePage(OneCommitPage, "https://api.github.com/repositories/1/commits?page=2")
            .EnqueuePage("[]", nextLink: null);

        var source = Create(handler);
        await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken));

        Assert.Equal(2, handler.Requests.Count);
        Assert.Contains("page=2", handler.Requests[1].RequestUri!.Query, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ReadCommits_SendsIfNoneMatchWhenTheCursorCarriesAnEtag()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.NotModified, string.Empty);
        var source = Create(handler);
        var cursor = new EvidenceCursor(Tenant, null, "W/\"deadbeef\"");

        await Collect(source.ReadCommitsAsync(cursor, TestContext.Current.CancellationToken));

        // EntityTagHeaderValue.Tag carries only the quoted portion — the W/ prefix lives in
        // IsWeak, so asserting the full weak form against .Tag can never pass. Assert both
        // parts, and the formatted value that actually goes on the wire.
        var ifNoneMatch = handler.Requests[0].Headers.IfNoneMatch.Single();
        Assert.True(ifNoneMatch.IsWeak);
        Assert.Equal("\"deadbeef\"", ifNoneMatch.Tag);
        Assert.Equal("W/\"deadbeef\"", ifNoneMatch.ToString());
    }

    [Fact]
    public async Task ReadCommits_NotModified_YieldsAnUnchangedBatchAndStops()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.NotModified, string.Empty);
        var source = Create(handler);
        var cursor = new EvidenceCursor(Tenant, null, "W/\"deadbeef\"");

        var batches = await Collect(source.ReadCommitsAsync(cursor, TestContext.Current.CancellationToken));

        Assert.Single(batches);
        Assert.True(batches[0].NotModified);
        Assert.Empty(batches[0].Items);
        Assert.Single(handler.Requests);
    }

    [Fact]
    public async Task ReadCommits_CapturesTheResponseEtagForTheNextRun()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.OK, OneCommitPage, response =>
            response.Headers.ETag = new System.Net.Http.Headers.EntityTagHeaderValue("\"fresh\""));

        var source = Create(handler);
        var batches = await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken));

        Assert.Equal("\"fresh\"", batches[0].ETag);
    }

    [Fact]
    public async Task ReadIssues_ExcludesPullRequests()
    {
        const string page = """
        [
          { "number": 10, "title": "A real issue", "body": "text", "state": "open", "labels": [{"name":"bug"}],
            "user": {"login":"bob"}, "created_at": "2026-01-01T00:00:00Z", "closed_at": null,
            "html_url": "https://github.com/microsoft/semantic-kernel/issues/10" },
          { "number": 11, "title": "Actually a PR", "body": "text", "state": "open", "labels": [],
            "user": {"login":"carol"}, "created_at": "2026-01-02T00:00:00Z", "closed_at": null,
            "html_url": "https://github.com/microsoft/semantic-kernel/pull/11",
            "pull_request": { "url": "https://api.github.com/repos/microsoft/semantic-kernel/pulls/11" } }
        ]
        """;

        var handler = new StubHttpMessageHandler().EnqueuePage(page, nextLink: null);
        var source = Create(handler);

        var issues = (await Collect(source.ReadIssuesAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken)))
            .SelectMany(b => b.Items).ToList();

        Assert.Single(issues);
        Assert.Equal(10, issues[0].Number);
        Assert.Equal(["bug"], issues[0].Labels);
    }

    [Fact]
    public async Task ReadReleases_MapsTagAndPublishedAt()
    {
        const string page = """
        [
          { "tag_name": "dotnet-1.30.0", "name": "1.30.0", "body": "notes",
            "published_at": "2026-03-01T00:00:00Z", "target_commitish": "main",
            "html_url": "https://github.com/microsoft/semantic-kernel/releases/tag/dotnet-1.30.0" }
        ]
        """;

        var handler = new StubHttpMessageHandler().EnqueuePage(page, nextLink: null);
        var source = Create(handler);

        var release = (await Collect(source.ReadReleasesAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken)))
            .SelectMany(b => b.Items).Single();

        Assert.Equal("dotnet-1.30.0", release.Tag);
        Assert.Equal(new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), release.PublishedAt);
    }

    [Fact]
    public async Task ReadCommits_WithFetchCommitFilesEnabled_MakesADetailRequestPerCommit()
    {
        const string detail = """
        {
          "sha": "abc1234def5678",
          "html_url": "https://github.com/microsoft/semantic-kernel/commit/abc1234def5678",
          "commit": {
            "message": "fix: null ref in planner",
            "author": { "name": "Alice", "email": "alice@example.com", "date": "2026-03-01T10:00:00Z" },
            "committer": { "date": "2026-03-01T10:05:00Z" }
          },
          "files": [
            { "filename": "dotnet/src/Planner.cs", "status": "modified", "additions": 12, "deletions": 3 }
          ]
        }
        """;

        var handler = new StubHttpMessageHandler()
            .EnqueuePage(OneCommitPage, nextLink: null)
            .EnqueueJson(detail);

        var source = Create(handler, new GitHubOptions
        {
            Owner = "microsoft",
            Repository = "semantic-kernel",
            Token = "ghp_test",
            SinceUtc = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero),
            FetchCommitFiles = true
        });

        var commit = (await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken)))
            .SelectMany(b => b.Items).Single();

        Assert.Single(commit.Files);
        Assert.Equal("dotnet/src/Planner.cs", commit.Files[0].Path);
        Assert.Equal(12, commit.Files[0].Additions);
    }

    [Fact]
    public async Task ReadCommits_ServerError_ThrowsRatherThanSilentlyReturningNothing()
    {
        var handler = new StubHttpMessageHandler().Enqueue(HttpStatusCode.InternalServerError, "{}");
        var source = Create(handler);

        await Assert.ThrowsAsync<HttpRequestException>(async () =>
            await Collect(source.ReadCommitsAsync(EvidenceCursor.Start(Tenant), TestContext.Current.CancellationToken)));
    }

    private static async Task<List<EvidenceBatch<T>>> Collect<T>(IAsyncEnumerable<EvidenceBatch<T>> source)
        where T : IEvidenceRecord
    {
        var batches = new List<EvidenceBatch<T>>();
        await foreach (var batch in source)
        {
            batches.Add(batch);
        }

        return batches;
    }
}
