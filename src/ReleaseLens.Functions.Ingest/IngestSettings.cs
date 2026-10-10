using Microsoft.Extensions.Configuration;

namespace ReleaseLens.Functions.Ingest;

/// <summary>
/// The app settings the ingest app needs, read once at startup so a missing one stops the app with
/// its name. Errors name a setting, never its value. The queue trigger's own connection
/// (<c>IngestQueue__queueServiceUri</c>, <c>IngestQueue__credential</c>, <c>IngestQueue__clientId</c>)
/// belongs to the Functions host, which reads it itself.
/// </summary>
public sealed record IngestSettings(
    string ClientId,
    Uri OpenAiBaseUrl,
    string EmbeddingDeployment,
    Uri SearchEndpoint,
    string SearchIndex,
    Uri BlobEndpoint)
{
    public static IngestSettings From(IConfiguration configuration) => new(
        ClientId: Text(configuration, "AZURE_CLIENT_ID"),
        // A trailing slash, or "embeddings" would replace "v1" instead of following it.
        OpenAiBaseUrl: HttpsUrl(configuration, "OPENAI_BASE_URL", slash: true),
        EmbeddingDeployment: Text(configuration, "OPENAI_EMBEDDING_DEPLOYMENT"),
        SearchEndpoint: HttpsUrl(configuration, "SEARCH_ENDPOINT", slash: false),
        SearchIndex: Text(configuration, "SEARCH_INDEX"),
        BlobEndpoint: HttpsUrl(configuration, "INGEST_BLOB_ENDPOINT", slash: false));

    private static string Text(IConfiguration configuration, string name) =>
        configuration[name] is { } value && !string.IsNullOrWhiteSpace(value)
            ? value.Trim()
            : throw new InvalidOperationException($"The app setting {name} is missing or empty.");

    private static Uri HttpsUrl(IConfiguration configuration, string name, bool slash)
    {
        var value = Text(configuration, name);
        if (slash && !value.EndsWith('/'))
        {
            value += "/";
        }

        // A bearer token goes wherever its client sends a request, so only https is accepted.
        return Uri.TryCreate(value, UriKind.Absolute, out var url) && url.Scheme == Uri.UriSchemeHttps
            ? url
            : throw new InvalidOperationException($"The app setting {name} is not an absolute https URL.");
    }
}
