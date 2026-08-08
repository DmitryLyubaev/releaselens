using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using ReleaseLens.Embedding;

namespace ReleaseLens.Ingestion.Tests;

/// <summary>
/// Deterministic stand-in for the ONNX embedder. <see cref="FailOnContentContaining"/>
/// makes the dead-letter path testable without breaking the real model.
/// </summary>
public sealed class FakeEmbedder : IEmbedder
{
    public int Dimensions => 384;
    public string ModelName => "fake-embedder";
    public string? FailOnContentContaining { get; set; }
    public int CallCount { get; private set; }

    public Task<IReadOnlyList<float[]>> EmbedDocumentsAsync(IReadOnlyList<string> texts, CancellationToken cancellationToken)
    {
        CallCount++;

        if (FailOnContentContaining is { } marker && texts.Any(t => t.Contains(marker, StringComparison.Ordinal)))
        {
            throw new InvalidOperationException($"fake embedder was told to fail on '{marker}'");
        }

        return Task.FromResult<IReadOnlyList<float[]>>([.. texts.Select(Deterministic)]);
    }

    public Task<float[]> EmbedQueryAsync(string query, CancellationToken cancellationToken)
        => Task.FromResult(Deterministic(query));

    private static float[] Deterministic(string text)
    {
        var vector = new float[384];
        var hash = text.GetHashCode(StringComparison.Ordinal);
        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] = MathF.Sin((hash % 1000) + i);
        }

        var magnitude = MathF.Sqrt(vector.Sum(x => x * x));
        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] /= magnitude;
        }

        return vector;
    }

    public void Dispose() { }
}
