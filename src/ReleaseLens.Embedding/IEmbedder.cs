namespace ReleaseLens.Embedding;

public interface IEmbedder : IDisposable
{
    int Dimensions { get; }

    /// <summary>Stored on every embedding row so a model change is detectable in data.</summary>
    string ModelName { get; }

    /// <summary>Embeds passages for storage. No query prefix is applied.</summary>
    Task<IReadOnlyList<float[]>> EmbedDocumentsAsync(IReadOnlyList<string> texts, CancellationToken cancellationToken);

    /// <summary>Embeds a search query. BGE is asymmetric — this applies the required query prefix.</summary>
    Task<float[]> EmbedQueryAsync(string query, CancellationToken cancellationToken);
}

public sealed class EmbedderOptions
{
    public const string SectionName = "Embedding";

    public string ModelDirectory { get; set; } = ModelLocator.Resolve();
    public int MaxSequenceLength { get; set; } = 512;
    public int BatchSize { get; set; } = 32;
}

public static class ModelLocator
{
    /// <summary>
    /// Walks up from the running assembly looking for models\bge-small-en-v1.5.
    /// Keeps tests and the worker working without an absolute path in configuration.
    /// </summary>
    public static string Resolve()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);

        while (directory is not null)
        {
            var candidate = Path.Combine(directory.FullName, "models", "bge-small-en-v1.5");
            if (Directory.Exists(candidate))
            {
                return candidate;
            }

            directory = directory.Parent;
        }

        return Path.Combine(AppContext.BaseDirectory, "models", "bge-small-en-v1.5");
    }
}
