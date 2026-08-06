using System;
using System.Linq;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using Xunit;

namespace ReleaseLens.Core.Tests.Chunking;

public class EvidenceChunkerTests
{
    private static readonly Guid Tenant = Guid.Parse("22222222-2222-2222-2222-222222222222");
    private readonly EvidenceChunker _chunker = new(ChunkOptions.Default);

    private static CommitEvidence Commit(string message, params FileChange[] files) => new(
        Tenant, "abc1234def", message, "Alice", "alice@example.com",
        DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
        "https://github.com/o/r/commit/abc1234def", files);

    [Fact]
    public void Chunk_ShortCommit_ProducesExactlyOneChunk()
    {
        var chunks = _chunker.Chunk(Commit("fix: null ref in planner"));
        Assert.Single(chunks);
    }

    [Fact]
    public void Chunk_Commit_HeaderNamesEntityAndShortSha()
    {
        var chunks = _chunker.Chunk(Commit("fix: null ref in planner"));
        Assert.StartsWith("[commit abc1234] fix: null ref in planner", chunks[0].Content, StringComparison.Ordinal);
    }

    [Fact]
    public void Chunk_Commit_IncludesChangedFilePaths()
    {
        var chunks = _chunker.Chunk(Commit(
            "fix: null ref",
            new FileChange("dotnet/src/Planners/Planner.cs", "modified", 12, 3)));

        Assert.Contains("dotnet/src/Planners/Planner.cs", chunks[0].Content, StringComparison.Ordinal);
    }

    [Fact]
    public void Chunk_LongBody_SplitsIntoMultipleChunksWithSequentialIndexes()
    {
        var body = string.Join("\n\n", Enumerable.Range(0, 60)
            .Select(i => $"Paragraph {i} describing behaviour of the planner in some detail."));

        var chunks = _chunker.Chunk(Commit("feat: big change\n\n" + body));

        Assert.True(chunks.Count > 1, $"expected a split, got {chunks.Count}");
        Assert.Equal(Enumerable.Range(0, chunks.Count), chunks.Select(c => c.Index));
    }

    [Fact]
    public void Chunk_EveryChunk_RepeatsTheHeader()
    {
        var body = string.Join("\n\n", Enumerable.Range(0, 60)
            .Select(i => $"Paragraph {i} describing behaviour of the planner in some detail."));

        var chunks = _chunker.Chunk(Commit("feat: big change\n\n" + body));

        Assert.All(chunks, c => Assert.StartsWith("[commit abc1234]", c.Content, StringComparison.Ordinal));
    }

    [Fact]
    public void Chunk_EveryChunk_StaysUnderTheTokenBudget()
    {
        var body = string.Join("\n\n", Enumerable.Range(0, 60)
            .Select(i => $"Paragraph {i} describing behaviour of the planner in some detail."));

        var chunks = _chunker.Chunk(Commit("feat: big change\n\n" + body));

        Assert.All(chunks, c => Assert.True(
            c.ApproxTokens <= ChunkOptions.Default.MaxTokens,
            $"chunk {c.Index} was {c.ApproxTokens} tokens"));
    }

    [Fact]
    public void Chunk_ConsecutiveChunks_Overlap()
    {
        var body = string.Join("\n\n", Enumerable.Range(0, 60)
            .Select(i => $"Paragraph {i} describing behaviour of the planner in some detail."));

        var chunks = _chunker.Chunk(Commit("feat: big change\n\n" + body));
        var tailOfFirst = chunks[0].Content[^40..];

        Assert.Contains(tailOfFirst[..20], chunks[1].Content, StringComparison.Ordinal);
    }

    [Fact]
    public void Chunk_Issue_HeaderCarriesNumberStateAndLabels()
    {
        var issue = new IssueEvidence(
            Tenant, 4211, "Planner drops tool results", "It crashes when the tool returns null.",
            "closed", ["bug", "planner"], "bob",
            DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch, "https://github.com/o/r/issues/4211");

        var chunks = _chunker.Chunk(issue);

        Assert.StartsWith("[issue #4211 closed] Planner drops tool results", chunks[0].Content, StringComparison.Ordinal);
        Assert.Contains("labels: bug, planner", chunks[0].Content, StringComparison.Ordinal);
    }

    [Fact]
    public void Chunk_EmptyBody_StillProducesOneChunkFromTheHeader()
    {
        var release = new ReleaseEvidence(
            Tenant, "v1.30.0", "1.30.0", string.Empty,
            DateTimeOffset.UnixEpoch, "main", "https://github.com/o/r/releases/tag/v1.30.0");

        var chunks = _chunker.Chunk(release);

        Assert.Single(chunks);
        Assert.Contains("v1.30.0", chunks[0].Content, StringComparison.Ordinal);
    }

    [Fact]
    public void Chunk_TenantAndKey_ArePropagatedToEveryChunk()
    {
        var chunks = _chunker.Chunk(Commit("fix: null ref"));
        Assert.All(chunks, c =>
        {
            Assert.Equal(Tenant, c.TenantId);
            Assert.Equal(EntityType.Commit, c.Type);
            Assert.Equal("abc1234def", c.EntityKey);
        });
    }
}
