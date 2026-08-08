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
    bool Truncated,
    string? Note);
