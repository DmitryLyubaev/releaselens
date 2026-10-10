using System.Net.Http.Json;
using System.Text.Json.Serialization;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// Azure OpenAI embeddings over the account's v1 API. The <see cref="HttpClient"/>'s base address is
/// the account's v1 URL (<c>https://&lt;account&gt;.openai.azure.com/openai/v1/</c>) and its handler
/// is a <see cref="BearerTokenHandler"/> for <see cref="BearerTokenHandler.OpenAiScope"/>: no key.
/// </summary>
public sealed class EmbeddingsClient(HttpClient http, string deployment)
{
    /// <summary>The dimensions of <c>releaselens-embed-small</c>, and of the index's vector field.</summary>
    public const int Dimensions = 1536;

    /// <summary>
    /// One vector for each input, in the order of <paramref name="inputs"/> (the reply is ordered by
    /// its <c>index</c>, not by position). Throws if the reply does not hold exactly one
    /// 1,536-dimension vector per input.
    /// </summary>
    public async Task<IReadOnlyList<float[]>> EmbedAsync(
        IReadOnlyList<string> inputs, CancellationToken cancellationToken)
    {
        if (inputs.Count == 0)
        {
            return [];
        }

        using var response = await http.PostAsJsonAsync(
            "embeddings", new EmbeddingsRequest(deployment, inputs), cancellationToken);
        HttpFailure.ThrowIfNotSuccess(response, "Azure OpenAI embeddings");

        var reply = await response.Content.ReadFromJsonAsync<EmbeddingsReply>(cancellationToken)
            ?? throw new InvalidOperationException("Azure OpenAI embeddings replied with no body.");
        var data = reply.Data ?? throw new InvalidOperationException("Azure OpenAI embeddings reply has no data.");

        var ordered = data.OrderBy(item => item.Index).ToList();
        if (ordered.Count != inputs.Count || !ordered.Select(item => item.Index).SequenceEqual(Enumerable.Range(0, inputs.Count)))
        {
            throw new InvalidOperationException(
                $"Azure OpenAI embeddings replied with {ordered.Count} vectors for {inputs.Count} inputs.");
        }

        var vectors = new List<float[]>(ordered.Count);
        foreach (var item in ordered)
        {
            if (item.Embedding is not { Length: Dimensions } vector)
            {
                throw new InvalidOperationException(
                    $"Embedding {item.Index} has {item.Embedding?.Length ?? 0} dimensions; the index needs {Dimensions}.");
            }

            vectors.Add(vector);
        }

        return vectors;
    }

    private sealed record EmbeddingsRequest(
        [property: JsonPropertyName("model")] string Model,
        [property: JsonPropertyName("input")] IReadOnlyList<string> Input);

    private sealed record EmbeddingsReply(
        [property: JsonPropertyName("data")] List<EmbeddingItem>? Data);

    private sealed record EmbeddingItem(
        [property: JsonPropertyName("index")] int Index,
        [property: JsonPropertyName("embedding")] float[]? Embedding);
}
