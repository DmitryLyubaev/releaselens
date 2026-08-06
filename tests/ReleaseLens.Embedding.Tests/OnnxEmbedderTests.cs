using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using ReleaseLens.Embedding;
using Xunit;

namespace ReleaseLens.Embedding.Tests;

public class OnnxEmbedderTests : IDisposable
{
    private readonly OnnxEmbedder _embedder;

    public OnnxEmbedderTests()
    {
        var modelDir = ModelLocator.Resolve();

        // xunit.v3 1.1.0 does not expose the `Skip.If` dynamic-skip helper the brief
        // describes; `Assert.SkipWhen` is this version's documented equivalent - it
        // throws a special exception the runner recognises as a skip, not a failure.
        Assert.SkipWhen(!Directory.Exists(modelDir), $"Model not present at {modelDir}. Run scripts/fetch-model.ps1.");
        _embedder = new OnnxEmbedder(new EmbedderOptions { ModelDirectory = modelDir });
    }

    public void Dispose() => _embedder.Dispose();

    [Fact]
    public void Dimensions_Is384()
    {
        Assert.Equal(384, _embedder.Dimensions);
    }

    [Fact]
    public void ModelName_IdentifiesTheModelStoredWithEachEmbedding()
    {
        Assert.Equal("bge-small-en-v1.5", _embedder.ModelName);
    }

    [Fact]
    public async Task EmbedDocuments_ReturnsOneVectorPerInput()
    {
        var vectors = await _embedder.EmbedDocumentsAsync(
            ["first document", "second document", "third document"],
            TestContext.Current.CancellationToken);

        Assert.Equal(3, vectors.Count);
        Assert.All(vectors, v => Assert.Equal(384, v.Length));
    }

    [Fact]
    public async Task EmbedDocuments_ProducesUnitLengthVectors()
    {
        var vectors = await _embedder.EmbedDocumentsAsync(
            ["the planner drops tool results when the tool returns null"],
            TestContext.Current.CancellationToken);

        var magnitude = MathF.Sqrt(vectors[0].Sum(x => x * x));
        Assert.InRange(magnitude, 0.999f, 1.001f);
    }

    [Fact]
    public async Task EmbedDocuments_IsDeterministic()
    {
        var first = await _embedder.EmbedDocumentsAsync(["stable input"], TestContext.Current.CancellationToken);
        var second = await _embedder.EmbedDocumentsAsync(["stable input"], TestContext.Current.CancellationToken);

        for (var i = 0; i < 384; i++)
        {
            Assert.Equal(first[0][i], second[0][i], 5);
        }
    }

    [Fact]
    public async Task RelatedTextsAreCloserThanUnrelatedOnes()
    {
        var vectors = await _embedder.EmbedDocumentsAsync(
        [
            "fix a null reference exception in the function calling planner",
            "resolve a NullReferenceException raised by the planner",
            "update the README with installation instructions for macOS"
        ], TestContext.Current.CancellationToken);

        var related = Cosine(vectors[0], vectors[1]);
        var unrelated = Cosine(vectors[0], vectors[2]);

        Assert.True(related > unrelated + 0.1f,
            $"expected related pair to be clearly closer: related={related:F3} unrelated={unrelated:F3}");
    }

    [Fact]
    public async Task EmbedQuery_AppliesTheBgeQueryPrefixAndSoDiffersFromTheDocumentVector()
    {
        const string text = "how do I register a plugin";

        var asQuery = await _embedder.EmbedQueryAsync(text, TestContext.Current.CancellationToken);
        var asDocument = await _embedder.EmbedDocumentsAsync([text], TestContext.Current.CancellationToken);

        Assert.True(Cosine(asQuery, asDocument[0]) < 0.999f,
            "query and document embeddings were identical - the BGE query prefix was not applied");
    }

    [Fact]
    public async Task EmbedDocuments_HandlesTextLongerThanTheModelWindow()
    {
        var long_ = string.Join(' ', Enumerable.Repeat("token", 4000));

        var vectors = await _embedder.EmbedDocumentsAsync([long_], TestContext.Current.CancellationToken);

        Assert.Equal(384, vectors[0].Length);
    }

    [Fact]
    public async Task EmbedDocuments_EmptyInput_ReturnsEmptyResult()
    {
        var vectors = await _embedder.EmbedDocumentsAsync([], TestContext.Current.CancellationToken);
        Assert.Empty(vectors);
    }

    private static float Cosine(float[] a, float[] b)
    {
        var dot = 0f;
        for (var i = 0; i < a.Length; i++)
        {
            dot += a[i] * b[i];
        }

        return dot; // both vectors are already unit length
    }
}
