using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Agent;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Tools;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Llm.Tests;

[Collection(nameof(PostgresCollection))]
public class QueryAgentTests(PostgresFixture fixture)
{
    private readonly FakeEmbedder _embedder = new();

    private sealed class ScriptedProvider(Queue<Func<ChatResponse>> script) : IChatProvider
    {
        public string Name => "scripted";
        public List<ChatRequest> Requests { get; } = [];

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Requests.Add(request);

            if (script.Count == 0)
            {
                throw new InvalidOperationException("Scripted provider ran out of responses.");
            }

            return Task.FromResult(script.Dequeue()());
        }
    }

    private static ChatResponse Text(string text) =>
        new(text, [], new TokenUsage(1000, 50, 0, 0), "end_turn", "claude-sonnet-5", "scripted");

    private static ChatResponse CallTool(string name, string argumentsJson) =>
        new(null,
            [new ToolCall("call_1", name, JsonDocument.Parse(argumentsJson).RootElement.Clone())],
            new TokenUsage(800, 30, 0, 0), "tool_use", "claude-sonnet-5", "scripted");

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunkRepository = new ChunkRepository();

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var commit = new CommitEvidence(tenantId, "sha_agent", "fix: planner null reference", "Alice",
            "a@example.com", DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            "https://github.com/microsoft/semantic-kernel/commit/sha_agent", []);

        await new EvidenceRepository().UpsertCommitsAsync(scope, [commit], TestContext.Current.CancellationToken);

        var chunks = chunker.Chunk(commit);
        var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. chunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    /// <summary>
    /// <paramref name="count"/> single-chunk commits, so seed retrieval at k = count yields
    /// exactly that many distinct artefacts and therefore markers E1..E&lt;count&gt;. Used by the
    /// filtering tests, which need an evidence pool visibly larger than what the answer cites.
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> SeedManyAsync(
        string slug, int count)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunkRepository = new ChunkRepository();

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var commits = Enumerable.Range(0, count).Select(i => new CommitEvidence(
            tenantId, $"sha_many_{i}", $"fix: planner defect number {i}", "Alice",
            "a@example.com", DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            $"https://github.com/microsoft/semantic-kernel/commit/sha_many_{i}", [])).ToList();

        await new EvidenceRepository().UpsertCommitsAsync(scope, commits, TestContext.Current.CancellationToken);

        var chunks = commits.SelectMany(chunker.Chunk).ToList();
        var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. chunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    /// <summary>
    /// A commit whose message is long enough that the chunker splits it into several
    /// chunks, alongside a second commit that fits in one. Seed retrieval then returns
    /// more than one chunk for a single artefact, which is the condition under which
    /// the citation list used to grow one entry per chunk instead of one per artefact.
    /// </summary>
    private async Task<(TenantConnectionFactory Factory, Guid TenantId, IReadOnlyList<Chunk> SplitChunks)>
        SeedSplitEntityAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);

        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunkRepository = new ChunkRepository();

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var split = new CommitEvidence(tenantId, "sha_split", LongPlannerMessage(), "Alice",
            "a@example.com", DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            "https://github.com/microsoft/semantic-kernel/commit/sha_split", []);

        var solo = new CommitEvidence(tenantId, "sha_solo", "docs: planner readme typo", "Bob",
            "b@example.com", DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            "https://github.com/microsoft/semantic-kernel/commit/sha_solo", []);

        await new EvidenceRepository().UpsertCommitsAsync(scope, [split, solo],
            TestContext.Current.CancellationToken);

        var splitChunks = chunker.Chunk(split);
        var allChunks = new List<Chunk>(splitChunks);
        allChunks.AddRange(chunker.Chunk(solo));

        var ids = await chunkRepository.UpsertChunksAsync(scope, allChunks, TestContext.Current.CancellationToken);
        var vectors = await _embedder.EmbedDocumentsAsync(
            [.. allChunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], _embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
        return (factory, tenantId, splitChunks);
    }

    private static string LongPlannerMessage()
    {
        var message = new StringBuilder();
        message.Append("fix: planner null reference\n\n");
        message.Append("sentinel-alpha opens the body of this commit message.\n\n");

        // Comfortably past ChunkOptions.Default.MaxChars so Segment produces several
        // chunks, all carrying the same entity key.
        for (var i = 0; i < 40; i++)
        {
            message.Append("Paragraph ").Append(i)
                   .Append(" describes another part of the planner change in enough words ")
                   .Append("to push this body well past a single chunk's character budget.\n\n");
        }

        message.Append("sentinel-omega closes the body of this commit message.\n");
        return message.ToString();
    }

    /// <summary>
    /// Each seed evidence entry is rendered as "[E&lt;n&gt;] (&lt;entity key&gt;) ...", so the
    /// prompt itself says which marker the model was told to use for which artefact.
    /// </summary>
    private static List<(int Marker, string EntityKey)> EvidenceEntries(string evidenceBlock)
        => [.. Regex.Matches(evidenceBlock, @"^\[E(\d+)\] \((\S+)\) ", RegexOptions.Multiline)
                    .Select(m => (int.Parse(m.Groups[1].Value), m.Groups[2].Value))];

    private QueryAgent Build(IChatProvider provider) => new(
        provider,
        new ToolRegistry(
        [
            new SearchCommitsTool(new HybridRetriever(), _embedder),
            new GetIssueTool(new EvidenceRepository()),
            new DiffBetweenReleasesTool(new EvidenceQueries()),
            new FindRegressionsTool(new EvidenceQueries())
        ]),
        new HybridRetriever(),
        _embedder,
        new AgentOptions { Model = "claude-sonnet-5", MaxIterations = 6 },
        NullLogger<QueryAgent>.Instance);

    [Fact]
    public async Task Answer_SingleTurn_ReturnsTextAndSeedCitations()
    {
        var (factory, tenantId) = await SeedAsync("agent-single");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("The planner null reference was fixed in [E1].")]));

        var answer = await Build(provider).AnswerAsync(scope, "What fixed the planner?", 5,
            TestContext.Current.CancellationToken);

        Assert.Contains("planner", answer.Answer, StringComparison.OrdinalIgnoreCase);
        Assert.NotEmpty(answer.Citations);
        Assert.False(answer.Metadata.Degraded);
        Assert.Equal(1, answer.Metadata.Iterations);
    }

    [Fact]
    public async Task Answer_SeedsTheConversationWithRetrievedEvidence()
    {
        var (factory, tenantId) = await SeedAsync("agent-seed");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>([() => Text("done")]));
        await Build(provider).AnswerAsync(scope, "planner", 5, TestContext.Current.CancellationToken);

        var firstUserMessage = provider.Requests[0].Messages[0].Text!;
        Assert.Contains("[E1]", firstUserMessage, StringComparison.Ordinal);
        Assert.Contains("sha_agent", firstUserMessage, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Answer_ToolCall_ExecutesItAndFeedsTheResultBack()
    {
        var (factory, tenantId) = await SeedAsync("agent-tool");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner","limit":3}"""),
            () => Text("Found it in [E1].")
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(2, answer.Metadata.Iterations);
        Assert.Contains("search_commits", answer.Metadata.ToolsCalled);

        var lastRequest = provider.Requests[^1];
        Assert.Contains(lastRequest.Messages, m => m.ToolResults is { Count: > 0 });
    }

    [Fact]
    public async Task Answer_AccumulatesUsageAcrossIterations()
    {
        var (factory, tenantId) = await SeedAsync("agent-usage");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => Text("done")
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(1800, answer.Metadata.Usage.InputTokens);
        Assert.Equal(80, answer.Metadata.Usage.OutputTokens);
        Assert.True(answer.Metadata.CostUsd > 0);
    }

    [Fact]
    public async Task Answer_StopsAtMaxIterations_AndStillProducesAnAnswer()
    {
        var (factory, tenantId) = await SeedAsync("agent-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        // The seventh response answers the "you have reached the tool-call limit" prompt the
        // ceiling sends with no tools offered. Without it the caller gets nothing back, which
        // is the whole point of the ceiling behaviour.
        script.Enqueue(() => Text("Reached the tool limit; from the evidence so far: [E1]."));

        var provider = new ScriptedProvider(script);

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(6, answer.Metadata.Iterations);

        // Asserting NotNull is not enough: a tool-call response carries null text, which the
        // loop coalesces to an empty string, so NotNull passes while proving nothing about
        // whether the ceiling actually produced an answer.
        Assert.False(string.IsNullOrWhiteSpace(answer.Answer));
        Assert.Contains("tool limit", answer.Answer, StringComparison.OrdinalIgnoreCase);

        // Exactly one extra request beyond the six iterations, and it offered no tools — so the
        // model cannot call another one on the way out.
        Assert.Equal(7, provider.Requests.Count);
        Assert.Empty(provider.Requests[^1].Tools);
    }

    [Fact]
    public async Task Answer_AllProvidersDown_ReturnsRetrievedEvidenceUnsynthesised()
    {
        var (factory, tenantId) = await SeedAsync("agent-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw new AllProvidersUnavailableException(["anthropic", "openai"])]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.NotNull(answer.Metadata.DegradedReason);
        Assert.NotEmpty(answer.Citations);
        Assert.Contains("sha_agent", answer.Answer, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Answer_RecordsRetrievalShortfallInMetadata()
    {
        var (factory, tenantId) = await SeedAsync("agent-shortfall");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>([() => Text("done")]));
        var answer = await Build(provider).AnswerAsync(scope, "planner", 50, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.RetrievalTruncated);
        Assert.Equal(50, answer.Metadata.RequestedK);
        Assert.NotNull(answer.Metadata.RetrievalNote);
    }

    [Fact]
    public async Task Answer_CitationMarkerOutsideTheEvidenceRange_IsReported()
    {
        var (factory, tenantId) = await SeedAsync("agent-bad-marker");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("This is supported by [E99], obviously.")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 5, TestContext.Current.CancellationToken);

        Assert.Contains("E99", answer.Metadata.UnresolvedCitationMarkers);
    }

    [Fact]
    public async Task Answer_SeedChunksOfOneEntity_ShareASingleCitationAndMarker()
    {
        var (factory, tenantId, splitChunks) = await SeedSplitEntityAsync("agent-split-entity");

        // Guards the fixture, not the agent: with only one chunk the test would pass
        // against the duplicating code and prove nothing.
        Assert.True(splitChunks.Count >= 2,
            $"fixture must split the commit into at least two chunks, got {splitChunks.Count}");

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // The answer cites both markers. Only two artefacts are seeded and markers are issued
        // densely, so the set is always {1, 2} whatever order retrieval ranks them in — and
        // citing both is what keeps this test about artefact-level deduplication rather than
        // about the cited-only filter, which has its own tests below.
        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("Both of these are relevant: [E1] and [E2].")]));
        var answer = await Build(provider).AnswerAsync(scope, "planner", 20,
            TestContext.Current.CancellationToken);

        var evidence = provider.Requests[0].Messages[0].Text!;
        var entries = EvidenceEntries(evidence);

        // Both artefacts were retrieved, and the split one contributed several chunks —
        // otherwise the deduplication under test is never exercised.
        Assert.True(entries.Count(e => e.EntityKey == "sha_split") >= 2,
            $"expected at least two seed chunks for sha_split, evidence block was:\n{evidence}");
        Assert.Contains(entries, e => e.EntityKey == "sha_solo");

        // 1. A citation identifies an artefact, so each artefact appears exactly once.
        Assert.Equal(
            answer.Citations.Select(c => (c.Citation.Type, c.Citation.EntityKey)).Distinct().Count(),
            answer.Citations.Count);
        Assert.Single(answer.Citations, c => c.Citation.EntityKey == "sha_split");
        Assert.Single(answer.Citations, c => c.Citation.EntityKey == "sha_solo");

        // 2. Collapsing the citation list must not collapse the evidence: the model still
        //    sees the text of every chunk, including the parts unique to each one.
        Assert.Contains(splitChunks[0].Content, evidence, StringComparison.Ordinal);
        Assert.Contains(splitChunks[1].Content, evidence, StringComparison.Ordinal);
        Assert.Contains("sentinel-alpha", evidence, StringComparison.Ordinal);
        Assert.Contains("sentinel-omega", evidence, StringComparison.Ordinal);

        // 3. Every chunk of the artefact points at one marker, and that marker resolves to
        //    the artefact. This is what a fix that dedupes the list but leaves the markers
        //    numbered per chunk would fail.
        //    Looked up BY MARKER, not by array position: the returned list is the cited subset,
        //    so index arithmetic on it is exactly the inference the marker field exists to kill.
        var splitMarker = Assert.Single(
            entries.Where(e => e.EntityKey == "sha_split").Select(e => e.Marker).Distinct());
        Assert.Equal("sha_split",
            Assert.Single(answer.Citations, c => c.Marker == splitMarker).Citation.EntityKey);

        var soloMarker = Assert.Single(
            entries.Where(e => e.EntityKey == "sha_solo").Select(e => e.Marker).Distinct());
        Assert.Equal("sha_solo",
            Assert.Single(answer.Citations, c => c.Marker == soloMarker).Citation.EntityKey);
    }

    /// <summary>
    /// The defect this whole change is about: the response used to carry every artefact any
    /// tool had returned, so a query that swept 48 releases reported 48 citations behind an
    /// answer resting on two. Precision was measured at 0.090 against the golden set.
    /// </summary>
    [Fact]
    public async Task Answer_CitingTwoOfEightArtefacts_ReturnsOnlyThoseTwoWithTheirMarkers()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-cited-subset", 8);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("The regression came in via [E2] and was reverted by [E5].")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 8,
            TestContext.Current.CancellationToken);

        // The pool really was eight, so "returns two" is a filter doing work rather than a
        // retrieval that happened to find two things.
        Assert.Equal(8, answer.Metadata.AccumulatedCitationCount);

        Assert.Equal([2, 5], answer.Citations.Select(c => c.Marker).ToArray());

        // Each marker still points at the artefact the evidence block told the model it meant.
        // A filter that renumbered as it shrank would pass the count assertion above and
        // silently re-aim both markers — the failure mode worth more than the one being fixed.
        var entries = EvidenceEntries(provider.Requests[0].Messages[0].Text!);

        foreach (var citation in answer.Citations)
        {
            var expected = Assert.Single(
                entries.Where(e => e.Marker == citation.Marker).Select(e => e.EntityKey).Distinct());
            Assert.Equal(expected, citation.Citation.EntityKey);
        }

        Assert.Empty(answer.Metadata.UnresolvedCitationMarkers);
    }

    [Fact]
    public async Task Answer_WithAHallucinatedMarker_ReportsItAndProducesNoCitation()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-hallucinated", 3);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("Supported by [E1] and, allegedly, [E9].")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 3,
            TestContext.Current.CancellationToken);

        // Validation ran against the full accumulated pool, before filtering — which is the
        // ordering that makes "E9 is out of range" mean anything. Had it run after, E9 would
        // have been checked against a one-entry list and E1 would have been the only survivor
        // either way, so this assertion pair is what pins the order.
        Assert.Equal(["E9"], answer.Metadata.UnresolvedCitationMarkers.ToArray());
        Assert.Equal(3, answer.Metadata.AccumulatedCitationCount);

        // Out of range yields nothing rather than a phantom entry.
        Assert.Equal([1], answer.Citations.Select(c => c.Marker).ToArray());
    }

    [Fact]
    public async Task Answer_RepeatingAMarker_YieldsOneCitation()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-repeat-marker", 3);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("First [E2]. Then again [E2]. And once more, [E2].")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 3,
            TestContext.Current.CancellationToken);

        var only = Assert.Single(answer.Citations);
        Assert.Equal(2, only.Marker);
    }

    [Fact]
    public async Task Answer_WithNoMarkersAtAll_ReturnsNoCitations()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-declines", 3);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // A decline. The evidence exists and was retrieved, but the answer rests on none of
        // it, so it is not what the answer rests on — the response says so by carrying nothing.
        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("The indexed evidence does not answer this question.")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 3,
            TestContext.Current.CancellationToken);

        Assert.Empty(answer.Citations);
        Assert.Equal(3, answer.Metadata.AccumulatedCitationCount);
    }

    /// <summary>
    /// The degraded response is the evidence block verbatim, and every entry in it is labelled
    /// "[E&lt;n&gt;]" — so the ordinary cited-only filter keeps exactly the artefacts the caller
    /// can see, and the caller can still attribute all of the prose they were handed. The
    /// intended behaviour is NOT "skip filtering when degraded": artefacts a tool returned
    /// before the provider died appear nowhere in the degraded text, and shipping those would
    /// reproduce the bug in miniature.
    /// </summary>
    [Fact]
    public async Task Answer_AllProvidersDown_CitesTheEvidenceItShows_AndNotToolResultsItDoesNot()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-degraded-filter", 3);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            // One successful tool call widens the accumulated pool beyond the seed...
            () => CallTool("search_commits", """{"query":"planner","limit":3}"""),
            // ...and then every provider dies, so the caller gets the seed evidence only.
            () => throw new AllProvidersUnavailableException(["anthropic", "openai"])
        ]));

        // k = 1 so the seed contributes exactly one artefact and the tool contributes the rest.
        var answer = await Build(provider).AnswerAsync(scope, "planner", 1,
            TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.True(answer.Metadata.AccumulatedCitationCount > 1,
            "the tool call must have added artefacts beyond the seed, or this proves nothing");

        // Exactly the markers the degraded text itself displays — no more, no fewer.
        var shown = Regex.Matches(answer.Answer, @"\[E(\d+)\]")
                         .Select(m => int.Parse(m.Groups[1].Value))
                         .Distinct().Order().ToArray();

        Assert.NotEmpty(shown);
        Assert.Equal(shown, answer.Citations.Select(c => c.Marker).ToArray());

        // Every artefact the caller can read about is attributable.
        foreach (var citation in answer.Citations)
        {
            Assert.Contains(citation.Citation.EntityKey, answer.Answer, StringComparison.Ordinal);
        }
    }
}
