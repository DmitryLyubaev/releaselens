using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class CorpusExporterTests(PostgresFixture fixture)
{
    private const string ShaA = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111";
    private const string ShaB = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222";
    private const string ShaNotInCorpus = "cccc3333cccc3333cccc3333cccc3333cccc3333";

    private static readonly DateTimeOffset At = new(2026, 6, 1, 0, 0, 0, TimeSpan.Zero);

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> CreateTenantAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private static CommitEvidence Commit(Guid tenantId, string sha) =>
        new(tenantId, sha, $"commit {sha[..7]}", "A", "a@example.com", At, At,
            $"https://example.invalid/commit/{sha}", []);

    private static PullRequestEvidence PullRequest(
        Guid tenantId, int number, DateTimeOffset? mergedAt, string? mergeCommitSha) =>
        new(tenantId, number, $"pull request {number}", "", mergedAt is null ? "open" : "closed",
            mergedAt, mergeCommitSha, "main", $"feature-{number}", "A", At,
            $"https://example.invalid/pull/{number}");

    /// <summary>2 commits, 1 pull request and 3 chunks, with no embeddings: the export is of chunks.</summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId, IReadOnlyList<long> ChunkIds)> SeedCorpusAsync(
        string slug)
    {
        var (factory, tenantId) = await CreateTenantAsync(slug);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var evidence = new EvidenceRepository();
        await evidence.UpsertCommitsAsync(scope, [Commit(tenantId, ShaA), Commit(tenantId, ShaB)],
            TestContext.Current.CancellationToken);
        await evidence.UpsertPullRequestsAsync(scope, [PullRequest(tenantId, 42, At, ShaA)],
            TestContext.Current.CancellationToken);

        var ids = await new ChunkRepository().UpsertChunksAsync(scope,
        [
            new Chunk(tenantId, EntityType.Commit, ShaA, 0, "[commit aaaa111] fix the planner", 6),
            new Chunk(tenantId, EntityType.PullRequest, "42", 0, "[pull request #42] planner fix", 7),
            new Chunk(tenantId, EntityType.Commit, ShaB, 0, "[commit bbbb222] update the docs", 6)
        ], TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId, ids);
    }

    private static async Task<List<ExportedChunk>> ExportAllAsync(TenantScope scope)
    {
        var chunks = new List<ExportedChunk>();
        await foreach (var chunk in new CorpusExporter().ExportChunksAsync(scope, TestContext.Current.CancellationToken))
        {
            chunks.Add(chunk);
        }

        return chunks;
    }

    [Fact]
    public async Task ExportChunks_ReturnsEveryChunkOfTheTenantWithItsArtefact()
    {
        var (factory, tenantId, ids) = await SeedCorpusAsync("export-chunks");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var chunks = await ExportAllAsync(scope);

        Assert.Equal(ids.Order().ToList(), chunks.Select(c => c.ChunkId).ToList());
        Assert.Equal(
            [$"commit:{ShaA}", "pull_request:42", $"commit:{ShaB}"],
            chunks.Select(c => c.Artefact).ToList());

        var pullRequest = chunks[1];
        Assert.Equal("pull_request", pullRequest.EntityType);
        Assert.Equal("42", pullRequest.EntityKey);
        Assert.Equal(0, pullRequest.ChunkIndex);
        Assert.Equal(7, pullRequest.TokenCount);
        Assert.Equal("[pull request #42] planner fix", pullRequest.Content);
    }

    [Fact]
    public async Task ExportChunks_ExcludesOtherTenants()
    {
        var (factory, tenantA, idsA) = await SeedCorpusAsync("export-tenant-a");
        var (_, tenantB, idsB) = await SeedCorpusAsync("export-tenant-b");

        await using var scope = await factory.OpenAsync(tenantB, TestContext.Current.CancellationToken);
        var chunks = await ExportAllAsync(scope);

        Assert.NotEqual(tenantA, tenantB);
        Assert.Equal(idsB.Order().ToList(), chunks.Select(c => c.ChunkId).ToList());
        Assert.DoesNotContain(chunks, c => idsA.Contains(c.ChunkId));
    }

    [Fact]
    public async Task ExportLinks_PairsAMergedPullRequestWithItsMergeCommit()
    {
        var (factory, tenantId, _) = await SeedCorpusAsync("export-links");

        await using (var seed = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await new EvidenceRepository().UpsertPullRequestsAsync(seed,
            [
                // Merged, but its merge commit is not in the corpus.
                PullRequest(tenantId, 43, At, ShaNotInCorpus),
                // Merged, with no SHA recorded.
                PullRequest(tenantId, 44, At, null),
                // Never merged. GitHub still reports a merge_commit_sha for an open pull
                // request (its test merge), so the SHA alone must not make a link.
                PullRequest(tenantId, 45, null, ShaB)
            ], TestContext.Current.CancellationToken);

            await seed.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var links = await new CorpusExporter().ExportLinksAsync(scope, TestContext.Current.CancellationToken);

        var link = Assert.Single(links);
        Assert.Equal(new ExportedLink("pull_request:42", $"commit:{ShaA}"), link);
    }
}
