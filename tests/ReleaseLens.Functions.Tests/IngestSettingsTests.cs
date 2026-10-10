using Microsoft.Extensions.Configuration;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

public class IngestSettingsTests
{
    private static readonly Dictionary<string, string?> Complete = new()
    {
        ["AZURE_CLIENT_ID"] = "00000000-0000-0000-0000-000000000002",
        ["OPENAI_BASE_URL"] = "https://example-account.openai.azure.com/openai/v1",
        ["OPENAI_EMBEDDING_DEPLOYMENT"] = "releaselens-embed-small",
        ["SEARCH_ENDPOINT"] = "https://example-search.search.windows.net",
        ["SEARCH_INDEX"] = "releaselens-chunks",
        ["INGEST_BLOB_ENDPOINT"] = "https://example.blob.core.windows.net",
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
        var settings = IngestSettings.From(Configuration());

        Assert.Equal("00000000-0000-0000-0000-000000000002", settings.ClientId);
        // Without the slash, "embeddings" would replace "v1" instead of following it.
        Assert.Equal(new Uri("https://example-account.openai.azure.com/openai/v1/"), settings.OpenAiBaseUrl);
        Assert.Equal("releaselens-embed-small", settings.EmbeddingDeployment);
        Assert.Equal(new Uri("https://example-search.search.windows.net"), settings.SearchEndpoint);
        Assert.Equal("releaselens-chunks", settings.SearchIndex);
        Assert.Equal(new Uri("https://example.blob.core.windows.net"), settings.BlobEndpoint);
    }

    [Theory]
    [InlineData("AZURE_CLIENT_ID")]
    [InlineData("OPENAI_BASE_URL")]
    [InlineData("OPENAI_EMBEDDING_DEPLOYMENT")]
    [InlineData("SEARCH_ENDPOINT")]
    [InlineData("SEARCH_INDEX")]
    [InlineData("INGEST_BLOB_ENDPOINT")]
    public void Settings_AMissingOrBlankSetting_FailsNamingIt(string name)
    {
        foreach (var blank in new string?[] { null, "", "  " })
        {
            var failure = Assert.Throws<InvalidOperationException>(
                () => IngestSettings.From(Configuration(values => values[name] = blank)));

            Assert.Contains(name, failure.Message);
        }
    }

    [Theory]
    [InlineData("OPENAI_BASE_URL")]
    [InlineData("SEARCH_ENDPOINT")]
    [InlineData("INGEST_BLOB_ENDPOINT")]
    public void Settings_AnEndpointThatIsNotAnHttpsUrl_FailsNamingItButNotItsValue(string name)
    {
        // The bearer token goes wherever the client sends a request, so plain http is refused.
        foreach (var value in new[] { "not a url", "http://example.com/", "/just/a/path" })
        {
            var failure = Assert.Throws<InvalidOperationException>(
                () => IngestSettings.From(Configuration(values => values[name] = value)));

            Assert.Contains(name, failure.Message);
            Assert.DoesNotContain(value, failure.Message);
        }
    }
}
