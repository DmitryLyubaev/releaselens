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
    bool RetrievalTruncated,
    string? RetrievalNote,
    IReadOnlyList<string> UnresolvedCitationMarkers);

public sealed record AgentAnswer(
    string Answer,
    IReadOnlyList<EvidenceCitation> Citations,
    AgentMetadata Metadata);
