using System.Collections.Generic;
using System.Threading;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Ingestion.Tests;

public sealed class FakeEvidenceSource : IEvidenceSource
{
    public string SourceName => "fake";

    public List<EvidenceBatch<CommitEvidence>> CommitBatches { get; } = [];
    public List<EvidenceBatch<IssueEvidence>> IssueBatches { get; } = [];
    public List<EvidenceBatch<PullRequestEvidence>> PullRequestBatches { get; } = [];
    public List<EvidenceBatch<ReleaseEvidence>> ReleaseBatches { get; } = [];

    public List<EvidenceCursor> CommitCursorsSeen { get; } = [];

    public async IAsyncEnumerable<EvidenceBatch<CommitEvidence>> ReadCommitsAsync(
        EvidenceCursor cursor, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        CommitCursorsSeen.Add(cursor);
        foreach (var batch in CommitBatches)
        {
            yield return batch;
        }

        await Task.CompletedTask;
    }

    public async IAsyncEnumerable<EvidenceBatch<IssueEvidence>> ReadIssuesAsync(
        EvidenceCursor cursor, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        foreach (var batch in IssueBatches)
        {
            yield return batch;
        }

        await Task.CompletedTask;
    }

    public async IAsyncEnumerable<EvidenceBatch<PullRequestEvidence>> ReadPullRequestsAsync(
        EvidenceCursor cursor, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        foreach (var batch in PullRequestBatches)
        {
            yield return batch;
        }

        await Task.CompletedTask;
    }

    public async IAsyncEnumerable<EvidenceBatch<ReleaseEvidence>> ReadReleasesAsync(
        EvidenceCursor cursor, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        foreach (var batch in ReleaseBatches)
        {
            yield return batch;
        }

        await Task.CompletedTask;
    }
}
