using System.Text.Json.Nodes;

namespace ReleaseLens.Functions.Tests;

public class ToolHostJsonTests
{
    // The project file copies the tool app's host.json next to the test assembly.
    private static JsonNode Mcp() =>
        JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "tool-host.json")))!["extensions"]!["mcp"]!;

    [Fact]
    public void HostJson_TurnsTheMcpExtensionKeyOff()
    {
        // Anonymous means no mcp_extension key; App Service authentication and the gateway guard the endpoint instead.
        Assert.Equal("Anonymous", (string?)Mcp()["system"]!["webhookAuthorizationLevel"]);
    }

    [Fact]
    public void HostJson_NamesTheServerAndSaysWhatItIsFor()
    {
        Assert.Equal("releaselens-search", (string?)Mcp()["serverName"]);
        Assert.False(string.IsNullOrWhiteSpace((string?)Mcp()["instructions"]));
    }
}
