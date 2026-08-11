using System;
using System.Linq;
using System.Threading.Tasks;
using Dapper;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class ChunkRepositoryTests(PostgresFixture fixture)
{
    private readonly ChunkRepository _chunks = new();
    private readonly CheckpointRepository _checkpoints = new();

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> ArrangeTenantAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private static Chunk Chunk(Guid tenant, int index, string content) =>
        new(tenant, EntityType.Commit, "sha7777", index, content, content.Length / 4);

    private static float[] Vector(float seed)
    {
        var v = new float[384];
        for (var i = 0; i < v.Length; i++)
        {
            v[i] = seed + (i * 0.0001f);
        }

        var magnitude = MathF.Sqrt(v.Sum(x => x * x));
        for (var i = 0; i < v.Length; i++)
        {
            v[i] /= magnitude;
        }

        return v;
    }

    [Fact]
    public async Task UpsertChunks_ReturnsChunkIdsInInputOrder()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("chunk-ids");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var chunks = new[]
        {
            Chunk(tenantId, 0, "first"), Chunk(tenantId, 1, "second"), Chunk(tenantId, 2, "third")
        };

        var ids = await _chunks.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);

        Assert.Equal(3, ids.Count);
        Assert.Equal(ids.Distinct().Count(), ids.Count);

        // The count and distinctness assertions above would pass against a scrambled result.
        // Task 9 zips these ids against vectors computed from the same input list, so a
        // reordering silently pairs every embedding with the wrong chunk — degraded retrieval
        // with nothing failing anywhere. Resolve each id back to its content to pin the order.
        for (var i = 0; i < ids.Count; i++)
        {
            var content = await scope.Connection.ExecuteScalarAsync<string>(new CommandDefinition(
                "select content from evidence_chunks where chunk_id = @id",
                new { id = ids[i] }, scope.Transaction, cancellationToken: TestContext.Current.CancellationToken));

            Assert.Equal(chunks[i].Content, content);
        }
    }

    [Fact]
    public async Task UpsertChunks_ReRunReturnsTheSameIds()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("chunk-rerun");

        long[] first;
        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            first = [.. await _chunks.UpsertChunksAsync(scope, [Chunk(tenantId, 0, "v1")], TestContext.Current.CancellationToken)];
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        long[] second;
        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            second = [.. await _chunks.UpsertChunksAsync(scope, [Chunk(tenantId, 0, "v2")], TestContext.Current.CancellationToken)];
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        Assert.Equal(first, second);
    }

    [Fact]
    public async Task UpsertEmbeddings_StoresVectorAndModelName()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("embed-store");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await _chunks.UpsertChunksAsync(scope, [Chunk(tenantId, 0, "content")], TestContext.Current.CancellationToken);
        await _chunks.UpsertEmbeddingsAsync(scope, [(ids[0], Vector(0.5f))], "bge-small-en-v1.5", TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var count = await _chunks.CountEmbeddedAsync(read, TestContext.Current.CancellationToken);

        Assert.Equal(1, count);
    }

    [Fact]
    public async Task DeadLetter_RecordsTheErrorAndSchedulesARetry()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("dead-letter");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await _chunks.UpsertChunksAsync(scope, [Chunk(tenantId, 0, "content")], TestContext.Current.CancellationToken);
        await _chunks.DeadLetterAsync(scope, ids[0], "onnx session threw", TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var due = await _chunks.GetDueDeadLettersAsync(read, 10, TestContext.Current.CancellationToken);

        Assert.Single(due);
        Assert.Equal(1, due[0].Attempts);
        Assert.Contains("onnx", due[0].LastError, StringComparison.Ordinal);
    }

    [Fact]
    public async Task DeadLetter_SecondFailureIncrementsAttemptsAndBacksOff()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("dead-letter-backoff");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await _chunks.UpsertChunksAsync(scope, [Chunk(tenantId, 0, "content")], TestContext.Current.CancellationToken);
        await _chunks.DeadLetterAsync(scope, ids[0], "first failure", TestContext.Current.CancellationToken);
        await _chunks.DeadLetterAsync(scope, ids[0], "second failure", TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var all = await _chunks.GetAllDeadLettersAsync(read, TestContext.Current.CancellationToken);

        Assert.Single(all);
        Assert.Equal(2, all[0].Attempts);
        Assert.Equal("second failure", all[0].LastError);
    }

    [Fact]
    public async Task GetUnembeddedChunkIds_ReturnsOnlyChunksWithoutAVector()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("unembedded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var ids = await _chunks.UpsertChunksAsync(scope,
            [Chunk(tenantId, 0, "a"), Chunk(tenantId, 1, "b")], TestContext.Current.CancellationToken);
        await _chunks.UpsertEmbeddingsAsync(scope, [(ids[0], Vector(0.3f))], "bge-small-en-v1.5", TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var pending = await _chunks.GetUnembeddedAsync(read, 100, TestContext.Current.CancellationToken);

        Assert.Single(pending);
        Assert.Equal(ids[1], pending[0].ChunkId);
    }

    [Fact]
    public async Task Checkpoint_RoundTripsCursorAndEtag()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("checkpoint");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _checkpoints.SaveAsync(scope, EntityType.Commit, "2026-03-01T00:00:00Z", "W/\"abc\"", 1200,
                TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var cursor = await _checkpoints.GetAsync(read, EntityType.Commit, TestContext.Current.CancellationToken);

        Assert.Equal("2026-03-01T00:00:00Z", cursor.Value);
        Assert.Equal("W/\"abc\"", cursor.ETag);
    }

    [Fact]
    public async Task Checkpoint_MissingRow_ReturnsAStartCursor()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("checkpoint-missing");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var cursor = await _checkpoints.GetAsync(scope, EntityType.Release, TestContext.Current.CancellationToken);

        Assert.Null(cursor.Value);
        Assert.Null(cursor.ETag);
        Assert.Equal(tenantId, cursor.TenantId);
    }

    [Fact]
    public async Task Checkpoint_Reset_ClearsBothCursorAndEtag()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("checkpoint-reset");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _checkpoints.SaveAsync(scope, EntityType.Commit, "2026-08-01T00:00:00Z", "W/\"commit-etag\"", 60,
                TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        IReadOnlyList<CheckpointReset> reset;
        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            reset = await _checkpoints.ResetAsync(scope, [EntityType.Commit], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        // The discarded values are reported, not just cleared: the command logs them so a
        // backfill is auditable after the fact.
        var entry = Assert.Single(reset);
        Assert.Equal(EntityType.Commit, entry.EntityType);
        Assert.Equal("2026-08-01T00:00:00Z", entry.DiscardedCursor);
        Assert.Equal("W/\"commit-etag\"", entry.DiscardedETag);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var cursor = await _checkpoints.GetAsync(read, EntityType.Commit, TestContext.Current.CancellationToken);

        Assert.Null(cursor.Value);

        // Asserted separately from the cursor because clearing the cursor alone is the
        // plausible half-fix: GitHub answers a conditional request carrying a live ETag with
        // 304, the pipeline treats that as "no change" and skips the source, and the backfill
        // the reset was meant to enable ingests nothing while reporting success.
        Assert.Null(cursor.ETag);
    }

    [Fact]
    public async Task Checkpoint_Reset_LeavesOtherEntityTypesAlone()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("checkpoint-reset-scoped");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _checkpoints.SaveAsync(scope, EntityType.Issue, "2026-07-01T00:00:00Z", "W/\"issue-etag\"", 163,
                TestContext.Current.CancellationToken);
            await _checkpoints.SaveAsync(scope, EntityType.Release, "2026-07-02T00:00:00Z", "W/\"release-etag\"", 276,
                TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            var reset = await _checkpoints.ResetAsync(scope, [EntityType.Issue], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);

            Assert.Equal([EntityType.Issue], reset.Select(r => r.EntityType));
        }

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var issue = await _checkpoints.GetAsync(read, EntityType.Issue, TestContext.Current.CancellationToken);
        var release = await _checkpoints.GetAsync(read, EntityType.Release, TestContext.Current.CancellationToken);

        Assert.Null(issue.Value);
        Assert.Null(issue.ETag);

        // Releases took nearly three hours to ingest in the real corpus. Widening the commit
        // window must not cost that.
        Assert.Equal("2026-07-02T00:00:00Z", release.Value);
        Assert.Equal("W/\"release-etag\"", release.ETag);
    }

    [Fact]
    public async Task Checkpoint_Reset_LeavesEvidenceInPlace()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("checkpoint-reset-keeps-evidence");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await new EvidenceRepository().UpsertCommitsAsync(scope,
                [new CommitEvidence(tenantId, "sha8888", "feat: planner", "Alice", "alice@example.com",
                    DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
                    "https://github.com/microsoft/semantic-kernel/commit/sha8888",
                    [new FileChange("dotnet/src/Planner.cs", "modified", 5, 2)])],
                TestContext.Current.CancellationToken);

            var ids = await _chunks.UpsertChunksAsync(scope,
                [Chunk(tenantId, 0, "feat: planner")], TestContext.Current.CancellationToken);
            await _chunks.UpsertEmbeddingsAsync(scope, [(ids[0], Vector(0.7f))], "bge-small-en-v1.5",
                TestContext.Current.CancellationToken);

            await _checkpoints.SaveAsync(scope, EntityType.Commit, "2026-08-01T00:00:00Z", "W/\"commit-etag\"", 1,
                TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _checkpoints.ResetAsync(scope, Enum.GetValues<EntityType>(), TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        // The property an operator most needs to hold. A reset means "walk the window again",
        // not "start empty" — re-ingestion upserts, so nothing here is rebuilt from scratch.
        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var commit = await new EvidenceRepository().GetCommitAsync(read, "sha8888", TestContext.Current.CancellationToken);
        Assert.NotNull(commit);
        Assert.Equal("feat: planner", commit.Message);
        Assert.Single(commit.Files);

        var chunkCount = await read.Connection.ExecuteScalarAsync<int>(new CommandDefinition(
            "select count(*) from evidence_chunks", transaction: read.Transaction,
            cancellationToken: TestContext.Current.CancellationToken));
        Assert.Equal(1, chunkCount);

        Assert.Equal(1, await _chunks.CountEmbeddedAsync(read, TestContext.Current.CancellationToken));
    }
}
