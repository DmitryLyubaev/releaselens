using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Retrieval;

public sealed record RetrievalRequest(string Query, float[] QueryVector, int K)
{
    public EntityType? EntityTypeFilter { get; init; }
    public DateTimeOffset? Since { get; init; }
    public DateTimeOffset? Until { get; init; }
    public string? PathFilter { get; init; }

    /// <summary>Weight given to the vector arm. The text arm gets 1 - Alpha.</summary>
    public double Alpha { get; init; } = 0.6;

    /// <summary>How many candidates each arm contributes before blending. 4x K, floored at 50.</summary>
    public int CandidatePoolSize => Math.Max(50, K * 4);
}

public sealed record RetrievedChunk(
    long ChunkId,
    EntityType Type,
    string EntityKey,
    int ChunkIndex,
    string Content,
    double VectorScore,
    double TextScore,
    double BlendedScore);

public sealed record RetrievalResult(
    IReadOnlyList<RetrievedChunk> Chunks,
    int RequestedK,
    int CandidatePoolSize,
    bool FewerThanRequested,
    string? Note,
    /// <summary>
    /// Chunks matching the text query under the same filters applied to the fts CTE
    /// (entity type, date/path). Zero when there is no text query. This counts only the
    /// text arm -- the vector arm has no equivalent "match count" because every chunk is
    /// a candidate at some distance, so there is nothing analogous to report for it.
    /// </summary>
    int TextMatchCount = 0,
    /// <summary>
    /// True when the text match count hit its counting cap (1000, reported after a
    /// 1001-row probe) rather than reflecting the exact number of matches.
    /// </summary>
    bool TextMatchCountIsLowerBound = false);
