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
        ],
        NullLogger<ToolRegistry>.Instance),
        new HybridRetriever(),
        _embedder,
        new AgentOptions { MaxIterations = 6 },
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

    private static readonly PricingIdentity Haiku = new("anthropic", "claude-haiku-4-5-20251001");

    private static readonly PricingIdentity AzureRegional =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    /// <summary>800 in, 30 out: 0.00095 at Haiku 4.5's 1.00/5.00, 0.0004048 at Azure's 0.44/1.76.</summary>
    private static ChatResponse PricedCallTool(string provider, string model, PricingIdentity pricing) =>
        CallTool("search_commits", """{"query":"planner"}""") with
        {
            Provider = provider, Model = model, Pricing = pricing
        };

    /// <summary>1,000 in, 50 out: 0.00125 at Haiku 4.5, 0.000528 at Azure.</summary>
    private static ChatResponse PricedText(string text, string provider, string model, PricingIdentity pricing) =>
        Text(text) with { Provider = provider, Model = model, Pricing = pricing };

    // T-C5
    [Fact]
    public async Task Answer_ProviderChangesMidQuery_IsPricedPerIteration()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-per-iteration");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => PricedCallTool("anthropic", "claude-haiku-4-5-20251001", Haiku),
            () => PricedText("Found it in [E1].", "azure-openai", "gpt-4.1-mini-2025-04-14", AzureRegional)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        // 0.00095 for the Anthropic iteration plus 0.000528 for the Azure one. Pricing the whole
        // query at the last provider's rate would give 0.0009328; the code before this fix priced
        // it by the last model name and gave $0.
        Assert.Equal(0.001478m, answer.Metadata.CostUsd);
        Assert.Equal("azure-openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_CeilingFinalCall_IsPricedAtItsOwnProvidersRate()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => PricedCallTool("anthropic", "claude-haiku-4-5-20251001", Haiku));
        }

        script.Enqueue(() => PricedText("From the evidence so far: [E1].", "azure-openai", "gpt-4.1-mini", AzureRegional));

        var answer = await Build(new ScriptedProvider(script)).AnswerAsync(
            scope, "planner?", 5, TestContext.Current.CancellationToken);

        // Six Anthropic iterations at 0.00095 each, plus the no-tools final call at Azure's 0.000528.
        Assert.Equal(0.006228m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_ProvidersFailAfterAPricedIteration_TheDegradedAnswerKeepsThatCost()
    {
        var (factory, tenantId) = await SeedAsync("agent-priced-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => PricedCallTool("azure-openai", "gpt-4.1-mini-2025-04-14", AzureRegional),
            () => throw new AllProvidersUnavailableException(["azure-openai"])
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);

        // The versioned name has no name-based rate, so pricing it by name gave $0 here.
        Assert.Equal(0.0004048m, answer.Metadata.CostUsd);
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

        // No provider answered, so no model did either; the agent has no model of its own.
        Assert.Equal("none", answer.Metadata.Provider);
        Assert.Equal("none", answer.Metadata.Model);
    }

    [Fact]
    public async Task Answer_RecordsRetrievalShortfallInMetadata()
    {
        var (factory, tenantId) = await SeedAsync("agent-shortfall");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>([() => Text("done")]));
        var answer = await Build(provider).AnswerAsync(scope, "planner", 50, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.RetrievalFewerThanRequested);
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

    /// <summary>
    /// SeedManyAsync gives every commit a unique subject, "fix: planner defect number N", and
    /// that subject is in the chunk header. So the text filed under a marker can be checked
    /// against the artefact that marker resolves to, rather than merely checked for being
    /// non-empty — which is what "the text corresponds to the right marker" has to mean.
    /// </summary>
    private static string SubjectOf(string entityKey)
        => "defect number " + entityKey["sha_many_".Length..];

    /// <summary>
    /// The defect: the response carried "issue:14111" and nothing else, so a groundedness
    /// judge asked whether the answer's claims were supported could only report that it had
    /// been given bare identifiers. Every cited artefact now carries the text the model read.
    /// </summary>
    [Fact]
    public async Task Answer_CitedArtefacts_CarryTheTextTheModelWasShown()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-evidence-text", 8);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("The regression came in via [E2] and was reverted by [E5].")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 8,
            TestContext.Current.CancellationToken);

        var evidenceBlock = provider.Requests[0].Messages[0].Text!;

        Assert.Equal([2, 5], answer.Citations.Select(c => c.Marker).ToArray());

        foreach (var citation in answer.Citations)
        {
            Assert.NotEmpty(citation.Excerpts);

            foreach (var excerpt in citation.Excerpts)
            {
                // The text belongs to THIS artefact, not to whichever one happened to be
                // retrieved first. A ledger keyed by anything but the marker would pass an
                // "is not empty" assertion while handing the judge another commit's message.
                Assert.Contains(SubjectOf(citation.Citation.EntityKey), excerpt, StringComparison.Ordinal);

                // And it is the text the model itself read, not a paraphrase or a rebuild.
                Assert.Contains(excerpt, evidenceBlock, StringComparison.Ordinal);
            }
        }
    }

    /// <summary>
    /// The decision that the judge's verdict actually turns on. An artefact split across
    /// chunks shares ONE marker, and the evidence behind that marker is every one of its
    /// chunks: a claim supported by the last chunk reads as unsupported to a reader shown
    /// only the first, which is a false groundedness failure caused by the harness.
    /// </summary>
    [Fact]
    public async Task Answer_EvidenceForAMarker_IsEveryChunkOfTheArtefact_NotJustTheFirst()
    {
        var (factory, tenantId, splitChunks) = await SeedSplitEntityAsync("agent-evidence-chunks");

        Assert.True(splitChunks.Count >= 2,
            $"fixture must split the commit into at least two chunks, got {splitChunks.Count}");

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => Text("Both of these are relevant: [E1] and [E2].")]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 20,
            TestContext.Current.CancellationToken);

        var split = Assert.Single(answer.Citations, c => c.Citation.EntityKey == "sha_split");

        Assert.True(split.Excerpts.Count >= 2,
            $"expected every chunk of sha_split, got {split.Excerpts.Count}");

        var joined = string.Join("\n", split.Excerpts);

        // The sentinels sit at opposite ends of the commit body, so both being present is
        // the whole artefact rather than a lucky first chunk.
        Assert.Contains("sentinel-alpha", joined, StringComparison.Ordinal);
        Assert.Contains("sentinel-omega", joined, StringComparison.Ordinal);

        // The single-chunk artefact is unaffected: one marker, one fragment, no padding
        // borrowed from its neighbour.
        var solo = Assert.Single(answer.Citations, c => c.Citation.EntityKey == "sha_solo");
        Assert.Single(solo.Excerpts);
        Assert.DoesNotContain("sentinel-alpha", solo.Excerpts[0], StringComparison.Ordinal);
    }

    /// <summary>
    /// Text arriving from a tool is filed under the marker its artefact already holds, and
    /// filing it must not itself create an artefact: an excerpt that appended to the pool
    /// would move AccumulatedCitationCount and could make an out-of-range marker resolve.
    /// </summary>
    [Fact]
    public async Task Answer_EvidenceFromAToolResult_IsFiledUnderItsArtefactsMarker_WithoutWideningThePool()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-evidence-tool", 6);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // k = 1, so the seed contributes one artefact and search_commits contributes the rest.
        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner","limit":4}"""),
            () => Text("Everything relevant: [E1], [E2], [E3], [E4].")
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 1,
            TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.AccumulatedCitationCount > 1,
            "the tool call must have added artefacts beyond the seed, or this proves nothing");

        // Every marker the tool contributed resolves, and the pool is exactly the artefacts
        // the seed and the tool cited between them — the excerpts added none of their own.
        Assert.Empty(answer.Metadata.UnresolvedCitationMarkers);
        Assert.Equal(
            answer.Metadata.AccumulatedCitationCount,
            answer.Citations.Select(c => c.Citation.EntityKey).Distinct().Count());

        foreach (var citation in answer.Citations)
        {
            Assert.NotEmpty(citation.Excerpts);
            Assert.All(citation.Excerpts, excerpt =>
                Assert.Contains(SubjectOf(citation.Citation.EntityKey), excerpt, StringComparison.Ordinal));
        }
    }

    /// <summary>
    /// The degraded response is the evidence block verbatim, so its text is redundant — and
    /// it is carried anyway. Withholding it would hand anything scoring groundedness an
    /// empty evidence list for a response that is nothing BUT evidence, and score it zero
    /// for a harness reason: the same failure this change exists to remove.
    /// </summary>
    [Fact]
    public async Task Answer_AllProvidersDown_StillCarriesTheTextOfWhatItCites()
    {
        var (factory, tenantId) = await SeedManyAsync("agent-evidence-degraded", 3);
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw new AllProvidersUnavailableException(["anthropic", "openai"])]));

        var answer = await Build(provider).AnswerAsync(scope, "planner", 3,
            TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.NotEmpty(answer.Citations);

        foreach (var citation in answer.Citations)
        {
            Assert.NotEmpty(citation.Excerpts);
            Assert.All(citation.Excerpts, excerpt =>
                Assert.Contains(SubjectOf(citation.Citation.EntityKey), excerpt, StringComparison.Ordinal));
        }
    }

    /// <summary>
    /// A chain member with a name of its own, for the tests that put a real
    /// <see cref="FallbackChatProvider"/> in front of the agent. A null entry in the script is
    /// an outage.
    /// </summary>
    private sealed class NamedProvider(string name, Queue<Func<ChatResponse>?> script) : IChatProvider
    {
        public string Name => name;
        public int Calls { get; private set; }

        public Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
        {
            Calls++;

            if (script.Count == 0)
            {
                throw new InvalidOperationException($"{name} ran out of responses.");
            }

            var next = script.Dequeue()
                ?? throw new ProviderUnavailableException(name, $"{name} is down");

            return Task.FromResult(next() with { Provider = name });
        }
    }

    // T-A2, the providers half
    [Fact]
    public async Task Answer_ListsEachAnsweringProviderOnce_InTheOrderTheyFirstAnswered()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-order");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}""") with { Provider = "openai" },
            () => CallTool("search_commits", """{"query":"planner"}""") with { Provider = "anthropic" },
            () => Text("Fixed in [E1].") with { Provider = "openai" }
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(["openai", "anthropic"], answer.Metadata.Providers);

        // `provider` keeps its meaning: whoever answered the final iteration.
        Assert.Equal("openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_TheCeilingsFinalCall_CountsAsAnAnsweringProvider()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        script.Enqueue(() => Text("Reached the tool limit: [E1].") with { Provider = "openai", Model = "gpt-4o" });

        var answer = await Build(new ScriptedProvider(script)).AnswerAsync(
            scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(["scripted", "openai"], answer.Metadata.Providers);
        Assert.Equal("openai", answer.Metadata.Provider);
        Assert.Equal("gpt-4o", answer.Metadata.Model);
    }

    [Fact]
    public async Task Answer_GivesEveryCallOfAQuery_TheSameContext_AndEachQueryItsOwn()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-context");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // Query 1 hits the iteration ceiling, so its requests include the final no-tools call;
        // query 2 answers at once.
        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        script.Enqueue(() => Text("Reached the tool limit: [E1]."));
        script.Enqueue(() => Text("Second query: [E1]."));

        var provider = new ScriptedProvider(script);
        var agent = Build(provider);

        await agent.AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);
        await agent.AnswerAsync(scope, "planner again?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(8, provider.Requests.Count);

        var first = Assert.IsType<QueryContext>(provider.Requests[0].Context);
        Assert.All(provider.Requests.Take(7), r => Assert.Same(first, r.Context));

        var second = Assert.IsType<QueryContext>(provider.Requests[7].Context);
        Assert.NotSame(first, second);

        // The budget comes from AgentOptions.RateLimitWaitBudgetMs, whose default is 3000.
        Assert.Equal(TimeSpan.FromMilliseconds(3000), second.RateLimitWaitRemaining);
    }

    [Fact]
    public async Task Answer_ThroughTheFallbackChain_StaysOnTheProviderThatAnsweredFirst()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-sticky");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // A is down for the first iteration and healthy after. Without the query's context the
        // chain would restart at A on iteration 2, and A would answer it.
        var a = new NamedProvider("a", new Queue<Func<ChatResponse>?>(
            [null, () => Text("A answered [E1].")]));
        var b = new NamedProvider("b", new Queue<Func<ChatResponse>?>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => Text("B answered [E1].")
        ]));

        var chain = new FallbackChatProvider([a, b], NullLogger<FallbackChatProvider>.Instance);

        var answer = await Build(chain).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal("B answered [E1].", answer.Answer);
        Assert.Equal(["b"], answer.Metadata.Providers);
        Assert.Equal("b", answer.Metadata.Provider);
        Assert.Equal(1, a.Calls);
        Assert.Equal(2, b.Calls);
    }

    [Fact]
    public async Task Answer_AllProvidersDownMidQuery_StillListsTheProviderThatAnswered()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-degraded");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => throw new AllProvidersUnavailableException(["anthropic", "openai"])
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.Equal(["scripted"], answer.Metadata.Providers);
    }

    [Fact]
    public async Task Answer_AllProvidersDownFromTheStart_ListsNoProviders()
    {
        var (factory, tenantId) = await SeedAsync("agent-providers-none");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw new AllProvidersUnavailableException(["anthropic", "openai"])]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.True(answer.Metadata.Degraded);
        Assert.NotNull(answer.Metadata.Providers);
        Assert.Empty(answer.Metadata.Providers);
    }

    private const string FilteredAnswer =
        "The request was blocked by the model provider's content filter, so no answer was produced.";

    private static readonly PricingIdentity AzureStandard =
        new("azure-openai", "gpt-4.1-mini", "2025-04-14", "Standard");

    /// <summary>1,000 uncached in, 50 out: 0.000528 at Azure's 0.44 / 1.76.</summary>
    private static readonly TokenUsage AzureToolUsage = new(1000, 50, 0, 0);

    /// <summary>
    /// 2,000 uncached in, 500 cached in, 100 out: 0.001111 at Azure's 0.44 / 0.11 / 1.76. The
    /// cached tokens make the filtered call's own rate visible in the total.
    /// </summary>
    private static readonly TokenUsage FilteredCompletionUsage = new(2000, 100, 500, 0);

    // A versioned model string, as a real Azure response may carry: recorded, never priced.
    private static ChatResponse AzureCallTool() =>
        CallTool("search_commits", """{"query":"planner"}""") with
        {
            Usage = AzureToolUsage,
            Model = "gpt-4.1-mini-2025-04-14",
            Provider = "azure-openai",
            Pricing = AzureStandard
        };

    private static ContentFilteredException AzureFiltered(ContentFilterStage stage, TokenUsage usage) =>
        new("azure-openai", stage, usage, AzureStandard);

    // T-C6
    [Fact]
    public async Task Answer_NormalIterationThenFilteredCompletion_CountsAndPricesBothIterations()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-completion");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            AzureCallTool,
            () => throw AzureFiltered(ContentFilterStage.Completion, FilteredCompletionUsage)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Empty(answer.Citations);
        Assert.False(answer.Metadata.Degraded);
        Assert.Null(answer.Metadata.DegradedReason);
        Assert.Equal(new FilteredOutcome("completion", "azure-openai"), answer.Metadata.Filtered);

        Assert.Equal(2, provider.Requests.Count);
        Assert.Equal(2, answer.Metadata.Iterations);
        Assert.Equal(["search_commits"], answer.Metadata.ToolsCalled);
        Assert.Equal(["azure-openai"], answer.Metadata.Providers!);
        Assert.Equal("azure-openai", answer.Metadata.Provider);

        Assert.Equal(AzureToolUsage + FilteredCompletionUsage, answer.Metadata.Usage);

        // 0.000528 for the answered iteration plus 0.001111 for the filtered one. Stopping at
        // the iteration before the filter would give 0.000528.
        Assert.Equal(0.001639m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_FilteredFinalNoToolsCall_IsCaughtCountedAndPriced()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-final");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // Six tool-calling iterations reach the ceiling; the no-tools call the ceiling makes on
        // the way out is the one that is filtered.
        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 6; i++)
        {
            script.Enqueue(AzureCallTool);
        }

        script.Enqueue(() => throw AzureFiltered(ContentFilterStage.Completion, FilteredCompletionUsage));

        var provider = new ScriptedProvider(script);

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Empty(answer.Citations);
        Assert.Equal(new FilteredOutcome("completion", "azure-openai"), answer.Metadata.Filtered);
        Assert.Equal(7, provider.Requests.Count);
        Assert.Empty(provider.Requests[^1].Tools);
        Assert.Equal(6, answer.Metadata.Iterations);

        // Six answered iterations at 0.000528 each, plus the filtered final call's 0.001111.
        Assert.Equal(new TokenUsage(8000, 400, 500, 0), answer.Metadata.Usage);
        Assert.Equal(0.004279m, answer.Metadata.CostUsd);
    }

    [Fact]
    public async Task Answer_FilteredPromptOnTheFirstCall_ReturnsTheFilteredAnswerAndCostsNothing()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-prompt");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
            [() => throw AzureFiltered(ContentFilterStage.Prompt, TokenUsage.Zero)]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(FilteredAnswer, answer.Answer);
        Assert.Equal(new FilteredOutcome("prompt", "azure-openai"), answer.Metadata.Filtered);
        Assert.False(answer.Metadata.Degraded);

        // Seed evidence was retrieved and shown to the model, but the answer rests on none of
        // it, so none of it is returned as a citation.
        Assert.True(answer.Metadata.AccumulatedCitationCount >= 1,
            "seed retrieval must have found the commit, or the empty citation list proves nothing");
        Assert.Empty(answer.Citations);

        Assert.Single(provider.Requests);
        Assert.Equal(TokenUsage.Zero, answer.Metadata.Usage);
        Assert.Equal(0m, answer.Metadata.CostUsd);

        // No provider answered: the only call was refused.
        Assert.Empty(answer.Metadata.Providers!);
        Assert.Equal("azure-openai", answer.Metadata.Provider);
    }

    [Fact]
    public async Task Answer_FilteredWithoutAPricingIdentity_IsPricedByTheLastRecordedModelName()
    {
        var (factory, tenantId) = await SeedAsync("agent-filtered-by-name");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        // A test fake may report no identity. The filtered call is then priced by the name of
        // the model the query last recorded, as CostOf prices such a response.
        var filteredUsage = new TokenUsage(2000, 100, 0, 0);
        var provider = new ScriptedProvider(new Queue<Func<ChatResponse>>(
        [
            () => CallTool("search_commits", """{"query":"planner"}"""),
            () => throw new ContentFilteredException("scripted", ContentFilterStage.Completion, filteredUsage)
        ]));

        var answer = await Build(provider).AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var expected = ModelPricing.CostUsd("claude-sonnet-5", new TokenUsage(800, 30, 0, 0), today)
                     + ModelPricing.CostUsd("claude-sonnet-5", filteredUsage, today);

        Assert.True(expected > 0m, "claude-sonnet-5 must have a name-based rate, or this test proves nothing");
        Assert.Equal(expected, answer.Metadata.CostUsd);
        Assert.Equal("claude-sonnet-5", answer.Metadata.Model);
    }
}
