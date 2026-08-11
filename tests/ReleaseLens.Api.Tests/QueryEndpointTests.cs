using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Api;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Embedding;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Api.Tests;

[Collection(nameof(PostgresCollection))]
public class QueryEndpointTests(PostgresFixture fixture) : IAsyncLifetime
{
    private WebApplicationFactory<Program> _factory = null!;
    private HttpClient _client = null!;
    private string _apiKey = null!;
    private Guid _tenantId;

    // A distinct tenant per test. xUnit constructs a new instance of the class for every test,
    // so this is unique per test — which is the point. TenantRepository.CreateAsync upserts on
    // slug, so a fixed slug would give all nine tests ONE shared tenant row with one shared
    // daily budget: the two tests that seed 5,000 tokens of usage would push every test that
    // runs after them over the 1,000-token cap, turning an expected 200 into a 429. The suite
    // would then pass or fail on test ordering, which makes it no gate at all.
    private readonly string _slug = "api-tests-" + Guid.NewGuid().ToString("n")[..12];

    public async ValueTask InitializeAsync()
    {
        Environment.SetEnvironmentVariable("RELEASELENS_DB", fixture.ConnectionString);
        Environment.SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-test");
        Environment.SetEnvironmentVariable("OPENAI_API_KEY", "sk-test");

        _factory = new WebApplicationFactory<Program>()
            .WithWebHostBuilder(builder => builder.UseEnvironment("Testing"));

        _client = _factory.CreateClient();

        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        _tenantId = await new TenantRepository(connections).CreateAsync(
            new TenantDefinition(_slug, "API tests", "github", "microsoft", "semantic-kernel", 1000),
            TestContext.Current.CancellationToken);

        _apiKey = await new ApiKeyRepository(connections).CreateKeyAsync(
            _tenantId, "test key", TestContext.Current.CancellationToken);
    }

    public async ValueTask DisposeAsync()
    {
        _client.Dispose();
        await _factory.DisposeAsync();
    }

    private HttpRequestMessage Query(string question, string? apiKey)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/query")
        {
            Content = JsonContent.Create(new { question, k = 5 })
        };

        if (apiKey is not null)
        {
            request.Headers.Add("X-Api-Key", apiKey);
        }

