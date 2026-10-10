using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class EmbeddingsClientTests
{
    private static readonly Uri BaseAddress = new("https://example-account.openai.azure.com/openai/v1/");

    private static EmbeddingsClient Client(StubHttpHandler stub) =>
        new(new HttpClient(stub) { BaseAddress = BaseAddress }, "releaselens-embed-small");

    private static float[] Vector(float first, int length = 1536)
    {
        var vector = new float[length];
        vector[0] = first;
        return vector;
    }

    private static string Reply(params (int Index, float[] Vector)[] items) =>
        JsonSerializer.Serialize(new
        {
            @object = "list",
            data = items.Select(item => new { @object = "embedding", index = item.Index, embedding = item.Vector }),
            model = "text-embedding-3-small",
        });

    [Fact]
    public async Task Embeddings_PostsTheDeploymentAndInputs_ReturnsVectorsInIndexOrder()
    {
        // The reply is out of order; the client must put it back.
        var stub = new StubHttpHandler().EnqueueJson(Reply((2, Vector(3)), (0, Vector(1)), (1, Vector(2))));

        var vectors = await Client(stub).EmbedAsync(["first", "second", "third"], TestContext.Current.CancellationToken);

        var request = Assert.Single(stub.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("/openai/v1/embeddings", request.Uri.AbsolutePath);
        var body = JsonNode.Parse(request.Body)!;
        Assert.Equal("releaselens-embed-small", (string?)body["model"]);
        Assert.Equal(["first", "second", "third"], body["input"]!.AsArray().Select(node => (string?)node));
        Assert.Equal([1f, 2f, 3f], vectors.Select(vector => vector[0]));
        Assert.All(vectors, vector => Assert.Equal(1536, vector.Length));
    }

    [Fact]
    public async Task Embeddings_NoInputs_SendsNothing()
    {
        var stub = new StubHttpHandler();

        var vectors = await Client(stub).EmbedAsync([], TestContext.Current.CancellationToken);

        Assert.Empty(vectors);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Embeddings_AVectorThatIsNot1536Long_Throws()
    {
        var stub = new StubHttpHandler().EnqueueJson(Reply((0, Vector(1)), (1, Vector(2, length: 3072))));

        var failure = await Assert.ThrowsAsync<InvalidOperationException>(
            () => Client(stub).EmbedAsync(["a", "b"], TestContext.Current.CancellationToken));

        Assert.Contains("1536", failure.Message);
    }

    [Fact]
    public async Task Embeddings_FewerVectorsThanInputs_Throws()
    {
        var stub = new StubHttpHandler().EnqueueJson(Reply((0, Vector(1))));

        await Assert.ThrowsAsync<InvalidOperationException>(
            () => Client(stub).EmbedAsync(["a", "b"], TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task Embeddings_AFailureStatus_ThrowsWithTheStatusAndNoBody()
    {
        var stub = new StubHttpHandler().Enqueue(HttpStatusCode.TooManyRequests, "backend text: the quota is gone");

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Client(stub).EmbedAsync(["a"], TestContext.Current.CancellationToken));

        Assert.Equal(HttpStatusCode.TooManyRequests, failure.StatusCode);
        Assert.Contains("429", failure.Message);
        Assert.DoesNotContain("backend text", failure.Message);
    }
}
