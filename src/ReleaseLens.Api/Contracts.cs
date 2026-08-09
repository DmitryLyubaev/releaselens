using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Api;

public sealed record QueryRequest(string Question, int? K);

public sealed record CitationDto(string Type, string Key, string Title, string Url);

public sealed record QueryMetadataDto(
    int Iterations,
    IReadOnlyList<string> ToolsCalled,
    int TokensIn,
    int TokensOut,
    int CacheReadInputTokens,
    decimal CostUsd,
    string Provider,
    string Model,
    bool Degraded,
    string? DegradedReason,
    int RetrievedCount,
    int RequestedK,
    bool RetrievalTruncated,
    string? RetrievalNote,
    IReadOnlyList<string> UnresolvedCitationMarkers,
    long ElapsedMs);

public sealed record QueryResponse(
    string Answer,
    IReadOnlyList<CitationDto> Citations,
    QueryMetadataDto Metadata);
