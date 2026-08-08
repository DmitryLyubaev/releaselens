namespace ReleaseLens.Ingestion;

public sealed record IngestionReport
{
    public int CommitsIngested { get; init; }
    public int IssuesIngested { get; init; }
    public int PullRequestsIngested { get; init; }
    public int ReleasesIngested { get; init; }
    public int ChunksWritten { get; init; }
    public int ChunksEmbedded { get; init; }
    public int DeadLettered { get; init; }
    public TimeSpan Elapsed { get; init; }

    public override string ToString() =>
        $"commits={CommitsIngested} issues={IssuesIngested} prs={PullRequestsIngested} " +
        $"releases={ReleasesIngested} chunks={ChunksWritten} embedded={ChunksEmbedded} " +
        $"dead_lettered={DeadLettered} elapsed={Elapsed:hh\\:mm\\:ss}";
}
