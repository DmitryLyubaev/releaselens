using System.Globalization;
using Dapper;
using Pgvector;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Retrieval;

/// <summary>
/// Postgres full-text and pgvector, blended by weighted score. Deliberately not a
/// reranking model — see the non-goals in the spec. Both arms run as CTEs in one
/// round trip; RLS scopes them to the tenant on the connection.
/// </summary>
public sealed class HybridRetriever
{
    public async Task<RetrievalResult> RetrieveAsync(
        TenantScope scope, RetrievalRequest request, CancellationToken cancellationToken)
    {
        var hasText = !string.IsNullOrWhiteSpace(request.Query);

        // embeddings_hnsw_idx has no tenant awareness, so under RLS the HNSW walk finds
        // its candidates first and the tenant filter is applied as a post-filter. A tenant
        // whose vectors are sparse relative to the whole index can therefore get fewer than
        // @poolSize rows back from the vec CTE even though its own corpus has more matches
        // than that — an artefact of the index walk, not of the data. iterative_scan makes
        // the index keep walking until the post-filter has enough survivors (bounded by
        // hnsw.max_scan_tuples). relaxed_order is enough here: the vec CTE is a candidate
        // pool that gets re-ranked by the blend below, not the final ordering, so the small
        // amount of order slack it trades for recall costs nothing. SET LOCAL confines it to
        // this transaction.
        await scope.Connection.ExecuteAsync(new CommandDefinition(
            "set local hnsw.iterative_scan = 'relaxed_order'",
            transaction: scope.Transaction, cancellationToken: cancellationToken));

        var sql = $"""
            with vec as (
                select c.chunk_id,
                       1 - (e.embedding <=> @queryVector) as vector_score
                from embeddings e
                join evidence_chunks c on c.chunk_id = e.chunk_id
                where (@entityType is null or c.entity_type = @entityType)
                  {EntityDateFilter()}
                order by e.embedding <=> @queryVector
                limit @poolSize
            ),
            fts as (
                select c.chunk_id,
                       ts_rank_cd(c.content_tsv, websearch_to_tsquery('english', @queryText)) as text_score
                from evidence_chunks c
                where @hasText
                  and c.content_tsv @@ websearch_to_tsquery('english', @queryText)
                  and (@entityType is null or c.entity_type = @entityType)
                  {EntityDateFilter()}
                order by text_score desc
                limit @poolSize
            ),
            merged as (
                select coalesce(v.chunk_id, f.chunk_id) as chunk_id,
                       coalesce(v.vector_score, 0)      as vector_score,
                       coalesce(f.text_score, 0)        as text_score
                from vec v
                full outer join fts f on f.chunk_id = v.chunk_id
            ),
            -- Divided by the pool maximum against a fixed floor of zero, NOT true min-max:
            -- rescaling against the empirical minimum would zero out the weakest genuine text
            -- match whenever every candidate matches the query, penalising exactly the
            -- single-arm hits the full outer join is there to keep.
            normalised as (
                select chunk_id, vector_score, text_score,
                       case when max(text_score) over () > 0
                            then text_score / max(text_score) over ()
                            else 0 end as text_score_norm
                from merged
            )
            select c.chunk_id     as ChunkId,
                   c.entity_type  as EntityTypeWire,
                   c.entity_key   as EntityKey,
                   c.chunk_index  as ChunkIndex,
                   c.content      as Content,
                   n.vector_score as VectorScore,
                   n.text_score::double precision as TextScore,
                   (@alpha * n.vector_score) + ((1 - @alpha) * n.text_score_norm) as BlendedScore
            from normalised n
            join evidence_chunks c on c.chunk_id = n.chunk_id
            order by BlendedScore desc
            limit @k
            """;

        var rows = await scope.Connection.QueryAsync<Row>(new CommandDefinition(sql, new
        {
            queryVector = new Vector(request.QueryVector),
            queryText = hasText ? request.Query : string.Empty,
            hasText,
            entityType = request.EntityTypeFilter?.ToWireName(),
            since = request.Since,
            until = request.Until,
            pathFilter = request.PathFilter,
            poolSize = request.CandidatePoolSize,
            alpha = request.Alpha,
            k = request.K
        }, scope.Transaction, cancellationToken: cancellationToken));

        var chunks = rows.Select(r => new RetrievedChunk(
            r.ChunkId,
            EntityTypeExtensions.FromWireName(r.EntityTypeWire),
            r.EntityKey,
            r.ChunkIndex,
            r.Content,
            r.VectorScore,
            r.TextScore,
            r.BlendedScore)).ToList();

        var truncated = chunks.Count < request.K;

        return new RetrievalResult(
            chunks,
            request.K,
            request.CandidatePoolSize,
            truncated,
            truncated
                ? string.Create(CultureInfo.InvariantCulture,
                    $"Retrieval returned {chunks.Count} chunks, fewer than the requested k={request.K}. The corpus does not contain more matching evidence; results were not truncated by a limit.")
                : null);
    }

    /// <summary>
    /// Date and path filters resolve against the underlying entity rather than the chunk,
    /// so they are expressed as EXISTS predicates over the evidence tables.
    /// </summary>
    private static string EntityDateFilter() => """
        and (@since is null or exists (
            select 1 from commits x
            where x.sha = c.entity_key and c.entity_type = 'commit' and x.committed_at >= @since))
        and (@until is null or exists (
            select 1 from commits x
            where x.sha = c.entity_key and c.entity_type = 'commit' and x.committed_at <= @until))
        and (@pathFilter is null or exists (
            select 1 from files_changed f
            where f.sha = c.entity_key and c.entity_type = 'commit' and f.path ilike '%' || @pathFilter || '%'))
        """;

    private sealed record Row(
        long ChunkId, string EntityTypeWire, string EntityKey, int ChunkIndex,
        string Content, double VectorScore, double TextScore, double BlendedScore);
}
