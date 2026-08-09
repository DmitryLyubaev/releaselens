using System.Diagnostics;

namespace ReleaseLens.Core.Telemetry;

public static class ReleaseLensTelemetry
{
    public const string IngestionSourceName = "ReleaseLens.Ingestion";
    public const string RetrievalSourceName = "ReleaseLens.Retrieval";
    public const string AgentSourceName = "ReleaseLens.Agent";

    public static ActivitySource Retrieval { get; } = new(RetrievalSourceName);

    /// <summary>Passed to AddSource so every stage of the data flow is traced end to end.</summary>
    public static IReadOnlyList<string> SourceNames { get; } =
        [IngestionSourceName, RetrievalSourceName, AgentSourceName];
}
