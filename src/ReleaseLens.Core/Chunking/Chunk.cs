using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Core.Chunking;

public sealed record Chunk(
    Guid TenantId,
    EntityType Type,
    string EntityKey,
    int Index,
    string Content,
    int ApproxTokens);

/// <summary>
/// MaxTokens is well under the BGE-small 512-token window so the repeated header
/// and the overlap both fit without truncation.
/// </summary>
public sealed record ChunkOptions(int MaxTokens, int OverlapTokens, double CharsPerToken)
{
    public static ChunkOptions Default { get; } = new(MaxTokens: 320, OverlapTokens: 48, CharsPerToken: 3.6);

    public int MaxChars => (int)(MaxTokens * CharsPerToken);
    public int OverlapChars => (int)(OverlapTokens * CharsPerToken);
    public int EstimateTokens(string text) => (int)Math.Ceiling(text.Length / CharsPerToken);
}
