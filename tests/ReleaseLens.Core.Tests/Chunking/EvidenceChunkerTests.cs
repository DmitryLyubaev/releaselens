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
    public void Chunk_HeaderLongEnoughToStarveTheBudget_StillSegmentsSanely()
    {
        // A ~1000-character single-line commit subject leaves a budget below the overlap
        // width. Without the guard, Segment advances one character per iteration and this
        // produces thousands of near-duplicate chunks instead of a handful.
        var subject = new string('x', 1000);
        var body = string.Join("\n\n", Enumerable.Range(0, 40)
            .Select(i => $"Paragraph {i} describing behaviour of the planner in some detail."));

        var chunks = _chunker.Chunk(Commit(subject + "\n\n" + body));

        Assert.True(chunks.Count < 20,
            $"expected a sane number of chunks, got {chunks.Count} — the budget/overlap guard is not holding");
        Assert.All(chunks, c => Assert.True(
            c.ApproxTokens <= ChunkOptions.Default.MaxTokens,
            $"chunk {c.Index} was {c.ApproxTokens} tokens"));
    }

    // One fixed body is not enough. Whether a cut lands mid-pair depends on where the emoji
    // happen to fall relative to the segment boundaries, and the obvious body misses by luck:
    // with the guards stripped out, 2 of these 10 offsets still clear every boundary. Shifting
    // the body one character at a time sweeps the boundaries across a surrogate pair whatever
    // MaxChars and OverlapChars are set to, so the test cannot silently lose its teeth.
    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(2)]
    [InlineData(3)]
    [InlineData(4)]
    [InlineData(5)]
    [InlineData(6)]
    [InlineData(7)]
    [InlineData(8)]
    [InlineData(9)]
    public void Chunk_BodyContainingEmoji_NeverSplitsASurrogatePair(int shift)
    {
        // Slicing by character index can land between the two halves of an emoji, producing a
        // lone surrogate: invalid UTF-16 that Npgsql's UTF-8 encoder rejects, killing an
        // ingest mid-run. This is exactly how the first real ingest of microsoft/semantic-kernel
        // died, on a robot-face emoji in a commit message.
        var body = new string('x', shift)
            + string.Join(" ", Enumerable.Range(0, 400).Select(i => $"word{i} 🤖"));

        var chunks = _chunker.Chunk(Commit("feat: emoji everywhere\n\n" + body));

        Assert.True(chunks.Count > 1, "expected the body to split across chunks");

        foreach (var chunk in chunks)
        {
            Assert.False(char.IsLowSurrogate(chunk.Content[0]),
                $"chunk {chunk.Index} starts with an orphaned low surrogate");
            Assert.False(char.IsHighSurrogate(chunk.Content[^1]),
                $"chunk {chunk.Index} ends with an orphaned high surrogate");

            // Round-trips through UTF-8 exactly, which is what Postgres requires.
            var bytes = System.Text.Encoding.UTF8.GetBytes(chunk.Content);
            Assert.Equal(chunk.Content, System.Text.Encoding.UTF8.GetString(bytes));
        }
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
