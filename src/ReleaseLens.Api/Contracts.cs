using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Api;

public sealed record QueryRequest(string Question, int? K);

/// <summary>
/// One artefact the answer cites. <paramref name="Marker"/> is the number inside the
/// <c>[E&lt;n&gt;]</c> label as it appears in the answer text, and it is the ONLY way to match a
/// citation to its mention: the array holds just the cited artefacts, so its indices are not
/// the markers. A consumer that reads <c>citations[0]</c> as <c>[E1]</c> will mislabel every
/// answer that skips a marker.
/// </summary>
public sealed record CitationDto(int Marker, string Type, string Key, string Title, string Url);

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
    // How many artefacts the agent had accumulated when it validated the answer's markers.
    // `citations` is the cited subset, so this is what UnresolvedCitationMarkers was range-
    // checked against, and the gap between the two is the signal that used to be the bug.
    int AccumulatedCitationCount,
    IReadOnlyList<string> UnresolvedCitationMarkers,
    long ElapsedMs);

public sealed record QueryResponse(
    string Answer,
    IReadOnlyList<CitationDto> Citations,
    QueryMetadataDto Metadata);
