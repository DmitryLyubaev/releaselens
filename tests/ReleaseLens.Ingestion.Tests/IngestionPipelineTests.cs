using System;
using System.Linq;
using System.Threading.Tasks;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Ingestion.Tests;

[Collection(nameof(PostgresCollection))]
public class IngestionPipelineTests(PostgresFixture fixture)
{
    private static CommitEvidence Commit(Guid tenant, string sha, string message) => new(
        tenant, sha, message, "Alice", "alice@example.com",
        new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero),
        new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero),
        $"https://github.com/microsoft/semantic-kernel/commit/{sha}", []);

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> ArrangeTenantAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "fake", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private static IngestionPipeline Build(
        TenantConnectionFactory factory, FakeEvidenceSource source, FakeEmbedder embedder) =>
        new(source, embedder, new EvidenceChunker(ChunkOptions.Default), factory,
            new EvidenceRepository(), new ChunkRepository(), new CheckpointRepository(),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<IngestionPipeline>.Instance);

    [Fact]
    public async Task Run_StoresCommitsChunksAndEmbeddings()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-happy");
        var source = new FakeEvidenceSource();
        source.CommitBatches.Add(EvidenceBatch<CommitEvidence>.Final(
            [Commit(tenantId, "sha0001", "fix: planner drops tool results")]));

        var report = await Build(factory, source, new FakeEmbedder())
            .RunAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Equal(1, report.CommitsIngested);
        Assert.True(report.ChunksWritten >= 1);
        Assert.Equal(report.ChunksWritten, report.ChunksEmbedded);
        Assert.Equal(0, report.DeadLettered);
    }

    [Fact]
    public async Task Run_SavesACheckpointSoASecondRunResumes()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-checkpoint");
        var source = new FakeEvidenceSource();
        source.CommitBatches.Add(new EvidenceBatch<CommitEvidence>(
            [Commit(tenantId, "sha0002", "feat: add planner")], "2026-03-01T00:00:00Z", "W/\"e1\"", false));

        var pipeline = Build(factory, source, new FakeEmbedder());
        await pipeline.RunAsync(tenantId, TestContext.Current.CancellationToken);
        await pipeline.RunAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Equal(2, source.CommitCursorsSeen.Count);
        Assert.Null(source.CommitCursorsSeen[0].Value);
        Assert.Equal("2026-03-01T00:00:00Z", source.CommitCursorsSeen[1].Value);
        Assert.Equal("W/\"e1\"", source.CommitCursorsSeen[1].ETag);
    }

    [Fact]
    public async Task Run_NotModifiedBatch_IngestsNothingAndKeepsTheCheckpoint()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-304");
        var source = new FakeEvidenceSource();
        source.CommitBatches.Add(EvidenceBatch<CommitEvidence>.Unchanged("W/\"same\""));

        var report = await Build(factory, source, new FakeEmbedder())
            .RunAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Equal(0, report.CommitsIngested);
        Assert.Equal(0, report.ChunksWritten);
    }

    [Fact]
    public async Task Run_EmbeddingFailure_DeadLettersTheChunkAndKeepsGoing()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-deadletter");
        var source = new FakeEvidenceSource();
        source.CommitBatches.Add(EvidenceBatch<CommitEvidence>.Final(
        [
            Commit(tenantId, "sha0003", "poison: this one explodes"),
            Commit(tenantId, "sha0004", "fine: this one is ok")
        ]));

        var embedder = new FakeEmbedder { FailOnContentContaining = "poison" };
        var report = await Build(factory, source, embedder).RunAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Equal(2, report.CommitsIngested);
        Assert.True(report.DeadLettered >= 1);
        Assert.True(report.ChunksEmbedded >= 1);
    }

    [Fact]
    public async Task Run_IsIdempotent_ASecondRunOverTheSameEvidenceAddsNoDuplicateChunks()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-idempotent");
        var source = new FakeEvidenceSource();
        source.CommitBatches.Add(EvidenceBatch<CommitEvidence>.Final([Commit(tenantId, "sha0005", "fix: thing")]));

        var pipeline = Build(factory, source, new FakeEmbedder());
        var first = await pipeline.RunAsync(tenantId, TestContext.Current.CancellationToken);
        await pipeline.RunAsync(tenantId, TestContext.Current.CancellationToken);

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var totalChunks = await new ChunkRepository().CountEmbeddedAsync(scope, TestContext.Current.CancellationToken);

        Assert.Equal(first.ChunksWritten, totalChunks);
    }

    [Fact]
    public async Task Run_IngestsIssuesPullRequestsAndReleasesToo()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("pipeline-all-types");
        var source = new FakeEvidenceSource();

        source.ReleaseBatches.Add(EvidenceBatch<ReleaseEvidence>.Final(
            [new ReleaseEvidence(tenantId, "dotnet-1.30.0", "1.30.0", "notes",
                new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), "main", "https://example.invalid/r")]));

        source.PullRequestBatches.Add(EvidenceBatch<PullRequestEvidence>.Final(
            [new PullRequestEvidence(tenantId, 900, "Fix planner", "body", "closed",
                new DateTimeOffset(2026, 2, 1, 0, 0, 0, TimeSpan.Zero), "sha0006", "main", "fix/planner",
                "alice", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), "https://example.invalid/p")]));

        source.IssueBatches.Add(EvidenceBatch<IssueEvidence>.Final(
            [new IssueEvidence(tenantId, 800, "Planner bug", "body", "closed", ["bug"], "bob",
                new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), null, "https://example.invalid/i")]));

        var report = await Build(factory, source, new FakeEmbedder())
            .RunAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Equal(1, report.ReleasesIngested);
        Assert.Equal(1, report.PullRequestsIngested);
        Assert.Equal(1, report.IssuesIngested);
    }
}
