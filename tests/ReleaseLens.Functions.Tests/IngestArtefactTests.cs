using System.Net;
using System.Text.Json.Nodes;
using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

public class IngestArtefactTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static IEnumerable<JsonNode> Documents(SentRequest request) =>
        JsonNode.Parse(request.Body)!["value"]!.AsArray().Select(node => node!);

    private static bool Is(SentRequest request, string path) =>
        request.Uri.AbsolutePath.EndsWith(path, StringComparison.Ordinal);

    private static SentRequest Single(StubHttpHandler stub, string path) =>
        Assert.Single(stub.Requests, request => Is(request, path));

    private static IEnumerable<string> DeletedKeys(StubHttpHandler stub) =>
        stub.Requests
            .Where(request => Is(request, "/docs/index"))
            .SelectMany(Documents)
            .Where(document => (string?)document["@search.action"] == "delete")
            .Select(document => (string)document["chunk_id"]!);

    [Fact]
    public async Task Ingest_ChunksWithTheAppsOptions_EmbedsOnce_UpsertsEveryChunkWithItsKey()
    {
        var issue = IngestFixtures.Issue();
        var expected = IngestFixtures.ChunksOf(issue);
        Assert.True(expected.Count > 1, "The fixture must make several chunks, or this test shows nothing.");
        var stub = IngestFixtures.StubFor(expected.Count);

        var result = await IngestFixtures.Ingest(stub).IngestAsync(issue, Ct);

        Assert.Equal(new IngestResult(IngestFixtures.ArtefactId, expected.Count, 0), result);

        var embeddings = Single(stub, "/embeddings");
        Assert.Equal(
            expected.Select(chunk => chunk.Content),
            JsonNode.Parse(embeddings.Body)!["input"]!.AsArray().Select(node => (string?)node));

        var upserted = Documents(Single(stub, "/docs/index")).ToList();
        Assert.Equal(expected.Count, upserted.Count);
        for (var i = 0; i < expected.Count; i++)
        {
            Assert.Equal("mergeOrUpload", (string?)upserted[i]["@search.action"]);
            Assert.Equal(ChunkKeys.For(IngestFixtures.ArtefactId, i), (string?)upserted[i]["chunk_id"]);
            Assert.Equal(IngestFixtures.ArtefactId, (string?)upserted[i]["artefact"]);
            Assert.Equal(expected[i].Content, (string?)upserted[i]["content"]);
            Assert.Equal(i + 1f, (float)upserted[i]["vector"]![0]!);
        }

        Assert.Empty(DeletedKeys(stub));
    }

    [Fact]
    public async Task Ingest_DeletesEveryOldKeyOfTheArtefact_IncludingNumericOnes()
    {
        var issue = IngestFixtures.Issue();
        var count = IngestFixtures.ChunksOf(issue).Count;
        var fresh = Enumerable.Range(0, count).Select(i => ChunkKeys.For(IngestFixtures.ArtefactId, i));
        var leftOvers = new[] { "1017", "1018", ChunkKeys.For(IngestFixtures.ArtefactId, count) };
        var stub = IngestFixtures.StubFor(count, [.. fresh, .. leftOvers]);
        stub.EnqueueJson(IngestFixtures.IndexReply);

        var result = await IngestFixtures.Ingest(stub).IngestAsync(issue, Ct);

        Assert.Equal(3, result.Deleted);
        Assert.Equal(leftOvers.Order(), DeletedKeys(stub).Order());

        // The lookup asks for this artefact's keys, and only after the upsert.
        var lookup = Single(stub, "/docs/search");
        Assert.Equal($"artefact eq '{IngestFixtures.ArtefactId}'", (string?)JsonNode.Parse(lookup.Body)!["filter"]);
        var upsert = stub.Requests.First(request => Is(request, "/docs/index"));
        Assert.True(stub.Requests.IndexOf(lookup) > stub.Requests.IndexOf(upsert));
    }

    [Fact]
    public async Task Ingest_SendingTheSameArtefactTwice_WritesTheSameKeysAndDeletesNothing()
    {
        var issue = IngestFixtures.Issue();
        var count = IngestFixtures.ChunksOf(issue).Count;
        var keys = Enumerable.Range(0, count).Select(i => ChunkKeys.For(IngestFixtures.ArtefactId, i)).ToArray();
        var stub = IngestFixtures.StubFor(count);
        var ingest = IngestFixtures.Ingest(stub);

        var first = await ingest.IngestAsync(issue, Ct);

        // The second delivery finds the first's keys already there.
        stub.EnqueueJson(IngestFixtures.EmbeddingsReply(count))
            .EnqueueJson(IngestFixtures.IndexReply)
            .EnqueueJson(IngestFixtures.KeysReply(keys));
        var second = await ingest.IngestAsync(issue, Ct);

        Assert.Equal(first, second);
        var upserts = stub.Requests
            .Where(request => Is(request, "/docs/index"))
            .Select(request => Documents(request).Select(document => (string)document["chunk_id"]!).ToList())
            .ToList();
        Assert.Equal(2, upserts.Count);
        Assert.Equal(keys, upserts[0]);
        Assert.Equal(keys, upserts[1]);
        Assert.Empty(DeletedKeys(stub));
    }

    [Theory]
    [InlineData(HttpStatusCode.TooManyRequests)]
    [InlineData(HttpStatusCode.InternalServerError)]
    public async Task Ingest_AnEmbeddingOr429Failure_PropagatesSoTheQueueRetries(HttpStatusCode status)
    {
        var stub = new StubHttpHandler().Enqueue(status, """{"error":{"message":"SECRET-BACKEND-TEXT"}}""");

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => IngestFixtures.Ingest(stub).IngestAsync(IngestFixtures.Issue(), Ct));

        Assert.Equal(status, failure.StatusCode);
        // Nothing was written, so the retry starts clean.
        Assert.Single(stub.Requests);
    }

    [Fact]
    public async Task Ingest_AnIndexFailure_PropagatesAndDeletesNothing()
    {
        var count = IngestFixtures.ChunksOf(IngestFixtures.Issue()).Count;
        var stub = new StubHttpHandler()
            .EnqueueJson(IngestFixtures.EmbeddingsReply(count))
            .Enqueue(HttpStatusCode.ServiceUnavailable, "{}");

        await Assert.ThrowsAsync<HttpRequestException>(
            () => IngestFixtures.Ingest(stub).IngestAsync(IngestFixtures.Issue(), Ct));

        Assert.Empty(DeletedKeys(stub));
        Assert.DoesNotContain(stub.Requests, request => Is(request, "/docs/search"));
    }
}
