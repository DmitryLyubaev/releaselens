using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class BearerTokenHandlerTests
{
    private const string Scope = "https://search.azure.com/.default";
    private static readonly DateTimeOffset Start = new(2026, 10, 10, 9, 0, 0, TimeSpan.Zero);

    private static HttpClient Client(FakeTokenCredential credential, TimeProvider time, StubHttpHandler stub) =>
        new(new BearerTokenHandler(credential, Scope, time) { InnerHandler = stub })
        {
            BaseAddress = new Uri("https://example-search.search.windows.net/")
        };

    private static Task<HttpResponseMessage> Get(HttpClient client) =>
        client.GetAsync("indexes", TestContext.Current.CancellationToken);

    [Fact]
    public async Task BearerTokenHandler_AttachesTheTokenForItsScope()
    {
        var credential = new FakeTokenCredential().Returns("token-1", Start.AddHours(1));
        var stub = new StubHttpHandler().EnqueueJson("{}");

        await Get(Client(credential, new FakeTimeProvider(Start), stub));

        var authorization = Assert.Single(stub.Requests).Authorization!;
        Assert.Equal("Bearer", authorization.Scheme);
        Assert.Equal("token-1", authorization.Parameter);
        Assert.Equal([Scope], Assert.Single(credential.RequestedScopes));
    }

    [Fact]
    public async Task BearerTokenHandler_ReusesUntilFiveMinutesBeforeExpiry()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", Start.AddHours(1))
            .Returns("token-2", Start.AddHours(2));
        var stub = new StubHttpHandler().EnqueueJson("{}").EnqueueJson("{}").EnqueueJson("{}").EnqueueJson("{}");
        var client = Client(credential, time, stub);

        await Get(client);
        time.Advance(TimeSpan.FromMinutes(54));                           // 6 minutes left: still token 1
        await Get(client);
        time.Advance(TimeSpan.FromMinutes(1) + TimeSpan.FromSeconds(1));  // under 5 minutes left
        await Get(client);
        await Get(client);

        Assert.Equal(
            ["token-1", "token-1", "token-2", "token-2"],
            stub.Requests.ConvertAll(request => request.Authorization!.Parameter));
        Assert.Equal(2, credential.RequestedScopes.Count);
    }

    [Fact]
    public void BearerTokenHandler_TheScopesAreTheServicesPublicAudiences()
    {
        Assert.Equal("https://ai.azure.com/.default", BearerTokenHandler.OpenAiScope);
        Assert.Equal("https://search.azure.com/.default", BearerTokenHandler.SearchScope);
    }
}
