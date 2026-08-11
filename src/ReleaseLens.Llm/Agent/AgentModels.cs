using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Tools;

namespace ReleaseLens.Llm.Agent;

public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    public string Model { get; set; } = "claude-sonnet-5";
    public int MaxIterations { get; set; } = 6;
    public int MaxTokens { get; set; } = 2048;
    public int SeedRetrievalK { get; set; } = 8;
}

public sealed record AgentMetadata(
    int Iterations,
    IReadOnlyList<string> ToolsCalled,
    TokenUsage Usage,
    decimal CostUsd,
    string Provider,
    string Model,
    bool Degraded,
    string? DegradedReason,
    int RetrievedCount,
    int RequestedK,
    bool RetrievalFewerThanRequested,
    string? RetrievalNote,
    // The size of the accumulated evidence pool the answer was written against - every
    // artefact any tool returned, cited or not. `Citations` no longer carries it, because it
    // now holds only what the answer cites; without this number a caller cannot reproduce the
    // range check that produced UnresolvedCitationMarkers, and "[E8] is unresolved" becomes an
    // assertion they have to take on faith.
    int AccumulatedCitationCount,
    IReadOnlyList<string> UnresolvedCitationMarkers);

/// <summary>
/// An artefact the answer actually cites, paired with the marker the answer used for it.
/// The marker is carried explicitly rather than implied by list position: the list is a
/// filtered subset, so position no longer equals marker.
/// </summary>
public sealed record CitedEvidence(int Marker, EvidenceCitation Citation);

public sealed record AgentAnswer(
    string Answer,
    IReadOnlyList<CitedEvidence> Citations,
    AgentMetadata Metadata);
