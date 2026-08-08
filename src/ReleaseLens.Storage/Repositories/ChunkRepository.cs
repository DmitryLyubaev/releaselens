using Dapper;
using Pgvector;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Repositories;

public sealed record PendingChunk(long ChunkId, string Content);

public sealed record DeadLetteredChunk(long DeadLetterId, long ChunkId, int Attempts, string LastError, string Content);

public sealed class ChunkRepository
{
    /// <summary>
    /// Upserts chunks and returns their ids in the same order as the input, so the
    /// caller can zip them against the vectors it is about to compute.
    /// </summary>
    public async Task<IReadOnlyList<long>> UpsertChunksAsync(
        TenantScope scope, IReadOnlyList<Chunk> chunks, CancellationToken cancellationToken)
    {
        if (chunks.Count == 0)
        {
            return [];
        }

        var ids = new long[chunks.Count];

        for (var i = 0; i < chunks.Count; i++)
        {
            var chunk = chunks[i];
            ids[i] = await scope.Connection.ExecuteScalarAsync<long>(new CommandDefinition(
                """
                insert into evidence_chunks (tenant_id, entity_type, entity_key, chunk_index, content, token_count)
                values (@TenantId, @EntityType, @EntityKey, @ChunkIndex, @Content, @TokenCount)
                on conflict (tenant_id, entity_type, entity_key, chunk_index) do update set
                    content = excluded.content,
                    token_count = excluded.token_count
                returning chunk_id
                """,
                new
                {
                    chunk.TenantId,
                    EntityType = chunk.Type.ToWireName(),
                    EntityKey = chunk.EntityKey,
                    ChunkIndex = chunk.Index,
                    chunk.Content,
                    TokenCount = chunk.ApproxTokens
                },
                scope.Transaction, cancellationToken: cancellationToken));
        }

        return ids;
    }

    public async Task UpsertEmbeddingsAsync(
        TenantScope scope,
        IReadOnlyList<(long ChunkId, float[] Vector)> embeddings,
        string modelName,
        CancellationToken cancellationToken)
    {
        if (embeddings.Count == 0)
        {
            return;
        }

        await scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into embeddings (chunk_id, tenant_id, model, dim, embedding)
            values (@ChunkId, @TenantId, @Model, @Dim, @Embedding)
            on conflict (chunk_id) do update set
                model = excluded.model,
                dim = excluded.dim,
                embedding = excluded.embedding,
                created_at = now()
            """,
            embeddings.Select(e => new
            {
                e.ChunkId,
                scope.TenantId,
                Model = modelName,
                Dim = e.Vector.Length,
                Embedding = new Vector(e.Vector)
            }),
            scope.Transaction, cancellationToken: cancellationToken));

        // A chunk that embeds successfully is no longer dead-lettered.
        await scope.Connection.ExecuteAsync(new CommandDefinition(
            "delete from embedding_dead_letter where chunk_id = any(@ids)",
            new { ids = embeddings.Select(e => e.ChunkId).ToArray() },
            scope.Transaction, cancellationToken: cancellationToken));
    }

    public Task<int> CountEmbeddedAsync(TenantScope scope, CancellationToken cancellationToken)
        => scope.Connection.ExecuteScalarAsync<int>(new CommandDefinition(
            "select count(*) from embeddings", transaction: scope.Transaction, cancellationToken: cancellationToken));

    public async Task<IReadOnlyList<PendingChunk>> GetUnembeddedAsync(
        TenantScope scope, int limit, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<PendingChunk>(new CommandDefinition(
            """
            select c.chunk_id as ChunkId, c.content as Content
            from evidence_chunks c
            left join embeddings e on e.chunk_id = c.chunk_id
            where e.chunk_id is null
            order by c.chunk_id
            limit @limit
            """,
            new { limit }, scope.Transaction, cancellationToken: cancellationToken))];

    /// <summary>
    /// Records an embedding failure with exponential backoff: 1, 2, 4, 8, 16 minutes.
    /// After five attempts the row stops appearing in <see cref="GetDueDeadLettersAsync"/>
    /// and stays as a permanent record rather than being retried forever.
    /// </summary>
    public Task DeadLetterAsync(TenantScope scope, long chunkId, string error, CancellationToken cancellationToken)
        => scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into embedding_dead_letter (tenant_id, chunk_id, attempts, last_error, next_attempt_at)
            values (@TenantId, @ChunkId, 1, @Error, now())
            on conflict (chunk_id) do update set
                attempts = embedding_dead_letter.attempts + 1,
                last_error = excluded.last_error,
                -- embedding_dead_letter.attempts is the value BEFORE the increment above, so the
                -- first retry waits 1 minute, then 2, 4, 8. The initial insert is due immediately:
                -- the first attempt already failed, and a fixed delay before the first retry buys
                -- nothing while making the due-window query untestable without a clock shift.
                next_attempt_at = now() + (interval '1 minute' * power(2, embedding_dead_letter.attempts - 1))
            """,
            new { scope.TenantId, ChunkId = chunkId, Error = error },
            scope.Transaction, cancellationToken: cancellationToken));

    public async Task<IReadOnlyList<DeadLetteredChunk>> GetDueDeadLettersAsync(
        TenantScope scope, int limit, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<DeadLetteredChunk>(new CommandDefinition(
            """
            select d.dead_letter_id as DeadLetterId, d.chunk_id as ChunkId, d.attempts as Attempts,
                   d.last_error as LastError, c.content as Content
            from embedding_dead_letter d
            join evidence_chunks c on c.chunk_id = d.chunk_id
            where d.attempts < 5 and d.next_attempt_at <= now()
            order by d.next_attempt_at
            limit @limit
            """,
            new { limit }, scope.Transaction, cancellationToken: cancellationToken))];

    public async Task<IReadOnlyList<DeadLetteredChunk>> GetAllDeadLettersAsync(
        TenantScope scope, CancellationToken cancellationToken)
        => [.. await scope.Connection.QueryAsync<DeadLetteredChunk>(new CommandDefinition(
            """
            select d.dead_letter_id as DeadLetterId, d.chunk_id as ChunkId, d.attempts as Attempts,
                   d.last_error as LastError, c.content as Content
            from embedding_dead_letter d
            join evidence_chunks c on c.chunk_id = d.chunk_id
            order by d.dead_letter_id
            """,
            transaction: scope.Transaction, cancellationToken: cancellationToken))];
}
