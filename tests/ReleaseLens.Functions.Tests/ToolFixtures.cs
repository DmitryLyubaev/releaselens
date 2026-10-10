using System.Text.Json.Nodes;
using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Tool;

namespace ReleaseLens.Functions.Tests;

/// <summary>What the tool tests share: the two services' replies and the search wired over one stub.</summary>
internal static class ToolFixtures
{
    public static string EmbeddingsReply() => IngestFixtures.EmbeddingsReply(1);

    /// <summary>A search reply with one ranked hit for each (artefact, content) pair; scores run 3, 2, 1, and so on.</summary>
    public static string SearchReply(params (string Artefact, string Content)[] hits) =>
        new JsonObject
        {
            ["value"] = new JsonArray([.. hits.Select((hit, i) => (JsonNode)new JsonObject
            {
                ["@search.score"] = 0.01,
                ["@search.rerankerScore"] = 3.0 - i,
                ["chunk_id"] = $"key-{i}",
                ["artefact"] = hit.Artefact,
                ["content"] = hit.Content,
            })]),
        }.ToJsonString();

    /// <summary>The embeddings call, then the search call, of one successful query.</summary>
    public static StubHttpHandler StubFor(params (string Artefact, string Content)[] hits) =>
        new StubHttpHandler().EnqueueJson(EmbeddingsReply()).EnqueueJson(SearchReply(hits));

    public static CorpusSearch Search(StubHttpHandler stub) =>
        new(
            new EmbeddingsClient(
                new HttpClient(stub) { BaseAddress = new Uri("https://example-account.openai.azure.com/openai/v1/") },
                "releaselens-embed-small"),
            new SearchIndexClient(
                new HttpClient(stub) { BaseAddress = new Uri("https://example-search.search.windows.net/") },
                "releaselens-chunks"));

    /// <summary>The embeddings request's body, as it was sent.</summary>
    public static JsonNode EmbeddingsBody(StubHttpHandler stub) => JsonNode.Parse(stub.Requests[0].Body)!;

    /// <summary>The search request's body, as it was sent.</summary>
    public static JsonNode SearchBody(StubHttpHandler stub) => JsonNode.Parse(stub.Requests[1].Body)!;
}
