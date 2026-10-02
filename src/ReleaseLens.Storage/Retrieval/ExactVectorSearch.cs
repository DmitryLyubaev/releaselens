using Dapper;
using Pgvector;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Retrieval;

/// <summary>
/// Exact cosine search over the stored BGE vectors: the benchmark's E1 arm. The HNSW index
/// is bypassed so the arm measures the embedding model, not the approximate index walk that
/// <see cref="HybridRetriever"/> relies on. RLS scopes it to the tenant on the connection.
/// </summary>
public sealed class ExactVectorSearch
{
    /// <summary>The stored BGE-small vectors' length, which a query vector must match.</summary>
    public const int Dimensions = 384;

    /// <summary>
    /// A cost penalty rather than a ban, but a large fixed cost penalty against a sequential
    /// scan of the corpus, so it is decisive. SET LOCAL confines it to this transaction, and it
    /// stays in force for the rest of that transaction: a <see cref="HybridRetriever"/> call
    /// made later in the same scope would lose its index too.
    /// </summary>
    internal const string DisableIndexScanSql = "set local enable_indexscan = off";

    // chunk_id breaks ties so that equal scores come back in the same order on every run.
    internal const string SearchSql = """
        select c.chunk_id    as ChunkId,
               c.entity_type as EntityTypeWire,
               c.entity_key  as EntityKey,
               1 - (e.embedding <=> @q) as Score
        from embeddings e
        join evidence_chunks c on c.chunk_id = e.chunk_id
        order by e.embedding <=> @q, c.chunk_id
        limit @k
        """;

    /// <summary>
    /// The <paramref name="k"/> chunks nearest <paramref name="queryVector"/> by cosine, best
    /// first. <paramref name="k"/> must be positive, and the vector must have
    /// <see cref="Dimensions"/> values; both are checked before the database is touched.
    /// </summary>
    public async Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>> SearchAsync(
        TenantScope scope, float[] queryVector, int k, CancellationToken cancellationToken)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(k);
        ArgumentNullException.ThrowIfNull(queryVector);
        if (queryVector.Length != Dimensions)
        {
            throw new ArgumentOutOfRangeException(nameof(queryVector), queryVector.Length,
                $"The query vector has {queryVector.Length} dimensions; the stored vectors have {Dimensions}.");
        }

        await scope.Connection.ExecuteAsync(new CommandDefinition(
            DisableIndexScanSql, transaction: scope.Transaction, cancellationToken: cancellationToken));

        var rows = await scope.Connection.QueryAsync<Row>(new CommandDefinition(
            SearchSql, new { q = new Vector(queryVector), k },
            scope.Transaction, cancellationToken: cancellationToken));

        return [.. rows.Select(r => (
            r.ChunkId,
            CorpusExporter.Artefact(EntityTypeExtensions.FromWireName(r.EntityTypeWire), r.EntityKey),
            r.Score))];
    }

    private sealed record Row(long ChunkId, string EntityTypeWire, string EntityKey, double Score);
}
