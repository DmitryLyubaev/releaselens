using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Core.Telemetry;
using ReleaseLens.Llm.Agent;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class TelemetryTests
{
    [Fact]
    public void SourceNames_CoverIngestionRetrievalAndAgent()
    {
        Assert.Contains("ReleaseLens.Ingestion", ReleaseLensTelemetry.SourceNames);
        Assert.Contains("ReleaseLens.Retrieval", ReleaseLensTelemetry.SourceNames);
        Assert.Contains("ReleaseLens.Agent", ReleaseLensTelemetry.SourceNames);
    }

    [Fact]
    public void AgentSpan_CarriesTheAttributesTheSpecRequires()
    {
        var captured = new List<Activity>();

        using var listener = new ActivityListener
        {
            ShouldListenTo = source => source.Name == "ReleaseLens.Agent",
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = captured.Add
        };

        ActivitySource.AddActivityListener(listener);

        using (var activity = QueryAgent.ActivitySource.StartActivity("agent.answer"))
        {
            activity?.SetTag("tokens_in", 1200);
            activity?.SetTag("tokens_out", 45);
            activity?.SetTag("cache_read_input_tokens", 1024);
            activity?.SetTag("cost_usd", 0.0034);
            activity?.SetTag("retrieval_k", 8);
            activity?.SetTag("provider", "anthropic");
            activity?.SetTag("model", "claude-sonnet-5");
        }

        var span = Assert.Single(captured);
        var tags = span.TagObjects.ToDictionary(t => t.Key, t => t.Value);

        foreach (var required in new[]
                 {
                     "tokens_in", "tokens_out", "cache_read_input_tokens",
                     "cost_usd", "retrieval_k", "provider", "model"
                 })
        {
            Assert.True(tags.ContainsKey(required), $"span is missing the '{required}' attribute");
        }
    }
}
