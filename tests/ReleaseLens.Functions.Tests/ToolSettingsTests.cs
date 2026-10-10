using Microsoft.Extensions.Configuration;
using ReleaseLens.Functions.Tool;

namespace ReleaseLens.Functions.Tests;

public class ToolSettingsTests
{
    private static readonly Dictionary<string, string?> Complete = new()
    {
        ["AZURE_CLIENT_ID"] = "00000000-0000-0000-0000-000000000003",
        ["OPENAI_BASE_URL"] = "https://example-account.openai.azure.com/openai/v1",
        ["OPENAI_EMBEDDING_DEPLOYMENT"] = "releaselens-embed-small",
        ["SEARCH_ENDPOINT"] = "https://example-search.search.windows.net",
        ["SEARCH_INDEX"] = "releaselens-chunks",
    };

    private static IConfiguration Configuration(Action<Dictionary<string, string?>>? change = null)
    {
        var values = new Dictionary<string, string?>(Complete);
        change?.Invoke(values);
        return new ConfigurationBuilder().AddInMemoryCollection(values).Build();
    }

    [Fact]
    public void Settings_ReadEverySetting_AndTheOpenAiBaseEndsWithASlash()
    {
        var settings = ToolSettings.From(Configuration());

        Assert.Equal("00000000-0000-0000-0000-000000000003", settings.ClientId);
        Assert.Equal(new Uri("https://example-account.openai.azure.com/openai/v1/"), settings.OpenAiBaseUrl);
        Assert.Equal("releaselens-embed-small", settings.EmbeddingDeployment);
        Assert.Equal(new Uri("https://example-search.search.windows.net"), settings.SearchEndpoint);
        Assert.Equal("releaselens-chunks", settings.SearchIndex);
    }

    [Theory]
    [InlineData("AZURE_CLIENT_ID")]
    [InlineData("OPENAI_BASE_URL")]
    [InlineData("OPENAI_EMBEDDING_DEPLOYMENT")]
    [InlineData("SEARCH_ENDPOINT")]
    [InlineData("SEARCH_INDEX")]
    public void Settings_AMissingOrBlankSetting_NamesItself(string name)
    {
        foreach (var value in new string?[] { null, "", "  " })
        {
            var error = Assert.Throws<InvalidOperationException>(
                () => ToolSettings.From(Configuration(values => values[name] = value)));

            Assert.Contains(name, error.Message);
        }
    }

    [Theory]
    [InlineData("OPENAI_BASE_URL")]
    [InlineData("SEARCH_ENDPOINT")]
    public void Settings_AUrlThatIsNotHttps_IsRefused_WithoutItsValue(string name)
    {
        const string insecure = "http://secret-host.example.com/path";

        var error = Assert.Throws<InvalidOperationException>(
            () => ToolSettings.From(Configuration(values => values[name] = insecure)));

        Assert.Contains(name, error.Message);
        Assert.DoesNotContain("secret-host", error.Message);
    }
}
