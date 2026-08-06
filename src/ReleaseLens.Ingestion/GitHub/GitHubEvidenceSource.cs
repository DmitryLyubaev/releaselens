using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Ingestion.GitHub;

public sealed class GitHubEvidenceSource : IEvidenceSource
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _client;
    private readonly GitHubOptions _options;
    private readonly RateLimitGate _gate;

    public string SourceName => "github";

    public GitHubEvidenceSource(HttpClient client, GitHubOptions options, RateLimitGate gate)
    {
        _client = client;
        _options = options;
        _gate = gate;

        if (_client.BaseAddress is null)
        {
            _client.BaseAddress = new Uri("https://api.github.com/");
        }

        _client.DefaultRequestHeaders.UserAgent.ParseAdd("ReleaseLens/1.0");
        _client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
        _client.DefaultRequestHeaders.TryAddWithoutValidation("X-GitHub-Api-Version", "2022-11-28");

        if (!string.IsNullOrEmpty(options.Token))
        {
            _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", options.Token);
        }
    }

    public async IAsyncEnumerable<EvidenceBatch<CommitEvidence>> ReadCommitsAsync(
        EvidenceCursor cursor, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var since = ResolveSince(cursor);
        var url = $"repos/{_options.Owner}/{_options.Repository}/commits" +
                  $"?per_page={_options.PageSize}&since={Uri.EscapeDataString(Iso(since))}";

        var firstPage = true;

        await foreach (var page in ReadPagesAsync<GhCommitListItem>(url, cursor.ETag, cancellationToken))
        {
            if (page.NotModified)
            {
                yield return EvidenceBatch<CommitEvidence>.Unchanged(cursor.ETag);
                yield break;
            }

            var commits = new List<CommitEvidence>(page.Items.Count);
            foreach (var item in page.Items)
            {
                var files = _options.FetchCommitFiles
                    ? await FetchCommitFilesAsync(item.Sha, cancellationToken)
                    : [];

                commits.Add(Map(cursor.TenantId, item, files));
            }

            var newest = commits.Count > 0 ? commits.Max(c => c.CommittedAt) : (DateTimeOffset?)null;

            yield return new EvidenceBatch<CommitEvidence>(
                commits,
                newest is { } n ? Iso(n) : null,
                firstPage ? page.ETag : null,
                NotModified: false);

            firstPage = false;
        }
    }

    public async IAsyncEnumerable<EvidenceBatch<IssueEvidence>> ReadIssuesAsync(
        EvidenceCursor cursor, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var since = ResolveSince(cursor);
        var url = $"repos/{_options.Owner}/{_options.Repository}/issues" +
                  $"?state=all&sort=updated&direction=asc&per_page={_options.PageSize}" +
                  $"&since={Uri.EscapeDataString(Iso(since))}";

        var firstPage = true;

        await foreach (var page in ReadPagesAsync<GhIssue>(url, cursor.ETag, cancellationToken))
        {
            if (page.NotModified)
            {
                yield return EvidenceBatch<IssueEvidence>.Unchanged(cursor.ETag);
                yield break;
            }

            // GitHub's issues endpoint returns pull requests too. They are ingested
            // separately with their own fields, so they are filtered out here.
            var issues = page.Items
                .Where(i => i.PullRequest is null)
                .Select(i => new IssueEvidence(
                    cursor.TenantId, i.Number, i.Title, i.Body ?? string.Empty, i.State,
                    i.Labels?.Select(l => l.Name).ToArray() ?? [],
                    i.User?.Login, i.CreatedAt, i.ClosedAt, i.HtmlUrl))
                .ToList();

            var newest = page.Items.Count > 0 ? page.Items.Max(i => i.CreatedAt) : (DateTimeOffset?)null;

            yield return new EvidenceBatch<IssueEvidence>(
                issues, newest is { } n ? Iso(n) : null, firstPage ? page.ETag : null, NotModified: false);

            firstPage = false;
        }
    }

    public async IAsyncEnumerable<EvidenceBatch<PullRequestEvidence>> ReadPullRequestsAsync(
        EvidenceCursor cursor, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var url = $"repos/{_options.Owner}/{_options.Repository}/pulls" +
                  $"?state=all&sort=updated&direction=asc&per_page={_options.PageSize}";

        var firstPage = true;

        await foreach (var page in ReadPagesAsync<GhPullRequest>(url, cursor.ETag, cancellationToken))
        {
            if (page.NotModified)
            {
                yield return EvidenceBatch<PullRequestEvidence>.Unchanged(cursor.ETag);
                yield break;
            }

            var pullRequests = page.Items.Select(p => new PullRequestEvidence(
                cursor.TenantId, p.Number, p.Title, p.Body ?? string.Empty, p.State,
                p.MergedAt, p.MergeCommitSha, p.Base?.Ref ?? "main", p.Head?.Ref ?? string.Empty,
                p.User?.Login, p.CreatedAt, p.HtmlUrl)).ToList();

            var newest = page.Items.Count > 0 ? page.Items.Max(p => p.CreatedAt) : (DateTimeOffset?)null;

            yield return new EvidenceBatch<PullRequestEvidence>(
                pullRequests, newest is { } n ? Iso(n) : null, firstPage ? page.ETag : null, NotModified: false);

            firstPage = false;
        }
    }

    public async IAsyncEnumerable<EvidenceBatch<ReleaseEvidence>> ReadReleasesAsync(
        EvidenceCursor cursor, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var url = $"repos/{_options.Owner}/{_options.Repository}/releases?per_page={_options.PageSize}";
        var firstPage = true;

        await foreach (var page in ReadPagesAsync<GhRelease>(url, cursor.ETag, cancellationToken))
        {
            if (page.NotModified)
            {
                yield return EvidenceBatch<ReleaseEvidence>.Unchanged(cursor.ETag);
                yield break;
            }

            var releases = page.Items.Select(r => new ReleaseEvidence(
                cursor.TenantId, r.TagName, r.Name, r.Body ?? string.Empty,
                r.PublishedAt, r.TargetCommitish, r.HtmlUrl)).ToList();

            yield return new EvidenceBatch<ReleaseEvidence>(
                releases, null, firstPage ? page.ETag : null, NotModified: false);

            firstPage = false;
        }
    }

    private sealed record Page<T>(IReadOnlyList<T> Items, string? NextUrl, string? ETag, bool NotModified);

    private async IAsyncEnumerable<Page<T>> ReadPagesAsync<T>(
        string firstUrl, string? etag, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var url = firstUrl;
        var isFirstRequest = true;

        while (url is not null)
        {
            await _gate.AcquireAsync(cancellationToken);

            using var request = new HttpRequestMessage(HttpMethod.Get, url);

            // The conditional request only makes sense for the first page: page two of a
            // result set has no meaningful entity tag from a previous run.
            if (isFirstRequest && !string.IsNullOrEmpty(etag))
            {
                request.Headers.TryAddWithoutValidation("If-None-Match", etag);
            }

            using var response = await _client.SendAsync(request, cancellationToken);
            ObserveRateLimit(response);

            if (response.StatusCode == HttpStatusCode.NotModified)
            {
                yield return new Page<T>([], null, etag, NotModified: true);
                yield break;
            }

            response.EnsureSuccessStatusCode();

            var items = await response.Content.ReadFromJsonAsync<List<T>>(JsonOptions, cancellationToken) ?? [];
            var next = ParseNextLink(response);

            yield return new Page<T>(items, next, response.Headers.ETag?.ToString(), NotModified: false);

            url = next;
            isFirstRequest = false;
        }
    }

    private async Task<IReadOnlyList<FileChange>> FetchCommitFilesAsync(string sha, CancellationToken cancellationToken)
    {
        await _gate.AcquireAsync(cancellationToken);

        using var response = await _client.GetAsync(
            $"repos/{_options.Owner}/{_options.Repository}/commits/{sha}", cancellationToken);

        ObserveRateLimit(response);
        response.EnsureSuccessStatusCode();

        var detail = await response.Content.ReadFromJsonAsync<GhCommitListItem>(JsonOptions, cancellationToken);

        return detail?.Files?
            .Select(f => new FileChange(f.Filename, f.Status, f.Additions, f.Deletions))
            .ToArray() ?? [];
    }

    private void ObserveRateLimit(HttpResponseMessage response)
    {
        if (response.Headers.TryGetValues("x-ratelimit-remaining", out var remainingValues) &&
            int.TryParse(remainingValues.FirstOrDefault(), CultureInfo.InvariantCulture, out var remaining) &&
            response.Headers.TryGetValues("x-ratelimit-reset", out var resetValues) &&
            long.TryParse(resetValues.FirstOrDefault(), CultureInfo.InvariantCulture, out var reset))
        {
            _gate.ObserveHeaders(remaining, reset);
        }
    }

    private static string? ParseNextLink(HttpResponseMessage response)
    {
        if (!response.Headers.TryGetValues("Link", out var values))
        {
            return null;
        }

        foreach (var segment in string.Join(',', values).Split(','))
        {
            var parts = segment.Split(';');
            if (parts.Length < 2 || !parts[1].Contains("rel=\"next\"", StringComparison.Ordinal))
            {
                continue;
            }

            return parts[0].Trim().Trim('<', '>');
        }

        return null;
    }

    private DateTimeOffset ResolveSince(EvidenceCursor cursor)
        => cursor.Value is { Length: > 0 } value &&
           DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal, out var parsed)
            ? parsed
            : _options.SinceUtc;

    private static string Iso(DateTimeOffset value)
        => value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);

    private static CommitEvidence Map(Guid tenantId, GhCommitListItem item, IReadOnlyList<FileChange> files)
        => new(
            tenantId,
            item.Sha,
            item.Commit.Message,
            item.Commit.Author?.Name,
            item.Commit.Author?.Email,
            item.Commit.Author?.Date ?? DateTimeOffset.UnixEpoch,
            item.Commit.Committer?.Date ?? item.Commit.Author?.Date ?? DateTimeOffset.UnixEpoch,
            item.HtmlUrl,
            files);
}
