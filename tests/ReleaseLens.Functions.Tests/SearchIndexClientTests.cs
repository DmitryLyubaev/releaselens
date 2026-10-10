using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class SearchIndexClientTests
{
    private static readonly Uri BaseAddress = new("https://example-search.search.windows.net/");

    private static SearchIndexClient Client(StubHttpHandler stub) =>
        new(new HttpClient(stub) { BaseAddress = BaseAddress }, "releaselens-chunks");

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static string Docs(string path) => $"/indexes/releaselens-chunks/docs{path}";

    [Fact]
    public async Task Search_UpsertSendsMergeOrUploadWithEveryField()
    {
        var stub = new StubHttpHandler().EnqueueJson("""{"value":[{"key":"k-0","status":true,"statusCode":200}]}""");
        var vector = new float[1536];
        vector[0] = 0.25f;
        vector[1535] = -0.5f;

        await Client(stub).UpsertAsync([new IndexChunk("k-0", "issue:7", "the content", vector)], Ct);

        var request = Assert.Single(stub.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal(Docs("/index"), request.Uri.AbsolutePath);
        Assert.Equal("?api-version=2026-04-01", request.Uri.Query);
        var document = Assert.Single(JsonNode.Parse(request.Body)!["value"]!.AsArray())!;
        Assert.Equal("mergeOrUpload", (string?)document["@search.action"]);
        Assert.Equal("k-0", (string?)document["chunk_id"]);
        Assert.Equal("issue:7", (string?)document["artefact"]);
        Assert.Equal("the content", (string?)document["content"]);
        var sent = document["vector"]!.AsArray().Select(node => (float)node!).ToArray();
        Assert.Equal(vector, sent);
    }

    [Fact]
    public async Task Search_UpsertOfNothingSendsNothing()
    {
        var stub = new StubHttpHandler();

        await Client(stub).UpsertAsync([], Ct);

        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Search_UpsertBatchesLargeSets()
    {
        var stub = new StubHttpHandler();
        var chunks = Enumerable.Range(0, 250).Select(i => new IndexChunk($"k-{i}", "issue:7", "c", [1f])).ToList();
        foreach (var _ in Enumerable.Range(0, 3))
        {
            stub.EnqueueJson("""{"value":[]}""");
        }

        await Client(stub).UpsertAsync(chunks, Ct);

        Assert.Equal([100, 100, 50],
            stub.Requests.Select(request => JsonNode.Parse(request.Body)!["value"]!.AsArray().Count));
    }

    [Fact]
    public async Task Search_UpsertWithADocumentTheServiceRefused_ThrowsNamingTheKeyAndStatusOnly()
    {
        var stub = new StubHttpHandler().Enqueue((HttpStatusCode)207, """
            {"value":[{"key":"k-0","status":true,"statusCode":200},
                      {"key":"k-1","status":false,"statusCode":400,"errorMessage":"backend text about content"}]}
            """);

        var failure = await Assert.ThrowsAsync<HttpRequestException>(() => Client(stub).UpsertAsync(
            [new IndexChunk("k-0", "issue:7", "a", [1f]), new IndexChunk("k-1", "issue:7", "b", [1f])], Ct));

        Assert.Contains("k-1", failure.Message);
        Assert.Contains("400", failure.Message);
        Assert.DoesNotContain("backend text", failure.Message);
    }

    [Fact]
    public async Task Search_DeleteSendsDeleteActionsByKey()
    {
        var stub = new StubHttpHandler().EnqueueJson("""{"value":[]}""");

        await Client(stub).DeleteAsync(["17", "k-2"], Ct);

        var request = Assert.Single(stub.Requests);
        Assert.Equal(Docs("/index"), request.Uri.AbsolutePath);
        var documents = JsonNode.Parse(request.Body)!["value"]!.AsArray();
        Assert.Equal(["delete", "delete"], documents.Select(document => (string?)document!["@search.action"]));
        Assert.Equal(["17", "k-2"], documents.Select(document => (string?)document!["chunk_id"]));
        Assert.All(documents, document => Assert.Equal(["@search.action", "chunk_id"],
            document!.AsObject().Select(property => property.Key)));
    }

    [Fact]
    public async Task Search_DeleteOfNothingSendsNothing()
    {
        var stub = new StubHttpHandler();

        await Client(stub).DeleteAsync([], Ct);

        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task KeysForArtefact_FiltersOnTheArtefactAndSelectsOnlyTheKey()
    {
        var stub = new StubHttpHandler().EnqueueJson("""{"value":[{"chunk_id":"a-0"},{"chunk_id":"17"}]}""");

        var keys = await Client(stub).KeysForArtefactAsync("release:python-1.44.1", Ct);

        Assert.Equal(["a-0", "17"], keys);
        var request = Assert.Single(stub.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal(Docs("/search"), request.Uri.AbsolutePath);
        Assert.Equal("?api-version=2026-04-01", request.Uri.Query);
        var body = JsonNode.Parse(request.Body)!;
        Assert.Equal("artefact eq 'release:python-1.44.1'", (string?)body["filter"]);
        Assert.Equal("chunk_id", (string?)body["select"]);
        Assert.Equal("*", (string?)body["search"]);
    }

    [Fact]
    public async Task KeysForArtefact_PagesUntilEveryKeyIsRead()
    {
        var nextLink = "https://example-search.search.windows.net/indexes/releaselens-chunks/docs/search?api-version=2026-04-01&$skiptoken=abc";
        var stub = new StubHttpHandler()
            .EnqueueJson(JsonSerializer.Serialize(new Dictionary<string, object>
            {
                ["value"] = new[] { new { chunk_id = "a-0" }, new { chunk_id = "a-1" } },
                ["@odata.nextLink"] = nextLink,
                ["@search.nextPageParameters"] = new { search = "*", filter = "artefact eq 'issue:7'", skip = 2 },
            }))
            .EnqueueJson("""{"value":[{"chunk_id":"a-2"}]}""");

        var keys = await Client(stub).KeysForArtefactAsync("issue:7", Ct);

        Assert.Equal(["a-0", "a-1", "a-2"], keys);
        Assert.Equal(2, stub.Requests.Count);
        Assert.Equal(nextLink, stub.Requests[1].Uri.ToString());
        Assert.Equal(2, (int?)JsonNode.Parse(stub.Requests[1].Body)!["skip"]);
    }

    [Fact]
    public async Task KeysForArtefact_ANextLinkToAnotherHost_IsNotFollowed()
    {
        var stub = new StubHttpHandler().EnqueueJson(JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["value"] = new[] { new { chunk_id = "a-0" } },
            ["@odata.nextLink"] = "https://elsewhere.example.com/collect",
        }));

        await Assert.ThrowsAsync<InvalidOperationException>(() => Client(stub).KeysForArtefactAsync("issue:7", Ct));

        Assert.Single(stub.Requests);
    }

    [Fact]
    public async Task KeysForArtefact_EscapesQuotesInTheFilter()
    {
        var stub = new StubHttpHandler().EnqueueJson("""{"value":[]}""");

        await Client(stub).KeysForArtefactAsync("issue:it's", Ct);

        var body = JsonNode.Parse(Assert.Single(stub.Requests).Body)!;
        Assert.Equal("artefact eq 'issue:it''s'", (string?)body["filter"]);
    }

    [Fact]
    public async Task HybridSemantic_SendsKeywordVectorAndSemanticAndReturnsRerankerScores()
    {
        var stub = new StubHttpHandler().EnqueueJson("""
            {"value":[
              {"@search.score":0.03,"@search.rerankerScore":3.4,"chunk_id":"a-0","artefact":"release:v1","content":"first"},
              {"@search.score":0.02,"@search.rerankerScore":2.1,"chunk_id":"b-1","artefact":"issue:9","content":"second"}]}
            """);
        var vector = new float[1536];
        vector[0] = 0.5f;

        var hits = await Client(stub).HybridSemanticAsync("how do I retry", vector, 5, Ct);

        Assert.Equal(
            [new SearchHit("a-0", "release:v1", "first", 3.4), new SearchHit("b-1", "issue:9", "second", 2.1)],
            hits);
        var request = Assert.Single(stub.Requests);
        Assert.Equal(Docs("/search"), request.Uri.AbsolutePath);
        Assert.Equal("?api-version=2026-04-01", request.Uri.Query);
        var body = JsonNode.Parse(request.Body)!;
        Assert.Equal("how do I retry", (string?)body["search"]);
        Assert.Equal(5, (int?)body["top"]);
        Assert.Equal("semantic", (string?)body["queryType"]);
        Assert.Equal("default", (string?)body["semanticConfiguration"]);
        Assert.Equal("chunk_id,artefact,content", (string?)body["select"]);
        var query = Assert.Single(body["vectorQueries"]!.AsArray())!;
        Assert.Equal("vector", (string?)query["kind"]);
        Assert.Equal("vector", (string?)query["fields"]);
        Assert.Equal(5, (int?)query["k"]);
        Assert.Equal(1536, query["vector"]!.AsArray().Count);
        Assert.Equal(0.5f, (float)query["vector"]![0]!);
    }

    [Fact]
    public async Task HybridSemantic_AHitWithNoRerankerScore_Throws()
    {
        var stub = new StubHttpHandler().EnqueueJson("""
            {"@search.semanticPartialResponseReason":"CapacityOverloaded","@search.semanticPartialResponseType":"baseResults",
             "value":[{"@search.score":0.03,"chunk_id":"a-0","artefact":"release:v1","content":"first"}]}
            """);

        await Assert.ThrowsAsync<InvalidOperationException>(
            () => Client(stub).HybridSemanticAsync("q", new float[1536], 5, Ct));
    }

    [Theory]
    [InlineData(HttpStatusCode.Forbidden)]
    [InlineData(HttpStatusCode.ServiceUnavailable)]
    public async Task Search_AFailureStatus_ThrowsWithTheStatusAndNoBody(HttpStatusCode status)
    {
        var stub = new StubHttpHandler().Enqueue(status, """{"error":{"message":"backend text with the index details"}}""");

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Client(stub).HybridSemanticAsync("q", new float[1536], 5, Ct));

        Assert.Equal(status, failure.StatusCode);
        Assert.Contains(((int)status).ToString(), failure.Message);
        Assert.DoesNotContain("backend text", failure.Message);
        Assert.DoesNotContain("index details", failure.Message);
    }

    [Fact]
    public async Task Search_AFailureStatusOnDelete_AndOnLookup_ThrowToo()
    {
        var stub = new StubHttpHandler()
            .Enqueue(HttpStatusCode.InternalServerError, "backend text")
            .Enqueue(HttpStatusCode.Unauthorized, "backend text");
        var client = Client(stub);

        var onDelete = await Assert.ThrowsAsync<HttpRequestException>(() => client.DeleteAsync(["k"], Ct));
        var onLookup = await Assert.ThrowsAsync<HttpRequestException>(() => client.KeysForArtefactAsync("issue:1", Ct));

        Assert.Equal(HttpStatusCode.InternalServerError, onDelete.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, onLookup.StatusCode);
        Assert.DoesNotContain("backend text", onDelete.Message + onLookup.Message);
    }
}
