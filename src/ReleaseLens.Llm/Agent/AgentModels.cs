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
/// <param name="Excerpts">
/// Every fragment of this artefact's text the agent put in front of the model, in the order
/// it saw them and with exact duplicates dropped. ALL of them, not the first: a citation is
/// one entry per artefact while an artefact can span several chunks, and a claim supported
/// by chunk three reads as unsupported to anyone shown only chunk one. Empty when the
/// artefact reached the agent as an identifier with no attributable text.
/// </param>
public sealed record CitedEvidence(
    int Marker, EvidenceCitation Citation, IReadOnlyList<string> Excerpts);

public sealed record AgentAnswer(
    string Answer,
    IReadOnlyList<CitedEvidence> Citations,
    AgentMetadata Metadata);
