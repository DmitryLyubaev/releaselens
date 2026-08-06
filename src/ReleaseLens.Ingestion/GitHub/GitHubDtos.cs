using System.Text.Json.Serialization;

namespace ReleaseLens.Ingestion.GitHub;

// Only the fields the evidence schema needs. Everything else in GitHub's payload is ignored.

internal sealed record GhCommitListItem(
    [property: JsonPropertyName("sha")] string Sha,
    [property: JsonPropertyName("html_url")] string HtmlUrl,
    [property: JsonPropertyName("commit")] GhCommitDetail Commit,
    [property: JsonPropertyName("files")] IReadOnlyList<GhFile>? Files);

internal sealed record GhCommitDetail(
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("author")] GhSignature? Author,
    [property: JsonPropertyName("committer")] GhSignature? Committer);

internal sealed record GhSignature(
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("email")] string? Email,
    [property: JsonPropertyName("date")] DateTimeOffset? Date);

internal sealed record GhFile(
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("additions")] int Additions,
    [property: JsonPropertyName("deletions")] int Deletions);

internal sealed record GhIssue(
    [property: JsonPropertyName("number")] int Number,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("body")] string? Body,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("labels")] IReadOnlyList<GhLabel>? Labels,
    [property: JsonPropertyName("user")] GhUser? User,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    // The issues endpoint is queried with sort=updated and filtered by `since`, and GitHub
    // applies `since` to updated_at — NOT created_at. The resume cursor must therefore track
    // updated_at, or every incremental run re-walks a tail of already-ingested issues.
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt,
    [property: JsonPropertyName("closed_at")] DateTimeOffset? ClosedAt,
    [property: JsonPropertyName("html_url")] string HtmlUrl,
    [property: JsonPropertyName("pull_request")] GhPullRequestLink? PullRequest);

internal sealed record GhLabel([property: JsonPropertyName("name")] string Name);

internal sealed record GhUser([property: JsonPropertyName("login")] string Login);

internal sealed record GhPullRequestLink([property: JsonPropertyName("url")] string Url);

internal sealed record GhPullRequest(
    [property: JsonPropertyName("number")] int Number,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("body")] string? Body,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("merged_at")] DateTimeOffset? MergedAt,
    [property: JsonPropertyName("merge_commit_sha")] string? MergeCommitSha,
    [property: JsonPropertyName("base")] GhRef? Base,
    [property: JsonPropertyName("head")] GhRef? Head,
    [property: JsonPropertyName("user")] GhUser? User,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("html_url")] string HtmlUrl);

internal sealed record GhRef([property: JsonPropertyName("ref")] string Ref);

internal sealed record GhRelease(
    [property: JsonPropertyName("tag_name")] string TagName,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("body")] string? Body,
    [property: JsonPropertyName("published_at")] DateTimeOffset? PublishedAt,
    [property: JsonPropertyName("target_commitish")] string? TargetCommitish,
    [property: JsonPropertyName("html_url")] string HtmlUrl);
