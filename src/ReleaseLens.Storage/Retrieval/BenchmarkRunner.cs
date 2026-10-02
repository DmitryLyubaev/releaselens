using System.Diagnostics;
using System.Text.Json;

namespace ReleaseLens.Storage.Retrieval;

/// <summary>
/// Runs the retrieval benchmark's in-app arms over a question file: S1 (<c>hybrid</c>, the
/// app's <see cref="HybridRetriever"/>) and E1 (<c>bge-exact</c>, <see cref="ExactVectorSearch"/>).
/// It writes one JSON line per question for the Python scorer. It does not score anything,
/// and it knows nothing of the database: the caller supplies the search.
/// </summary>
public static class BenchmarkRunner
{
    public const string HybridMode = "hybrid";
    public const string BgeExactMode = "bge-exact";

    /// <summary>Every arm of the benchmark returns its top 50 chunks (spec §4).</summary>
    public const int K = 50;

    /// <summary>The camelCase shape the Python side reads, for both the corpus export and the hits.</summary>
    public static JsonSerializerOptions Json { get; } = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    private static readonly Dictionary<string, string> ArmsByMode = new(StringComparer.Ordinal)
    {
        [HybridMode] = "S1",
        [BgeExactMode] = "E1"
    };

    /// <summary>
    /// Reads <c>{"qid", "question"}</c> lines, ignoring any other field, and writes
    /// <c>{"qid", "arm", "hits", "ms", "error"}</c> lines in input order. <c>ms</c> times the
    /// whole <paramref name="search"/> call, so it covers whatever the caller put inside it.
    /// An exception from one question becomes that line's <c>error</c>, with no hits, and the
    /// run moves on. Cancellation is not a question's failure: it stops the run.
    /// </summary>
    /// <returns>How many questions were run, and how many of them recorded an error.</returns>
    public static async Task<(int Questions, int Errors)> RunRetrieveAsync(
        string mode,
        TextReader questions,
        TextWriter output,
        Func<string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>> search,
        CancellationToken cancellationToken)
    {
        if (!ArmsByMode.TryGetValue(mode, out var arm))
        {
            throw new ArgumentException(
                $"Unknown retrieve mode '{mode}'. Use {HybridMode} or {BgeExactMode}.", nameof(mode));
        }

        var count = 0;
        var errors = 0;
        var lineNumber = 0;

        while (await questions.ReadLineAsync(cancellationToken) is { } line)
        {
            lineNumber++;
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            var question = JsonSerializer.Deserialize<QuestionLine>(line, Json);
            if (question?.Qid is null || question.Question is null)
            {
                // A malformed question file is not one bad question: the frozen file is
                // checksummed, so this means the wrong file, and the whole run is suspect.
                throw new InvalidDataException($"Question line {lineNumber} has no qid or question.");
            }

            IReadOnlyList<HitLine> hits;
            string? error = null;
            var stopwatch = Stopwatch.StartNew();
            try
            {
                var found = await search(question.Question, cancellationToken);
                hits = [.. found.Take(K).Select(h => new HitLine(h.ChunkId, h.Artefact, h.Score))];
            }
            catch (Exception ex) when (!cancellationToken.IsCancellationRequested)
            {
                hits = [];
                error = $"{ex.GetType().Name}: {ex.Message}";
                errors++;
            }

            stopwatch.Stop();

            await output.WriteLineAsync(JsonSerializer.Serialize(
                new OutputLine(question.Qid, arm, hits, stopwatch.Elapsed.TotalMilliseconds, error), Json));
            count++;
        }

        await output.FlushAsync(cancellationToken);
        return (count, errors);
    }

    /// <summary>
    /// Wraps a search so that every question opens, uses and disposes its own scope. A SQL
    /// error aborts a Postgres transaction, and <see cref="ExactVectorSearch"/>'s
    /// <c>set local</c> lasts for the rest of one, so a scope shared across questions would
    /// let one failure, or one arm's setting, reach the questions after it.
    /// </summary>
    public static Func<string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>>
        PerQuestionScope<TScope>(
            Func<CancellationToken, Task<TScope>> openScope,
            Func<TScope, string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>> search)
        where TScope : IAsyncDisposable
        => async (question, cancellationToken) =>
        {
            var scope = await openScope(cancellationToken);
            await using (scope)
            {
                return await search(scope, question, cancellationToken);
            }
        };

    /// <summary>S1's hits: the blended score is the one <see cref="HybridRetriever"/> ranks by.</summary>
    public static IReadOnlyList<(long ChunkId, string Artefact, double Score)> HybridHits(RetrievalResult result)
        => [.. result.Chunks.Select(c => (c.ChunkId, CorpusExporter.Artefact(c.Type, c.EntityKey), c.BlendedScore))];

    private sealed record QuestionLine(string? Qid, string? Question);

    private sealed record HitLine(long ChunkId, string Artefact, double Score);

    private sealed record OutputLine(string Qid, string Arm, IReadOnlyList<HitLine> Hits, double Ms, string? Error);
}
