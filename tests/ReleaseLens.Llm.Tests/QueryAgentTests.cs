using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
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
    public async Task Answer_StopsAtMaxIterations()
    {
        var (factory, tenantId) = await SeedAsync("agent-ceiling");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var script = new Queue<Func<ChatResponse>>();
        for (var i = 0; i < 10; i++)
        {
            script.Enqueue(() => CallTool("search_commits", """{"query":"planner"}"""));
        }

        var answer = await Build(new ScriptedProvider(script))
            .AnswerAsync(scope, "planner?", 5, TestContext.Current.CancellationToken);

        Assert.Equal(6, answer.Metadata.Iterations);
        Assert.NotNull(answer.Answer);
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
}
