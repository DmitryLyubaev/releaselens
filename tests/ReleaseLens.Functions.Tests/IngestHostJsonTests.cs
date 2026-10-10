using System.Text.Json.Nodes;

namespace ReleaseLens.Functions.Tests;

public class IngestHostJsonTests
{
    [Fact]
    public void HostJson_HasThreeDequeuesAThirtySecondVisibilityAndNoEncoding()
    {
        // The project file copies the ingest app's host.json next to the test assembly.
        var host = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "ingest-host.json")))!;

        var queues = host["extensions"]!["queues"]!;
        Assert.Equal(3, (int?)queues["maxDequeueCount"]);
        Assert.Equal("00:00:30", (string?)queues["visibilityTimeout"]);
        // The parser reads plain or base64 itself, so the extension must not decode first.
        Assert.Equal("none", (string?)queues["messageEncoding"]);
    }
}
