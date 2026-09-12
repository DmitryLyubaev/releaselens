using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Api.Tests;

[Collection(nameof(PostgresCollection))]
public class EvidenceEndpointTests(PostgresFixture fixture) : IAsyncLifetime
{
    private WebApplicationFactory<Program> _factory = null!;
    private HttpClient _client = null!;
    private string _apiKey = null!;
    private Guid _tenantId;

    // A distinct tenant per test, for the same reason QueryEndpointTests uses one:
    // TenantRepository.CreateAsync upserts on slug, so a shared slug makes the suite
    // pass or fail on ordering.
    private readonly string _slug = "evidence-tests-" + Guid.NewGuid().ToString("n")[..12];

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
            new TenantDefinition(_slug, "Evidence tests", "github", "microsoft", "semantic-kernel", 1000),
            TestContext.Current.CancellationToken);

        _apiKey = await new ApiKeyRepository(connections).CreateKeyAsync(
            _tenantId, "evidence-tests", TestContext.Current.CancellationToken);
    }

    public ValueTask DisposeAsync()
    {
        _client.Dispose();
        _factory.Dispose();
        return ValueTask.CompletedTask;
    }

    private HttpRequestMessage Authorised(HttpMethod method, string url)
    {
        var request = new HttpRequestMessage(method, url);
        request.Headers.Add("X-Api-Key", _apiKey);
        return request;
    }

    [Fact]
    public async Task ListTools_WithoutAKey_Is401()
    {
        var response = await _client.GetAsync("/evidence/tools", TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ListTools_ReturnsEverySixToolWithASchema()
    {
        var response = await _client.SendAsync(
            Authorised(HttpMethod.Get, "/evidence/tools"), TestContext.Current.CancellationToken);

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);
        var tools = body.GetProperty("tools");

        var names = tools.EnumerateArray().Select(t => t.GetProperty("name").GetString()).ToArray();

        Assert.Equal(6, names.Length);
        Assert.Contains("search_commits", names);
        Assert.Contains("get_issue", names);
        Assert.Contains("diff_between_releases", names);
        Assert.Contains("find_regressions", names);
        Assert.Contains("count_evidence", names);
        Assert.Contains("list_releases", names);

        foreach (var tool in tools.EnumerateArray())
        {
            Assert.False(string.IsNullOrWhiteSpace(tool.GetProperty("description").GetString()));
            Assert.Equal(JsonValueKind.Object, tool.GetProperty("inputSchema").ValueKind);
        }
    }

    [Fact]
    public async Task ListTools_ExposesNoTenantParameterOnAnySchema()
    {
        // The security property of the whole MCP surface. If a tenant parameter ever appears
        // in a published schema, a caller could name a tenant and only RLS would stand
        // between them and the answer.
        var response = await _client.SendAsync(
            Authorised(HttpMethod.Get, "/evidence/tools"), TestContext.Current.CancellationToken);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);

        var serialised = body.GetRawText();

        Assert.DoesNotContain("tenant", serialised, StringComparison.OrdinalIgnoreCase);
    }
}
