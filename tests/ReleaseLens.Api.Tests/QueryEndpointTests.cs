using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Api;
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
            new TenantDefinition("api-tests", "API tests", "github", "microsoft", "semantic-kernel", 1000),
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

        // The status alone would also pass if the endpoint called the agent first and only
        // then noticed the budget — the regression shape that matters, because it spends
        // money before refusing. Assert the usage row is untouched, which that ordering
        // would violate: a completed agent call records its tokens.
        await using var check = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken);
        var after = await new TokenUsageRepository().GetTodayAsync(
            check, DateOnly.FromDateTime(DateTime.UtcNow), TestContext.Current.CancellationToken);

        Assert.Equal(5000, after.TokensIn);
        Assert.Equal(500, after.TokensOut);
    }

    [Fact]
    public async Task Swagger_IsServed()
    {
        var response = await _client.GetAsync("/openapi/v1.json", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}
