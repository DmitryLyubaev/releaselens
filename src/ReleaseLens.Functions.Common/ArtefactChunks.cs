using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// How an artefact is chunked in the ingest app: <see cref="EvidenceChunker"/> with
/// <see cref="ChunkOptions.Default"/>. The ingest function and the Worker's <c>export-artefacts</c>
/// (which records each file's expected chunk count for the harness) both use this, so the count the
/// harness expects is the count the function writes.
/// </summary>
public static class ArtefactChunks
{
    private static readonly EvidenceChunker Chunker = new(ChunkOptions.Default);

    /// <summary>The chunks of <paramref name="record"/>, in order.</summary>
    public static IReadOnlyList<Chunk> Of(IEvidenceRecord record)
    {
        ArgumentNullException.ThrowIfNull(record);

        return record switch
        {
            CommitEvidence commit => Chunker.Chunk(commit),
            IssueEvidence issue => Chunker.Chunk(issue),
            PullRequestEvidence pullRequest => Chunker.Chunk(pullRequest),
            ReleaseEvidence release => Chunker.Chunk(release),
            _ => throw new ArgumentException($"{record.GetType().Name} is not an evidence record.", nameof(record)),
        };
    }

    /// <summary>How many chunks <paramref name="record"/> makes.</summary>
    public static int Count(IEvidenceRecord record) => Of(record).Count;

    /// <summary>The name of the file that records each exported artefact's expected chunk count.</summary>
    public const string ManifestFileName = "manifest.json";

    /// <summary>
    /// The manifest the export writes beside the artefact files: <c>{ "&lt;file name&gt;": &lt;expected chunk count&gt;, ... }</c>.
    /// Each count is taken from the file's own JSON, parsed back through <see cref="ArtefactJson.Parse"/>
    /// (the path the ingest app takes), so it is the count the function will write. Entries of
    /// <paramref name="existing"/> (a manifest already in the directory) are kept unless a file of the
    /// same name is given again. Names are sorted, so the same files give the same bytes.
    /// </summary>
    /// <exception cref="InvalidArtefactException">A file's JSON is not an artefact.</exception>
    public static string Manifest(string? existing, IEnumerable<(string FileName, string Json)> files)
    {
        ArgumentNullException.ThrowIfNull(files);

        var counts = new SortedDictionary<string, int>(StringComparer.Ordinal);
        if (!string.IsNullOrWhiteSpace(existing))
        {
            foreach (var (name, count) in JsonNode.Parse(existing)!.AsObject())
            {
                counts[name] = count!.GetValue<int>();
            }
        }

        foreach (var (fileName, json) in files)
        {
            counts[fileName] = Count(ArtefactJson.Parse(json));
        }

        return JsonSerializer.Serialize(counts, new JsonSerializerOptions { WriteIndented = true }) + "\n";
    }
}