        return request;
    }

    [Fact]
    public async Task Health_IsAnonymous()
    {
        var response = await _client.GetAsync("/health", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Query_WithoutAnApiKey_Returns401()
    {
        var response = await _client.SendAsync(Query("anything", null), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Query_WithAnUnknownApiKey_Returns401()
    {
        var response = await _client.SendAsync(Query("anything", "rl_deadbeef"), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Query_WithAnEmptyQuestion_Returns400()
    {
        var response = await _client.SendAsync(Query("   ", _apiKey), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Query_WithAValidKey_ReturnsAnAnswerEnvelopeWithMetadata()
    {
        var response = await _client.SendAsync(Query("What changed recently?", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        Assert.True(body.TryGetProperty("answer", out _));
        Assert.True(body.TryGetProperty("citations", out _));

        var metadata = body.GetProperty("metadata");
        Assert.True(metadata.TryGetProperty("retrievedCount", out _));
        Assert.True(metadata.TryGetProperty("requestedK", out _));
        Assert.True(metadata.TryGetProperty("retrievalTruncated", out _));
        Assert.True(metadata.TryGetProperty("degraded", out _));
    }

    [Fact]
    public async Task Query_WhenBothProvidersAreUnreachable_Returns200Degraded()
    {
        // The test environment points both providers at an unroutable address,
        // so this exercises the real degraded path rather than a mock.
        var response = await _client.SendAsync(Query("What changed recently?", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.True(body.GetProperty("metadata").GetProperty("degraded").GetBoolean());
    }

    [Fact]
    public async Task Query_OverTheDailyTokenBudget_Returns429()
    {
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        await using var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);

        // The tenant's budget is 1000 tokens; record more than that.
        await new TokenUsageRepository().RecordAsync(
            scope, DateOnly.FromDateTime(DateTime.UtcNow), 5000, 500, 0.05m, TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        var response = await _client.SendAsync(Query("anything", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.TooManyRequests, response.StatusCode);

        // The usage row is asserted untouched below, but note what that does and does not
        // prove: in this test environment both providers point at the discard port, so a
        // completed agent call records zero usage anyway and the row looks identical under
        // either ordering. Verified experimentally. The ordering itself is pinned by
        // Query_OverTheDailyTokenBudget_NeverReachesTheProvider, which counts provider calls.
        await using var check = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);
        var after = await new TokenUsageRepository().GetTodayAsync(
            check, DateOnly.FromDateTime(DateTime.UtcNow), TestContext.Current.CancellationToken);

        Assert.Equal(5000, after.TokensIn);
        Assert.Equal(500, after.TokensOut);
    }

    [Fact]
    public async Task Query_OverTheDailyTokenBudget_NeverReachesTheProvider()
    {
        // The spec's hard stop is not "return 429" — it is "refuse before spending". Asserting
        // the status, or the usage row, cannot distinguish a short-circuit from an endpoint
        // that calls the agent and refuses afterwards, because a failed provider call records
        // nothing either way. Counting provider invocations is what actually pins it.
        var spy = new SpyChatProvider();

        await using var spied = _factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IChatProvider>();
                services.AddSingleton<IChatProvider>(spy);
            }));

        using var client = spied.CreateClient();

        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        await using (var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken))
        {
            await new TokenUsageRepository().RecordAsync(
                scope, DateOnly.FromDateTime(DateTime.UtcNow), 5000, 500, 0.05m,
                TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        var request = new HttpRequestMessage(HttpMethod.Post, "/query")
        {
            Content = JsonContent.Create(new { question = "anything", k = 5 })
        };
        request.Headers.Add("X-Api-Key", _apiKey);

        var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.TooManyRequests, response.StatusCode);
        Assert.Equal(0, spy.Calls);
    }

    /// <summary>
    /// Seeds this test's tenant with <paramref name="count"/> single-chunk commits, embedded
    /// with the host's own embedder, so seed retrieval has a pool to draw markers from.
    /// </summary>
    private async Task SeedCommitsAsync(int count)
    {
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        var embedder = _factory.Services.GetRequiredService<IEmbedder>();

        await using var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);

        var commits = Enumerable.Range(0, count).Select(i => new CommitEvidence(
            _tenantId, $"sha_api_{i}", $"fix: planner defect number {i}", "Alice", "a@example.com",
            DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            $"https://github.com/microsoft/semantic-kernel/commit/sha_api_{i}", [])).ToList();

        await new EvidenceRepository().UpsertCommitsAsync(scope, commits, TestContext.Current.CancellationToken);

        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunks = commits.SelectMany(chunker.Chunk).ToList();
        var chunkRepository = new ChunkRepository();

        var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
        var vectors = await embedder.EmbedDocumentsAsync(
            [.. chunks.Select(c => c.Content)], TestContext.Current.CancellationToken);
        await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], embedder.ModelName,
            TestContext.Current.CancellationToken);

        await scope.CommitAsync(TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task Query_ReturnsOnlyTheCitationsTheAnswerCites_EachCarryingItsMarker()
    {
        await SeedCommitsAsync(4);

        await using var scripted = _factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IChatProvider>();
                services.AddSingleton<IChatProvider>(new ScriptedChatProvider(
                    "The defect arrived in [E1] and was fixed by [E3]; see [E3] again for the test."));
            }));

        using var client = scripted.CreateClient();

        var request = new HttpRequestMessage(HttpMethod.Post, "/query")
        {
            Content = JsonContent.Create(new { question = "planner", k = 5 })
        };
        request.Headers.Add("X-Api-Key", _apiKey);

        var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        var metadata = body.GetProperty("metadata");

        Assert.False(metadata.GetProperty("degraded").GetBoolean());

        // Four artefacts were retrieved and put in front of the model; the answer rests on two.
        Assert.Equal(4, metadata.GetProperty("accumulatedCitationCount").GetInt32());

        // The wire contract under test: the marker in the response is the marker in the prose.
        // Read as a set — [E3] is mentioned twice and must still yield one citation.
        var inProse = Regex.Matches(body.GetProperty("answer").GetString()!, @"\[E(\d+)\]")
                           .Select(m => int.Parse(m.Groups[1].Value))
                           .Distinct().Order().ToArray();

        var onWire = body.GetProperty("citations").EnumerateArray()
                         .Select(c => c.GetProperty("marker").GetInt32())
                         .ToArray();

        Assert.Equal([1, 3], inProse);
        Assert.Equal(inProse, onWire);

        // Position is not the marker any more, and each entry is still a whole citation.
        foreach (var citation in body.GetProperty("citations").EnumerateArray())
        {
            Assert.Equal("commit", citation.GetProperty("type").GetString());
            Assert.StartsWith("sha_api_", citation.GetProperty("key").GetString(), StringComparison.Ordinal);
            Assert.StartsWith("https://github.com/microsoft/semantic-kernel/",
                citation.GetProperty("url").GetString(), StringComparison.Ordinal);
        }
    }

    [Fact]
    public async Task Swagger_IsServed()
    {
        var response = await _client.GetAsync("/openapi/v1.json", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}
