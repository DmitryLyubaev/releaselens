using System.Globalization;
using System.Text;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Core.Chunking;

/// <summary>
/// Turns evidence records into embeddable chunks. Each chunk repeats a header
/// naming its entity so a fragment retrieved in isolation is still self-describing
/// when it lands in a prompt.
/// </summary>
public sealed class EvidenceChunker(ChunkOptions options)
{
    private readonly ChunkOptions _options = options;

    public IReadOnlyList<Chunk> Chunk(CommitEvidence commit)
    {
        var shortSha = commit.Sha.Length >= 7 ? commit.Sha[..7] : commit.Sha;
        var subject = FirstLine(commit.Message);
        var header = $"[commit {shortSha}] {subject}";

        var body = new StringBuilder();
        body.Append("author: ").Append(commit.AuthorName ?? "unknown")
            .Append(" | committed: ").Append(commit.CommittedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))
            .Append('\n');

        var rest = RemainderAfterFirstLine(commit.Message);
        if (rest.Length > 0)
        {
            body.Append(TextNormaliser.Normalise(rest)).Append('\n');
        }

        if (commit.Files.Count > 0)
        {
            body.Append("files changed:\n");
            foreach (var file in commit.Files)
            {
                body.Append("  ").Append(file.Path)
                    .Append(" (").Append(file.Status)
                    .Append(" +").Append(file.Additions)
                    .Append(" -").Append(file.Deletions).Append(")\n");
            }
        }

        return Build(commit.TenantId, EntityType.Commit, commit.Sha, header, body.ToString());
    }

    public IReadOnlyList<Chunk> Chunk(IssueEvidence issue)
    {
        var header = $"[issue #{issue.Number} {issue.State}] {issue.Title}";

        var body = new StringBuilder();
        if (issue.Labels.Count > 0)
        {
            body.Append("labels: ").Append(string.Join(", ", issue.Labels)).Append('\n');
        }

        body.Append("opened by ").Append(issue.Author ?? "unknown")
            .Append(" on ").Append(issue.CreatedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));

        if (issue.ClosedAt is { } closed)
        {
            body.Append(", closed ").Append(closed.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        }

        body.Append('\n').Append(TextNormaliser.Normalise(issue.Body));

        return Build(issue.TenantId, EntityType.Issue,
            issue.Number.ToString(CultureInfo.InvariantCulture), header, body.ToString());
    }

    public IReadOnlyList<Chunk> Chunk(PullRequestEvidence pr)
    {
        var state = pr.MergedAt is null ? pr.State : "merged";
        var header = $"[pull request #{pr.Number} {state}] {pr.Title}";

        var body = new StringBuilder();
        body.Append(pr.HeadRef).Append(" -> ").Append(pr.BaseRef)
            .Append(" | opened by ").Append(pr.Author ?? "unknown")
            .Append(" on ").Append(pr.CreatedAt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));

        if (pr.MergedAt is { } merged)
        {
            body.Append(", merged ").Append(merged.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            if (pr.MergeCommitSha is { Length: >= 7 } sha)
            {
                body.Append(" as ").Append(sha[..7]);
            }
        }

        body.Append('\n').Append(TextNormaliser.Normalise(pr.Body));

        return Build(pr.TenantId, EntityType.PullRequest,
            pr.Number.ToString(CultureInfo.InvariantCulture), header, body.ToString());
    }

    public IReadOnlyList<Chunk> Chunk(ReleaseEvidence release)
    {
        var header = $"[release {release.Tag}] {release.Name ?? release.Tag}";

        var body = new StringBuilder();
        if (release.PublishedAt is { } published)
        {
            body.Append("published ").Append(published.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).Append('\n');
        }

        body.Append(TextNormaliser.Normalise(release.Body));

        return Build(release.TenantId, EntityType.Release, release.Tag, header, body.ToString());
    }

    private IReadOnlyList<Chunk> Build(Guid tenantId, EntityType type, string entityKey, string header, string body)
    {
        var headerLine = header + "\n";
        var budget = _options.MaxChars - headerLine.Length;

        // The guard has to keep budget comfortably ABOVE OverlapChars, not merely above zero.
        // Segment advances by (cut - overlap); if budget <= overlap then even the widest cut
        // gives a non-positive advance, Math.Max clamps it to 1, and a long body yields one
        // chunk per character. With the default options OverlapChars is 172 and MaxChars 1152,
        // so a header of 981-1052 chars lands in exactly that band — a real commit subject
        // length — and quietly explodes into thousands of one-character-shifted chunks.
        if (budget < _options.OverlapChars * 2)
        {
            // Pathological header (a novel-length commit subject). Truncate it rather than
            // producing chunks that are all header and no content.
            headerLine = headerLine[..Math.Min(headerLine.Length, _options.MaxChars / 2)] + "\n";
            budget = _options.MaxChars - headerLine.Length;
        }

        var segments = Segment(body.Trim(), budget, _options.OverlapChars);
        if (segments.Count == 0)
        {
            segments = [string.Empty];
        }

        var chunks = new List<Chunk>(segments.Count);
        for (var i = 0; i < segments.Count; i++)
        {
            var content = (headerLine + segments[i]).TrimEnd();
            chunks.Add(new Chunk(tenantId, type, entityKey, i, content, _options.EstimateTokens(content)));
        }

        return chunks;
    }

    /// <summary>
    /// Splits on paragraph boundaries, falling back to sentence boundaries and then
    /// to a hard character cut. Consecutive segments are intended to overlap by
    /// <paramref name="overlap"/> characters so a fact spanning a boundary survives in
    /// at least one segment whole — but that overlap is only real when
    /// <paramref name="budget"/> is comfortably larger than <paramref name="overlap"/>.
    /// Callers with a <c>budget</c> at or below <c>overlap</c> get correctness (the loop
    /// still terminates), not the overlap guarantee.
    /// </summary>
    private static List<string> Segment(string text, int budget, int overlap)
    {
        if (text.Length == 0)
        {
            return [];
        }

        if (text.Length <= budget)
        {
            return [text];
        }

        var segments = new List<string>();
        var position = 0;

        while (position < text.Length)
        {
            var remaining = text.Length - position;
            if (remaining <= budget)
            {
                segments.Add(text[position..]);
                break;
            }

            var window = text.AsSpan(position, budget);
            var cut = window.LastIndexOf("\n\n".AsSpan());

            if (cut < budget / 3)
            {
                cut = window.LastIndexOf(". ".AsSpan());
            }

            if (cut < budget / 3)
            {
                cut = window.LastIndexOf('\n');
            }

            if (cut < budget / 3)
            {
                cut = budget;
            }

            segments.Add(text[position..(position + cut)].Trim());

            var advance = Math.Max(1, cut - overlap);
            position += advance;
        }

        return segments;
    }

    private static string FirstLine(string text)
    {
        var newline = text.IndexOf('\n');
        return (newline < 0 ? text : text[..newline]).Trim();
    }

    private static string RemainderAfterFirstLine(string text)
    {
        var newline = text.IndexOf('\n');
        return newline < 0 ? string.Empty : text[(newline + 1)..].Trim();
    }
}
