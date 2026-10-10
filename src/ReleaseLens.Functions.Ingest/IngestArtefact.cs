using ReleaseLens.Core.Evidence;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Ingest;

/// <summary>What one ingest did: the artefact's ID, the chunks written, and the stale keys deleted.</summary>
public sealed record IngestResult(string Artefact, int Chunks, int Deleted);

/// <summary>
/// An evidence record becomes chunks in the search index: chunk, embed in one call, upsert under
/// deterministic keys, then delete every other key the artefact has in the index. Any failure
/// propagates, so the queue retries and then poisons the message. The order makes a retry safe at
/// every step: the upsert is idempotent, and the delete comes last, so a failure before it leaves the
/// old chunks, never nothing.
/// </summary>
public sealed class IngestArtefact(EmbeddingsClient embeddings, SearchIndexClient index)
{
    public async Task<IngestResult> IngestAsync(IEvidenceRecord record, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(record);

        var artefact = record.Key.ToString();
        var chunks = ArtefactChunks.Of(record);

        var vectors = await embeddings.EmbedAsync([.. chunks.Select(chunk => chunk.Content)], ct);

        var documents = chunks
            .Select((chunk, position) => new IndexChunk(
                ChunkKeys.For(artefact, chunk.Index), artefact, chunk.Content, vectors[position]))
            .ToList();
        await index.UpsertAsync(documents, ct);

        var fresh = documents.Select(document => document.ChunkId).ToHashSet(StringComparer.Ordinal);
        var stale = ChunkKeys.Stale(await index.KeysForArtefactAsync(artefact, ct), fresh);
        await index.DeleteAsync(stale, ct);

        return new IngestResult(artefact, documents.Count, stale.Count);
    }
}
