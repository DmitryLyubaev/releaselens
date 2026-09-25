using System.Text.Json;
using System.Text.Json.Serialization;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Api;

/// <summary>
/// A question to answer.
/// </summary>
/// <param name="IncludeEvidence">
/// Opt in to the text of each cited artefact, on <see cref="CitationDto.Evidence"/>. Off by
/// default and absent from the payload when off: a chunk runs to roughly a thousand
/// characters and an answer can cite twenty artefacts, so shipping it to everyone would
/// inflate every production response for the sake of the one consumer — the groundedness
/// judge — that has to read the text rather than the identifier.
/// </param>
public sealed record QueryRequest(string Question, int? K, bool? IncludeEvidence);

/// <summary>
/// One artefact the answer cites. <paramref name="Marker"/> is the number inside the
/// <c>[E&lt;n&gt;]</c> label as it appears in the answer text, and it is the ONLY way to match a
/// citation to its mention: the array holds just the cited artefacts, so its indices are not
/// the markers. A consumer that reads <c>citations[0]</c> as <c>[E1]</c> will mislabel every
/// answer that skips a marker.
/// </summary>
/// <param name="Evidence">
/// The text of this artefact as the agent saw it — every fragment of it, not the first —
/// present only when the request set <see cref="QueryRequest.IncludeEvidence"/>. It rides on
/// the citation rather than arriving as a parallel collection because the marker-to-text
/// binding is the whole point: a separate array would have to be re-joined on marker by every
/// consumer, and could drift into holding text for an artefact the citation list does not
/// carry. Absent means not requested; an empty array means requested and this artefact
/// reached the agent as an identifier with no attributable text — the two are different
/// facts and are reported differently. Nothing here is truncated.
/// </param>
public sealed record CitationDto(
    int Marker,
    string Type,
    string Key,
    string Title,
    string Url,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    IReadOnlyList<string>? Evidence = null);

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
    bool RetrievalFewerThanRequested,
    string? RetrievalNote,
    // How many artefacts the agent had accumulated when it validated the answer's markers.
    // `citations` is the cited subset, so this is what UnresolvedCitationMarkers was range-
    // checked against, and the gap between the two is the signal that used to be the bug.
    int AccumulatedCitationCount,
    IReadOnlyList<string> UnresolvedCitationMarkers,
    long ElapsedMs,
    // Every provider that answered, once each, in first-answer order; `Provider` is the one
    // that answered last. Empty when none did.
    IReadOnlyList<string>? Providers = null,
    // Present only when a provider's content filter blocked the request. Absent otherwise, not
    // null, so every response that was not filtered keeps exactly the shape it had before.
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    FilteredOutcomeDto? Filtered = null);

/// <summary>
/// A content filter blocked the request: <paramref name="Stage"/> is <c>prompt</c> or
/// <c>completion</c>, and <paramref name="Provider"/> is the provider whose filter it was.
/// </summary>
public sealed record FilteredOutcomeDto(string Stage, string Provider);

public sealed record QueryResponse(
    string Answer,
    IReadOnlyList<CitationDto> Citations,
    QueryMetadataDto Metadata);

/// <summary>One tool as published to an external client, mirroring MCP's tool shape.</summary>
public sealed record EvidenceToolDto(string Name, string Description, JsonElement InputSchema);

public sealed record EvidenceToolListResponse(IReadOnlyList<EvidenceToolDto> Tools);

public sealed record EvidenceCitationDto(string Type, string Key, string Title, string Url);

public sealed record EvidenceExcerptDto(string Type, string Key, string Text);

public sealed record EvidenceBoundsDto(int Returned, int Matched, bool Truncated);

/// <summary>
/// <paramref name="Earliest"/> and <paramref name="Latest"/> are ISO-8601 strings
/// (<c>DateTimeOffset.ToString("O")</c>) rather than <see cref="DateTimeOffset"/>, so the wire
/// shape does not depend on how the serialiser happens to render dates.
/// </summary>
public sealed record EvidenceCoverageDto(string? Earliest, string? Latest, bool CompleteForWindow);

public sealed record EvidenceToolResultResponse(
    string Kind,
    string Content,
    bool IsError,
    IReadOnlyList<EvidenceCitationDto> Citations,
    IReadOnlyList<EvidenceExcerptDto> Excerpts,
    EvidenceBoundsDto? Bounds = null,
    EvidenceCoverageDto? Coverage = null);
