using Microsoft.ML.OnnxRuntime;
using Microsoft.ML.OnnxRuntime.Tensors;
using Microsoft.ML.Tokenizers;

namespace ReleaseLens.Embedding;

/// <summary>
/// BGE-small-en-v1.5 running in-process on ONNX Runtime. 384 dimensions, CLS pooling,
/// L2-normalised output. Nothing leaves the machine to produce an embedding, which is
/// what makes a zero-egress deployment possible (spec section 8.2).
/// </summary>
public sealed class OnnxEmbedder : IEmbedder
{
    private readonly InferenceSession _session;
    private readonly BertTokenizer _tokenizer;
    private readonly EmbedderOptions _options;
    private readonly SemaphoreSlim _gate = new(1, 1);

    private const string QueryPrefix = "Represent this sentence for searching relevant passages: ";

    public int Dimensions => 384;
    public string ModelName => "bge-small-en-v1.5";

    public OnnxEmbedder(EmbedderOptions options)
    {
        _options = options;

        var modelPath = Path.Combine(options.ModelDirectory, "model.onnx");
        var vocabPath = Path.Combine(options.ModelDirectory, "vocab.txt");

        if (!File.Exists(modelPath) || !File.Exists(vocabPath))
        {
            throw new FileNotFoundException(
                $"Embedding model not found in '{options.ModelDirectory}'. Run scripts/fetch-model.ps1 (or .sh).");
        }

        var sessionOptions = new SessionOptions
        {
            GraphOptimizationLevel = GraphOptimizationLevel.ORT_ENABLE_ALL,
            IntraOpNumThreads = Environment.ProcessorCount
        };

        _session = new InferenceSession(modelPath, sessionOptions);

        using var vocabStream = File.OpenRead(vocabPath);
        _tokenizer = BertTokenizer.Create(vocabStream);
    }

    public async Task<float[]> EmbedQueryAsync(string query, CancellationToken cancellationToken)
    {
        var vectors = await EmbedAsync([QueryPrefix + query], cancellationToken);
        return vectors[0];
    }

    public Task<IReadOnlyList<float[]>> EmbedDocumentsAsync(
        IReadOnlyList<string> texts, CancellationToken cancellationToken)
        => EmbedAsync(texts, cancellationToken);

    private async Task<IReadOnlyList<float[]>> EmbedAsync(
        IReadOnlyList<string> texts, CancellationToken cancellationToken)
    {
        if (texts.Count == 0)
        {
            return [];
        }

        // The session is not guaranteed thread-safe for concurrent Run calls with
        // shared state; one gate is far cheaper than debugging intermittent corruption.
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var results = new List<float[]>(texts.Count);

            for (var offset = 0; offset < texts.Count; offset += _options.BatchSize)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var batch = texts.Skip(offset).Take(_options.BatchSize).ToList();
                results.AddRange(RunBatch(batch));
            }

            return results;
        }
        finally
        {
            _gate.Release();
        }
    }

    private List<float[]> RunBatch(List<string> batch)
    {
        var encoded = batch.Select(Encode).ToList();
        var maxLength = encoded.Max(e => e.Length);
        var batchSize = batch.Count;

        var inputIds = new DenseTensor<long>([batchSize, maxLength]);
        var attentionMask = new DenseTensor<long>([batchSize, maxLength]);
        var tokenTypeIds = new DenseTensor<long>([batchSize, maxLength]);

        for (var row = 0; row < batchSize; row++)
        {
            var ids = encoded[row];
            for (var column = 0; column < maxLength; column++)
            {
                var withinSequence = column < ids.Length;
                inputIds[row, column] = withinSequence ? ids[column] : 0;
                attentionMask[row, column] = withinSequence ? 1 : 0;
                tokenTypeIds[row, column] = 0;
            }
        }

        var inputs = new List<NamedOnnxValue>
        {
            NamedOnnxValue.CreateFromTensor("input_ids", inputIds),
            NamedOnnxValue.CreateFromTensor("attention_mask", attentionMask)
        };

        if (_session.InputMetadata.ContainsKey("token_type_ids"))
        {
            inputs.Add(NamedOnnxValue.CreateFromTensor("token_type_ids", tokenTypeIds));
        }

        using var outputs = _session.Run(inputs);
        var hiddenStates = outputs.First().AsTensor<float>();

        var vectors = new List<float[]>(batchSize);
        for (var row = 0; row < batchSize; row++)
        {
            // CLS pooling: BGE uses the first token's hidden state, not a mean over tokens.
            var vector = new float[Dimensions];
            for (var d = 0; d < Dimensions; d++)
            {
                vector[d] = hiddenStates[row, 0, d];
            }

            Normalise(vector);
            vectors.Add(vector);
        }

        return vectors;
    }

    private long[] Encode(string text)
    {
        var ids = _tokenizer.EncodeToIds(text);

        // Truncate to the model window, keeping [CLS] at position 0 and [SEP] at the end.
        if (ids.Count > _options.MaxSequenceLength)
        {
            var truncated = ids.Take(_options.MaxSequenceLength - 1).ToList();
            truncated.Add(ids[^1]);
            ids = truncated;
        }

        return [.. ids.Select(id => (long)id)];
    }

    private static void Normalise(float[] vector)
    {
        var sumOfSquares = 0f;
        foreach (var component in vector)
        {
            sumOfSquares += component * component;
        }

        var magnitude = MathF.Sqrt(sumOfSquares);
        if (magnitude < 1e-9f)
        {
            return;
        }

        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] /= magnitude;
        }
    }

    public void Dispose()
    {
        _session.Dispose();
        _gate.Dispose();
    }
}
