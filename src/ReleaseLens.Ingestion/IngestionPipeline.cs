using System.Diagnostics;
using Microsoft.Extensions.Logging;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Embedding;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;

namespace ReleaseLens.Ingestion;

/// <summary>
/// Normalise, chunk, embed, store — per batch, committing after each one so an
/// interrupted run resumes from the last completed batch rather than starting over.
/// </summary>
public sealed class IngestionPipeline(
    IEvidenceSource source,
    IEmbedder embedder,
    EvidenceChunker chunker,
    TenantConnectionFactory factory,
    EvidenceRepository evidence,
    ChunkRepository chunks,
    CheckpointRepository checkpoints,
    ILogger<IngestionPipeline> logger)
{
    public static readonly ActivitySource ActivitySource = new("ReleaseLens.Ingestion");

    private const string BatchEmbedSavepoint = "embed_batch_attempt";
    private const string SingleEmbedSavepoint = "embed_single_attempt";

    public async Task<IngestionReport> RunAsync(Guid tenantId, CancellationToken cancellationToken)
    {
        var started = Stopwatch.GetTimestamp();
        var totals = new Totals();

        using var activity = ActivitySource.StartActivity("ingest.run");
        activity?.SetTag("tenant_id", tenantId);
        activity?.SetTag("source", source.SourceName);

        // Cheap evidence first: an interrupted run has already banked releases and PRs
        // before it reaches the expensive per-commit stage.
        await IngestAsync(tenantId, EntityType.Release, totals,
            cursor => source.ReadReleasesAsync(cursor, cancellationToken),
            (scope, items, ct) => evidence.UpsertReleasesAsync(scope, items, ct),
            item => chunker.Chunk(item),
            n => totals.Releases += n,
            cancellationToken);

        await IngestAsync(tenantId, EntityType.PullRequest, totals,
            cursor => source.ReadPullRequestsAsync(cursor, cancellationToken),
            (scope, items, ct) => evidence.UpsertPullRequestsAsync(scope, items, ct),
            item => chunker.Chunk(item),
            n => totals.PullRequests += n,
            cancellationToken);

        await IngestAsync(tenantId, EntityType.Issue, totals,
            cursor => source.ReadIssuesAsync(cursor, cancellationToken),
            (scope, items, ct) => evidence.UpsertIssuesAsync(scope, items, ct),
            item => chunker.Chunk(item),
            n => totals.Issues += n,
            cancellationToken);

        await IngestAsync(tenantId, EntityType.Commit, totals,
            cursor => source.ReadCommitsAsync(cursor, cancellationToken),
            (scope, items, ct) => evidence.UpsertCommitsAsync(scope, items, ct),
            item => chunker.Chunk(item),
            n => totals.Commits += n,
            cancellationToken);

        var report = new IngestionReport
        {
            CommitsIngested = totals.Commits,
            IssuesIngested = totals.Issues,
            PullRequestsIngested = totals.PullRequests,
            ReleasesIngested = totals.Releases,
            ChunksWritten = totals.ChunksWritten,
            ChunksEmbedded = totals.ChunksEmbedded,
            DeadLettered = totals.DeadLettered,
            Elapsed = Stopwatch.GetElapsedTime(started)
        };

        logger.LogInformation("Ingestion complete: {Report}", report);
        return report;
    }

    private async Task IngestAsync<T>(
        Guid tenantId,
        EntityType entityType,
        Totals totals,
        Func<EvidenceCursor, IAsyncEnumerable<EvidenceBatch<T>>> read,
        Func<TenantScope, IReadOnlyList<T>, CancellationToken, Task<int>> persist,
        Func<T, IReadOnlyList<Chunk>> chunk,
        Action<int> countEntities,
        CancellationToken cancellationToken)
        where T : IEvidenceRecord
    {
        EvidenceCursor cursor;
        await using (var scope = await factory.OpenAsync(tenantId, cancellationToken))
        {
            cursor = await checkpoints.GetAsync(scope, entityType, cancellationToken);
        }

        await foreach (var batch in read(cursor).WithCancellation(cancellationToken))
        {
            if (batch.NotModified)
            {
                logger.LogInformation("{EntityType}: source reported no change since the last run", entityType);
                return;
            }

            if (batch.Items.Count == 0)
            {
                continue;
            }

            using var activity = ActivitySource.StartActivity("ingest.batch");
            activity?.SetTag("entity_type", entityType.ToWireName());
            activity?.SetTag("batch_size", batch.Items.Count);

            var batchChunks = batch.Items.SelectMany(chunk).ToList();

            await using var scope = await factory.OpenAsync(tenantId, cancellationToken);

            var written = await persist(scope, batch.Items, cancellationToken);
            countEntities(written);

            var chunkIds = await chunks.UpsertChunksAsync(scope, batchChunks, cancellationToken);
            totals.ChunksWritten += chunkIds.Count;

            await EmbedAsync(scope, chunkIds, batchChunks, totals, cancellationToken);

            await checkpoints.SaveAsync(
                scope, entityType, batch.NextCursor ?? cursor.Value, batch.ETag ?? cursor.ETag,
                batch.Items.Count, cancellationToken);

            await scope.CommitAsync(cancellationToken);
        }
    }

    /// <summary>
    /// Embeds a batch, falling back to one-at-a-time on failure so a single poison chunk
    /// dead-letters itself rather than taking the whole batch down with it.
    /// </summary>
    /// <remarks>
    /// Both the embedder call and the <see cref="ChunkRepository.UpsertEmbeddingsAsync"/>
    /// persistence call are wrapped in one try, deliberately: the failure that sends us into
    /// the retry path might come from Postgres (a constraint violation on the insert) rather
    /// than the embedder. A Postgres error leaves the surrounding transaction aborted — every
    /// later statement on it, including the fallback embed calls and
    /// <see cref="ChunkRepository.DeadLetterAsync"/>, would otherwise fail with "current
    /// transaction is aborted", masking the real error behind a recovery path that cannot run.
    /// A savepoint taken immediately before each attempt and rolled back to on failure clears
    /// that aborted state without discarding anything committed earlier in the batch's
    /// transaction, so the retry-then-dead-letter path stays usable regardless of whether the
    /// embedder or Postgres is what actually failed.
    /// </remarks>
    private async Task EmbedAsync(
        TenantScope scope,
        IReadOnlyList<long> chunkIds,
        IReadOnlyList<Chunk> batchChunks,
        Totals totals,
        CancellationToken cancellationToken)
    {
        using var activity = ActivitySource.StartActivity("embed");
        activity?.SetTag("chunk_count", chunkIds.Count);

        await scope.Transaction.SaveAsync(BatchEmbedSavepoint, cancellationToken);

        try
        {
            var vectors = await embedder.EmbedDocumentsAsync(
                [.. batchChunks.Select(c => c.Content)], cancellationToken);

            await chunks.UpsertEmbeddingsAsync(
                scope, [.. chunkIds.Zip(vectors)], embedder.ModelName, cancellationToken);

            totals.ChunksEmbedded += chunkIds.Count;
        }
        catch (Exception batchFailure) when (batchFailure is not OperationCanceledException)
        {
            logger.LogWarning(batchFailure, "Batch embedding failed; retrying individually");

            // Whether the embedder or Postgres threw, the transaction may now be aborted.
            // Rolling back to the savepoint taken before the attempt clears that state without
            // undoing anything from earlier batches in this run.
            await scope.Transaction.RollbackAsync(BatchEmbedSavepoint, cancellationToken);

            for (var i = 0; i < chunkIds.Count; i++)
            {
                await scope.Transaction.SaveAsync(SingleEmbedSavepoint, cancellationToken);

                try
                {
                    var vector = await embedder.EmbedDocumentsAsync([batchChunks[i].Content], cancellationToken);
                    await chunks.UpsertEmbeddingsAsync(
                        scope, [(chunkIds[i], vector[0])], embedder.ModelName, cancellationToken);
                    totals.ChunksEmbedded++;
                }
                catch (Exception single) when (single is not OperationCanceledException)
                {
                    logger.LogError(single, "Dead-lettering chunk {ChunkId}", chunkIds[i]);
                    await scope.Transaction.RollbackAsync(SingleEmbedSavepoint, cancellationToken);
                    await chunks.DeadLetterAsync(scope, chunkIds[i], single.Message, cancellationToken);
                    totals.DeadLettered++;
                }
            }
        }
    }

    private sealed class Totals
    {
        public int Commits;
        public int Issues;
        public int PullRequests;
        public int Releases;
        public int ChunksWritten;
        public int ChunksEmbedded;
        public int DeadLettered;
    }
}
