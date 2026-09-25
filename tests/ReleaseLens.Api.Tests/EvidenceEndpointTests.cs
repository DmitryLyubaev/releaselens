using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Core.Chunking;
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

    private HttpRequestMessage AuthorisedPost(string url, string json)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, url);
        request.Headers.Add("X-Api-Key", _apiKey);
        request.Content = new StringContent(json, System.Text.Encoding.UTF8, "application/json");
        return request;
    }

    [Fact]
    public async Task Execute_WithoutAKey_Is401()
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/evidence/tools/count_evidence")
        {
            Content = new StringContent("{}", System.Text.Encoding.UTF8, "application/json")
        };

        var response = await _client.SendAsync(request, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Execute_CountEvidence_ReportsKindComputed()
    {
        var response = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/count_evidence",
                """{"entity_type":"release","date_field":"published"}"""),
            TestContext.Current.CancellationToken);

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);

        Assert.Equal("computed", body.GetProperty("kind").GetString());
        Assert.Empty(body.GetProperty("citations").EnumerateArray());
    }

    /// <summary>
    /// Deterministic stand-in for a real embedding, good enough to store a chunk with a
    /// legal vector(384) value. search_commits is reached here through full-text matching
    /// on a nonsense term, so nothing in this test depends on the vector actually being
    /// semantically meaningful - only on it satisfying the column's dimension check.
    /// </summary>
    private static float[] PlaceholderVector(string text)
    {
        var vector = new float[384];
        var hash = text.GetHashCode(StringComparison.Ordinal);
        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] = MathF.Sin((hash % 1000) + i);
        }

        var magnitude = MathF.Sqrt(vector.Sum(x => x * x));
        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] /= magnitude;
        }

        return vector;
    }

    /// <summary>
    /// bounds travels on the wire, or the MCP server this API feeds would have to parse
    /// prose to recover the same fact. coverage must NOT ride along on an evidence result -
    /// that field means something only for a computed figure (see ToolExecutionResult.Kind).
    /// </summary>
    [Fact]
    public async Task Execute_SearchCommits_ReportsBoundsOnTheWire_AndCarriesNoCoverage()
    {
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        var evidence = new EvidenceRepository();
        var chunkRepository = new ChunkRepository();
        var chunker = new EvidenceChunker(ChunkOptions.Default);

        await using (var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken))
        {
            var commit = new CommitEvidence(_tenantId, "sha_bounds_wire",
                "fix: resolve the gizmowire defect", "Author", "author@example.invalid",
                DateTimeOffset.UtcNow, DateTimeOffset.UtcNow,
                "https://example.invalid/commit/sha_bounds_wire", []);

            await evidence.UpsertCommitsAsync(scope, [commit], TestContext.Current.CancellationToken);

            var chunks = chunker.Chunk(commit);
            var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
            var vectors = chunks.Select(c => PlaceholderVector(c.Content)).ToArray();
            await chunkRepository.UpsertEmbeddingsAsync(scope, [.. ids.Zip(vectors)], "placeholder",
                TestContext.Current.CancellationToken);

            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        var response = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/search_commits", """{"query":"gizmowire","limit":10}"""),
            TestContext.Current.CancellationToken);

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        // bounds travels on the wire, or the MCP server would have to parse prose to recover it.
        var bounds = body.GetProperty("bounds");
        Assert.True(bounds.GetProperty("matched").GetInt32() >= bounds.GetProperty("returned").GetInt32());
        Assert.False(body.TryGetProperty("coverage", out var coverage) && coverage.ValueKind != JsonValueKind.Null,
            "an evidence result must not carry coverage");
    }

    [Fact]
    public async Task Execute_UnknownTool_IsAToolErrorNotACrash()
    {
        var response = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/no_such_tool", "{}"),
            TestContext.Current.CancellationToken);

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);

        // An unknown tool is a recoverable condition the caller can act on, not a 500.
        Assert.True(body.GetProperty("isError").GetBoolean());
        Assert.Contains("no_such_tool", body.GetProperty("content").GetString()!);
    }

    [Fact]
    public async Task Execute_CannotReachAnotherTenantsEvidence()
    {
        // The cross-tenant rejection test, over the evidence path rather than /query.
        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        var otherTenant = await new TenantRepository(connections).CreateAsync(
            new TenantDefinition(
                "evidence-other-" + Guid.NewGuid().ToString("n")[..12],
                "Other tenant", "github", "other", "repo", 1000),
            TestContext.Current.CancellationToken);

        const string otherTag = "other-tenant-only-1.0.0";

        await using (var otherScope = await connections.OpenAsync(
            otherTenant, TestContext.Current.CancellationToken))
        {
            await new EvidenceRepository().UpsertReleasesAsync(
                otherScope,
                [new ReleaseEvidence(otherTenant, otherTag, "Other", "body",
                    DateTimeOffset.UtcNow, null, "https://example.invalid/r")],
                TestContext.Current.CancellationToken);

            await otherScope.CommitAsync(TestContext.Current.CancellationToken);
        }

        // Control: the same request, run under the seeded tenant's own key, must find the
        // release. Without this, a broken query (wrong prefix, wrong argument name) would
        // return nothing for either tenant, and the negative assertion below would pass for
        // a reason that has nothing to do with isolation.
        var otherApiKey = await new ApiKeyRepository(connections).CreateKeyAsync(
            otherTenant, "evidence-other-tests", TestContext.Current.CancellationToken);

        var ownRequest = new HttpRequestMessage(HttpMethod.Post, "/evidence/tools/list_releases")
        {
            Content = new StringContent("""{"tag_prefix":"other-tenant-only"}""",
                System.Text.Encoding.UTF8, "application/json")
        };
        ownRequest.Headers.Add("X-Api-Key", otherApiKey);

        var ownResponse = await _client.SendAsync(ownRequest, TestContext.Current.CancellationToken);
        ownResponse.EnsureSuccessStatusCode();

        var ownBody = await ownResponse.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);

        Assert.Contains(otherTag, ownBody.GetProperty("content").GetString()!);
        var ownCitations = ownBody.GetProperty("citations").EnumerateArray().ToArray();
        Assert.NotEmpty(ownCitations);

        // Pins the citation field mapping (type/key/title/url are assembled positionally in
        // Program.cs, so a swapped pair would otherwise pass every other assertion here) and
        // that the url is absolute, the same way /query resolves it against the tenant's repo,
        // rather than the bare repo-relative fragment the tool itself returns.
        var ownCitation = ownCitations[0];
        Assert.Equal("release", ownCitation.GetProperty("type").GetString());
        Assert.Equal(otherTag, ownCitation.GetProperty("key").GetString());
        Assert.Equal(
            $"https://github.com/other/repo/releases/tag/{otherTag}",
            ownCitation.GetProperty("url").GetString());

        var response = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/list_releases", """{"tag_prefix":"other-tenant-only"}"""),
            TestContext.Current.CancellationToken);

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JsonElement>(
            TestContext.Current.CancellationToken);

        // Asserted against the full tag, not just the shared prefix: the caller's own
        // "tag_prefix" argument is echoed back into a zero-match response too (e.g. "No
        // releases match tag prefix 'other-tenant-only'."), so asserting on the bare prefix
        // fails even when isolation holds. The full tag appears only if a matching row from
        // the other tenant actually leaked into this response.
        Assert.DoesNotContain(otherTag, body.GetProperty("content").GetString()!);
        Assert.Empty(body.GetProperty("citations").EnumerateArray());

        // isError:true with empty citations also describes a tool that failed outright
        // (ToolRegistry.ExecuteAsync turns any exception into that exact shape), which would
        // make this test pass for a reason that has nothing to do with isolation. Requiring
        // isError:false, plus the specific wording of a genuine empty result, pins this down
        // to "looked under the caller's tenant and found nothing" rather than "could not look".
        Assert.False(body.GetProperty("isError").GetBoolean());
        Assert.Contains("No releases match", body.GetProperty("content").GetString()!);
    }

    private static string[] Keys(JsonElement element)
        => [.. element.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal)];

    /// <summary>
    /// T-A3. releaselens-mcp reads these responses, so their shape is a contract: every key,
    /// not only the ones some other test happens to read. The tests above pin values; this
    /// pins that no key is added, renamed or dropped, including that an absent bounds or
    /// coverage is written as null rather than left out.
    /// </summary>
    [Fact]
    public async Task EvidenceContract_ResponseShapes_AreExactlyTheCurrentOnes()
    {
        var list = await _client.SendAsync(
            Authorised(HttpMethod.Get, "/evidence/tools"), TestContext.Current.CancellationToken);
        list.EnsureSuccessStatusCode();

        var listBody = await list.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(["tools"], Keys(listBody));

        foreach (var tool in listBody.GetProperty("tools").EnumerateArray())
        {
            Assert.Equal(["description", "inputSchema", "name"], Keys(tool));
        }

        var connections = new TenantConnectionFactory(fixture.ConnectionString);
        var chunker = new EvidenceChunker(ChunkOptions.Default);
        var chunkRepository = new ChunkRepository();

        await using (var scope = await connections.OpenAsync(_tenantId, TestContext.Current.CancellationToken))
        {
            var commit = new CommitEvidence(_tenantId, "sha_contract_shape",
                "fix: resolve the gizmoshape defect", "Author", "author@example.invalid",
                DateTimeOffset.UtcNow, DateTimeOffset.UtcNow,
                "https://example.invalid/commit/sha_contract_shape", []);

            await new EvidenceRepository().UpsertCommitsAsync(scope, [commit], TestContext.Current.CancellationToken);

            var chunks = chunker.Chunk(commit);
            var ids = await chunkRepository.UpsertChunksAsync(scope, chunks, TestContext.Current.CancellationToken);
            await chunkRepository.UpsertEmbeddingsAsync(scope,
                [.. ids.Zip(chunks.Select(c => PlaceholderVector(c.Content)))], "placeholder",
                TestContext.Current.CancellationToken);

            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        string[] resultKeys = ["bounds", "citations", "content", "coverage", "excerpts", "isError", "kind"];

        // An evidence result: bounds set, coverage null, and one citation and excerpt to read.
        var search = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/search_commits", """{"query":"gizmoshape","limit":10}"""),
            TestContext.Current.CancellationToken);
        search.EnsureSuccessStatusCode();

        var evidence = await search.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(resultKeys, Keys(evidence));
        Assert.Equal("evidence", evidence.GetProperty("kind").GetString());
        Assert.Equal(["matched", "returned", "truncated"], Keys(evidence.GetProperty("bounds")));
        Assert.Equal(JsonValueKind.Null, evidence.GetProperty("coverage").ValueKind);

        var citation = Assert.Single(evidence.GetProperty("citations").EnumerateArray());
        Assert.Equal(["key", "title", "type", "url"], Keys(citation));

        var excerpt = evidence.GetProperty("excerpts").EnumerateArray().First();
        Assert.Equal(["key", "text", "type"], Keys(excerpt));

        // A computed result: coverage set, bounds null, no citations and no excerpts.
        var count = await _client.SendAsync(
            AuthorisedPost("/evidence/tools/count_evidence", """{"entity_type":"release","date_field":"published"}"""),
            TestContext.Current.CancellationToken);
        count.EnsureSuccessStatusCode();

        var computed = await count.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);
        Assert.Equal(resultKeys, Keys(computed));
        Assert.Equal("computed", computed.GetProperty("kind").GetString());
        Assert.Equal(JsonValueKind.Null, computed.GetProperty("bounds").ValueKind);
        Assert.Equal(["completeForWindow", "earliest", "latest"], Keys(computed.GetProperty("coverage")));
        Assert.Empty(computed.GetProperty("citations").EnumerateArray());
        Assert.Empty(computed.GetProperty("excerpts").EnumerateArray());
    }
}
