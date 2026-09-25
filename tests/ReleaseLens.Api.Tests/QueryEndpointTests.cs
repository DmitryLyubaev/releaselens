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

    /// <summary>
    /// When <paramref name="includeEvidence"/> is null the field is left OUT of the request
    /// body entirely, rather than sent as false. That is the shape every existing client
    /// sends, and it is the one the "off by default" contract has to hold for.
    /// </summary>
    private HttpRequestMessage Query(string question, string? apiKey, bool? includeEvidence = null)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/query")
        {
            Content = includeEvidence is null
                ? JsonContent.Create(new { question, k = 5 })
                : JsonContent.Create(new { question, k = 5, includeEvidence = includeEvidence.Value })
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
        Assert.True(metadata.TryGetProperty("retrievalFewerThanRequested", out _));
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

    /// <summary>
    /// Runs one scripted query and hands back the parsed body, so the evidence tests below
    /// differ only in the flag they send.
    /// </summary>
    private async Task<JsonElement> ScriptedQueryAsync(string answer, bool? includeEvidence)
    {
        await using var scripted = _factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IChatProvider>();
                services.AddSingleton<IChatProvider>(new ScriptedChatProvider(answer));
            }));

        using var client = scripted.CreateClient();

        var response = await client.SendAsync(
            Query("planner", _apiKey, includeEvidence), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task Query_WithoutTheEvidenceFlag_CarriesNoEvidenceText()
    {
        await SeedCommitsAsync(4);

        var body = await ScriptedQueryAsync("Caused by [E1], fixed by [E3].", includeEvidence: null);
        var citations = body.GetProperty("citations").EnumerateArray().ToArray();

        Assert.NotEmpty(citations);

        // Absent, not null and not empty. A chunk is around a thousand characters and an
        // answer can cite twenty artefacts; every production response paying that so one
        // consumer can read it is the thing the flag exists to prevent.
        foreach (var citation in citations)
        {
            Assert.False(citation.TryGetProperty("evidence", out _));
        }

        // And the flag being off must not have cost the response anything else.
        Assert.Equal([1, 3], citations.Select(c => c.GetProperty("marker").GetInt32()).ToArray());
    }

    [Fact]
    public async Task Query_WithTheEvidenceFlag_CarriesTheTextOfEachCitedArtefact()
    {
        await SeedCommitsAsync(4);

        var body = await ScriptedQueryAsync("Caused by [E1], fixed by [E3].", includeEvidence: true);
        var citations = body.GetProperty("citations").EnumerateArray().ToArray();

        Assert.Equal([1, 3], citations.Select(c => c.GetProperty("marker").GetInt32()).ToArray());

        foreach (var citation in citations)
        {
            Assert.True(citation.TryGetProperty("evidence", out var evidence));

            var fragments = evidence.EnumerateArray().Select(e => e.GetString()!).ToArray();
            Assert.NotEmpty(fragments);

            // SeedCommitsAsync gives each commit the unique subject "fix: planner defect
            // number N", and the key is sha_api_N — so this pins the text to the artefact
            // its marker resolves to, not merely to some artefact.
            var key = citation.GetProperty("key").GetString()!;
            var subject = "defect number " + key["sha_api_".Length..];

            Assert.All(fragments, fragment =>
                Assert.Contains(subject, fragment, StringComparison.Ordinal));
        }
    }

    /// <summary>
    /// citation_recall and citation_precision in the eval harness are computed over the
    /// "type:key" identifiers in this list. The flag must be inert with respect to them, so
    /// the same query with the flag on and off has to produce an identical citation list.
    /// </summary>
    [Fact]
    public async Task Query_TheEvidenceFlag_ChangesNothingAboutWhichArtefactsAreCited()
    {
        await SeedCommitsAsync(4);

        const string Answer = "Caused by [E1], fixed by [E3]; see [E3] again for the test.";

        static string[] Identifiers(JsonElement body) =>
            [.. body.GetProperty("citations").EnumerateArray().Select(c =>
                $"{c.GetProperty("marker").GetInt32()}|{c.GetProperty("type").GetString()}:" +
                $"{c.GetProperty("key").GetString()}|{c.GetProperty("url").GetString()}")];

        var without = await ScriptedQueryAsync(Answer, includeEvidence: null);
        var with = await ScriptedQueryAsync(Answer, includeEvidence: true);

        Assert.Equal(Identifiers(without), Identifiers(with));
        Assert.Equal(
            without.GetProperty("metadata").GetProperty("accumulatedCitationCount").GetInt32(),
            with.GetProperty("metadata").GetProperty("accumulatedCitationCount").GetInt32());
    }

    /// <summary>
    /// The degraded path honours the flag too. Its answer is the evidence block verbatim so
    /// the text is redundant there — but a consumer that asked for evidence and received an
    /// empty list would score the response ungrounded for a reason that is entirely the
    /// harness's, which is the defect this change removes.
    /// </summary>
    [Fact]
    public async Task Query_WhenDegraded_StillCarriesEvidenceTextWhenAsked()
    {
        await SeedCommitsAsync(3);

        // The default client's providers point at an unroutable address, so this is the
        // real degraded path rather than a mock of it.
        var response = await _client.SendAsync(
            Query("planner", _apiKey, includeEvidence: true), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.True(body.GetProperty("metadata").GetProperty("degraded").GetBoolean());

        var citations = body.GetProperty("citations").EnumerateArray().ToArray();
        Assert.NotEmpty(citations);

        foreach (var citation in citations)
        {
            Assert.True(citation.TryGetProperty("evidence", out var evidence));
            Assert.NotEmpty(evidence.EnumerateArray().Select(e => e.GetString()!).ToArray());
        }
    }

    // T-A2, the providers half, at the wire
    [Fact]
    public async Task Query_Metadata_ListsTheProvidersThatAnswered()
    {
        await SeedCommitsAsync(2);

        var body = await ScriptedQueryAsync("Fixed in [E1].", includeEvidence: null);
        var metadata = body.GetProperty("metadata");

        Assert.Equal(["scripted"],
            metadata.GetProperty("providers").EnumerateArray().Select(p => p.GetString()!).ToArray());
        Assert.Equal("scripted", metadata.GetProperty("provider").GetString());
    }

    [Fact]
    public async Task Query_WhenNoProviderAnswers_ListsNoProviders()
    {
        // The test environment points both providers at an unroutable address, so nothing answers.
        var response = await _client.SendAsync(Query("What changed recently?", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var metadata = (await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken))
            .GetProperty("metadata");

        Assert.True(metadata.GetProperty("degraded").GetBoolean());
        Assert.Equal(JsonValueKind.Array, metadata.GetProperty("providers").ValueKind);
        Assert.Empty(metadata.GetProperty("providers").EnumerateArray());
    }

    private const string FilteredAnswer =
        "The request was blocked by the model provider's content filter, so no answer was produced.";

    /// <summary>
    /// Runs one query against a host whose only chat provider is <paramref name="provider"/>,
    /// and hands back the parsed body of the 200 it must return.
    /// </summary>
    private async Task<JsonElement> QueryWithAsync(IChatProvider provider)
    {
        await using var hosted = _factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IChatProvider>();
                services.AddSingleton<IChatProvider>(provider);
            }));

        using var client = hosted.CreateClient();

        var response = await client.SendAsync(Query("planner", _apiKey), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
    }

    /// <summary>
    /// T-A2, the filtered half. A filtered completion is its own outcome on the wire: HTTP 200,
    /// the fixed answer, nothing cited, not degraded, and metadata.filtered naming the stage and
    /// the provider, beside metadata.providers listing who answered before the filter fired.
    /// </summary>
    [Fact]
    public async Task Query_WhenTheCompletionIsFiltered_ReportsFilteredAndTheProvidersThatAnswered()
    {
        var body = await QueryWithAsync(new FilteringChatProvider(ContentFilterStage.Completion, answeredCalls: 1));

        Assert.Equal(FilteredAnswer, body.GetProperty("answer").GetString());
        Assert.Empty(body.GetProperty("citations").EnumerateArray());

        var metadata = body.GetProperty("metadata");
        Assert.False(metadata.GetProperty("degraded").GetBoolean());

        var filtered = metadata.GetProperty("filtered");
        Assert.Equal(["provider", "stage"],
            filtered.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).ToArray());
        Assert.Equal("completion", filtered.GetProperty("stage").GetString());
        Assert.Equal("azure-openai", filtered.GetProperty("provider").GetString());

        Assert.Equal(["azure-openai"],
            metadata.GetProperty("providers").EnumerateArray().Select(p => p.GetString()!).ToArray());

        // Both calls' tokens: the answered tool call's (10 in, 5 out) and the filtered
        // completion's (20 in, 7 out).
        Assert.Equal(30, metadata.GetProperty("tokensIn").GetInt32());
        Assert.Equal(12, metadata.GetProperty("tokensOut").GetInt32());
        Assert.True(metadata.GetProperty("costUsd").GetDecimal() > 0m, "a filtered Azure call is not free");

        // And the filtered answer's usage counts against the tenant's daily budget like any other.
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        await using var check = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);
        var recorded = await new TokenUsageRepository().GetTodayAsync(
            check, DateOnly.FromDateTime(DateTime.UtcNow), TestContext.Current.CancellationToken);

        Assert.Equal(30, recorded.TokensIn);
        Assert.Equal(12, recorded.TokensOut);
        Assert.True(recorded.CostUsd > 0m);
    }

    [Fact]
    public async Task Query_WhenThePromptIsFiltered_ReportsStagePromptAndNoAnsweringProvider()
    {
        var body = await QueryWithAsync(new FilteringChatProvider(ContentFilterStage.Prompt, answeredCalls: 0));

        Assert.Equal(FilteredAnswer, body.GetProperty("answer").GetString());

        var metadata = body.GetProperty("metadata");
        Assert.False(metadata.GetProperty("degraded").GetBoolean());
        Assert.Equal("prompt", metadata.GetProperty("filtered").GetProperty("stage").GetString());
        Assert.Equal("azure-openai", metadata.GetProperty("filtered").GetProperty("provider").GetString());

        // No provider answered: the only call was refused.
        Assert.Equal(0, metadata.GetProperty("providers").GetArrayLength());
        Assert.Equal(0, metadata.GetProperty("tokensIn").GetInt32());
        Assert.Equal(0m, metadata.GetProperty("costUsd").GetDecimal());
    }

    /// <summary>
    /// The field is additive: a response that was not filtered carries no "filtered" key at
    /// all, so every existing consumer sees exactly the shape it saw before. Checked on a
    /// synthesised answer and on the degraded one.
    /// </summary>
    [Fact]
    public async Task Query_WhenNothingIsFiltered_OmitsTheFilteredField()
    {
        var answered = await QueryWithAsync(new ScriptedChatProvider("Nothing in the evidence answers this."));
        Assert.False(answered.GetProperty("metadata").TryGetProperty("filtered", out _));

        // The default host's providers point at an unroutable address, so this is the real
        // degraded path.
        var response = await _client.SendAsync(Query("planner", _apiKey), TestContext.Current.CancellationToken);
        var degraded = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        Assert.True(degraded.GetProperty("metadata").GetProperty("degraded").GetBoolean());
        Assert.False(degraded.GetProperty("metadata").TryGetProperty("filtered", out _));
    }

    [Fact]
    public async Task Swagger_IsServed()
    {
        var response = await _client.GetAsync("/openapi/v1.json", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}
